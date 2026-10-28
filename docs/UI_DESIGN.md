# UI 卡片设计规范（小/中/大卡）

> 基于 2026-10-09 调优沉淀。看板页卡片体系：iOS 18 天气小组件风——数字主角/语义状态行/紧凑分区。

## 一、卡片分级

| 级别 | 容器类 | 用途 | 特征 |
|---|---|---|---|
| 小卡 | `.card.stat-card` | 指标摘要（GMV/待处理/健康/断货） | 正方形、4 卡一行 grid |
| 中卡 | `.mid-chart-grid .card` | 图表（漏斗/店铺品牌 GMV） | 图表主体 + 标题 |
| 大卡 | `.chart-row-3 .card` | 明细列表（低库存/采购补货） | SKU 列表铺满、紧凑 |

### 卡片内边距分档（2026-10-29 定版，按内容密度而非随意取值）

| 档位 | 值 | 适用 | 判定条件 |
|---|---|---|---|
| **详情卡档** | `14px 16px` | 任务卡 `.task-card`、看板 `.card`(16px) | 卡片高度 > 100px **或** 含交互按钮/进度条/多行元信息(3 层以上) |
| **密集列表档** | `12px 14px` | 规则卡 `.rule-card`、模板弹窗行 `.tpl-item` | 列表项、3 层内信息、无独立操作按钮 |
| **弹窗控件** | `9px 12px` + `min-height:40px` | `.tpl-field`(输入/下拉) | 胶囊控件统一高度基准 40px |

**原则**：内容越密越宽松（避免贴边拥挤），列表越密越紧凑（一屏多看几条）。**不是所有卡片都用同一个值**——判定看「层数 + 有无操作按钮」。
**注意**：改动任一卡片 padding 时，检查同档其他卡片是否需同步（当前 14/16 档与 12/14 档各自内部一致）。

## 二、小卡规范（stat-card）

**布局**
- 正方形强制：`aspect-ratio: 1`（不可改——信息密度基准）
- 内边距：`padding: 16px` 统一
- 容器：`card-grid` → `grid-template-columns: repeat(auto-fit, minmax(140px, 1fr))`，gap 12
- 内容：flex column，`justify-content: flex-end`（内容贴底——"上松下紧"），`overflow: hidden`

**间距基线（以 GMV 卡为准，4 卡一致——2026-10-10 定版）**
- 标题行：`minHeight: 14` + 标题 `font-sm`/600/`var(--muted)`/letterSpacing 0.2
- 大数字：`clamp(17px, 8cqi, 28px)` / 700 / `tabular-nums`
- 明细行：统一 `font-xs`（12px）/600 色点语义 / `flex-wrap`
- **底部几何统一（视觉基准）**：最后内容块 `margin: 0` + 卡 padding 16 兜底——4 卡底部留白**几何恒 16**
- **动态下沉分层（图柱 vs 文字行视觉差）**：
  - 卡底部有**图柱**（GMV 微趋势/健康趋势）→ 图柱是最后块 → **16 兜底**（图柱自带视觉重量）
  - 图柱**缺失**（如 periodTrend<3 不渲染）→ 最后文字行是最后块 → 字身底接近行底（`lineHeight 1.2`——字身沉到 16 区，视觉与图柱对齐）
  - 断货 SKU 行（自带内容，视觉重）→ **16 兜底**
  - **不做负 margin 下沉**（曾致底距 16→11 破坏一致）；**不做少行放大**（曾致告警并排截断——统一 font-xs）
- **字身渲染精度（2026-10-10 最终）**：lineHeight 1.0 使字身底≈行盒底，但中文字体 glyph descent 致字身底 15.6（vs 图柱底 16，差 0.4px）——flex margin ±0.4 不可靠（flex 布局语义）——用 `transform: translateY(-0.4px)`（静态偏移，卡内元素无 fixed 后代安全）——0.4px 为浏览器亚像素渲染极限
- **禁止**：卡片内容 `marginBottom` 差异化；负 margin 补偿（破坏 16 基线）；少行字号放大（截断）

