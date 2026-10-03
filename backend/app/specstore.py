"""版本化约束规范的领域服务：草稿 → 发布 → 停用流转、乐观并发、版本差异。

并发一致性口径：
- SpecFamily 行锁（SELECT ... FOR UPDATE）串行化同一规范族的发布/复制/停用；
- lock_version（族级与修订版级）提供乐观并发，失配返回 409 VERSION_CONFLICT；
- 部分唯一索引（draft/published 每族各至多一个）是落库层的最终防线。
发布形成的修订版参数快照不可编辑；改已发布规范只能 copy 出新草稿。
"""
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .config import HAZARDOUS

# 允许登记上限的有害组分键（alkali_eq = Na2O + 0.658 K2O）
KNOWN_HAZARDS = set(HAZARDOUS)
INDICATORS = ("SM", "IM", "KH")


class SpecError(Exception):
    """规范域业务异常，API 层映射为对应 HTTP 状态。"""

    def __init__(self, code: str, message: str, status: int = 409, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


# ---------------- 参数快照校验 ----------------

def normalize_snapshot(payload: dict, *, strict: bool) -> dict:
    """校验并归一化规范参数。

    strict=True（发布时）：SM/IM/KH 必须给出完整合法双边窗口，
    有害限值表必须显式给出（至少含碱当量/Cl 等被该规范采用的键，
    允许空表但字段必须存在）。
    strict=False（草稿编辑）：允许只填一部分或留空（None 边界）。
    """
    if not isinstance(payload, dict):
        raise SpecError("SPEC_PARAMS_INVALID", "规范参数必须是对象。", 400)
    targets_in = payload.get("targets")
    if not isinstance(targets_in, dict):
        raise SpecError("SPEC_PARAMS_INVALID",
                        "规范必须包含 targets（SM/IM/KH 区间）。", 400,
                        {"field": "targets"})

    targets_out: dict[str, dict] = {}
    for key in INDICATORS:
        iv = targets_in.get(key)
        if iv is None:
            if strict:
                raise SpecError("SPEC_PARAMS_INVALID",
                                f"发布前必须给出 {key} 区间。", 400,
                                {"field": f"targets.{key}"})
            iv = {}
        if not isinstance(iv, dict):
            raise SpecError("SPEC_PARAMS_INVALID",
                            f"{key} 区间必须是 {min,max} 对象。", 400,
                            {"field": f"targets.{key}"})
        lo, hi = iv.get("min"), iv.get("max")
        lo = _num_or_none(lo, f"targets.{key}.min")
        hi = _num_or_none(hi, f"targets.{key}.max")
        if strict and (lo is None or hi is None):
            raise SpecError("SPEC_PARAMS_INVALID",
                            f"发布的规范必须给出 {key} 的上下限（双边窗口）。", 400,
                            {"field": f"targets.{key}"})
        if lo is not None and hi is not None and lo > hi:
            raise SpecError("SPEC_PARAMS_INVALID",
                            f"{key} 区间下限 {lo} 不得大于上限 {hi}。", 400,
                            {"field": f"targets.{key}", "min": lo, "max": hi})
        targets_out[key] = {"min": lo, "max": hi}

    hazards_in = payload.get("hazard_limits_pct")
    if hazards_in is None:
        if strict:
            raise SpecError("SPEC_PARAMS_INVALID",
                            "发布的规范必须显式给出 hazard_limits_pct（可为空表）。",
                            400, {"field": "hazard_limits_pct"})
        hazards_in = {}
    if not isinstance(hazards_in, dict):
        raise SpecError("SPEC_PARAMS_INVALID",
                        "hazard_limits_pct 必须是 {组分: 干基上限%} 对象。", 400)
    hazards_out: dict[str, float] = {}
    for k, v in hazards_in.items():
        if k not in KNOWN_HAZARDS:
            raise SpecError("SPEC_PARAMS_INVALID",
                            f"未知有害组分键 {k}，允许：{sorted(KNOWN_HAZARDS)}。",
                            400, {"field": f"hazard_limits_pct.{k}"})
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise SpecError("SPEC_PARAMS_INVALID",
                            f"{k} 的限值必须是数值。", 400,
                            {"field": f"hazard_limits_pct.{k}"})
        if v < 0:
            raise SpecError("SPEC_PARAMS_INVALID",
                            f"{k} 的有害组分上限不得为负。", 400,
                            {"field": f"hazard_limits_pct.{k}", "value": v})
        hazards_out[k] = float(v)

    return {"targets": targets_out, "hazard_limits_pct": hazards_out}


def _num_or_none(v, field: str):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SpecError("SPEC_PARAMS_INVALID", f"{field} 必须是数值或 null。", 400,
                        {"field": field, "value": v})
    return float(v)


# ---------------- 序列化 ----------------

def revision_out(rev: models.SpecRevision) -> dict:
    return {
        "id": rev.id,
        "family_id": rev.family_id,
        "spec_code": rev.family.spec_code,
        "spec_name": rev.family.name,
        "revision_no": rev.revision_no,
        "status": rev.status,
        "spec_snapshot": deepcopy(rev.spec_snapshot),
        "lock_version": rev.lock_version,
        "created_at": rev.created_at.isoformat(timespec="seconds"),
        "published_at": rev.published_at.isoformat(timespec="seconds") if rev.published_at else None,
        "superseded_at": rev.superseded_at.isoformat(timespec="seconds") if rev.superseded_at else None,
        "created_from_revision_id": rev.created_from_revision_id,
        "change_note": rev.change_note,
    }


def family_out(fam: models.SpecFamily) -> dict:
    revs = sorted(fam.revisions, key=lambda r: r.id)
    current = next((r for r in revs if r.status == "published"), None)
    draft = next((r for r in revs if r.status == "draft"), None)
    return {
        "id": fam.id,
        "spec_code": fam.spec_code,
        "name": fam.name,
        "status": fam.status,
        "lock_version": fam.lock_version,
        "note": fam.note,
        "created_at": fam.created_at.isoformat(timespec="seconds"),
        "deprecated_at": fam.deprecated_at.isoformat(timespec="seconds") if fam.deprecated_at else None,
        "current_revision_id": current.id if current else None,
        "current_revision_no": current.revision_no if current else None,
        "draft_revision_id": draft.id if draft else None,
        "revisions": [revision_out(r) for r in revs],
    }


# ---------------- 查询 ----------------

def list_families(db: Session, status: str | None = None) -> list[models.SpecFamily]:
    stmt = select(models.SpecFamily).order_by(models.SpecFamily.id)
    fams = list(db.scalars(stmt))
    if status:
        fams = [f for f in fams if f.status == status]
    return fams


def get_family(db: Session, family_id: int) -> models.SpecFamily:
    fam = db.get(models.SpecFamily, family_id)
    if fam is None:
        raise SpecError("SPEC_NOT_FOUND", f"规范族 id={family_id} 不存在。", 404,
                        {"family_id": family_id})
    return fam


def get_family_locked(db: Session, family_id: int) -> models.SpecFamily:
    """族级行锁：发布/复制/停用必须在同一事务内持锁完成。"""
    fam = db.scalars(
        select(models.SpecFamily).where(models.SpecFamily.id == family_id).with_for_update()
    ).first()
    if fam is None:
        raise SpecError("SPEC_NOT_FOUND", f"规范族 id={family_id} 不存在。", 404,
                        {"family_id": family_id})
    return fam


def get_revision(db: Session, revision_id: int) -> models.SpecRevision:
    rev = db.get(models.SpecRevision, revision_id)
    if rev is None:
        raise SpecError("SPEC_REVISION_NOT_FOUND",
                        f"规范修订版 id={revision_id} 不存在。", 404,
                        {"revision_id": revision_id})
    return rev


def get_revision_locked(db: Session, revision_id: int) -> models.SpecRevision:
    """修订版行锁：草稿编辑/发布在事务内持锁，保证乐观锁检查不发生脏读。"""
    rev = db.scalars(
        select(models.SpecRevision)
        .where(models.SpecRevision.id == revision_id)
        .with_for_update()
    ).first()
    if rev is None:
        raise SpecError("SPEC_REVISION_NOT_FOUND",
                        f"规范修订版 id={revision_id} 不存在。", 404,
                        {"revision_id": revision_id})
    return rev


def _check_lock(obj, expected: int | None):
    if expected is not None and obj.lock_version != expected:
        raise SpecError(
            "VERSION_CONFLICT",
            "规范已被他人修改（乐观锁版本不一致），请刷新后重试。",
            409,
            {"expected_lock_version": expected,
             "current_lock_version": obj.lock_version},
        )


def _draft_of(fam: models.SpecFamily) -> models.SpecRevision | None:
    return next((r for r in fam.revisions if r.status == "draft"), None)


def _published_of(fam: models.SpecFamily) -> models.SpecRevision | None:
    return next((r for r in fam.revisions if r.status == "published"), None)


def _next_revision_no(fam: models.SpecFamily) -> str:
    nums = []
    for r in fam.revisions:
        if r.revision_no.startswith("R"):
            try:
                nums.append(int(r.revision_no[1:]))
            except ValueError:
                pass
    return f"R{max(nums, default=0) + 1}"


# ---------------- 族与草稿 ----------------

def create_family(db: Session, *, spec_code: str, name: str, note: str | None,
                  snapshot: dict) -> dict:
    exists = db.scalars(
        select(models.SpecFamily).where(models.SpecFamily.spec_code == spec_code)
    ).first()
    if exists is not None:
        raise SpecError("SPEC_CODE_EXISTS", f"规范编号 {spec_code} 已存在。", 409,
                        {"spec_code": spec_code})
    clean = normalize_snapshot(snapshot, strict=False)
    fam = models.SpecFamily(spec_code=spec_code, name=name, note=note,
                            status="active", lock_version=0)
    db.add(fam)
    db.flush()
    rev = models.SpecRevision(
        family_id=fam.id, revision_no="R1", status="draft",
        spec_snapshot=clean, lock_version=0,
    )
    db.add(rev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SpecError("SPEC_CODE_EXISTS", f"规范编号 {spec_code} 已存在。", 409,
                        {"spec_code": spec_code})
    db.refresh(fam)
    return family_out(fam)


def update_draft(db: Session, revision_id: int, *, snapshot: dict,
                 expected_lock: int | None, change_note: str | None = None) -> dict:
    rev = get_revision_locked(db, revision_id)
    if rev.status != "draft":
        raise SpecError(
            "SPEC_PUBLISHED_IMMUTABLE",
            f"修订版 {rev.revision_no} 已发布（{rev.status}），参数不可编辑；"
            "请复制出新版本后再调整。",
            409,
            {"revision_id": rev.id, "status": rev.status},
        )
    _check_lock(rev, expected_lock)
    rev.spec_snapshot = normalize_snapshot(snapshot, strict=False)
    if change_note is not None:
        rev.change_note = change_note
    rev.lock_version += 1
    db.commit()
    db.refresh(rev)
    return revision_out(rev)


def delete_draft(db: Session, revision_id: int, expected_lock: int | None) -> None:
    rev = get_revision_locked(db, revision_id)
    if rev.status != "draft":
        raise SpecError("SPEC_PUBLISHED_IMMUTABLE",
                        f"修订版 {rev.revision_no} 状态为 {rev.status}，不可删除。",
                        409, {"revision_id": rev.id, "status": rev.status})
    _check_lock(rev, expected_lock)
    db.delete(rev)
    db.commit()


# ---------------- 发布（不可变修订） ----------------

def publish(db: Session, revision_id: int, *, expected_lock: int | None,
            expected_family_lock: int | None) -> dict:
    """把草稿冻结为不可编辑的有效修订。

    同一规范族串行：旧的 published 转为 superseded。两个窗口基于同一草稿
    并发发布时，只有一个成功，另一方拿到 409 VERSION_CONFLICT。
    """
    rev = get_revision(db, revision_id)
    fam = get_family_locked(db, rev.family_id)  # 族锁，串行化发布/复制/停用
    # 拿到族锁后重读修订行（FOR UPDATE + 覆盖身份映射），确保看到并发方刚提交的状态
    rev = db.scalars(
        select(models.SpecRevision)
        .where(models.SpecRevision.id == revision_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    _check_lock(fam, expected_family_lock)
    _check_lock(rev, expected_lock)

    if fam.status == "deprecated":
        raise SpecError("SPEC_FAMILY_DEPRECATED",
                        f"规范 {fam.spec_code} 已停用，不能再发布修订。",
                        409, {"family_id": fam.id})
    if rev.status != "draft":
        raise SpecError(
            "VERSION_CONFLICT",
            f"修订版 {rev.revision_no} 已是 {rev.status} 状态，不能重复发布；"
            "该规范当前只有一个有效修订。",
            409,
            {"revision_id": rev.id, "status": rev.status,
             "current_revision_id": (_published_of(fam) or rev).id},
        )

    # 发布前完整校验：SM/IM/KH 双边窗口 + 显式有害限值表
    clean = normalize_snapshot(rev.spec_snapshot, strict=True)
    rev.spec_snapshot = clean

    now = datetime.utcnow()
    old = _published_of(fam)
    if old is not None:
        old.status = "superseded"
        old.superseded_at = now
    rev.status = "published"
    rev.published_at = now
    rev.lock_version += 1
    fam.lock_version += 1
    try:
        db.commit()
    except IntegrityError:
        # 部分唯一索引兜底：并发发布同一草稿/同族双 published
        db.rollback()
        raise SpecError(
            "VERSION_CONFLICT",
            "并发发布冲突：该规范只允许一个有效修订，另一方的发布未生效，请刷新后处理。",
            409, {"family_id": fam.id, "revision_id": rev.id},
        )
    db.refresh(rev)
    return revision_out(rev)


# ---------------- 复制出新版本 ----------------

def copy_to_draft(db: Session, revision_id: int, *, expected_family_lock: int | None,
                  change_note: str | None) -> dict:
    """从任意修订（含已发布/已停用）复制出可编辑的新草稿。"""
    src = get_revision(db, revision_id)
    fam = get_family_locked(db, src.family_id)
    # 族锁后重读源修订（可能并发方刚发布/取代），以最新快照复制
    src = db.scalars(
        select(models.SpecRevision)
        .where(models.SpecRevision.id == revision_id)
        .execution_options(populate_existing=True)
    ).one()
    _check_lock(fam, expected_family_lock)
    if fam.status == "deprecated":
        raise SpecError("SPEC_FAMILY_DEPRECATED",
                        f"规范 {fam.spec_code} 已停用，不能再派生新版本。",
                        409, {"family_id": fam.id})
    if _draft_of(fam) is not None:
        raise SpecError(
            "SPEC_DRAFT_EXISTS",
            f"规范 {fam.spec_code} 已存在草稿修订，请先发布或删除该草稿后再复制。",
            409,
            {"family_id": fam.id, "draft_revision_id": _draft_of(fam).id},
        )
    rev = models.SpecRevision(
        family_id=fam.id,
        revision_no=_next_revision_no(fam),
        status="draft",
        spec_snapshot=deepcopy(src.spec_snapshot),
        lock_version=0,
        created_from_revision_id=src.id,
        change_note=change_note,
    )
    db.add(rev)
    fam.lock_version += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise SpecError(
            "VERSION_CONFLICT",
            "并发冲突：复制新版本未生效（可能已有草稿），请刷新后重试。",
            409, {"family_id": fam.id},
        )
    db.refresh(rev)
    return revision_out(rev)


# ---------------- 停用 ----------------

def deprecate(db: Session, family_id: int, *, expected_lock: int | None) -> dict:
    fam = get_family_locked(db, family_id)
    _check_lock(fam, expected_lock)
    if fam.status == "deprecated":
        raise SpecError("SPEC_ALREADY_DEPRECATED",
                        f"规范 {fam.spec_code} 已处于停用状态。", 409,
                        {"family_id": fam.id})
    now = datetime.utcnow()
    fam.status = "deprecated"
    fam.deprecated_at = now
    fam.lock_version += 1
    for r in fam.revisions:
        if r.status in ("published", "draft"):
            r.status = "deprecated"
            r.lock_version += 1
    db.commit()
    db.refresh(fam)
    return family_out(fam)


# ---------------- 试算引用 ----------------

def resolve_for_blend(db: Session, revision_id: int, lock: bool = False) -> models.SpecRevision:
    """试算引用规范：只允许引用规范族当前有效的 published 修订。

    停用规范（deprecated）禁止新试算；引用 superseded 旧修订或草稿一律拒绝。
    lock=True 时对族与修订行加 FOR UPDATE，行锁持有到试算结果入库提交，
    与停用/发布/复制串行化：避免求解期间规范被停用/取代后仍写入新引用批次。
    """
    rev = db.get(models.SpecRevision, revision_id)
    if rev is None:
        raise SpecError("SPEC_REVISION_NOT_FOUND",
                        f"规范修订版 id={revision_id} 不存在。", 404,
                        {"revision_id": revision_id})
    # 锁顺序固定为“族 → 修订”，与发布一致，避免互锁死锁
    q = select(models.SpecFamily).where(models.SpecFamily.id == rev.family_id)
    if lock:
        q = q.with_for_update()
    fam = db.scalars(q).first()
    if lock:
        rev = db.scalars(
            select(models.SpecRevision)
            .where(models.SpecRevision.id == revision_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
    if fam is None:
        raise SpecError("SPEC_NOT_USABLE", "规范族不存在，不能用于新试算。", 409)
    if fam.status == "deprecated" or rev.status == "deprecated":
        raise SpecError(
            "SPEC_NOT_USABLE",
            f"规范 {fam.spec_code}（修订 {rev.revision_no}）已停用，"
            "不能用于新试算（历史记录仍可回看）。",
            409,
            {"revision_id": rev.id, "revision_no": rev.revision_no,
             "status": "deprecated", "family_status": "deprecated"},
        )
    if rev.status == "draft":
        raise SpecError(
            "SPEC_NOT_USABLE",
            f"修订 {rev.revision_no} 还是草稿，未发布的规范不能用于试算。",
            409,
            {"revision_id": rev.id, "revision_no": rev.revision_no,
             "status": "draft", "family_status": fam.status},
        )
    current = _published_of(fam)
    if current is None or current.id != rev.id:
        raise SpecError(
            "SPEC_NOT_CURRENT",
            f"修订 {rev.revision_no} 已被新有效修订取代，"
            "历史方案不随新规范重新解释；请引用当前有效修订发起试算。",
            409,
            {"revision_id": rev.id, "revision_no": rev.revision_no,
             "status": rev.status,
             "current_revision_id": current.id if current else None,
             "current_revision_no": current.revision_no if current else None},
        )
    return rev


# ---------------- 影响范围与版本差异 ----------------

def affected_runs(db: Session, revision_id: int, limit: int = 200) -> list[dict]:
    rev = get_revision(db, revision_id)
    rows = list(db.scalars(
        select(models.BlendRun)
        .where(models.BlendRun.spec_revision_id == rev.id)
        .order_by(models.BlendRun.id.desc())
        .limit(limit)
    ))
    return [{
        "id": r.id,
        "run_code": r.run_code,
        "scenario_name": r.scenario_name,
        "status": r.status,
        "created_at": r.created_at.isoformat(timespec="seconds"),
        "spec_code": r.spec_code,
        "spec_revision_no": r.spec_revision_no,
    } for r in rows]


def _flatten(snapshot: dict, prefix: str = "") -> dict:
    out: dict[str, float | None] = {}
    for k, v in snapshot.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, path))
        else:
            out[path] = v
    return out


def diff_revisions(db: Session, from_id: int, to_id: int) -> dict:
    a, b = get_revision(db, from_id), get_revision(db, to_id)
    fa, fb = _flatten(a.spec_snapshot), _flatten(b.spec_snapshot)
    changes = []
    for path in sorted(set(fa) | set(fb)):
        va, vb = fa.get(path), fb.get(path)
        if va != vb:
            changes.append({"path": path, "from": va, "to": vb})
    return {
        "from_revision": revision_out(a),
        "to_revision": revision_out(b),
        "changes": changes,
    }
