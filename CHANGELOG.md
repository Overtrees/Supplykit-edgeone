# 变更总览（CHANGELOG）

> **这是什么**：按日期倒序的**交付摘要** —— 每天/每批做了什么改动、涉及哪些模块。
> **这不是什么**：不是"为什么这么改"的根因记录。根因、踩坑、验证数据在
> `docs/history/`（按日期分卷，§15.x 编号全局唯一）与各专题文档里。

| 想看什么 | 去哪 |
|---|---|
| 最近交付了什么 | 本文件（保留最近一个月） |
| 某天为什么这么改、踩了什么坑 | `docs/history/` |
| 代码怎么写、红线是什么 | `docs/DEVELOPMENT.md`（规范册） |
| 当前已知遗留 | `CONTEXT.md` → 「已知遗留」 |
| 架构与数据流 | `docs/ARCHITECTURE.md` |

> 2026-11-06 文档重构：2026-08 ~ 2026-10 的 73 条记录移到
> [`docs/history/changelog-2026-08~10.md`](docs/history/changelog-2026-08~10.md)，
> 本文件只保留 2026-11。原「当前已知问题：Chart 组件 ECharts 初始化偶发失败」是 8 月的
> 单条记录、早已被按需加载重构覆盖，改为统一在 CONTEXT.md 维护遗留清单（一处维护，避免两份漂移）。

---

## 2026-11-06 全天（S0~S8 八包落地 · 文档重构 · 三条遗留收口）

> 详情见 `docs/history/2026-11-06.md` §15.150 / §15.151。

| # | 主线 | 核心结论 |
|---|---|---|
| 1 | **S0~S8 八包** | 从"止血"做到"换键"：止血 6 处（`MAX(ordered_at)` / `safety_qty` 10→0 / 分片 / `DEMAND_STATUSES` / 孤儿关闭传 channel / `evaluated +=`）→ 前端缓存键与在途（S1）→ 告警生成搬出 builder + `uq_alerts_dim` upsert（S2）→ `core/metrics.py` 成为唯一真源、多码不再静默合并（S3）→ 评估作业化（S4）→ 租约 + `import_stage` + `eval_queue`（S5）→ 归档集合式 / 重建分片事务 / 对账 JOIN / 日志分级（S6）→ `/debug` 关停 + 常数时间比较（S7）→ products 唯一键 `(sku)`→`(channel,sku)`（S8） |
| 2 | **两项口径定案** | 删掉 `order.total_qty`/`lifetime_qty`（改用未发货量 `order.open_qty`，全历史销量"卖过就必成立"）；粒度按渠道模式统一且可配置（`metrics.grain_of`，bbcc→69码 / traditional→SKU，`mode_<m>_grain` 可覆盖） |
| 3 | **文档重构** | `docs/DEVELOPMENT.md` 7,745 → 246 行（一~十四章原是 SQLite/PA 时代写法，整体重写）；§15.1~§15.149 按日期拆成 12 卷 `docs/history/`（只移动不改写，编号全局唯一）；CHANGELOG 2,981 → 785 行；WBY_WORKFLOW 删掉与 SKILL 重复且已漂移的闭环 |
| 4 | **S4-1 补做** | 全量评估原为 `ORDER BY id LIMIT 100000`，超出部分**既不评估也不关闭**（线上 jd 90,100 行离上限只差 1 万）。改 id 游标分页；首版踩坑：`INVENTORY_COLS` 不含 `id` → `_page[-1]["id"]` 恒 0 → 同一页读 60 次（12 万行读成 30 万行），实测抓到后修 |
| 5 | **遗留收口** | ① 评估"执行前就写指纹"导致冻结后永久跳过（线上 5.5 小时空窗）→ 三态占位、成功才落地、超时可抢占、cron 与兜底共用；② 评估真分批（`units(acc=)` 增量累加 + `_agg` 下推 SQL），8 万行实测内存 300→138MB、逐单元一致；③ aux 剖析：销量物化按需取列（少传 2.5MB）+ 自有仓三块并行，A/B 输出逐字节一致 |

**验证**：本地回归 **618 → 690 通过 / 0 失败**；preflight 八步全绿（SQL 冒烟 290 条 0 错 /
schema 49 表 476 列 15 索引对齐 / vitest 46 / i18n 死键 0）。线上实测：归档 25,000 行 3 个区间
GMV 逐分对平；12 万行评估 25 次分页读满、无重复无截断；多码断货 `code_out_count=1`；
负库存重算 −30 保持负值；S8 两渠道同 SKU 各一行成功、同渠道重复仍 1062。

---

## 2026-11-05 全天总览（六条主线）

> 按当天 git 记录归并（本节及后续文档修订本身亦计入当天提交，故不写死次数）。
> 全天回归 **466 → 618 通过 / 0 失败**，preflight **7 → 8 步**，
> `SCHEMA_VERSION` **6 → 12**，i18n 字典 **1548 → 1428 键**（zh/en 对齐）、死键 **513 → 0**。
> 当天多次部署 Success 并上生产；末段切换到第二集群重建空库并做全链路验证。

| # | 主线 | 提交 | 核心结论 |
|---|---|---|---|
| 1 | 账期 / 首次结账实跑 | `3049cfb` `018bda4` `85c90ba` `86bdc26` `e4c110e` `d13e056` `9819f74` | 预演数字全对、真跑连挂三次，故障全在**写入路径**不在算法：`state_get` 空表默认 `period=当月` 把首结拦死；批次切片撞主键(1062)；`SCHEMA_VERSION` 未 bump 致 `ledger_base` 不落地；结账把结账月量写进当月字段致跨月串号。补 `ledger_base` 基线、期初双场景(结转 / 窗口内实盘覆盖留痕)、`/ledger/status` 的 cur 账期语义 |
| 2 | 规则字段 / i18n 门禁 | `227f76a` `d87986b` `5bb926f` `5434a2d` `0ae5e10` `738baa8` `243c9b9` | 规则字段三份列表合一为 `core/rule_fields.py`(单一真源)，反向堵住「界面可选、保存被拒」与「能选却从不注入」；i18n 审计接入 preflight 第 8 步 + CI，死键 513 的差额全是审计脚本自身误报(动态前缀/词表键/对象简写)，清理 125 条并做文档级语言同步(title / meta / html lang) |
| 3 | seed 规模与 RU | `d08bd5a` `45b5bad` | 规模扩到 90 天 / 1 万 SKU / 21 万单；**RU 按「请求次数」计不按行数**：`_recalc_own_inventory` 由每 200 SKU 一条 SELECT 改成「一条 SELECT 取全量 → 内存算 → executemany 回写」，批数 24000 → 2，RU 2500 万 → 2.4 万。新增 `seed_watermark` 水位(回滚只删新增行 + expected/actual 对账)。落账段 280s → 36s(批 100 → 1000) |
| 4 | seed 改走真·导入链路 | `b1d3463` `64dfe5d` `1c04aad` `ac5ca12` `c6d4c07` `ee2a06a` `2505470` | 「复用生产函数」只抽了生成逻辑，落库仍是裸 executemany —— 导入入口才有的归码 / 四维键 / 期初校准 / 可用重算 / 批次 FIFO 一条没走过。改成内存行 → **中文表头 CSV** → `_parse_table` → `_clean_rows` → `_write_rows`，四层全复用线上实现。中途实测暴露并修：库存文件自带仓型被抹平、批次效期逐行 SQL、月汇总未走唯一键、`baseline` 后门撤回改 `detect_scene` 四场景、租约 600s → 150s → **30s 心跳 + `lease_token`** |
| 5 | 窗口接力与前端竞态根治 | `73336a7` `f7295ba` `dbe67e2` `2fa6d0a` | 段号不再随切分粒度变(曾因跑一半部署换段号导致重复写入)，`step` 恒为 19 个业务阶段，数据范围只由持久化 `offset` 决定；单请求跑到 **75s 预算**即停、写回游标、返回同一 step，看守看到 offset 前进立刻接力。TiDB 实测单条 INSERT **40 万行 2.948s / SQL 4.9MB**(64MB 上限的 7.6%)，推翻「2000/批只落 500」的过时结论 —— 瓶颈是 Python 生成与逐行 SQL，故放大写批次。前端侧：状态端点加 **`probe=1` 纯读**(此前查一次 = 抢锁 + 推进 + 写库，被 6 处轮询打)，`taskWatch` 成为唯一驱动源；徽标分前后台在途计数；任务卡显示已运行总时长；任务页三态收敛为单一 `phase`；reset 后整体失效 store 快照 |
| 6 | 第二集群 + CSV 交付 + 日志噪音根治 | `2fa6d0a` `8fc907b` `d233345` | RU 配额用尽后启用第二集群 `Supplykit`(现有、非新建)：32 张遗留表全清 → 48 张表按 schema v12 全量重建 + 基础配置 + 全链路联动验证，线上 Production env 切新凭据(其余键不变)。7 种导入类型按 seed 成品**只读导出**成 CSV(合计 807,650 行，数量/场景/字段一律不变)。末两轮根治「非真异常连续噪音」：正常态不再走异常日志通道 |

**第 6 条主线的两轮收尾**

