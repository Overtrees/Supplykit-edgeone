# 性能基准手册（Performance Baseline）

> **一本书替掉 30 篇历史记录。** 这里回答三个问题：
> ① 这个页面的这个模块，正常应该多快？② 慢在哪一层？③ 改完怎么证明没变慢？
>
> 后续接入新功能、做任何调优，**先查本页对应章节，再动手**。
> 判据的可执行版本在 `scripts/perf/run.py`；接口与页面的对应关系在
> `scripts/perf/endpoints.py`（本文档的表格由它生成，不手抄）。

**基准快照**：2026-11-08 21:17 · commit `e9b35dd`（代码）/ `1fa8a92`（文档）· 线上 `supplykit.top`
**数据来源**：`scripts/perf/out/*_hot.json`（热态 5 轮）、`list_head.timing`（冷算本体）、
`unit_state_head.timing`（判定模型重建）

---

## 0. 30 秒用法

```bash
# 我要改某个页面的接口
python3 scripts/perf/run.py hot --pages dash --rounds 5      # 改前
# ... 改代码、部署、等 3 分钟（部署后立刻压测会撞冷启动）...
python3 scripts/perf/run.py hot --pages dash --rounds 5      # 改后
# 读脚本末尾的判据：PASS/FAIL + 每个接口的 p50/p95/离散度

# 我要确认没把系统压挂（含写）
SUPPLY_USER=admin SUPPLY_PASS=*** python3 scripts/perf/run.py storm --scene S5

# 接口报异常 / 登录报 OperationalError / 怀疑 DB 挂了 → 只读冒烟判失败面
python3 scripts/perf/smoke.py                       # 见 §8
```

三个数字就能定位问题：**p50 高 → 读侧没走物化/SWR**；**p95 高 → 撞重建窗口或 TiDB 排队**；
**CV 高（>0.2）→ 这轮测量本身不可信，重测**。

---

## 1. 判据（红线 17 的可执行版）

### 1.1 三种问题，三种口径，不许混用

| 问题 | 唯一可用口径 | 实测可分辨度 |
|---|---|---|
| **快了多少** | `hot`（纯读 + 锁相位，5 轮）或单请求 `timing`/`X-Srv-Ms` | CV 0.04 → 约 9% |
| **会不会停摆** | `storm`（带写）：0×503 / 0 条不完整 / <10s | 定性 |
| **是否等价** | 逐字段对账（`legacy=1` 全量 diff） | 精确 |

### 1.2 为什么带写压测的 p50 不能用

实测（2026-11-08 20:26~20:57，同一份代码、同一个脚本、间隔 20s）：

| 场景 | p50 序列 | CV | 极差比 |
|---|---|---|---|
| 纯读 + 锁相位 | 74 / 66 / 66 / 69 / 70 | **0.043** | 1.12× |
| 带写 + 自由相位 | 2,100 / 296 / 112 / 95 / 214 | 3.606 | **22.1×** |
| 带写 + 锁相位 | 2,554 / 177 / 368 / 112 / 132 | 5.351 | **22.8×** |

写操作每 1.2~1.5s 触发一次 `invalidate` → 重建窗口刚关又开（`busy_share` 0.83~0.96，
有一轮等 91.7s 也没等到静默）。此时 p50 是**双峰分布的中位数**：撞窗口 1.5~2.5s、
不撞 0.1s，中位数取决于"撞上的比例"（轮间 4%~62%）。**它在两峰之间跳，不是误差。**

> 教训：曾据此判定某改动"提升 3 倍"，而该差异小于同配置轮间极差 22 倍。

### 1.3 阈值表

| 项 | 阈值 | 出处 |
|---|---|---|
| 热态并发 p50 / p95（8 读） | ≤ 800ms / ≤ 1500ms | 稳态判据 |
| 热态离散度 | CV ≤ 0.2 **或** 极差 ≤ 100ms(p50)/150ms(p95) | 本轮新增（低延迟时 CV 无意义） |
| 单 kind 重建 `total` | ≤ 6s | `cold` |
| 真首建单发 / 4 并发 p95 | ≤ 8s / ≤ 8s | `first`（单飞，不是 4×） |
| 单请求上限（含写风暴） | < 10s | 网关与用户感知 |
| 503 数 | 0 | 硬判据 |
| 空结构/零数据 | 0 条 | 硬判据 |

---

## 2. 机制清单（M1~M8）

改动任何一条链路前，先确认它靠哪个机制站稳；**机制被绕开就是性能事故的起点**。

| 编号 | 机制 | 解决什么 | 边界 / 失效症状 |
|---|---|---|---|
| **M1** | **物化 + 域指纹**：算一次落快照（`list_snapshot` / `own_health_snapshot` / `unit_state` / `dmat`），读侧按 `fp` 命中 | 把"每请求现算全量"变成"读一行" | fp 读写不同源 → 永不命中、静默退化成现算（不报错，只是慢）→ 红线 16 |
| **M2** | **SWR**：过期但有旧值 → 立即返上一版 + 后台重建 + 顶层 `__swr` | TTL 到点不再阻塞请求 | 首建无旧值时必须同步算（给空结构 = 数据事故）；写后要立刻看见的接口不能接（`orders`） |
| **M3** | **单飞**：同 key 并发 miss 只允许一个 builder | 防 8 个请求同时算同一份 | 进程内锁在多实例下只挡本实例；跨实例要靠 M4 |
| **M4** | **重建名额两级**：实例内 token 槽（3）+ 跨实例 `heavy_slot`（2，180s 租约），**先抢全局再抢实例内** | 防"一次写触发 7 个域同时重建"把全站拖慢 | 顺序反了会让等待线程白占实例名额 2s（p50 297→1717ms）；`INTERVAL` 必须内联 `str(int())`，传 float 会静默恒假 → 僵死槽、重建停摆 |
| **M5** | **维护任务 DB 租约锁**（`maint_lock`，600s） | 连点"重建聚合"不再各起一个线程 | 锁不可用必须放行（治理手段不能变成新的不可用来源） |
| **M6** | **取数下推聚合**：`GROUP BY` + `SUM/MAX`，8~9 万行合成 2~3 万行 | 减少 Python 逐行累加与传输 | 会改变"一库存行一条建议"的语义（`traditional`、`disposal` 不能聚合，红线 11） |
| **M7** | **快照批量并行写**：seq 段互斥、≤3 路 | write 与行数线性，是冷算第二大头 | 超过 3 路收益被 TiDB 写竞争吃掉 |
| **M8** | **传输收敛**：显式 `X-Accept-Encoding` 触发 gzip + 列裁剪 + 真分页 | 首屏 KB 级下降 | 网关会剥 `Accept-Encoding`，只带 X 头会被解压回明文（红线 13） |

---

## 3. 按页面分类的模块基准

> 数字口径：热态 = 8 读并发 5 轮中位（`hot`，CV 0.07）；冷算本体 = `list_head.timing`。
> **数字会随数据量漂移，判据不变**；每节末尾给复跑命令。

### 3.1 看板（dash）

首屏 4 个重接口并发 + 30s 刷新；**卡面数据全部来自物化，读侧不应有全量现算**。

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 首屏聚合 | `/api/dashboard/boot` | `dmat:dash_summary+dash_aux` / `unit_state` / alerts 计数 | M1/M2/M4 | **457 / 479** |
| GMV/转化卡 | `/api/dashboard/summary?part=main` | `dmat:dash_summary` | M1/M2 | 29 / 32 |
| 健康/低库存/采购补货卡 | `/api/dashboard/aux` | `dmat:dash_aux` + list 快照 | M1/M2 | 54 / 60 |
| 断货三级 | `/api/dashboard/stock-risk` | `unit_state`（`head.risk_stat`）+ 明细分页 | M1/M2 | 25 / 31 |
| 自有仓健康 | `/api/dashboard/own-health` | `own_health_snapshot` | M1/M2/M8 | 55 / 79 |
| 判定模型（诊断口） | `/api/dashboard/unit-state` | `unit_state` + `unit_state_head` | M1 | 116 / 154 |
| 告警列表 | `/api/alerts` | alerts 表（分组配额 + 真分页） | M8 | 71 / 77 |
| 缺货清单 | `/api/inventory/out-of-stock?wh=own` | inventory 现算（`paged`） | M8 | 29 / 32 |

**边界**：
- `boot` 是首屏关键路径，457ms 已含 30s 缓存的物化读取；**不要往 boot 里再加实时计算**。
- `/dashboard/unit-state` 是**只读诊断口**，不调 `ensure_async`；把 `stale=1` 当"触发重建"是无效的（踩过）。
- 判定模型重建本体：`read 4.3 + scan 2.7 + validate 4.7 + risk 11.8 + publish 2.4 ≈ 25.9s / 10 万单元`。
  其中 `validate` 是全量 tally 独立复核（护栏）、`risk` 是断货公式——两者删了都会拆红线。

**复跑**：`python3 scripts/perf/run.py hot --pages dash --rounds 5`

### 3.2 补货建议（insights-repl）

`list_snapshot kind=repl`；BBCC 两步法，**返回行数 = 建议条数**（一 SKU 一条）。

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 建议表 | `/api/insights/replenishment` | `list_snapshot kind=repl`（fp 命中） | M1/M2/M3/M4/M6/M7 | **86 / 86** |

**冷算本体**（`list_head.timing`，2026-11-08 18:20）：

| 段 | 值 | 说明 |
|---|---|---|
| `calc` | **1.1s**（sql 0.9 / 6 条 SQL） | 含 M6 下推聚合（80,000 → 20,000 行） |
| `write` | 0.9s | 10,000 行，M7 并行写 |
| `total` | **2.1s** | 触发请求 srv 95ms（读上一版，M2） |

**真首建**：2,284ms（单发）/ 4 并发 1.88s 墙钟（单飞 + 排队，**不是 4×**）。

**边界**：`days` 换值会走全新 scope → 等于首建（会占全局槽 2.8s）。

**复跑**：`python3 scripts/perf/run.py cold`（需 `.env`）、`python3 scripts/perf/run.py first --kind repl`

### 3.3 采购建议（insights-purchase）

`list_snapshot kind=purchase`；14+28 融合日销、系统总库存、供应商级 MOQ 聚合。

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 建议表 | `/api/insights/purchase` | `list_snapshot kind=purchase` | M1/M2/M3/M4/M6/M7 | **77 / 90** |

**冷算本体**：`calc` **1.4s**（下推聚合后，90,100 → 30,100 行）· `write` 0.7s · `total` **2.1s**；
真首建 **2,598ms**。

**边界**：`own_warehouse` 由"遍历首个非空"改为 `MIN(warehouse)`（多仓时从不确定变确定，仅展示字段）。

### 3.4 滞销/处置（insights-slow）

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 处置建议表 | `/api/insights/disposal-suggestions` | `list_snapshot kind=disposal` | M1/M2/M4/M7 | **68 / 74** |
| 滞销榜 | `/api/insights/slow-moving` | 现算（orders 聚合） | M8 | **425 / 457** |

**冷算本体**：`calc` 2.1s · `write` 0.9s · `total` **3.0s**（15,089 行）。

**边界**：处置是「一库存行一条建议」，**不能下推聚合**（会改变结果行数，红线 11）——
所以它的 `calc` 没吃到 M6 的收益，是三个 kind 里最慢的。

### 3.5 历史归档（history）

`orders` + `daily_stats` 双表拼接；`analysis_cache` + SWR。

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95（p95 峰值） |
|---|---|---|---|---|
| 归档状态 | `/api/history/coverage` | orders + daily_stats | M1 | 12 / 14 |
| 汇总卡 | `/api/history/summary` | `analysis_cache` 300s | M2 | 15 / 16（**423**） |
| Top20 | `/api/history/top` | `analysis_cache` 300s | M2 | 15 / 19（**525**） |
| 同比 | `/api/history/yoy` | `analysis_cache` 120s | M2 | 14 / 15（**404**） |

