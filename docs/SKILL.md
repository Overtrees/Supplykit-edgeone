---
name: supplykit
description: "SupplyKit 开发手册(EdgeOne Makers + TiDB 原生)。含六条业务口径、22 个路由的架构事实、Makers 框架坑位、七步交付闭环(preflight → 回归 → push_api → 部署判据 → 冒烟)、缓存/切线与 schema 版本铁律、UI 规范要点、失败模式黑名单。当用户提到 SupplyKit、补货建议、采购建议、数据清洗、规则引擎、看板、BBCC/传统补货、滞销、进销存、告警、GMV、TiDB、EdgeOne Makers、supplykit.top 时触发。"
---

# SupplyKit 开发手册

> 这是**入口索引**, 不是全量文档。每条只给判据、命令、禁止项; 根因与实测数据在
> `docs/DEVELOPMENT.md §15.x`, 改动前按第 8 节索引精读对应章节。
> 冲突时以**代码 + 用户最新指令**为准, 本文与 `CONTEXT.md` 同步维护。

## 0. 一屏速览

| 项 | 值 |
|---|---|
| 本地根 | `/workspace/supplykit` (`frontend/` + `cloud-functions/api/`) |
| 仓库 / 分支 | `Overtrees/Supplykit-edgeone` / **feat/edgeone** |
| 线上 | `https://supplykit.top` (**免签名公开访问**, admin/admin123 写, demo/demo123 只读) |
| 平台 | EdgeOne Makers, ProjectId `makers-8gstkvheqm2c`(名 supplykit, Area=overseas 免备案) |
| 部署 | **Provider=Github → 只有 push 能触发构建**, `makers deploy` 被拒; 约 2~2.5 分钟 |
| 数据库 | TiDB Cloud Serverless, `.env` 里 `TIDB_*` + `DB_BACKEND=tidb` + `JWT_SECRET` + `CRON_SECRET` |
| 规模 | 23 个 APIRouter / 33 个路由文件 / 16 页面 / 13 组件 + 11 锤子面板 / 7 迁移步骤 |
| 性能基线与判据 | `docs/PERF.md`（页面→模块）+ `scripts/perf/run.py`（hot/storm/cold/first/noise） |
| 回归 | `local_test.py` **681 通过 / 0 失败**;加 `--sql-smoke` 顺带做真实 SQL 冒烟(435 条唯一 SQL / 290 条 EXPLAIN + schema 对账) |

```bash
cd /workspace/supplykit
python3 scripts/env_check.py                  # 换机/新会话第一条: 缺凭据一次性列全
bash scripts/preflight.sh                     # 八步门禁, 全绿才算可推(有 TIDB 凭据时含 SQL 冒烟)
cd cloud-functions/api && python3 local_test.py   # 单跑回归(注意目录!)
cd cloud-functions/api && python3 local_test.py --sql-smoke  # 回归 + 真实 SQL 冒烟(需 TIDB_*)
python3 scripts/push_api.py -m "feat(x): ..."     # 推送(= 触发部署)
python3 scripts/deploy_status.py                  # 部署状态(最近 3 次)
python3 scripts/smoke_check.py                    # 线上冒烟(health JSON + 首页 root + 业务接口)
```

---

## 1. 业务口径 → 唯一真源 `CONTEXT.md`

**七条口径（含期初两场景）只在 [`CONTEXT.md`](../CONTEXT.md) 维护一处**，本手册不再抄第二份
（抄一份就多一处漂移：2026-11-06 之前两边已经出现 6 vs 7 条的差异）。

这里只留三条**改动时最容易踩**的，其余全部去 CONTEXT：

1. **告警关闭必须经 `core.alert_lifecycle.close()` 并带 reason**；重开复用同维度行（`reopen_count`），
   维度键 5 维 `alert_type + 渠道 + SKU + 69码 + 仓`，与库存四维对账同构。
2. **金额口径分两套**：看板 GMV = 已支付；历史归档金额 = 订单原价全状态（含未支付/退款）。
   历史页挂着「非 GMV 口径」胶囊，**不要统一成一个数**。
