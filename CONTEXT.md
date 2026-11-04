# CONTEXT.md — 新会话引导文件

> **给 AI 助手**: 用户在新对话中提到 supplykit / 供应链系统 / 看板 / 规则 / 导入等关键词时,
> **先读完本文件**, 再按需读 `docs/DEVELOPMENT.md` 对应章节。读完即可接续工作, 无需用户复述背景。

## 项目是什么

供应链管理系统(进销存 + 看板 + 规则引擎 + 数据清洗导入), 部署在 EdgeOne Makers:

- 线上地址: https://supplykit.top (登录 admin/admin123)
- 仓库: https://github.com/Overtrees/Supplykit-edgeone.git (分支 `feat/edgeone`)
- 本地路径: `/workspace/supplykit` (前端 `frontend/`, 云函数 `cloud-functions/api/`)

## 必读文档索引

| 主题 | 位置 |
|---|---|
| **一屏入口(口径/坑位/七步闭环/失败黑名单)** | `docs/SKILL.md` |
| UI 设计规范(小卡/大卡/弹窗/图标/数字格式) | `docs/UI_DESIGN.md` |
| 开发经验与根因记录(按天追加) | `docs/DEVELOPMENT.md` §15.x |
| 2026-11-01 全天 56 提交总览(18 条问题→根因→验证) | `docs/DEVELOPMENT.md` §15.105 |
| 低库存卡明细丢失三层根因(最顽固 bug) | `docs/DEVELOPMENT.md` §15.104 第五节 |
| 自有仓为什么不能用低库存口径 | `docs/DEVELOPMENT.md` §15.102 |
| 库存导入职责边界(期初/在途 vs 批次) | `docs/DEVELOPMENT.md` §15.98 |
| 69 码不猜: 补码两道闸 | `docs/DEVELOPMENT.md` §15.97 |
| 定时任务(平台 schedules, 不是 GitHub Actions) | `docs/DEVELOPMENT.md` §15.101 |
| 门禁转绿 + 三处「try 吞 NameError」潜伏 bug | `docs/DEVELOPMENT.md` §15.106 |
| 低库存卡口径: bbcc=BC 合计 / traditional=逐仓(轮转只服务后者) | `docs/DEVELOPMENT.md` §15.109 |
| S0 止血: cron 执行次数护栏 / 连接池名额 / traceback 收口 / 前端泄漏 | `docs/DEVELOPMENT.md` §15.110 |
| 凭据来源 + 只能走 GitHub API 推送(附踩坑) | `docs/DEVELOPMENT.md` §15.107 |
| S2-8/S2-9 启动优化(DDL 版本门禁/补列统一/迁移退役) | `docs/DEVELOPMENT.md` §15.122 / §15.123 |
| 2026-11-02 午后 UI 批次(归档页二次收敛/渠道页样式上提/CI 修复) | `docs/DEVELOPMENT.md` §15.124 |
| **安全: 已根治 / 平台终态 / 轮换步骤** | `docs/security.md` |
| 安全三件事能不能根治(cron 时间窗/历史扫描/认证头定案) | `docs/DEVELOPMENT.md` §15.127 |

## 关键口径(不可破坏的业务约定)

1. **自有仓(集货仓)不产生销售** — 低库存/断货口径在自有仓恒不触发;
   自有仓用供给侧三口径: 调拨缺口 `own_transfer_gap` / 积压 `own_overstock` / 效期 `own_expiry_risk`
2. **补货模式**: bbcc(B→C 一盘) / traditional(C 仓); 日销取值统一走 `biz.sales.pick_ds`, 绝不拿全国合计兜底
3. **告警是「SKU × 仓」粒度** — 同一 SKU 在多仓各一条; 卡面展示需去重, 弹窗展示全量
4. **计数与列表分离** — 卡面 `(N)` 用后端独立 COUNT(`alertCounts`), 列表可能截断, 二者天然不等
5. **库存月汇总** = 仓型×SKU×69码×仓 聚合, 先清零再覆盖
6. **效期管理三态** — `expiry_managed` 是/否: 是→FEFO 精确批次, 否→到货序 FIFO