| 轮次 | 根因 | 改法 |
|---|---|---|
| 后端 `8fc907b` | `try_err` 是「静默吞异常」通道(固定 warning/quiet_error)，三处正常态误用：`continue-tasks` 每 60s 无条件写一条 `rows=0` 诊断(空库永不停止)；空库 `MAX(ordered_at)=''` 传进 `strptime` 必抛异常、每次 dashboard 一条告警；建表/迁移成功信息传空 exc 记成 warning 且带 `: str: ` 尾巴 | 新增 `log_info`(info/maint) 与异常通道**分离**；`continue-tasks` 空态零写入、有任务才记 info；`_track_data` / `_snapshot_step` 入口判空；成功信息改走 `log_info` / `_log_ok`；`sql_smoke.report` 兼容前置失败哨兵(连不上库时清楚报因、不崩 traceback)。`try_err` 本体一字未动 |
| 前端 `d233345` | 401 未登录 / 403 访客只读是**预期鉴权结果**，却和网络错误、5xx 一起上报成 `api_http_error/warning`；匿名访问部署预览域名必然 401 → 每次部署刷噪音 | 新增 `isExpectedAuth(status)` 单一判定，axios / `apiJson` / `uploadRawBytes` 三处 HTTP 状态上报点统一排除；`throw` 保留(业务层仍感知失败)，5xx / 网络错误 / 400·422 照常上报。401 的认证态仍由 `auth:may-expired → /auth/check` 专门链路处理 |

**验证（线上，部署 `92a9cc3` = 本地 `d233345` 推送后远端生成的 commit，Success 后）**：清空日志基线 → 匿名 401 访问 + 12 个模块接口全 200 +
跨 75s(覆盖 `continue-tasks` 60s 周期)持续请求 → **5 类非真异常全部归零**
(`continue-tasks rows=0` / 快照续段降级 / 无效 perf_debug / 建表成功误记 / 前端 401·403 误报)。
库内仅剩 1 条 `slow_request` warning(`GET /dashboard/summary 3.0s`) —— 这是 `>3s` 才触发的
真实性能监控信号、非连续噪音，按「不削弱真异常留痕」的红线保留。
本地回归 618 / preflight 八步全绿(新库真实 SQL 冒烟 267 条 0 失败，schema 对账 47 表 464 列 13 索引无差异)。

**口径红线复核**：seed 与真实客户走**同一条**导入链路，无专用开关、无绕窗口、无另立公式；
`ee2a06a` 曾加的 `baseline=True` 后门已于 `2505470` 撤回并改四场景统一识别。全天未动账期算法口径。

---

### 2026-11-05 追加（第三轮：seed 改走导入链路 + 四类实测问题）

| # | 现象 | 结论与改法 |
|---|---|---|
| 1 | 二次进任务页：骨架 → **闪一下空态** → 数据 | **真 bug**：`channel` effect 先 `setTasks([])` 再置 `loading`；首个响应回来若真的空，骨架消失的同一帧就落进 EmptyState。加 `loadedOnce` —— 没成功拉到过一次之前不渲染空态 |
| 2 | seed 是否真走导入 | 上一轮只让 seed **复用**生产函数，绕过了导入本身。本轮改成真·导入：内存行 → 中文表头 **CSV** → `_parse_table` → `_clean_rows` → `_write_rows` |
| 3 | 卡片还是一直「刚刚」 | **第二层根因**：`relTime` 把 `2026-11-05T08:30:00` 补一个 `Z` 当 UTC（差 8h），带 `+08:00` 时再补 `Z` 直接解析失败 → `NaN` → 恒「刚刚」。改为按后缀归一；running 且本次没推进时显示「已运行 Ns」 |
| 4 | 持锁 600s 时别的页面怎么办 / 多次切页面 | 驱动从任务页搬到 **App 层看守**：全局串行单请求、超时 115s（< 网关 120s）、无进展指数退避 3s→15s、切页面不摘监听 |

**seed 走导入链路（本轮主体）**

新增 `routes/seed_import.py`：6 个 target 的文件规格（中文导出列名 + 映射 + 键 + 冲突模式），
以及 `to_csv` / `import_rows`。seed 只负责**生成内存行**，落库一律经导入：

| 数据 | 导入 target | 顺带被验证的链路环节 |
|---|---|---|
| 商品档案 | `product` | 映射漏列（`supplier_code` 那次就是这么发现的） |
| 订单（21 万/渠道） | `order` | 69码归码、四维键对齐、批内聚合、脏日重建、缓存失效 |
| 库存快照 | `inventory`（自有仓）/ `inventory_b`（平台仓） | 未归码拒写、期初实盘校准、可用重算、批次 FIFO 重建 |
| 出入库流水 | `inbound` / `outbound` | 仓型归属纠正、效期批次同步、月汇总回填 |

`_write_batch` 增 `conflict_sql`：调用方显式给 `ON DUPLICATE KEY UPDATE` 子句。
**为什么必须有**：键里含 `deleted_at` 时，缺失对应 update 子句会让 TiDB 的 upsert
静默退化成"什么都不做" —— 写成功、数据没变，比报错更难查。

`inventory` 增 `week_inbound` / `week_outbound`（本周口径，周一至今，**不能由 `month_*` 推导**），
`SCHEMA_VERSION` 10 → 11。

**可用重算不再由 seed 补第二次**：落账段原先再调一遍 `_recalc_own_inventory`，
现在只做**只读核对**（`_assert_available_balanced`：可用 vs 期初+Σ入−Σ出，有差就留痕）。
理由：重算已经是导入链路自己的职责，seed 再算一遍等于承认链路没生效 —— 正好掩盖要验证的东西。


**部署后线上实测暴露的两个问题（已修）**

| 现象 | 根因 | 改法 |
|---|---|---|
| `platform_b` 凭空 8 万行、`platform` 归零 | `_write_rows` 库存分支**无条件** `it["warehouse_type"] = wt`，把文件自带的仓型抹掉 —— 一份含"平台仓 + B仓"的导出传上去仓型全变 | target 只作**默认值**：`_wt0 or wt`。文件写明的仓型要尊重 |
| 库存导入 362s（整链最重） | 两渠道 × 全部仓型一次性导入 | 库存也按「渠道 × 仓型」拆四段，与落账同一套切法 |
| 订单单段 71~148s（第 4 段 148s） | 15 天双渠道一次生成 | 订单 6 段 × 15 天 → **9 段 × 10 天**，单段订单量降到 1/3 |

段号：`SEED_DONE` 15 → 21（订单 9 + 库存 4 + 流水 1 + 落账 4 + 月汇总 1 + 尾段 1 + 1）。

```
本地回归  530 → 568 通过 / 0 失败（新增 33 条：导入规格/CSV 往返/链路复用/看守/解析）
preflight 八步全绿（schema 对账 47 表 463 列 13 索引 无差异）
实测       本地连库发 3 行商品 + 1 行库存 + 1 行入库 → 批次自动生成、barcode 归码正确
```

### 2026-11-05 追加（第二轮：用户报「没根治」的三条，挖到与上轮不同的根因）

上一轮修的是症状（加序号、加定时器），这一轮每条都再往下挖一层 —— 这也解释了为什么用户仍在复现。

| # | 反馈 | 上一轮结论 | 真实根因 |
|---|---|---|---|
| 1 | 任务卡仍显示「刚刚」 | 卡片不重渲染 | **数据层**：卡片读 `created_at`（创建时刻），seed 分步长跑每步推进不改它；且轮询内容不变时 React 不重渲染 → 时间冻结。补 `updated_at` 心跳 + 单条 1s 时钟 |
| 2 | 多页面卡加载中/同步中 | 加竞态序号 | **胶囊只信 9 处手工配对的 `syncing`**；`loadAll` 一边 `+1` 赋值一边 `Math.max(0,-1)`，减的是别人的名额。网络层补在途请求数硬信号 |
| 3 | 进任务页卡骨架屏 | 加竞态序号 | `setLoading(false)` 挂在 `if (mySeq !== loadSeq.current) return` **之后**，3s 轮询每轮推走序号 → 永不执行。挪进 `finally` |

**顺带挖出的三个真 bug**（不在反馈里）：

| 位置 | 问题 | 后果 |
|---|---|---|
| `TaskPage` `forEach(function (t){...})` | `t` 遮蔽翻译函数 `t()` | `t('task.done_toast',…)` 变成调用任务对象 → TypeError 被吞：完成提示永不弹 + 后续刷新全跳过 |
| `useAppStore` `set({orderLoading:false})` | 在 `stock-risk`（35s）之后且无序号保护 | 快速切页时旧请求踩新请求 → 骨架闪空白 / 卡 35s |
| `InventoryPage` 竞态丢弃分支 | 被丢弃也 `setLoading(false)` | 新请求在跑却被刷成"加载完" → 列表闪空白 |

**落账段再拆（12 → 13 段）**：两渠道 21.6s×2 ≈ 50s 是整链最重一段，拆成 `落账jd` / `落账other` 各 ≤30s。
⚠ 拆段必须同步修 `_seed_batches`：`DELETE` 清两渠道但 `rows` 查询无 channel 条件 → 只清 jd 会
**jd 双份、other 三份**。已用 `_chs` 统一约束 DELETE / rows / products / best_before 四处。

```
本地回归  517 → 530 通过 / 0 失败（新增 13 条）
preflight 八步全绿
```

