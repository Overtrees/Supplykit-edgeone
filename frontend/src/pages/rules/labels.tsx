/**
 * 规则页字典与条件解析(纯函数, 无副作用) —— 自 RulesPage.tsx 外提(S3-1 续):
 * VARS/LF/OPS 字段字典 + fieldLbl/opLbl 标签映射 + pc 条件解析 + defaultTestInv 测试默认值
 */
export const VARS = {
  product_name: '商品名',
  sku: 'SKU',
  avail: '可用量',
  safety: '动态安全线',
  days: '天数',
  stock: '库存量',
  order_qty: '订单数',
  store: '店铺',
  warehouse: '仓库',
  // 2026-10-28 补全(后端模板变量全集: seed_fill.py/core 扫描)——曾缺这些 key 直接显示英文原值
  health_score: '库存健康分',
  health_warning: '健康预警档位',
  adj_dos: '调整后可撑天数',
  lit: '补货周期',
  // 2026-11-01 补 sellable —— 内置低库存规则的模板是「可售 {sellable} < 动态安全线 {safety}」,
  //   后端 ctx 有 sellable(core/rules.py:110 仅库存事件注入), 但前端 VARS 漏了它 →
  //   规则列表里直接显示英文原值 "sellable"。扫描模板变量全集发现的唯一遗漏项。
  sellable: '可售量',
  // 2026-11-01 自有仓供给侧 —— 后端 core/rules.py 渲染前会把 own./params./batch. 三组
  //   **扁平化**到顶层(模板里只能写 {gap} 不能写 {own.gap}), 故这里按扁平名映射中文
  available: '自有仓可用',
  transit: '自有仓在途',
  po_transit: '采购在途',
  can_ship: '自有可发',
  downstream_ds: '下游日销',
  downstream_avail: '下游在仓',
  b2c_days: '调拨周期',
  need: '下游要货量',
  gap: '调拨缺口',
  cover_days: '可撑天数',
  days_to_expire: '距过期天数',
  // 参数键(模板里直接引用 {own_overstock_days} 这类扁平名, 后端 flatten params 后可见)
  transfer_gap_min: '缺口下限',
  own_overstock_days: '积压天数阈值',
  own_expiry_buffer_days: '效期缓冲',
  // 2026-11-01 补全: 参数表单里可配置的其余键 —— 用户把 `own.gap > params.xxx` 写成这些参数时,
  //   条件右侧原样显示 params.b_to_c_days 这类英文路径(fieldLbl 回落到原始字符串)。
  //   凡表单可填的参数都必须在此有中文名, 否则"可视化可配置"在列表回显上打折。
  b_to_c_days: 'B→C调拨天数',
  c_safety_days: 'C仓缓冲天数',
  ship_to_b_days: '自有仓→B仓时效',
  safety_multiplier: '安全库存天数',
  turnover_warning_15: '仓储费阈值',
  turnover_warning_90: '周转考核红线',
  lead_time_days: '前置期',
  purchase_lead_days: '采购前置期',
  purchase_safety_days: '采购安全库存',
  moq: 'MOQ最小起订',
  max_turnover_days: '目标周转天数',
  // 断货规则参数(条件里 params.lit 已覆盖, 其余供右侧引用时显示)
  otif_min: 'OTIF置信下限',
  ss_z: '动态SS Z值',
  sigma_days: '波动率σ样本天数',
  accel_ratio: '加速倍率阈值',
  accel_min_qty: '加速样本量',
  buffer_orange: '橙灯缓冲上限',
  buffer_yellow: '黄灯缓冲上限',
  orange_slack_days: '橙灯天数余量',
  include_avail_zero: '已断纳入红灯',
  health_good: '健康档位线',
  log: '审计日志',
  alert_enabled: '告警开关',
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
            fontWeight: 600,
            fontSize: 'var(--font-10)',
          }}
        >
          {VARS[parts[i + 1]] || parts[i + 1]}
        </span>
      )
    if (i % 3 === 2) return null
    return <span key={i}>{p}</span>
  })
}
export const LF = [
  { l: '可用库存', v: 'inv.available_qty' },
  { l: '安全库存', v: 'inv.safety_qty' },
  { l: '在途库存', v: 'inv.in_transit_qty' },
  { l: '锁定库存', v: 'inv.locked_qty' },
  { l: '可用+在途', v: 'inv.available_qty + inv.in_transit_qty' },
  { l: '可用+在途+锁定', v: 'inv.available_qty + inv.in_transit_qty + inv.locked_qty' },
  // 2026-10-30 可售口径(与内建低库存/断货/采购同源): 顶层 sellable=可用+在途, transit=在途
  { l: '可售(可用+在途)', v: 'sellable' },
  { l: '在途(单列)', v: 'transit' },
  { l: '可售缺口(安全线−可售)', v: 'safety - sellable' },
  { l: '可售/安全线(比例)', v: 'sellable / safety' },
  { l: '动态安全线-可用(缺口)', v: 'inv.safety_qty - inv.available_qty' },
  { l: '可用/动态安全线(比例)', v: 'inv.available_qty / inv.safety_qty' },
  { l: '日销(定时任务提供)', v: 'daily_sales' },
  { l: '可撑天数(可用/日销)', v: 'inv.available_qty / daily_sales' },
  { l: '距上次销售(天)', v: 'days_since_last' },
  { l: '库存量', v: 'stock' },
  { l: '仓库类型', v: 'inv.warehouse_type' },
  { l: '69码', v: 'barcode' },
  { l: '订单数量', v: 'order.quantity' },
  // 2026-10-29 累计订单量(超卖判定应使用此字段: 单笔口径会漏报累计超卖)
  { l: '累计订单量', v: 'order.total_qty' },
  { l: '最大单笔量', v: 'order.max_quantity' },
  { l: '最小单笔量', v: 'order.min_quantity' },
  { l: '本批行数', v: 'order.line_count' },
  // 出入库事件字段
  { l: '出入库数量', v: 'flow.quantity' },
  { l: '出入库仓库', v: 'flow.warehouse' },
  { l: '出入库日期', v: 'flow.date' },
  { l: '入库数量', v: 'inbound.quantity' },
  { l: '出库数量', v: 'outbound.quantity' },
  // ctx 扩展字段(2026-10-29): 缺失时条件自动跳过(不按 0 误判)
  { l: '批次剩余效期(天)', v: 'batch.days_to_expire' },
  { l: '供应商评分', v: 'supplier.score' },
  { l: '周转天数', v: 'inv.turnover_days' },
  { l: '订单金额', v: 'order.total_amount' },
  { l: '订单数量×单价', v: 'order.quantity * order.unit_price' },
  { l: '单价', v: 'order.unit_price' },
  { l: '可售天数 Adj-DOS(看板断货同源)', v: 'inv.adj_dos' },
  { l: '缓冲比 Buffer(可用/动态安全线)', v: 'inv.buffer' },
  { l: '在途置信 OTIF(供应商分/5)', v: 'inv.otif' },
  { l: '动态安全线 SS(规则参数)', v: 'inv.ss_dyn' },
  { l: '加速倍率(当天 vs 前3天)', v: 'inv.accel_rate' },
  { l: '库存健康分(渠道)', v: 'health.score' },
  { l: '补货周期 L/T(规则参数)', v: 'params.lit' },
  { l: '健康预警档位(规则参数)', v: 'params.health_warning' },
  // 2026-11-01 自有仓供给侧口径 —— 自有仓(集货仓)不产生销售(daily_sales_snapshot 只有平台仓行),
  //   逐仓日销 ds=0 → 动态安全线=0 → 低库存口径恒不触发(口径错配, 非 bug)。
  //   自有仓是供给侧, 该判的是「下游要货备不备得出 / 压太多 / 会不会过期」, 故另开 own.* 变量组。
  //   注意: 这些键**仅在 inv.warehouse_type=='own' 的行注入**, 所以条件里务必带上该前置。
  { l: '— 自有仓 —', v: '' },
  { l: '自有仓可用', v: 'own.available' },
  { l: '自有仓在途', v: 'own.transit' },
  { l: '采购在途(已发未到)', v: 'own.po_transit' },
  { l: '自有可发(在仓+在途+采购在途)', v: 'own.can_ship' },
  { l: '下游日销(平台仓)', v: 'own.downstream_ds' },
  { l: '下游在仓(平台仓合计)', v: 'own.downstream_avail' },
  { l: '调拨周期(天, 与规则同源)', v: 'own.b2c_days' },
  { l: '下游本期要货量', v: 'own.need' },
  { l: '调拨缺口(要货 − 可发)', v: 'own.gap' },
  { l: '自有可撑天数(可用/下游日销)', v: 'own.cover_days' },
]
export const OPS = [
  { l: '小于', v: '<' },
  { l: '小于等于', v: '<=' },
  { l: '大于', v: '>' },
  { l: '大于等于', v: '>=' },
  { l: '等于', v: '==' },
  { l: '不等于', v: '!=' },
]