**信息密度（手机横屏 ≤932px）— 2026-10-28 已移除压缩块**
- 4 卡必须保持一行（不换 2×2）——网格布局保留
- 🔴 **已移除** 10-09 引入的横屏 cqi 压缩块（padding 10/12、`card-value clamp(12,6.5cqi,19)`、明细 `clamp(8,2.2cqi,10)`）
  - **原因**：数字降维（fmtBig/fmtInt）已根治"计数/金额膨胀挤压"这个最初的问题；压缩把数字压到 12-19px 反而牺牲可读性
  - **现状**：横屏 = 竖屏同款基态流体 `.card-value { clamp(16px, 8cqi, 28px) }`（数字最低 16px）
  - **回归方式**：若将来数据量暴涨再现挤压，优先做**内容冗余去除**或**降维阈值收紧**，不要回退到缩字号

**信息分层（2026-10-28 改为总览+明细两级结构）**
- **待处理卡=总览层**：大数字 = 全量待处理聚合（低库存 + 采购 + 补货 + 其他告警 + 滞销），三行布局：
  - 行1：大数字（`fmtInt`）
  - 行2：`● 采购 n` · `● 补货 n`
  - 行3：`● 低库存 n` · `● 其他 n`（可点击 → 弹窗看规则评估明细）· `● 滞销 n`
  - **颜色语义**：采购/补货/低库存 = `--warning`；其他 = `#007AFF`（可点击）；滞销 = `--muted2`
  - **计数不加千分位逗号**（`1513` 而非 `1,513`），各段用 gap 分隔不加 `·`
- **其他大卡=明细层**：低库存卡 / 采购&补货卡 / 断货卡各自独立计数，与待处理大数字**可推导**（分行展示，不做跨卡去重）
- 空态：其他为 0 时显示灰色 `其他 0`（无 ●），保持行位稳定不跳动
- 点击入口：三级预警 pill 各自进弹窗看级别明细（`riskFilter` + 弹窗 tab 切换）

### 任务卡(`.task-card`, 2026-10-28 定版)

| 项 | 规范 |
|---|---|
| 圆角 | `var(--radius-lg)` = 32px（与任务页外层卡一致；曾 16px 不一致） |
| 内边距 | `14px 16px`（**详情卡档**，见下「卡片内边距分档」） |
| 标题行 | 标题 14px 600 + **相对时间 9px muted2（`margin-left:auto` 紧贴状态）** + 状态标签 |
| 相对时间 | `刚刚` / `N 分钟前` / `N 小时前` / `MM-DD`（读 `created_at`） |
| 状态标签 | **浅底深字**（非实心）：done 绿 12% / running 橙 14% / error 红 12% |
| 结果数字 | `tabular-nums`（对齐不抖动） |
| 已完成折叠 | running/pending 全显；done/error 默认 10 条 + 「显示更多(还有 N 条)」 |

## 三、中卡规范（mid-chart-grid）

- grid：1 列（`gap 12`）；横屏 ≤932px → 2 列；≥1024px → auto-fill minmax(300px,1fr)
- 底部间距：`margin-bottom: 12px`（2026-10-09 从 16 调小——卡片整体间距收紧）
- 结构：`.section-title`（标题）+ `<Chart height={200}>`
- 图表：纯 opacity 动画、深色自适应（Chart 组件自带 theme）

### 规则卡（`.rule-card`，2026-10-28 定版）