### 2026-11-05 追加（五条用户实测反馈 → 五处独立缺陷）

| # | 用户反馈 | 结论 |
|---|---|---|
| 1 | 任务卡片跑了好几分钟还显示「刚刚」 | **真 bug**：`relTime()` 纯渲染期计算，卡片不重渲染就不重算 |
| 2 | 重置后自有仓健康还有数据 | **不是 bug**：那是重置后重新灌的 seed 数据（own 10,100 行/渠道，接口返回 622 条对得上） |
| 3 | 返回看板再进任务卡卡在骨架屏 | **真 bug**：`loadTasks` 三源并发竞态，慢的请求后到把 `loading` 覆盖回 true |
| 4 | 多数页面一直在加载/同步中 | **真 bug**：`loadAll` 有 4 处 `return` 在 finally 之前，`loading` 不复位 |
| 5 | 任务十来分钟不结束 | **真 bug（最贵）**：`_rebuild_own_batches` 100 SKU/批 × 2 万 = 200 批 × 1.4s ≈ **280s**，撞死 90s 上限 |

**第 5 条的修法**与 §15.157 的重算是同一条规律——耗时只跟**查询次数**有关：

```
批大小  单批耗时  批数   总计
100     1.26s    200   ≈280s  ❌ 撞死
1000    1.78s     20   ≈36s   ✅
2000    2.78s     10   ≈28s   （单批抖动风险更大，1000 是拐点）
```

同批省掉 `SELECT DISTINCT sku FROM inventory`（2 万行实测 **3.1s**），改用内存 `skus_data`。

**水位对账第一次跑就抓到一个老 bug**：`_seed_orders_range` 返回的是全局累计序号
（含 `day_from*1000000` 偏移），不是本段行数 —— 报「预期 15,072,115 / 实际 72,115」，
差正好 1500 万。这个错值一直用于 step summary 展示，从没人核对过。

**顺带修**：进度条 `progStep / 10` 写死 10 段（段号已变 12，step>10 顶在 99%）；
`this._loadSeq` 改为模块级 `_seqBox.v`（`this` 在延迟回调里不一定指向 store）。

```
回归        510 → 517 通过（新增 7 条断言，全部针对上述缺陷）
门禁        八步全绿
实测        /seed/step 含落账段: 改前 121s 超时 → 批 1000 后应 ≈36s
```

详见 DEVELOPMENT §15.158。

### 2026-11-05 追加（seed 规模扩到 90 天 / 1 万 SKU / 21 万单，RU 从 ≈2500 万压到 2.4 万）

用户定案：每渠道约 21 万单 / 10000 SKU / 90 天，RU 是硬约束（剩 2900 万）。

**规模必须反推**：按"90 天 × 2333 ≈ 21 万"直算跑出 **32 万单** —— 促销日（×2~4，25 天）
与周末（×0.6~1.2）是在基数上**再乘**的，21 万是含波峰后的总量。基数改 1550，
实测 **jd 214,984 / other 212,173**。

**RU 按「批次数」计，不按「SKU 数」计** —— 这是本轮最贵的一课。旧
`_recalc_own_inventory` 每 200 SKU 一条 SELECT（29.6 RU/批 × 120 批）。改成
`UPDATE JOIN` 只省两成（因为**还是按 SKU 分批**）；真正解法是把批数压到 1：
一条 SELECT 取全量 → Python 算 → executemany 回写（119.4 RU / 39.6ms）。
**≈2500 万 → 239 RU**。同批去掉滞销判定的 422 RU 全表扫描（改内存优先）。

**段号重排引爆两处硬编码**：`/seed/step` 白名单 `(5,6,7,8)` → 从段常量推导；
`task_seed.py` 的 `nxt >= 10` → 完成值已变 12，**任务越过 10 永不落 done，前端轮询无限续跑**
（每轮空转烧 RU）。改为 `nxt >= SEED_DONE` 并在回归里钉死。

**新增 `seed_watermark`**：写入前记 `MAX(id)` 水位，回滚只删 `id > hwm_before`
（历史行一行不碰），并做 expected/actual 行数对账 —— 旧实现"生成 17 万、库里 16.98 万"
完全静默。新增 `POST /api/seed/watermark`（只读）与 `POST /api/seed/rollback`。

**`SCHEMA_VERSION` v8 → v9**：加声明时忘了 bump，`ensure_schema` 走热路径 →
新表永远不建。这次是 `--schema-diff` 对账在 preflight 里抓出来的
（`seed_watermark.<整表不存在>`），说明"改 schema 必 bump"必须由门禁挡，不能只写注释。

```
回归        497 → 510 通过（新增 13 条：规模/段号/多码比例/水位白名单/完成判据）
实测生成    jd 214,984 / other 212,173 单；库存 170,200 行；多码行 200
SQL 冒烟    396 条 → ✓263 / ✗0 / ⚠0
schema      47 表 / 460 列 / 13 索引 全对齐
门禁        八步全绿
```

详见 DEVELOPMENT §15.157。

### 2026-11-05 追加（文档级语言同步：title / meta / html lang）

`app.name` / `app.desc` 从"全仓零引用的死键"变成活键：`locale.ts` 新增 `applyDocumentLang()`，
在 `setLocale()` 与 `main.tsx` 启动时各调一次，同步 `document.title`、
`meta[name=description]`、`<html lang>`（zh→`zh-CN`，en→`en`）。

两处调用缺一不可：首访语言由 `localStorage`/`navigator` 探测，**走不到 `setLocale`**，
只在 setLocale 里做等于"英文系统首访仍看到中文标题"。

关键防御：`t()` 缺键时回落到键名，所以加了 `name !== 'app.name'` 判断 ——
否则哪天删了词条，标签页会直接显示 `app.name`（同类事故在 RiskSheet / renderNoteSegs 上都发生过）。

```
死键   2 → 0
vitest 30 → 35（新增 documentLang.test.ts 5 条）
门禁   八步全绿
```

**残余**：语言切换入口本身不存在 —— 全仓只有 `setLocale()` 定义，无任何 UI 调用它，
en 字典目前只能靠 `localStorage.c_locale` 手工设置。红线 B 的前提是"能切"，这条单独定。

详见 DEVELOPMENT §15.156。

### 2026-11-05 追加（清理 125 条死键，字典 1548 → 1423）

删掉 §15.154 核实出的 127 条中的 125 条，`app.name`/`app.desc` 保留。

`dash.*` 58 条曾担心是"卡片临时下线"，逐条比对后否掉：40 条同文案改名
（`vs_yesterday→vs_prev_day`、`proc_buy→line_purchase`、`gross→view_total`…）、
3 条换说法、15 条功能真没了（`dup_rows` 三条同文案，只剩一句注释）。整组是重构残骸。

zh/en 各删 125 行，删完断言两份键集合一致（只删 en 更隐蔽：zh 兜底，切 EN 才露馅）。

```
字典   1548 → 1423（zh/en 对齐）
死键   127 → 2（app.name / app.desc，按计划保留）
diff   各 -125 行，无其它改动
门禁   八步全绿（回归 497 / SQL 冒烟 263 ✓ / vitest 30 / i18n PASS）
```

`app.*` 保留原因：前端从未读过这两条，标题写死 `index.html` 且 `document.title` 不更新 ——
**切 EN 时标签页标题/PWA 名/分享卡片全是中文**。应先接 `document.title = t('app.name')` 再删。

详见 DEVELOPMENT §15.155。

### 2026-11-05 追加（死键核实：513 → 127，差额全是审计脚本误报）

「死键 513」这个数字从产生就不准 —— `i18n_audit.used_keys()` 只认 `t('字面量')`，
于是 `t('col.'+id)` / `t('fld.'+name)` / `t('seg.'+k)` 共 161 条活键、词表键字段
`{ l: 'rf.lf.3' }`、对象简写 `{ lit: 'rf.vars.lit' }`、后端下发的 `name_key` 全被算成死键。

改法三层：动态前缀登记为命名空间（改由 `scan_dynamic_prefix_health()` 盯产键函数本身）
+ 通用键识别（键字段 / 元组元素 / 简写 / 后端 `name_key`）+ 字面量兜底。

```
死键      513 → 127（与人工逐条核对集合完全相等）
缺键      0（中途被自己的改动报成 43 条假缺失，已修）
自检      新增 --selftest 18 条正负例，接进 preflight [8/8] 与 CI
```

127 条构成：`dash.*` 58（卡片重构弃用）／`common.*` 12／`settings.*` 11（搬进 DevMode，改 `dev.*`）／
清洗页旧前缀 8／其余 38。`app.name`/`app.desc` 特殊：前端从未消费，标题写死 `index.html`，
**切 EN 时标题不变**，删键前应先补消费点。

详见 DEVELOPMENT §15.154。

### 2026-11-05 追加（红线 B 接入门禁：i18n 审计不再只是"手跑"）

`scripts/i18n_audit.py --gate` 早就写好了判据，但 preflight 七步与 CI 都没调它 ——
三条红线里 any 有 ESLint 门禁、i18n 只有文档里一句话。补齐：

- `preflight.sh` 七步 → **八步**，新增 `[8/8] i18n 门禁`（中文硬编码残留 / zh-en 键集相等 /
  `t()` 引用缺失 / 变量遮蔽 / 空串假翻译）；
