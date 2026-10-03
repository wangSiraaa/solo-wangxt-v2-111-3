"""SQLAlchemy 模型：原料、化验版本、试算方案。

化验成分按版本保存（assay_version），方案结果通过 blend_item.assay_version_id
与 assay_composition 回指具体化验单，保证结果可追溯到原始化验版与换算过程。
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Material(Base):
    __tablename__ = "material"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))  # 钙质/硅铝质/铁质/校正料/演示用
    moisture_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 收到基含水率 %
    cost_per_t_wet: Mapped[float] = mapped_column(Float)  # 元/吨（收到基/湿基）
    availability_t_wet: Mapped[float | None] = mapped_column(Float, nullable=True)  # 可用量，湿基吨；NULL 不限
    min_share_pct: Mapped[float] = mapped_column(Float, default=0.0)  # 最低掺量（干基份额，%）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    assay_versions: Mapped[list["AssayVersion"]] = relationship(
        back_populates="material", cascade="all, delete-orphan"
    )


class AssayVersion(Base):
    """原料化验单（一个原料可有多个化验版）。

    composition 形如 {"CaO": 78.2, "SiO2": 4.1, ..., "LOI": 35.0}，
    basis = dry 表示干基化验值（占干样 %），basis = wet 表示收到基化验值（占湿样 %）。
    缺测氧化物应不出现在 dict 中（由 measured_oxides 或 NULL 区分），
    禁止用 0 代替“未测”。
    """

    __tablename__ = "assay_version"
    __table_args__ = (UniqueConstraint("material_id", "version", name="uq_assay_material_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    version: Mapped[str] = mapped_column(String(32))
    lab_report_no: Mapped[str] = mapped_column(String(64))
    assayed_at: Mapped[datetime] = mapped_column(DateTime)
    basis: Mapped[str] = mapped_column(String(8), default="dry")  # dry / wet
    composition: Mapped[dict] = mapped_column(JSON)
    measured_oxides: Mapped[list] = mapped_column(JSON)  # 实际测定项目，如 ["CaO","SiO2"]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    material: Mapped["Material"] = relationship(back_populates="assay_versions")


class SpecFamily(Base):
    """约束规范族：如“低碱规范”。规范按族流转 草稿 → 发布 → 停用，
    每族至多一个有效（published）修订、至多一个草稿；修改已发布规范
    只能复制出新版本（见 SpecRevision）。
    """

    __tablename__ = "spec_family"

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_code: Mapped[str] = mapped_column(String(32), unique=True)  # 规范编号
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / deprecated
    lock_version: Mapped[int] = mapped_column(Integer, default=0)  # 乐观并发版本号
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    revisions: Mapped[list["SpecRevision"]] = relationship(
        back_populates="family", cascade="all, delete-orphan"
    )


class SpecRevision(Base):
    """规范修订版。draft 可改；published 为不可变冻结修订（参数快照不可编辑）；
    superseded（被新有效修订取代）与 deprecated（规范停用）只读，仅用于历史回看。
    每次试算都把 spec_code/revision_no 与完整参数快照一起写进 blend_run，
    后来规范更新不会重新解释历史运行。
    """

    __tablename__ = "spec_revision"
    __table_args__ = (
        UniqueConstraint("family_id", "revision_no", name="uq_spec_revision_no"),
        # 每族至多一个 published、至多一个 draft（由后端行锁 + 部分唯一索引双保险）
        Index("uq_spec_family_published", "family_id",
              unique=True, postgresql_where=text("status = 'published'")),
        Index("uq_spec_family_draft", "family_id",
              unique=True, postgresql_where=text("status = 'draft'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("spec_family.id"))
    revision_no: Mapped[str] = mapped_column(String(32))  # R1 / R2 / ...
    status: Mapped[str] = mapped_column(String(16))  # draft / published / superseded / deprecated
    # 完整参数快照：{"targets": {SM/IM/KH:[min,max]}, "hazard_limits_pct": {...}}
    spec_snapshot: Mapped[dict] = mapped_column(JSON)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_from_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("spec_revision.id"), nullable=True
    )
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    family: Mapped["SpecFamily"] = relationship(back_populates="revisions")


class BlendRun(Base):
    """一次试算（可含多个方案：成本最优/廉价料最多/平衡方案）。

    引用规范时冻结 spec_revision_id（RESTRICT：历史运行存在则修订不可删），
    并冗余 spec_code/revision_no 与 spec_snapshot，保证历史永远按当时规则回看，
    spec_revision_id 为 NULL 的旧记录仍可直接回看（未关联规范时代的批次）。
    """

    __tablename__ = "blend_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_code: Mapped[str] = mapped_column(String(64), unique=True)
    scenario_name: Mapped[str] = mapped_column(String(128))
    batch_t_dry: Mapped[float] = mapped_column(Float)
    target: Mapped[dict] = mapped_column(JSON)
    constraint_set: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))  # feasible / infeasible / error
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    spec_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("spec_revision.id", ondelete="RESTRICT"), nullable=True
    )
    spec_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    spec_revision_no: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 试算时刻的规范完整参数快照（与修订版快照一致，历史回看以此为准）
    spec_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    items: Mapped[list["BlendItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    solutions: Mapped[list["BlendSolution"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BlendSolution(Base):
    __tablename__ = "blend_solution"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("blend_run.id"))
    mode: Mapped[str] = mapped_column(String(32))  # min_cost / max_cheap / balanced
    success: Mapped[bool] = mapped_column(Boolean)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    indicators: Mapped[dict] = mapped_column(JSON)  # SM/IM/KH + 合成成分 + 有害组分
    diagnostic: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 冲突项诊断
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    run: Mapped["BlendRun"] = relationship(back_populates="solutions")
    items: Mapped[list["BlendItem"]] = relationship(
        primaryjoin="BlendSolution.id == BlendItem.solution_id",
        viewonly=True,
    )


class BlendItem(Base):
    """某方案下某原料的配比与干湿基换算过程。"""

    __tablename__ = "blend_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("blend_run.id"))
    solution_id: Mapped[int] = mapped_column(ForeignKey("blend_solution.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    assay_version_id: Mapped[int] = mapped_column(ForeignKey("assay_version.id"))
    share_pct_dry: Mapped[float] = mapped_column(Float)  # 干基份额 %
    mass_t_dry: Mapped[float] = mapped_column(Float)
    mass_t_wet: Mapped[float] = mapped_column(Float)
    water_t: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    # 换算留痕：湿基->干基/干基->湿基每个氧化物的完整过程
    conversion_trace: Mapped[dict] = mapped_column(JSON)
    assay_composition_snapshot: Mapped[dict] = mapped_column(JSON)  # 原始化验单快照

    run: Mapped["BlendRun"] = relationship(back_populates="items")
