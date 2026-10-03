"""版本化约束规范端到端测试：发布/复制/停用、乐观并发、试算冻结绑定。

依赖已播种的 PostgreSQL（虚构演示数据）。
"""
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app import specstore
from app.database import SessionLocal
from app.main import app

c = TestClient(app)

T = {"SM": {"min": 2.4, "max": 2.8}, "IM": {"min": 1.4, "max": 1.8},
     "KH": {"min": 0.88, "max": 0.94}}
CAND5 = [{"material_id": i} for i in (1, 2, 3, 4, 5)]


def _unique_code(prefix="T"):
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def _create_family(code=None, name="测试规范", snapshot=None, publish=False):
    code = code or _unique_code()
    snap = snapshot or {"targets": T, "hazard_limits_pct": {"Cl": 0.05}}
    r = c.post("/api/specs", json={
        "spec_code": code, "name": name, "snapshot": snap,
    })
    assert r.status_code == 201, r.text
    fam = r.json()
    if publish:
        pr = c.post(f"/api/spec-revisions/{fam['draft_revision_id']}/publish", json={})
        assert pr.status_code == 200, pr.text
        fam = c.get(f"/api/specs/{fam['id']}").json()
    return fam


def _blend(spec_revision_id=None, ids=(1, 2, 3, 4, 5), hazards=None,
           targets=None, save=True, modes=("min_cost",)):
    body = {
        "scenario_name": "spec-test", "batch_t_dry": 1000,
        "candidates": [{"material_id": i} for i in ids],
        "modes": list(modes), "save": save,
    }
    if spec_revision_id is not None:
        body["spec_revision_id"] = spec_revision_id
        # 故意夹带临时参数，验证服务端以冻结快照为准
        body["targets"] = targets or T
        body["hazard_limits_pct"] = hazards if hazards is not None else {"Cl": 99.0}
    else:
        body["targets"] = targets or T
        body["hazard_limits_pct"] = hazards or {}
    return c.post("/api/blend", json=body)


# ---------- ① 发布“低碱”规范并试算，结果与历史绑定 ----------

def test_publish_low_alkali_and_blend_binds_version():
    fam = _create_family(
        code=_unique_code("LA"), name="低碱规范",
        snapshot={"targets": {"SM": {"min": 2.4, "max": 2.7},
                              "IM": {"min": 1.4, "max": 1.7},
                              "KH": {"min": 0.89, "max": 0.93}},
                  "hazard_limits_pct": {"Cl": 0.03, "alkali_eq": 0.6}},
    )
    draft_id = fam["draft_revision_id"]
    # 草稿不能直接用于试算
    pre = _blend(spec_revision_id=draft_id)
    assert pre.status_code == 409
    assert pre.json()["error_code"] == "SPEC_NOT_USABLE"

    pr = c.post(f"/api/spec-revisions/{draft_id}/publish", json={})
    assert pr.status_code == 200
    published = pr.json()
    assert published["status"] == "published"
    assert published["revision_no"] == "R1"

    r = _blend(spec_revision_id=published["id"])
    assert r.status_code == 200, r.text
    data = r.json()
    # 结果明确绑定规范编号 + 修订号 + 完整快照
    assert data["spec"]["spec_code"] == fam["spec_code"]
    assert data["spec"]["revision_no"] == "R1"
    assert data["spec"]["revision_id"] == published["id"]
    assert data["spec"]["snapshot"]["hazard_limits_pct"]["alkali_eq"] == 0.6
    run_id = data["run_id"]

    detail = c.get(f"/api/runs/{run_id}").json()
    # 历史页同样明确绑定该版本，且快照冻结在批次上
    assert detail["spec"]["spec_code"] == fam["spec_code"]
    assert detail["spec"]["revision_no"] == "R1"
    assert detail["spec"]["revision_id"] == published["id"]
    assert detail["spec"]["snapshot"]["hazard_limits_pct"]["alkali_eq"] == 0.6

    # 该修订的影响运行列表包含本批次
    runs = c.get(f"/api/spec-revisions/{published['id']}/runs").json()
    assert any(x["id"] == run_id for x in runs)

    listed = c.get("/api/runs").json()
    row = next(x for x in listed if x["id"] == run_id)
    assert row["spec_code"] == fam["spec_code"]
    assert row["spec_revision_no"] == "R1"