3. **前端所有写操作走 `api/mutate.ts`**（提示 / 缓存失效 / 联动事件 / 操作态一次声明）。
   `syncing` 只表示"前端在取数"，后端在算/在导用 `ops` —— `syncing` 有 20s 强制归零，
   长任务会被显示成已完成。`App` 顶层在 `ToastProvider` 之外，那里 `useToast()` 是 no-op，必须用 `notify.*`。

---

## 2. 架构事实

```
cloud-functions/api/
  index.py          唯一入口: 行首 app = FastAPI(); 鉴权中间件; /health
  db.py             原生数据层: pymysql DictCursor + 连接池(BoundedSemaphore 名额)
  schema_create.py  表/索引声明 + SCHEMA_VERSION 门禁(热路径零 DDL)
  migrations/       有序迁移框架: steps/*.py 单步事务 + schema_migrations 记账(只跑未应用的)
  biz/sales.py      三窗口日销(3σ 剔除非零日 + 近 3 天 1.5 倍加权 + 稀疏窗口特判)
  routes/common.py  @traced(异常统一 200 + ok:false) / try_err / 日志落库
  routes/*.py       23 个 APIRouter: auth dashboard orders products inventory replenishment ledger
                    purchase alerts insights history cleansing channels rules suppliers tasks
                    batches ops misc debug cron task_seed task_export
  local_test.py     回归; test_audit.py 静态审计(CI 也跑)
```

**鉴权**: 只认 `X-Auth-Token`(网关把 `Authorization` 改写成 `q-sign`, 用 Bearer 会登录成功但全接口 401)。
`auth` / `health` / `debug` / `insights/ping` / `cron` / `logs/frontend` 免鉴权; demo 用户写操作返回 403。

**cron**: `routes/cron.py` 11 端点, 由 `edgeone.json` 的 `schedules` 驱动(**仅 10 条, maxItems 上限**:
archive 01:05 / cleanup 03:05 / freshness 03:10 / snapshot 03:35 / daily-rules 04:05 / reconcile 04:10 /
recycle 04:35 / continue-tasks 05:05 / push-alerts 08:30 / ping 13:55) —— 第 11 个端点
`/cron/ledger-close`(账期月结)**不单独挂调度**, 嵌入 `cleanup-logs` 内调用(见 `docs/decisions/0002`)。
**新增定时任务前必须先腾条数**: 加到 11 条会导致整组 schedules 路由丢失。
平台 schedules **无法注入 header**(schema 只有 name/cron/timezone/method/path/payload, 且 `edgeone.json`
进公开仓库, 用 payload 传密钥等于公开) → **"鉴权"根治不了**, 所以守的是"危害":
① `_throttle` 进程内限频 ② `_claim` DB 当日抢占(多实例安全) ③ **时间窗守卫** ——
9 个重任务只在各自调度窗口内有效, 窗口外**零 SQL** 直接 skipped ④ 失败 `_unclaim` 回滚。
时间窗按 `Asia/Shanghai` 显式计算(容器在新加坡), 边界来自实测: 平台触发稳定**早 57~60 秒**,
所以窗口两端各留 ≥1.5 小时。`ping`/`continue_tasks` 不设窗(后者被请求驱动直接调用, 设窗会打死续跑)。
外部调用者的能力上限 = 在窗口内把任务提前几分钟触发一次, `_claim` 保证当日仍只跑一次。

**前端**: React + Vite, `frontend/src/pages/*.tsx` 16 页; 筛选/区间控件集中在
`components/hammer/*.tsx`(锤子面板); `.menu-btn` 是侧边栏汉堡, 别和锤子混。

---

## 3. Makers 框架坑位(血泪, 逐条都要记)

