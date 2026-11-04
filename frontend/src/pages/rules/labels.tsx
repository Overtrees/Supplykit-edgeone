/**
 * 规则页字典与条件解析(纯函数, 无副作用) —— 自 RulesPage.tsx 外提(S3-1 续):
 * VARS/LF/OPS 字段字典 + fieldLbl/opLbl 标签映射 + pc 条件解析 + defaultTestInv 测试默认值
 */
import { t } from '../../locale'

export const VARS = {
  product_name: 'rf.vars.product_name',
  sku: 'rf.vars.sku',
  avail: 'rf.vars.avail',
  safety: 'rf.vars.safety',
  days: 'rf.vars.days',
  stock: 'rf.vars.stock',
  order_qty: 'rf.vars.order_qty',
  store: 'rf.vars.store',
  warehouse: 'rf.vars.warehouse',
  // 2026-10-28 补全(后端模板变量全集: seed_fill.py/core 扫描)——曾缺这些 key 直接显示英文原值
  health_score: 'rf.vars.health_score',
  health_warning: 'rf.vars.health_warning',
  adj_dos: 'rf.vars.adj_dos',
  lit: 'rf.vars.lit',
  // 2026-11-01 补 sellable —— 内置低库存规则的模板是「可售 {sellable} < 动态安全线 {safety}」,
  //   后端 ctx 有 sellable(core/rules.py:110 仅库存事件注入), 但前端 VARS 漏了它 →
  //   规则列表里直接显示英文原值 "sellable"。扫描模板变量全集发现的唯一遗漏项。
  sellable: 'rf.vars.sellable',
  // 2026-11-01 自有仓供给侧 —— 后端 core/rules.py 渲染前会把 own./params./batch. 三组
  //   **扁平化**到顶层(模板里只能写 {gap} 不能写 {own.gap}), 故这里按扁平名映射中文
  available: 'rf.vars.available',
  transit: 'rf.vars.transit',
  po_transit: 'rf.vars.po_transit',
  can_ship: 'rf.vars.can_ship',
  downstream_ds: 'rf.vars.downstream_ds',
  downstream_avail: 'rf.vars.downstream_avail',
  b2c_days: 'rf.vars.b2c_days',
  need: 'rf.vars.need',
  gap: 'rf.vars.gap',
  cover_days: 'rf.vars.cover_days',
  days_to_expire: 'rf.vars.days_to_expire',
  // 参数键(模板里直接引用 {own_overstock_days} 这类扁平名, 后端 flatten params 后可见)
  transfer_gap_min: 'rf.vars.transfer_gap_min',
  own_overstock_days: 'rf.vars.own_overstock_days',
  own_expiry_buffer_days: 'rf.vars.own_expiry_buffer_days',
  // 2026-11-01 补全: 参数表单里可配置的其余键 —— 用户把 `own.gap > params.xxx` 写成这些参数时,
  //   条件右侧原样显示 params.b_to_c_days 这类英文路径(fieldLbl 回落到原始字符串)。
  //   凡表单可填的参数都必须在此有中文名, 否则"可视化可配置"在列表回显上打折。
  b_to_c_days: 'rf.vars.b_to_c_days',
  c_safety_days: 'rf.vars.c_safety_days',
  ship_to_b_days: 'rf.vars.ship_to_b_days',
  safety_multiplier: 'rf.vars.safety_multiplier',
  turnover_warning_15: 'rf.vars.turnover_warning_15',
  turnover_warning_90: 'rf.vars.turnover_warning_90',
  lead_time_days: 'rf.vars.lead_time_days',
  purchase_lead_days: 'rf.vars.purchase_lead_days',
  purchase_safety_days: 'rf.vars.purchase_safety_days',
  moq: 'rf.vars.moq',
  max_turnover_days: 'rf.vars.max_turnover_days',
  // 断货规则参数(条件里 params.lit 已覆盖, 其余供右侧引用时显示)
  otif_min: 'rf.vars.otif_min',
  ss_z: 'rf.vars.ss_z',
  sigma_days: 'rf.vars.sigma_days',
  accel_ratio: 'rf.vars.accel_ratio',
  accel_min_qty: 'rf.vars.accel_min_qty',
  buffer_orange: 'rf.vars.buffer_orange',
  buffer_yellow: 'rf.vars.buffer_yellow',
  orange_slack_days: 'rf.vars.orange_slack_days',
  include_avail_zero: 'rf.vars.include_avail_zero',
  health_good: 'rf.vars.health_good',
  log: 'rf.vars.log',
  alert_enabled: 'rf.vars.alert_enabled',
}
export const renderTmpl = text => {
  if (!text) return null
  const parts = text.split(/(\{(\w+)\})/g)
  return parts.map((p, i) => {
    if (i % 3 === 1)
      return (
        <span
          key={i}
          style={{
            display: 'inline-block',
            background: 'rgba(29,78,216,0.1)',
            color: 'var(--primary)',
            padding: '0 4px',
            borderRadius: 4,
            fontWeight: "var(--fw-semibold)",
            fontSize: 'var(--font-10)',
          }}
        >
          {VARS[parts[i + 1]] ? t(VARS[parts[i + 1]]) : parts[i + 1]}
        </span>
      )
    if (i % 3 === 2) return null
    return <span key={i}>{p}</span>
  })
}
export const LF = [
  { l: 'rf.lf.0', v: 'inv.available_qty' },
  { l: 'rf.lf.1', v: 'inv.safety_qty' },
  { l: 'rf.lf.2', v: 'inv.in_transit_qty' },
  { l: 'rf.lf.3', v: 'inv.locked_qty' },
  { l: 'rf.lf.4', v: 'inv.available_qty + inv.in_transit_qty' },
  { l: 'rf.lf.5', v: 'inv.available_qty + inv.in_transit_qty + inv.locked_qty' },
  // 2026-10-30 可售口径(与内建低库存/断货/采购同源): 顶层 sellable=可用+在途, transit=在途
  { l: 'rf.lf.6', v: 'sellable' },
  { l: 'rf.lf.7', v: 'transit' },
  { l: 'rf.lf.8', v: 'safety - sellable' },
  { l: 'rf.lf.9', v: 'sellable / safety' },
  { l: 'rf.lf.10', v: 'inv.safety_qty - inv.available_qty' },
  { l: 'rf.lf.11', v: 'inv.available_qty / inv.safety_qty' },
  { l: 'rf.lf.12', v: 'daily_sales' },
  { l: 'rf.lf.13', v: 'inv.available_qty / daily_sales' },
  { l: 'rf.lf.14', v: 'days_since_last' },
  { l: 'rf.lf.15', v: 'stock' },
  { l: 'rf.lf.16', v: 'inv.warehouse_type' },
  { l: 'rf.lf.17', v: 'barcode' },
  { l: 'rf.lf.18', v: 'order.quantity' },
  // 2026-10-29 累计订单量(超卖判定应使用此字段: 单笔口径会漏报累计超卖)
  { l: 'rf.lf.19', v: 'order.total_qty' },
  { l: 'rf.lf.20', v: 'order.max_quantity' },
  { l: 'rf.lf.21', v: 'order.min_quantity' },
  { l: 'rf.lf.22', v: 'order.line_count' },
  // 出入库事件字段
  { l: 'rf.lf.23', v: 'flow.quantity' },
  { l: 'rf.lf.24', v: 'flow.warehouse' },
  { l: 'rf.lf.25', v: 'flow.date' },
  { l: 'rf.lf.26', v: 'inbound.quantity' },
  { l: 'rf.lf.27', v: 'outbound.quantity' },
  // ctx 扩展字段(2026-10-29): 缺失时条件自动跳过(不按 0 误判)
  { l: 'rf.lf.28', v: 'batch.days_to_expire' },
  { l: 'rf.lf.29', v: 'supplier.score' },
  { l: 'rf.lf.30', v: 'inv.turnover_days' },
  { l: 'rf.lf.31', v: 'order.total_amount' },
  { l: 'rf.lf.32', v: 'order.quantity * order.unit_price' },
  { l: 'rf.lf.33', v: 'order.unit_price' },
  { l: 'rf.lf.34', v: 'inv.adj_dos' },
  { l: 'rf.lf.35', v: 'inv.buffer' },
  { l: 'rf.lf.36', v: 'inv.otif' },
  { l: 'rf.lf.37', v: 'inv.ss_dyn' },
  { l: 'rf.lf.38', v: 'inv.accel_rate' },
  { l: 'rf.lf.39', v: 'health.score' },
  { l: 'rf.lf.40', v: 'params.lit' },
  { l: 'rf.lf.41', v: 'params.health_warning' },
  // 2026-11-01 自有仓供给侧口径 —— 自有仓(集货仓)不产生销售(daily_sales_snapshot 只有平台仓行),
  //   逐仓日销 ds=0 → 动态安全线=0 → 低库存口径恒不触发(口径错配, 非 bug)。
  //   自有仓是供给侧, 该判的是「下游要货备不备得出 / 压太多 / 会不会过期」, 故另开 own.* 变量组。
  //   注意: 这些键**仅在 inv.warehouse_type=='own' 的行注入**, 所以条件里务必带上该前置。
  { l: 'rf.lf.42', v: '' },
  { l: 'rf.lf.43', v: 'own.available' },
  { l: 'rf.lf.44', v: 'own.transit' },
  { l: 'rf.lf.45', v: 'own.po_transit' },
  { l: 'rf.lf.46', v: 'own.can_ship' },
  { l: 'rf.lf.47', v: 'own.downstream_ds' },
  { l: 'rf.lf.48', v: 'own.downstream_avail' },
  { l: 'rf.lf.49', v: 'own.b2c_days' },
  { l: 'rf.lf.50', v: 'own.need' },
  { l: 'rf.lf.51', v: 'own.gap' },
  { l: 'rf.lf.52', v: 'own.cover_days' },
]
export const OPS = [
  { l: 'rf.op.lt', v: '<' },
  { l: 'rf.op.lteq', v: '<=' },
  { l: 'rf.op.gt', v: '>' },
  { l: 'rf.op.gteq', v: '>=' },
  { l: 'rf.op.eqeq', v: '==' },
  { l: 'rf.op.neq', v: '!=' },
]