def test_blend_uses_frozen_snapshot_not_body_params():
    fam = _create_family(snapshot={"targets": T,
                                   "hazard_limits_pct": {"Cl": 0.05}}, publish=True)
    rid = fam["current_revision_id"]
    # 请求体给出极宽窗口/极大 Cl 上限，必须被忽略
    loose = {"SM": {"min": 0.0, "max": 99.0}, "IM": {"min": 0.0, "max": 99.0},
             "KH": {"min": 0.0, "max": 9.0}}
    r = _blend(spec_revision_id=rid, hazards={"Cl": 999.0}, targets=loose)
    assert r.status_code == 200
    sol = r.json()["solutions"][0]
    assert sol["success"]
    # 结果满足冻结快照的原始窗口，而非请求体放宽后的窗口
    ind = sol["indicators"]
    assert 2.4 - 1e-6 <= ind["SM"] <= 2.8 + 1e-6
    run = c.get(f"/api/runs/{r.json()['run_id']}").json()
    assert run["target"]["SM"] == {"min": 2.4, "max": 2.8}
    assert run["constraint_set"]["hazard_limits_pct"] == {"Cl": 0.05}


# ---------- ② 已发布不可编辑；复制出新修订且旧结果不变 ----------

def test_published_is_immutable_copy_makes_new_revision():
    fam = _create_family(snapshot={"targets": T,
                                   "hazard_limits_pct": {"Cl": 0.05}}, publish=True)
    r1 = fam["current_revision_id"]

    # 直接 PATCH 已发布修订 → 拒绝
    blocked = c.patch(f"/api/spec-revisions/{r1}", json={
        "snapshot": {"targets": T, "hazard_limits_pct": {"Cl": 0.01}},
        "lock_version": 0,
    })
    assert blocked.status_code == 409
    assert blocked.json()["error_code"] == "SPEC_PUBLISHED_IMMUTABLE"
    # DELETE 同样拒绝
    assert c.delete(f"/api/spec-revisions/{r1}").status_code == 409

    # 先按 R1 跑一批，留作回归对照
    before = _blend(spec_revision_id=r1)
    assert before.status_code == 200
    before_cost = before.json()["solutions"][0]["total_cost"]
    old_run_id = before.json()["run_id"]

    # 复制出新草稿（R2）并收紧 Cl 限值后发布
    cp = c.post(f"/api/spec-revisions/{r1}/copy", json={})
    assert cp.status_code == 201, cp.text
    r2_draft = cp.json()
    assert r2_draft["revision_no"] == "R2" and r2_draft["status"] == "draft"
    upd = c.patch(f"/api/spec-revisions/{r2_draft['id']}", json={
        "snapshot": {"targets": T, "hazard_limits_pct": {"Cl": 0.004}},
        "lock_version": 0,
    })
    assert upd.status_code == 200, upd.text
    pub2 = c.post(f"/api/spec-revisions/{r2_draft['id']}/publish", json={})
    assert pub2.status_code == 200, pub2.text
    fam2 = c.get(f"/api/specs/{fam['id']}").json()
    assert fam2["current_revision_no"] == "R2"

    # 旧修订已被取代：不能再引用它发起新试算
    stale = _blend(spec_revision_id=r1)
    assert stale.status_code == 409
    assert stale.json()["error_code"] == "SPEC_NOT_CURRENT"

    # 历史旧批次不被重新解释：仍绑定 R1、参数快照与成本结果不变
    old_detail = c.get(f"/api/runs/{old_run_id}").json()
    assert old_detail["spec"]["revision_no"] == "R1"
    assert old_detail["spec"]["snapshot"]["hazard_limits_pct"] == {"Cl": 0.05}
    assert old_detail["solutions"][0]["total_cost"] == before_cost

    # R1 → R2 差异接口只列出真正变化的叶子路径
    diff = c.get(f"/api/spec-revisions/{r1}/diff/{r2_draft['id']}").json()
    paths = {ch["path"]: (ch["from"], ch["to"]) for ch in diff["changes"]}
    assert paths["hazard_limits_pct.Cl"] == (0.05, 0.004)
    assert "targets.SM.max" not in paths