## 工作流程铁律

- **两条代码红线(2026-11-04 用户定案, 准入条件)**: 详见 `docs/SKILL.md` §11
  - **A 非内联优先**: 新增/调整功能的逻辑落独立文件, 页面/路由只编排; 禁止继续把新逻辑堆进大文件
    (信号: `.py`>600 行 / `.tsx`>500 行 / 单函数>80 行 / 一个文件出现第二套职责)。按职责拆, 不为行数拆。
- **B 不硬编码中文**: 界面文案一律 `t('key')`, 新增 key 必须 zh+en 双份且键集合相等;
  后端 `error` 属诊断文本可用中文, 但不能是唯一用户提示
  - **判据: "界面的说法" vs "世界的名字"**。是界面说法 → 翻; 是世界的名字(商品名/仓库名/
    渠道名/订单状态值/规则名/告警模板) → 不翻, 翻了下游匹配就断。见 `docs/DEVELOPMENT.md` §15.145
  - **词表化后必须补解析函数**: 字典存词条键时(`DOC_TYPE_NAMES`/`STATUS_LABEL`/`INV_COLS`),
    必须有 `docTypeLabel()`/`statusLabelOf()`/`colLabel()` 这类出口, 否则键没人渲染
  - **动态拼键是审计盲区**: `t('col.' + id)` 这类拼出来的键, 静态引用检查看不见。
    加列/加字段时必须同步加词条, 靠 `i18n_audit.py` 的 `scan_col_ids()` 兜住
- **i18n 的三个"静默坑"**(都不报错, 只能靠实机发现):
  - 文案漏 `{n}` → 数字被丢弃, 界面只剩单位(「万单」)
  - en 写成 `''` → `t()` 回落中文, 英文界面冒中文(「单 / 件」)
  - 英文比中文长 60% + 固定宽容器 + `overflow:hidden` → 硬裁且**不产生横向溢出**
- **码值不要 `word-break: break-all`**: SKU/条码/单号不是自然语言, 用 `nowrap`(或按 `-` 断)。
  `break-all` 会把 13 位条码逐字折行, 把行高从 58px 撑到 244px
- **`nowrap` 必须配 `overflow:hidden`**: 只写 `nowrap` 而 `overflow` 保持默认 `visible`,
  文字比格子宽时会**画到相邻列上直接叠印**(订单页 69 码事故: 订单号 164px 撞 `max-width:140px`
  的格子, 右溢 35px 压在条码字符上)。码值列宽度用 `components/hammer/codeCell.ts` 的
  `codeColWidth()` 按该页最长值算, 不要再写死 `max-width`
- **store 是店铺, warehouse 是仓库**(2026-11-04 用户定案): 两列语义不许混用。
  改列头时 `locale` 的 `col.store` 与 `components/colLabels.ts` 的回落表**两处都要改**,
  只改一处会在词条缺失时把错语义带回来
- **列表页宽屏自适应**: `Group/Row` 型页面(设置/开发者/回收/渠道)统一用 `.form-page` 容器,
  ≥1024px 自动两列; 不要再写内联 `maxWidth: 500` —— 那会让桌面端永远是竖屏窄条
- **换机先自检**: `python3 scripts/env_check.py` —— 缺 .env / EDGEONE token / push 凭据会一次性列出, 别等 push 才发现
- **先本地, 再线上**: 逻辑验证用 `python3 scripts/local_api.py`(本地直连同库), 推一次 2~3 分钟且云函数 120s 硬限
- **部署判据**: `scripts/deploy_status.py` 输出 `Commit == 本地 HEAD && Prod == yes && Status == Success` 才算部署完成;
  若本机走 `push_api.py`(见下), 本地 SHA 与远端不同 —— 改判「远端 HEAD 与本地 HEAD 逐文件一致」(脚本会自检)
- **先看数据源实际返回什么**(拦截响应看真实分布), 再决定改前端还是后端 — 不要拿前端兜底掩盖口径问题
- **本地回归**: `cd cloud-functions/api && python3 local_test.py` → 当前 437 通过/0 失败
  (目录必须在 `cloud-functions/api/`, 否则 `routes/cron.py` 找不到); 前端 `cd frontend && npm test` → 22
