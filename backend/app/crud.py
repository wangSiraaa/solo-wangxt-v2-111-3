"""数据库读写辅助。"""
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .chemistry import SpecError
from .optimizer import Row, prepare_rows


def list_materials(db: Session, active_only: bool = False):
    stmt = select(models.Material).order_by(models.Material.id)
    if active_only:
        stmt = stmt.where(models.Material.is_active.is_(True))
    mats = list(db.scalars(stmt))
    for m in mats:
        m.assay_versions.sort(key=lambda a: (a.assayed_at, a.id), reverse=True)
    return mats


def resolve_candidates(db: Session, candidates) -> list[tuple]:
    """把 [{material_id, assay_version_id?}] 解析成 (Material, AssayVersion)。"""
    pairs = []
    for c in candidates:
        mat = db.get(models.Material, c.material_id)
        if mat is None:
            from .chemistry import BlendError
            raise BlendError("MATERIAL_NOT_FOUND",
                             f"原料 id={c.material_id} 不存在。",
                             {"material_id": c.material_id})
        if c.assay_version_id is not None:
            ass = db.get(models.AssayVersion, c.assay_version_id)
            if ass is None or ass.material_id != mat.id:
                from .chemistry import BlendError
                raise BlendError("ASSAY_NOT_FOUND",
                                 f"化验版 id={c.assay_version_id} 不属于原料 {mat.code}。")
        else:
            ass = max(mat.assay_versions, key=lambda a: (a.assayed_at, a.id))
        pairs.append((mat, ass))
    return pairs


def rows_from_candidates(db: Session, candidates) -> list[Row]:
    return prepare_rows(resolve_candidates(db, candidates))


def save_run(db: Session, req, solutions: list[dict], scenario_name: str | None = None,
             spec_binding: tuple | None = None):
    spec_snapshot = None
    spec_revision_id = None
    if spec_binding is not None:
        spec, rev = spec_binding
        # 试算引用与停用之间的并发一致性：提交前对规范行加锁复核状态，
        # 若规范在求解期间被停用，则整批拒绝落库。
        locked = db.scalars(
            select(models.ConstraintSpec)
            .where(models.ConstraintSpec.id == spec.id)
            .with_for_update()
        ).one()
        if locked.status == "retired":
            raise SpecError(
                "SPEC_RETIRED",
                f"规范 {locked.code} 已停用，禁止新的试算引用；历史批次仍可回看。",
                {"spec_id": locked.id, "code": locked.code},
            )
        spec_revision_id = rev.id
        spec_snapshot = {
            "spec_id": spec.id,
            "spec_code": spec.code,
            "spec_name": spec.name,
            "revision_id": rev.id,
            "revision_no": rev.revision_no,
            "targets": rev.targets,
            "hazard_limits_pct": rev.hazard_limits_pct,
            "published_at": rev.published_at.isoformat(timespec="seconds"),
        }
    run = models.BlendRun(
        run_code=f"RUN-{uuid.uuid4().hex[:10].upper()}",
        scenario_name=scenario_name or getattr(req, "scenario_name", "试算"),
        batch_t_dry=getattr(req, "batch_t_dry", 1000.0),
        target=req.targets.model_dump(),
        constraint_set={
            "hazard_limits_pct": getattr(req, "hazard_limits_pct", {}),
            "cheap_material_id": getattr(req, "cheap_material_id", None),
            "modes": getattr(req, "modes", []),
        },
        status="feasible" if any(s["success"] for s in solutions) else "infeasible",
        spec_revision_id=spec_revision_id,
        spec_snapshot=spec_snapshot,
    )
    db.add(run)
    db.flush()

    for sol in solutions:
        srec = models.BlendSolution(
            run_id=run.id,
            mode=sol["mode"],
            success=sol["success"],
            total_cost=sol.get("total_cost"),
            indicators={
                "indicators": sol.get("indicators"),
                "composition_dry_pct": sol.get("composition_dry_pct"),
                "composition_wet_pct": sol.get("composition_wet_pct"),
                "water_pct_in_wet_mix": sol.get("water_pct_in_wet_mix"),
                "cost_per_t_dry": sol.get("cost_per_t_dry"),
            },
            diagnostic=sol.get("diagnostic"),
        )
        db.add(srec)
        db.flush()
        for it in sol.get("items", []):
            db.add(models.BlendItem(
                run_id=run.id,
                solution_id=srec.id,
                material_id=it["_material_id"],
                assay_version_id=it["_assay_version_id"],
                share_pct_dry=it["share_pct_dry"],
                mass_t_dry=it["mass_t_dry"],
                mass_t_wet=it["mass_t_wet"],
                water_t=it["water_t"],
                cost=it["cost"],
                conversion_trace=it["conversion_trace"],
                assay_composition_snapshot=it["conversion_trace"]["steps"],
            ))
    db.commit()
    db.refresh(run)
    return run


