# 离线原料配比试算台（虚构工艺边界 · 工艺研发用）

> ⚠️ **边界声明**：本应用使用的原料名称、化验单数值、成本、可用量与率值窗口均为**虚构演示数据**，
> 仅用于工艺研发离线比较“原料成本 ↔ 生料化学指标”的取舍。
> 应用不连接任何生产控制系统，**不向真实生产设备下发指令**。

## 技术栈

| 层 | 技术 | 职责 |
|---|---|---|
| 前端 | Angular 18（standalone 组件，纯 CSS 堆叠条） | 氧化物来源/配比比例展示、规范管理与版本比较、试算交互、方案对比、化验追溯 |
| 后端 | FastAPI + Pydantic | REST API、干湿基换算、错误码、版本化约束规范、静态托管 |
| 优化 | SciPy `linprog`（HiGHS） | 线性规划：成本最优 / 廉价料最大 / 率值居中 |
| 存储 | PostgreSQL 15 | 原料、**多版化验单**、**版本化约束规范（草稿/发布/停用）**、试算批次（冻结规范快照）、方案、逐原料换算留痕 |

## 计算口径

1. **先质量守恒合成，再算率值**（不把率值当输入去反推成分）。
   干基份额 x_i（Σx_i=1）：`合成干基% = Σ x_i × 原料干基%`。
2. 率值（分母为零即报错，见下）：
   - 硅率 `SM = SiO2 / (Al2O3 + Fe2O3)`
   - 铝率 `IM = Al2O3 / Fe2O3`
   - 石灰饱和系数 `KH = (CaO − 1.65·Al2O3 − 0.35·Fe2O3) / (2.8·SiO2)`
3. **干湿基**：化验按 `dry`（干基）或 `wet`（收到基）登记；
   含水率 w 时 `干基% = 湿基% /(1−w)`，自由水不并入 LOI；
   质量换算 `湿料t = 干料t /(1−w)`，成本按湿料吨价结算。
4. 碱当量 `Na2O + 0.658·K2O`。

### 硬性错误规则（不以零含量兜底）

- **缺测**：候选原料的必测组分（CaO/SiO2/Al2O3/Fe2O3，及被设上限的有害组分）
  不在 `measured_oxides` 中 → HTTP 422 `MISSING_ASSAY`，返回原料/化验版/单号/缺测项；
- **分母为零**：Fe2O₃、SiO₂ 等为 0 导致 IM/SM/KH 无定义 → HTTP 422 `ZERO_DENOMINATOR`；
- “已实测为 0”（演示料 QZ00）与“未测/缺测”（演示料 SP01）严格区分。

### 约束

- 原料**最低掺量**（干基 %，档案字段）、**湿基可用量**（换算成干基份额上限）；
- **有害组分干基上限**（Cl、碱当量等，可扩展）；
- 率值区间经线性化进入 LP（如 SM≤hi ⇔ `Σ(SiO2−hi(Al2O3+Fe2O3))x ≤ 0`）。

### 版本化约束规范（草稿 → 发布 → 停用）

率值窗口与有害组分限值常被研发规范收紧，临时参数不得冒充可复现规范：

- 规范（`spec_family`）下挂多个修订（`spec_revision`），修订参数至少含
  **SM/IM/KH 区间 + 有害组分限值**；状态机 `draft → published →（superseded）/deprecated`。
- **草稿**可编辑/删除；**发布**做完整校验（双边窗口、限值合法）并冻结为**不可编辑修订**。
  修改已发布规范只能 **copy 出新草稿**，发布后旧有效修订自动转 `superseded`，旧方案结果不变。
- 发布/复制/停用在同一事务内持**族级行锁**串行化，配合族/修订两级 `lock_version`
  乐观锁与 `draft/published` 部分唯一索引：同草稿并发发布只有一个成功，
  另一方得到 HTTP 409 `VERSION_CONFLICT`（可复制草稿继续处理）。
- 试算引用规范时只传 `spec_revision_id`，窗口/限值**一律以服务端冻结快照为准**，
  请求体夹带的临时参数被忽略；`blend_run` 冗余规范编号、修订号与**完整参数快照**，
  历史批次永远按当时规则回看，不会因后来更新规范而被重新解释。
  未关联规范的历史批次（`spec_revision_id IS NULL`）仍可直接回看。
- 停用规范后禁止新试算引用（409 `SPEC_NOT_USABLE`），历史与“影响运行”列表仍可查；
  规范要求的有害组分缺测时仍返回 422 `MISSING_ASSAY`（一次性列出全部缺测项），不会标为可行。

### 求解模式与无解诊断

- `min_cost`：最小元/吨干生料；
- `max_cheap`：两阶段 LP——先最大化指定廉价料份额，再锁定份额最小化成本打破平局；
- `balanced`：率值对区间中点的绝对偏差最小（线性化），轻微成本偏好做次序裁决；
- **求解失败**：对全部不等式做“最小违约松弛”模型，列出仍被突破的冲突约束、
  限值、最小违约解达到值与缺口；最低掺量之和 >100% 另有算术预检 `MIN_SHARE_OVERFLOW`。

## 目录