def test_copy_while_draft_exists_conflicts_and_is_recoverable():
    fam = _create_family()  # 自带 R1 草稿
    # 草稿未发布时再复制 → 可处理冲突
    r = c.post(f"/api/spec-revisions/{fam['draft_revision_id']}/copy", json={})
    assert r.status_code == 409
    assert r.json()["error_code"] == "SPEC_DRAFT_EXISTS"
    # 处理掉草稿（发布）后即可复制
    c.post(f"/api/spec-revisions/{fam['draft_revision_id']}/publish", json={})
    fam = c.get(f"/api/specs/{fam['id']}").json()
    r2 = c.post(f"/api/spec-revisions/{fam['current_revision_id']}/copy", json={})
    assert r2.status_code == 201


def test_optimistic_lock_rejects_stale_version():
    fam = _create_family()
    draft = fam["draft_revision_id"]
    ok = c.patch(f"/api/spec-revisions/{draft}", json={
        "snapshot": {"targets": T, "hazard_limits_pct": {}}, "lock_version": 0,
    })
    assert ok.status_code == 200
    # 仍拿旧 lock_version=0 提交 → 409
    bad = c.patch(f"/api/spec-revisions/{draft}", json={
        "snapshot": {"targets": T, "hazard_limits_pct": {"Cl": 0.01}},
        "lock_version": 0,
    })
    assert bad.status_code == 409
    assert bad.json()["error_code"] == "VERSION_CONFLICT"
    # 带新版本号可继续
    good = c.patch(f"/api/spec-revisions/{draft}", json={
        "snapshot": {"targets": T, "hazard_limits_pct": {"Cl": 0.01}},
        "lock_version": 1,
    })
    assert good.status_code == 200


# ---------- ③ 两个窗口基于同一草稿并发发布，只产生一个有效修订 ----------

def test_concurrent_publish_only_one_wins():
    fam = _create_family()
    draft_id = fam["draft_revision_id"]
    barrier = threading.Barrier(2)
    results = []

    def worker():
        db = SessionLocal()
        barrier.wait()
        try:
            out = specstore.publish(db, draft_id,
                                    expected_lock=0, expected_family_lock=0)
            results.append(("ok", out["id"], out["status"]))
        except specstore.SpecError as e:
            results.append(("conflict", e.code, e.status))
        finally:
            db.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    statuses = sorted(r[0] for r in results)
    assert statuses == ["conflict", "ok"], results
    loser = next(r for r in results if r[0] == "conflict")
    assert loser[1] == "VERSION_CONFLICT"

    fresh = c.get(f"/api/specs/{fam['id']}").json()
    published = [r for r in fresh["revisions"] if r["status"] == "published"]
    drafts = [r for r in fresh["revisions"] if r["status"] == "draft"]
    assert len(published) == 1 and len(drafts) == 0
    # 失败方拿到冲突后可复制出新草稿继续工作
    cp = c.post(f"/api/spec-revisions/{published[0]['id']}/copy", json={})
    assert cp.status_code == 201


# ---------- ④ 停用后禁止新试算但历史可回看；缺测仍报 MISSING_ASSAY ----------

def test_deprecate_blocks_new_blend_but_history_remains():
    fam = _create_family(snapshot={"targets": T,
                                   "hazard_limits_pct": {"Cl": 0.05}}, publish=True)
    rid = fam["current_revision_id"]
    r = _blend(spec_revision_id=rid)
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    dep = c.post(f"/api/specs/{fam['id']}/deprecate",
                 json={"lock_version": fam["lock_version"]})
    assert dep.status_code == 200, dep.text
    assert dep.json()["status"] == "deprecated"

    blocked = _blend(spec_revision_id=rid)
    assert blocked.status_code == 409
    assert blocked.json()["error_code"] == "SPEC_NOT_USABLE"
    # 停用规范不能重复停用/再发布/复制
    fam2 = c.get(f"/api/specs/{fam['id']}").json()
    assert c.post(f"/api/specs/{fam['id']}/deprecate",
                  json={"lock_version": fam2["lock_version"]}).status_code == 409

    # 历史仍可回看
    detail = c.get(f"/api/runs/{run_id}").json()
    assert detail["spec"]["revision_no"] == "R1"
    runs = c.get(f"/api/spec-revisions/{rid}/runs").json()
    assert any(x["id"] == run_id for x in runs)


