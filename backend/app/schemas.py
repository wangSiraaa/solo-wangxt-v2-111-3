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
    # 引用版本化约束规范时以服务端冻结快照为准，二者可省略（旧调用方式保留兼容）
    spec_revision_id: int | None = None
    targets: Targets | None = None
    hazard_limits_pct: dict[str, float] | None = None  # 干基 %，如 {"Cl": 0.03, "alkali_eq": 1.5}
    modes: list[Literal["min_cost", "max_cheap", "balanced"]] = ["min_cost"]
    cheap_material_id: int | None = None  # max_cheap 模式的“廉价原料”
    save: bool = True


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


# ---------------- 版本化约束规范 ----------------

class SpecBinding(BaseModel):
    """试算与规范修订的绑定信息（结果页/历史页明确展示）。"""

    revision_id: int
    spec_code: str
    spec_name: str | None = None
    revision_no: str
    status: str
    snapshot: dict


class BlendResponse(BaseModel):
    run_id: int | None
    run_code: str
    status: str
    spec: SpecBinding | None = None
    solutions: list[SolutionOut]

class SpecSnapshotIn(BaseModel):
    targets: dict = Field(
        description="SM/IM/KH 区间，形如 {\"SM\":{\"min\":2.4,\"max\":2.8},...}"
    )
    hazard_limits_pct: dict[str, float] = Field(
        default_factory=dict, description="有害组分干基上限 %，如 {\"Cl\":0.03,\"alkali_eq\":0.6}"
    )


class SpecFamilyCreate(BaseModel):
    spec_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    note: str | None = None
    snapshot: SpecSnapshotIn


class DraftUpdate(BaseModel):
    snapshot: SpecSnapshotIn
    lock_version: int | None = None
    change_note: str | None = None


class PublishRequest(BaseModel):
    lock_version: int | None = None          # 修订版乐观锁
    family_lock_version: int | None = None   # 规范族乐观锁


class CopyRequest(BaseModel):
    family_lock_version: int | None = None
    change_note: str | None = None


class DeprecateRequest(BaseModel):
    lock_version: int | None = None
