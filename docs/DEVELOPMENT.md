# SupplyKit 开发规范

> **本册只回答"怎么写代码"**：规范 / 红线 / 流程 / 门禁。
> 变更史（某天为什么这么改、踩了什么坑、实测数据）在 [`docs/history/`](history/)，按日期分卷、§15.x 编号全局唯一。
> UI 规则见 [`UI_DESIGN.md`](UI_DESIGN.md)；架构与数据流全景见 [`ARCHITECTURE.md`](ARCHITECTURE.md)；
> 新会话入口读 [`../CONTEXT.md`](../CONTEXT.md)。
>
> 2026-11-06 重构：一~十四章原是 **SQLite + PythonAnywhere 时代**（`backend/`、WAL、VACUUM、CF Pages）的写法，
> 与现状（TiDB + EdgeOne Makers）冲突，已整体重写。旧细节保留在 `docs/history/2026-08-07~30.md` 等卷里，仅作考古。

---

## 一、项目定位

SupplyKit 是**电商供应链数据清洗 + 补货决策看板**，定位为 ERP 与 Excel 之间的"中间层"：
不做 ERP 的流程管理，也不替代 Excel 的灵活性。

- 看板 + 补货决策是核心；数据经清洗 → 规则引擎 → 补货/采购建议 → 输出决策
- 不替代 ERP 的核心流程；不与 Excel 竞争（SupplyKit 做自动化，Excel 做灵活性）
- 两条渠道：`jd` / `other`；两种补货模式：`bbcc`（B→C 一盘）/ `traditional`（C 仓逐仓）

---

## 二、技术栈（现行）

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | React 18 + TypeScript + Vite | 16 页面；图表 ECharts **按需引入**；状态 Zustand |
| 后端 | FastAPI（Python 3.10 语法门禁） | 23 个 APIRouter / 33 个路由文件；入口 `cloud-functions/api/index.py` |
| 数据层 | pymysql + TiDB Cloud Serverless | `db.py` 原生数据层（无 ORM）；连接池用 `BoundedSemaphore` 名额 |
| 部署 | EdgeOne Makers | Provider=Github → **只有 push 触发构建**，约 2~2.5 分钟 |
| 定时 | `edgeone.json` schedules | **10 条硬上限**，`Asia/Shanghai` 显式时区 |
| 质量 | preflight 八步 + CI | 语法 / pyflakes / 回归 / ESLint / Prettier / tsc / vitest / i18n |

---

## 三、项目结构（现行）

```
supplykit/
├── cloud-functions/api/          # 后端（Makers 函数根）
│   ├── index.py                  # 唯一入口：行首 `app = FastAPI()`；鉴权中间件；/health
│   ├── db.py                     # 数据层：连接池 + 占位符前置守卫 + query/execute/transaction
│   ├── schema_create.py          # 表/索引声明 + SCHEMA_VERSION 门禁（热路径零 DDL）
│   ├── migrations/               # 有序迁移：steps/*.py 单步事务 + schema_migrations 记账
│   ├── core/                     # 纯逻辑：metrics / rule_eval / alert_lifecycle / archive / task_lease ...
│   ├── biz/                      # 业务计算：sales（三窗口日销）
│   ├── routes/                   # HTTP 层：每个域一个文件
│   └── local_test.py             # 回归 681 条；--sql-smoke 真实 SQL 冒烟
├── frontend/src/
│   ├── pages/                    # 16 页；页面私有块放 pages/<页>/
│   ├── components/               # 跨页复用；hammer/ 是筛选面板（与侧边栏 .menu-btn 不是一回事）
│   ├── api/                      # client.ts（缓存/在途去重）/ mutate.ts（写操作统一入口）
│   ├── locale.zh.ts / locale.en.ts  # 中英双份字典，键集合必须严格相等（1437 键）
│   └── tests/                    # vitest
├── scripts/                      # 运维与门禁：preflight / push_api / deploy_status / local_api / i18n_audit ...
└── docs/                         # 规范 / 架构 / 决策 / 历史（history/）
```

