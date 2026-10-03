"""版本化约束规范：生命周期、乐观并发、试算绑定与历史冻结。"""
import threading
import uuid

from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)
T = {"SM": {"min": 2.4, "max": 2.8}, "IM": {"min": 1.4, "max": 1.8},
     "KH": {"min": 0.88, "max": 0.94}}


def _code():
    return f"SPEC-T-{uuid.uuid4().hex[:8].upper()}"


def _create(code=None, hazards=None, targets=None):
    r = c.post("/api/specs", json={
        "code": code or _code(), "name": "测试规范",
        "targets": targets or T,
        "hazard_limits_pct": hazards if hazards is not None
        else {"Cl": 0.03, "alkali_eq": 0.6},
    })
    assert r.status_code == 201, r.text
    return r.json()


def _publish(spec, lv=None, note=None):
    return c.post(f"/api/specs/{spec['id']}/publish",
                  json={"lock_version": lv if lv is not None else spec["lock_version"],
                        "note": note})


def _blend(ids, spec_revision_id=None, save=True, hazards=None):
    body = {
        "scenario_name": "spec-test", "batch_t_dry": 1000,
        "candidates": [{"material_id": i} for i in ids],
        "targets": T, "hazard_limits_pct": hazards or {},
        "modes": ["min_cost"], "save": save,
    }
    if spec_revision_id is not None:
        body["spec_revision_id"] = spec_revision_id
    return c.post("/api/blend", json=body)


def test_spec_lifecycle_publish_immutable_copy_new_revision():
    """①② 草稿→发布→禁直接编辑→复制→改限值→新修订；旧批次结果不变。"""
    spec = _create()
    assert spec["status"] == "draft"

    # 草稿可编辑（携带 lock_version）
    r = c.put(f"/api/specs/{spec['id']}/draft", json={
        "lock_version": spec["lock_version"], "targets": T,
        "hazard_limits_pct": {"Cl": 0.03, "alkali_eq": 0.6},
    })
    assert r.status_code == 200 and r.json()["lock_version"] == 2

    # 发布 → 不可变修订版 r1
    rev1 = _publish(r.json(), note="首版")
    assert rev1.status_code == 201, rev1.text
    rev1 = rev1.json()
    assert rev1["revision_no"] == 1
    assert rev1["targets"]["SM"] == T["SM"]

    # 已发布：直接编辑草稿被拒绝
    spec = c.get(f"/api/specs/{spec['id']}").json()
    assert spec["status"] == "published"
    r = c.put(f"/api/specs/{spec['id']}/draft", json={
        "lock_version": spec["lock_version"], "targets": T,
        "hazard_limits_pct": {"Cl": 0.03},
    })
    assert r.status_code == 409
    assert r.json()["error_code"] == "SPEC_IMMUTABLE"

    # 按 r1 试算并入库
    rb = _blend([1, 2, 3, 4, 5], spec_revision_id=rev1["id"])
    assert rb.status_code == 200, rb.text
    body = rb.json()
    assert body["spec"]["spec_code"] == spec["code"]
    assert body["spec"]["revision_no"] == 1
    run_id = body["run_id"]
    sol_before = c.get(f"/api/runs/{run_id}").json()

    # 复制 → 调整限值 → 发布 r2
    r = c.post(f"/api/specs/{spec['id']}/copy",
               json={"lock_version": spec["lock_version"]})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    draft = r.json()
    assert draft["draft_hazard_limits_pct"]["alkali_eq"] == 0.6  # 从 r1 复制
    r = c.put(f"/api/specs/{spec['id']}/draft", json={
        "lock_version": draft["lock_version"], "targets": T,
        "hazard_limits_pct": {"Cl": 0.03, "alkali_eq": 0.4},  # 收紧碱当量
    })
    rev2 = _publish(r.json())
    assert rev2.status_code == 201
    assert rev2.json()["revision_no"] == 2
    assert rev2.json()["hazard_limits_pct"]["alkali_eq"] == 0.4

    # 修订版不可变：r1 参数保持原值
    spec = c.get(f"/api/specs/{spec['id']}").json()
    r1 = next(rv for rv in spec["revisions"] if rv["revision_no"] == 1)
    assert r1["hazard_limits_pct"]["alkali_eq"] == 0.6

    # 旧批次仍绑定 r1 且结果未被重新解释
    sol_after = c.get(f"/api/runs/{run_id}").json()
    assert sol_after["spec_snapshot"]["revision_no"] == 1
    assert sol_after["spec_snapshot"]["hazard_limits_pct"]["alkali_eq"] == 0.6
    assert sol_after["solutions"] == sol_before["solutions"]

    # 版本差异接口：r1→r2 只有碱当量收紧
    diff = c.get(f"/api/specs/{spec['id']}/diff",
                 params={"from_no": 1, "to_no": 2}).json()
    assert diff["changes"] == [
        {"field": "hazard.alkali_eq", "from": 0.6, "to": 0.4, "direction": "收紧"}]

    # 修订版影响的运行记录
    impacted = c.get(f"/api/specs/{spec['id']}/revisions/1/runs").json()
    assert [r["id"] for r in impacted["runs"]] == [run_id]
    assert c.get(f"/api/specs/{spec['id']}/revisions/2/runs").json()["runs"] == []


