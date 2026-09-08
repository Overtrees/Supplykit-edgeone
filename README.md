# SupplyKit — 供应链数据清洗与补货决策看板

<p align="center">
  <img src="https://img.shields.io/badge/React-18-61DAFB?logo=react" alt="React">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/TiDB-Serverless-2EA5E0?logo=tidb" alt="TiDB">
  <img src="https://img.shields.io/badge/EdgeOne%20Makers-deployed-F38020" alt="EdgeOne Makers">
  <img src="https://img.shields.io/badge/status-production-brightgreen" alt="Status">
</p>

<p align="center">
  <a href="https://supplykit.top">🌐 在线体验</a> ·
  <a href="#产品亮点">亮点</a> ·
  <a href="#功能总览">功能</a> ·
  <a href="#快速开始">开发</a> ·
  <a href="#部署">部署</a>
</p>

---

## 产品定位

面向电商供应链运营人员的**轻量级数据工作台**，定位为 ERP 与 Excel 之间的"中间层工具"——不做 ERP 的流程管理，也不替代 Excel 的灵活性。

从原始导出文件到补货决策，一条链路打通。SupplyKit 做"自动化"（数据清洗、补货计算、断货预警、看板监控），Excel 做"灵活性"（深度分析、报表排版），各司其职。

### 解决了什么

| 痛点 | 方案 |
|------|------|
| 京东/天猫后台导出数据杂乱，手动清洗费时 | 智能列名匹配 + 可视化映射，一次配置永久复用，导入即时联动库存/规则 |
| 补货靠经验拍脑袋，不同人算出来不一样 | 三窗口滚动预测日销 + BBCC/传统双模式，结果可复现可追溯 |
| B 仓超期仓储费、C 仓断货风险没人盯 | 濒临断货**三级判定**（Adj-DOS vs 补货周期）+ 采购&补货告警卡，自动联动 |
| 规则配置与看板计算脱节 | **规则=业务计算引擎配置**：断货/健康计算参数融合进规则编辑页，卡片同源读取 |
| Excel 做报表，每次都要重新拉数 | 看板 30s 静默刷新 + 版本指纹轮询，打开即用 |

---

## 产品亮点

### 🎯 从数据到决策，三步完成

```
导入 → 智能匹配 → 预览确认 → 一键执行（自动联动库存/规则评估）
  ↓
补货建议 → 查看/导出 → 执行下单 → 导入在途列完成闭环 → 断货/采购卡即时反映
```

### 🌐 多渠道支持，数据独立隔离

京东/其他渠道（天猫、唯品会等）的数据完全隔离：库存、商品、规则、配置、告警均按渠道独立；清洗导入自动标记渠道归属。

### 🔐 JWT 认证，支持访客模式

- HS256 JWT（零依赖）+ PBKDF2 密码哈希，首次设置管理员账号
- **访客模式** `demo / demo123`（只读，403 拦截写操作），适合在线体验

### 🔄 双模式补货 + 全页模式跟随

| 模式 | 一句话 | 适用渠道 |
|------|--------|---------|
| **BBCC** | 全国一盘棋：供应商统一发 B 仓 → B→C 调拨补 C 缺口 | 京东 |
| **传统** | 按仓逐条算，各仓独立补货（供应商直发各仓） | 京东 / 其他渠道 |

看板断货/低库存/健康/采购&补货卡、进销存 B 仓维度、规则 lit 双线**全部跟随补货模式**切换。

### 📊 看板一页尽览，按渠道/模式自动切换

- GMV 趋势 + 店铺/品牌分布 + 订单阶段转化漏斗
- **濒临断货三级预警**：Adj-DOS（修正可售天数：在仓+在途×OTIF置信+调拨）≤ 补货周期 L/T → 🔴 紧急 / 🟠 预警 / 🟡 关注；动态安全库存 SS、小时流速加速、季节系数全接入
- **采购&补货告警卡**：与建议页同源（补货=建议需补、采购=需采，行标签区分）
- 低库存卡按模式过滤维度（bbcc→BC盘+own / 传统→C仓+own）
- 告警逐仓（SKU×仓）、恢复自动关闭、30s 静默刷新