| # | 坑 | 判据 |
|---|---|---|
| 1 | **路由没有 `/api` 前缀** | 框架按 `root_path=/api` 剥 path; 代码里写 `/api/x` 在生产 404, 本地 uvicorn 反而通 |
| 2 | **入口必须行首 `app =`** | 构建器按 `/^app\s*=/m` 找; 缩进即"函数未构建", 线上表现为边缘 404 / health 返回 SPA HTML |
| 3 | **`sys.path` 只有函数根** | 同目录 `db.py`/`routes/*` 必须 `sys.path.insert(0, os.path.dirname(__file__))`, 否则 ModuleNotFoundError 被表现成 plain "Not Found" |
| 4 | **文件系统只读** | 模块级 `mkdir` 要可写探测 + 回退 `/tmp` |
| 5 | **标量参数默认读 query** | POST 的 JSON body 必须 `await request.json()` 手动解析 |
| 6 | **TiDB 返回 `date`/`datetime` 对象** | 与字符串比较/拼接前 `str(x)[:10]` |
| 7 | **500 会变成"函数崩溃"HTML 页** | 业务异常统一 **200 + `{ok:false, error, detail}`**; 堆栈只写 `quality_logs`, 不回浏览器 |
| 8 | **env 是部署时快照** | 改 env 必须重新部署才生效; `JWT_SECRET` 必须固定值(否则多实例互相踢) |
| 9 | **TiDB DDL 是异步的** | DROP 后立刻 CREATE INDEX 会 `Duplicate key name` → 索引一律并入 `CREATE TABLE` 声明 |
| 10 | **本地 uvicorn 不剥 `/api`** | 本地起后端要套 ASGI 中间件改 `scope["path"]`; 否则前端代理全 404 |

**预览域名要签名**(3h 会话): `supplykit-8rgbtjfx.edgeone.dev` 需 `DescribePagesEncipherToken`
(Text 传**域名**, 不是 ProjectId) 换 cookie。**生产域名 supplykit.top 免签**, 直接 curl。

---

## 4. 七步交付闭环

### ① 改前(缺一步都会返工)

- `git status --short` 干净; 读 `DEVELOPMENT.md §15.8` 十条数据原则 + 同日 `CHANGELOG` 条目;
- **先把验收指标写成数字**("四维偏差 0 项" / "日均从 71.16 万降到 16.01 万"), 否则无法判断改完;
- 涉及口径改动: 先确认六条口径哪条被触碰。

### ② 本地验证(preflight 八步)

```bash
bash scripts/preflight.sh
# [1/8] 后端 Python 3.10 语法   [2/8] pyflakes(undefined name / 重定义)
# [3/8] local_test 回归 681 (+SQL 冒烟)  [4/8] ESLint(含红线 C 禁 any)
# [5/8] Prettier  [6/8] tsc  [7/8] vitest 单测
# [8/8] i18n 门禁(红线 B: 中文硬编码为 0 + zh/en 键集相等 + 无死引用)
```

**规模 mock 不能省**: 单 SKU 样本会让批次/分摊/分组类缺陷全过(每次只走一条分支), 至少 ≥200 SKU 跑一轮。

**逻辑调试优先本地直连**, 别靠部署试:
```bash
python3 scripts/local_api.py GET  /health
python3 scripts/local_api.py POST /seed/step '{"from_step":7}'
```
(进程内 TestClient, 连 `.env` 的库; 冷加载约 40s, 之后毫秒级。云函数 wall clock 120s 硬限。)

### ③ 提交

`fix|feat|perf(scope): 主题`; body 写 **根因 → 修法 → 实测数字**。绝不提交 `.env` / `.edgeone/`。

### ④ 推送

`github.com:443` 在本沙箱**不可达**(131s TLS 断开, 重试无意义), 只能:
```bash
python3 scripts/push_api.py -m "fix(x): ..."       # Git Data API, 一次 = 一个 squash 提交
python3 scripts/push_api.py -m "..." --dry-run     # 先看差异文件
```
代价: **本地 SHA ≠ 远端 SHA**(author/committer 时间戳不同), 这是预期, 不是失败。

### ⑤ 部署判据

| 推送方式 | 判据 |
|---|---|
| 若走 `git push`(本沙箱通常不行) | `Commit == 本地 HEAD` |
| **走 `push_api.py`(实际路径)** | **远端 HEAD 与本地 HEAD 逐文件一致**(脚本结束自检打印) |

外加 `Status=Success` + `Prod=yes`。**部署成功 ≠ 功能生效**。

### ⑥ 线上验证

`smoke_check.py`(health 必须 JSON 且 status=ok —— 变 HTML 就是路由丢了) → 逐接口实测返回结构/新字段 →
写路径改动查 `quality_logs` 最近 2 小时。**没数据触发的分支必须明说"当前无数据触发", 不能声称已验证。**

### ⑦ 落文档