- **多视口 UI 验证**: Playwright 实测, 参考 `scripts/ui_own_card_measure.py`; 视口 375/390/844/768/1024/1440
  - **i18n 专项快照**: `python3 scripts/ui_shot_matrix.py --serve --locale zh|en --viewport 375|390|844|1440`
    (可分批跑, report.json 自动合并)。三个必踩坑已在 `scripts/ui_preview_env.py` 里处理:
    `service_workers='block'`(dist/sw.js 会给 /api 返回 offline 假响应)、走 `vite preview` proxy
    (线上未开 CORS)、登录后补 `c_welcome_seen`(否则拍到的全是引导页且全报 ok)
  - **单元格叠印专项**: `python3 scripts/ui_overlap_check.py --serve --page all --viewport all --locale both`
    逐单元格比「文字实际宽 vs 内容区宽」, 抓的是"文字压到邻列"。
  - **只查 `scrollWidth` 不够**: ①固定宽 + `overflow:hidden` 的容器会"静默裁切", 溢出为 0 但内容没了;
    ②文字溢出到相邻单元格**不会让 table 的 scrollWidth 变大**, 只是画在别人头上(叠印事故就是这样漏掉的)。
    文案最长的语言一定要眼睛看一遍截图
- **文档同步**: 每项改动在 `docs/DEVELOPMENT.md` 追加 §15.10x, 写根因 + 实测数据

## 常用命令

```bash
# 环境自检(换机/新会话第一条命令)
python3 scripts/env_check.py
# 推送(github.com:443 在本沙箱不可达, 走 API; 一次一个 squash 提交)
python3 scripts/push_api.py -m "fix(xxx): ..." 
# 部署状态
python3 scripts/deploy_status.py
# 本地直连测试
python3 scripts/local_api.py GET /health
# 前端构建
cd frontend && VITE_API_BASE_URL= npx vite build   # 本地验证留空走 proxy; 部署时按 CI 注入
# i18n 门禁(残留 / 键集合 / 占位符 / 空串 / 列词条 / t 遮蔽)
python3 scripts/i18n_audit.py --gate
# 4 视口 × 双语界面快照(需先起 `cd frontend && npx vite preview --port 4173`)
python3 .artifacts/i18n/shot.py --locale zh --viewport 375
# 本地回归(注意目录)
cd cloud-functions/api && python3 local_test.py
# git 提交(消息含特殊字符时用文件)
cat > /tmp/msg.txt <<'EOF' ... EOF && git commit -F /tmp/msg.txt
```

## 已知遗留(截至 2026-11-04)

- **后端拼装的分析文案未英文化**(只有中文): 补货备注「7天走弱 · B建议补120.0件(…)」、
  采购建议「🔴 库存告急：只够卖4天…」、任务步名「窗口 1 / 评估分片 1/1」——
  由 `routes/replenishment.py` / `purchase_calc.py` / `cleansing.py` 拼成字符串返回,
  与数字混在一起, 还进任务记录和 Excel 导出。要英文化需把 `note` 改为结构化片段数组
  (前端渲染), 属数据契约变更, 影响导出/历史/告警文本 —— 单独议题。见 §15.145 五
- **字典死键 483**(`app.desc`、`cl.title`、`clean.title`、`cleansing.empty` 等):
  多为历史命名空间残留, 待甄别后删除; 不影响运行, 但会干扰"键集合"判读
- ~~低库存卡 5 行全落郑州仓~~ **已修**: 卡面改「按仓轮转 + 轮内 SKU 去重」——第一轮每仓 1 条,
  轮内跳过已上卡的 SKU; 不满 5 条继续第二轮(3 个仓 = 3+2, 仍是 5 行)。
  **不要再做"每仓最多 1 条"的硬配额**(仓少时卡面塌成 3 行), **也不要只按仓轮转**
  (各仓头一条是同一个 SKU, 会变成同 SKU × 5 仓)。见 `docs/DEVELOPMENT.md` §15.108