- CI `frontend` job 在 `npm test` 后加同名一步，**每推必跑**；
- 探针反证：插入 `<button title="保存并关闭">` → 第 8 步 FAIL、preflight exit 1；
  插入 `(x: any)` → 第 4 步 FAIL。删探针后八步全绿。
- 当前基线：zh/en **各 1548 键对齐**、中文残留 **0 行 / 0 文件**、死键 513（只报数不拦）。

详见 DEVELOPMENT §15.153。

### 2026-11-05 追加（期初双场景：结转 / 窗口内实盘导入）

回归 487 → **497 通过 / 0 失败**；schema 45 → 46 表（`SCHEMA_VERSION` 7→8）。

用户定的两个场景，原实现只覆盖了第一个：

| 场景 | 触发 | 本期期初 |
|---|---|---|
| A 默认 | 窗口内没导入库存 | 上月期末（结转，原逻辑） |
| B 实盘 | 上月末最后一天 ~ 当月 3 号导入过库存 | **导入值**覆盖结转，并留痕 |

关键设计：**校准的是「导入时点结存」，不是硬塞期初** —— `期初 = 导入量 − Σ净流水[本月1日, 导入时刻)`，
所以 2-3 号导入不会把 1-3 号流水算两遍；上月末导入时区间为空，期初 = 导入量。
期初优先级 ⓪（压过「上月结转」），`inventory_month.src` 逐行标 `import`/`derived`。

配套：新表 `ledger_begin_adj`（原结转/导入量/回退流水/差额/批次 + 应用时间，可审计）；
盘盈补 `9999-12-31` 调整批次、盘亏按 FEFO 逐批扣，维持 `Σ批次 ≡ 可用`；
`/ledger/status` 增 `begin_source / begin_adj_rows / begin_adj_delta / begin_window`；
用户日志新增 `ledger_override`。详见 DEVELOPMENT §15.152。

### 2026-11-05 追加（对账 key 列盲区 + 测试弹窗仓型说谎）

回归 478 → **487 通过 / 0 失败**（新增 9 条断言，含 1 条反证）。

| 主题 | 一句话 |
|---|---|
| 对账报"库里多出 3 列" | `sql_smoke.parse_columns` 只看首 token 判约束行, 列名恰好叫 `key` 的三张表被当成 `KEY idx_x (...)` 跳过 → **声明了却不校验**(真缺列会漏报)。改为"关键字后跟类型 = 列, 跟索引名/括号 = 约束" |
| 规则测试弹窗恒"未触发" | `rule_sim` 的 `inv.warehouse_type` 默认空串, 而规则上的仓型守卫判 `"" != "own"` → 直接 False。真实评估每行都有具体仓型, 空串是不存在的输入。默认落 `own`, `own.*` 也只在该行注入(与 `rule_eval` 同条件) |

补账结果：声明 45 表 / **433 列**（原 430，三列 `key` 归位）、缺失 0 / 多余 0 / 缺索引 0。详见 DEVELOPMENT §15.151。

## 2026-11-05 总览（10 月账期首次结账实跑）

回归 458 → **466 通过 / 0 失败**；i18n PASS；四次构建 Success 并上生产。
预演数字全对，真跑连挂三次 —— 三个故障都在写入路径上，不在算法里。

| 主题 | 提交 | 一句话 |
|---|---|---|
| 首结被自己拦死 | `3f8bd3d` | `state_get` 空表默认 `period=`当月，链条判断把「已结到 11 月」当事实 → 结 10 月被判跳期。`period` 语义是「已结账的账期」，默认值应为空 |
| 批次切片撞主键 | `235aa8a` | `batches` 以自增 id 为主键（同批次键多行），`batch_opening` 以批次键为主键 → `INSERT..SELECT` 必撞 1062（78 组重复）。改 `SUM(qty) GROUP BY` |
| 中间态 + 不幂等 | `235aa8a` | 顺序改为「写账期 → 切片 → 最后改 inventory」；新增 `ledger_base` 固化基线；重结同期期初取已存 `begin_qty` |
| 加列没生效 | `4b7adad` | `ensure_schema` 是版本门禁，加列必须 bump `SCHEMA_VERSION`（6→7），否则补列段根本不执行 |
| 跨月串号 | `142ced1` | 结账把「结账月」的量写进「当月」字段 → 11 月页显示 10 月的 357,637/233,985。结账只回写期初；自有仓缺流水按 0；汇总改按流水现算 |

**实跑结果**：`rows 1003 / batches 2540 / beg 377,865 / in 357,637 / out 233,985 / end 501,517 / orphan 0`；
切片 Σqty `376,673` **等于**当前可用（批次轨闭合）；11 月页 `501,517+98,515−223,359 = 376,673` 零跳变；
仓周转 `439,095×5÷223,359 = 9.83 天`（月内累计 5/30）。详见 DEVELOPMENT §15.147。

### 同日追加（规则字段单一真源）

回归 466 → **475 通过 / 0 失败**。

| 主题 | 一句话 |
|---|---|
| 静默吞规则 | `supplier.score` / `inv.turnover_days` 在白名单、在规则页下拉里，却不在 `_CALC_VARS` → 走快速路径 → ctx 无键 → 条件恒空、规则永不触发、不报错 |
| 三份列表合一 | 新建 `core/rule_fields.py`：字段只登记一次 `(kind, scopes)`，白名单/注入判定/事件作用域全部派生 |
| 反向漏洞 | `inv.locked_qty` / `order.unit_price` 在界面里能选、保存却被拒；`order.order_status` 等字段从来没注入过 |
| 校验有洞 | 原正则只认 `a.b`，顶层字段拼错（`daily_sale`）直接放行；现无点字段也校验 |
| 右侧恒假 | `_check_single` 只解析带点的 `right`，`right=daily_sales` 被当字符串比较 → 恒 False；`'own'` 带引号同样恒假 |
| 测试弹窗 | ctx 构造抽出为 `core/rule_sim.py`（路由 606→542 行），补 supplier/turnover/顶层别名 —— 否则弹窗永远显示"未触发" |

详见 DEVELOPMENT §15.148。

### 同日追加（月汇总上界 / 键盘焦点）

| 主题 | 一句话 |
|---|---|
| 月汇总缺上界 | 自有仓"本月入/出"只写 `>= 本月1日`，表里有下月/未来日期流水就会被算进本月；4 处聚合统一改 `< 下月1日`（与账期同口径） |
| 键盘焦点不可见 | 142 个原生按钮无自定义焦点样式，13 处内联 `outline:'none'` 把默认环也去掉了（登录页输入框聚焦毫无反馈）；一处 `:focus-visible` 集中覆盖，含老 Safari 与 Windows 高对比度降级 |
| 焦点环抖动 | 根因是 `button:active{transform:scale(.97)}` 带着环一起缩；全站按压反馈由「缩放」改「变暗 `filter:brightness`」，几何不变，环不动，鼠标/键盘/触屏一致 |
| 两列静态值 | `month_inbound/month_outbound` 核实**不能删**：平台仓期末/周转只能取它、导出与对账巡检在用、清洗页是可映射字段、archive 表要求列一一对应 |

### 同日追加（平台仓解绑 / Prettier 归零）

回归 **475 → 477 通过 / 0 失败**；preflight 7/7 全绿（Prettier 从长期飘红 → 合规）。

| 主题 | 一句话 |
|---|---|
| 平台仓不需要月入出 | 四个"可能的消费方"全部不成立：列集没有、导出只对 own、合计按列渲染、周转列只在 own。`insights.py` 为平台仓算的期末/平均/周转**零读者**，且因平台仓无期初列，`期初+0−0` 恒等于期初(0) → 期末恒 0 |
| 解绑 | 非 own：SELECT 不带两列、行级固定 0、`end_stock = available_qty`（导出即权威）、汇总 SQL 不 SUM 两列；前端 `totalTurnover` 加 `whType !== 'own' → null` 护栏 |
| 自有仓仍要 | 那两列是「期初+入−出=期末」的操作数，期末/平均/周转/仓周转/账期对账全挂在上头；且值来自**流水聚合**，不是 `inventory` 静态列 |
| 列不删 | 存储层保留并照写：导入原始字段 + archive 列一一对应 + 巡检对账基准。区别只是**不再有人拿它算东西** |
| Prettier 门禁 | 10 个既有文件（HEAD 就不合规）全量格式化，逐文件 diff 仅 2~9 行，纯格式 |

详见 DEVELOPMENT §15.150。

详见 DEVELOPMENT §15.149。

## 2026-11-04 总览（周转口径 / 北京时间 / 账期月结）

回归 437 → **458 通过 / 0 失败**；i18n audit PASS（中文硬编码 0）；tsc / eslint / vitest 30 / build 全绿；线上构建两次 Success 并已上生产。