export const fieldLbl = v => {
  const f = LF.find(x => x.v === v)
  if (f) return f.l
  // 2026-11-01 params.* 引用落到"右侧值"位置时(条件 `own.gap > params.transfer_gap_min`),
  //   原样返回 "params.transfer_gap_min" —— 用户在列表里看到的是英文路径不是人话。
  //   参数名本身不在 LF 里, 单独查 VARS(后者已含自有仓三个阈值的中文名)。
  if (typeof v === 'string' && v.indexOf('params.') === 0) {
    const k = v.split('.')[1]
    return VARS[k] || k
  }
  return v
}
export const opLbl = v => {
  const o = OPS.find(x => x.v === v)
  return o ? o.l : v
}

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
  { value: 'inventory.changed', label: '库存变动' },
  { value: 'order.created', label: '订单创建' },
  { value: 'scheduled.daily', label: '每日定时' },
  // 2026-10-29 出入库事件(此前完全不触发评估)
  { value: 'inbound.created', label: '入库记录' },
  { value: 'outbound.created', label: '出库记录' },
]
export const WHS = [
  { l: '全部', v: '' },
  { l: 'B仓', v: 'platform_b' },
  { l: 'BC合计(BBCC)', v: 'bc' },
  { l: 'C仓', v: 'platform' },
  { l: '自有仓', v: 'own' },
]
export const MODES = [
  { l: '全部', v: '' },
  { l: 'BBCC', v: 'bbcc' },
  { l: '传统多仓', v: 'traditional' },
]
