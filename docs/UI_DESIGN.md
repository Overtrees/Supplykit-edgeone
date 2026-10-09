# UI 卡片设计规范（小/中/大卡）

> 基于 2026-10-09 调优沉淀。看板页卡片体系：iOS 18 天气小组件风——数字主角/语义状态行/紧凑分区。

## 一、卡片分级

| 级别 | 容器类 | 用途 | 特征 |
|---|---|---|---|
| 小卡 | `.card.stat-card` | 指标摘要（GMV/待处理/健康/断货） | 正方形、4 卡一行 grid |
| 中卡 | `.mid-chart-grid .card` | 图表（漏斗/店铺品牌 GMV） | 图表主体 + 标题 |
| 大卡 | `.chart-row-3 .card` | 明细列表（低库存/采购补货） | SKU 列表铺满、紧凑 |

## 二、小卡规范（stat-card）

**布局**
- 正方形强制：`aspect-ratio: 1`（不可改——信息密度基准）
- 内边距：`padding: 16px` 统一
- 容器：`card-grid` → `grid-template-columns: repeat(auto-fit, minmax(140px, 1fr))`，gap 12
- 内容：flex column，`justify-content: flex-end`（内容贴底——"上松下紧"），`overflow: hidden`

**间距基线（以 GMV 卡为准，4 卡一致）**
- 标题行：`minHeight: 14` + 标题 `font-sm`/600/`var(--muted)`/letterSpacing 0.2
- 内容区：统一 `marginBottom: 4`（卡内最后内容距卡底 = 4 + padding 16 = 20px）
- 大数字：`clamp(17px, 8cqi, 28px)` / 700 / `tabular-nums`
- 明细行：`font-9`/`font-10`，`flex-wrap`，行距 `lineHeight 1.3-1.5`
- **禁止**：卡片内容 `marginBottom` 差异化（曾致 4 卡底部 16 vs 20 不一致——统一 16 贴底）

**信息密度（手机横屏 ≤932px）**
- 4 卡必须保持一行（不换 2×2）
- `@media (orientation: landscape) and (max-width: 932px)`：字体/间距按容器 cqi 自动缩小
  - `.stat-card { padding: 10px 12px !important }`（盖内联）
  - `.card-value { font-size: clamp(12px, 6.5cqi, 19px) !important }`
  - 明细 `font-9/10` → `clamp(8px, 2.2cqi, 10px)`
  - 内容冗余优先去除（见"信息分层"）而非无限缩字

**信息分层（去重原则）**
- 小卡只保留**唯一信息**（其他卡不展示的）：如待处理卡保留大数字/异常/告警/滞销/其他告警入口——低库存/采购补货计数与下方大卡重复 → 删除
- 点击入口：三级预警 pill 各自进弹窗看级别明细（`riskFilter` + 弹窗 tab 切换）

## 三、中卡规范（mid-chart-grid）

- grid：1 列（`gap 12`）；横屏 ≤932px → 2 列；≥1024px → auto-fill minmax(300px,1fr)
- 底部间距：`margin-bottom: 12px`（2026-10-09 从 16 调小——卡片整体间距收紧）
- 结构：`.section-title`（标题）+ `<Chart height={200}>`
- 图表：纯 opacity 动画、深色自适应（Chart 组件自带 theme）

## 四、大卡规范（chart-row-3）

- grid：1 列（gap 12）；≥640px → 2 列；≥1024px → auto-fill minmax(300px,1fr)
- 结构：`.section-title`（标题含计数 `(N)`）+ SKU 行列表
- **SKU 行**（铺满紧凑——横竖屏一致）：
  - `padding: '8px 0'` + `borderBottom: 1px solid var(--border)`（行间分隔）
  - 行内：名称（flex 1 截断）+ 级别 pill（severity 色）+ 明细（small muted）
  - 点击跳进销存联动高亮（`onAlert`）
  - 底部"更多 N"用 `pill info`（参考待处理卡"其他 1"样式：font-10/padding 1px 8px/lineHeight 16px）
- 多卡（低库存/采购补货）结构必须同构（标题+行+pill 样式一致）

## 五、通用规范