| 主题 | 提交 | 一句话 |
|---|---|---|
| 库存批量删除/恢复 | `a9aafaa` `631c81a` `923b8d0` | 借订单的交互不借语义；勾选列按 auto layout 下限 32px 收窄 |
| 点一行=全选全表 | `08dc5f0` `438e35d` `b570a97` | 主键在「重建 dict」里丢了：SELECT 加了 `i.id`，但 dict 重建时没带上 |
| 迁移框架 + 两条红线 | `d11b551` | S3-5 `schema_migrations`；非内联 SQL / 不硬编码中文进 preflight |
| i18n 全量对齐 | `f8bf880` `5809e71` `1c339f1` `ccad7a7` | 4 视口×双语实机快照；列 id 集合 ⊆ `col.*` 键集合；后端中文句子结构化 `note_segs` |
| **周转改平均库存法** | `7144ceb` | `平均×自然月天数÷当月出库`；期末 = 期初+入−出（不 clamp）；合计改「仓周转」先合再除；出库 0 → 未出库不计算；负期末 → 标异常不兜底 |
| **全链路北京时间** | `7144ceb` | 新增 `core/clock.py` + `utils/date.ts`；后端 14 文件 47 处 + 前端 7 文件统一 `+08:00`；连接后 `SET time_zone`，失败抛出。**根因不是显示差 8 小时，是锁超时提前 8h 失效、效期/库龄在 00:00–08:00 算错一天** |
| **账期月结（甲+B）** | `7144ceb` | 期初 = 上月期末；可用 = 锚点 + 其后流水；三表 `inventory_month`/`batch_opening`/`ledger_state`（SCHEMA_VERSION 5→6）；批次月初余额**带日期**切片，指定批次跨月仍命中；数据不齐拒绝结账 |
| **结账两个真 bug** | `171cca0` | ① `<= 月末日` 对 DATETIME 只覆盖到当天 00:00，末日整天丢失（10-31 单日入 19,405/出 23,704）→ 右开区间 ② bootstrap 直接拿基线当月期初，漏掉基线之后的历史流水（10 月少 79,315）→ 加 `_net_before` |
| 推送通道 | `171cca0` | `github.com:443` 不可达（131s TLS 断）→ 走 `push_api.py`；修 `ls-tree` 缺 `-z` 导致中文名被 quotepath 转义、同一文件误判「新增 1 + 删除 1」的问题 |

### 线上验证（不是本地自证）

`GET /ledger/preview?period=2026-10`（dry-run，只算不写）：

```
begin 377,865   in 357,637   out 233,985   end 501,517
rows 1,003      orphan 0     days 31       available_now 376,673   diff 124,844
```

`diff 124,844` 正好等于 11 月净流水（98,515 − 223,359），即 **`501,517 − 124,844 = 376,673` ≡ 当前可用**。
结账对页面数字零跳变 —— 这是口径闭环的判据，不是「看起来对」。

`health` 返回 `timestamp +08:00`；`snapshot_qty` 列与 `ledger_state` 表已就位；`/ledger/status` 返回
`period 2026-11 / days 4 / month_days 30 / target_period 2026-10 / target_ready true`。

### 调度落点（有上限，别乱加）

`edgeone.json` 的 `schedules` **maxItems 10 已占满**，加第 11 条会让整组定时任务丢路由。
月结因此嵌进最轻的 `cleanup-logs`（每日 03:05 CST），调用点在 `_claim` 之前，4 号之后零 SQL 直接返回。
回归加断言锁死这条绑定：挪走不报错，只会让账期永远不结。

### 待办

- 11 月这次不会自动结（1–3 号窗口已过），需手动 `POST /ledger/reclose {"period":"2026-10"}`，12-01 起全自动；
- `snapshot_qty` 只存不比（差异看板未做）；
- 重结更早历史月份不支持，需整链重建；
- `cleanse_write.py` 1500+ 行，超 600 红线，账期逻辑已外置但该文件仍需拆。

详见 `docs/DEVELOPMENT.md` §15.146 与 `docs/decisions/0002-账期月结.md`。

---

## 2026-11-03 下午总览（D4/D5/D8 + 提示收口 + S3-4）

回归 363 → **392 通过 / 0 失败**；preflight 七步全绿（有凭据时含真实 SQL 冒烟）；CI success。

| 主题 | 提交 | 一句话 |
|---|---|---|
| D4 影响面驱动评估 | `ae22fdb` | 改一条规则不再跑整套每日任务：文案变更 0 评估，实质变更只评本渠道×本告警类型；全量 8.3s → 浅档 420ms |
| D5 任务可查询 | `ae22fdb` | `eval_tasks` + `/rules/eval-status`：保存返回 task_id，前端按真实状态等待，不再 `setTimeout(2000)` 猜 |
| D8 告警状态机 | `ae22fdb` | 11 个关闭入口收敛为 `close()/close_by_ids()`（带 reason/at）；同维度重开而非插新行（`reopen_count+1`，抖动可数） |
| 浅档 NameError ×2 | `42838d7` `cd9d29a` | `only_ats` 过滤后 calc 注入整块跳过，块外 4 个映射未初始化；第二次才用 AST 一次盯四个，门禁改为源码顺序断言 |
| 提示通道断点 ×2 | `5f757bc` | App 顶层 `useToast()` 是 no-op（10 处提示从未显示）；"提示+清缓存+发事件"手写三件套必漏 → `api/notify.ts` + `api/mutate.ts` |
| 操作态胶囊 | `5f757bc` | `ops` 与 `syncing` 分离（分钟级 vs 秒级，前者有 20s 强制归零会误报完成）；App 层轮询，切页不丢 |
| vitest 进门禁 | `89cc45f` | 22 个用例从未被执行，攒了 12 个长期失败；修完纳入 preflight `[7/7]` 与 CI |
| **S3-4 真实 SQL 冒烟** | 本轮 | `EXPLAIN` 对生产库做名称解析（零副作用），388 条唯一 SQL / 31 个模块；**首跑抓到 3 个真缺陷** |

### S3-1 巨型页面拆分(前两页)

| 页面 | 前 → 后 | 外提 |
|---|---|---|
| DashboardPage | 3014 → **2017** | `dashboard/format.ts` + 6 个弹窗组件 |
| RulesPage | 3407 → **2769** | `rules/shared.ts` + 4 个参数面板 |
| CleansingPage | 2398 → **1005** | `cleansing/fields.ts`（497 行纯数据）+ 映射/预览/完成/列状态弹窗四组件 |

验证方式不是看截图，是**两版对同一后端、同一 token、同一时刻比「层级\|标签\|class」序列**：看板主视图 232 节点、三个告警弹窗 1284/1090/1000 节点、规则页 164 节点、清洗映射页 125 节点、预览页 59 节点，全部逐节点一致。

> 一处误判已修正：曾在文档里写"规则页 4 个参数面板在桌面没有入口"。实测（1440 与 390 同流程）推翻——`HammerRules` 是锤子菜单组件，桌面与移动渲染的是同一份代码，同一入口，行为一致。错因是只追"谁写 store 字段"，没查"组件在哪些布局被挂载"。

> 清洗页拆分顺带修了一个只在非同源部署下才炸的缺陷：`uploadRawBytes()` 裸 `fetch(url)`，设了 `VITE_API_BASE_URL` 时上传打到前端自己的 origin → 404，被上层 catch 吞成"识别失败"。线上同源所以从未暴露。

> **预览页标题贴步骤条**：`.clean-preview-page` 的 `margin-top:-24px` 想把容器 padding 让给表格，但负 margin 压的是整块，先撞上紧邻的步骤条 —— 标题 top=116 而步骤条 bottom=126，重叠 10px（1440 与 390 同样）。回退上移、高度公式 152→176 抵消，底部按钮位置不变，代价是表格少 24px。

### CI 凭据: 不再维护第二份 TiDB 副本

| 项 | 内容 |
|---|---|
| 问题 | GitHub Secrets 与 EdgeOne 项目环境变量是两套独立副本，改一边不会同步另一边。CI 首跑 60 条 `1146` 就是这个漂移：CI 连的是另一个集群的空库 |
| 改法 | `scripts/ci_fetch_env.py` 启动时调 `DescribePagesProjectEnvs` 取 TIDB_*（只取 Production 档）写进 `$GITHUB_ENV`；GitHub 侧只留一个 `EDGEONE_PAGES_API_TOKEN` |
| 踩坑 | ① `>> $GITHUB_ENV` 会把 `::add-mask::` 一起写进环境文件 → 掩码失效；② 掩码粒度过粗（连 `4000`/`supplykit` 都打码）会毁掉排查线索；③ `.edgeone/` 被 gitignore，CI 读不到 ProjectId 就会安静回落到旧 secrets |
| 结果 | CI 日志 `数据库连接来源: edgeone`；263 条 EXPLAIN 全过、schema 对账 41 表/391 列/13 索引全在；轮换密码从改 2 处变 1 处 |
| 清理 | 删除 `CRON_SECRET`/`JWT_SECRET`（无任何工作流引用）；`TIDB_SSL` 映射项一并删除（仓库里从无此 secret，缺省即开 TLS）；`TIDB_*` 保留作降级 |

### S3-4 详解: 让测试第一次"问过数据库"

