"""FastAPI 入口：原料/化验查询、配比试算、手工评估、历史追溯。

注意：本服务为离线工艺研发试算工具，采用虚构工艺边界与演示数据，
不向任何真实生产设备下发指令。
"""
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import chemistry, crud, optimizer
from .database import Base, engine, get_db
from .schemas import (
    BlendRequest,
    BlendResponse,
    EvaluateRequest,
    MaterialOut,
    SolutionItem,
    SolutionOut,
    SpecAction,
    SpecCreate,
    SpecDraftUpdate,
    SpecOut,
    SpecRevisionOut,
    Targets,
)

Base.metadata.create_all(bind=engine)

# 轻量迁移：老库 blend_run 表补规范绑定列（新库 create_all 已含，幂等）。
with engine.begin() as _conn:
    _conn.execute(text(
        "ALTER TABLE blend_run ADD COLUMN IF NOT EXISTS "
        "spec_revision_id INTEGER REFERENCES spec_revision(id)"
    ))
    _conn.execute(text(
        "ALTER TABLE blend_run ADD COLUMN IF NOT EXISTS spec_snapshot JSON"
    ))

app = FastAPI(
    title="离线原料配比试算（虚构工艺边界 · 研发用）",
    version="1.0.0",
    description="质量守恒合成 + 率值计算 + SciPy LP 优化；不连接任何生产控制系统。",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


@app.exception_handler(chemistry.BlendError)
def blend_error_handler(request, exc: chemistry.BlendError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=getattr(exc, "status_code", 422),
        content={
            "error_code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "rawmix-offline", "mode": "fictional-boundary"}


@app.get("/api/materials", response_model=list[MaterialOut])
def materials(active_only: bool = False, db: Session = Depends(get_db)):
    return crud.list_materials(db, active_only=active_only)


def _serialize_solution(sol: dict) -> SolutionOut:
    return SolutionOut(
        mode=sol["mode"],
        mode_label=sol["mode_label"],
        success=sol["success"],
        total_cost=sol.get("total_cost"),
        cost_per_t_dry=sol.get("cost_per_t_dry"),
        indicators=sol.get("indicators"),
        composition_dry_pct=sol.get("composition_dry_pct"),
        composition_wet_pct=sol.get("composition_wet_pct"),
        water_pct_in_wet_mix=sol.get("water_pct_in_wet_mix"),
        diagnostic=sol.get("diagnostic"),
        items=[SolutionItem(**{k: v for k, v in it.items()
                               if not k.startswith("_")}) for it in sol.get("items", [])],
    )


@app.post("/api/blend", response_model=BlendResponse)
def blend(req: BlendRequest, db: Session = Depends(get_db)):
    spec_binding = None
    spec_info = None
    if req.spec_revision_id is not None:
        # 引用规范：以修订版冻结参数为准，请求里的临时率值/限值不生效，
        # 防止把一串临时参数误当成可复现的规范。
        rev, spec = crud.get_revision_with_spec(db, req.spec_revision_id)
        if spec.status == "retired":
            raise chemistry.SpecError(
                "SPEC_RETIRED",
                f"规范 {spec.code} 已停用，禁止新的试算引用；历史批次仍可回看。",
                {"spec_id": spec.id, "code": spec.code},
            )
        req = req.model_copy(update={
            "targets": Targets(**rev.targets),
            "hazard_limits_pct": dict(rev.hazard_limits_pct),
        })
        spec_binding = (spec, rev)
        spec_info = {
            "spec_id": spec.id, "spec_code": spec.code, "spec_name": spec.name,
            "revision_id": rev.id, "revision_no": rev.revision_no,
            "targets": rev.targets, "hazard_limits_pct": rev.hazard_limits_pct,
        }
    pairs = crud.resolve_candidates(db, req.candidates)
    rows = optimizer.prepare_rows(pairs)
    if not rows:
        raise HTTPException(400, "候选原料为空。")
    solutions = optimizer.solve(rows, req)
    run_id, run_code = None, ""
    if req.save:
        run = crud.save_run(db, req, solutions, spec_binding=spec_binding)
        run_id, run_code = run.id, run.run_code
    return BlendResponse(
        run_id=run_id,
        run_code=run_code,
        status="feasible" if any(s["success"] for s in solutions) else "infeasible",
        solutions=[_serialize_solution(s) for s in solutions],
        spec=spec_info,
    )


@app.post("/api/evaluate")
def evaluate(req: EvaluateRequest, db: Session = Depends(get_db)):
    """手工给定干基份额做质量守恒合成与率值计算。

    用于显式演示：缺测报错、分母为零报错（不以零含量兜底）。
    """
    if len(req.picks) != len(req.shares_pct_dry):
        raise HTTPException(400, "picks 与 shares_pct_dry 长度必须一致。")
    pairs = crud.resolve_candidates(db, req.picks)
    rows = optimizer.prepare_rows(pairs)

    total = sum(req.shares_pct_dry)
    if total <= 0:
        raise HTTPException(400, "配比份额之和必须为正。")
    x = np.array([v / total for v in req.shares_pct_dry])

    material_rows = [{
        "code": r.code, "name": r.name, "version": r.version,
        "lab_report_no": r.lab_report_no,
        "composition": r.composition_dry, "measured_oxides": list(r.measured),
    } for r in rows]
    chemistry.require_measured(material_rows, ["CaO", "SiO2", "Al2O3", "Fe2O3"])

    components = [c for c in optimizer.COMPONENT_ORDER if any(
        c in r.composition_dry for r in rows
    )]
    pick_dicts = [{
        "code": r.code, "name": r.name, "moisture_pct": r.moisture_pct,
        "composition_dry": {k: r.composition_dry.get(k, 0.0) for k in components},
    } for r in rows]
    synth = chemistry.synthesize(pick_dicts, list(x), components)

    # 率值：分母为零必须由 ZeroDenominatorError 显式抛出
    indicators = chemistry.calc_indicators(synth["dry_pct"]).as_dict()

    items = []
    for r, xi in zip(rows, x):
        if xi < 1e-10:
            continue
        trace = chemistry.build_conversion_trace(
            r.code, r.name, r.composition_raw, r.basis, r.moisture_pct
        )
        items.append({
            "material_code": r.code,
            "material_name": r.name,
            "assay_version": r.version,
            "lab_report_no": r.lab_report_no,
            "share_pct_dry": round(xi * 100.0, 4),
            "conversion_trace": trace,
        })
    return {
        "scenario_name": req.scenario_name,
        "indicators": indicators,
        "composition_dry_pct": synth["dry_pct"],
        "composition_wet_pct": synth["wet_pct"],
        "water_pct_in_wet_mix": synth["water_pct_in_wet_mix"],
        "contributions": synth["contributions"],
        "items": items,
    }


@app.get("/api/runs")
def runs(limit: int = 50, db: Session = Depends(get_db)):
    return crud.list_runs(db, limit)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    detail = crud.get_run_detail(db, run_id)
    if detail is None:
        raise HTTPException(404, "试算记录不存在。")
    return detail


# ---- 版本化约束规范：草稿 → 发布（不可变修订版）→ 停用 ----

@app.get("/api/specs", response_model=list[SpecOut])
def specs(db: Session = Depends(get_db)):
    return crud.list_specs(db)


@app.post("/api/specs", response_model=SpecOut, status_code=201)
def spec_create(req: SpecCreate, db: Session = Depends(get_db)):
    return crud.create_spec(db, req)


@app.get("/api/specs/{spec_id}", response_model=SpecOut)
def spec_detail(spec_id: int, db: Session = Depends(get_db)):
    return crud._get_spec(db, spec_id)


@app.put("/api/specs/{spec_id}/draft", response_model=SpecOut)
def spec_draft_update(spec_id: int, req: SpecDraftUpdate, db: Session = Depends(get_db)):
    """编辑草稿参数；已发布规范返回 409 SPEC_IMMUTABLE，只能复制出新草稿。"""
    return crud.update_spec_draft(db, spec_id, req)


@app.post("/api/specs/{spec_id}/publish", response_model=SpecRevisionOut, status_code=201)
def spec_publish(spec_id: int, req: SpecAction, db: Session = Depends(get_db)):
    """发布草稿 → 生成不可编辑的修订版；并发发布仅一个成功，其余 409。"""
    return crud.publish_spec(db, spec_id, req)


@app.post("/api/specs/{spec_id}/copy", response_model=SpecOut)
def spec_copy(spec_id: int, req: SpecAction, db: Session = Depends(get_db)):
    """把最新已发布修订版复制为新草稿（修改已发布规范的唯一入口）。"""
    return crud.copy_spec_to_draft(db, spec_id, req)


@app.post("/api/specs/{spec_id}/retire", response_model=SpecOut)
def spec_retire(spec_id: int, req: SpecAction, db: Session = Depends(get_db)):
    """停用规范：禁止新试算引用，历史批次仍可回看。"""
    return crud.retire_spec(db, spec_id, req)


@app.get("/api/specs/{spec_id}/diff")
def spec_diff(spec_id: int, from_no: int, to_no: int,
              db: Session = Depends(get_db)):
    """两个修订版的参数差异（from_no/to_no 为修订号）。"""
    return crud.diff_revisions(db, spec_id, from_no, to_no)


@app.get("/api/specs/{spec_id}/revisions/{revision_no}/runs")
def spec_revision_runs(spec_id: int, revision_no: int,
                       db: Session = Depends(get_db)):
    """列出引用该修订版的全部试算批次。"""
    rev, runs = crud.runs_of_revision(db, spec_id, revision_no)
    return {
        "spec_id": spec_id,
        "revision_no": revision_no,
        "revision": {
            "id": rev.id,
            "revision_no": rev.revision_no,
            "targets": rev.targets,
            "hazard_limits_pct": rev.hazard_limits_pct,
            "published_at": rev.published_at.isoformat(timespec="seconds"),
            "note": rev.note,
        },
        "runs": runs,
    }


# ---- 生产构建后的静态前端（ng build 产物） ----
_dist = Path(__file__).resolve().parent.parent / "static" / "browser"
if _dist.exists():
    _assets = _dist / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        index = _dist / "index.html"
        if full_path and (candidate := _dist / full_path).is_file():
            return FileResponse(candidate)
        return FileResponse(index)