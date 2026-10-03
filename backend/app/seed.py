"""虚构演示数据。

声明：所有原料名称、化验单数值、成本与可用量均为工艺研发试算虚构，
不代表任何真实矿山/供应商，应用不对真实生产设备下发指令。
"""
from datetime import datetime

from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .models import AssayVersion, Material, SpecFamily, SpecRevision

ALL_MAJOR = ["CaO", "SiO2", "Al2O3", "Fe2O3",
             "MgO", "SO3", "K2O", "Na2O", "Cl", "LOI"]

MATERIALS = [
    dict(
        code="LS01", name="石灰石(虚构A矿)", category="钙质",
        moisture_pct=2.0, cost_per_t_wet=65.0, availability_t_wet=3000.0,
        min_share_pct=60.0, is_active=True,
        note="钙质主原料；最低掺量 60%（虚构边界）。",
        versions=[
            dict(version="V2026-09", lab_report_no="LAB-2609-118",
                 assayed_at=datetime(2026, 9, 12), basis="dry",
                 composition={"CaO": 49.5, "SiO2": 5.2, "Al2O3": 1.4, "Fe2O3": 0.7,
                              "MgO": 1.2, "SO3": 0.25, "K2O": 0.35, "Na2O": 0.08,
                              "Cl": 0.005, "LOI": 41.315},
                 measured_oxides=ALL_MAJOR),
            dict(version="V2026-08", lab_report_no="LAB-2608-077",
                 assayed_at=datetime(2026, 8, 15), basis="dry",
                 composition={"CaO": 48.9, "SiO2": 5.9, "Al2O3": 1.6, "Fe2O3": 0.8,
                              "MgO": 1.3, "SO3": 0.28, "K2O": 0.4, "Na2O": 0.09,
                              "Cl": 0.006, "LOI": 40.724},
                 measured_oxides=ALL_MAJOR),
        ],
    ),
    dict(
        code="SS01", name="砂岩(虚构B料场)", category="硅质校正",
        moisture_pct=6.0, cost_per_t_wet=42.0, availability_t_wet=800.0,
        min_share_pct=0.0, is_active=True,
        note="高硅校正料，含水率较高。",
        versions=[dict(version="V2026-09", lab_report_no="LAB-2609-121",
                       assayed_at=datetime(2026, 9, 10), basis="dry",
                       composition={"CaO": 1.5, "SiO2": 78.0, "Al2O3": 9.5,
                                    "Fe2O3": 3.8, "MgO": 0.8, "SO3": 0.1,
                                    "K2O": 1.1, "Na2O": 0.25, "Cl": 0.01,
                                    "LOI": 4.94},
                       measured_oxides=ALL_MAJOR)]),
    dict(
        code="SH01", name="页岩(廉价虚构C矿)", category="硅铝质",
        moisture_pct=14.0, cost_per_t_wet=22.0, availability_t_wet=1200.0,
        min_share_pct=0.0, is_active=True,
        note="廉价硅铝质原料，碱含量偏高；含水率 14%。",
        versions=[dict(version="V2026-09", lab_report_no="LAB-2609-119",
                       assayed_at=datetime(2026, 9, 11), basis="dry",
                       composition={"CaO": 8.0, "SiO2": 55.0, "Al2O3": 17.0,
                                    "Fe2O3": 7.5, "MgO": 2.2, "SO3": 0.3,
                                    "K2O": 2.6, "Na2O": 0.7, "Cl": 0.02,
                                    "LOI": 6.68},
                       measured_oxides=ALL_MAJOR)]),
    dict(
        code="AS01", name="粉煤灰(湿排·湿基化验单)", category="铝质校正",
        moisture_pct=18.0, cost_per_t_wet=18.0, availability_t_wet=600.0,
        min_share_pct=0.0, is_active=True,
        note="廉价铝质校正；化验单按收到基(湿基)报送，需先做湿→干换算。",
        versions=[dict(version="V2026-09W", lab_report_no="LAB-2609-130W",
                       assayed_at=datetime(2026, 9, 8), basis="wet",
                       # 湿基值 = 干基值 × (1-0.18)
                       composition={"CaO": 3.69, "SiO2": 39.36, "Al2O3": 24.6,
                                    "Fe2O3": 5.33, "MgO": 1.23, "SO3": 0.656,
                                    "K2O": 1.476, "Na2O": 0.738, "Cl": 0.0123,
                                    "LOI": 4.9077},
                       measured_oxides=ALL_MAJOR)]),
    dict(
        code="IR01", name="铁粉(虚构副产)", category="铁质校正",
        moisture_pct=8.0, cost_per_t_wet=320.0, availability_t_wet=200.0,
        min_share_pct=0.0, is_active=True,
        note="铁质校正料，单价高。",
        versions=[dict(version="V2026-09", lab_report_no="LAB-2609-122",
                       assayed_at=datetime(2026, 9, 9), basis="dry",
                       composition={"CaO": 4.0, "SiO2": 18.0, "Al2O3": 5.0,
                                    "Fe2O3": 62.0, "MgO": 2.5, "SO3": 1.2,
                                    "K2O": 0.4, "Na2O": 0.1, "Cl": 0.02,
                                    "LOI": 6.78},
                       measured_oxides=ALL_MAJOR)]),
    # ---- 以下为报错/极端情形演示物料，默认不进入常规候选 ----
    dict(
        code="QZ01", name="高纯石英砂(演示)", category="演示用",
        moisture_pct=0.5, cost_per_t_wet=95.0, availability_t_wet=500.0,
        min_share_pct=0.0, is_active=False,
        note="极端高硅，用于演示率值极端化；无真实配方含义。",
        versions=[dict(version="V2026-09", lab_report_no="LAB-DEMO-01",
                       assayed_at=datetime(2026, 9, 1), basis="dry",
                       composition={"CaO": 0.05, "SiO2": 98.6, "Al2O3": 0.35,
                                    "Fe2O3": 0.18, "MgO": 0.05, "SO3": 0.0,
                                    "K2O": 0.05, "Na2O": 0.02, "Cl": 0.0,
                                    "LOI": 0.7},
                       measured_oxides=ALL_MAJOR)]),
    dict(
        code="SP01", name="缺测矿样(演示·Fe2O3未检)", category="演示用",
        moisture_pct=3.0, cost_per_t_wet=30.0, availability_t_wet=300.0,
        min_share_pct=0.0, is_active=False,
        note="化验单缺测 Fe2O3——系统必须报 MISSING_ASSAY，禁止按零含量配入。",
        versions=[dict(version="V2026-09", lab_report_no="LAB-DEMO-MISS",
                       assayed_at=datetime(2026, 9, 1), basis="dry",
                       composition={"CaO": 45.0, "SiO2": 6.0, "Al2O3": 2.0,
                                    "MgO": 1.0, "LOI": 46.0},
                       measured_oxides=["CaO", "SiO2", "Al2O3", "MgO", "LOI"])]),
    dict(
        code="QZ00", name="零铁石英(演示·Fe2O3实测0)", category="演示用",
        moisture_pct=0.2, cost_per_t_wet=110.0, availability_t_wet=200.0,
        min_share_pct=0.0, is_active=False,
        note="Fe2O3 已实测且确为 0.0%——与“缺测”不同；100% 配入时 IM 分母为零必须报错。",
        versions=[dict(version="V2026-09", lab_report_no="LAB-DEMO-ZERO",
                       assayed_at=datetime(2026, 9, 1), basis="dry",
                       composition={"CaO": 0.05, "SiO2": 99.6, "Al2O3": 0.35,
                                    "Fe2O3": 0.0, "LOI": 0.0},
                       measured_oxides=["CaO", "SiO2", "Al2O3", "Fe2O3", "LOI"])]),
]