后端分层铁律：`routes/` 只做 HTTP 编排，`core/` 放纯逻辑，`biz/` 放业务计算，`migrations/steps/` 放迁移。
**不要把新逻辑继续堆进页面或路由文件**（见 `SKILL.md` 红线 A）。

---

## 四、代码规范

### 4.1 TypeScript

- 组件 props 写精确 `interface`，动态字段用 `[k: string]: unknown` 索引签名
- **禁用显式 `any`**（红线 C）：`: any` / `as any` / `any[]` / `Record<string, any>` 一律不允许；
  门禁 `@typescript-eslint/no-explicit-any: error`，`npx eslint src` 必须 0 error
- API 动态数据用显式泛型 `api.get<T>()`，边界处 `String()` / `Number()` 收敛
- 类型改动是**编译期擦除**，整改时只动类型层，禁止顺手改表达式或逻辑

### 4.2 React

- 函数组件；`ErrorBoundary` 是唯一 class 组件
- 组件不直接算口径：数据长什么样由后端给，页面只渲染；筛选/区间口径归锤子面板
- 所有写操作走 `api/mutate.ts`（提示 / 缓存失效 / 联动事件 / 操作态一次声明）

### 4.3 CSS

- 尺寸/颜色一律 CSS 变量（`--radius-*` `--space-*` `--font-*` `--h-btn*`），不写魔法数字
- 玻璃三档差异化参数，动效只用 `opacity`（`transform` 会破坏 `fixed` 与安全区）
- 控件迁走后**死 CSS 随手删**，否则后人以为还有一套自绘控件

### 4.4 Python（后端）

- Python 3.10 语法（preflight 用 `ast.parse(feature_version=(3,10))` 硬门禁）
- SQL 一律 `%s` 占位符 + `db.query/execute`；**`INTERVAL %d` 必须 Python 级内联**
  （PyMySQL 会把 int 参数转 str → `TypeError`）
- 异常必须 `try_err(src, what, exc)`，**禁止 `except: pass`**
- 业务异常统一 `200 + {ok:false, error, detail}`；堆栈只进 `quality_logs`，不回浏览器

---

## 五、数据流与缓存

### 5.1 口径真源

判定口径只有一处：`core/metrics.py`（`units` / `grain_of` / `judge` / `code_risk`）。
六个消费方（断货卡 / 规则 ctx / 补货 / 采购 / 带销售 / 健康分）都调它，**任何地方不得自己拼公式**。
判定粒度跟随渠道补货模式：`bbcc → SKU×69码`，`traditional → SKU`，可用 `mode_<m>_grain` 覆盖。

### 5.2 缓存

- 全量结果按**序列化后 ≤1MB 分块**存 `analysis_cache`，读侧拼回再内存分页（单条上限 6MB，绝不接近）
- **写操作按域失效**：`invalidate("<域>")`，族依赖由 `_FAMILY_DEPS` 反推；未登记域名 → 全清兜底
- 失效是**打墓碑**（`created_at=1970` + `tok`），防"重建期间被失效 → 旧值覆盖"
- 加会改变结果的入参**必须同步改缓存 key**，否则只在命中缓存时复现，本地几乎抓不到

### 5.3 写入与原子性

- 库存增量用 `UPDATE ... SET available_qty=GREATEST(0, available_qty+%s)`（防重复扣成负数）；
  **重算**（期初+入库−出库）**保持负值不夹 0** —— 负可用是账实不符的证据
- 归档：365 天切线，每 1 万 id 一个闭区间 `[a, z]`，区间内 1 条 `INSERT...SELECT...GROUP BY` + 1 条 `DELETE` 同事务
- 增量重建按**业务日期**（`ordered_at`），不能由导入时间推导
- 改 schema 必须 bump `SCHEMA_VERSION`；加表/索引改声明即可，迁移走 `migrations/steps/`

### 5.4 读接口零副作用

`_build_*` 只算不写 —— 告警生成由任务驱动（导入完成 / 参数保存 / cron / 规则页运行），
**不在读路径里写库**。`test_audit` 有静态断言守这条。

---

## 六、部署与发布