- ~~bbcc 模式低库存卡空白(标题 160 条 / 卡面 0 行)~~ **已修(2026-11-02)**: bc 行 id 比 platform 行
  小 6 万, 混在 `ORDER BY id DESC LIMIT 300` 里被整体挤出窗口 → low_stock 拆成单独查询。
  别再"从截断样本推断全量"(11-01 就误判成"库里没有 bc 行")。见 `docs/DEVELOPMENT.md` §15.108 第三节
- ~~bbcc 卡面显示 C 仓(应为 BC 合计)~~ **已修(2026-11-02)**: `lowStockAlerts = fullAlerts || alertsList`
  这个"数据源优先级"把 aux 已按模式筛好的结果顶掉了; 仓型过滤上提到 lowStockAlerts 这一层,
  卡面与弹窗共用一份数据。bbcc 只有 `__BC__` 一个桶, **轮转退化为顺序取 5 条(无害无功)**,
  轮转真正服务 traditional 的 7 仓。见 §15.109
- 大卡弹窗为全量列表(自有仓 90 条全展示), `maxHeight:70vh` 滚动承接
- ~~cron 对公网开放~~ **已加固(2026-11-02)**: EdgeOne schedules 无 headers 字段 → **无法给平台调度注入凭据**,
  所以改守"执行次数"(`_throttle` 进程内限频 + `_claim` 当日抢占)。**archive 失败刻意不回滚**
  (逐条累加非幂等, 重跑会把金额算两遍), 其余可回滚。见 §15.110 第一节
- ~~连接池上限形同虚设~~ **已修(2026-11-02)**: `q.qsize()` 数的是空闲不是总数 → 并发 60 建 60 条;
  改 `BoundedSemaphore`, 压测 60 并发只建 10 条。见 §15.110 第二节
- ~~traceback 回传浏览器~~ **已修**: 主通道是 `traced` 装饰器(所有路由都套), 不是全局 handler。
  现在只回 request_id, 堆栈写 quality_logs。见 §15.110 第三节
- ~~`t=Date.now()` 不要简单删掉~~ **已定案并实施(2026-11-02)**: 它保证数据实时性(用户明确要求),
  所以不删, 改「时间窗分桶」`freshStamp()`(默认 15s, 在 `frontend/src/api/client.ts`)。
  **只有出现在 URL 查询串里的 `Date.now()` 才该换** —— 日期差/唯一 id/React key 必须留原样
  (批量字符串替换误伤过 3 处)。见 §15.111 第一节
- ~~链路不可观测(评估/重建跑没跑只能翻日志猜)~~ **已修(2026-11-02)**: `pipeline_runs` 表 +
  `GET /health?detail=1` 的 `last_runs`。默认 `/health` 不带(前端 15s 轮询, 不能加查询);
  该端点匿名可读 → **只回 status/耗时/行数, 不回 error 正文**。见 §15.111 第二节
- ~~日志只留 500 条(一天就冲掉排障证据)~~ **已修**: `common.prune_quality_logs()` 按天保留 30 天
  + 2 万条硬上限, cron 与 dashboard 共用(此前两处策略不一致)。见 §15.111 第三节
- ~~告警"未命中即关闭"可能批量误关~~ **已加护栏**: 全量评估时拟关 ≥30 条且 ≥80% → 跳过并记
  `rule_guard/error`。**宁可短时虚高, 不可批量误关**(关掉的告警不会自己亮回来)。见 §15.111 第四节
- ~~渠道硬编码 `("jd","other")`~~ **已改数据驱动**(`ch_codes()`/`ch_conf()`), 覆盖每日评估/物化重建/
  告警推送渠道名。见 §15.111 第五节
- ~~对账只比件数不比金额~~ **已补(2026-11-02)**: GMV 段比 `orders_day_agg` 与订单源表,
  口径必须与 `_rebuild_day_agg` 逐字一致(金额仅已支付, cnt 不过滤状态), 收口 today-2。
  见 §15.112