`docs/DEVELOPMENT.md` 追加 §15.1xx(根因 + 实测数据 + 踩坑), 同步 `CHANGELOG.md` 索引与相关数字。
文档里的数字(回归数/路由数/页面数)是**门禁的一部分**, 改了必须全库同步。

---

## 5. 数据与缓存铁律

- **键要含全部维度**: 业务键四维 `(channel, order_no, sku, barcode)`; 库存 `(仓型, SKU, 69码, 仓)`;
  缺一维就是"同单同 SKU 换码被 1062 降级 → 少算"这类静默错账
- **加会改变结果的入参, 必须同步改缓存 key**: `yoy` 加 `start` 后 key 补 `_s`; 否则同 `end` 不同起点
  共用结果, **只在命中缓存时复现**, 本地几乎抓不到
- **缓存按域失效**: 写入口用 `invalidate("<域>")`, `analysis_cache._FAMILY_DEPS` 反推要清的族。
  未登记域名 → 全清(兜底); 失效是**打墓碑**(`created_at=1970` + `tok`), 防"重建期间被失效 → 旧值覆盖"
- **归档切线 365 天严格小于**: cron 判定 `< today-365`, 所以 t 日订单第 366 天才搬。
  `history._CUT_DAYS` 与 archive cutoff **必须同步改**, 两表互斥是不变量
- **改 schema 必 bump `SCHEMA_VERSION`**, 否则存量实例不执行新声明(加表/索引 = 改声明 + bump, 不写迁移)
- **增量重建按业务日期**: 数据入口是文件导入(可横跨任意日期), 重建范围只能由 `ordered_at` 决定,
  不能由导入时间推导。删单后日销孤儿行按"日销表 SKU − 快照窗口 − 订单窗口"差集清
- **事务/幂等**: archive 失败**刻意不回滚**(逐条累加非幂等, 重跑会把金额算两遍); 其余可回滚

---

## 6. UI 规范要点(动手前读 `docs/UI_DESIGN.md`)

- **卡片内边距分档**: 详情卡 `14px 16px` / 密集列表 `12px 14px` / 弹窗控件 `9px 12px` + `min-height:40px`
- **底部 16px 几何恒等**: 禁差异化 margin、禁负 margin 下沉、禁少行放大字号(会截断)
- **动效只有 opacity**: `transform` 破坏 `fixed` 与安全区; 弹窗一律 `createPortal`, zIndex 9998/9999
- **玻璃三档差异化**(light/dark 不同参数, 否则 250% 饱和残留): `--blur-thin` / `--blur-regular` / `--glass-filter`
- **数字降维是换单位不是缩字号**: `fmtBig` 金额 2 位小数; `fmtInt` 计数窄屏(≤932px)≥1 万、宽屏 ≥10 万
  降整数万, 否则普通数字(**不加千分位逗号**)
- **页面只管"数据长什么样", 锤子菜单只管"查什么 + 口径"** —— 两侧零重复, 任何提示只出现一处
- **死 CSS 随手删**: 控件迁走后残留样式会让后人以为还有一套自绘控件

---

## 7. 失败模式黑名单(见过就不该再见)

| 别做 | 为什么 |
|---|---|
| 前端兜底掩盖口径问题 | 用户明确要求"彻底根治"; 兜底会把"库里有 vs 算错了"两种故障混成一种 |
| 从截断样本推断全量 | `ORDER BY id DESC LIMIT 300` 里 id 小的行被整体挤出 → 曾误判"库里没有这些行" |
| 前端自己算时间窗口 | 后端一截断(>400 天)标题就显示假窗口; 一律取接口 `cur.start/end` |
| 同页并存两个时间口径 | 90 天汇总 vs 写死 30 天同比, 用户会拿去直接比 |
| 写死"满 365 天归档" | cron 是严格小于, 差一天; 用后端 `archive_day` |
| `except: pass` | 静默吞异常 = 事故源; 一律 `try_err(src, what, exc)`(10s 同源限频) |
| `t = Date.now()` 全局替换 | 只替换**出现在 URL 查询串里**的; 日期差/唯一 id/React key 必须原样(误伤过 3 处) |
| 反复大批量导入验证性能 | 单月烧 60M RU 的教训; 用 `ru_diag.sh`(只读 EXPLAIN + GET) + 3~10 行小文件冒烟 |
| 跑 `edgeone makers env ls` | 明文回显 `TIDB_PASSWORD` / `JWT_SECRET`; 要值读本地 `.env` |
| 只信 `git push \| tail -2` 的退出码 | 那是 `tail` 的 0; 失败会被当成功 |
| 两个重灌进程并发 | 批次删除是整段 DELETE, 会互相清表 |
| 引用文档里的**具体实现**而不看代码 | 2026-11-06 前 `DEVELOPMENT.md` 一~十四章是 PA+SQLite 时代写的(`backend/`、WAL、Cloudflare 部署), 已整体重写; 但**任何文档都可能滞后于代码** —— 判据以代码 + ARCHITECTURE.md 为准, 文档只给方向 |
| 往页面/路由文件里继续堆新逻辑 | 上帝文件是这么长出来的; 新逻辑落独立文件, 页面只编排 —— 见 §11 红线 A |
| JSX / toast / placeholder 写中文字面量 | 语言切换立刻露馅; 文案一律 `t('key')` 且 zh/en 双份 —— 见 §11 红线 B |