export const fieldLbl = v => {
  const f = LF.find(x => x.v === v)
  if (f) return t(f.l)
  // 2026-11-01 params.* 引用落到"右侧值"位置时(条件 `own.gap > params.transfer_gap_min`),
  //   原样返回 "params.transfer_gap_min" —— 用户在列表里看到的是英文路径不是人话。
  //   参数名本身不在 LF 里, 单独查 VARS(后者已含自有仓三个阈值的中文名)。
  if (typeof v === 'string' && v.indexOf('params.') === 0) {
    const k = v.split('.')[1]
    return VARS[k] ? t(VARS[k]) : k
  }
  return v
}
export const opLbl = v => {
  const o = OPS.find(x => x.v === v)
  return o ? t(o.l) : v
}
/** 仓库/模式/事件字典的标签取值(字典里存的是词条键, 这里统一解析) */
export const whLbl = (v: string) => t((WHS.find(x => x.v === v) || WHS[0]).l)
export const modeLbl = (v: string) => t((MODES.find(x => x.v === v) || MODES[0]).l)

export const pc = j => {
  try {
    const c = JSON.parse(j)
    let rt = c.rightType || 'field'
    let r = c.right || 'inv.safety_qty'
    let pct = 100
    const wh = c.warehouse || ''
    const m =
      typeof r === 'string' ? r.match(/^max\(1,\s*(\w+(?:\.\w+)*)\s*\*\s*([\d.]+)\)$/) : null
    if (m) {
      r = m[1]
      rt = 'pct'
      pct = Math.round(parseFloat(m[2]) * 100)
    }
    if (!rt || rt === 'field') {
      const f = LF.find(x => x.v === r)
      if (!f && typeof r === 'string' && !r.replace('.', '').match(/^\d+$/)) rt = 'text'
      else if (!f) rt = 'number'
    }
    const orArr = Array.isArray(c.or)
      ? c.or.map(sub => ({
          left: (sub || {}).left || 'inv.available_qty',
          op: (sub || {}).op || '<',
          right: (sub || {}).right || 'inv.safety_qty',
          rightType: (sub || {}).rightType || 'field',
          warehouse: (sub || {}).warehouse || '',
          pctValue: 100,
        }))
      : []
    // 2026-11-01 and 子条件原样带出 —— 内置自有仓规则是 `warehouse_type==own and {own.gap>N}` 结构,
    //   不解析 and 则编辑打开时前置条件视觉缺失, 保存又被丢掉 → 语义静默变更
    const andSub =
      c.and && typeof c.and === 'object' && !Array.isArray(c.and)
        ? {
            left: (c.and || {}).left || '',
            op: (c.and || {}).op || '<',
            right: (c.and || {}).right ?? '',
            rightType: (c.and || {}).rightType || 'field',
            warehouse: (c.and || {}).warehouse || '',
            pctValue: 100,
          }
        : undefined
    return {
      left: c.left || 'inv.available_qty',
      op: c.op || '<',
      right: r,
      rightType: rt,
      pctValue: pct,
      warehouse: wh,
      or: orArr,
      ...(andSub ? { and: andSub } : {}),
    }
  } catch {
    return {
      left: 'inv.available_qty',
      op: '<',
      right: 'inv.safety_qty',
      rightType: 'field',
      pctValue: 100,
      warehouse: '',
      or: [],
    }
  }
}