def test_concurrent_publish_single_revision():
    """③ 两个窗口基于同一草稿发布：仅一个有效修订，另一个 409。"""
    spec = _create()
    lv = spec["lock_version"]
    results = {}

    def pub(tag):
        results[tag] = _publish(spec, lv=lv)

    t1, t2 = threading.Thread(target=pub, args=("a",)), \
        threading.Thread(target=pub, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    codes = sorted(r.status_code for r in results.values())
    assert codes == [201, 409]
    loser = next(r for r in results.values() if r.status_code == 409)
    assert loser.json()["error_code"] == "SPEC_CONFLICT"
    assert loser.json()["details"]["lock_version"]  # 可处理：带最新状态供刷新

    spec = c.get(f"/api/specs/{spec['id']}").json()
    assert len(spec["revisions"]) == 1
    assert spec["status"] == "published"

    # 用过期 lock_version 再操作同样 409
    r = c.post(f"/api/specs/{spec['id']}/retire", json={"lock_version": lv})
    assert r.status_code == 409
    assert r.json()["error_code"] == "SPEC_CONFLICT"


def test_retire_blocks_new_blend_but_history_readable():
    """④ 停用后禁止新试算引用；历史批次仍可回看且绑定信息完整。"""
    spec = _create()
    rev = _publish(spec).json()
    rb = _blend([1, 2, 3, 4, 5], spec_revision_id=rev["id"])
    run_id = rb.json()["run_id"]

    spec = c.get(f"/api/specs/{spec['id']}").json()
    r = c.post(f"/api/specs/{spec['id']}/retire",
               json={"lock_version": spec["lock_version"]})
    assert r.status_code == 200 and r.json()["status"] == "retired"

    # 新试算引用被拒
    rb2 = _blend([1, 2, 3, 4, 5], spec_revision_id=rev["id"])
    assert rb2.status_code == 409
    assert rb2.json()["error_code"] == "SPEC_RETIRED"

    # 历史回看不受影响，且列表/详情均带规范绑定
    detail = c.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    snap = detail.json()["spec_snapshot"]
    assert snap["spec_code"] == spec["code"] and snap["revision_no"] == 1
    runs = c.get("/api/runs").json()
    entry = next(x for x in runs if x["id"] == run_id)
    assert entry["spec"]["spec_code"] == spec["code"]
    assert entry["spec"]["revision_no"] == 1


def test_spec_hazard_limit_missing_assay_not_feasible():
    """④ 规范要求的有害组分缺测 → MISSING_ASSAY，而不是把方案标为可行。"""
    spec = _create(hazards={"Cl": 0.03, "alkali_eq": 0.6, "K2O": 1.0})
    rev = _publish(spec).json()
    # SP01(id=7) 缺测 Fe2O3/K2O/Na2O/Cl：规范限值要求的组分未测
    r = _blend([1, 7], spec_revision_id=rev["id"])
    assert r.status_code == 422
    body = r.json()
    assert body["error_code"] == "MISSING_ASSAY"
    missing = {(m["material_code"], m["component"])
               for m in body["details"]["missing"]}
    assert ("SP01", "K2O") in missing


def test_legacy_run_without_spec_still_viewable():
    """规范出现前的历史批次（未关联规范）仍可完整回看。"""
    r = _blend([1, 2, 3, 4, 5], save=True)
    assert r.status_code == 200
    assert r.json()["spec"] is None
    run_id = r.json()["run_id"]
    detail = c.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["spec_snapshot"] is None
    assert detail.json()["solutions"][0]["items"]
    entry = next(x for x in c.get("/api/runs").json() if x["id"] == run_id)
    assert entry["spec"] is None


def test_spec_freezes_params_over_request_values():
    """引用规范时以冻结参数为准：请求里的临时限值不生效。"""
    spec = _create(hazards={"Cl": 0.03, "alkali_eq": 0.4})  # 碱当量收紧 → 无解
    rev = _publish(spec).json()
    # 请求里放宽松的临时限值，服务端必须忽略并使用规范冻结值
    r = _blend([1, 2, 3, 4, 5], spec_revision_id=rev["id"],
               hazards={"Cl": 0.05, "alkali_eq": 1.5})
    assert r.status_code == 200
    body = r.json()
    assert body["spec"]["hazard_limits_pct"]["alkali_eq"] == 0.4
    sol = body["solutions"][0]
    assert not sol["success"]  # 按规范冻结限值判定：冲突无解
    kinds = [cf["constraint"] for cf in sol["diagnostic"]["conflicts"]]
    assert any("alkali_eq" in k for k in kinds)
    # 落库的目标/约束也是冻结值
    detail = c.get(f"/api/runs/{body['run_id']}").json()
    assert detail["constraint_set"]["hazard_limits_pct"]["alkali_eq"] == 0.4