```bash
python3 scripts/env_check.py            # 换机/新会话第一条：缺凭据一次性列全
bash scripts/preflight.sh               # 八步门禁，全绿才可推
python3 scripts/push_api.py -m "fix(x): ..."   # 推送 = 触发构建（github.com:443 在本沙箱不可达）
python3 scripts/deploy_status.py        # 部署判据：Status=Success + Prod=yes + 远端内容与本地一致
python3 scripts/smoke_check.py          # 线上冒烟：health 必须 JSON，变 HTML 就是路由丢了
```

- 走 `push_api.py` 时**本地 SHA ≠ 远端 SHA 是预期**（author/committer 时间戳不同），
  判据是"远端 HEAD 与本地 HEAD 逐文件一致"，不是 hash 相等
- 部署成功 ≠ 功能生效：写完路径必须查 `quality_logs` 最近 2 小时
- **没数据触发的分支必须明说"当前无数据触发"，不能声称已验证**
- 迁移/重建这类重操作先只读确认影响面，并记下回滚点（行数 / Σ 金额）

---

## 七、测试规范

### 7.1 原则

```
先写测试 → 确认它失败 → 改代码 → 确认通过
```
不跑的测试等于没有。新增门禁时必须**反证**（临时移除一项确认会报红），否则"全绿"可能只是没扫到。

### 7.2 现行门禁（`scripts/preflight.sh`）

| 步 | 检查 | 判据 |
|---|---|---|
| 1 | Python 3.10 语法 | 全部 `.py` 可解析 |
| 2 | pyflakes | 无 undefined name / 重定义 / 先使用后定义 |
| 3 | `local_test.py` 回归 | **681 通过 / 0 失败**；有 TIDB 凭据时顺带 SQL 冒烟 |
| 4 | ESLint | `--max-warnings 0`，含红线 C 禁 any |
| 5 | Prettier | 格式合规 |
| 6 | `tsc --noEmit` | 0 error |
| 7 | vitest | 全部通过 |
| 8 | i18n 门禁 | 中文硬编码 0 / zh-en 键集相等 / 无 t() 死引用 / 自检 18 例 |

真实 SQL 冒烟（`--sql-smoke`）：收集执行过的 SQL 对生产库做 `EXPLAIN`（零副作用），
并做 schema 对账（声明的表/列/索引必须存在）。**写了新 SQL 先问：它会被冒烟收集到吗？**
没被测试触发的分支等于没验。

### 7.3 规模

单 SKU 样本会让批次/分摊/分组类缺陷全过（每次只走一条分支）。**至少 ≥200 SKU 跑一轮**，
分页/评估类改动要在**超过单页上限**的规模上验（例：12 万行验证游标分页）。

---

## 七点五、性能改动红线（强制，2026-11-07 事故沉淀）

> **优化前先读懂原设计：它为什么这样写、解决过什么问题。不理解就动 = 叠加，不是优化。**

2026-11-06"并行化优化"把 aux 的 24 条串行查询改成 3×3 嵌套并行、own_health 再开 3 路，
单接口冷算从 9s 降到 4.5s；但同一实例同时进来两个重接口就把 10 条连接池吃满，
后续请求在 `_acquire` 阻塞 20s —— 线上表现为**所有页面一起卡 10~40s**
（aux 4.5s→35.8s、stock-risk 4.5s→41.4s，同刻 summary 仍 0.8s）。
局部更快，整站不可用。