**重灌顺序(错必出偏差)**: `seed step6 → _recalc_own_inventory → _rebuild_own_batches → best_before 回写`。

---

## 8. 按需精读索引

| 场景 | 先读这份 |
|---|---|
| 任何改动 | `docs/DEVELOPMENT.md §15.8`(数据四维十条) + `§15.9`(生产红线) |
| 接手/换机 | `CONTEXT.md`(口径 + 已知遗留逐条) |
| 改 UI | `docs/UI_DESIGN.md`(卡片分档/动效/数字格式) |
| 改路由/数据流 | `docs/ARCHITECTURE.md` |
| 平台 API / 签名 | `docs/MAKERS_API.md` |
| 密钥与权限边界 | `docs/security.md`、`docs/decisions/0001-x-auth-token.md` |
| 交付顺序与门禁 | `docs/WBY_WORKFLOW.md`(本文第 4 节的展开) |
| 看板/补货/告警口径 | §15.84 / §15.102 / §15.104 / §15.109 |
| 导入与 69 码 | §15.97(补码两道闸) / §15.98(职责边界) / §15.116(四维业务键) |
| 缓存与性能 | §15.117(按域失效) / §15.120(降往返不加缓存) |
| 归档与同比 | §15.113(切线双表) / §15.124(信息层级) / §15.125(同口径) |
| 启动与 schema | §15.122(版本门禁) / §15.123(冗余清理) |
| 定时任务 | §15.101(平台 schedules) / §15.110(cron 护栏) |

---

## 9. 环境红线

- `.env`(运行变量)、`.edgeone/`(token)已 gitignore; 提交前 `git status` 复核
- 凭据分布: `.edgeone/auth.json` 的 `EDGEONE_PAGES_API_TOKEN`(查部署) 与 `GITHUB_TOKEN`(推送, 需
  `repo contents:write`); TiDB 连接串在 `.env`
- **数据库连接只有一份真相源 = EdgeOne 项目环境变量**(Production 档)。CI 不存 TIDB_*,
  启动时由 `scripts/ci_fetch_env.py` 现取(日志显示 `数据库连接来源: edgeone`); 轮换密码只改
  EdgeOne 一处。见 `DEVELOPMENT.md` 15.131
- **密码/secret 永不进 commit message、对话、日志**; 泄露即刻 amend + 改密 + 同步 Makers env 与 GitHub Secrets
- **审计脚本**: `python3 scripts/audit_secrets.py` —— 硬编码 / `.env` 是否入库 / `.gitignore` 覆盖 /
  **git 全历史 blob + commit message + reflog 脱分支提交**。CI 每次推送 + 每周一定时跑, `fetch-depth: 0`。
  输出已脱敏(只留前 4 位), 不会把值打到日志里。**引用审计报告到文档/issue 时要二次脱敏** ——
  公开仓库的文档也算源码, 连样例串都不能留(实测踩过两次, 都是文档自己被判成硬编码)
- 生产写操作前先只读确认影响面, 并记下回滚点(行数 / Σ 金额)
- 日志三面收口: 用户侧 `GET /quality-logs` 分页筛选 + 导出; 开发者侧 `/quality-logs/file(s)` /
  `/debug/list`; 前端 `reportError()`, 后端 `try_err()`, 业务错误统一 `ok:false` → `business_error`