- ~~`daily_stats` 是死表~~ **已给出口(2026-11-02, 方案 A)**: `routes/history.py` 把
  `orders`(≤365 天)与 `daily_stats`(>365 天)按 365 天切线 UNION 成一个查询面,
  前端新增「历史归档」页(汇总/趋势/Top/同比)。**两表互斥**是不变量, 改 archive cutoff
  必须同步改 `_CUT_DAYS`; 口径与 archive 写入侧一致(金额=total_amount 全状态, ≠ 看板 GMV)。
  同比无去年数据时回 `has_prev=false`(**不拿 0 算 -100%**)。见 §15.113
- **历史归档当前只有近一年可查**: 线上 `daily_stats` 0 行、订单最早 2026-09-03,
  归档侧要到 2027-09 才有第一批数据 —— 这是数据长度问题, 不是功能缺陷
- **增量重建已按「业务日期」落地(2026-11-02 S2-2)**: 数据入口是清洗页导入而非 ERP 事件流,
  一批文件可横跨任意业务日期 —— 所以重建范围只能由 `ordered_at` 决定, 不能由导入时间推导。
  机制: `agg_dirty_days` 队列(主键去重) + 连续日期合并区间(≤7 天/段) + 出队比 ts
  (重建期间的新写入不会被吃掉) + `orders.synced_at` 每日兜底扫描(应用代码可以漏标记, DB 不会)。
  见 §15.114
- **`orders` 业务键已改为四维(2026-11-02 S2-5)**: `uq_orders_biz(channel, order_no, sku, barcode)`,
  写入层同键 upsert(全列覆盖 + `deleted_at=''` 恢复), 重导幂等。与聚合规则(`agg_key3`)、查重口径
  (`dup-check`)三者终于同键。前置修正: 旧键 `uq_orders_no_sku(order_no, sku)` 一直在(此前误记
  "无唯一键"), 所以重复导入是整批 1062 而非翻倍; 真正的错是键比业务键窄 → 同单同SKU换69码时
  第二行 1062 被单行降级 → **少算**。配套三件事见 §15.116: 写前对齐码(防补码导致分叉)、
  旧业务日期并入重建队列(防换日期双计)、订单移出入口查重(否则覆盖成死代码)
- **补录 365 天前的数据当天在历史页查不到**(等下一次 archive 搬走); `/history/coverage`
  已返回 `stale_orders` 并提示。刻意不改查询逻辑 —— 会让每个历史查询多一次全量存在性检查
- **缓存已按域失效(2026-11-02 S2-1)**: 35 个写入口不再共用 `DELETE FROM analysis_cache`, 改为
  `invalidate("<域>")`, 由 `analysis_cache._FAMILY_DEPS`(缓存族 → 依赖域)反推要清哪几族。
  改供应商只清 看板summary/aux/断货风险 3 族, 采购/补货/订单/历史继续复用; 改采购单只清 1 族。
  两道兜底: 域名没登记 → 全清; 缓存族没登记 → 任何写入都清它(`local_test` 静态用例盯住)。
  失效改为**打墓碑**(`created_at=1970` + `tok` 写入令牌), 堵住"重建期间被失效 → 旧值覆盖"的
  脏满 TTL 问题。见 §15.117
- **日销三窗口(SKU 日销)恢复了增量重建(2026-11-02 S2-1 附带修)**: S2-2 删掉"写操作后全量重建
  90 天"时没补增量 → 线上 `sku_sales_stats` 比订单晚一天(补货/采购/断货/健康全读它, 等于
  "缓存很新、数据很旧")。现按脏日期反查受影响 SKU 只重算他们, 影响面 >2000 SKU 才全量
- **物化重建不再分"全量/增量"两套算法(2026-11-02 S2-1b)**: 脏日期 >80 天时原来走
  `_rebuild_day_agg(90)` 单条 upsert —— ① upsert 删不掉软删订单留下的组合 ② 完全没重建快照
  (线上实测: 删 2 单后 `daily_sales_snapshot` 残 1 行, 补货读旧日销)。现全量 = 增量 + 不设批次上限,
  共用「区间 DELETE+INSERT」: agg → 快照 → 日销三窗口。段预算用尽不算完成(余下留队列 +
  `agg_dirty` 置位 → 看板继续降级直查)。见 §15.117 第八节