def get_run_detail(db: Session, run_id: int):
    run = db.get(models.BlendRun, run_id)
    if run is None:
        return None
    out = {
        "id": run.id,
        "run_code": run.run_code,
        "scenario_name": run.scenario_name,
        "batch_t_dry": run.batch_t_dry,
        "target": run.target,
        "constraint_set": run.constraint_set,
        "status": run.status,
        "created_at": run.created_at.isoformat(timespec="seconds"),
        # 冻结在批次上的规范快照：规范日后改版/停用都不改变本批次的解释；
        # 规范出现前的历史批次为 None，按“临时参数”原样回看。
        "spec_snapshot": run.spec_snapshot,
        "solutions": [],
    }
    for s in run.solutions:
        items = []
        for it in s.items:
            mat = db.get(models.Material, it.material_id)
            ass = db.get(models.AssayVersion, it.assay_version_id)
            items.append({
                "material_code": mat.code,
                "material_name": mat.name,
                "assay_version": ass.version,
                "lab_report_no": ass.lab_report_no,
                "share_pct_dry": it.share_pct_dry,
                "mass_t_dry": it.mass_t_dry,
                "mass_t_wet": it.mass_t_wet,
                "water_t": it.water_t,
                "cost": it.cost,
                "conversion_trace": it.conversion_trace,
                "raw_assay": {
                    "basis": ass.basis,
                    "composition": ass.composition,
                    "measured_oxides": ass.measured_oxides,
                },
            })
        out["solutions"].append({
            "mode": s.mode,
            "success": s.success,
            "total_cost": s.total_cost,
            "payload": s.indicators,
            "diagnostic": s.diagnostic,
            "items": items,
        })
    return out


def list_runs(db: Session, limit: int = 50):
    runs = list(db.scalars(
        select(models.BlendRun).order_by(models.BlendRun.id.desc()).limit(limit)
    ))
    return [{
        "id": r.id,
        "run_code": r.run_code,
        "scenario_name": r.scenario_name,
        "status": r.status,
        "created_at": r.created_at.isoformat(timespec="seconds"),
        "modes": [s.mode for s in r.solutions],
        "spec": ({
            "spec_id": r.spec_snapshot["spec_id"],
            "spec_code": r.spec_snapshot["spec_code"],
            "revision_no": r.spec_snapshot["revision_no"],
        } if r.spec_snapshot else None),
    } for r in runs]


# ---------- 版本化约束规范：草稿 → 发布 → 停用 ----------

def _get_spec(db: Session, spec_id: int) -> models.ConstraintSpec:
    spec = db.get(models.ConstraintSpec, spec_id)
    if spec is None:
        raise SpecError("SPEC_NOT_FOUND", f"规范 id={spec_id} 不存在。",
                        {"spec_id": spec_id})
    return spec


def list_specs(db: Session):
    return list(db.scalars(
        select(models.ConstraintSpec).order_by(models.ConstraintSpec.id)
    ))


