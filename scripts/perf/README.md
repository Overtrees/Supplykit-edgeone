# scripts/perf — 性能基线与压测

一套**可复跑**的线上性能测量工具。目的不是"再多测几次"，而是把
「什么场景能判什么结论」固化成代码，避免每次靠翻历史记录+单轮采样下判断。

> 背景：2026-11-08 曾用两轮"带写压测"的 p50 判定某改动"提升了 3 倍"，复核发现
> **同配置同脚本 5 轮 p50 极差 22 倍**（2,100 / 296 / 112 / 95 / 214）。原因是写操作每
> 1.2~1.5s 重开一次重建窗口，p50 在"撞窗口 2s / 不撞 0.1s"两个峰之间跳。
> 详见 `docs/PERF.md` §1 与 `docs/DEVELOPMENT.md` 红线 17。

## 目录

| 文件 | 作用 |
|---|---|
| `endpoints.py` | **页面 → 模块 → 接口 → 数据来源 → 机制**的唯一真源；`--list-md` 生成 `docs/PERF.md` 的表格 |
| `kit.py` | 登录态会话、`X-Srv-Ms` 读取、百分位、相位查询（`heavy_slot` + `built_at`）、真实写操作 |
| `scenes.py` | 五类场景：`hot` / `storm` / `cold` / `first` / `noise` |
| `run.py` | CLI + **判据（阈值只在这里）** |
| `seed_preflight.py` | **seed 一次性作业**的开跑门禁（零 RU 静态检查）/ 只读 DB 前置 / 跑完取证；判据独立，不进 `run.py` 的 `TH`。见 `docs/PERF.md` §9 |
| `pipeline_probe.py` | **零 RU** 全链路结构/代码验证：`check` 链路节点与机制常量、`coverage` 基准覆盖矩阵、`findings` 优化点→业务映射、`simulate` 集中导入结构推演、`predict` 100 万单复核、`selftest` 反证。见 `docs/PERF.md` §10 |
| `import_smoke.py` | **线上小批量拟真冒烟**：走前端导入链路（同步 + 异步），CSV 按 `spec()` 中文表头从线上真实行取样原样回导（零业务数据变化）；`plan` 零写 / `run` 只跑一次 / `verify` 只读取证 + **第四类作业/联动判据** `judge_evidence`（纯函数，可喂历史 json 反证）。见 `docs/PERF.md` §10.7 / §11 |
| `converge_probe.py` | **优化后结构/代码验证**（§15.189）：零 RU、不重跑 seed、不连线上；`check` 用 AST 判 P0 补建自愈 / P1 合并失效 / 4 维不退化 / 红线合规，`selftest` 反证，`evidence` 把 13:17 历史取喂进第四类判据（应报红 `materialize_gen_ok`）。见 `docs/PERF.md` §11 |
| `suite_once.py` | **部署后一轮全套压测编排**（§15.190）：`probe` 零写取证（等 `drain_pending` 收敛 + 四维/联动快照 + 第四类判据）/ `run` 一轮 8 场景（hot 多并发+单条、storm S2/S4/S5、cold、first、noise；30 分钟防重锁）/ `report` 汇总；判据直接 import `run.py` 的 `TH`。见 `docs/PERF.md` §12 |
| `suite_p6.py` | **只跑未达标项的稳态复验**（§15.191/§13.7）：从 `suite_once.SCENE_PLAN` 筛场景，`--only a,b,c` 精确指定（**通过项不跑**），默认 5 个 P6 项；每场景前 `wait_quiet(≤90s)`、场景间 `--gap` 秒，让上一场景的写触发重建收敛；判据真源仍是 `run.py TH`，**一字未改、不改判据转绿**；独立 30 分钟防重锁（只一轮）；额外落 hot 逐接口空壳明细、cold 前后 `list_head` gen、跑前/跑后零写四维快照、**503 逐条归因**（`app_headers`/`body`/`writes.codes`）。见 `docs/PERF.md` §13 |
| `docs_probe.py` | **文档一致性门禁**（§15.191，零 RU 纯静态）：从代码/配置现读真源（`ARCHIVE_KEEP_DAYS` / preflight 步数 / cron 端点 / 路由数 / 分卷数 / §15.x 重号数），核对活文档 `check` 30 项、`selftest` 反证、`matrix` 真源→文档对照；历史卷与 CHANGELOG 历史批次按红线不检查。见 `docs/PERF.md` §13.5 |
| `out/` | 每次运行的 json（不入库） |

## 用法