- **硬删订单的日销孤儿行会清(2026-11-02 S2-1c)**: `sku_sales_stats` 是"有销量才建行", 软删能自愈,
  硬删后 SKU 既不在 orders 也不在快照 → "按受影响 SKU 重建"反查不到 → 旧日销行永久残留。
  现按 `日销表 SKU - (快照窗口 ∪ 订单窗口)` 差集清孤儿, 只在删单类原因下扫, 每日对账兜底一次
- **自有仓采购在途 = 库存导入(自有仓维度)填写的 `in_transit_qty`(2026-11-02 定案)**: 不再从
  `purchase_orders` 反推(那张表没有 qty/received_qty, 恒 0 且与导入值双计)。同日对齐组合键:
  自有仓按 `(sku, 69码, 仓)` 聚合、批次只查 own 仓并按同码同仓优先、告警行用库存行真实码。
  线上 jd 效期 51 → 31(20 条是把 B 仓/platform 仓批次当自有仓的误报)。见 §15.118
- **`cleanse_write._adjust_inventory` 键已补成四维(2026-11-02)**: `(sku, 69码, 仓, 仓型)`,
  `_cur_map` 升五维、INSERT 补 barcode、`beginning_stock` 与 available 同置(否则重算把增量抹成 0)、
  评估上下文复用同一次查询(原逐 SKU `LIMIT 1` 多码串行)。该函数**仍无调用点**(出库联动走
  `_recalc_own_inventory`), 属"启用前先补对"。见 §15.119
- **冷算慢的根因是往返次数, 不是 Python 计算(2026-11-02 S2-6)**: `_assemble` tottime 0.015s /
  cumtime 6.15s —— 组装阶段 99% 是它内部 21 条串行 SQL 的往返(线上单次 ~57ms)。本轮只合并同源查询
  (三计数合一 / 回款一条 GROUP BY / 品牌按日一次取 / 只看被消费的列 / DDL 进程内一次 / 去 800 分片),
  并让 `_assemble` 接收并行池预取值(全注入时零 SQL)。往返 22→14 / 23→16 / 10→7, 输出与旧实现逐字段一致
  (jd/other summary + aux 四组; own_health 仅并列定序差异, 已补稳定次级键)。**不加缓存、不拖 TTL**。
  线上实测冷算 1.45s → **0.57s**(查询 0.53 / 组装 0.04), 线上返回与本地同版本 0 差异。见 §15.120
- **库存删除可恢复 = 归档表快照(2026-11-02 S2-7, 用户定案)**: 先钉事实——inventory 删除原是
  **硬删**、`deleted_at` 无写入路径(软删 0 条), 此前"重算不过滤 deleted_at"待办的前提有误。
  口径「恢复删除 = 复原删除时的快照」落成 `inventory_archive` 归档表(而非软删: 60 处读路径
  零改动、唯一键不占位): DELETE 先快照后删; `POST /inventory/{iid}/restore` 原样插回, 同业务键
  已重新导入则拦截; `_recalc_own_inventory` 补 deleted_at 过滤(防御)。恢复 UI 待接(接口已备)。
  见 §15.121
- **启动 DDL 版本门禁(2026-11-02 S2-8)**: 实例冷启动原发 ~50 条幂等 DDL; 现 schema(表+索引)
  全部收敛为 schema_create.py 声明 + `SCHEMA_VERSION` 门禁 —— 热路径**一条 SELECT、零 DDL**,
  全量执行失败不写版本(下次重试)。以后加表/索引 = 改声明 + bump 版本, 不再写迁移检查。
  **改 schema 必 bump `SCHEMA_VERSION`, 否则存量实例不执行新声明**。见 §15.122
- **冗余清理 + 补列/探测统一(2026-11-02 S2-9)**: 4 个实证死代码删除; 三套补列合并为
  `_COLUMN_ADDS`(受门禁管); M0/M1/规则修复/退役四段**整体删除**(线上实证使命完成后定案),
  冷启动 7 条探测 SQL 归零; 90 天物化重建/69码补齐/规则恢复的复用做法见 §15.123 第三节