| # | 红线 | 违反的典型症状 |
|---|------|----------------|
| 1 | **先读原逻辑再动手**：改动前必须回答"这段代码原来在解决什么、注释里记过什么坑"。看不懂就先写清设计意图，再决定改不改 | 把"去锁/去串行/去缓存"当成纯洁癖，把当初的防护拆掉 |
| 2 | **禁止叠加**：同一件事只允许一处真源。新方案必须**替换**旧路径并删除旧代码，不许两套并存"兜底" | summary 被 `loadAll` 与页面自拉各算一遍；alerts 两次请求；缓存三层互不感知 |
| 3 | **优化必须证伪全局**：给出"单接口变快"之前，先量"并发时整站"。判据：并发 4 个重接口，任一接口 P95 不得超过单跑的 2 倍 | 串行改并行、1 次改 8 线程 → 单跑快 2 倍，并发慢 8 倍 |
| 4 | **连接/线程是全局资源**：并发度由全局预算分配（`db.heavy`），不许每个请求各开 3~8 路；禁止嵌套并行 | 一个请求峰值占 6~9 条连接，池上限 10 |
| 5 | **冷算必须单飞**：同一缓存键并发 miss 只允许一个 builder，其余等结果或读旧值 | 同一份结果被算 5~8 遍，CPU 互抢 → 30s+ |
| 6 | **读路径不做全量**：请求路径只读"已物化/已缓存 + 有界行数"的结果；全量扫描只允许在后台任务里 | 9 万行库存 / 6 万行日销为了 10 个整数被搬进请求 |
| 7 | **改动前后都要留线上证据**：改前记 slow_request/perf_debug 基线，改后同口径复测；不许用本地直连计时代替线上响应时间 | "本地 200ms"上线 8s |
| 8 | **物化快照三件套**：①「没算成」与「真的是 0」必须可分（哨兵列，如 `risk_rows=-1`）；② 指纹要覆盖**全部输入域**（判定不看 OTIF，但断货卡看供应商评分 → 必须独立 `risk_fp`）；③ 排序要有确定性 tie-breaker，且现算/读回**共用同一函数** | 计算异常时卡片显示"0 条"，用户以为没风险；供应商改分不生效；双路对账 17000 条纯错位 |
| 9 | **物化的算法不许复制**：重建侧只调现算入口（`_stock_risk(legacy=True)`），不把公式抄一份到 `core/` | 两份公式，改一处漏一处，两张卡片各自漂 |
| 10 | **并行只在双方都不饱和时更快**：重计算接口（扫全量表的）并发 = 互相限速；判据是"两条一起跑量墙钟"，不是理论并行度 | replenishment 单跑 1.4s、与 purchase 并发变 15.9s/16.4s（1+1=30） |
| 11 | **分页不解决"算得慢"，只解决"搬得多"**：先物化（算一次）再分页（读一页）。在"每次现算全量"之上加分页是负优化 —— 翻 10 页等于算 10 遍。判据：随手翻到第 3 页，后端**没有**重算全量 | own-health 早就是 200/页，单页仍 2.9s（算 1.3 万条再切片）；"共 N 条"这类计数必须物化或建索引，分页救不了 |
| 12 | **分页切页的序号必须与卡面排序同源**：SQL 不参与排序（collation 与 Python 码位序不同），重建时把排序结果落成 `ord`，读侧按 `ord` 切 | 同一行在第 1 页和第 3 页各出现一次、另一行永远翻不到；`full=0` 的 TOP10 与第 1 页前 10 不同序 |
| 13 | **边缘网关会剥 `Accept-Encoding`**：压缩不能依赖标准协商头。显式 `X-Accept-Encoding: gzip`（X- 头可透传）触发；已压缩类型(xlsx/zip/SSE)与 <1KB 跳过 | identity/gzip/none 三种请求响应头逐字节相同；82KB 明文按 ≈130KB/s 出网要多花 0.6s |
| 14 | **SQL 字符串里禁止混用占位符与 % 格式化**：`"A" + x + "B % _FIELDS"` 里 % 只作用于最后一段（优先级高于 +），`%%s` 会配上多余实参炸 TypeError。多段拼接一律显式 `"SELECT " + _FIELDS + " WHERE ..."` | `/api/alerts` 非分页静默 500 半天没人发现（前端都走 paged 了） |

## 八、代码审查

提交前自查（与 preflight 互补，不替代）：

- [ ] 改动是否触碰七条业务口径之一？触碰了要说明"哪条、为什么"
- [ ] 新增字段是否进了所有消费方（写入方对了不够，渲染点、导出、告警文本都要看）
- [ ] 缓存 key 是否覆盖新增入参
- [ ] 新 SQL 是否会被冒烟收集到；`INTERVAL` 是否内联
- [ ] 新文案是否 zh + en 双份；表格列名是否走 `colLabel()` 而非字段名原文
- [ ] 失败路径是否留痕（`try_err`），有没有 `except: pass`
- [ ] 文档里的数字（回归数 / 路由数 / 页面数 / 字典键数）是否同步