| 项 | 规范 |
|---|---|
| 内边距 | `12px 14px`（与任务卡/模板卡统一） |
| 边框 | `1px solid var(--border)`，`radius: var(--radius-lg)`(32px)，`margin-bottom: 8px` |
| 背景 | `transparent`（未选中）/ 选中态 `rgba(0,122,255,.06)` + `border-color: var(--primary)` |
| 标题 `.rule-title` | 16px 600 + flex wrap gap 6（名称 + 启用 pill + 严重度 pill） |
| 模式 `.rule-mode` | **10px `--muted2` 灰字** + `margin-left:auto` 右置（曾用第三个 pill，视觉噪音） |
| 条件 `.rule-cond` | 13px `--primary` + `line-height:1.5`，图标 12px 顶部对齐 |
| 说明 `.rule-desc` | 12px `--muted`（原 `--font-sm`），flex wrap gap 3 |
| 复选框 `.rule-check` | 18×18 / `radius: var(--radius-xs)` / 选中 `--primary` 底 + 白 ✓ |
| 空态 | `EmptyState` + 「+ 新建规则」主按钮（**必须有出口**，不能只有文字） |

> 条件块曾用 `var(--bg)` 背景 + 圆角嵌套，2026-10-28 去掉嵌套背景；同一时期曾加的左侧 3px 蓝竖线也按需求移除。

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

## 七·五、列表行规范（ListGroup 公共件）

- Group（标题 + 圆角卡）/ Row（信息行）/ LastRow（末行无分隔线）/ ListItem（可选行：checkbox + 高亮）
- 行规范：`padding: 0 16px`（外层）/ 内层 `14px 0` / `minHeight: 48` / `borderBottom: 1px solid var(--border)`（inset 分隔线）
- **末行无分隔线**（LastRow / ListItem last）——列表末行 borderBottom:none（卡片底部不露分割线）
- 分组卡列表**不用"每行独立卡片条"模式**（背景+圆角+marginBottom 内联行与分隔线行两套风格——必须统一为分隔线行）

## 八、弹窗体系（底部 sheet）

- **一律 createPortal(document.body)**（防被滚动容器裁剪——嵌套弹窗教训）
- 定位：`bottom: calc(env(safe-area-inset-bottom) + 14px)`；**zIndex 9998（遮罩）/9999（面板）**（实测值，早期文档写 4000/4001 已过时）
- 动效：纯 opacity（sheetIn 0.22s）+ 遮罩淡入——transform 破坏 fixed 禁止
- 材料样式：`material-regular` = `background: var(--bg-regular)` + `backdrop-filter: var(--blur-regular)` + `border: 1px solid var(--border-medium)`
- **阴影（2026-10-26 去外投后）**：`boxShadow: 'var(--shadow-sheet)'`，而 `--shadow-sheet: var(--inset-glass)` = **上亮下暗双内阴影**（不再有外投影，也不再内联拼 `inset 0 1px 0 白0.25`）
- 标准模板：遮罩 `fixed inset:0 transparent zIndex:9998` + 面板 `fixed left:0 right:0 bottom:calc(safe+14px) zIndex:9999 flex center padding:0 14px pointerEvents:none` + 内 `width:100% maxWidth:600 var(--radius-lg) padding:16px + env(safe-area-inset-bottom) boxShadow:'var(--shadow-sheet)' maxHeight:70vh pointerEvents:auto`

### 模板管理弹窗（`tpl-*` 类族，2026-10-28 定版）

| 类 | 用途 | 关键值 |
|---|---|---|
| `.tpl-title` | sticky 吸顶容器 | `position:sticky top:0` + `display:flex justify-content:center` + **`pointer-events:none`**（不挡交互） |
| `.tpl-title > span` | 标题胶囊 | `padding:7px 18px / radius 99 / min-height 34` + `--bg-regular` 毛玻璃 + `--inset-glass`（**仅包裹文字，不整条铺满**） |
| `.tpl-section-title` | 分区标题 | 13px 600 + flex between（**2026-10-28 已去上边框分割线**——标题胶囊+留白已足够分区） |
| `.tpl-field` | 输入/下拉胶囊 | 15px / `padding 9px 12px` / radius 99 / **`min-height 40`** / `box-sizing:border-box` |
| `.tpl-btn`(+`-primary`/`-ghost`/`-danger`) | 按钮胶囊 | 13px 600 / radius 99 / **高度 40** |
| `.tpl-item` | 列表行 | 40px 高 / radius 12 / `--bg` 底 / 1px `--border` |
| `.tpl-empty` | 空态 | 12px `--muted2` |
| `.tpl-hint` | 提示行 | 10px `--muted2`，长说明放 `title` tooltip 不占版面 |
| `.tpl-cf-box` | 自定义字段容器 | 折叠默认收起（`display:none`），保留内容不卸载 |