```
backend/
  app/
    main.py        FastAPI 路由 + 错误处理 + 规范接口 + SPA 托管
    chemistry.py   干湿基换算 / 质量守恒 / SM/IM/KH / 缺测与零分母异常
    optimizer.py   SciPy HiGHS LP、多模式、冲突诊断
    models.py      SQLAlchemy：material / assay_version / spec_family / spec_revision
                   / blend_run(冻结规范快照) / solution / item
    specstore.py   规范流转、校验、乐观锁/行锁、影响运行与版本差异
    crud.py        持久化与历史回看
    schemas.py     Pydantic 模型
    seed.py        虚构演示数据（含湿基化验单、缺测/零分母演示料、基准+低碱规范）
  tests/           31 个 pytest（换算/守恒/报错/求解/API/追溯/版本化规范）
  scripts/         pg_start / pg_stop / seed / serve
frontend/
  src/app/
    components/    materials / specs / blend / solution-card / history / stack-bar
    services/      api.service / tab.service
    models/models.ts
```

## 启动（本机用户态，无需 root/docker）

PostgreSQL 15 以 deb 解包方式安装在 `~/.local/pgsql`，数据目录 `~/.local/pgdata`，
端口 **55432**，库名 **rawmix**，连接串：
`postgresql+psycopg2://mixapp@127.0.0.1:55432/rawmix`（可用 `RAWMIX_DATABASE_URL` 覆盖）。

```bash
# 1) 启动数据库（首次自动 initdb + 建库）
backend/scripts/pg_start.sh
# 2) 写入虚构演示数据
backend/scripts/seed.sh
# 3) 启动 API + 已构建前端（http://127.0.0.1:8000）
backend/scripts/serve.sh
```

前端开发模式（热更新，代理 /api → :8000）：

```bash
cd frontend && ./dev.sh        # http://127.0.0.1:4200
# 生产构建（输出到 backend/static，由 FastAPI 托管）：
cd frontend && ./node_modules/.bin/ng build frontend
```

Python 依赖：`pip install -r backend/requirements.txt`（本机装于用户 site-packages）。

## 前端四个标签页

1. **原料与化验**：全部原料/多版化验单、干湿基标记、缺测红格、干基换算预览；
2. **约束规范**：规范族与修订列表、草稿编辑、发布冻结、复制新版本、停用、
   修订间**参数差异比较**与**影响运行**列表（从历史批次可一键回到当时冻结规则）；
3. **配比试算与方案对比**：选择已发布规范（窗口只读、参数服务端冻结）或显式标注的
   临时参数试算；候选/化验版/模式选择，四个快速场景：
   - 基准三方案对比（含水率差异：粉煤灰 18% 湿基化验单 → 采购湿料量与留痕）；
   - 廉价原料（页岩）致 IM/KH 超限 → 失败 + 冲突项；
   - 碱当量上限收紧（0.40%，临时参数演示）→ 有害组分冲突与突破量；
   - 5000 t 大批量 → 湿基可用量与 KH 同时冲突；
4. **手工配比**：一键装入“100% 零铁石英（IM 分母为零）”和“缺测矿样（MISSING_ASSAY）”；
5. **历史追溯**：每个方案可追到批次号、**绑定的规范编号/修订号/冻结参数快照**、
   原始化验版本/单号、原始 wet/dry 报送值、逐组分湿→干公式、干/湿料质量、水量与成本算式；
   未关联规范的旧批次标注“未关联规范”并可直接回看。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/materials?active_only=` | 原料与全部化验版本 |
| GET/POST | `/api/specs` | 规范族列表 / 创建（自带 R1 草稿） |
| GET | `/api/specs/{id}` | 规范族详情（含全部修订与锁版本） |
| POST | `/api/specs/{id}/deprecate` | 停用规范（乐观锁 `lock_version`） |
| PATCH/DELETE | `/api/spec-revisions/{id}` | 改/删**草稿**（已发布拒绝 409） |
| POST | `/api/spec-revisions/{id}/publish` | 草稿发布为不可编辑修订 |
| POST | `/api/spec-revisions/{id}/copy` | 复制修订为新草稿（201） |
| GET | `/api/spec-revisions/{id}/runs` | 该修订影响的运行记录 |
| GET | `/api/spec-revisions/{id}/diff/{other}` | 两个修订的参数差异 |
| POST | `/api/blend` | 试算（`spec_revision_id` 引用冻结规范，或给临时 `targets`） |
| POST | `/api/evaluate` | 手工份额合成 + 率值（错误演示） |
| GET | `/api/runs` `/api/runs/{id}` | 历史批次与完整追溯（含规范绑定/快照） |
| GET | `/api/health` | 健康检查（含 fictional-boundary 标记） |

规范类错误响应：409 `VERSION_CONFLICT|SPEC_PUBLISHED_IMMUTABLE|SPEC_NOT_USABLE|SPEC_NOT_CURRENT|SPEC_DRAFT_EXISTS|SPEC_CODE_EXISTS`、
400 `SPEC_PARAMS_INVALID`；试算错误体：`{ "error_code": "MISSING_ASSAY|ZERO_DENOMINATOR|...", ... }`。

## 测试

```bash
cd backend && python3 -m pytest tests/ -q
# 31 passed（21 原有 + 10 版本化规范：发布绑定/不可编辑/复制/并发发布/停用/缺测/旧记录回看）
```