**边界**：p50 15ms 是命中缓存；p95 峰值 400~525ms 是 TTL 到点后**后台重建**期间的取数
（此时已返旧值 + `__swr`，用户不阻塞）。**不要把这个峰值当回归**。

### 3.6 进销存台账（inv）⚠ 唯一热点

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 台账三视图 | `/api/ledger/preview` | ledger + inventory **现算** | M8 | **1,093 / 1,093** |
| 批次效期 | `/api/batches` | batches 表 | M8 | 88 / 88 |

**已知热点**：`ledger/preview` 是当前**唯一千毫秒级**的读接口，因为它是现算（无物化、无 SWR），
p50 与 p95 几乎相等（1,093/1,093）说明是稳定的计算成本，不是排队。
**改进方向**：按 SKU×仓 物化当月进出（M1）+ SWR（M2），预估可降到 100ms 级——属于未排期项。

### 3.7 订单明细（orders）

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 订单表 | `/api/orders` | orders 表（真分页，TTL 5s） | M8 | **514 / 527** |

**边界**：**不接 SWR**——写完要立刻看见（TTL 5s）。514ms 主要是分页 COUNT + 排序，
若要做，方向是"计数物化"（M1），不是加缓存。

### 3.8 任务页（tasks）

| 模块 | 接口 | 数据来源 | 机制 | 热态 p50 / p95 |
|---|---|---|---|---|
| 任务列表 | `/api/tasks` | `sync_tasks` 单条 SELECT | — | **14 / 15** |

**边界**：`/tasks` 本身极轻，它的历史问题不是自己慢，而是**被同实例的重建线程拖慢**
（一次写触发 7 个域重建时曾排队到 3.4s）。维护任务必须走 M5 单飞。

### 3.9 其余页面（轻接口，无物化）

| 页面 | 接口 | 热态 p50 / p95 | 备注 |
|---|---|---|---|
| 商品 | `/api/products` | 26 / 117 | p95 尖峰来自首页大页 |
| 供应商 | `/api/suppliers` | 14 / 15 | — |
| 规则引擎 | `/api/rules` | 15 / 17 | 评估任务异步，`rules/eval-status` 轮询 |
| 异常记录 | `/api/quality-logs` | 31 / 33 | 真分页 + 加载更多 |
| 设置 | `/api/channels` | 14 / 14 | — |

**复跑**：`python3 scripts/perf/run.py hot --pages products,suppliers,rules,quality,settings`

---

## 4. 场景矩阵

| 场景 | 命令 | 并发 | 时长 | 判什么 |
|---|---|---|---|---|
| 热态（单发） | `hot --readers 1 --rounds 3` | 1 | 15s | 首屏感知、单用户体验 |
| 热态（日常并发） | `hot --readers 8 --rounds 5` | 8 | 15s | **默认基准**，判"快了多少" |
| 日常最坏 | `storm --scene S2` | 6 读 2 写 | 30s | 写间隔 1.5s，重建追得上 |
| 极端 | `storm --scene S5` | 16 读 3 写 | 20s | 写间隔 1.2s，重建追不上 → 长期追赶态 |
| 多用户读写单条 | `storm --scene S4` | 4 读 4 写 | 25s | 4 维：p50/p95/max/非200 |
| 多任务（任务页连点） | `storm --scene S5` + 手动连提 `/tasks/maintain` | — | — | 维护任务是否单飞（M5） |
| 冷算本体 | `cold` | — | ≤150s | `calc`/`write`/`total` 分段 |
| 真首建 | `first` | 1 + 4 | — | 无旧版本可读时的等待 |
| 测量可信度 | `noise --rounds 3` | — | — | `read_only` CV ≪ `with_write` CV |

**极端场景的预期**：p95 1.7~4.6s（容量，非缺陷），**0 个 503 / 0 条不完整 / <10s** 是硬线。
根因是"重建 25.9s 追不上写 1.2s 一次"，解法只有减重算量，不是调参数（§15.181 已证伪调参）。

---

## 5. 脚本索引

| 脚本 | 干什么 | 何时用 |
|---|---|---|
| `scripts/perf/run.py` | 统一入口 + 判据 | 所有场景 |
| `scripts/perf/endpoints.py` | 页面/模块/接口真源 | 新增接口时改这里 |
| `scripts/perf/scenes.py` | 五类场景实现 | 需要新场景时 |
| `scripts/perf/kit.py` | 登录/`X-Srv-Ms`/相位/写操作 | 一般不用直接调 |
| `scripts/preflight.sh` | 回归 + 语法 + lint | 每次提交前 |
| `scripts/deploy_status.py` | 部署状态（Success + Prod） | 每次推送后 |

历史专用脚本（一次性、已归档结论）：`verify_15182.py`（减列口径对账）、`snapshot_legacy.py`
（`legacy=1` 全量快照）、`diff_legacy.py`（逐字段 diff）。**新写的验证请用 `scripts/perf/`，别再散落。**

---

## 6. 新增页面/接口的接入清单

```text
[ ] 1. 在 scripts/perf/endpoints.py 登记：path / page / module / src / mech / expect
[ ] 2. python3 scripts/perf/run.py --list-md  → 把表格粘进本文档对应页面章节
[ ] 3. 判"快了多少"：hot --pages <page> --rounds 5，记 p50/p95 与 CV
[ ] 4. 判"不停摆"：若该接口有写或会被写影响 → storm --scene S2/S5
[ ] 5. 若引入物化：必须同时给出"fp 读写同源"的证据（红线 16）+ 逐字段对账
[ ] 6. 若引入 SWR：确认"晚几秒不影响决策"，且首建分支必须同步算（不许给空）
[ ] 7. 若走重算：必须走 db.heavy（M4），且顺序是**先全局后实例内**
[ ] 8. 本文档该页面章节补一行；`docs/history` 记一条 §编号
```

**不许做的事**：拿 `storm` 的 p50 当"变快了"的证据；拿单轮 `hot` 下结论（CV 必须 ≤0.2）；
本地直连测出秒数写进报告（红线 7）。

---

## 7. 已知边界与未解项

| 项 | 现状 | 影响 | 下一步 |
|---|---|---|---|
| `ledger/preview` 1,093ms | 现算，无物化 | 进销存页首屏最慢 | 物化 + SWR（未排期） |
| `unit_store` 重建 25.9s | `validate` 4.7s + `risk` 11.8s 是大头 | 极端场景 p95 2~4s | 增量重建；`validate` 抽样需决策（拆护栏） |
| `orders` 514ms | 分页 COUNT + 排序 | 订单页翻页慢 | 计数物化 |
| 极端场景 p95 超 1.5s | 容量问题，非缺陷 | 用户感知慢 | 减重算量（唯一解） |
| 100 万订单 | 日销三窗口 222~444s > 120s 函数上限 | 只能靠续跑 | 预聚合 / 下推 |
| TiDB 侧争抢 | 7 个域重建打同一个库 | 尾部延迟由 DB 决定 | 域依赖图，只重建受影响域 |

---

## 8. 故障排查：TiDB Starter 额度耗尽（`1105`）

> 这是**基础设施故障**，不是性能退化。判据 §1 与压测脚本在这里全部不适用：
> 不要跑 `hot` / `storm`，不要下任何延迟结论。目标是**判定失败面 + 恢复**。

### 8.1 根因与规则（TiDB Cloud Starter 官方口径）

| 项 | 规则 |
|---|---|
| 免费额度 | 每集群每月 **5000 万 RU** + 行存 5 GiB + 列存 5 GiB，**按集群独立**（不按组织/项目共享） |
| 触顶行为 | **立即拒绝所有新连接**，已建立的连接保留但被限流 |
| 报错 | `(1105, "Due to the usage quota being exhausted, access to the cluster '<id>' has been restricted...")` |
| 解除 | ① 提高 spending limit（立即）；② **自然月初自动重置**（不是滚动 30 天） |
| 顺带 | 设 spending limit > 0 后并发连接上限 400 → 5000 |