```bash
cd <repo>

# 0) 文档里的页面表（改了 endpoints.py 后重跑，粘回 docs/PERF.md）
python3 scripts/perf/run.py --list-md

# 1) 热态：唯一可判"快了多少"的场景（纯读 + 锁相位，实测 CV 0.043）
python3 scripts/perf/run.py hot --rounds 5                 # 全部已登记接口
python3 scripts/perf/run.py hot --pages core --rounds 5    # 12 个历史可比接口
python3 scripts/perf/run.py hot --pages dash,insights-repl # 只测某几页

# 2) 极端/日常：只判"会不会停摆 + 数据是否完整"，**不判延迟**
SUPPLY_USER=admin SUPPLY_PASS=*** python3 scripts/perf/run.py storm --scene S2
SUPPLY_USER=admin SUPPLY_PASS=*** python3 scripts/perf/run.py storm --scene S5

# 3) 冷算本体：读 list_head.timing 分段（需要 .env 里有 DB 凭据）
python3 scripts/perf/run.py cold

# 4) 真首建：未建过的 scope，自带清理
python3 scripts/perf/run.py first --kind repl

# 5) 验证"这轮测量是否可信"（相位对照）
python3 scripts/perf/run.py noise --rounds 3
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SUPPLY_BASE` | `https://supplykit.top` | 目标站点 |
| `SUPPLY_USER` / `SUPPLY_PASS` | `demo` / `demo123` | 访客只读；**写场景必须给管理员**，否则判 FAIL 并提示 |
| `.env`（仓库根） | — | 只有 `cold`/`first`/相位需要（DB 直连）；没有就降级为"未知"，不崩 |

## 场景与判据

| 场景 | 回答什么 | 判据 | 不可用口径 |
|---|---|---|---|
| `hot` | 快了多少 | 5 轮 p50/p95 中位 ≤ 1.5s / 0.8s；**CV ≤ 0.2**（否则此轮不可信）；0 个 503/不完整 | — |
| `storm` | 会不会停摆 | 0 个 503、0 条不完整、max < 10s、**写入次数 ≥ 下限且 0 失败** | **p50/p95 不作判据**（CV 3.6~5.4） |
| `cold` | 重建本体各段 | 单 kind `total` ≤ 6s | 触发请求的 srv 不含重建时间 |
| `first` | 真首建 | 单发 ≤ 8s；4 并发 p95 ≤ 8s | — |
| `noise` | 本轮可信吗 | `read_only` 组 CV 应 ≪ `with_write` 组 | — |

「写入次数 ≥ 下限」这条是踩坑加的：访客账号写入 403 时，若不带这条，纯读压测会报
"0 个 503 → PASS"，看起来通过、实际**根本没压到写**。

## 三个常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| `写入失败 N 次` | 用了 `demo` 只读账号 | `SUPPLY_USER/PASS` 给管理员 |
| `phase.ok=False`（等 60s 仍不静默） | 场景内持续写，窗口刚关又开 | 正常。带写场景**不存在**静默相位，改用 `storm` 只判不停摆 |
| `hot` 的 CV > 0.2 | 期间有别的写/定时任务在跑 | 换时段重跑，或先 `cold` 确认无重建 |

## 冒烟（DB 不可用时也能跑）

```bash
python3 scripts/perf/smoke.py                 # 只读、串行、零写，一轮 ~20s
python3 scripts/perf/smoke.py --base https://xxx
```

> 别和仓库根目录的 `scripts/smoke_check.py` 混淆：那个只验 `health` JSON + 首页 `#root`
> 是否可访问（部署后冒烟）；本脚本逐接口判数据有效性，用于**故障定位**。

## seed 作业门禁（不测速度，只管「能不能只跑一次」）

```bash
python3 scripts/perf/seed_preflight.py             # 零 RU 静态门禁（段表/规格/基准/路径）
python3 scripts/perf/seed_preflight.py --db        # 追加只读 DB 前置（约 8 条 SELECT，<10 RU）
python3 scripts/perf/seed_preflight.py collect --task-id <id>   # 跑完只读取证，落 out/
```

`run.py` 判的是**接口稳态**；seed 是 20 段 / ≈37 窗口的一次性作业，拿 `storm_max<10s`
判必然 FAIL，所以判据单独放这里。**本脚本永不执行导入**，真导入由
`POST /api/seed/fill` + `scripts/seed_drive.py` 单驱动完成，完整规范见 `docs/PERF.md` §9。

与 `run.py` 的区别：`run.py` 测「快不快 / 停不停摆」，需要 DB 正常、多轮、带判据；
`smoke.py` 测「正不正常」，只发一轮 GET，**额度耗尽时用来判定失败面**。
接口清单同样来自 `endpoints.py`。

用 `demo/demo123` 登录（内置硬编码不查库）：admin 登录在 DB 挂掉时拿不到 token，
看到的全是级联 401，接口本身什么形态根本测不到。

输出五类：`A:可用` / `C:DB失败` / **`E:静默空壳`**（HTTP 200 但业务数据全 0，
前端显示「暂无数据」——最危险）/ `5xx` / `401`。
2026-11-09 额度耗尽实测 `A 3 / C 20 / E 4 / 5xx 1`。
完整处置见 [`docs/PERF.md` §8](../../docs/PERF.md)。

## 与文档的关系

- `docs/PERF.md`：**人读的基准手册**（按页面 → 模块，含基线数字与机制）
- 本目录：**机器执行的判据**（数字可漂移，判据不变）

两者的一致性由 `endpoints.py` 保证 —— 文档表格由 `--list-md` 生成，不手抄。
