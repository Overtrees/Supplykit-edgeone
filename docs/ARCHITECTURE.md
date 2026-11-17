# SupplyKit 架构与技术细节

技术评估者与开发者入口——架构、路由、项目结构、核心算法、测试与部署。

## 架构

```
┌─ 前端 (EdgeOne Makers 同源部署) ────────────┐
│  React 18 · TypeScript · ECharts 5 · Zustand  │
│  Axios(30s缓存+在途去重+统一解包) · 版本指纹30s │
│  看板30s静默刷新 · 事件驱动全链路刷新           │
└──────────────────────┬────────────────────────┘
                       │ HTTPS (supplykit.top, 免签公开)
┌─ 后端 (EdgeOne Makers 函数) ────────────────┐
│  FastAPI · 24 路由 · 34 文件 · 回归全绿    │
│  ├─ 入口: index.py(行首 app=, 鉴权, 启动自愈) │
│  ├─ 业务: dashboard(断货三级/健康) /         │
│  │        replenishment / purchase / insights │
│  │        cleansing / rules(计算参数融合) /   │
│  │        cron(12端点) / tasks(seed分步) / ...│
│  ├─ 核心: core/rules.py(表达式引擎+计算变量)  │
│  │        biz/sales.py(三窗口日销融合)       │
│  └─ 基础: db.py(TiDB 原生)                   │
└──────────────────────┬────────────────────────┘
                       │
┌─ 数据库 ────────────────────────────────────┐
│  TiDB Serverless (pymysql DictCursor)        │
└──────────────────────────────────────────────┘
```

### 后端路由一览（API 分组 · 24 个 APIRouter / 34 个路由文件——其中支撑模块属代码组织非路由）