| 项 | 内容 |
|---|---|
| 问题 | 205 个用例全 mock db，`fake_query` 按 SQL **片段**匹配 —— 列名写错照样返回假行、照样绿 |
| 方案 | `EXPLAIN <stmt>`：只做名称解析与计划，不读行、不加锁、不执行；实测 `EXPLAIN DELETE` 前后行数不变（17006） |
| 覆盖 | 388 条唯一 SQL / 31 个后端模块；263 条通过，125 条 DDL/SHOW 由 `--schema-diff` 覆盖 |
| 定位 | 报错带调用点（`core/rule_eval.py:349`），不是只报"Unknown column" |
| 零误报 | 仅名称解析类错误码判失败；参数形状不符先 NULL 填充重试；其余记 ⚠ 由人判断 |
| 防假绿 | `_MIN_SQL=200` 覆盖率下限 + 4 条静态断言（注入点必须带 `_A()` 包装） |
| 防错库 | 库指纹（inventory/orders/rules 三张核心表）：CI 首次跑时 secrets 漏传 `TIDB_DB` → 60 条同构 1146，加指纹后只报一条根因 |

抓到并修掉的三个真缺陷：

1. **`core/rule_eval.py` 采购在途恒 0**（1054 被 `except` 吞）—— `purchase_orders` 没有 `qty/received_qty`。
   同一个错 11-02 已在 `dashboard_own_health` 修过并留了断言，**这里漏了**。改为与库存导入同源，`can_ship` 不双计。
2. **`routes/cron.py:478` 批次对账多一个右括号**（1064）—— 每日任务走到这里必失败，被记成"降级"，
   **这段对账上线以来一次都没跑成过**。
3. **3 处把 `str` 当 params 传**（`routes/cleansing.py` / `dashboard_maintain.py` ×2）——
   `"..." % (...)` 求值成字符串，pymysql 逐字符当参数 → `not all arguments converted`，
   2026-10 加的诊断日志**从未落库**。

> 共同特征: 全部被 `try/except` 吞成"降级"，线上不报错、数字恒 0、日志里只有一行警告。
> 这正是 §15.126 说的"功能从未生效"型缺陷 —— mock 测试对它们完全失明。

---

## 2026-11-03: 项目技能 SKILL.md 重写

| 主题 | 提交 | 一句话 |
|---|---|---|
| 项目技能 | 本轮 | `docs/SKILL.md` 由 9 月 Makers 初版重构为当前入口：校正六处硬冲突（路径/回归 358/22 路由/无 backend/生产免签/push_api）、保留框架十坑、新增七步闭环与失败黑名单 12 条 |
| 导航 | 本轮 | `CONTEXT.md` 索引首位 + `README.md` 架构行加 SKILL 入口；`WBY_WORKFLOW.md` 回归数 184→358 |
| cron 时间窗 | 本轮 | 8 个重任务加 `_off_window`：窗口外**零 SQL** 直接 skipped，把"随时可触发"收敛到"每日窗口内 + 当日一次"；窗口边界来自 `pipeline_runs` 实测（平台触发稳定早 57~60s） |
| 密钥审计自动化 | 本轮 | `audit_secrets.py` 默认扫 **git 全历史**（blob + commit message + reflog 脱分支），输出脱敏；CI 加 `secrets` job（`fetch-depth: 0` + 每周一）→ "只能人工复查"不再成立 |
| 安全定案 | 本轮 | `security.md` 重写为「已根治 / 平台终态 / 泄漏责任」三段；`X-Auth-Token` 标终态 + 30 秒验证法；回归 358→363 |

---

## 2026-11-02 全天总览（29 个提交）

> 本节是索引，细节见下方各条。回归 358 通过 / 0 失败；preflight 六步全绿；CI success。

| 主题 | 提交 | 一句话 |
|---|---|---|
| S2-8 启动门禁 | `1f031fa` | schema 声明收敛单一真源 + 版本门禁，冷启热路径一条 SELECT 零 DDL |
| S2-9 冗余清理 | `db702b0` `d25fead` | 4 个实证死代码删除；三套补列合并 `_COLUMN_ADDS`；M0/M1/规则自愈四段整体退役（7 条空转 SQL 归零） |
| CI 修复 | `d93d7ad` | 审计规则两条断言失效（版本段参数误判为维度 / DATE_FORMAT 只扫单文件）→ 修；CI 自此全绿 |
| 渠道页样式 | `d93d7ad` | 7 处内联 → 0，上提全局 `.form-*` 基元，全走现有变量 |
| 历史页控件收敛 | `58cb20b` `058676e` | 区间/粒度进锤子菜单，store 同源；短文案/去多余按钮/间距对齐看板 |
| 交互完整性 | `9f6ecd6` | 停用卡末行分割线去除；骨架/淡出/点击态补齐；历史页接入全局同步胶囊 |
| 归档页二次收敛 | `7a60484` | 头部去卡片壳；「含 1 年以前」删（实为第 366 天）；归档状态进锤子菜单底部；来源行去冗 |
| 同比跟随区间 | `cd4c938` | `history_yoy` 收 `start`（同窗口/同 400 天截断/key 含 `_s`）；卡片标题显示真实区间，"非 GMV 口径"胶囊上提标题行 |
| 文档 | 本轮 | DEVELOPMENT §15.125、README/ARCHITECTURE/CONTEXT 数字与描述校正 |

---

## 2026-11-02: 400 天截断提示间距修正(实测驱动)

> Playwright 实测: 构造 2024-01-01 ~ 2026-11-02(跨度 673 天)触发提示并逐项量间距

- 修前: margin -2px 0 6px → 与上方摘要行只隔 2px, 贴成一行像摘要的一部分
- 修后: margin 4px 0 8px → 上 4(与摘要分离)/下 8(与 segmented 恢复呼吸感),
  左右 0 与摘要行对齐(labelLeft == hintLeft 实测一致)

## 2026-11-02: 历史归档页首屏只剩标题(区间/来源也移入锤子菜单)

> preflight 六步全绿; Playwright 实测: 页面头部 innerText = "历史归档" 四个字

- `hist-meta`(区间行 + 来源行)整块删除 —— 区间在锤子菜单 header 摘要(10-04/11-02),
  来源段信息随区间一并由菜单表达, 页面重复写第三遍属纯冗余(用户点名)
- 400 天截断提示不丢: 移到锤子菜单区间摘要下方(.hist-clamp-hint, 跨度>400 才出现),
  前端按 start/end 算跨度做提示, 裁剪仍由后端 _MAX_SPAN 决定
- 死代码清理: readSeg 兼容层 / srcText useMemo / .hist-meta 样式

## 2026-11-02: 历史归档页归档说明彻底移出页面(只留锤子菜单)

> preflight 六步全绿; Playwright 端到端实测(demo 登录+线上 API)

- 页面上一处不留: 头部 note 行删除 + 图表空态兜底说明删除(.hist-empty 只留"该区间没有数据")
- 页面不再请求 /history/coverage(3 个请求, 少一次往返); 锤子菜单自己拉自己渲染
- 死样式清理: .hist-empty-sub

## 2026-11-02: 历史归档页头部/归档状态二次收敛(交互去冗)

> preflight 六步全绿; Playwright 端到端实测(demo 登录+线上 API)

- 头部去掉卡片壳: 标题+覆盖摘要+区间改为裸排版(.hist-headcard, padding 与 card 对齐),
  其余内容卡片不变 —— 首卡片 4 个 card 里唯一没有可操作内容的, 卡片套标题属冗余层级
- "含 1 年以前"删除: 归档线由后端 cut_days 决定, 写死"1 年"在边界日会与真实规则对不上
  (cron 判定是严格小于, 实为第 366 天才搬); 归档口径整体移入锤子菜单的归档状态
- 归档状态落锤子菜单底部(.hist-arc): 状态 pill(已归档=success 绿/暂无归档=中性灰)
  + 一行人话说明(有归档→覆盖区间; 无归档→"订单自 {d} 起, 满 {n} 天后自动归档",
  {d} 由后端新字段 archive_day 给出, 前端不再自己算时区); stale_orders>0 时追加待搬移行
  间距: 上 12(分组间距)/下 4/左右 2(与自定义面板标签缩进对齐), 每次打开拉一次
  coverage(与页面同 300s 缓存键, 不多打库)
- hist-meta 去冗: 来源行不再重复日期区间(与首行逐字重复), 改为"来源 归档 N 天 · 订单 M 天"
- styles.css 死类清理: .hist-sub/.hist-row/.hist-row-label/.hist-pill/.hist-date
  (随控件迁入锤子菜单后页面已无引用)

## 2026-11-02: 历史归档时间控件收敛到锤子菜单(交互联动)

> preflight 六步全绿; Playwright 端到端实测(真实登录+线上 API)

- 布局照抄看板页锤子菜单: hammer-header → 区间摘要 → hammer-segmented(近7/30/90天/自定义)
  → hammer-panel(自定义日期+确认) → hammer-segmented(按日/按月) → hammer-btn-row(打开归档页)
- 状态全部落 store(histPreset/histStart/histEnd/histBy, 按渠道存 localStorage):
  锤子面板与页面同源读写, 关面板即按新条件重查; 自定义日期为组件本地草稿,
  点「确认」才提交(避免每选一天就重查一次, 且取消不污染已生效区间)
- 页面卡片内控件改为只读回显 pill + 「调整」按钮(经 store hammerOpenReq 信号打开锤子面板,
  自增计数保证连续点击每次都触发); 非法区间(起>止)确认按钮禁用并提示
- 切渠道时按渠道恢复历史区间(与看板周期同规则), 页面 daysAgo/today 死代码删除