BASE_TARGETS = {"SM": {"min": 2.4, "max": 2.8},
                "IM": {"min": 1.4, "max": 1.8},
                "KH": {"min": 0.88, "max": 0.94}}

# 演示用版本化约束规范（虚构工艺边界）
SPECS = [
    dict(
        spec_code="STD-GENERAL", name="通用基准规范（虚构）",
        note="研发日常对比的默认窗口：SM/IM/KH 双边窗口 + Cl/碱当量限值。",
        revision_no="R1", status="published",
        snapshot={"targets": BASE_TARGETS,
                  "hazard_limits_pct": {"Cl": 0.05, "alkali_eq": 1.5}},
    ),
    dict(
        spec_code="LOW-ALKALI", name="低碱规范（草稿·待发布）",
        note="收紧碱当量到 0.60% 的低碱水泥研发边界，当前为草稿，发布后才生效。",
        revision_no="R1", status="draft",
        snapshot={"targets": {"SM": {"min": 2.4, "max": 2.7},
                              "IM": {"min": 1.4, "max": 1.7},
                              "KH": {"min": 0.89, "max": 0.93}},
                  "hazard_limits_pct": {"Cl": 0.03, "alkali_eq": 0.6}},
    ),
]


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.scalars(select(Material)).first():
            for spec in MATERIALS:
                versions = spec.pop("versions")
                mat = Material(**spec)
                db.add(mat)
                db.flush()
                for v in versions:
                    db.add(AssayVersion(material_id=mat.id, **v))
            db.commit()
            print(f"seed: 已写入 {len(MATERIALS)} 个虚构原料及其化验版本。")
        else:
            print("seed: 原料数据已存在，跳过。")

        if not db.scalars(select(SpecFamily)).first():
            for sp in SPECS:
                rev_status = sp.pop("status")
                rev_no = sp.pop("revision_no")
                snap = sp.pop("snapshot")
                fam = SpecFamily(**sp, status="active", lock_version=0)
                db.add(fam)
                db.flush()
                db.add(SpecRevision(
                    family_id=fam.id, revision_no=rev_no,
                    status=rev_status, spec_snapshot=snap, lock_version=0,
                    published_at=datetime.utcnow() if rev_status == "published" else None,
                ))
            db.commit()
            print(f"seed: 已写入 {len(SPECS)} 个版本化约束规范（1 发布 + 1 草稿）。")
        else:
            print("seed: 规范数据已存在，跳过。")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