- **历史归档页信息层级已二次定案(2026-11-02 §15.124 第五节)**: 首卡片去壳走裸排版
  (`.hist-headcard`), 「含 1 年以前」删除 —— cron 是**严格小于** `today-365`, 实为第 366 天
  才搬, 写死"1 年"会差一天; 归档状态(暂无归档/已归档)在**锤子菜单底部** `.hist-arc`,
  "新订单哪天起进归档"由后端 `archive_day` 给(前端不自己算时区); 页面 `hist-meta` 只写一次
  区间, 来源行降级为"归档 N 天 · 订单 M 天"。**不要再把归档状态搬回页面卡片**。
- **CSS 死类要随手删**: 控件迁走(`.hist-row/.hist-pill/.hist-date/.hist-sub`)后留着,
  后来人会以为页面还有一套自绘控件。改 UI 时顺手 `grep` 一遍引用。
- **同页只能有一个时间口径(2026-11-02 §15.125)**: 历史页汇总跟随菜单区间, 同比原先写死
  「近 30 天」—— 选 90 天时同页并存两个口径且无任何说明。现 `history_yoy` 收 `start`,
  与汇总同窗口、同 400 天截断。**任何"卡片自带时间范围"的写法都要跟着菜单走, 不要写死**。
- **加了会改变结果的入参, 必须同步改缓存 key**: `yoy` 加 `start` 后 key 补 `_s`, 否则同 `end`
  不同起点共用结果, 且只在命中缓存时复现(线上"时好时坏", 本地难查)。
- **前端不要自己算时间窗口**: 区间一律取接口返回的 `cur.start/end` 展示。前端按预设再算一遍,
  后端一截断标题就显示假窗口 —— 与 `archive_day` 同一类"两处各算一份"的坑。
- **口径提示放标题行, 不放卡片中部**: 长句压住 KPI 与图表之间是"先看数再看解释"; 现为
  「区间汇总」标题右侧 10px 胶囊「非 GMV 口径」, 完整长句挂 `title`。
- **本地起后端的正确姿势**: `.env` 凭据 + `uvicorn`, 生产由 Makers 框架剥 `/api` 而 uvicorn
  不剥 → 套一层 ASGI 中间件改 `scope["path"]`。**dev 代理指向线上时, 线上跑的是旧代码**,
  排查"改了没生效"先直连后端验接口。验证完 `git checkout -- frontend/vite.config.js`。
- **cron 有第四道锁: 时间窗(2026-11-03 §15.127)**: 平台 schedules 注不了 header → 鉴权做不了,
  于是守"危害": `_throttle` + `_claim` + **`_off_window`** + 失败 `_unclaim`。窗口按 `Asia/Shanghai`
  显式算, 边界来自实测(平台触发稳定早 57~60 秒), 窗口外**零 SQL**。
  **加新 cron 任务必须同时接窗**(`_WINDOWS` 登记 + 端点调 `_off_window`), `local_test` 有"防漏接"断言。
  `ping` 与 `continue_tasks` 永远不要设窗 —— 后者被请求驱动直接调用, 设窗会打死续跑。
- **"只能人工复查"要先量规模(2026-11-03 §15.127)**: git 历史扫描曾被写成"受 refs 限制只能人工",
  实测 424 个 blob 扫完只要 3.4 秒。现 `audit_secrets.py` 默认扫三个面(历史 blob / commit message /
  reflog 脱分支), CI `fetch-depth: 0` + 每周一定时。**下次想写"无法自动化"时先跑一遍再下结论。**
- **安全工具的输也要脱敏**: 审计脚本初版把 `.env` 与 `.edgeone/auth.json` 的真实值打到了终端 ——
  比不扫更糟。凡是要打日志/终端的疑似密钥一律 `前4位***(位数)`; 密钥本体目录(`.env*`、`.edgeone/`)
  不参与"硬编码"判定, 它们由"是否入库"来管。