## 2026-11-02: 渠道管理页样式上提 + CI 审计修复(S2-9 收尾)

> 回归 355 通过 / 0 失败; 审计 11 通过 / 0 失败; preflight 六步全绿

- ChannelsPage.tsx 内联样式清零(7 处 → 0): 上提为全局表单基元 .form-page/.form-grid/
  .form-field/.form-check/.form-input, 全部走现有 CSS 变量(--font-13/--muted2/--radius-md/
  --card/--border), 新增 --space-xs(4px)/--space-md(16px) 补齐间距刻度; 供后续页面复用
- Playwright 实测 computed style 与原内联逐项一致(含截图); React 冗余默认导入清理
- CI 修复: ① test_audit 缓存 key 校验误报(stock_risk 的 _CV 版本段参数未被识别为非维度)
  ② history.py 四处 _cache_get 直传字符串 key → 统一走 _key 变量(全站惯例)
  ③ index.py 迁移段退役后 DATE_FORMAT 检查恒真 → 改为全库同口径

## 2026-11-02: 冗余清理 + 补列统一 + 启动探测统一(S2-9)

> 回归 355 通过 / 0 失败; preflight 六步全绿

- 死代码删除(AST 零引用扫描+人工核实): db.py esc_ident / suppliers.py _norm_order_status / cleansing.py _after+_detect_columns
- 三套并存补列统一为 schema_create._COLUMN_ADDS 单一声明, 受版本门禁管(热路径零列探测)
- M0/M1 迁移与规则修复/退役四段**整体退役删除**(线上实证标记齐全/规则目标态 → 使命完成, 冷启动 7 条探测 SQL 纯空转); 90天物化重建/69码补齐/规则恢复的复用做法写入 docs §15.123 第三节备用
- index.py 注释与临时计时注释同步收口

## 2026-11-02: 启动 DDL 版本门禁 —— 热路径一条 SELECT, 零 DDL(S2-8)

> 回归 349 通过 / 0 失败; preflight 六步全绿

- 旧启动每次冷启动发 ~50 条幂等 DDL(38 建表 + 15 索引/补列); IF NOT EXISTS 在 TiDB 上仍走 schema 路径
- 用户定案"从表源头解决": index.py 内联的 12 张运行表 + 三组索引声明全部收敛进 schema_create.py
  (单一真源); 以后加表/索引 = 改声明 + bump SCHEMA_VERSION, 不再写迁移检查代码
- `ensure_schema()` 版本门禁: 版本一致 → **一条 SELECT 短路, 零 DDL**; 落后/首启 → 幂等全量,
  **全部成功才写版本**(任一失败不写, 下次冷启动重试); 并发用 GREATEST 防版本回退
- 数据迁移(M0/M1/ext_json 补列)留 index.py(有自身标记且非 DDL); 3 条无条件补列收进门禁
- 回归新增 5 条; 踩坑记录: 测试须同时 patch schema_create.execute(模块级绑定), 否则注入不生效

## 2026-11-02: 库存删除可恢复 —— 归档表快照(S2-7)

> 回归 343 通过 / 0 失败; preflight 六步全绿

- 先钉事实: inventory 删除原是**硬删**(`DELETE FROM inventory WHERE id=%s`), `deleted_at`
  无写入路径(软删行 0 条), "恢复删除"功能本就不存在 —— 此前登记的"重算不过滤 deleted_at"
  待办前提有误, 已修正
- 用户定案口径: **恢复删除 = 复原删除时的快照**
- 方案选**归档表**而非软删: 60 处读库存 SQL 零改动、唯一键 uq_inventory_biz 不被已删行占位、
  导入语义无两难
- 新增 `inventory_archive`(与 inventory 同列序 + archived_at, 原 id 作主键);
  DELETE 先快照后删(rowcount 判存在, 不会"删了没存");
  `GET /inventory/archive` 归档列表; `POST /inventory/{iid}/restore` 原样插回,
  同业务键已重新导入/原 id 被占 → 拦截并报错可读
- `_recalc_own_inventory` 补 deleted_at 过滤(防御); 前端删除文案改为如实描述
- 语义: 快照管恢复那一刻; 恢复后再次导入会按最新流水重算覆盖
- 回归新增 8 条(S2-7, 含 4 条动态)

## 2026-11-02: 冷算耗时收口(往返 22→14 / 23→16 / 10→7, 口径逐字段不变)

> 回归 334 通过 / 0 失败; preflight 六步全绿

### 一、结论先摆: 不是加缓存, 是减往返
线上 `perf_debug` 是 "查询 0.3s / 组装 1.15s", cProfile 一照 `_assemble` tottime **0.015s**、
cumtime **6.15s** —— 组装阶段自身只算 15ms, 6.1s 全是它内部 21 条串行 SQL 的往返(TiDB 跨区, 单次 ~57ms)。
加缓存/拖 TTL 只会把慢藏起来(首个请求依旧慢, 且 TTL 越长数据越旧), 因此本轮只动**取数方式**。

### 二、合并同源往返(口径逐字不改)
- alerts/products/suppliers 三个 COUNT → 一条语句三个标量子查询(同快照)
- 月度回款 总额/按日/按店 三次扫同窗 → 一次 `GROUP BY DATE(arrived_time), shop`, 内存派生
- 品牌 GMV 五次(60天+today/week/month/custom) → 一次 `GROUP BY DATE(ordered_at), brand`, 内存切
- `_assemble` 改为接收预取值 `_counts/_flows/_brands_raw/_cfg`(调用方并行池已取好):
  全部注入时组装阶段**零 SQL**; 缺失时仍自行查询(自定义区间直查路径不变)
- 看板物料 12 列 → 5 列(9003 行: 1.44s → 0.45s, 差的是传输量)
- `sku_sales_stats` 建表 DDL 进程内只发一次(原每屏 2 次)
- 自有仓 products/批次去掉 800 分片(实测仅 1000 / 1696 行)
- 删除已无调用点的 `_payout_flows()`(回款并入上面那条)

### 三、等价性(逐字段比对, 非目测)
旧实现从 HEAD 导出后与新版双跑同一份生产数据:
- summary jd / other: 10 个顶层键**完全一致**(jd 118091 单, GMV 54515859.25)
- aux jd/other × bbcc/traditional: 四组完全一致(含顺序)
- own_health jd / other: 数值与集合一致

唯一差异是**并列定序**: 旧实现靠 SQL 返回顺序, GMV 并列(退款抵消为 0)时品牌顺序抖动;
自有仓告警同分项先后随机, 而 `full=0` 时 `[:limit]` 会截到不同成员。现补稳定次级键
(品牌名 / sku+码+仓), 可复现且数值不变。

### 四、回归
新增 9 条(S2-6), 其中 2 条动态: 六个预取全注入时把 `query/one` 换成抛异常的桩, `_assemble`
仍跑完且**零 SQL**; 注入值直接生效(products=2 / suppliers=3 / month payout=123.45 来自入参)。

### 五、线上实测(部署后强制 cache miss, 三轮)
| 阶段 | 改前 | 改后 |
|---|---|---|
| 预取查询 | 0.30s | 0.50 / 0.53 / 0.57s |
| 组装 | 1.15s | 0.04 / 0.04 / 0.04s |
| 冷算合计 | 1.45s | **0.54 ~ 0.61s**(-60%) |
| HTTP 端到端 | — | 2.02 / 2.27 / 2.38s |

线上 summary 与本地同版本**逐字段 0 差异**; aux(jd/bbcc)4.84s / 143KB 也逐字段 0 差异;
自有仓健康 total 89 / 缺口 7 / 积压 51 / 效期 31 与 S2-1d 定案数一致。

## 2026-11-02: 库存联动 `_adjust_inventory` 键补齐(写侧, 与 15.118 同一类)

> 回归 325 通过 / 0 失败; preflight 六步全绿

- deltas 键 `(sku, 仓, 仓型)` → `(sku, 69码, 仓, 仓型)`, 兼容旧三维(缺码按空串, 不猜码)
- `_cur_map` 键升五维(含 channel + 仓型), 同键先合并再更新(四维/三维混传不互相覆盖)
- 命中查询排除软删行(`deleted_at`): 增量加到看不见的行 = 用户看不到变化
- INSERT 补 `barcode`, 且 `beginning_stock` 与 `available_qty` 同置 —— 否则
  `_recalc_own_inventory`(`available = beginning + Σ入 − Σ出`)会把增量抹成 0
- 评估上下文复用同一次查询结果(原逐 SKU `one(... LIMIT 1)`: 多码串行 + N 次往返),
  逐行 `(sku, 码, 仓)` 一条, 与告警去重键同粒度
- 该函数当前无调用点(出库联动走 `_recalc_own_inventory`), 线上行为零变化 —— 属"启用前先把键补对"
- 回归新增 9 条(S2-1e), 5 条动态断言: 多码各扣各的 / 旧三维键落空码行 / 新行带码且 beginning 同步 /
  上下文用更新后的量 / query 只调 1 次

## 2026-11-02: 自有仓「采购在途」口径定案 + 组合键对齐

> 回归 316 通过 / 0 失败; preflight 六步全绿