**三条铁律**：① 弹窗内**不要再套一层带背景的圆角容器**包胶囊（胶囊自身已有边界，外层背景会在底部露块）② 所有胶囊同高 40px ③ `select` 不能内嵌 SVG → 图标选项用两态 pill

### 状态映射弹窗（2026-10-28）

- 行：`display:flex align-items:center gap:8 padding:5px 0` + `overflowX:auto`（窄屏横向滚动，删除按钮不贴边）
- **无行分隔线、无行胶囊框**（控件自身胶囊即视觉分区）
- 档位选择：两态 pill（`IconCheck` 销量池 = 绿 / `IconClipboard` 仅记录 = 灰），激活态白底 + `inset-glass`
- 重复值校验：同列表重名 → 输入框红边 + `title` 提示"导入归一化时后者覆盖前者"
- 「补全内置默认」= **合并**（保留用户已有项，仅补缺失），不是覆盖
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


## 十三、玻璃材质体系（2026-10-25 收敛定版）

> 模糊档位从 4 档收敛 3 档（消费实证驱动：ultra-thin/thick 零消费）；玻璃消费方内联统一为全局变量。

### 材质三档（面积→强度映射）

| 档 | 滤镜变量 | 底色 | 边框 | 消费场景 |
|---|---|---|---|---|
| 轻 | `--blur-thin`(16px+160%) | `--bg-thin` 0.4 | `--border`(两端可见中性灰) | 玻璃圆钮(menu/back/hammer/page-btn)、状态胶囊 |
| 中 | `--blur-regular`(24px+180%) | `--bg-regular` 0.55 | `--border-medium` | `.material-regular` 弹层/对话框(12 处主流) |
| 重 | `--glass-filter`(40px+250%+1.15) | `--glass-bg` 0.5 | `--glass-border`=var(--border-strong) | 浮层菜单(锤子/侧边)、`--glass-filter-soft`=Toast |

### 玻璃钮立体（浅色表现）

