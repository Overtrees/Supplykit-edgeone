# EdgeOne Makers 迁移评估与对比方案（2026-09-04）

> 前提：环境已验证（CLI 认证通过：梅熟日/100048405433）；源码已备份（git tag `backup-pa-sqlite-20260904` + tar.gz 归档）。
> 目标：后端迁 EdgeOne Makers（免费版），前端同步迁移，CF 保留为备选；PA 版源码完整保留可回退。

## 1. 双源码管理策略（git 分支，非拷贝）

| 分支 | 对应环境 | 说明 |
|------|---------|------|
| `main`（现 HEAD 7c20d884） | PA + SQLite（现状） | 已打 tag `backup-pa-sqlite-20260904` 可随时回退；未来也可整体迁轻量服务器 |
| `feat/edgeone`（新建） | EdgeOne Makers + TiDB | 从 main 拉出，在现有完整代码上增量改动 |

- 两份源码 = 两个分支，共享 git 历史，不复制文件
- 前端 `frontend/` 无需改动（Vite 构建产物可同时部署 CF 和 Makers）

## 2. 适配面量化（已在代码库实测）

| 项 | 数量/位置 | Makers 限制 | 处理 |
|----|----------|------------|------|
| Python 版本 | 3.11（PA） | **3.10** | `datetime.UTC` 43 处（3.11+ API）→ 批量改 `timezone.utc`（**已完成 Phase 1，20 文件 45/45 通过 3.10 语法验证**）；无其他 3.11+ 特性 |
| SQLite 数据层 | 214 ORM + 322 原生 SQL + 59 PRAGMA | **无持久文件系统** | 数据层换 TiDB（已有新加坡实例）或腾讯云 TDSQL；ORM 适配 |
| APScheduler 常驻任务 | 10 个（快照/归档/备份/规则/checkpoint 等） | **无常驻进程、单次≤120s** | ✅ **Makers 原生 `schedules` 支持**（edgeone.json cron 表达式+时区+触发路径，上限10条）——1:1 映射，无需外部 cron（CLI schema 实测确认） |
| WebSocket | `/ws/events`（清洗进度推送） | 平台层✅/函数层⚠️ | **修正：EO 平台层原生支持 WS**（站点加速→网络优化开启，HTTP/1.1，超时最长 300s）；Cloud Functions 运行时 ASGI WS 待实测（120s 上限内短连接可用）；前端已有轮询兜底，优先级低 |
| 线程长任务 | seed 填充（分钟级）、清洗导入、导出 | 单次执行 ≤120s | 拆分/异步化；**文件存储用 Blob 预签名 URL 直传**（createUploadUrl，绕开函数中转） |
| 请求体 | 清洗 CSV 导入 | **≤6MB** | Blob 预签名直传解决 |
| **存储** | — | KV（仅 Edge Functions，1GB）；Blob（Cloud/Edge 通用 1GB，Node.js SDK 现成，Python SDK 开发中）；**Python 函数可用 `context.agent.store`**（Blob 底层，snake_case API，跨实例持久化，CLI≥1.6.26 已满足） | **修正 2 次**：Python 可经 `context.agent.store` 存 JSON/会话态（非 SQL）；SQL 数据仍需 TiDB；大文件走 Blob 预签名（Node 中间层）或 COS |
| 免费额度 | — | Cloud Functions 100万次/月、KV/Blob 各 1GB、构建 500次/月 | 当前用量 ~6-7万次/月，余量充足 |

## 3. 迁移前后优势对比

