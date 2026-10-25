# SupplyKit 架构与技术细节

技术评估者与开发者入口——架构、路由、项目结构、核心算法、测试与部署。

## 架构
## 架构

```
┌─ 前端 (EdgeOne Makers 同源部署) ────────────┐
│  React 18 · TypeScript · ECharts 5 · Zustand  │
│  Axios(30s缓存+在途去重+统一解包) · 版本指纹30s │
│  看板30s静默刷新 · 事件驱动全链路刷新           │
└──────────────────────┬────────────────────────┘
                       │ HTTPS (supplykit.top, 免签公开)
┌─ 后端 (EdgeOne Makers 函数) ────────────────┐
│  FastAPI · 24 路由模块 · local_test 155 项     │
│  ├─ 入口: index.py(行首 app=, 鉴权, 启动自愈) │
│  ├─ 业务: dashboard(断货三级/健康) /         │
│  │        replenishment / purchase / insights │
│  │        cleansing / rules(计算参数融合) /   │
│  │        cron(7端点) / tasks(seed分步) / ... │
│  ├─ 核心: core/rules.py(表达式引擎+计算变量)  │
│  │        biz/sales.py(三窗口日销融合)       │
│  └─ 基础: db.py(TiDB 原生)                   │
└──────────────────────┬────────────────────────┘
                       │
┌─ 数据库 ────────────────────────────────────┐
│  TiDB Serverless (pymysql DictCursor)        │
└──────────────────────────────────────────────┘
```

### 后端路由一览（API 分组 · 18 个——拆分模块见项目结构, 属代码组织非路由）

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
| `auth` | `/api/auth` | setup/login/check |
| `cron` | `/api/cron` | 7 端点（snapshot/freshness/archive/cleanup/daily-rules/recycle/push-alerts） |

### 项目结构

```
frontend/src/
├── pages/ (14个): Dashboard / Insights / Cleansing / Rules / Orders / Inventory /
│                 Products / Suppliers / Quality / Settings / Task / Login / ...
├── components/ (11个): Chart / Sidebar / Toast / ErrorBoundary / Icons / dash/RiskCard 等 + hammer/(10 个锤子面板组件)
├── store/useAppStore.ts          Zustand + 渠道/模式/批量状态
├── api/client.ts                 Axios + 缓存 + 统一响应解包
└── App.tsx / main.tsx / theme.ts / styles.css

cloud-functions/api/
├── index.py                      FastAPI 入口（行首 app=, 鉴权, 启动自愈 ALTER/补种/退役）
├── db.py                         TiDB 原生数据层（pymysql DictCursor）
├── biz/sales.py                  三窗口日销滚动预测 + 逐仓/全国C仓双口径
├── core/rules.py                 表达式引擎 + 计算变量注入 + 批量评估 + 恢复关闭
├── core/rule_cond.py             规则条件解析(2026-10-11 拆分)
├── routes/ (24个)                业务路由(2026-10-11 模块化: dashboard_maintain/stock_risk/health/cleanse_write/purchase_calc/task_export/task_seed)
└── local_test.py                 本地回归 155 项（mock db + TestClient）

scripts/gen_schema.py             SQLite→TiDB DDL 转换器
```

---

## 补货核心逻辑

### 日销滚动预测（三窗口融合）

7/14/28 天窗口各自 3σ 异常剔除 + 近 3 天 1.5 倍加权，按趋势信号自动分配权重；BBCC 用全国 C 仓合计日销，传统用逐仓日销（与断货判定同源）。

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
cd cloud-functions/api && python3 local_test.py   # 155 项（auth/看板/补货/清洗/规则/开关联动/agg路径/接力导出）
```

---

## 部署

| 组件 | 位置 | 方式 |
|------|------|------|
| 前端+后端 | **EdgeOne Makers**（Git 集成自动构建部署） | push `edgeone` 远程自动构建 |
| 生产域名 | **supplykit.top**（免签公开访问） | CNAME → supplykit.top.pages.dnsoe6.com |
| 数据库 | **TiDB Serverless** | pymysql 直连 |

```bash
# 部署：提交后推送 edgeone 远程 → Makers Git 集成自动构建
git push edgeone feat/edgeone
# 构建状态查询 + 部署后冒烟 /api/health（部署确认/细粒度指纹方法见 docs/DEVELOPMENT）
```

> **定时任务**：cron 7 端点由 edgeone.json schedules 调度（最小间隔一天）；每日规则评估/快照/归档/清理。
> **自愈**：index.py 启动幂等（补索引/补列/内置规则补种与退役）；cron freshness 守护快照。

---

<p align="center">
  <a href="https://supplykit.top">在线体验</a> ·
  <a href="docs/DEVELOPMENT.md">开发规范</a> ·
  <a href="CHANGELOG.md">变更日志</a>
</p>

---

## 历史与迁移（2026-10-25 文档治理合并——原 MIGRATION_EDGEONE / EDGEONE_VERIFICATION / TIDB_MIGRATION / PRODUCTION_CHECKLIST 精华)

> 迁移决策、验证结论、平台硬限制、上线防护的历史沉淀——全部并入本文档,原独立文档已删除(教训细节见 DEVELOPMENT §15 系列)。

### 双源码分支与回退
- `main` = PA+SQLite 备份版(已冻结,可回退);`feat/edgeone` = EdgeOne Makers + TiDB 主线;远程 `edgeone`=Overtrees/Supplykit-edgeone
- **回退点**: `git checkout backup-pa-sqlite-20260904`(1 小时内恢复 PA 版)

### Makers 迁移验证结论(2026-09-05 实证)
- 入口检测: 行首 `app =`(构建器正则 /^app\s*=/m);函数包=仅 cloud-functions/;依赖 uv+CPython 3.10;九路由线上全通
- **生产公开访问**: supplykit.top(CNAME pages.dnsoe6.com, Area=overseas 免备案, 免 eo_token 签名)——与 EO 网站加速二选一(DNS CNAME 互斥)
- 签名机制/框架规范细节: 见 docs/MAKERS_API.md §3 + DEVELOPMENT §15.19/§15.20

### TiDB 硬限制(实测)与 RU 预算
- ①单条多值 INSERT ~500 行上限 ②大 DELETE 全表内存取消(8176)→ 分批 LIMIT 10000 ③execute 返回 rowcount(非 lastrowid) ④DATETIME 读出 datetime 对象(比较/展示 str[:10]) ⑤TiDB 异步 DDL(索引并入 CREATE TABLE)
- **RU 预算**: 免费 5000 万 RU/月(按集群独立——旧集群耗尽不影响新集群);高频接口索引直达后 ≤51 RU/请求;EXPLAIN ANALYZE 实测走控制台用量页观察
- 迁移决策: SQLite ORM 适配 TiDB 边际成本失控 → **停适配, 方案B 原生重构**(数据层直写 TiDB 方言, 接口契约前端零改动)

### 上线防护清单(切真实数据前必做)
- 异地备份(GitHub Release, 每日拉取) / 目标版本语法门禁 / 误删防护 / 备份恢复演练(下载→gzip 校验→integrity_check→抽样核对→记录)——事故红线细节见 DEVELOPMENT §15.9/§15.17/§15.18