- 边框禁白边(`--border-light` 浅色背景不可见)→ **`--border`**(light #e5e7eb/dark #334155 两端可见)
- **`box-shadow: var(--shadow-control)`**(inset 顶部白高光 0.5px + 外投 2px8px)——立体核心配方
- 32px 小钮同规格化(page-btn 曾白底扁平漏反光)

### 反光层 + 动态光追

- `.glass-edge::before`：1px 边缘切割高光(`radial-gradient` 光位 + `mask-composite: exclude` 只留边框环)——挂浮层菜单 + 4 玻璃圆钮
- **动态光追**：`useLightTracker`(pointermove→rAF 节流→写 `--light-x/--light-y`, clamp -50~150%)；默认 30%/30% 静态兜底；dark 弱化(0.45)
- 禁 contrast 于 blur 滤镜(iOS 颜色断层教训)

### 平台降级 + dark 差异化

- **@supports not (backdrop-filter)**：`--blur-*`/`--glass-filter*` → none + 底色提 0.85~0.92(旧 Android/WebView 不裸奔)；反光层不依赖滤镜自动保留
- **dark 差异化**：`--glass-saturate 2.5→1.5`、`--glass-brightness 1.15→1.05`(深色防颜色断层)；dark 块须覆盖 glass 三参数(曾只覆盖 bg/border——浮层 dark 下 250% 饱和残留)

### 验收（玻璃相关改动必查）

1. 浅色背景下白边是否不可见(换 `--border` 或给阴影)
2. 反光/光追是否覆盖目标控件(禁只挂类不挂样式)
3. dark 下饱和/亮度是否差异化(非默认 250%/1.15)
4. 无 backdrop-filter 设备是否降级可读
## 十四、看板数字降维(2026-10-26 定版)

> 卡片金额/大数字超 5 位显示问题——降维不是缩字号, 是数字单位换算。

### 规则

| 语义 | 函数 | 规则 | 应用 |
|---|---|---|---|
| 金额 | `fmtBig` | ≥1亿 → `x.xx亿` / ≥10万 → `x.xx万`(**2 位小数**) / 否则千分位 | GMV 卡主数字(总/净/回款)、日均 |
| 计数 | `fmtInt` | **窄屏(≤932)≥1万** → 整数万 / **非窄屏 ≥10万** → 整数万 / 否则千分位（**无逗号**） | 待处理、断货三级(紧急/预警/观察/全部)、低库存"共 N 条" |

### 约定

- 图表 tooltip / 坐标轴 formatter **保留完整值**(悬浮看细节, 卡片清爽)
- 计数是整数语义(万档整数); 金额才 2 位小数——勿混用
- **降维阈值按屏幕**(2026-10-28): 手机窄屏(`max-width:932px`, 含竖屏横屏)从 **5 位(1万)** 起降(空间紧);
  平板/桌面保留 **6 位(10万)** 起降。判断函数内读 `window.matchMedia('(max-width:932px)')`, 旋转实时生效
- 计数**不用千分位逗号**(`1513` 而非 `1,513`); 金额 `fmtBig` 的千分位分支保留

### 验收

1. GMV 卡 `¥1520.95万` 而非 `¥15,209,528.54`(卡片不挤爆)
2. 低库存/待处理超 5 位时 `20万` 而非 `199,610`
3. tooltip 悬浮仍是完整千分位## 十五、玻璃内外光学定稿(2026-10-27——42px 手稿版同构)

> 去外投后的"玻璃四层":全部内化,无任何外投影;对齐 42px 手稿逐值打磨。

### 四层结构(由外到内)

| 层 | 实现 | 职责 |
|---|---|---|
| 边框 1px | 浅灰 `rgba(0,0,0,.16)`(浮层/弹窗)或 `--border` #e5e7eb(按钮); dark 白 .16 | **分离感**(白边浅底隐身=无边界, 灰边是明确分离线) |
| 反光环 ::before | padding **1px** + mask 边框切割, 亮度 light 1.0 / dark 0.9 | 切割高光(与光追跟随) |
| inset-glass | `inset 0 1px 1px 白.5 / inset 0 -1px 1px 黑.06`(light); dark 白.14+黑.45 | **厚度**(上亮下暗=光源在上) |
| 焦散 ::after | 椭圆光斑 `radial-gradient(ellipse 50% 3px at 50% 100%)` 圆钮/胶囊、`85% 3px` 浮层/弹窗; bottom 0 贴底; 亮度 `--caustic-a .5`→light 1.0 / dark .5; opacity .8 | 底部聚光(覆盖倒数第二层暗边, 不碰反光环) |

### 关键参数与铁律

- **椭圆焦散无直线边**:inset:0 + 椭圆自然衰减 + 元素圆角裁弧——禁用 left/right+height 横带(mask 会出平行线/硬截止)
- **圆上溢出整治**:`overflow: hidden`(圆钮/胶囊)——焦散越出圆的部分直接裁掉,非算弦宽(纯 CSS 算弦不可能)
- **弃容器单位**:cqh/cqw 等比缩放破坏跨尺寸一致(厚随高变=72px 与 42px 不同家族); size 容器塌内容宽元素
- **浅色观感**:灰边(分离)+ top 白高光(最显)+ 底暗/焦散被浅底稀释属本征; 深色:边框 .16 + 焦散 .5 更实
- **验收清单**:①无任何 box-shadow 外投(0 Npx 残留) ②玻璃件四层齐 ③圆钮底部无溢出线 ④浅色灰边可见 ⑤暗色边框 ≥.16
## 十六、清洗页四步流程 UX 规范(2026-10-28 定版)

> 从上传到完成的全流程视觉与交互约定；所有页面遵循同一套间距/字号/胶囊尺寸。

### 步骤条(.step-indicator)

```css
.step-indicator { display:flex; gap:6px; padding:10px 12px; border-radius:var(--radius-lg);
  background:var(--bg); font-size:var(--font-xs); overflow-x:auto; }
.step { padding:4px 10px; border-radius:99px; color:var(--muted2); white-space:nowrap; }
.step.active { background:var(--card); color:var(--text); font-weight:600; box-shadow:var(--inset-glass); }
.step.done { color:var(--success); }
```
- 四步：上传文件 / 映射字段 / 预览确认 / 完成
- 当前步：白底凸起；已完成：绿色（配 ✓）；窄屏横向滚动不换行

### 四步统一尺寸

| 项 | 值 |
|---|---|
| 卡片留白 | 首屏 28px / 其余 16px |
| 区块标题 | 16px 600 |
| 说明文字 | 11px `--muted2` + letter-spacing .2 |
| 主按钮 | 高 48px、胶囊、宽度 260（首屏下拉同宽） |
| 次操作按钮 | 高 44px 胶囊，左固定 96px |
| 胶囊控件 | 统一 40px 高（`tpl-field`） |
| 段间距 | 16px / 块内 12px / 行内 8px |

### 首屏(上传)

**保持居中**（单任务引导，非长表单）：标题 16px → 类型说明 11px → 类型下拉(260×48) → 上传按钮(260×48) → 提示 11px。

### 映射页

- **行去外层胶囊框** → 细分隔线（`map-row`，末行无线）
- **响应式**（容器查询 480px 断点）：
  - 竖屏（实测 326px 可用）：两行堆叠 = 列名+状态 pill / 副行(映射目标·智能建议) / 下拉(全宽 40px)+ 必填
  - 宽屏：三列横排 = 列名区 150px → `→` → 下拉 flex → 计数 32px
- 色彩：已映射=绿色 pill / 未映射=橙色 pill / 必填=灰字 32px 右置

### 预览页

- 表格**内部限高滚动**，不撑高整页：`max-height: clamp(240px, calc(100dvh - 340px), 100dvh)`；横屏(≤500px 高) `clamp(180px, 60dvh, 78dvh)`
- 表头 `position: sticky`（保留）；表头下方 10px 副行显示来源列名
- 脏数据警示条：11px、图标 flex 顶部对齐、`radius-lg`
- 底部：左「返回」96px(次要) + 右「确认写入 N 条」**主色 primary**（写入不是"成功"，不用绿色）

### 完成页

- 图标 56px 圆形底：异步=蓝 / 成功=绿 / 失败=橙
- 标题语义化（已提交后台处理 / 导入完成 / 导入未完成），不用后端 message 直出
- 数字 28px + `tabular-nums`（与看板卡同规）
- 失败明细分隔线 1px（与其他表格一致）
- 底部 44px 胶囊：重新开始(ghost, 96px) + 导入相同格式(success, flex)

### 禁忌（踩过的坑）

| ❌ 不要 | 原因 |
|---|---|
| 隐藏 `::-webkit-scrollbar` / 移除 `WebkitOverflowScrolling` | iOS 滚动容器首帧不绘制内容 |
| 监听 `resize` 重置 `scrollTop` | 地址栏伸缩触发 resize → 滚动位置被反复清零 |
| `max-height` 与 `min-height` 并存 | max 优先，min 失效 |
| `<option>` 内嵌 SVG | 不支持，改两态 pill |
| 弹窗内再套一层带背景圆角容器包住胶囊 | 胶囊自身已有视觉边界，外层背景在底部露块 |