// 测试弹窗默认模拟值: 从规则条件提取(数字阈值→按 op 方向设贴近值保证可观察触发; 字段引用→右侧字段示范值)
export const defaultTestInv = condInfo => {
  const inv = {
    available_qty: 0,
    safety_qty: 0,
    in_transit_qty: 0,
    warehouse_type: condInfo.warehouse || '',
    days_since_last: 0,
    order_quantity: 0,
    adj_dos: '',
    buffer: '',
    health_score: 100,
  }
  const fk = e =>
    e === 'inv.available_qty' || e === 'avail'
      ? 'available_qty'
      : e === 'inv.safety_qty' || e === 'safety'
        ? 'safety_qty'
        : e === 'inv.in_transit_qty'
          ? 'in_transit_qty'
          : e === 'days_since_last' || e === 'days'
            ? 'days_since_last'
            : e === 'order.quantity' || e === 'order_qty'
              ? 'order_quantity'
              : null
  const rNum = condInfo.rightType === 'number' ? Number(condInfo.right) : NaN
  const lk = fk(condInfo.left)
  if (!isNaN(rNum) && lk) {
    if (condInfo.op === '<') inv[lk] = Math.max(rNum - 1, 0)
    else if (condInfo.op === '>') inv[lk] = rNum + 1
    else inv[lk] = rNum // <= >= = 等
  } else {
    const rk = fk(condInfo.right)
    if (rk && !inv[rk]) inv[rk] = rk === 'safety_qty' ? 10 : 5 // 字段引用 → 右侧字段示范值(便于 0<x 触发观察)
  }
  return inv
}

// ── 事件/仓库/模式字典(自 RulesPage 外提, 仅供编辑表单与列表使用) ──
export const EVENTS = [
  { value: 'inventory.changed', label: 'rf.ev.0' },
  { value: 'order.created', label: 'rf.ev.1' },
  { value: 'scheduled.daily', label: 'rf.ev.2' },
  // 2026-10-29 出入库事件(此前完全不触发评估)
  { value: 'inbound.created', label: 'rf.ev.3' },
  { value: 'outbound.created', label: 'rf.ev.4' },
]
export const WHS = [
  { l: 'rf.wh.all', v: '' },
  { l: 'rf.wh.platform_b', v: 'platform_b' },
  { l: 'rf.wh.bc', v: 'bc' },
  { l: 'rf.wh.platform', v: 'platform' },
  { l: 'rf.wh.own', v: 'own' },
]
export const MODES = [
  { l: 'rf.mode.all', v: '' },
  { l: 'rf.mode.bbcc', v: 'bbcc' },
  { l: 'rf.mode.traditional', v: 'traditional' },
]
