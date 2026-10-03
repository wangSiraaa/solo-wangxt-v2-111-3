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
from sqlalchemy.orm import Session

from . import chemistry, crud, optimizer, specstore
from .database import Base, engine, get_db
from .schemas import (
    BlendRequest,
    BlendResponse,
    CopyRequest,
    DeprecateRequest,
    DraftUpdate,
    EvaluateRequest,
    MaterialOut,
    PublishRequest,
    SpecBinding,
    SpecFamilyCreate,
    SolutionItem,
    SolutionOut,
    Targets,
    Interval,
)

def _ensure_schema():
    """幂等建表 + 旧库补列（开发/演示环境无迁移框架时使用）。"""
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "blend_run" in inspector.get_table_names():
        have = {c["name"] for c in inspector.get_columns("blend_run")}
        want = {
            "spec_revision_id": "INTEGER REFERENCES spec_revision(id) ON DELETE RESTRICT",
            "spec_code": "VARCHAR(32)",
            "spec_revision_no": "VARCHAR(32)",
            "spec_snapshot": "JSON",
        }
        with engine.begin() as conn:
            for name, ddl in want.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE blend_run ADD COLUMN {name} {ddl}"))


_ensure_schema()

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
        status_code=422,
        content={
            "error_code": exc.code,
            "message": exc.message,
            "details": exc.details,
        },
    )


@app.exception_handler(specstore.SpecError)
def spec_error_handler(request, exc: specstore.SpecError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status,
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
    # 版本化约束规范：引用修订版时，求解参数一律取自服务端冻结快照，
    # 忽略请求体里夹带的临时 targets/hazard_limits，避免临时参数冒充规范。
    # 对规范族/修订加行锁并持有到结果入库：停用/发布/复制与试算引用串行化，
    # 保证“停用后不可能再混入引用该规范的新批次”。
    spec_rev = None
    try:
        if req.spec_revision_id is not None:
            spec_rev = specstore.resolve_for_blend(db, req.spec_revision_id, lock=True)
            snap = spec_rev.spec_snapshot
            solve_req = BlendRequest(
                scenario_name=req.scenario_name,
                batch_t_dry=req.batch_t_dry,
                candidates=req.candidates,
                targets=Targets(**{
                    k: Interval(**v) for k, v in snap["targets"].items()
                }),
                hazard_limits_pct=dict(snap["hazard_limits_pct"]),
                modes=req.modes,
                cheap_material_id=req.cheap_material_id,
                save=req.save,
            )
        else:
            if req.targets is None:
                raise HTTPException(
                    400, "必须指定 spec_revision_id 或给出 targets（SM/IM/KH 窗口）。"
                )
            solve_req = req.model_copy(
                update={"hazard_limits_pct": req.hazard_limits_pct or {}}
            )

        pairs = crud.resolve_candidates(db, solve_req.candidates)
        rows = optimizer.prepare_rows(pairs)
        if not rows:
            raise HTTPException(400, "候选原料为空。")
        solutions = optimizer.solve(rows, solve_req)
        run_id, run_code = None, ""
        if solve_req.save:
            run = crud.save_run(db, solve_req, solutions, spec_rev=spec_rev)  # 提交并释放锁
            run_id, run_code = run.id, run.run_code
        elif spec_rev is not None:
            db.rollback()  # 不入库：显式结束事务释放规范锁
    except Exception:
        if spec_rev is not None:
            db.rollback()
        raise

    binding = None
    if spec_rev is not None:
        binding = SpecBinding(
            revision_id=spec_rev.id,
            spec_code=spec_rev.family.spec_code,
            spec_name=spec_rev.family.name,
            revision_no=spec_rev.revision_no,
            status=spec_rev.status,
            snapshot=spec_rev.spec_snapshot,
        )
    return BlendResponse(
        run_id=run_id,
        run_code=run_code,
        status="feasible" if any(s["success"] for s in solutions) else "infeasible",
        spec=binding,
        solutions=[_serialize_solution(s) for s in solutions],
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


@app.get("/api/specs")
def specs(status: str | None = None, db: Session = Depends(get_db)):
    """列出约束规范族（含全部修订版）。"""
    return [specstore.family_out(f)
            for f in specstore.list_families(db, status=status)]


@app.post("/api/specs", status_code=201)
def create_spec(body: SpecFamilyCreate, db: Session = Depends(get_db)):
    """创建规范族并自带 R1 草稿（SM/IM/KH 区间 + 有害组分限值）。"""
    return specstore.create_family(
        db, spec_code=body.spec_code, name=body.name, note=body.note,
        snapshot=body.snapshot.model_dump(),
    )


@app.get("/api/specs/{family_id}")
def spec_detail(family_id: int, db: Session = Depends(get_db)):
    return specstore.family_out(specstore.get_family(db, family_id))


@app.post("/api/specs/{family_id}/deprecate")
def deprecate_spec(family_id: int, body: DeprecateRequest,
                   db: Session = Depends(get_db)):
    return specstore.deprecate(db, family_id, expected_lock=body.lock_version)


@app.patch("/api/spec-revisions/{revision_id}")
def patch_draft(revision_id: int, body: DraftUpdate,
                db: Session = Depends(get_db)):
    """仅 draft 可改；已发布修订直接拒绝（SPEC_PUBLISHED_IMMUTABLE）。"""
    return specstore.update_draft(
        db, revision_id,
        snapshot=body.snapshot.model_dump(),
        expected_lock=body.lock_version,
        change_note=body.change_note,
    )


@app.delete("/api/spec-revisions/{revision_id}", status_code=204)
def delete_draft(revision_id: int, lock_version: int | None = None,
                 db: Session = Depends(get_db)):
    specstore.delete_draft(db, revision_id, expected_lock=lock_version)


@app.post("/api/spec-revisions/{revision_id}/publish")
def publish_revision(revision_id: int, body: PublishRequest,
                     db: Session = Depends(get_db)):
    """草稿发布为不可编辑修订；同草稿并发发布只有一方成功。"""
    return specstore.publish(
        db, revision_id,
        expected_lock=body.lock_version,
        expected_family_lock=body.family_lock_version,
    )


@app.post("/api/spec-revisions/{revision_id}/copy", status_code=201)
def copy_revision(revision_id: int, body: CopyRequest,
                  db: Session = Depends(get_db)):
    """复制已发布/历史修订为新草稿，供调整限值后再发布。"""
    return specstore.copy_to_draft(
        db, revision_id,
        expected_family_lock=body.family_lock_version,
        change_note=body.change_note,
    )


@app.get("/api/spec-revisions/{revision_id}/runs")
def revision_runs(revision_id: int, db: Session = Depends(get_db)):
    """列出引用了该修订版的全部试算运行（该修订的影响范围）。"""
    return specstore.affected_runs(db, revision_id)


@app.get("/api/spec-revisions/{revision_id}/diff/{other_id}")
def revision_diff(revision_id: int, other_id: int, db: Session = Depends(get_db)):
    """两个修订版之间的参数差异（逐项 from/to）。"""
    return specstore.diff_revisions(db, revision_id, other_id)


@app.get("/api/runs")
def runs(limit: int = 50, db: Session = Depends(get_db)):
    return crud.list_runs(db, limit)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int, db: Session = Depends(get_db)):
    detail = crud.get_run_detail(db, run_id)
    if detail is None:
        raise HTTPException(404, "试算记录不存在。")
    return detail


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