---

## 10. 用户偏好 → 见 `CONTEXT.md`「用户偏好」

交付方式偏好（中文交流、拍板要给推荐、彻底根治不要兜底、实测优先、敢认错）是**同一份**，
两处维护必然漂移，只在 CONTEXT.md 更新。本手册只补充一条工程侧的：

- **文档里的数字是门禁的一部分**：回归数 / 路由数 / 页面数 / 字典键数改了必须全库同步，
  发现过时描述要主动报告，而不是照着过时描述干活。

---

## 11. 四条红线(2026-11-04 定案 + 2026-11-07 增补, 与业务口径同级)

> 这三条不是"建议", 是**准入条件**。违反的改动不予提交, 已有代码顺手改到合规。

### 红线 A: 非内联优先, 不造上帝文件

**新增或调整功能时, 新逻辑默认落独立文件**; 页面/路由只做编排与装配。

| 层 | 放在哪 |
|---|---|
| 后端 | `routes/<域>.py`(HTTP) / `core/<域>.py`(纯逻辑) / `biz/<域>.py`(业务计算) / `migrations/steps/<域>.py`(迁移步骤) |
| 前端 | `pages/<页>/<块>.tsx`(页面私有块) / `components/<域>/<块>.tsx`(跨页复用) |

**触发即拆的信号**(任一命中就动手, 别等"下次"):

- 单文件 `.py` > 600 行 / `.tsx` > 500 行;
- 单函数或单组件 > 80 行, 或需要滚动两屏才能读完;
- **一个文件里出现第二套职责** —— 声明 + 执行 + 数据修补混在一起(典型: 建表声明里塞 10 段互不相关的数据迁移);
- 新增代码让某文件的职责需要"和"字才能描述。

**不许为拆而拆**: 5 行的文件、只被引用一次且无独立语义的包装层, 同样算噪音。按**职责**拆, 不按行数拆。

### 红线 D: 先理解原设计再优化, 禁止叠加(2026-11-07 事故定案)

**动手改性能之前, 必须先读懂这段代码原来的设计意图**: 它为什么串行/为什么加锁/为什么缓存/
注释里记过什么坑。读不懂就先写清意图, 再决定改不改。

**优化 = 替换, 不是叠加。** 同一件事只允许一处真源; 新路径上线时旧路径必须删除,
不许"两套并存兜底"(那叫垃圾堆, 越叠越高, 最后一次改动谁都说不清哪条路径在生效)。

事故实证(2026-11-06→11-07): aux 24 条串行改 3×3 嵌套并行、own_health 再开 3 路, 单接口
9s→4.5s; 但两个重接口并发就吃满 10 条连接池, 其余请求阻塞 20s → **全站 10~40s**
(aux 35.8s / stock-risk 41.4s, 同刻 summary 0.8s)。同期 `loadAll` 与看板页自拉并存,
summary/stock-risk/alerts 各算两遍。局部更快, 整站不可用。

| 要求 | 判据 |
|---|---|
| 改动前 | 写下"原设计意图 + 本次替换掉哪条旧路径"(提交信息里一句话) |
| 改动后 | 旧路径代码删干净, 不留"开关两套"作常态(紧急回滚开关除外, 须写明退出时间) |
| 性能类 | 必须量"并发时整站": 并发 4 个重接口, 任一接口 P95 ≤ 单跑的 2 倍 |
| 数据库 | 连接/线程是全局资源: 用 `db.heavy` 全局预算, 禁止嵌套并行; 冷算必须单飞 |

完整条款见 `docs/DEVELOPMENT.md §七点五 性能改动红线`。

### 红线 B: 界面文案不硬编码中文

**前端所有用户可见文案走 `t('key')`, 新增 key 必须 zh + en 同时补**, 两份字典键集合必须严格相等。

