"""Pydantic 入参/出参模型。"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AssayVersionOut(BaseModel):
    id: int
    version: str
    lab_report_no: str
    assayed_at: datetime
    basis: str
    composition: dict
    measured_oxides: list


class MaterialOut(BaseModel):
    id: int
    code: str
    name: str
    category: str
    moisture_pct: float
    cost_per_t_wet: float
    availability_t_wet: float | None
    min_share_pct: float
    is_active: bool
    note: str | None = None
    assay_versions: list[AssayVersionOut] = []


class Interval(BaseModel):
    min: float | None = None
    max: float | None = None


class Targets(BaseModel):
    SM: Interval
    IM: Interval
    KH: Interval


class Candidate(BaseModel):
    material_id: int
    assay_version_id: int | None = None  # 默认取最新版


class BlendRequest(BaseModel):
    scenario_name: str = "未命名试算"
    batch_t_dry: float = Field(default=1000.0, gt=0)
    candidates: list[Candidate]
    targets: Targets
    hazard_limits_pct: dict[str, float] = {}  # 干基 %，如 {"Cl": 0.03, "alkali_eq": 1.5}
    modes: list[Literal["min_cost", "max_cheap", "balanced"]] = ["min_cost"]
    cheap_material_id: int | None = None  # max_cheap 模式的“廉价原料”
    save: bool = True
    # 引用已发布规范修订版：服务端以冻结参数为准，覆盖 targets/hazard_limits_pct
    spec_revision_id: int | None = None


class EvaluateRequest(BaseModel):
    """手工配比试算：直接给干基份额，用于分母为零/缺测报错演示。"""

    scenario_name: str = "手工配比"
    picks: list[Candidate]
    shares_pct_dry: list[float]  # 与 picks 等长；和不强制 100，会归一化


class SolutionItem(BaseModel):
    material_code: str
    material_name: str
    assay_version: str
    lab_report_no: str
    share_pct_dry: float
    mass_t_dry: float
    mass_t_wet: float
    water_t: float
    cost: float
    conversion_trace: dict


class SolutionOut(BaseModel):
    mode: str
    mode_label: str
    success: bool
    total_cost: float | None = None
    cost_per_t_dry: float | None = None
    indicators: dict | None = None
    composition_dry_pct: dict | None = None
    composition_wet_pct: dict | None = None
    water_pct_in_wet_mix: float | None = None
    items: list[SolutionItem] = []
    diagnostic: dict | None = None


class BlendResponse(BaseModel):
    run_id: int | None
    run_code: str
    status: str
    solutions: list[SolutionOut]
    spec: dict | None = None  # 本次试算绑定的规范修订（编号/修订号/冻结参数）


# ---------- 版本化约束规范 ----------

class SpecCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    targets: Targets
    hazard_limits_pct: dict[str, float] = {}
    note: str | None = None


class SpecDraftUpdate(BaseModel):
    """编辑草稿：必须携带读到的 lock_version（乐观并发）。"""

    lock_version: int
    name: str | None = None
    targets: Targets
    hazard_limits_pct: dict[str, float] = {}
    note: str | None = None


class SpecAction(BaseModel):
    """发布/复制/停用：携带 lock_version 做乐观并发校验。"""

    lock_version: int
    note: str | None = None


class SpecRevisionOut(BaseModel):
    id: int
    revision_no: int
    targets: dict
    hazard_limits_pct: dict
    published_at: datetime
    note: str | None = None


class SpecOut(BaseModel):
    id: int
    code: str
    name: str
    status: str  # draft / published / retired
    lock_version: int
    draft_targets: dict | None = None
    draft_hazard_limits_pct: dict | None = None
    note: str | None = None
    created_at: datetime
    revisions: list[SpecRevisionOut] = []