---

## 九、国际化

- 所有用户可见文案走 `t('key')`，新增键必须 zh + en 同时补，键集合严格相等
- **注释不算文案**（本仓库注释一律中文，这是刻意的）；后端 `error` 是诊断文本，允许中文
- 门禁：`python3 scripts/i18n_audit.py --gate`（preflight 第 8 步 + CI），定位用 `--lines` / `--by-file`
- 扫不到的地方：`document.title` / `<html lang>` / `meta description` 由 `applyDocumentLang()` 同步，
  新增这类"文档级字符串"必须挂进同一函数
- 死键只报数不拦门禁，**当前基线 0**；新增死键说明文案下线却没删词条

---

## 十、版本控制

- 提交格式：`fix|feat|perf|refactor|docs|test(scope): 主题`；body 写「根因 → 修法 → 实测数字」
- 分支：`feat/edgeone` 为开发主线；**push 即部署**，所以推送前必须门禁全绿
- 绝不提交 `.env` / `.edgeone/`；密钥不进 commit message、对话、日志

---

## 十一、常见问题

| 现象 | 先查 |
|---|---|
| 本地通、线上 404 | 路由是否写了 `/api` 前缀（框架自己会剥）；入口 `app =` 是否在行首 |
| 线上 500 变成 HTML | 业务异常没走 `ok:false` 包装；堆栈进了浏览器 |
| 登录成功但全接口 401 | 用了 `Authorization: Bearer`，应改 `X-Auth-Token` |
| 页面空白但构建成功 | 模块级代码引用了未导入的变量（bundle 整体加载失败） |
| 数字只在某些时候不对 | 缓存 key 缺维度 / 读到了别的页的缓存 |
| 定时任务整组消失 | `edgeone.json` schedules 超过 10 条 |
| 归档少一行 | 区间用了 `id > a`，应为 `id >= a`（会丢 MIN(id) 那条） |
| 评估漏数据 | 是否撞上安全上限（现在会留痕告警，不再静默） |

---

## 十二、变更史（已按日期拆分）

> 2026-11-06 文档重构：原 147 节 / 约 7,391 行按日期移入 `docs/history/`。
> **只移动未改写**，编号 §15.x 全局唯一 —— `grep -rn '15.100' docs/` 可直接定位。

| 范围 | 章节 | 节数 | 文件 |
|---|---|---|---|
| 2026-08-07~30 | §15.1 ~ §15.13 | 13 | [history/2026-08-07~30.md](history/2026-08-07~30.md) |
| 2026-09-01~11 | §15.14 ~ §15.30 | 17 | [history/2026-09-01~11.md](history/2026-09-01~11.md) |
| 2026-10-06~30 | §15.31 ~ §15.93 | 60 | [history/2026-10-06~30.md](history/2026-10-06~30.md) |
| 2026-11-01~02 | §15.94 ~ §15.106 | 14 | [history/2026-11-01~02.md](history/2026-11-01~02.md) |
| 2026-11-02 | §15.107 ~ §15.116 | 10 | [history/2026-11-02.md](history/2026-11-02.md) |
| 2026-11-02~03 | §15.117 ~ §15.126 | 10 | [history/2026-11-02~03.md](history/2026-11-02~03.md) |
| 2026-11-03 | §15.127 ~ §15.132 | 6 | [history/2026-11-03.md](history/2026-11-03.md) |
| 2026-11-03~04 | §15.133 ~ §15.145 | 13 | [history/2026-11-03~04.md](history/2026-11-03~04.md) |
| 2026-11-04 | §15.146 | 1 | [history/2026-11-04.md](history/2026-11-04.md) |
| 2026-11-05 | §15.147 | 1 | [history/2026-11-05.md](history/2026-11-05.md) |
| 2026-11-06 | §15.148 ~ §15.150 | 3 | [history/2026-11-06.md](history/2026-11-06.md) |

找法：不知道哪一天 → `grep -rn '<关键词>' docs/history/`，命中后再按 §15.x 编号回查。
**新增记录写在 `docs/history/<当天>.md`，不要再往本册追加。**