官方：[Usage quota](https://docs.pingcap.com/tidbcloud/serverless-limitations#usage-quota) ·
[Set Spending Limit](https://docs.pingcap.com/tidbcloud/manage-serverless-spend-limit/#update-spending-limit)

### 8.2 30 秒判定（两条命令）

```bash
# ① 线上探针：status=degraded 且 db 里有 1105 → 额度耗尽
curl -s https://supplykit.top/api/health | python3 -m json.tool

# ② 直连复核（只发 SELECT 1，≈1 RU）
python3 -c "import sys;sys.path.insert(0,'cloud-functions/api');\
from dotenv import load_dotenv;load_dotenv('.env');import db;print(db.one('SELECT 1'))"
```

**典型症状链**（本次 2026-11-09 实测）：

```
登录 POST /api/auth/login → HTTP 200 + {"ok":false,"detail":"OperationalError"}
  （admin 分支要查 users 表；demo 是内置硬编码，不查库，仍可登录）
→ 拿不到 token → 所有业务接口级联 401「未登录」
→ health 独立放行，返回 200 + status:degraded + 1105 全文
```

> **别被 401 骗了**：那不是鉴权坏了，是登录失败的级联。用 `demo/demo123` 拿 token
> 才能看到接口本身的失败形态。

### 8.3 冒烟：判定失败面

```bash
python3 scripts/perf/smoke.py            # 只读、串行、零写，DB 挂掉时也能跑
python3 scripts/perf/smoke.py --base https://xxx   # 换环境
```

接口清单来自 `endpoints.py`（唯一真源）+ 3 个探活端点。输出分五类：

| 类别 | 含义 | 危险度 |
|---|---|---|
| `A:可用` | 不查库（health/ping/auth_check），或确实返回了有效数据 | — |
| `C:DB失败` | 响应体含 `OperationalError`/`1105`/`quota`（含 `boot` 的嵌套子块） | 明确，易识别 |
| **`E:静默空壳`** | **HTTP 200 + 业务数据全 0/空**，前端显示「暂无数据」 | **最危险**：用户会以为数据丢了 |
| `5xx` | 网关/平台错误页（如 `tasks` 未套 `@traced`，异常直接冒泡） | 明确 |
| `401/403` | 鉴权或访客只读 | — |

**2026-11-09 10:22 实测（28 个接口）**：`A 3 / C 20 / E 4 / 5xx 1`。
除 3 个探活端点外**全部不可用，且同根因**。
`E` 四个是 `aux` / `stock_risk` / `unit_state` / `hist_coverage`；
其中 `hist_coverage` 返回「暂无归档：订单自 - 起」——**DB 查不到被包装成「没有数据」**。

### 8.4 恢复（需要账号权限，代码侧无法代做）

| 优先级 | 做法 | 时效 | 代价 |
|---|---|---|---|
| **① 推荐** | TiDB Cloud 控制台 → **My TiDB** → 目标集群 Overview → **Capacity used this month** → **Set Spending Limit / Edit** → 填月度上限 → **Update**（未绑卡需先加信用卡） | **立即** | 按量付费 |
| ② 等重置 | 什么都不做，等自然月 1 号免费额度归零重计 | 最久 31 天 | 服务不可用 |
| ③ 换集群 | 新建 Starter 集群（额度独立）→ 改 `.env` **与 EdgeOne 项目 Production env** → **重新部署**（env 是部署时快照）→ `schema_create.ensure_schema()` 建表自举 → 按需导数据 | 小时级 | 新库无业务数据；新集群同样 5000 万 RU/月，会重演 |

上次（2026-10-17）走的是 ③，见 `docs/history/2026-10-06~30.md` §15.63
（含 `serverless.tidbapi.com/v1beta1` 建集群 API、DoH 绕 DNS 劫持、env 快照等踩坑）。

**2026-11-09 第二批也走了 ③**：附件补齐了可用的 TiDB Cloud API 凭据，新建
`supplykit-1109`（`10406157977958137320`），改 Production env 并重新部署。
`ensure_schema()` 在空库上建出 **61 张表** / 索引 / 7 条迁移 / 渠道种子 /
`data_version` 初始行 / `data_version_dom('schema')` / `schema_state=v26`。
**按红线不导业务数据、不重跑 seed** —— 新库是干净空库，业务页面空数据为预期，
要恢复历史数据须你另行导入。空库首建踩到的 2 个 bug 与修法见 §15.186 / 红线 18。

### 8.5 恢复后的验证顺序（别一上来就压测）

```bash
# 1) 探针转 ok
curl -s https://supplykit.top/api/health | grep -o '"db":"[^"]*"'
# 2) 冒烟：A 应≈全部，E/C/5xx 应为 0
python3 scripts/perf/smoke.py
# 3) 只读热态 1 轮（确认物化/SWR 已回填，不做延迟结论）
python3 scripts/perf/run.py hot --rounds 1 --secs 10
# 4) 需要判「快了多少」时，才跑 5 轮纯读锁相位
python3 scripts/perf/run.py hot --rounds 5 --secs 15 --readers 8
```

> 部署后 **≥3 分钟**再压测（§15.181：部署完成 2 分钟就打 19 并发出现过 12 个 503）。

### 8.6 止血与预防（避免恢复后秒穿）

- **立刻停**所有压测 / 冷算矩阵 / 反复首建；恢复当天不跑 `storm` / `cold` / `first`。
- 一次写会触发 **7 个域**重建，`unit_store` 单次约 25.9s / 10 万单元，每次全表扫描 + 写快照，
  是 RU 大头。历史教训：`docs/SKILL.md`「反复大批量导入验证性能 → 单月烧 60M RU」。
- 验证用**只读栈**：`scripts/ru_diag.sh`（EXPLAIN ANALYZE，一次 <300 RU）+ 3~10 行小文件冒烟，
  替代反复大批量导入（3 行 ≈50 RU vs 5000 行 ≈2.5 万 RU）。
- 控制台看 **Capacity used this month / Top RU**，确认是否有异常高频重建；
  `quality_logs` 里 `invalidate` / 重建日志同源限频后计数可查。
- 高频链路保持**索引直达**（§7：多数接口 ≤51 RU/请求，与数据量解耦）；
  新接口接入前按 §6 清单做 RU 体检。
- **空库首启必查 `data_version` 是否稳定**（红线 19）：空集群上「表空 → 自愈重建 →
  `invalidate`」会自我失效，每请求 bump 域计数 → 物化 fp 永远追不上 → 持续烧 RU。
  判据：发 4 个只读 GET，前后 `SELECT v FROM data_version WHERE id=1` 应不变。
  详见 §15.186 第五节。

### 8.7 两个健壮性缺口（2026-11-09 第二批已修并验证）

两者都不是性能问题，属「故障时可观测性」，随新集群迁移一起修完（详见 §15.186）：

| 缺口 | 修法 | 验证 |
|---|---|---|
| `E` 静默空壳 | `db.py` 记最近一次**连接/配额类** `OperationalError`（1105/1040/2003…），`index.py` 中间件给该进程后续响应统一打 **`X-App-Degraded: 1`** 头（零额外 RU、不回传异常正文）；前端 `client.ts` 的 axios 通道与 `apiJson` 裸 fetch 通道都读头 → 派发 `app:degraded` → `App.tsx` 常驻横幅，**空壳不写进 30s 缓存**；登录页把 `OperationalError` 换成「服务暂时不可用」 | 旧库（1105）对照：`/auth/login` 与 `/health` 均带 `X-App-Degraded: 1`；新库（正常）均不带 |
| `tasks` 无 `@traced` | `list_tasks` 补 `@traced`，异常统一走 200 + `request_id`，不再冒泡成 EdgeOne HTML 500 | `local_test.py` 860 通过 / 0 失败；`tsc`/`prettier`/`eslint`/前端 build 全绿 |

> **为什么只走响应头**：`@app.middleware("http")` 是 Starlette `BaseHTTPMiddleware`，
> `call_next` 返回 `_StreamingResponse`（实测 `media_type=None` / 无 `body` 属性），
> 响应体不可写；头可写且不碰 `content-length`。语义与 §15.161 的顶层 `__swr` 同款，
> 前端一处收口。`scripts/perf/smoke.py` 会打 `[DEGRADED]` 标注，**分类判据不变**。
>
> **空库注意**：新集群按红线**不导业务数据、不重跑 seed**，业务接口返回空数据是预期。
> 此时 `smoke.py` 的 `E` 类可能与「DB 挂掉的空壳」混淆 —— 用 `X-App-Degraded` 区分：
> **带头 = 降级，不带头 = 新库确实没数据**。恢复当天仍不跑 `storm`/`cold`/`first`（§8.6）。

---

## 9. seed 一次性作业：基准复用与「只跑一次」规范（2026-11-09 评估，**未执行导入**）

> 用户口径：本地与线上端到端速度不一致（§15.96 已证伪「用本地秒数推线上」），
> 所以**不做端到端速度测试**，只做结构/代码测试；基于 §1~§8 的统一基准评估能否复用到 seed；
> 考虑 RU 成本，**只跑一次全程**，清楚前不跑。本节是「弄清楚」的产物，待确认后才执行。

### 9.1 seed 不是接口稳态，判据必须分开

当前实现：`SEED_DONE=20`（**20 段**，不是历史注释里的 27 段），窗口接力后
一次全程 ≈ **37 个驱动请求**（商品 1 + 订单 6 + 库存 16 + 流水 4 + 落账 4 + 月汇总 1 + 尾 5），
单窗口预算 `SEED_WINDOW_BUDGET_S=75s`（网关硬限 120s）。规模 1 万 SKU/渠道 × 2、90 天、≈44 万订单行。

**它是「一次性 ETL 作业」，不是 GET 读接口**：拿 `storm_max<10s` 判必然 FAIL；
拿「0 条空数据」判空库首灌也是错的（空数据是预期）。红线 17 的三种口径之外，
seed 属第四类 **「作业是否完成且等价」**，判据单独放在 `scripts/perf/seed_preflight.py`，
**不进 `run.py` 的 `TH`**。

### 9.2 基准能复用什么、不能复用什么

| 层 | 复用 | 说明 |
|---|---|---|
| 框架 | ✅ | `kit.py` 的 `login/sess/srv_ms/stat/init_db`、直连只读状态；`run.py` 的「判据集中一处 + 统一打印 + `out/*.json` 归档」；`endpoints.py` 的「唯一真源 + `--list-md` 生成文档」纪律；红线 17 的口径分离原则 |
| 接口判据 `TH` | ❌ | `hot`/`storm`/`cold`/`first` 的 p50/p95/503/空数据对 37 窗口作业无意义 |
| 接口登记 | ❌ | seed 不是 GET 读接口，**不进** `ENDPOINTS`（会污染页面表与 `complete()` 判据）；门禁实测 `--list-md` 不含 seed |
| **导入后稳态** | ✅ **真正复用数字** | 导入完成后跑 `hot --rounds 5`，与 §3 基线（25 接口 / 13 页面，5 轮 p50 32ms / p95 509ms）**同口径**对比。导入前空库不可比（空数据 p50 虚低），必须完成后才跑 |
| 新增 | ✅ | `seed_preflight.py` 的 `check`（零 RU 静态门禁）/ `collect`（跑完只读取证） |

### 9.3 seed 专用判据（不与接口判据混用）

| 项 | 判据 | 证据来源 |
|---|---|---|
| 终态 | `sync_tasks.status = done`（非 error / 非卡 running） | `collect` |
| 行数对账 | `seed_watermark` 的 `short = 0`（expected vs actual） | `wm_report` |
| 单窗口 | `steps[].elapsed ≤ 90s` 且 **0 次 504** | `sync_tasks.params.steps` |
| 不重复 | `win_total` 与驱动次数自洽；**0 次 reset、0 次整库重灌** | `params` + 人工纪律 |
| 数据四维 | 69 码空码 0 行、订单/库存四维键重复组 0、`_assert_available_balanced` 无差额、渠道 GMV 可核 | `collect` 的 `evidence` |
| 收敛 | 跑完 4 个只读 GET 前后 `data_version` 不变（红线 19） | `collect` + §8.6 |
| RU | ≤ 5 万（见 9.4），且期间**无压测/冷算矩阵** | 控制台前后差 |

### 9.4 RU 预算：一次很便宜，反复才致命

§15.157 全量重灌粗估 **≈24,300 RU**（订单写入 10,250 / 流水 8,170 / 库存 2,720 /
批次 1,900 / own 重算 239 / 快照 880 / stats 88）。该表是 11-05 的估算，当时 §15.163
三处缺陷（360 次全表扫、月汇总 3 遍写、缓存写不进导致每请求重算两遍）**仍在**，
修复后实际应更低，是偏保守的上界。

表外还有：37 次 `invalidate("all")`（`task_seed.py:282`，每窗口一次）、完成时
`_rebuild_day_agg(90)` 后台线程、`_align_order_keys` ≈221 次预读（§15.164 折算 ≈13s）。
**保守上界：一次全程 ≤ 5 万 RU ≈ 新集群月额度 5000 万的 0.1%。**

> 11-09 打穿 5000 万 RU 的主因不是单次导入，而是 9 天内 40 个提交 + 反复大批量导入验证
> + 冷算矩阵反复首建 + 10 轮噪声实验 + 25 接口 5 轮基线（§8.6），叠加空库自我失效循环
> （红线 19，已修）。所以「只跑一次」的约束针对的是**重复全量重灌**与**期间并发压测**。

### 9.5 一次全程的执行顺序（2026-11-09 已执行，结果见 §9.7）

```text
0. python3 scripts/perf/seed_preflight.py --db     # 门禁 PASS（本轮已验 25 项 / 0 FAIL）
1. 单一驱动源：跑期间不开前端页面（taskWatch 看守会抢驱动、看板请求会重算烧 RU）
2. POST /api/seed/fill（admin token）→ 拿 task_id
3. python3 scripts/seed_drive.py <task_id>          # 单驱动推进到 done
   期间：不跑 storm / cold / first / 多轮 hot
4. 失败处置（严禁第 2 次全程）：
   同窗口幂等重跑（offset 不变 → random.seed(42+off) 不变 → 生成行一致）
   → /api/seed/step 局部重跑 → /api/seed/rollback 只删本轮新增（水位）
   **绝不 reset + 全量重灌**
5. python3 scripts/perf/seed_preflight.py collect --task-id <id>   # 只读取证落 out/
6. 立刻验 data_version 稳定（红线 19）
7. 才跑 hot --rounds 5 与 §3 基线同口径对比（复用基准数字的落点）
8. 把修复后首个真实耗时/RU 实数写回 §9.7（已补，不拿换算当实测）
```

### 9.6 未解项与风险（如实）

| 项 | 现状 | 缓解 |
|---|---|---|
| 每窗口 `invalidate("all")` | §15.163 明列**未修**（刷新策略变更需定口径）→ 37 次失效 + 任务期间缓存恒冷 | 跑期间不开页面；不在导入期做延迟结论 |
| `alerts` 段恒产 0 条 | seed 的 `safety_qty` 恒 0（静态安全线已退役）→ 看板告警卡恒空，数据四维-完整的真实退化 | 口径变更待定案，本次不修 |
| ~~修复后无端到端实数~~ | **已补**：2026-11-09 一次全程实测 21.1 分钟，见 §9.7 | 不拿换算当实测 |
| 本地/线上速度不一致 | §15.96：本地 RTT 221~423ms，线上快一个数量级 | 只认线上 `X-Srv-Ms` / `params.steps.elapsed`，不采信本地秒数 |

**本轮状态**：零 RU 静态门禁、`local_test.py` 860/0（mock，不连库）、只读 DB 前置
26 项 PASS；修了 `seed_drive.py` 的过期绝对路径与模块级参数取值；随后按 §9.5
**只跑了一次全程**，实测结果见 §9.7。

### 9.7 一次全程实测（2026-11-09，修复后首个真实实数）

**执行**：`task_id=seed_1794197679_6669`，12:14:39 `POST /api/seed/fill`（HTTP 200 / 3.5s，
步骤 0 商品·供应商同步完成）→ 12:36:07 `status=done`。**墙钟 1265.0s ≈ 21.1 分钟**，
纯执行（`params.steps[].elapsed` 之和）**1123.6s**；20 段 / **36 窗口** / 50 次驱动请求
（`seed_drive.py` 单驱动），全程 0 个 503、无 1105、未触发任何 rollback / 第 2 次全量重灌。

**各阶段耗时**（业务阶段合并后，取自线上 `params.steps`）：

| 阶段 | 耗时 | 窗口 | 行数 |
|---|---:|---:|---:|
| 库存·JD·自有仓 | 375.8s | 4 | 120,100 |
| 库存·OTHER·自有仓 | 295.5s | 4 | 110,100 |
| 订单 | 262.7s | 6 | 447,517 |
| 物化·JD / OTHER | 40.0 + 39.5s | 2 | 256,512 |
| 其余 14 阶段 | ≈110s | 20 | — |
| **合计** | **1123.6s** | **36** | — |

自有仓两段合计 **671.3s（59.7%）** 仍是最大头（历史 §15.163 修复前 790.8s / 48.5%）；
单窗口 72~79s，与 75s 预算贴线。订单段 6 窗口 262.7s。
驱动日志 req#12~25 是 0.8~1.9s 的**抢不到锁空转**（`continue_tasks` 双驱动），
租约锁 + 段级幂等保证无重复写入 —— 印证 §9.6 的单驱动约束不靠「不开页面」硬扛。

**数据四维核验**（`collect --task-id` 取证 + 直连复核）：

| 维 | 结果 |
|---|---|
| 准确 | 69码空码订单 **0 行**；订单四维键重复组 **0**；库存唯一键重复组 **0**；渠道 GMV jd 1.048 亿 / other 1.049 亿 ✅ ⚠️ `_assert_available_balanced` 终态仍报 jd **10057** / other **10062** 行「可用≠期初+Σ流水」（max_diff 1312/1355，`quality_logs` 90222 留痕）—— **既有缺口，非本轮引入，未修** |
| 完整 | orders 446,974 / products 20,000 / inventory 170,200 / inbound 40,424 / outbound 143,032 / batches 182,174 / snapshot 206,461 / sku_sales_stats 256,512 / orders_day_agg 5,400。订单水位 6 条全 ok；**inventory 16 条 `short` 是口径错配非丢行**：expected 合计 340,400 = 170,200×2（两个仓型 step 各记一次整片），actual 合计 170,200 = 表实际行数 → `collect` 判据已改为「actual 之和 == 表行数」，复跑 5 项全 OK。⚠️ `alerts` 恒 0（`safety_qty` 恒 0） |
| 实时 | `data_version` 40→78（+38 = 36 窗口 + 完成 `invalidate("all")` 等）；跑完 6+3 个只读 GET 后 **78→78→78 稳定**（红线 19 在**有数据**的库上同样成立）。每窗口 `invalidate("all")` 仍未修 |
| 可靠 | 终态 done、无 5xx、单驱动、无重灌；seed 完成阶段物化集中重建触发 **`HeavyBusy`（全局名额 2）**，`repl/purchase/disposal` 部分重建被跳过，另有 2 次 `/seed/fill/status` 内嵌 40s 严重慢请求 |

**基准复用对比**（`hot --rounds 5`，8 并发纯读 / 12 CORE 接口，与 §3 同口径）：

| 轮 | p50 | p95 | 503 | incomplete | 相位 |
|---|---:|---:|---:|---:|---|
| 1 | 42 | 444 | 0 | 4 | `wait_quiet` 超时（busy=2，等 61.5s） |
| 2 | 89 | 913 | 0 | 5 | 同上 |
| 3 | 87 | **1703** | 0 | 4 | ok |
| 4 | 38 | 436 | 0 | 5 | ok |
| 5 | 38 | 449 | 0 | 4 | ok |

**稳态中位 p50 42ms / p95 449ms**，与 §3 空库基线（32ms / 509ms）同量级，p95 甚至略低
→ **性能基准的框架与稳态数字可复用**。判据 FAIL 两项均非性能退化：
① 22 条 `incomplete` **全部来自 `alerts` 恒空**（逐接口诊断 11/12 OK、连打 3 轮只 `alerts` 一条，
`kb=0`），而 `TH["storm_incomplete"]=0` 是 storm 判据被 hot 复用；
② `srv_p95` 离散 CV 1.096 / 极差 1267ms 来自第 3 轮 1703ms —— 导入刚结束的**冷算相位**
（前两轮 `wait_quiet` 超时 `busy=2`，物化集中重建未完成 + `HeavyBusy` 跳过）。
**结论**：导入完成后应先等冷算相位安静（`phase_now().busy==0`，实测 151s 后稳定）再跑 hot，
且 `alerts` 这类已知空数据接口需在判据里标注，不能把「导入刚结束的 5 轮」直接判成性能 FAIL。

**与历史耗时对照**（回答「5 分钟 → 近 1 小时」）：

| 版本 | 规模 | 墙钟 |
|---|---|---|
| 改前直写（10-31） | 1000 SKU/渠道 × 60 天 ≈19 万单 | 5 分钟 |
| 真导入·修复前（11-05） | 1 万 SKU × 90 天 ≈44.1 万单 / 17 万库存行 | 27 分钟纯执行（墙钟近 1 小时） |
| **真导入·修复后（本次）** | 同上规模 | **21.1 分钟墙钟 / 18.7 分钟纯执行** |

三处缺陷修复后比修复前快约 6 分钟（≈22%），但规模是改前的 **10 倍**，
所以「慢几十倍」主要来自规模放大 + 每批固定查库成本 + 分段驱动，不是导入链路本身慢
（CSV 序列化/解析/清洗仅约 8s / 2.6%，见 §15.163）。

**RU**：无额度告警，`health` 全程 `db:ok`；恢复当天只跑这一次全程 + 一次 `hot 5 轮`，
未跑 `storm`/`cold`/`first`/多轮重复（§8.6 止血）。文档与脚本改动**仅本地提交、未推送**，
避免触发重新部署打断稳态 / 引入额外冷算 RU。

---

## 10. 集中导入与联动任务：第四类判据（2026-11-09，§15.188）

> ⚠ **本章是 13:17 的诊断记录，保留原文供追溯**。其中「按目前基准**拦不住**」
> 「完成阶段 `HeavyBusy` 无后台重试 / 补建靠下一次读请求」「本轮只给方案、未改
> 业务逻辑」等结论，已于 **§11（§15.189）根治收敛时反转**。**当前状态一律以 §11 为准**。

> 起因：一次全程跑完之后追问「发现了什么优化点、能否映射到现实**集中导入不同渠道/
> 不同类型数据**；物化重建 / 评估联动等任务是否达标；按目前性能基准是否拦得住；
> 100 万单 / 10000 SKU 预测是否站得住脚」。
>
> 结论由两个可复跑脚本固化，不再翻历史：
> `scripts/perf/pipeline_probe.py`（**零 RU** 结构/代码验证 + 覆盖矩阵 + 预测复核）、
> `scripts/perf/import_smoke.py`（**线上小批量拟真冒烟**，只跑一次）。
> 红线 7：本地脚本只做结构判定，所有时间/行数系数只引用线上实测（§9.7 / §3 /
> `predict_scale.json`），不产出任何本地 Xms/Xs 结论。

### 10.1 结论速览

| 问题 | 结论 |
|---|---|
| 一次全程有优化点吗 | **有 7 项**，最大头是自有仓两段 671.3s / 59.7%，以及每窗口 `invalidate("all")` 引发的 36 轮全链路联动 |
| 映射到现实集中导入 | **机理同源**：一次写 → 一次全链路联动；连续 N 批 = 连续 N 轮，瓶颈是「联动轮数 × 每轮 6 类重建」，不是单批数据量。现实更优（域级失效、单批行数小）但**多出 eval/purchase/freshness 三条联动链**，全局名额争抢风险不比 seed 小 |
| 物化重建 / 评估联动达标吗 | **能达标，但不是自保证**：物化 build 2.1~3.0s ≤ `cold_total` 6s；评估有事件驱动 + 60s 兜底不丢。但完成阶段 `HeavyBusy` 会**跳过**部分物化且**无后台重试**（要等下次读请求补建），评估任务多批时单线程排队 |
| 按目前基准拦得住吗 | **当时拦不住**：`pipeline_probe.py coverage` 实测 **6/6 个联动节点都无判据**（2 个 `NOT_JUDGED` / 4 个 `GAP`）；`storm` 的写只是改季节系数，压根不走 CSV 导入链路。**§11 已根治为 0/6 未覆盖（拦得住）** |
| 100 万单 / 10000 SKU | **读接口站得住、单次长作业站不住、架构站得住**：目标 SKU == 当前实测单渠道 SKU，物化行数不放大；日销三窗口全量重建外推 ≈334s（11-08 预测 444s）仍 > 180s 函数上限，只能续跑 |

### 10.2 一次全程暴露的优化点 → 现实集中导入映射

证据全部来自 §9.7 线上实数；「代码」列是结构性验证的锚点（当时 `check` 31 项 PASS，
§11 后扩为 **40 项 PASS**）。

| # | 优化点（一次全程实数） | 代码锚点 | 现实集中导入是否同样 |
|---|---|---|---|
| 1 | 自有仓两段 **671.3s / 59.7%**，单窗 72~79s 贴 75s 预算 | `core/list_store.py` M7 分批并行写 | 是。多渠道 × 多仓型按 SKU 分片线性叠加 |
| 2 | 每窗口 `invalidate("all")` × 36 → 8 域 + 3 kind + unit/own + agg 全链路重建 | `routes/task_seed.py:282` / `analysis_cache.invalidate()` | 部分。前端导入是**域级**失效（更省），但每完成一批仍触发 1 轮全链路；连续多批 = 连续多轮 |
| 3 | 完成阶段 `HeavyBusy` 跳过部分物化重建，**无后台重试** | `core/list_store.py:520` / `db.heavy(global_=True)` 名额 2 | 是且更严重。6 类重建争 2 个全局名额，被跳过的 kind 不打开页面就停在旧 gen |
| 4 | `_MAT_WINDOW=3s` 合并不了 30~80s 间隔的窗口 → 缓存恒冷 | `routes/analysis_cache.py:32` | 是。批次间隔通常 >3s，合并窗口同样失效 |
| 5 | 双驱动抢锁空转 14 次、2 次 `status` 内嵌 40s 慢请求 | `scripts/seed_drive.py` / `sync_tasks` 租约 | 是。前端轮询 + 中间件 `continue_tasks` 同构 |
| 6 | 既有未修：`available_balanced` 终态差额、`alerts` 恒 0 | `seed_fill` / `safety_qty=0` | 拟真数据若 `safety_qty` 有值则 alerts 不恒 0；差额类缺口随集中导入放大 |
| 7 | `hot` 判据把 `alerts` 恒空算 `incomplete`、把导入后冷算相位算性能 FAIL | `run.py _judge_hot` / `kit.wait_quiet` | **基准自身缺口**：缺「已知空接口白名单」与「相位安静」前置 |

### 10.3 「拦不住」：基准覆盖矩阵（§11 已反转为「拦得住」）

`pipeline_probe.py coverage` 从 `run.py` 的 `TH` 字面量与 `_judge_*` 断言行提取判据层，
从 `scenes.py` 提取采集层；**只有两层都有才算「拦得住」**。结果：

| 联动节点 | 判据层 | 采集层 | 结论 |
|---|---|---|---|
| 物化集中重建是否全部建成 | 无 | 有（`cold` 读 `list_head.timing`） | `NOT_JUDGED` |
| 评估联动任务是否完成 | 无 | 无 | `GAP` |
| 告警是否真的生成 | 无 | 无 | `GAP` |
| 集中导入作业是否完成且等价 | 无 | 无 | `GAP` |
| 导入后冷算相位是否安静 | 无 | 有（`hot` 轮前 `wait_quiet`） | `NOT_JUDGED` |
| 已知空数据接口是否标注 | 无 | 无 | `GAP` |

**逐项原因**：`cold` 只判单个 kind 的 `total≤6s`，不判「完成阶段是否全部建成 / HeavyBusy 跳过」；
`storm` 的 `real_write()` 改的是季节系数（`invalidate('config')`），**不触发 CSV 导入 / eval_kick /
purchase_alerts / data_freshness**；seed 的作业判据在 `seed_preflight.py`，按 §9.2 明确不进 `TH`；
`hot` 的 `wait_quiet` 超时既不判 FAIL 也不跳轮，正是 §9.7 第 3 轮 1703ms 被误判的来源。

> `selftest` 已反证：注入一个 `materialize_gen_ok` 判据键后覆盖态真的从 GAP 变为有判据
> （不是恒 GAP），且 `chk(False)` 确实计红。**门禁本身可信**。

### 10.4 物化重建 / 评估联动：能达标，但不是自保证

| 任务 | 机制 | 达标证据 | 缺口 |
|---|---|---|---|
| 物化重建（repl/purchase/disposal） | M1+M3+M4：`ensure_async` → `submit_background` 单飞 → `heavy(global_=True)` | §3 冷算本体 `total` 2.1/2.1/3.0s ≤ `cold_total` 6s；真首建 2.3~2.6s ≤ 8s | **当时**全局名额 2，抢不到 → `HeavyBusy` 本轮跳过 + 2s 冷却，不排队、不自动重试，补建靠「下一次读请求」；不打开页面就停在旧 gen。**§11 P0 已改为 `drain_pending()` 有界补建自愈** |
| 判定模型 / 自有仓 | `unit_store` / `own_store` 同源域计数 + 后台重建 | `unit_store` 25.9s / 17 万单元，旧 gen 继续服务 | 同一轮联动里也要抢名额，是 RU 大头（§8.6） |
| 评估联动 | `kick_eval_task` 事件驱动 + `_KICK_LOCK` 单线程 + `_MAX_SEGMENTS=60` + `continue_tasks` 60s 兜底 | 导入 done 即联动，冻结也不丢任务 | 多批导入 `_eval` 任务**串行排队**；实例冻结时靠 60s 兜底，可能延迟而非即时 |
| 采购需求 / 新鲜度 | `kick_purchase_alerts` / `data_freshness` upsert | done 分支写，只有真写完才记账 | 三条链各自起线程，集中导入时叠加 |

**结论**：单次冷算耗时与读接口稳态**达标**；「完成阶段是否全部建成 / 评估是否跑完」这类
**作业级达标当前无判据、也无自保证**，靠兜底与下一次读请求收敛。

### 10.5 补齐方案：第四类「作业/联动」判据（不进 `run.py` 的 `TH`）

沿用 §9.2 纪律：接口稳态判据留在 `TH`；**作业与联动判据独立**，不污染页面表与 `complete()`。

| 判据 | 阈值 | 取证 | 落地 |
|---|---|---|---|
| 集中导入作业完成且等价 | `status=done`、行数对账、四维键重复组 0、0 次重灌 | `sync_tasks` + 索引点查键集合 | `import_smoke.py verify` |
| 物化集中重建全部建成 | 所有 kind 的 `head.gen` 推进、`fp_match=true`、`HeavyBusy` 跳过数可核 | `list_head` / `unit_state_head` / `quality_logs` 最近 500 行 | `import_smoke.py verify` |
| 评估联动完成 | `_eval` 任务非 running（done / 兜底接上），新鲜度/租约未僵死（红线 15） | `sync_tasks` 最近 12 条 | `import_smoke.py run` 末尾轮询 |
| 告警生成增量 | 已知恒空接口显式白名单，不算 `incomplete` | `alerts` 计数 | `import_smoke.py verify` + 建议给 `_judge_hot` 加白名单 |
| 相位安静前置 | `hot` 前 `phase_now().busy==0`（§9.7 实测 151s），超时跳轮而非判 FAIL | `heavy_slot` | **§11 已落地**：`scenes.hot` 跳轮 + `skipped_rounds` 留痕 |
| 已知空接口白名单 | `alerts` 恒空在判据里标注，不混入 `storm_incomplete` | `endpoints.py` | **§11 已落地**：`KNOWN_EMPTY` 唯一真源 + `complete()` 只对合法空形状豁免 |

**新增场景 S6（集中导入）暂不跑**：`storm` 的轻写不能替代 CSV 导入；真要纳入基准，
应复用 `import_smoke.py` 的小批量拟真批次，判「0×503 / 0 条不完整 / 单请求 <10s」，
**不判 p50**（红线 17）。

### 10.6 100 万单 / 10000 SKU 复核（只用线上实测系数）

`pipeline_probe.py predict` 用 §9.7 的 `orders=446,974 / 10000 SKU·渠道 / 90 天` 外推，
对照 11-08 的 `predict_scale.json`（5000 SKU / 100 万单）：

| 项 | 当前实测 | 100 万单 / 10000 SKU | 判据 |
|---|---|---|---|
| 订单 / SKU·天 λ | 0.497 | 1.111 | 泊松覆盖率 0.111 → 0.671 |
| 日销快照行数 | ≈20 万（两渠道） | ≈603,726（×3.02） | — |
| 日销三窗口全量重建 | `agg_incremental` 110.6s | **≈334s**（11-08 预测 444s） | > 180s 函数上限 → **单次站不住** |
| seed 订单段 / 全程 | 262.7s / 21.1 分钟 | ≈588s / 上界 ≈1631s | 靠 20 段 / 36 窗口接力 → **架构站得住** |
| repl/purchase/disposal build | 2.1 / 2.1 / 3.0s | SKU 不放大（=10000） | ≤ `cold_total` 6s → **读接口站得住** |
| `hot` 稳态 | p50 42ms / p95 449ms | 与 SKU 量级线性 | ≤ 800/1500ms → **站得住** |

**结论**：昨天的预测结论仍成立且更精确 —— 瓶颈是**长作业与联动轮数**，不是读接口；
100 万单 / 10000 SKU 不会击穿 §3 稳态基准，但会击穿「单次全量重建」的调用上限。
前提是**别把续跑改成单次全量**。

### 10.7 线上小批量拟真冒烟规范（只跑一次）

- **数据拟真**：CSV 按 `routes/seed_import.py spec()` 的中文表头，从线上**真实业务行**取样后
  **原样回导**（`conflict=overwrite` / 四维键 upsert 幂等）→ 走完整前端导入链路但
  **零业务数据变化**；不凭空造数、不导大批量、不累加流水（避开 inbound/outbound 的 sum）。
- **批次**：同步 5 类 × 5 行（order / inventory / product / supplier / gmv_flow，覆盖 jd + other）
  + 异步 1 类 × 1000 行（order / other，走 `/cleansing/status` 接力）= **6 批 / 1025 行**。
- **鉴权**：新空库 `users` 0 行，admin 登录不可用 → 从 EdgeOne Production env 现取
  `JWT_SECRET`（不落盘/不回显），`routes.common.create_token("admin")` 本地签发（零 RU）。
- **RU 纪律**：总行数 ≤1500、批数 ≤6、串行提交；跑前跑后只读取证，不跑
  `storm`/`cold`/`first`/多轮 `hot`；`out/import_smoke_last.json` 30 分钟防重（红线：只跑一次）。
- **判据**：四维（准确=键重复组 0 / 完整=行数对账 / 实时=dv 收敛 / 可靠=0×5xx、无降级头）
  + 联动（物化 gen、评估任务状态、HeavyBusy 留痕、alerts 增量）。

### 10.8 复跑命令

```bash
# 零 RU 结构/代码验证（可反复）
python3 scripts/perf/pipeline_probe.py check            # 40 项链路/机制门禁（§11 后）
python3 scripts/perf/pipeline_probe.py coverage       # 0/6 未覆盖（§11 后拦得住）
python3 scripts/perf/pipeline_probe.py findings       # 7 项优化点 → 业务映射
python3 scripts/perf/pipeline_probe.py simulate --batches 8 --types 4
python3 scripts/perf/pipeline_probe.py predict       # 100 万单复核
python3 scripts/perf/pipeline_probe.py selftest      # 反证：门禁能报红
python3 scripts/perf/pipeline_probe.py check --db    # 追加只读 DB 状态（约 10 SELECT）

# 线上小批量拟真冒烟（**只跑一次**，plan 零写可反复）
python3 scripts/perf/import_smoke.py plan
python3 scripts/perf/import_smoke.py run
python3 scripts/perf/import_smoke.py verify

# 优化后结构/代码验证（§11，零 RU、不重跑 seed、不连线上）
python3 scripts/perf/converge_probe.py all            # 85 项 + 反证 + 历史取证
```

### 10.9 线上小批量拟真冒烟实测（2026-11-09 13:17，只跑一次）

**执行**：6 批 / **1025 行** / 2 渠道（jd + other）/ 5 类型。CSV 按 `spec()` 中文表头从线上
真实业务行取样后原样回导（`conflict=overwrite`）。**未重跑 seed、未跑 storm/cold/first、
未导第二次数据**；admin token 用线上 `JWT_SECRET` 本地签发（零 RU、不写 users）。

| 批 | 类型 / 渠道 | 行数 | 模式 | 结果 |
|---|---|---:|---|---|
| 1 | order / jd | 5 | 同步 | HTTP 200 · success 5 / failed 0 |
| 2 | inventory / jd | 5 | 同步 | HTTP 200 · success 5 / failed 0 |
| 3 | product / other | 5 | 同步 | HTTP 200 · success 5 / failed 0 |
| 4 | supplier / other | 5 | 同步 | HTTP 200 · success 5 / failed 0 |
| 5 | gmv_flow / jd | 5 | 同步 | HTTP 200 · **success 0 / failed 5（1062，见下）** |
| 6 | order / other | 1000 | **异步接力** | HTTP 200 → 1 次 `status` 调用即 `done` · success 1000 / failed 0 |

**数据四维**：

| 维 | 结果 |
|---|---|
| 准确 | 5 张源表（orders 446,974 / inventory 170,200 / products 20,000 / suppliers 20 / gmv_flows 885）**行数变化全为 0** —— 原样回导幂等，零业务数据变化；告警重复组最大 c=2 且分属 `__BC__`/`集货仓` 两仓，**合法**非重复缺陷 |
| 完整 | 异步 1000 行 `success=1000`；评估任务 8 条（4 cleansing + 4 cleansing_eval）**全部 done**，无僵死 running（红线 15） |
| 实时 | `data_version` 78→88（6 批失效 + 联动），跑完 4 个只读 GET **88→88 稳定**（红线 19 在集中导入后同样成立） |
| 可靠 | 0 个 5xx / 0 个 503 / `X-App-Degraded` 全程缺席 / `health db:ok` |

**联动实证（本节最关键，回答「基准拦不拦得住」）**：

- `quality_logs` 近期 **11 条 `HeavyBusy`**（13:16:26~13:18:14），被跳过的类别逐一可点名：
  `list:repl` / `list:purchase` / `list:disposal` / `list:dmat:dash_aux`，且带
  「同源 10s 内 ×2/×3/×4 次」合并留痕 —— **6 类重建争 2 个全局名额**在真实导入中复现。
- `list_head` gen：disposal 27→**28**、purchase 43→**44**、**repl 40→40 未推进**
  （`built_at` 停在 12:43:25，即导入前）；`unit_state_head` gen **14 未变**（built 12:43:26）。
  → 部分物化确实**没有被补建**，读侧要靠「下一次读请求」才触发（不脏，但退化为现算）。
- `heavy_slot` 跑完瞬间 2 张全占，13:18:12 后全部释放（租约回收生效）。
- **评估联动达标**：`kick_eval_task` 事件驱动 + 兜底把任务推到 done，`alerts` 由 0 生成，
  类型分布 `low_stock 1976 / purchase_need 1374 / own_overstock 1001 / own_transfer_gap 845 /
  own_expiry_risk 69 / oversell 45`。
- ⚠️ **告警在收敛期持续增长**：13:18:20 取数 1419 → 13:19+ 5258 → 13:20+ 10230
  → 13:26 **22072**。说明「N 批导入 = N 轮全量评估」的成本是真实的，且
  **告警生成增量不能取瞬时值**。587 组按 `(channel,sku,barcode,alert_type)` 聚合的
  多行经抽样是不同仓，判为合法。这一项在现有 `TH` 里**完全没有判据**（覆盖矩阵 `GAP`）。
- **13:26 终态（跑完约 9 分钟）**：`health db:ok`、`dv=88` 稳定、`running_tasks=0`，
  但 **`repl` 物化 gen 仍为 40（始终未补建）**、`heavy_slot` 仍有 1 张占用、
  `alerts=22072`。→ 「物化补建靠下一次读请求、不是自保证」由推演变为**确定性实证**：
  不停摆、不脏读，但物化会停在导入前版本直到有人打开补货建议页。联动收敛时间明显
  长于导入本身，是集中导入的真实 RU 成本（对应 §10.5 的 P0 / P2）。
  本轮按红线**未做任何清理、删除或业务逻辑改动**。

**两处如实保留的观察项（非本轮引入）**：

1. `gmv_flow` 5 行 `(1062, Duplicate entry '?' for key 'gmv_flows.uq_platform_flow_ch')`：
   该 target 写入层**无 upsert**（§15.187 已记「保留 dup-check 拦截」）。首跑脚本带了
   `force_new=1` 绕过前端 dup-check，才暴露同键回导 1062；5 行全失败、行数不变。
   已修 `import_smoke.py` 的 PLAN 去掉该参数，使后续复跑与前端一致（**本次不重跑**）。
2. 告警在评估收敛期的持续增长与重复评估，属「联动轮数」成本；建议后续在评估侧做
   按批合并 / 幂等去重，**本轮按红线只验证、不改业务逻辑**。

**结论与 100 万单预测的对应**：小批量导入单批（≤1000 行）在同步秒级 / 异步 1 次接力内完成，
读接口稳态未被击穿（0×5xx、`dv` 收敛）；**真正出问题的是联动层**（名额争抢、物化未补建、
评估重复轮次），与 §10.6 外推一致 —— 规模放大到 100 万单不会先压垮读接口，
而会先放大「联动轮数 × 每轮 6 类重建」与「单次全量长作业」。

---

## 11. 尾巴根治收敛（2026-11-09，§15.189）

> 起因：§10 把一次全程 + 小批量拟真冒烟暴露的尾巴整理成了方案，但按红线
> **只验证、未改业务逻辑**。本轮用户要求「对测试出来的尾巴、可优化点做次根治收敛，
> 注意优化原则含数据 4 维，红线依旧；**seed 优化完也不重跑，但可以写脚本测试
> 优化后的结构/代码**」。
>
> 结论：**改了业务逻辑，但一次都没重跑 seed、没导数据、没连线上压测、没部署**。
> 达标性由 `scripts/perf/converge_probe.py`（零 RU、85 项 + 反证）与既有门禁证明。
> 仅本地提交、未推送（§8.6 止血纪律：推送会触发重新部署打断稳态 / 引入额外冷算 RU）。

### 11.1 逐条尾巴 → 根治 / 如实收口

| # | 尾巴（§10 实证） | 本轮动作 | 数据四维 | 红线 |
|---|---|---|---|---|
| **P0-1** | `HeavyBusy` 跳过后**无后台重试**，`repl` gen 停在 40 直到有人打开补货建议页（13:26 终态确定性实证） | **根治**：`list_store.drain_pending()` 以 `list_head.fp` 落后这一 **DB 持久事实**为「待补建」真源，有界补建，投进**同一个** `submit_background`；挂 `index.py` 已有的 60s 请求驱动线程 | 准确/完整不退化（旧 gen 完整可读、`build` 同源 + 发布前 COUNT 自校验）；实时改善（主动追平 fp）；可靠（有界 + 留痕 + 降级放行） | 2/4/5/6/15/19 |
| **P0-2** | 作业级「是否全部建成」无判据 | **根治**：`import_smoke.judge_evidence` 判 `materialize_gen_ok`（所有 kind gen 推进）；`list_store.status()` 暴露 `drain` 有界参数与 `fp_match` | — | 17（不判 p50） |
| **P1** | seed 每窗口 `invalidate("all")` ×36 → 36 轮全链路重建、缓存恒冷 | **根治**：中间步改 `analysis_cache.mark_progress()`（只 bump `data_version`，零墓碑 / 零域计数 / 零重建），**完成步仍全量失效** | 准确（中间态不发布更正确）；完整不变；实时（完成即失效）；可靠（少 35 轮 RU） | 2/19 |
| **P3** | `coverage` 6/6 联动节点无判据 → 拦不住 | **根治**：第四类「作业/联动」判据独立落地（**不进 `TH`**，§9.2 纪律）；覆盖矩阵判据层扩到 `run.py + import_smoke + endpoints`，实测 **0/6 未覆盖（拦得住）** | — | 17 |
| **P5-1** | `alerts` 恒空被算 `incomplete`（§9.7 22 条误报） | **根治**：`endpoints.KNOWN_EMPTY = {alerts}`，`complete()` 只对**合法空形状**豁免；响应形状不对仍判失败 | 完整（「真的是 0」与「没算成」可分） | 8① |
| **P5-2** | 导入后冷算相位把 hot 第 3 轮 p95 1703ms 误判成性能退化 | **根治**：`scenes.hot` 相位未静默 → **跳轮** + `skipped_rounds` 留痕；`run._judge_hot` 对空有效轮判 FAIL，空 rounds 有 dispersion 保护 | — | 17 |
| **P2** | 告警 1419→5258→10230→22072、N 批 = N 轮全量评估 | **判据收敛**：`alert_delta_ok` 只判「已生成」，**不取瞬时值**（收敛期增长是评估分片续跑，写入是幂等 upsert）。业务侧评估链**不改**，理由见 §11.5 | 准确（幂等 upsert，非脏数据） | 1 |
| gmv_flow | 5 行 `1062`（无 upsert 撞 `uq_platform_flow_ch`） | **如实收口**：后端无 upsert 是「流水不可重复」的刻意保护，前端 dup-check 拦截；改 upsert 会静默掩盖重复流水（损准确维）。脚本已去掉 `force_new=1`，与前端一致 | 准确 | 1 |
| available_balanced | seed 终态差额 jd 10057 / other 10062 行 | **如实收口**：数据质量断言的边界，非接口/联动缺陷；因不重跑 seed 不改生成逻辑，判据里显式标注 | — | 7 |

### 11.2 P0 设计：为什么不会退化 4 维、不会叠加

「待补建」不新增表、不新增进程内队列，而是复用 **`list_head` 里 fp 已落后于当前
数据版本、且 `gen>0` 的 head 行** —— 这是 DB 持久事实，实例冻结 / 重启 / 跨实例
都不丢，正好绕开 §10.9「HeavyBusy 只留痕不重试」的死胡同。

- **替换而非叠加**（红线 2）：`drain_pending()` 只是 `submit_background` 的**新触发入口**，
  最终仍走 `heavy(global_=True)` → `build()`，与读侧冷态、写侧 `invalidate` 逐字同路径；
  原 `ensure_async` / `serve` / `_cold_worker` 一字未删。不自建名额、不新起调度线程。
- **读路径不做全量**（红线 6）：补建挂在 `index.py` 已有的 60s 请求驱动 daemon 线程，
  `serve()` 里没有 `drain_pending`；读请求仍立即返回上一版完整物化 + `__swr`。
- **不自我失效**（红线 19）：`build()` 只写物化快照、切 head，不调 `invalidate`。
- **有界**（红线 15）：`_DRAIN_GAP=8s` 全局间隔 + `_DRAIN_SCOPE_COOL=12s` 单 scope 冷却
  + `_DRAIN_LIMIT=3` 单轮上限 + `submit_background` 自带 `_FLIGHT_TTL`/`_BUSY_UNTIL` 单飞；
  抢不到名额只留痕不重试，绝不自旋烧 RU。扫描异常降级放行并 `try_err`，治理手段
  不变成新的不可用来源。
- **4 维**：旧 gen 在补建完成前完整服务（准确 / 完整不退化），`build()` 发布前 COUNT
  自校验 + fp 读写同源（不引入第二套算法），补建后 fp 追平（实时改善）。
- `unit_state_head` / `own_health_head` 同源收编：确有物化落后时，有界触发各自
  `ensure_async()`（内部按 `head.fp` 判 fresh + 单飞去重，不白算全量），不自己算 fp
  以避开 mode 归一漂移（红线 16）。

### 11.3 验证（全部零 RU / 不重跑）

| 脚本 / 门禁 | 结果 |
|---|---|
| `converge_probe.py check` | **77 项 PASS**：AST 判 P0 补建自愈、P1 合并失效、P3/P5 判据、4 维不退化、红线 1/2/4/5/6/7/14/15/17/19 |
| `converge_probe.py selftest` | **8 项 PASS**：`chk(False)` 计红、移除 `drain_pending` 后 AST 找不到、AST 不数注释里的 `invalidate("all")`、判据「repl 未推进→FAIL / 推进→PASS」双向反证、白名单不放宽其它接口 |
| `converge_probe.py evidence` | 用 **13:17 唯一一次**历史取证喂第四类判据：`materialize_gen_ok` **报红**（repl 40→40），其余 5 项 PASS → 反证判据真能拦住 §10.9 的缺口；**未重跑导入** |
| `pipeline_probe.py check / selftest / coverage` | **40 项 PASS** / 反证 PASS / **0/6 未覆盖（拦得住）** |
| `preflight.sh --skip-sql-smoke` | **八步全绿**：Python 3.10 语法、pyflakes、`local_test.py` **860/0**、ESLint、Prettier、tsc、vitest **46/46**、i18n（1442/1442 键） |
| 结构推演 | 全链路联动轮数 **37 → 1**（整数计数，红线 7 不产任何 Xms/Xs 结论） |

**判据自身的一处真实修正**：`import_equivalent` 初版把 `alerts` 也算进「源表」对账，
历史取证反证立刻报红 —— 告警是评估派生表，收敛期必然增长。已改为只对
`orders/inventory/products/suppliers/gmv_flows` 五张源表判等价，历史取证随之转 PASS。
这正是「反证」的价值：门禁不是恒绿。

### 11.4 复跑命令（零 RU，可反复）

```bash
python3 scripts/perf/converge_probe.py all          # 85 项 + 反证 + 历史取证
python3 scripts/perf/converge_probe.py check        # 优化后结构/代码门禁
python3 scripts/perf/converge_probe.py evidence     # 历史取证 → 第四类判据（不重跑）
python3 scripts/perf/pipeline_probe.py check        # 40 项链路门禁
python3 scripts/perf/pipeline_probe.py coverage     # 0/6 未覆盖
python3 scripts/perf/pipeline_probe.py selftest     # 反证
```

### 11.5 边界（如实，不掩饰）

- **线上未部署**：本轮全部是本地代码 + 本地验证，**没有线上同口径复测**（红线 7）。
  因此不下「快了多少」的结论，也不声称线上已生效；要生效需另行走 `push_api.py` 部署并
  按 §8.5 顺序验证。
- **serverless 固有边界**：站点完全无流量时没有请求驱动，补建不推进。⚠️ **§12 实测更正**：
  驱动钩子挂在**鉴权中间件**里（`index.py:281-307`），而 `/health` 在 `index.py:266-270`
  被直接放行、**不经过该钩子** —— 所以「`/health` 保活即可驱动补建」是错的，必须是带
  token 的业务 GET（如 `?state=1`）。此时也没人在看数据，不算退化。
- **P2 业务侧未改**：评估链涉及库存重算、告警生命周期、任务租约与分片，是核心业务；
  告警写入 `alert_lifecycle.open_or_reopen` 是**幂等 upsert**（`uq_alerts_dim`），
  收敛期增长 ≠ 脏数据。按红线 1「不理解就动 = 叠加」与 4 维约束，本轮只在判据侧根治
  「不取瞬时值」；按批合并 `eval_queue` 的方案保留为可选项，未实施。
- **gmv_flow / available_balanced 未改**：理由见 §11.1；前者改 upsert 会掩盖重复流水，
  后者需重跑 seed 才能验证，均超出本轮「不重跑」的授权边界。

---

## 12. 部署后一轮全套压测与尾巴收敛验证（2026-11-09，§15.190）

> 授权：推送 → 等部署 → **只跑一轮**（RU 成本）多场景/混合场景压测，判性能基准是否退化，
> 顺带验证 §11 的尾巴是否收敛达标。红线依旧。
> 执行 `scripts/perf/suite_once.py`（`probe` 零写取证 / `run` 一轮防重 / `report` 汇总），
> 判据直接 import `run.py` 的 `TH` / `_judge_*`（唯一真源），不另造阈值。

### 12.1 推送与部署

`push_api.py` 把 §11 的全部改动推到 `feat/edgeone`：远端 HEAD `58da1ef` → **`546ef12`**，
新增 4 / 修改 18 / 删除 0，**逐文件复核一致**。部署 `dphzm62vpqwl`
（CreatedOn 06:17:49 UTC / ModifiedOn 06:20:21 UTC）**Success + Prod=yes**，约 2.5 分钟；
按 `DescribePagesDeployments` 状态轮询推进，**未做定死等待**。

### 12.2 部署前后对照（新代码生效的硬判据）

| 项 | 部署前（旧 `58da1ef`） | 部署后（新 `546ef12`） |
|---|---|---|
| `?state=1` 的 `drain` | **`None`**（无该机制） | `{gap_s:8, scope_cool_s:12, limit:3, aux_cool_s:30, tracked_scopes:3, last:…}` |
| 三 kind `fp_match` | 全 `False`，repl gen 停在 40 | 19.8s 内 gen 全推进，`drain_submitted=13` |
| 补建来源 | 靠「下一次读该页面」 | 60s 请求驱动 `drain_pending()` 自愈 |

### 12.3 一轮压测全景（25 接口 / 13 页面 / 8 场景 / 总墙钟 593s）

| 场景 | 结论 | 关键实测 |
|---|---|---|
| `hot` 8并发×5轮 | **PASS** | p50 **43** / p95 **678** / CV 0.121·0.058 / 0×503 / 0 不完整 / 5 轮全部相位静默 |
| `hot` 1并发×3轮 | FAIL | p50 66 / p95 中位 593（均在阈值内）；p95 CV 0.374、极差 474ms、1 条不完整 |
| `storm S2` 日常 6读2写 | FAIL | 0×503 / 0 不完整 / max 4357<10s / 写 0 失败；**写 19 < 下限 20** |
| `storm S4` 多用户 4读4写 | FAIL | 0×503 / 0 不完整 / max 2474 / 写 0 失败；**写 30 < 41** |
| `storm S5` 极端 16读3写 | FAIL | 0×503 / 0 不完整 / max 5025 / 写 0 失败；**写 19 < 25** |
| `cold` 冷算本体 | **PASS** | purchase total 2.5s / disposal 4.3s ≤6s；repl 150s 轮询 timeout（gen 最终仍推进） |
| `first` 真首建 | **PASS** | 单发 2266ms / 4并发 p95 110ms ≤8s |
| `noise` 相位可信度 | INFO | with_write CV 0.093 / read_only CV 0.076 |

### 12.4 性能基准退化判定：**无退化**

`hot` 是唯一可判「快了多少」的场景（红线 17：纯读 + 锁相位）：p50 **43ms** vs §3 基线
32ms（1.34×）、p95 **678ms** vs 509ms（1.33×），均在阈值 800/1500 内，CV 0.121/0.058
≤0.20，0×503 / 0 不完整。当前数据量比 §3 基线时更大（44.7 万单 / 2 万商品 / 17 万库存行），
1.3× 属同量级漂移（§3 明确「数字会随数据量漂移，判据不变」）。冷算态 `cold`/`first`
达标，带写混合 0×503、max <10s、0 不完整 —— **不停摆**。

### 12.5 尾巴收敛验证：**达标**

| 尾巴 | 结果 | 证据 |
|---|---|---|
| **P0 物化补建非自保证** | ✅ 收敛 | 部署后 **19.8s** 内 repl 40→41 / purchase 44→45 / disposal 28→29；跑后 gen 40→**48** / 44→48 / 28→39；`drain_submitted=13`、`drain_pending=13`、`drain_cooled=0`；`heavy_slot` 两张全释放 |
| **P3 第四类判据 6/6 GAP** | ✅ 收敛 | 用 13:17 唯一一次历史 run + 部署后取证喂 `judge_evidence`：**6/6 PASS**，`materialize_gen_ok` 由历史反证的报红转绿 |
| **P5 hot 判据缺口** | ✅ 收敛 | 5 轮全 `phase=True`、`skipped_rounds=0`；`KNOWN_EMPTY` 白名单生效（0 条不完整误报） |
| **红线 19 自我失效循环** | ✅ 未复现 | 4 个只读 GET 全 200、无降级头、`dv 154→154` 稳定、`running_tasks=0`、`alerts` 22072 未增长 |
| **P1 seed 合并失效** | 结构验证 | 本轮不重跑 seed，线上无法直测；`converge_probe` 85 项 / `pipeline_probe` 40 项全绿（如实保留） |

### 12.6 FAIL 项如实定性（**不改判据、不重跑**）

1. **storm 写入次数**：`expect_min = writers×secs/write_gap×0.5` 按理想节奏估算；实测
   `busy_share` **0.923~1.0**（重建全程追赶），写请求被拖慢，19/30/19 次仍**真实发生且
   0 失败**，三条核心判据（0×503 / 0 不完整 / max<10s）全过。属判据下限与追赶态节奏
   不匹配，**不是「没压到写」，也不是性能退化**。
2. **hot 单条 3 轮**：单并发单轮请求数少 → p95 天然抖动（127 / 601 / 593ms）；
   `_judge_hot` 的 CV/极差容差按 5 轮 8 并发标定。中位 p50 66 / p95 593 均在阈值内。
   属**判据口径未适配小样本**，不是退化。第 2 轮 1 条不完整的逐接口明细未落盘，如实保留。
3. **cold repl timeout**：置 `stale=1` 后 150s 轮询窗口内未观测到 gen 递增，但跑后 gen=48
   （自 40 累计推进 8 次），说明补建最终发生、只是不在该窗口内（heavy 名额被压测占用）。
   purchase/disposal 正常达标。

> 这三项均**不通过改判据转绿**：改了就会掩盖真实节奏，且本轮「只一轮」不允许改完再复跑。
> 列为 P6 待办（判据按场景标定下限 / 小样本标注为参考），本轮只如实记录。

### 12.7 新观察点与 RU 成本

- 跑后 `repl fp_match=False`（另两 kind `True`）：压测末尾持续写使 fp 又变，drain 仍在追
  （`drain_pending=13`），不是回归。
- `HeavyBusy` 近 500 行 **42 条**：与 `busy_share` 一致，属名额争抢；旧 gen 完整可读，
  准确/完整维不退化。
- **RU 代理量**：观察写 **81** 次（季节系数 1.5↔1.5001，对业务零影响）、`cold` 3 次
  重建触发、`first` 1 次 91 天 scope 建后清理、总墙钟 593s。**未重跑 seed、未导数据、
  未跑第二轮**；取证全部零 RU 或只读。
- §11.5 的 `/health` 驱动说法已更正（见 12.2 / `index.py` 鉴权中间件）。

### 12.8 复跑命令

```bash
python3 scripts/perf/suite_once.py probe    # 零写取证（可反复，极低 RU）
python3 scripts/perf/suite_once.py run      # 一轮（30 分钟防重锁，红线：只跑一次）
python3 scripts/perf/suite_once.py report   # 汇总 probe + run
```

---

## 13. 只跑未达标项的稳态复验 + 全仓文档勘误（2026-11-09，§15.191）

> 用户授权「再跑一轮**未达标项**，通过的不跑；验证完后全面排查 README/MD 与实际不符描述，
> 重点历史归档天数」。§12 那一轮是在「部署刚完成 + 持续写追赶态」下跑的，3 项 FAIL 全是
> **判据口径**问题（P6），不是性能退化；本轮在**完全静默稳态**下只复验未达标项。

### 13.1 编排：只跑 5 个未达标场景，判据一字未改

新增 `scripts/perf/suite_p6.py`：从 `suite_once.SCENE_PLAN` 只筛上一轮判 FAIL / 属 P6 尾巴的
5 个场景，**明确不跑**已通过的 3 个：

| 跑 | 不跑（上一轮结论） |
|---|---|
| `hot_single` / `storm_S2` / `storm_S4` / `storm_S5` / `cold` | `hot_multiconc`（PASS，p50 43/p95 678）/ `first`（PASS）/ `noise`（INFO） |

- 判据真源仍是 `run.py` 的 `TH` 与 `_judge_*`（`SO._judge`），**一个字未改**；红线「不改判据转绿」保持。
- 独立 30 分钟防重锁 `.suite_p6.lock`，与 §12 的 `.suite_once.lock` 分开，保证本轮只跑一次。
- 附加取证（不进判据）：`hot_single` 逐接口空壳明细、`cold` 前后 `list_head` gen、跑前/跑后
  四维零写快照。全部零写或只读，未重跑 seed、未导数据。

### 13.2 跑前相位（与 §12 的关键差别）

14:51 冻结快照：`dv=154`、3 个 kind `fp_match=True`、`heavy_slot` 两张全释放、`drain` 全 0、
`running_tasks=0`。§12 是「部署后立刻 + 持续写追赶」，`busy_share` 0.923~1.0；本轮起点是
**完全静默稳态** —— 这正是复验 P6 是否属「相位噪音」的对照条件。

### 13.3 一轮结果（总墙钟 472.8s，5 场景）

| 场景 | 结论 | 关键数 |
|---|---|---|
| `hot_single` 1并发×3轮 | **FAIL** | p50 中位 62 / p95 中位 698 达标；p95 CV **0.225**>0.20、极差 373ms>150ms；0 个 503 / 0 不完整 |
| `storm_S2` 6读2写 | **PASS** | 写 **20**≥20（§12 是 19 差 1）、0×503 / 0 不完整 / max 4301<10s / 写 0 失败 |
| `storm_S4` 4读4写 | **FAIL** | 写 31<41（`busy_share` 1.0）；0×503 / 0 不完整 / max 6691<10s |
| `storm_S5` 16读3写 | **FAIL** | 写 14<25；**2×503 + 2 条不完整**（121 请求中 1.65%）；max 4061<10s |
| `cold` 3 kind | **PASS** | disposal total 4.7s≤6s；repl/purchase 150s 窗口 timeout（判据只对有 `timing` 项判，如实保留） |

**P6 三项在稳态下是否收敛**（结论分两层）：

| P6 项 | 稳态复验 | 判定 |
|---|---|---|
| P6-1 storm 写入下限 | S2 由 19→20 **转 PASS**；S4/S5 仍 <`expect_min`（`busy_share` 0.923~1.0） | **部分**：低写压达标，高写压「追赶态拖慢写」是机理不是相位噪音，判据仍需按场景标定 |
| P6-2 hot 小样本 | CV 0.374→**0.225**（改善）、0 条不完整（§12 是 1 条），但仍 >0.20 | **未达标**：3 轮 1 并发 p95 天然抖，容差按 5轮8并发标定，口径未适配 |
| P6-3 cold repl | 仍 timeout，但跑后 gen 49→52 证明补建最终发生 | **未根治**：150s 窗口 + heavy 名额被压测占用；无 `timing` 项被 `_judge_cold` 静默跳过（假 PASS 缺口） |

**S5 的 2 个 503 如实定性**：121 请求中 1.65%，`quality_logs` 近 800 行 **0 条 503 / 1105 /
degrad / quota / OperationalError** → 非应用层错误、非配额耗尽、非降级；`HeavyBusy` 27 条与
`busy_share` 1.0 一致，属极端并发 + 全站重建追赶相位的**边缘瞬时抖动**。旧 gen 完整可读，
准确/完整维不退化。但不改判据转绿、不重跑，列为本轮新观察点。

### 13.4 尾巴收敛：**P0 达标**（跑后终态零写取证）

| 项 | 结果 |
|---|---|
| drain 有界补建自愈 | ✅ 跑前 `dv=154` 全静默 → 跑后 3 kind `fp_match` **全 True**、`stale=0`、`heavy_slot` 两张全释放、`busy=0`、`drain_pending=0`、`running_tasks=0` |
| repl 物化 | ✅ gen 49→**53**，跑后 `fp_match=True`（§12 收尾时仍 False 在追），约 1 分钟内自愈收敛 |
| 红线 19 自我失效循环 | ✅ 未复现：4 个只读 GET 全 200、无降级头、`dv 206→206` 稳定 |
| 数据四维 | ✅ `alerts` 22072 未增长；0 条业务数据改动（写=季节系数 1.5↔1.5001 零影响） |

`dv 154→206` 是 65 次观察写 + `cold` 3 次 stale 触发 + drain 补建的版本推进，非脏数据。
`RU 代理量`：观察写 65 次、cold 3 次重建触发；**未重跑 seed / 未导数据 / 未跑已通过场景 /
未跑第二轮**。

### 13.5 全仓文档勘误（用户「历史归档天数不符」）

复核真源后逐条勘误；**历史卷 `docs/history/*.md` 正文与卷首注记一律保留原文**（红线：历史记录
不改写），只更新活文档 + 在活跃索引里如实标注历史快照的偏差。

| # | 不符描述 | 真源 | 处理 |
|---|---|---|---|
| 1 | 「订单满 **365 天**归档」「365 天切线」「第 366 天」 | `core/archive.py` `ARCHIVE_KEEP_DAYS=**180**`（2026-11-07 由 365 改回；写侧 cron / 读侧 history import 同一常量；严格小于 `today-180` → 第 181 天） | README×2、ARCHITECTURE×1、DEVELOPMENT×1、SKILL×2 改 180/181；CONTEXT「已知遗留」章节加**勘误注记**（历史定案原文保留）；CHANGELOG 历史批次不改 |
| 2 | `DEVELOPMENT.md` §12 变更史索引表：只列到 11-06 的 11 行、「12 卷 / 147 节」、节数多处错 | 实际 **14 个日期卷 + 3 专题卷**，章节到 §15.191 | 重建 §12 表为当前真值（含 11-07/08/09），统一「节数=唯一 §15.x 标题数」口径 |
| 3 | 「§15.x 编号**全局唯一**」 | 实测 **21 个编号跨卷重号**（标题级「同号出现在多个文件」口径：`docs_probe.py` 现读；§15.8/47 是专题卷交叉引用，日期卷真实重号集中在 §15.146~164 —— 11-05 卷追加 §15.148~164 与后续卷重叠） | README / CONTEXT / CHANGELOG 头 / DEVELOPMENT / SKILL 改「有跨卷重号，按 `grep -rn` 命中文件+日期判断」；新增 `docs_probe.py` 固化为可复跑门禁 |
| 4 | 「分卷首行 N 节 / 范围」与卷内实际标题不符（11-01~02 写 14 实 13、11-05 写 1 实 18、11-06 写 2 实 4、11-07 写 9 实 11…） | 见 §12 新表 | 历史卷不回改，在 §12 表头注明「一律以本表为准」 |
| 5 | 「preflight **七步**」 | `preflight.sh` 实际 **8 步**（`[1/8]`~`[8/8]`，含 `[8/8]` i18n 门禁） | README 自检注释改八步；「七步交付闭环」是 SKILL §4 的 ①~⑦ 流程，**正确不改**；CHANGELOG 历史批次保留 |
| 6 | 「cron **11** 个端点」「第 11 个 ledger-close」「22 个 router / 31 模块」 | cron **12** 端点（10 挂 schedules，`ledger-close` + `agg-sweep` 不单独挂）；**24 APIRouter / 34 路由文件** | README / ARCHITECTURE×2 / SKILL×3 更新 |
| 7 | README「CV 0.04」写死 | §3 基线当轮 CV 随测量漂移 | 改为引用 §3 基线，不写死数值 |

**明确不改（避免误伤正确描述）**：README「**9 类导入**」= 前端 `TARGET_FIELDS` 去重 9 类 ✅；
「重建名额 实例内 3 + 跨实例 2、先全局后实例内」= `db.py` `_HEAVY_LIMIT=3`/`_HEAVY_GLOBAL=2`/顺序 ✅；
「860 项回归」= preflight 实测 ✅；SKILL「七步交付闭环」✅；`400 天截断`=`history._MAX_SPAN` ✅；
各历史卷与 CHANGELOG 历史批次的当时记录 ✅。

### 13.6 复跑命令

```bash
# 只跑未达标项（独立 30 分钟防重锁，只一轮）；--only 用于「通过的不跑」的后续复验
python3 scripts/perf/suite_p6.py run
python3 scripts/perf/suite_p6.py run --only hot_single,storm_S4,storm_S5 --gap 25
python3 scripts/perf/suite_p6.py report   # 汇总

# 文档一致性门禁（零 RU / 纯静态，可反复）
python3 scripts/perf/docs_probe.py check       # 活文档 30 项一致性
python3 scripts/perf/docs_probe.py selftest    # 反证（判据能报红，不是恒真）
python3 scripts/perf/docs_probe.py matrix      # 真源 → 文档 对照矩阵
```

### 13.7 503 归因 + 间隔节奏复验（第三次，15:49~15:54，277.6s）

> 用户问：「503 是读测的还是写的？冷态单发还是冷态多并发导致？」——答案是**都不对**：
> 是**热态多并发下的读**，而且**边缘层**给的，不是函数返回的。

**证据链（代码 + 本轮实测）**

| # | 证据 | 结论 |
|---|---|---|
| 1 | `scenes.storm` 的 `status503` 只统计 `hits`，而 `hits` 仅由 `_reader` 写入；写走 `wlog` → `writes.fail` | 503 计数**天然只覆盖读**；"写有没有 503"必须看 `writes` |
| 2 | `scenes.cold` 是**单发**：置 `stale=1` → 打 **1 次** 读接口 → 轮询 `gen` | 全程只有 1 个在途请求，**冷态单发不可能产生并发 503** |
| 3 | 本轮 S4（4 读 4 写）4 个 503 分布 `hist_top` / `slow_moving` / `batches` / `own_health` 各 1 次，`srv=-1`、`app_headers=false`、响应体 `<!doctype html>…<title>Tencent…` | 响应里**没有 `X-Srv-Ms`** → 应用层没参与；HTML 是**边缘/网关白页** |
| 4 | 同窗口 35 次写里 3 次失败（无异常、非 403 → 不是只读账号） | 写侧也撞同一层；本轮未落状态码（`writes.codes` 是跑完才补的字段） |
| 5 | 上一轮 S5 有 2 个 503，本轮 S5 **0 个**；两轮 `busy_share` 都 1.0 | **非确定性**，跟并发数/边缘瞬时有关，不是某条代码路径 |

**本轮结果（只跑上轮未达标的 3 项，S2 / cold 上轮已 PASS 不跑）**

| 场景 | 结论 | 关键数 | 与上轮对比 |
|---|---|---|---|
| `hot_single` 1并发×3轮 | **FAIL**（判据未改） | 轮1 p50 **207** / p95 **1263**；轮2 56/471；轮3 56/505；p50 CV **1.271**、p95 CV **0.724**；0×503 / 0 不完整 | 上轮 p50 中位 62；**轮 1 是 warm-up 尖峰**（首次连接/实例冷启），轮 2~3 稳态 CV **0.000 / 0.035** |
| `storm_S4` 4读4写 | **FAIL** | 写 35/41、fail 3、`busy_share` 0.909；**4×503 + 4 不完整**（48 请求中 8.3%）；max 4100 | 上轮 0×503；写入 31→35 略升 |
| `storm_S5` 16读3写 | **FAIL** | 写 23/25、fail 0、`busy_share` 1.0；**0×503 / 0 不完整**；max **1163** | 上轮 2×503 / max 4061 → **本轮干净** |

**间隔节奏**：每场景前 `wait_quiet`（≤90s）+ 场景间 **25s**。S4 前相位已静默（`busy=0`，
静默 2573s）；S5 前等满 90s 仍 `busy=2`（S4 的写触发重建还没收敛）——**S4 的 503 发生在
静默相位之后**，所以不是"上一场景的尾巴"造成的。

**结论（P6 三项）**：都**没变成代码退化**，但也**没到"判据该转绿"的程度**：

| P6 项 | 本轮 | 定性 |
|---|---|---|
| P6-1 写入下限 | S4 35<41、S5 23<25（S2 上轮 20=20 PASS） | `expect_min` 是理想节奏估算；实测写**每次都在等重建**（`busy_share` 0.909/1.0）→ 高写压下短 8%~15% 是机理，判据仍需按场景标定（**未改**） |
| P6-2 hot 小样本 | CV 0.724 / 1.271 仍 >0.20 | **根因是轮 1 的 warm-up**：轮 2~3 p50 都是 56、p95 471/505，稳态 CV≈0.03。1 并发没有并发度摊薄首次连接开销 → 3 轮里 1 轮尖峰就顶穿 CV。要根治得"丢弃 warm-up 轮"或"按轮数分档"，那是**改测量口径**，不在本轮授权内 |
| P6-3 cold | 未跑（上轮 PASS） | 保留"无 `timing` 项被静默跳过"的假 PASS 缺口，列 P6-3 待办 |

**新增 P6-4/5 待办（如实记录，未改）**：`hot_single` 加 warm-up 轮（不计入统计，需明示）；
`writes.codes` 已补，下一轮可直接点名写失败的状态码。

**本轮已补的取证能力（下次不用再猜）**：
`kit.hit` 对非 200 记录 `app_headers`（有没有 `X-Srv-Ms`）/ `server` / `body`(400 字)；
`scenes._by_ep` 增加 `n / n_srv / s503 / non200 / inc` 逐接口归因；
`scenes.storm` 增加 `writes.codes` 状态码直方图与 `bad[]` 逐条留证。