- 覆盖: JSX 文本节点、`title`/`placeholder`/`aria-label`、`alert`/`confirm`、toast、空态、确认框、表格列名;
- **注释不算文案**, 不受限(本仓库注释一律中文, 这是刻意的);
- 后端 `error` 字段是**诊断文本**, 允许中文; 但它不能是唯一用户提示 —— 前端展示时用 `t()` 的可读前缀, 或按接口返回的 `code` 本地化;
- 判据: 改动文件里 `grep -nE "[\u4e00-\u9fa5]"` 的命中, 只应出现在注释与 `locale.ts` 内;
- **可执行门禁(2026-11-05 接上)**: `python3 scripts/i18n_audit.py --gate`, 已进
  `preflight.sh` 第 [8/8] 步与 CI `frontend` job —— 中文硬编码残留 > 0 / zh-en 键集不等 /
  `t()` 引用了不存在的键 / 变量名遮蔽 `t` / 空串假翻译, 任一命中即 exit 1。
  逐条定位用 `--lines`, 按文件汇总 `--by-file`。
- 死键(字典有但没被引用)只报数、不拦门禁。**当前基线 0 条**, 新增即说明有文案下线却没删词条。
  2026-11-05 从 513(其中 386 条是脚本误报)→ 127 → 0, 详见 `docs/history/2026-11-03~04.md` §15.144。
- **门禁扫不到的地方**: `document.title` / `<html lang>` / `meta description` 不在 React 树里,
  也不经过 `t()` —— 它们由 `locale.ts` 的 `applyDocumentLang()` 同步(setLocale + 启动各一次)。
  新增这类"文档级字符串"要挂进同一个函数, 否则切语言时它会留在中文。
  动态前缀 `col./fld./seg.` 不逐条判死, 由「产键函数是否还在」兜底;
  新增死键说明有文案下线却没删词条, 顺手清掉即可。
- 判定自检: `python3 scripts/i18n_audit.py --selftest`(18 条正负例)。改扫描规则必先过它 ——
  这条命令也进了 preflight [8/8] 与 CI, 自检红说明规则坏了, 后面的 PASS 不可信。

**为什么较真**: 项目已有中英双份字典(1437 键), 但页面里仍散着大量中文字面量 —— 切到 EN 就是半中半英。新代码再堆硬编码, 这件事永远收不了口。

### 红线 C: 禁用显式 any, 按 TS 原生类型写

**源码里不得出现 `any` 类型关键字**(`: any` / `as any` / `any[]` / `Record<string, any>` /
`Promise<any>` / `<T = any>` / `api.get<any>()`)。这是 `docs/DEVELOPMENT.md §4.1` 与 `§15.38`
早就写下的红线 —— 但 `.eslintrc.cjs` 一直把 `no-explicit-any` 设成 `off`(注释「历史大量 any,
逐步治理」), 于是历史存量涨到 194 处。2026-11-04 全仓清零并升为**准入门禁**。

| 场景 | 写法 |
|---|---|
| 组件 props / store 状态 | 精确 `interface` + 动态字段 `[k: string]: unknown` 索引签名 |
| API 动态数据 | `api.get<T>()` / `apiJson<T>()` 显式泛型; 兜底 `Record<string, unknown>` + `String()`/`Number()` 边界收敛 |
| 回调参数 | 删掉 `: any`, 让 TS 从数组元素类型推断 |
| 异常 | `catch (e: unknown)` + `e instanceof Error` / `'message' in e` 守卫 |
| 全局对象扩展 | 写 `global.d.ts`, 不要 `(window as any)` |
| Promise 挂元数据 | `Object.assign(promise, {...})` 交叉类型, 不要 `const p: any = promise` |

- **门禁**: `frontend/.eslintrc.cjs` 的 `@typescript-eslint/no-explicit-any` 为 `error`,
  `npx eslint src` 必须 0 error; 共享契约集中放 `frontend/src/api/types.ts`。
  执行位置: `preflight.sh` 第 [4/8] 步 + CI `frontend` job(`--max-warnings 0`)。
- **不许绕过**: `as unknown as any`、`@ts-ignore`、`// eslint-disable ...no-explicit-any` 一律视为违规。
- **不造假严格**(§15.38): 动态数据用 `unknown` 索引签名如实表达"不知道", 不要为了过编译
  把字段声明成假的具体类型, 也不要用 `ReturnType<typeof JSON.parse>` 之类的 any 替身。
- **类型改动 = 运行时零变化**: 类型标注/泛型/断言在编译期擦除; 整改时只动类型层,
  禁止顺手改表达式、条件、字符串值与函数体逻辑。