### ⚙️ 规则引擎 = 业务计算引擎配置

- **看板计算参数融合规则**：断货判定（OTIF 下限/动态SS Z值/加速阈值/分级 Buffer）与健康分档参数配置在规则编辑页，**断货卡/健康卡同源读取，即时生效**（停用规则=回默认）
- 条件字段支持看板同源计算变量（`inv.adj_dos`/`inv.buffer`/`health.score`/`params.lit` 等）+ `or` 多组
- **类型模板化**：选类型自动带出事件+条件+参数（超卖→order.created+quantity>avail 等），防事件-变量错配
- **告警开关**：内置断货/健康规则为参数载体（不重复告警，卡片已承载）；自定义规则默认开告警，开关即时联动（关=清存量/开=评估生成）
- 触发即命中恢复关闭、动作审计（params.log 写 quality_logs）、立即运行（双渠道全量 3s）

### 🧹 数据清洗，告别 Excel 手工

- 上传 Excel/CSV → 自动识别 30+ 中文列名 → 可视化映射 → 模板复用
- 6 类导入（订单/自有仓库存/平台仓库存/B仓库存/入库/出库/商品/供应商）+ 异步进度 + 去重保护
- 导入自动联动：库存增减、规则评估、渠道归属

### ↩️ 操作撤销 + 回收站

删除规则/订单后 toast 5 秒撤销窗口；设置页回收站批量恢复/永久删除。

### 📱 PWA + 首次引导

支持添加到主屏幕离线运行；首次使用欢迎页一键填充种子数据（异步分步任务）。

---

> [!IMPORTANT]
> **演示与免责声明**：本系统为供应链数据清洗与补货决策的**演示项目**。内置种子数据均为**虚构示例**，与任何真实企业、品牌或个人无关。请勿将演示数据用于真实业务决策。

## 功能总览

| 页面 | 核心能力 |
|------|---------|
| 📊 **看板** | GMV趋势 / 店铺/品牌GMV / 漏斗 / 健康度 / 断货三级 / 低库存(模式过滤) / 采购&补货 / 待处理 / 30s刷新+版本指纹 |
| 💡 **补货建议** | BBCC 两步法（C缺口→B调拨）/ 传统逐仓 / 三窗口日销 / B仓仓储费 / 已下单标记 / 导出 |
| 📦 **采购建议** | 14+28 融合日销 / 系统总库存 / 供应商级参数+MOQ聚合 / 采购告警 / 导出 |
| 🧹 **数据清洗** | 6 类导入 / 渠道标记 / 列名匹配 / 模板复用 / 异步进度 / 库存联动 / 规则评估 |
| ⚙️ **规则引擎** | 计算参数融合(断货/健康) / 计算变量字段 / or 多组 / 类型模板 / 告警开关 / 立即运行 / 审计 |
| 📋 **订单明细** | 分页 / 搜索 / 状态筛选 / 软删撤销+回收站 |
| 📦 **进销存台账** | 自有/平台/B仓三视图(模式跟随) / 批次效期 / 当月进出 / 告警跳转定位 |
| 🏷️ **商品/供应商** | CRUD / 搜索 / 批次效期 |
| ⚠️ **异常记录** | 数据质量日志（分页加载更多）/ 断货审计 |

### 大数据能力（万级 SKU 业务量）

| 能力 | 说明 |
|------|------|
| 后端分页 + 前端滚动懒加载 | page/page_size + IntersectionObserver 哨兵逐页 |
| 分析缓存四维保障 | 写操作 `invalidate_all()` 中央失效（不等 TTL）；缓存 key 含 channel/mode |
| 规则批量评估 | params 预解析 + 按事件计算变量检测 → 双渠道全量 3s |
| 实时性 | stock-risk 独立 30s TTL；aux 60s；事件驱动全链路刷新；30s 兜底 |