def create_spec(db: Session, req) -> models.ConstraintSpec:
    if db.scalars(select(models.ConstraintSpec)
                  .where(models.ConstraintSpec.code == req.code)).first():
        raise SpecError("SPEC_CODE_EXISTS",
                        f"规范编号 {req.code} 已存在。", {"code": req.code})
    spec = models.ConstraintSpec(
        code=req.code,
        name=req.name,
        status="draft",
        draft_targets=req.targets.model_dump(),
        draft_hazard_limits_pct=dict(req.hazard_limits_pct),
        note=req.note,
        lock_version=1,
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return spec


def _cas_spec(db: Session, spec_id: int, expect_lock: int,
              expect_status: tuple[str, ...], **fields) -> models.ConstraintSpec:
    """乐观并发 CAS：仅当 lock_version 与状态同时匹配才更新。

    并发下最多一个事务更新成功；其余 rowcount=0，读出当前状态后抛
    SPEC_CONFLICT / SPEC_IMMUTABLE，调用方（前端窗口）可刷新重试。
    """
    stmt = (
        models.ConstraintSpec.__table__.update()
        .where(models.ConstraintSpec.id == spec_id)
        .where(models.ConstraintSpec.lock_version == expect_lock)
        .where(models.ConstraintSpec.status.in_(expect_status))
        .values(**fields, lock_version=expect_lock + 1,
                updated_at=datetime.utcnow())
    )
    res = db.execute(stmt)
    if res.rowcount != 1:
        db.rollback()
        cur = db.get(models.ConstraintSpec, spec_id)
        if cur is None:
            raise SpecError("SPEC_NOT_FOUND", f"规范 id={spec_id} 不存在。",
                            {"spec_id": spec_id})
        if cur.lock_version != expect_lock:
            # 乐观并发：读到的版本已被其他窗口推进（含并发发布同一草稿），
            # 调用方拿到最新状态后可刷新重试。
            raise SpecError(
                "SPEC_CONFLICT",
                f"规范 {cur.code} 已被其他窗口修改（当前 status={cur.status}，"
                f"lock_version={cur.lock_version}），请刷新后基于最新状态重试。",
                {"spec_id": cur.id, "code": cur.code, "status": cur.status,
                 "lock_version": cur.lock_version},
            )
        raise SpecError(
            "SPEC_IMMUTABLE" if cur.status == "published" else "SPEC_BAD_STATE",
            f"规范 {cur.code} 当前状态为 {cur.status}，该操作仅允许在 "
            f"{'/'.join(expect_status)} 状态执行；已发布规范不可直接编辑，"
            "请先复制为新草稿。",
            {"spec_id": cur.id, "code": cur.code, "status": cur.status,
             "lock_version": cur.lock_version},
        )
    db.flush()
    return db.get(models.ConstraintSpec, spec_id)


def update_spec_draft(db: Session, spec_id: int, req) -> models.ConstraintSpec:
    fields = {
        "draft_targets": req.targets.model_dump(),
        "draft_hazard_limits_pct": dict(req.hazard_limits_pct),
        "note": req.note,
    }
    if req.name is not None:
        fields["name"] = req.name
    spec = _cas_spec(db, spec_id, req.lock_version, ("draft",), **fields)
    db.commit()
    db.refresh(spec)
    return spec


def publish_spec(db: Session, spec_id: int, req) -> models.SpecRevision:
    """把草稿冻结为不可变修订版。

    两个窗口基于同一草稿并发发布时，CAS 只允许一个事务把
    draft → published；另一个 rowcount=0 → SPEC_CONFLICT（409），
    因此同一草稿只会产生一个有效修订版。
    """
    spec = _get_spec(db, spec_id)
    if not spec.draft_targets:
        raise SpecError("SPEC_NO_DRAFT", f"规范 {spec.code} 没有可发布的草稿参数。",
                        {"spec_id": spec.id, "code": spec.code})
    next_no = (db.scalar(
        select(func.max(models.SpecRevision.revision_no))
        .where(models.SpecRevision.spec_id == spec_id)
    ) or 0) + 1
    rev = models.SpecRevision(
        spec_id=spec_id,
        revision_no=next_no,
        targets=spec.draft_targets,
        hazard_limits_pct=spec.draft_hazard_limits_pct or {},
        note=req.note,
    )
    db.add(rev)
    _cas_spec(db, spec_id, req.lock_version, ("draft",), status="published")
    db.commit()
    db.refresh(rev)
    return rev


def copy_spec_to_draft(db: Session, spec_id: int, req) -> models.ConstraintSpec:
    """修改已发布规范的唯一入口：把最新修订版复制回草稿，发布后即新修订。"""
    spec = _get_spec(db, spec_id)
    if not spec.revisions:
        raise SpecError("SPEC_BAD_STATE",
                        f"规范 {spec.code} 尚无已发布修订版，可直接编辑草稿。",
                        {"spec_id": spec.id, "code": spec.code})
    latest = spec.revisions[-1]
    spec = _cas_spec(
        db, spec_id, req.lock_version, ("published",),
        status="draft",
        draft_targets=latest.targets,
        draft_hazard_limits_pct=dict(latest.hazard_limits_pct),
    )
    db.commit()
    db.refresh(spec)
    return spec


def retire_spec(db: Session, spec_id: int, req) -> models.ConstraintSpec:
    spec = _cas_spec(db, spec_id, req.lock_version, ("draft", "published"),
                     status="retired")
    db.commit()
    db.refresh(spec)
    return spec


def get_revision(db: Session, spec_id: int, revision_no: int) -> models.SpecRevision:
    rev = db.scalars(
        select(models.SpecRevision)
        .where(models.SpecRevision.spec_id == spec_id)
        .where(models.SpecRevision.revision_no == revision_no)
    ).first()
    if rev is None:
        raise SpecError("REVISION_NOT_FOUND",
                        f"规范 id={spec_id} 不存在修订版 r{revision_no}。",
                        {"spec_id": spec_id, "revision_no": revision_no})
    return rev


def get_revision_with_spec(db: Session, revision_id: int):
    rev = db.get(models.SpecRevision, revision_id)
    if rev is None:
        raise SpecError("REVISION_NOT_FOUND",
                        f"规范修订版 id={revision_id} 不存在。",
                        {"revision_id": revision_id})
    return rev, _get_spec(db, rev.spec_id)


def diff_revisions(db: Session, spec_id: int, from_no: int, to_no: int) -> dict:
    """逐字段比较两个修订版：率值区间与有害组分限值的新增/删除/收紧/放宽。"""
    spec = _get_spec(db, spec_id)
    a = get_revision(db, spec_id, from_no)
    b = get_revision(db, spec_id, to_no)
    changes = []

    def emit(field, old, new):
        if old == new:
            return
        if old is None:
            direction = "新增"
        elif new is None:
            direction = "删除"
        elif field.endswith(".min"):
            direction = "收紧" if new > old else "放宽"
        else:  # 上限类字段：值变小即收紧
            direction = "收紧" if new < old else "放宽"
        changes.append({"field": field, "from": old, "to": new,
                        "direction": direction})

    for ind in ("SM", "IM", "KH"):
        for side in ("min", "max"):
            emit(f"{ind}.{side}",
                 (a.targets.get(ind) or {}).get(side),
                 (b.targets.get(ind) or {}).get(side))
    for key in sorted(set(a.hazard_limits_pct) | set(b.hazard_limits_pct)):
        emit(f"hazard.{key}",
             a.hazard_limits_pct.get(key), b.hazard_limits_pct.get(key))
    return {
        "spec_id": spec.id, "spec_code": spec.code, "spec_name": spec.name,
        "from_revision": from_no, "to_revision": to_no, "changes": changes,
    }


def runs_of_revision(db: Session, spec_id: int, revision_no: int, limit: int = 100):
    """列出某修订版影响（被引用）的全部试算批次。"""
    rev = get_revision(db, spec_id, revision_no)
    runs = list(db.scalars(
        select(models.BlendRun)
        .where(models.BlendRun.spec_revision_id == rev.id)
        .order_by(models.BlendRun.id.desc())
        .limit(limit)
    ))
    return rev, [{
        "id": r.id,
        "run_code": r.run_code,
        "scenario_name": r.scenario_name,
        "status": r.status,
        "created_at": r.created_at.isoformat(timespec="seconds"),
        "modes": [s.mode for s in r.solutions],
    } for r in runs]