- **`X-Auth-Token` 是终态, 不再讨论回退(2026-11-03 补)**: 网关改写 `Authorization` 是平台行为,
  应用层改不了; 加 Bearer 回退会让"是否 401"变成随机现象。重估条件与 30 秒验证法见
  `docs/decisions/0001` —— **没有验证方法的"终态"就是懒惰**。
- **列名写错只有 SQL 冒烟能抓(2026-11-03 §15.130)**: mock 按 SQL **片段**匹配 —— 列名写错照样命中、
  照样返回假行、照样绿。`local_test.py --sql-smoke` 用 `EXPLAIN` 对生产库做名称解析(零副作用:
  不读行不加锁不执行)。首次运行抓到 3 个真缺陷, 其中"批次对账第④段从未跑成"和"采购在途恒 0"
  都是被 `except` 吞成降级的。**写了新 SQL 先问: 它会被冒烟收集到吗?** 未被测试触发的分支等于没验。
- 审计待办: ~~S1~~ → ~~S2-3~~ → ~~S2-4/A~~ → ~~S2-2~~ → ~~S2-5~~ → ~~S2-1~~ → ~~S2-6~~ → ~~S2-9~~ → ~~S3-4(真实 SQL 冒烟)~~ → ~~S3-1(四大页+App 拆分全部完成: Dashboard 2070→1143 / Rules 2769→840 / 清洗 2398→1005 / 建议 2192→618 / App 1471→1040, §15.139)~~ → ~~S3-2/S3-3(42 处裸 fetch 收口到 apiJson, §15.140)~~ → S3-5(迁移框架); S3-6(async 化)明确不做
- **拆分必须连过两类门禁(2026-11-03 §15.141)**: S3-1 只验了行为等价, CI 红灯由另
  一类翻车补课——eslint 25 个残留未使用 import + local_test 四条静态断言 grep 的还是
  RulesPage.tsx 单文件。现 `_rp` 断言改扫「主页面 + rules/*.tsx 拼接」, 与拆分位置解耦。
- ~~lockfile 缺 esbuild 平台包~~ **已修(2026-11-02)**: `npm install --package-lock-only` 补齐 22 个
  `@esbuild/*` 可选包, 删除 0 / 版本漂移 0, `npm ci` 真实执行通过。见 §15.109 第三节

## 用户偏好

- 中文交流; 决策由用户拍板时, 选项要带推荐与理由
- 痛点出现时要求"彻底根治", 不接受前端兜底糊弄; 要敢报"我判断错了"并回滚
- UI 必须先完整读 `docs/UI_DESIGN.md` 再动手; 实测(浏览器量尺寸)优先于推断
- **"存词条键、渲染时 t()"必须查全消费方**: 写入方对了不够 —— riskTabs 两处渲染点漏 t(),
  中文界面弹出英文键名; 新增键值数据时 grep 它的所有消费点逐个确认
- **表头不许渲染 col.label / 字段名原文**: 一律走 colLabel(col.id) / fieldLabel(name),
  哪怕 label 当前恰好是英文 —— 那是词条键占位, 不是文案
- **触屏看不到 title/悬浮**: 需要"看全文"的信息(如备注列)必须给点击展开, 不能只靠 title
- **后端中文句子要双语, 用「新增结构化字段」而非替换**: 保留原 `note`/`name` 字符串
  (导出/历史/告警零改动), 另加 `note_segs`/`name_key+p` 供前端 t() 渲染; 中文串由
  `zh_note(segs)` 单一真源产出, 禁止两份手拼。前端必须有无 segs 时的回落路径
- **新增 log_type 必须登记 `routes.misc._LOG_SCOPE_TYPES`**: 否则该日志在质量日志页(user)/
  开发者页(dev)/导出/归档全被 IN 过滤掉 —— "写了等于没写"(本轮抓到 rule_guard、
  api_unhandled 两个 error 级)。`local_test.py` 已有静态自检, 加类型后跑一次
- **静态自检必须反证**: 临时移除一项确认会报错, 否则「全绿」可能只是正则没扫到东西
  (同 `i18n_audit.py` 探测用必然不匹配的 bogus 参数)