def test_publish_requires_complete_windows():
    r = c.post("/api/specs", json={
        "spec_code": _unique_code("BAD"), "name": "缺窗口规范",
        "snapshot": {"targets": {"SM": {"min": 2.4}, "IM": {"min": 1.4, "max": 1.8},
                                 "KH": {"min": 0.88, "max": 0.94}},
                     "hazard_limits_pct": {}},
    })
    assert r.status_code == 201  # 草稿允许不完整
    draft = r.json()["draft_revision_id"]
    pub = c.post(f"/api/spec-revisions/{draft}/publish", json={})
    assert pub.status_code == 400
    assert pub.json()["error_code"] == "SPEC_PARAMS_INVALID"


def test_missing_assay_under_spec_still_422_not_feasible():
    fam = _create_family(
        snapshot={"targets": T,
                  "hazard_limits_pct": {"Cl": 0.05, "alkali_eq": 1.5}},
        publish=True)
    rid = fam["current_revision_id"]
    # SP01(id=7) 缺 Fe2O3，且 K2O/Na2O/Cl 未测 → 必须 MISSING_ASSAY
    r = _blend(spec_revision_id=rid, ids=(1, 2, 7))
    assert r.status_code == 422
    err = r.json()
    assert err["error_code"] == "MISSING_ASSAY"
    missing = {m["component"] for m in err["details"]["missing"]}
    assert "Fe2O3" in missing
    # 缺测在求解前抛出，早于入库：该规范下不应产生任何批次，更不会被标为可行
    runs = c.get(f"/api/spec-revisions/{rid}/runs").json()
    assert runs == []


def test_legacy_runs_without_spec_remain_viewable():
    # 不带规范的旧式试算仍可保存与回看
    r = _blend(ids=(1, 2, 3, 4, 5))
    assert r.status_code == 200
    assert r.json()["spec"] is None
    run_id = r.json()["run_id"]
    detail = c.get(f"/api/runs/{run_id}").json()
    assert detail["spec"] is None


def test_blend_holds_lock_until_persist_serializes_with_deprecate():
    """试算引用持锁到入库；停用必须等试算提交后才完成（或反之），两者不交错。"""
    fam = _create_family(snapshot={"targets": T,
                                   "hazard_limits_pct": {"Cl": 0.05}}, publish=True)
    rid = fam["current_revision_id"]
    order = []

    def do_blend():
        db = SessionLocal()
        try:
            rev = specstore.resolve_for_blend(db, rid, lock=True)
            order.append("blend-locked")
            import time as _t
            _t.sleep(0.3)  # 模拟求解耗时
            # 最小入库：直接写一条绑定该修订的批次
            from app import models
            run = models.BlendRun(
                run_code=f"RUN-LOCK-{uuid.uuid4().hex[:8].upper()}",
                scenario_name="lock-serial", batch_t_dry=1000.0,
                target=rev.spec_snapshot["targets"],
                constraint_set={"hazard_limits_pct": rev.spec_snapshot["hazard_limits_pct"],
                                "modes": ["min_cost"]},
                status="feasible", spec_revision_id=rev.id,
                spec_code=rev.family.spec_code, spec_revision_no=rev.revision_no,
                spec_snapshot=dict(rev.spec_snapshot),
            )
            db.add(run); db.commit()
            order.append("blend-committed")
        finally:
            db.close()

    def do_deprecate():
        import time as _t
        _t.sleep(0.1)  # 确保先让 blend 拿到锁
        db = SessionLocal()
        try:
            specstore.deprecate(db, fam["id"], expected_lock=fam["lock_version"])
            order.append("deprecate-committed")
        finally:
            db.close()

    t1 = threading.Thread(target=do_blend)
    t2 = threading.Thread(target=do_deprecate)
    t1.start(); t2.start(); t1.join(timeout=10); t2.join(timeout=10)
    assert not t1.is_alive() and not t2.is_alive()
    # 严格串行：blend 提交在 deprecate 之前（停用等待行锁）
    assert order == ["blend-locked", "blend-committed", "deprecate-committed"], order
    # 停用完成后，该修订不能再引用；但刚入库的批次可回看
    assert _blend(spec_revision_id=rid).status_code == 409
    runs = c.get(f"/api/spec-revisions/{rid}/runs").json()
    assert any(x["scenario_name"] == "lock-serial" for x in runs)
