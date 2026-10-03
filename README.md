# 离线原料配比试算台（虚构工艺边界 · 工艺研发用）

> ⚠️ **边界声明**：本应用使用的原料名称、化验单数值、成本、可用量与率值窗口均为**虚构演示数据**，
> 仅用于工艺研发离线比较“原料成本 ↔ 生料化学指标”的取舍。
> 应用不连接任何生产控制系统，**不向真实生产设备下发指令**。

## 技术栈

| 层 | 技术 | 职责 |
|---|---|---|
| 前端 | Angular 18（standalone 组件，纯 CSS 堆叠条） | 氧化物来源/配比比例展示、规范版本管理、试算交互、方案对比、化验追溯 |
| 后端 | FastAPI + Pydantic | REST API、干湿基换算、规范生命周期与乐观并发、错误码、静态托管 |
| 优化 | SciPy `linprog`（HiGHS） | 线性规划：成本最优 / 廉价料最大 / 率值居中 |
| 存储 | PostgreSQL 15 | 原料、**多版化验单**、**版本化约束规范**、试算批次、方案、逐原料换算留痕 |

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

### 版本化约束规范（constraint_spec / spec_revision）

研发规范收紧时，不能让一串临时参数被误当成可复现的规范，因此率值窗口与
有害组分限值按**规范 → 不可变修订版**管理：

- **生命周期**：`draft → published → retired`。草稿可编辑（携带 `lock_version`
  乐观并发令牌）；发布把草稿冻结为**不可编辑的修订版**（`spec_revision`）；
  修改已发布规范只能“复制为新草稿”再发布下一修订；停用后禁止新试算引用，
  历史批次仍可回看。
- **乐观并发**：发布/复制/停用/草稿保存均为 `WHERE id AND lock_version AND status`
  的 CAS 更新——两个窗口基于同一草稿并发发布时**只产生一个有效修订**，
  另一方收到 409 `SPEC_CONFLICT`（响应带最新状态，可刷新重试）；
  直接编辑已发布规范返回 409 `SPEC_IMMUTABLE`；试算落库前对规范行加锁复核，
  与“停用”竞争时整批拒绝（409 `SPEC_RETIRED`）。
- **试算绑定**：`POST /api/blend` 传 `spec_revision_id` 时，服务端**以修订版冻结
  参数为准**（请求里的临时 targets/限值不生效），并把规范编号、修订号、完整参数
  快照与求解结果一起写入 `blend_run.spec_snapshot`——规范日后改版/停用都不会
  重新解释历史批次；规范体系建立前的历史批次（`spec_snapshot=NULL`）按
  “临时参数”原样回看。
- 规范要求的有害组分缺测时，仍返回 422 `MISSING_ASSAY`，不会把方案标为可行。

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
    main.py        FastAPI 路由 + 错误处理 + SPA 托管
    chemistry.py   干湿基换算 / 质量守恒 / SM/IM/KH / 缺测与零分母异常
    optimizer.py   SciPy HiGHS LP、多模式、冲突诊断
    models.py      SQLAlchemy：material / assay_version / constraint_spec /
                   spec_revision / blend_run / solution / item
    crud.py        持久化、规范生命周期（CAS 乐观并发）与历史回看
    schemas.py     Pydantic 模型
    seed.py        虚构演示数据（含湿基化验单、缺测/零分母演示料、低碱规范草案）
  tests/           27 个 pytest（换算/守恒/报错/求解/规范并发与冻结/API/追溯）
  scripts/         pg_start / pg_stop / seed / serve
frontend/
  src/app/
    components/    materials / specs / blend / solution-card / history / stack-bar
    services/api.service.ts
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
2. **约束规范**：规范列表与状态流转（草稿→发布→停用）、草稿编辑（率值窗口 +
   有害组分限值行）、发布不可变修订版、复制已发布规范为新草稿、修订版两两比较
   （新增/删除/收紧/放宽）、查看某修订版影响的试算批次；409 冲突自动刷新提示；
3. **配比试算与方案对比**：约束来源可选“临时参数”或任一已发布规范修订版
   （选中后窗口/限值锁定为冻结值），候选/化验版/模式选择，四个快速场景：
   - 基准三方案对比（含水率差异：粉煤灰 18% 湿基化验单 → 采购湿料量与留痕）；
   - 廉价原料（页岩）致 IM/KH 超限 → 失败 + 冲突项；
   - 碱当量上限收紧（0.40%）→ 有害组分冲突与突破量；
   - 5000 t 大批量 → 湿基可用量与 KH 同时冲突；
   另附**手工配比**：一键装入“100% 零铁石英（IM 分母为零）”和
   “缺测矿样（MISSING_ASSAY）”；
4. **历史追溯**：批次列表标注绑定的规范修订（或“临时参数”）；每个方案可追到
   批次号、原始化验版本/单号、原始 wet/dry 报送值、逐组分湿→干公式、干/湿料
   质量、水量与成本算式；绑定规范的批次展示冻结参数快照，并可一键跳回
   “当时冻结规则”的规范修订页。

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/materials?active_only=` | 原料与全部化验版本 |
| POST | `/api/blend` | 试算（多模式、约束、可入库；`spec_revision_id` 按规范冻结参数求解） |
| POST | `/api/evaluate` | 手工份额合成 + 率值（错误演示） |
| GET | `/api/runs` `/api/runs/{id}` | 历史批次（含规范绑定）与完整追溯 |
| GET/POST | `/api/specs` | 规范列表 / 新建草稿 |
| GET | `/api/specs/{id}` | 规范详情（草稿参数 + 全部修订版 + lock_version） |
| PUT | `/api/specs/{id}/draft` | 编辑草稿（已发布 → 409 `SPEC_IMMUTABLE`） |
| POST | `/api/specs/{id}/publish` | 发布草稿为不可变修订版（并发仅一个成功，其余 409 `SPEC_CONFLICT`） |
| POST | `/api/specs/{id}/copy` | 复制最新修订版为新草稿（修改已发布规范的唯一入口） |
| POST | `/api/specs/{id}/retire` | 停用（新试算引用 → 409 `SPEC_RETIRED`，历史可回看） |
| GET | `/api/specs/{id}/diff?from_no=&to_no=` | 两个修订版的参数差异（新增/删除/收紧/放宽） |
| GET | `/api/specs/{id}/revisions/{no}/runs` | 该修订版影响的全部试算批次 |
| GET | `/api/health` | 健康检查（含 fictional-boundary 标记） |

错误响应体：`{ "error_code": "MISSING_ASSAY|ZERO_DENOMINATOR|SPEC_CONFLICT|SPEC_IMMUTABLE|SPEC_RETIRED|...", "message": ..., "details": ... }`
（业务 422；规范状态/并发冲突 409）。

## 测试

```bash
cd backend && python3 -m pytest tests/ -q
# 27 passed
```
