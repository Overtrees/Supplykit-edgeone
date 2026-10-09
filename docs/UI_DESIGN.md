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