| 路由 | 前缀 | 功能 |
|------|------|------|
| `dashboard` | `/api/dashboard` | 看板摘要/aux/stock-risk（30s TTL 独立） |
| `replenishment` | `/api/insights` | BBCC/传统补货建议 |
| `purchase` | `/api/insights` | 采购建议/滞销处置建议/批量处置 |
| `insights` | `/api/insights` | 慢动识别/进销存 with-sales/ping |
| `cleansing` | `/api/cleansing` | 清洗导入/模板/任务 |
| `orders/products/inventory` | `/api/*` | 分页 CRUD/软删/批量/缺货清单 |
| `suppliers` | `/api/*` | 供应商 CRUD |
| `rules` | `/api/rules` | 规则 CRUD/测试/批量/**evaluate(立即运行)** |
| `alerts` | `/api/alerts` | 告警列表/计数（逐仓） |
| `tasks` | `/api/*` | seed 填充分步/reset/导出 |
| `purchase-orders` | `/api/purchase-orders` | 采购单标记+到仓日期 |
| `replenishment-config` | `/api/replenishment-config` | 补货参数(mode 前缀)/slow-cats/seasons/history |
| `misc` | `/api/*` | quality-logs(分页)/monitor/db/diag |
| `batches` | `/api/batches` | 批次明细 |
| `channels` | `/api/channels` | 渠道 CRUD(启停/排序) |
| `history` | `/api/history` | 归档查询面(coverage/summary/top/yoy), **180 天切线**(`ARCHIVE_KEEP_DAYS`, 写侧 cron / 读侧 history 共用常量)拼 orders + daily_stats; summary/yoy 同收 `[start,end]` 与 400 天截断 |
| `auth` | `/api/auth` | setup/login/check |
| `cron` | `/api/cron` | 12 端点（snapshot/freshness/archive/cleanup-logs/daily-rules/recycle/push-alerts/ping/continue-tasks/agg-sweep/reconcile/ledger-close） |

### 性能结构（一句话版）

> 完整机制、边界与按页面的基线见 [`docs/PERF.md`](PERF.md)；这里只画数据流，避免两处重复。

```
读：请求 ──► 物化快照(list_snapshot / dmat / own_health_snapshot / unit_state)
              ├─ fp 命中 ─────────────► 直接返回（p50 十几~几十 ms）
              ├─ 过期但有旧值(SWR) ──► 立即返上一版 + 顶层 __swr + 后台重建
              └─ 首建/无旧值 ────────► 请求内同步算一次（绝不给空结构）

写：写操作 ──► invalidate(域) ──► 后台线程（daemon，寄生在请求实例）
                                   └─► db.heavy：**先抢跨实例 heavy_slot(2)** ─► 再抢实例内 token(3)
                                         └─► 重建(calc → write) ─► publish(gen+1) ─► 读侧切换
```

| 表 | 作用 |
|---|---|
| `list_snapshot` / `list_head` | 建议类物化（repl / purchase / disposal），`head` 存 `gen`/`fp`/`timing` |
| `unit_state` / `unit_state_head` | 判定模型（健康/断货）物化，`head.risk_stat` 承载断货计数 |
| `own_health_snapshot` | 自有仓健康明细（seq 切页） |
| `dmat:*` | 看板卡片物化（`dash_summary` / `dash_aux` / `stock_risk`） |
| `analysis_cache` | 结论型缓存（history 等），支持 SWR |
| `heavy_slot` | **跨实例**重建名额（2 槽，180s 租约）；`holder=''` 即空闲 |
| `maint_lock` | 维护任务租约锁（600s），防连点重复提交 |

### 项目结构

```
frontend/src/
├── pages/ (16个): Dashboard / Insights / Cleansing / Rules / Orders / Inventory /
│                 History / Channels / Recycle / Products / Suppliers / Quality /
│                 Settings / Task / DevMode / Login
├── components/ (41个): Chart / Sidebar / ListGroup / Toast / OpPill / ErrorBoundary /
│                 Icons / dash/RiskCard 等 + hammer/(10 个锤子面板 + configs)
├── store/useAppStore.ts          Zustand + 渠道/模式/批量状态 + 全局操作态 ops
├── api/client.ts                 Axios + 缓存 + 统一响应解包
├── api/mutate.ts                 写操作统一出口(提示/缓存/事件/操作态)
├── api/notify.ts                 全局提示注册表(Provider 外的唯一可用通道)
└── App.tsx / main.tsx / theme.ts / styles.css

cloud-functions/api/
├── index.py                      FastAPI 入口（行首 app=, 鉴权）
├── schema_create.py              表/索引/补列声明单一真源 + SCHEMA_VERSION 版本门禁
├── db.py                         TiDB 原生数据层（pymysql DictCursor）
├── biz/sales.py                  三窗口日销滚动预测 + 逐仓/全国C仓双口径
├── core/rules.py                 表达式引擎 + 计算变量注入 + 批量评估 + 恢复关闭
├── core/rule_cond.py             规则条件解析(2026-10-11 拆分)
├── core/alert_lifecycle.py       告警生命周期单一入口(维度键/关闭原因/重开复用, 2026-11-03 D8)
├── routes/ (34个 = 24 router + 10 支撑)  路由模块 + 支撑模块(common/analysis_cache/cleanse_write/dashboard_health/dashboard_maintain/dashboard_own_health/dashboard_stock_risk/purchase_calc/seed_fill/seed_import); 已按域拆分 dashboard_maintain/stock_risk/health/cleanse_write/purchase_calc/task_export/task_seed
├── test_audit.py                 静态审计(断言式: 缓存 key 含维度/无硬编码渠道/口径一致)
├── local_test.py                 本地全量回归（mock db + TestClient）
└── sql_smoke.py                  真实 SQL 冒烟（EXPLAIN 396 条 + schema 对账, 2026-11-03 S3-4）

scripts/gen_schema.py             SQLite→TiDB DDL 转换器
```

---

## 日销口径（唯一一套，2026-11-16 收敛）

```
orders + 库存快照 ─→ biz/sales._rebuild_sku_sales_stats ─→ sku_sales_stats
                        一行 = 渠道 × 仓维度 × SKU × 69码
                        ├ ds7 / ds14 / ds28              原始日均
                        ├ ds7_cut / ds14_cut / ds28_cut  削峰 + 稀疏收缩后
                        ├ sigma / nnz / last_sale
                        └ __C__(全国C仓) / __BC__(BC合一) / 逐仓
                                     ↓ 各消费方取一行、现场融合（O(1)）
```

**消费方一律读 `cut` 三列**，融合只有一份实现：

| 消费方 | 窗口 | 公式 |
|---|---|---|
| 补货 bbcc / traditional | 7/14/28 | `rolling_predict(ds7_cut, ds14_cut, ds28_cut)` × `season` × `act` |
| 进销存台账（own 行 / platform 行） | 7/14/28 | 同上（不含业务系数） |
| 看板卡片（断货 / 库存 / 健康） | 7/14/28 | 同上 |
| 告警 / 规则引擎 | 7/14/28 | 同上 |
| 采购 | **14/28** | `rolling_predict_2w(ds14_cut, ds28_cut)` × `active_factor`¹ |

采购保留两窗口是**设计**（采购周期长，7 天噪音不进），是四家唯一剩下的结构性差异；
两窗口权重表沿用原值（`>1.15→.55/.45`、`<.85→.35/.65`、平稳 `.20/.80`），采购结果逐位不变。

> ¹ 表中 `active_factor` 是代码变量（`routes/purchase_calc.py:72` 取 `core.metrics.season_factor`；当前 season 全 disabled，恒 1.0）。同名的 `replenishment_config.active_factor` 配置键才是死配置（seed 写 `'1.0'`，无代码读取该键，见 `CONTEXT.md` P8-4；后文“`active_factor` 仍是死配置”指的也是配置键）。

四条不变式：

1. **一份算法**。`rolling_predict` / `rolling_predict_2w` 都在 `biz/sales.py`，
   同一套"判趋势 → 查权重表"机制，只有窗口数与表不同（红线 9）。
2. **降级只降码，不跨维**：`该维该69码 → 该维SKU合计 → 0`，没有"某仓没数读全国"这一级。
   层级只判一次，三列必然同源（`Series._level`），不会出现"7 天取码行、14 天取合计"的混搭。
   例外只有自有仓（own）：它自己不卖货，维度直接取 `__C__`。
3. **cut 是什么**：先 `smooth_promo_spikes`（单日 >5×基线截到 2×），再 `calc_sales_multi`
   且 `sparse="shrink"`（窗口内非零日 <3 天时 ×nnz/3）。3σ 剔除阈值仍是
   `max(3σ, 1.5×非零均值)` —— **保留**，不改成纯 3σ。
4. **维度决定处唯一**：`metrics.dim_of()` —— BC 盘 / 自有仓 / B仓 一律 `__C__`
   （B 仓是调拨中转，不产生销售）。

### 趋势长基准（近 28 vs 近 56，2026-11-16）

日销列末尾那一档箭头比的是「近 28 天 vs 长基准」。**补货页的渲染没动**（原本就有这一档，
只是分母装错了东西）；**采购页原先缺这一档**（只有 14 vs 28），本轮补上。

| | 字段 | 值 | 结果 |
|---|---|---|---|
| 原 | `daily_sales_60` | `rolling_predict(7/14/28)` 融合值 | 名字说 60 天，内容不是；且恒低于 ds28 → **75% 朝上 / 2.7% 朝下** |
| 现 | `daily_sales_56`（键名已改，`_60` 退役） | `ds56_cut` 近 56 天 cut 日均 | 与 ds28 同维度、同粒度、同 cut → **59% 上 / 15% 平 / 24.5% 下** |

> **56 天窗口的定位：只做「近 28 vs 近 56」这一个箭头，不进任何取值窗口。**
> 日销取值仍是近 7 / 14 / 28（补货、进销存、看板、告警）与近 14 / 28（采购），
> `rolling_predict` / `rolling_predict_2w` 的入参里没有 56，采购量、补货量、安全线、
> 告警判定也都不读它。它只出现在两个地方：`sku_sales_stats.ds56_cut` 列，
> 和接口返回的 `daily_sales_56` 字段（前端箭头用）。回归里有一条静态扫描盯着这条边界
> （「56 天窗口只喂趋势箭头，不进 rolling_predict / 采购量」）。

- **为什么 56 不是 60**：28 = 4 周、56 = 8 周，恰好 2 倍，两窗口的星期构成完全对称；
  60 天是 8 周 + 4 天，两端错开 4 个星期位，比值里会混进"这 28 天含几个周六"的伪影。
- **为什么不能用融合值**：融合权重表本身已把短期趋势吸收进权重（`ds28/fused` 中位 2.00），
  拿它当分母，箭头读的是权重表而不是需求变化。
- **削峰必须与窗口配对**：`cut_series(daily, win)` 的 `max_win=win / hist_days=win+32`。
  复用 28 天削峰结果算 56 天窗口，29~56 天前的促销尖峰会留在窗口里抬高基准。
- **实测分布**（jd，1500 样本随机）：新口径比值中位 1.349、p10 0.444、p90 2.000。
  同期数据本身近 28 天总量是前 28 天的 2.35 倍，所以"多数朝上"是数据事实而非口径伪影。

> **已知限制（未修，等定）**：`nnz28 < 3` 的稀疏 SKU，28 天窗口被 shrink（×nnz/3）
> 而 56 天窗口不 shrink，两侧收缩系数不同 → 比值偏小。抽样里这类占 27%，其中 71% 判"下滑"。
> 后端已能识别（`nnz28` 列在），但前端判据在 JSX 里，加保护要动渲染 —— 按"只改计算不动
> 渲染"的要求，本轮先如实记录。

**收敛前的状态（供回溯）**：告警读 `ds28` 单值、补货读 cut、其余读原始列，同一 SKU 的
`daily_sales` 最多差 39%（SKU-08291-J）。收敛后四家同名同值，只剩采购窗口数不同。

**收敛的代价（实测，2026-11-16 对账）**：

| 项 | 变化 |
|---|---|
| 进销存 / 看板 日销 | 中位 **−23.9%**（cut 较 raw），54% 的 SKU 降超 20% |
| 告警判定（旧 ds28 → 新 cut 融合） | jd 新增 4 / 消失 75（0.8%）；other 新增 1 / 消失 90（0.9%） |
| 采购建议量 | 随日销同步下降，约 −20%（cut 较 raw） |

> 消失的告警里有一类是**间歇性需求** SKU（不是「卖得少」，是「卖得断续」）。
> 以 `SKU-06001-J` 为例，告警用的值 3.536 → 0.99，拆开看是两个原因叠加：
>
> | 环节 | 值 | 贡献 |
> |---|---|---|
> | 旧口径（只取 `ds28`） | 3.536 | — |
> | 三窗口融合（`nnz7=0`、`nnz14=1`、`nnz28=6`，档位 −1/−1） | 1.209 | **−65.8%**（28 天仅占 25% 权重） |
> | 再叠 `shrink`（14 天窗口 `nnz=1` → ×1/3） | 0.99 | −17.9% |
>
> 也就是说：**主因是三窗口趋势加权对间歇需求的固有偏差**（近期窗口几乎为 0，把值往下拉），
> cut 只是次要一环。这不是 bug，但业务必须知道这类 SKU 现在更不容易报警。
>
> 现有规则参数里**没有只针对稀疏 SKU 的旋钮**：`buffer_orange` / `buffer_yellow` / `lit`
> 都是全局阈值，调它们会让所有 SKU 一起变灵敏。若确实要治，得在融合层加稀疏保护
> （如 `nnz7=0 且 nnz14<3` 时抬高 28 天权重），那是新机制，需要单独定方案。

`season` / `active_factor` 当前全 `enabled:false`（`season_config_bbcc` 三条），
启用后 ×1.3~1.5；`act` 在 7/14 天无销量时 ×0.5（断货复售的保守处理）。

> `season` 的读取在 2026-11-16 收敛为 `core.metrics.season_factor`（补货与采购共用一份）。
> 改之前采购是「本渠道 + `channel=''`」塞 dict 互相覆盖，全局行会盖掉本渠道配置 —— 启用
> 季节系数那天，两个页面会算出两个需求量。`active_factor` 仍是死配置（见 `CONTEXT.md` P8-4）。

### 数据新鲜度（停更提示，2026-10-29 上线 / 2026-11-16 根治）

所有数据源都是 ERP/平台导出快照，停更时界面原本无感知（日销逐日摊薄 → 建议量缓慢失真，
没有任何一个数字会"跳"）。`data_freshness` 表记每个源的最后导入时间，前端在标题/胶囊上
渲染一个 7px 圆点（`level>0` 才渲染，正常时零布局影响），点开看指标级 + 源级明细。

| 环节 | 规则 |
|---|---|
| 记账 | `common.record_freshness(channel, target, rows)` 唯一写入点；`last_biz_date` 取**库里真实** `MAX(业务日期)`，不是"这批文件里的最大日期" |
| 天数 | `max(业务日期差, 导入时间差)` —— 内容旧就是旧，不看谁导的；`_FRESH_IMPORT_ONLY` 里的源（库存/档案/回款）只认导入时间 |
| 阈值 | 按源分档：日更（订单/库存/平台库存/B仓库存）1 天橙、3 天红；流水 7/30；回款 30/60；档案 14/60（代码 `routes/misc._FRESH_TTL`，2026-11-17 核） |
| 缺失 | 未导入的源也进 `sources`（`days=null / level=0 / missing=true`）—— 看得见，但不报红 |
| 聚合 | `staleLevel` = 各源最差；看板 4 个指标取依赖源的最旧 |

> 阈值是代码常量 `routes/misc._FRESH_TTL`，新增 `doc_type` 必须同时登记，否则走默认 7/30
> （低频源会被当高频误报）。为什么不再用"一刀切 7 天"：日更数据停 7 天恰在阈值内 →
> `level=0` → 圆点不渲染。2026-11-09 断更到 11-16 正是这种情况，用户实测"没有任何提示"。

---

## 补货核心逻辑

### 日销滚动预测（三窗口融合）

7/14/28 天窗口各自 3σ 异常剔除 + 近 3 天 1.5 倍加权，按趋势信号自动分配权重；BBCC 用全国 C 仓合计日销，传统用逐仓日销（与断货判定同源）。

2026-11-16 起四家消费方一律传 `cut` 三列，这里不再区分「谁传原始列」——见上一节 §日销口径。

### 濒临断货三级判定（供应链时间线优先）

```
Adj-DOS = (在仓可用 + 在途×OTIF置信 + B→C调拨×1.0) ÷ 日销(×季节系数×加速倍率)
OTIF    = suppliers.score/5 (0.6~1.0)；动态安全库存 SS = Z(1.65)×日销σ×√L/T
🔴 紧急: Adj-DOS ≤ L/T（含已断） | 🟠 预警: ≤L/T+1 且 Buffer≤1.2 | 🟡 关注: Buffer≤1.0
```

参数可在规则页"濒临断货预警"规则中配置（卡片同源读取），停用规则=回默认。

### BBCC 两步法

```
C缺口 = max(日销×前置期 − C可用 − B→C在途, 0)
B建议补 = C缺口 − B可用 − B在途 + 调拨期消耗（箱规取整）
供应商统一发 B 仓 → B→C 调拨补 C；采购闭环：导出建议→评估→下单→导入在途列
```

### 采购建议公式

```
建议采购 = max(日销(14+28融合)×采购前置期 + 安全库存 − 系统总库存 − 已下单在途, 0)
供应商级参数(前置期/安全天数/MOQ) 回退全局；同供应商合计<MOQ 按占比放大
```

---

## 测试

```bash
cd cloud-functions/api && python3 local_test.py   # 全量（auth/看板/补货/清洗/规则/开关联动/agg路径/接力导出/历史归档/cron窗口/S2 系列）
cd cloud-functions/api && python3 local_test.py --sql-smoke   # 另加 396 条 EXPLAIN + schema 对账(需 TIDB_*)
```

---

## 部署

| 组件 | 位置 | 方式 |
|------|------|------|
| 前端+后端 | **EdgeOne Makers**（Git 集成自动构建部署） | `python3 scripts/push_api.py -m "…"` 推送（GitHub Git Data API）→ 自动构建 |
| 生产域名 | **supplykit.top**（免签公开访问） | CNAME → supplykit.top.pages.dnsoe6.com |
| 数据库 | **TiDB Serverless** | pymysql 直连 |

```bash
# 部署：push_api.py 推送（GitHub Git Data API）→ Makers Git 集成自动构建
# 注意：本机 git remote 只有 origin，无 edgeone 远端；github.com:443 在本沙箱不可达，不要用 git push
python3 scripts/push_api.py -m "fix(x): ..."
# 构建状态查询 + 部署后冒烟 /api/health（部署确认/细粒度指纹方法见 docs/DEVELOPMENT）
```

> **定时任务**：cron 12 个端点中 10 个由 edgeone.json schedules 调度（**maxItems 上限 10，已占满**，最小间隔一天）；
> `ledger-close`（账期月结）与 `agg-sweep` 不单独挂调度 —— 月结嵌入 `cleanup-logs` 调用，不新增调度条数。详见 `docs/decisions/0002-账期月结.md`。
> **自愈**：index.py 启动幂等（补索引/补列/内置规则补种与退役）；cron freshness 守护快照。

---

<p align="center">
  <a href="https://supplykit.top">在线体验</a> ·
  <a href="docs/DEVELOPMENT.md">开发规范</a> ·
  <a href="CHANGELOG.md">变更日志</a>
</p>

---

## 历史与迁移（2026-10-25 文档治理合并——原 MIGRATION_EDGEONE / EDGEONE_VERIFICATION / TIDB_MIGRATION / PRODUCTION_CHECKLIST 精华)

> 迁移决策、验证结论、平台硬限制、上线防护的历史沉淀——全部并入本文档,原独立文档已删除(教训细节见 history §15 系列)。

### 双源码分支与回退
- `main` = PA+SQLite 备份版(已冻结,可回退);`feat/edgeone` = EdgeOne Makers + TiDB 主线;远程 `edgeone`=Overtrees/Supplykit-edgeone
- **回退点**: `git checkout backup-pa-sqlite-20260904`(1 小时内恢复 PA 版)

### Makers 迁移验证结论(2026-09-05 实证)
- 入口检测: 行首 `app =`(构建器正则 /^app\s*=/m);函数包=仅 cloud-functions/;依赖 uv+CPython 3.10;九路由线上全通
- **生产公开访问**: supplykit.top(CNAME pages.dnsoe6.com, Area=overseas 免备案, 免 eo_token 签名)——与 EO 网站加速二选一(DNS CNAME 互斥)
- 签名机制/框架规范细节: 见 docs/MAKERS_API.md §3 + history §15.19/§15.20

### TiDB 硬限制(实测)与 RU 预算
- ①单条多值 INSERT ~500 行上限 ②大 DELETE 全表内存取消(8176)→ 分批 LIMIT 10000 ③execute 返回 rowcount(非 lastrowid) ④DATETIME 读出 datetime 对象(比较/展示 str[:10]) ⑤TiDB 异步 DDL(索引并入 CREATE TABLE)
- **RU 预算**: 免费 5000 万 RU/月(按集群独立——旧集群耗尽不影响新集群);高频接口索引直达后 ≤51 RU/请求;EXPLAIN ANALYZE 实测走控制台用量页观察
- 迁移决策: SQLite ORM 适配 TiDB 边际成本失控 → **停适配, 方案B 原生重构**(数据层直写 TiDB 方言, 接口契约前端零改动)

### 上线防护清单(切真实数据前必做)
- 异地备份(GitHub Release, 每日拉取) / 目标版本语法门禁 / 误删防护 / 备份恢复演练(下载→gzip 校验→integrity_check→抽样核对→记录)——事故红线细节见 history §15.9/§15.17/§15.18
