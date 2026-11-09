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