### 一、采购在途只认库存导入(自有仓)的 in_transit_qty
- 旧实现叠了两份: `inventory`(own) 的 `in_transit_qty` + `purchase_orders` 的
  `SUM(qty-received_qty)`。后者字段不存在(表里只有 suggested_qty/actual_qty/arrival_date),
  每次 `Unknown column 'qty'` 被吞 → 恒按 0, 且与导入值语义重复(同一批货双计)
- 口径: **以导入类型=库存、自有仓维度、导入填写的为准**; 可发 = 自有可用 + 采购在途

### 二、组合键三处没对齐(一处造成 20 条误报)
- 自有仓聚合压扁到 SKU → 多码串行、仓名只留第一个 → 改为 `(sku, 69码, 仓)` 粒度
- 批次效期 `JOIN inventory` 只按 `sku+channel` → **把 B 仓/platform 仓批次当成自有仓**,
  取"最早到期"取到别的仓 → 线上 51 条效期里 20 条属此类(SKU-0442-J: 集货仓 exp_date 为空,
  却报 B 仓 2026-10-17 批次待报损)。改为直接查 own 仓批次 + `_pick_batch` 三级降级
- 告警行的码取自 products 档案码(单值) → 多码时记到别的码名下 → 改用库存行真实码
- 日销按同码取, 走 `pick_ds`(只有码可降级); 新增 `get_sku_sales_stats(barcode="all")`

### 三、实测(线上 jd 同一份数据)
- total 110 → 89; 效期 51 → **31**(20 条误报); 积压 52 → 51; 缺口 7 → 7
- 31 条效期全部能对到同码同仓的 own 批次; "仅靠在途才够"的 2 个 SKU 均未误报缺口
- 耗时 8.8s → 7.2s(日销合并一次往返 + 分片 300→800)

## 2026-11-02: 缓存按域失效(S2-1) + 日销三窗口增量重建

> 回归 305 通过 / 0 失败; preflight 六步全绿

### 零、收尾: 全量重建漏建快照（S2-1b，线上实测暴露）
- **现象**: 删掉两笔测试订单 → `agg_dirty_days` 正常消费、`agg` 合计正常, 但
  `cron/agg-sweep?hours=48` 返回 `mode:"full"` 之后 `daily_sales_snapshot` 仍留 1 行、
  `sku_sales_stats` 仍 2 行 → 补货/采购继续读旧日销
- **根因**: "脏日期 > 80 天"的全量分支 `_rebuild_day_agg(90)` 是**单条 upsert**:
  ① upsert 删不掉软删订单留下的组合(agg 与快照都是组合粒度);
  ② 只重建 agg + 日销三窗口, **压根没重建快照**。而这条分支恰恰只有删单/大批补录才会触发
- **修法**: 去掉分叉 —— 全量 = 增量 + 不设批次上限(`_cap=_pend+8`, `_rounds=1`, `_budget=24 段`)。
  循环体完全共用: `_rebuild_agg_range` → `_rebuild_snapshot_range` → `rebuild_sku_stats_for_days`
- **不假装完成**: 段预算用尽→余下留队列 + `agg_dirty` 保持置位 → 看板继续降级直查(慢但准)
- 回归新增 8 条(S2-1b): agg 区间与快照区间必须一一对应; 400 天时预算用尽后 `left>0`

### 零之二、硬删订单的日销孤儿行（S2-1c，同一类问题的第二段）
- **现象**: 快照残行已随 S2-1b 回收, 但 `sku_sales_stats` 里 `ZZ-S21-SKU2` 仍 2 行
- **根因**: 该表"有销量才建行"。软删能自愈(行还在 orders → 按日反查得到 → 先删后建);
  **硬删**之后 SKU 既不在 orders 也不在快照 → 任何"按受影响 SKU 重建"都反查不到 → 永久残留
  → 补货/断货/健康继续按已删订单的日销算
- **修法**: 换判据 —— `日销表 SKU - (快照窗口 ∪ 订单窗口)` 差集即孤儿, 分片删除
- **省成本**: 只在出现删单类原因时扫(`agg_has_delete`, 且必须在出队前问, 否则 reason 随行消失);
  每日对账线程再兜底一次(直接改库/漏标记的唯一出口)
- 线上预演: jd 971 个 SKU 里恰好 1 个孤儿、other 渠道 0 个(判据无误伤)。回归再补 6 条(S2-1c)

### 一、改一个供应商不再打掉全站缓存（S2-1）
- **问题**: 35 个写入口共用 `invalidate_all()`(`DELETE FROM analysis_cache`), 改供应商联系人
  也会把看板/规则/采购/补货全部冷算 —— 采购/补货 builder 各 2~3s 且扫 5 万行, 谁都白付一遍
- **做法**: `_FAMILY_DEPS` 声明「缓存族 → 依赖的数据域」, 写入方只写 `invalidate("<域>")`
  - `suppliers` → 只清 `dash_summary`/`dash_aux`/`stock_risk`
  - `purchases` → 只清 `purchase`；`config` → 不动 orders/history/accel/sales_digest
  - `orders` → 9 族全清(订单确实是根)
- **两道兜底**: 域名没登记 → 按 `all`；缓存族没登记 → 任何写入都清它(新键忘了登记也不会脏)
- **墓碑 + 写入令牌**: 失效从 DELETE 改为 `created_at=1970` + `tok=失效时刻`, 落表带 `tok` 守卫
  (`IF(VALUES(tok)>=tok, ...)`) —— 堵住"重建期间被失效, 旧值又写回去, 脏满一个 TTL(采购 300s)"
- `stock_risk` 手写缓存块并回 `cache_get`(键族归首 `stock_risk|c1|...`, 否则域级匹配不到);
  11.6MB 体积上限改成 `max_bytes` 参数

### 二、附带修一个更严重的: 日销三窗口从 S2-2 起没再重建（S2-1）
- S2-2 删掉"写操作后全量重建 90 天"(5000 SKU ≈ 22 万 RU)却没补增量 → 线上
  `sku_sales_stats.updated_at` 比 `orders.synced_at` 晚一天, 而补货/采购/断货/健康全读它
  → 缓存失效做得再好也是"新瓶装旧数"
- 新增 `rebuild_sku_stats_for_days`: 脏日期 → 反查受影响 SKU → 只重算他们(窗口仍 90 天);
  >2000 SKU 直接全量。挂在 `_drain_agg_dirty`, 大范围补录走全量分支
- 配套: `_rebuild_sku_sales_stats` 的 DELETE 支持按 SKU 子集(原实现无条件删整渠道,
  子集重建会把其余 SKU 清空 → 补货当场只剩这一批)

### 三、联动补全
- 供应商 `score` 变更 → 触发规则重算(评分是 `supplier.score` 条件与断货 OTIF 的输入)
- 补货参数保存 → 触发规则重算(前置期/安全天数是动态安全线的输入)
- 规则后台评估完补一次 `invalidate("rules")`(原来调用方的失效发生在评估之前 → 告警变了看板还旧)

## 2026-11-02: 订单业务键四维化(重导幂等) + 增量重建 + 历史归档页

> 当日 S2-2(增量重建按业务日期)/S2-4(历史归档出口)/S2-5(订单四维业务键); 回归 267 通过 / 0 失败

### 一、订单业务键: 二维(order_no,sku) → 四维(channel,order_no,sku,barcode)（S2-5）
- **起因**: 复盘时把"重复导入"记成"orders 没有唯一键, 会翻倍", 用户反问"前端不是做了去重吗"。
  查 `SHOW INDEX` 发现唯一键一直在(`uq_orders_no_sku`) —— 不会翻倍, 第二次导入是**整批 1062**
- **真正的错**(方向反了): 键比业务键窄。①不含 barcode → 同单同 SKU 换 69 码时第二行 1062 →
  单行降级计入 failed → 数量金额**静默少算**, 提示只有 `Duplicate entry`; ②不含 channel →
  跨渠道同号互阻(gmv_flows 修过同款); ③软删后重导撞键
- **修法**: 写入层加 `ord_upsert`(同键全列覆盖 + `deleted_at=''` 恢复) → 新键
  `uq_orders_biz(channel,order_no,sku,barcode)` → 删旧键。迁移幂等, 先建后删(不留裸奔窗口),
  建键前先合并四维重复(线上 0 组, ALTER 实测 11.2s / 179,061 行)
- **三处配套**(少一个就出新的静默错):
  `_align_order_keys` 写前对齐码(防"档案补码→同一行分叉成两行")、
  旧业务日期并入 `_days | _old_days`(防 upsert 换日期后两天各算一次)、
  订单移出入口查重(否则覆盖逻辑成死代码 —— 与 2026-11-01 库存那次同类错误)
- **性能**: TiDB 实测 2000 行纯 INSERT 2.14s vs upsert 1.03s —— 注释里"ON DUP 慢 10 倍"来自
  旧表结构, 不适用
- **验证**: 独立库真 SQL 跑 8 个场景(重导/id 保持/换码/软删恢复/换日期/补码/缺码/多码), 全过

### 二、其余两项见 docs/DEVELOPMENT.md
- S2-2 增量重建按业务日期: §15.114(队列 + 合并区间 + synced_at 兜底扫描)
- S2-4 历史归档出口: §15.115(四端点 + 同比 + 页面内联样式归零)