---

## 快速开始（开发）

```bash
git clone https://github.com/Overtrees/Supplykit-edgeone.git
cd Supplykit-edgeone

# 后端本地回归（mock db + TestClient，改代码先跑）
cd cloud-functions/api && python3 local_test.py        # 91 项全过

# 前端
cd frontend && npm install && npm run dev

# 3.10 语法门禁（部署前）
python3 -c "import ast; ast.parse(open('cloud-functions/api/index.py').read(), feature_version=(3,10))"
```

环境变量（Makers 项目 env，部署时快照）：

| 变量 | 说明 |
|------|------|
| `TIDB_HOST/PORT/USER/PASSWORD/DB/SSL` | TiDB Serverless 连接 |
| `DB_BACKEND` | `tidb` |
| `JWT_SECRET` | 固定值（防多实例竞态） |
| `CRON_SECRET` | 定时任务校验 |

---

## 架构

```
┌─ 前端 (Cloudflare Pages, 同源部署) ──────────┐
│  React 18 · TypeScript · ECharts 5 · Zustand  │
│  Axios(30s缓存+在途去重+统一解包) · 版本指纹30s │
│  看板30s静默刷新 · 事件驱动全链路刷新           │
└──────────────────────┬────────────────────────┘
                       │ HTTPS (supplykit.top, 免签公开)
┌─ 后端 (EdgeOne Makers 函数) ────────────────┐
│  FastAPI · 17 路由模块 · local_test 91 项     │
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

### 后端路由一览（17 个）

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
| `analysis_cache` | — | 中央缓存失效注册表 |

### 项目结构

```
frontend/src/
├── pages/ (13个): Dashboard / Insights / Cleansing / Rules / Orders / Inventory /
│                 Products / Suppliers / Quality / Settings / Task / Login / ...
├── components/ (13个): Chart / Sidebar / Toast / ErrorBoundary / Icons / hammer/(9) / ...
├── store/useAppStore.ts          Zustand + 渠道/模式/批量状态
├── api/client.ts                 Axios + 缓存 + 统一响应解包
└── App.tsx / main.tsx / theme.ts / styles.css

cloud-functions/api/
├── index.py                      FastAPI 入口（行首 app=, 鉴权, 启动自愈 ALTER/补种/退役）
├── db.py                         TiDB 原生数据层（pymysql DictCursor）
├── biz/sales.py                  三窗口日销滚动预测 + 逐仓/全国C仓双口径
├── core/rules.py                 表达式引擎 + 计算变量注入 + 批量评估 + 恢复关闭
├── routes/ (17个)                业务路由
└── local_test.py                 本地回归 91 项（mock db + TestClient）

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
cd cloud-functions/api && python3 local_test.py   # 91 项（auth/看板/补货/清洗/规则/开关联动/字符串契约）
```

---

## 部署

| 组件 | 位置 | 方式 |
|------|------|------|
| 前端+后端 | **EdgeOne Makers**（makers-8gstkvheqm2c, Area=overseas 免备案） | push `edgeone` 远程自动构建 |
| 生产域名 | **supplykit.top**（免签公开访问） | CNAME → supplykit.top.pages.dnsoe6.com |
| 数据库 | **TiDB Serverless** | pymysql 直连 |

```bash
# 部署：提交后推送 edgeone 远程 → Makers Git 集成自动构建
git push edgeone feat/edgeone
# 构建状态查询（DescribePagesDeployments）；部署后冒烟 /api/health
```

> **定时任务**：cron 7 端点由 edgeone.json schedules 调度（最小间隔一天）；每日规则评估/快照/归档/清理。
> **自愈**：index.py 启动幂等（补索引/补列/内置规则补种与退役）；cron freshness 守护快照。

---

<p align="center">
  <a href="https://supplykit.top">在线体验</a> ·
  <a href="docs/DEVELOPMENT.md">开发规范</a> ·
  <a href="CHANGELOG.md">变更日志</a>
</p>