- **动效**：仅纯 opacity（fadeIn 0.2-0.25s / sheetIn）——transform 破坏 fixed/安全区（9 月教训）；骨架 pulse + 数据 fade-in（key 随 loading 切换重挂载）
- **安全区**：`.container` padding `calc(70px + env(safe-area-inset-top)) 16px calc(20px + env(safe-area-inset-bottom))`；底部弹窗 `bottom: calc(env(safe-area-inset-bottom) + 14px)`
- **错误态**：ErrorRetry 三要素（标题"加载失败"/原因"网络波动|服务不可用"/重试按钮）；4xx 与网络错误分类（errText）
- **数字排版**：所有数值列 `font-variant-numeric: tabular-nums`（防跳动）；金额/数量/状态列居中，文字列左对齐
- **触控**：icon 按钮 ≥48px；pill 点击目标 ≥ lineHeight 16 + padding

## 六、验收清单（新卡/改卡必查）

1. 4 小卡底部留白一致（16px 贴底，无差异化 margin）
2. 手机横屏 4 卡一行不截断（密度压缩生效）
3. 冗余信息分层（与其他卡重复 → 删）
4. 动画纯 opacity；安全区 env() 齐全
5. 数字列 tabular-nums + 居中；行 hover 反馈
6. 横竖屏内容完整（不依赖 aspect-ratio 以外的固定高度）

## 七、导航与全局结构

- **侧边栏**：10 个模块项（SVG 图标 + 短标签），当前页无高亮边框（激活态用文字色/字重区分）；看板页自动隐藏"看板"项
- **Header**：页面标题 + 锤子菜单（icon-btn 48px 圆形）+ 侧边栏按钮；欢迎页全屏覆盖（首次引导 4 核心入口）
- **页面容器**：`main.container` maxWidth 1200 / padding 含安全区 / minHeight 100svh

## 八、弹窗体系（底部 sheet）

- **一律 createPortal(document.body)**（防被滚动容器裁剪——嵌套弹窗教训）
- 定位：`bottom: calc(env(safe-area-inset-bottom) + 14px)`；zIndex 4000（遮罩）/4001（面板）
- 动效：纯 opacity（sheetIn 0.22s）+ 遮罩淡入——transform 破坏 fixed 禁止
- 材料样式：`material-regular`（毛玻璃 + inset 高光 + radius-lg）
- 确认弹窗（ConfirmDialog）：标题 + 描述 + 取消/确认按钮（`sheet-close`/`sheet-danger`），`confirming` 态禁用+降透明
- 级别明细弹窗：标题含级别 + tab 切换（选中高亮：白字 + 级别色底）

## 九、列表与表格

- 表格：sticky 表头（背景 var(--card)）、行 hover 反馈（tr:hover td）、偶数行底色
- 列对齐：文字列左 / 金额·数量·日期·状态列居中（`text-align: center`）/ 数字 `tabular-nums`
- 单元格：`col-sku`（mono）/`col-name`（截断）/`col-store`/`col-price`/`col-qty`/`col-date`（样式类统一）
- 状态 pill：`pill success/warning/danger/info`——font-10/padding 1px 8px/lineHeight 16px/minHeight auto
- 选中态：勾选列固定最左（32px 占位 th）+ 行背景高亮（rgba primary 0.06）+ 锤子菜单批量按钮
- 空态：EmptyState（icon + 标题 + desc + action 按钮）——所有列表页统一
- 加载：骨架屏（skeleton-pulse）+ 数据淡入（key 随 loading 切换）；加载更多骨架条

## 十、表单与交互反馈

- 输入：`.hammer-input`（圆角/font-13）；segmented pill tab（选中白底高亮）
- 按钮：btn-primary/ghost/success/danger + 加载态（spinner + disabled + 降透明）
- Toast：4 色（success/error/warning/info）+ 可带 action（跳转）
- 错误：ErrorRetry 三要素（标题/原因/重试）——`errText` 区分网络波动 vs 加载失败
- 点击反馈：全局 `.clickable`（active 态）；icon-btn ≥48px 触控

## 十一、主题与排版

- 深色模式：CSS 变量（--text/--bg/--card/--border）+ Chart 自动跟随 prefers-color-scheme
- 字体层级：font-9/10/13（xs）/sm/lg/md/18——信息密度优先（小卡明细 font-9）
- 数字：`font-variant-numeric: tabular-nums` 全局 td + 大数字 clamp(cqi)
- 图标：SVG 统一（Icons 组件，currentColor）——emoji 已迁移

## 十二、响应式与安全区

- 断点：手机竖屏（4 小卡 1-2 列 auto-fit）/ 手机横屏（≤932 密度压缩）/ 平板 640 / 桌面 1024+
- 安全区：`env(safe-area-inset-top/bottom/left/right)` 全面（container/弹窗/灵动岛）
- 横屏：小卡 4 卡一行强制（字体 cqi 压缩）；中卡 2 列；不依赖固定高度（防截断）