| 维度 | 现状（PA + SQLite + CF） | 目标（Makers + TiDB + Makers前端） | 优劣 |
|------|--------------------------|-----------------------------------|------|
| **国内访问** | pythonanywhere.com/pages.dev 国内需代理 | EdgeOne 3200+ 节点、大陆地域（广州/上海/北京等） | 🟢 大幅改善，质变 |
| **成本** | 0 | 0（免费版，Cloud Functions 100万次/月；TiDB 免费 5GiB+5000万RU） | 🟢 持平 |
| **部署** | PA curl 上传 + reload（409 假失败） | Git 推送自动构建（CLI/CI），无 reload 玄学 | 🟢 更现代化 |
| **磁盘配额** | 512MB 硬限（3 次事故源） | 无此概念（KV/Blob 各 1GB + 外部 DB） | 🟢 根治配额 |
| **数据安全** | SQLite 单文件 + 自愈 + 异地备份 | TiDB 多副本托管 | 🟢 更强 |
| **出站限制** | PA 白名单（TiDB 连不了，已实测） | Makers 出站无白名单（可连 TiDB 新加坡） | 🟢 解除 |
| **响应性** | 看板 1.7s 基线 | TiDB 跨公网 RTT + 函数冷启动；大陆地域部署后用户侧更快 | 🟡 服务器侧略降、用户侧提升 |
| **常驻能力** | scheduler/WS/线程任务全支持 | **修正**：scheduler→`schedules` 原生映射✅；WS 平台层支持✅；线程长任务受 120s 限制 | 🟡 改造量下调（schedules 免外部 cron） |
| **数据层** | SQLite 全功能（PRAGMA/WAL/自愈） | 需迁移 TiDB（20-40 人日白皮书估） | 🔴 核心工作量 |
| **改造量** | — | 数据层 + 任务系统 + WS + 版本兼容 | 🔴 估 3-6 周（单人） |
| **备案** | 无需 | 大陆加速区域自定义域名**需备案**；平台默认域名免备案但体验差 | 🟡 注意项 |
| **稳定性** | PA 脆弱（已多轮加固） | 腾讯云托管，SLA 高于 PA | 🟢 改善 |

## 4. 分阶段迁移路径(状态更新 2026-09-05)

- **Phase 0 ✅**：备份 tag + 归档；环境调试
- **Phase 1 ✅（09-04）**：datetime.UTC→timezone.utc（43 处）；3.10 语法门禁 45/45；全链路实证
- **Phase 2 🔄（09-05 基本完成）**：TiDB 建库（23 表+31 索引，index 并入 CREATE TABLE）+ seed 验证 + **方案B 原生重构**（九路由线上全通，停 SQLite 适配）；RU 48h 实测门禁=控制台用量页观察中
- **Phase 3**（待）：schedules 映射 10 常驻任务（cron+时区，上限 10 条）；WS→轮询（平台层支持待实测）
- **Phase 4**（待）：前端部署 Makers + 域名——**障碍: 函数访问需 eo_token 签名(3h), 浏览器前端无法自动带; 需自定义域名(大陆可用区需备案)或确认平台免签方案**
- **Phase 5**（待）：双跑验证 → 切流量 → 观察 2 周 → 停 PA


1. **Phase 0（已完成）**：备份 tag + 归档；环境调试（token/region/CLI 全通）
2. **Phase 1**：建 `feat/edgeone` 分支；`datetime.UTC`→`timezone.utc`（43 处）；本地 3.10 语法门禁验证
3. **Phase 2**：TiDB 建库 + 数据迁移（全量演练）；ORM 适配层（SQLite/TiDB 双后端）
4. **Phase 3**：schedules 映射 10 个常驻任务（edgeone.json）；WS 实测（平台层已开启则保留，否则轮询）；导入/导出走 Blob 预签名（Node 中间层或 COS）
5. **Phase 4**：前端部署 Makers（Vite 构建零改动）+ 自定义域名（备案或先用默认域）；CF 保留
6. **Phase 5**：双跑验证（PA 版为主、Makers 版影子验证数据一致性）→ 切流量 → 观察 2 周 → 停 PA

## 5. 风险与回退

- **回退**：随时 `git checkout backup-pa-sqlite-20260904` + PA 部署，1 小时内恢复
- **风险清单**：TiDB RU 免费额度（公网出口 1KiB=1RU，粗算看板重建接近上限）→ Phase 2 必须先跑 48h RU 实测；函数冷启动对看板体验影响需实测；商业化后免费额度可能收紧
- **决断点**：Phase 2 RU 实测超红线 → 停止迁移，维持 PA 或转轻量服务器

## 6. 结论

- 迁移**技术上可行**（Python 3.10 + FastAPI 原生支持已确认），国内访问是质变收益
- 代价是架构改造（数据层/任务系统/WS），**不是部署搬家而是适配迁移**
- 收益排序：国内访问(大) > 配额根治 > 部署现代化 > 托管稳定性
- 成本排序：数据层迁移（TiDB，最大） > 文件存储适配（Blob 预签名/Node 中间层/COS） > 任务映射（schedules 原生，轻） > 版本兼容（已完成）
- 建议：接受 3-6 周改造 + Phase 2 RU 实测门禁，可启动；否则维持现状（PA+SQLite）或转轻量服务器（零改造）
