# 0003: CF Worker 接力驱动（supplykit-relay-driver）

- 日期: 2026-11-17
- 状态: **已定案（已上线，生产实测闭环）**

## 决策

新增 Cloudflare Worker `supplykit-relay-driver`（Overtrees 账号），每分钟 GET
`https://supplykit.top/api/health`，用它驱动长任务的接力推进。**不占 EdgeOne
schedules 名额，不改应用的调度结构**。

## 背景：评估变慢是接力驱动饥饿，不是算得更慢

同工作量（6 块 / 20,000 SKU）的规则评估，耗时在 364s–2244s 之间摆动（**6 倍**）：
有人用 App 时 6–8 分钟跑完，无人时拖到 20–37 分钟。计算本身不慢——每块
约 20–30s CPU，6 块约 180s——慢的是**块间空等**。

机制（`core/rule_eval_task.py`）：评估走接力，`step()` 每轮最多 40s，推进只靠
外部调 `continue_tasks()`。驱动只有三处：

| 驱动 | 约束 |
|---|---|
| 中间件 60s 请求驱动（`index.py:322/327-328`） | 仅认证请求，进程内 60s 限流 |
| `/health`（`index.py:385-386`） | 有 running 任务才调 `continue_tasks()`，无限流；前端每 30s 轮询，UptimeRobot 每 5 分钟 ping |
| 平台 cron（每天 05:05） | 一天一次 |

证据：任务 240001 第 3 块花了 **308s ≈ UptimeRobot 的 5 分钟间隔**（中间无请求
推进）；180001（04:04 cron）跑 1 块后停住（凌晨无人用 App），被 30 分钟看门狗标
error；210001 的 error 字段残留 `stale running: instance frozen (no done/error
writeback)`——EdgeOne serverless 无请求即冻结实例。

心跳租约已复用但**心跳 ≠ 驱动**：评估 `_advance` 共用 `core/task_lease.py`
（2026-11-06 S5/P0-6 统一四类 sync_tasks 任务），BEAT 15s 续租、TTL 45s，防双跑、
45s 冻结后可接管。但心跳只保块内租约，不推下一块——下一块只在
`continue_tasks()` 被请求调起时开始。所以进度“只有后端推”，而评估之前只靠偶然流量。

## 被否掉的方案

| 方案 | 为什么不采用 |
|---|---|
| 1 分钟 EdgeOne cron | `edgeone.json` 的 `schedules` 上限 **10 条已满**、最小间隔一天。加第 11 条会让整组定时任务丢路由（0002 已记）；且即便能加，EdgeOne cron 的粒度也到不了 1 分钟 |
| UptimeRobot 改 1 分钟 | 用户拒升级、改不了。免费档最密 5 分钟，恰好就是饥饿的间隔本身 |
| 前端常驻轮询 | 只在开着 App 时有效。无人用 App 的凌晨、周末，驱动照样断档；把“有人在线”当成基础设施不可靠 |

## 为什么选 Worker

- **粒度够**：cron `* * * * *` 每分钟一次，块间空等从 ~5 分钟压到 ~1 分钟。
- **不碰配额**：Worker 在 Cloudflare 侧，不占 EdgeOne 10 条 schedules，不动 `edgeone.json`。
- **无请求风暴**：`/cron/continue-tasks` 有 30s 进程内节流（`routes/cron.py:48`
  `_throttle("continue-tasks", 30)`）；`task_lease` 保证单持有者推进；Worker
  （1/分钟）+ 前端（30s）+ UptimeRobot（5 分钟）合起来每 30s 最多推进一次；
  任务跑完 `run_c=0`，`/health` 不做事。
- **无任务时零开销**：`/health` 只在有 running 任务时才调 `continue_tasks()`；
  有任务时只提前做本就要做的活，不新增计算。`/health` 是 GET，只读 `run_c` +
  `data_version`，每分钟 +1 次读可忽略。
- **只线上受益**：Worker 只调 supplykit.top；本地是 systemd 常驻服务，不冻结，不需要。

## 调度落点

| 项 | 值 |
|---|---|
| Worker | `supplykit-relay-driver`，Overtrees 账号（`4c3178949cce0a3db4f993a3e14712a6`），UUID `a1ad00746ebf4af2944e0b077f9f5548` |
| 行为 | scheduled handler 每分钟 GET `https://supplykit.top/api/health` 并记日志；fetch handler 代理同一 `/health`（手动测试用） |
| cron | `* * * * *`，2026-11-17T07:30:52Z 设置，schedules-get 确认 |
| 源码 | `~/workspace/supplykit-relay-driver/worker.js`（Service Worker 格式，`addEventListener`；ES-modules `export default` 会报 10021） |

## 验证

- GraphQL `workersInvocationsAdaptive`：07:38:52Z 查时 0 次调用（cron 触发传播
  最长 15 分钟 + 统计延迟）；**07:39:50Z 起每分钟 1 次调用、0 报错**，持续至今——
  约 9 分钟传播延迟后生效。
- 评估 **270001**（2026-11-17 实测）：created 16:00:10 → finished 16:04:17，
  **247 秒（4.1 分钟）完成**，rows_out 30，error 空。此前同工作量无人时 20–37 分钟。
- 评估计算未变：仍跑满 6 块，只是更快；告警 6–8 分钟出，30 分钟看门狗超时减少。

## 代价与已知限制

- **Worker 本身是外部依赖**：Cloudflare 账号或 Worker 服务异常，驱动停摆，退回
  UptimeRobot 5 分钟驱动、评估重新变慢。Worker 不写数据、不改算法，坏也只坏速度。
- **cron 触发有最长 15 分钟生效延迟**：新建/改 cron 后不要立刻判“没生效”，
  先等 15 分钟再查 `workersInvocationsAdaptive`（本次实测约 9 分钟）。
- workers.dev 抓取测试 404（error 1042，该账号 setup 下 fetch handler 路径不可用）；
  验证靠 scheduled 执行统计 + 下一次生产评估耗时，不靠 workers.dev。
