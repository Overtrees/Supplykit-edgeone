// 共享列配置和工具函数
/**
 * 列定义: **只存 id**(以及需要覆盖时给 width), 标签一律由 `colLabel(id)` 出双语。
 * 2026-11-04 红线 B: 原 label 写死中文, 表头/列选择面板/锤子菜单三处同时半中半英。
 */
export interface ColumnDef {
  id: string
  /** 仅当列 id 无法直接映射词条时使用(如 seq 序号列留空) */
  label?: string
}

export type WarehouseType = 'own' | 'platform' | 'platform_b'

export const PRODUCT_COLS: ColumnDef[] = [
  { id: 'barcode', label: 'barcode' },
  { id: 'channel', label: 'channel' },
  { id: 'brand', label: 'brand' },
  { id: 'batch_days', label: 'batch_days' },
  { id: 'sku', label: 'sku' },
  { id: 'name', label: 'name' },
  { id: 'store', label: 'store' },
  { id: 'cat', label: 'cat' },
  { id: 'price', label: 'price' },
  { id: 'box', label: 'box' },
  { id: 'unit', label: 'unit' },
  { id: 'weight', label: 'weight' },
  { id: 'volume', label: 'volume' },
  { id: 'status', label: 'status' },
  // 2026-11-01 是否做效期管理(只读展示, 编辑统一走清洗页导入)
  //   三态: 是 / 否 / 未标注(空) —— 空不是"否", 它会让出入库按"做效期"处理并告警
  { id: 'expiry_managed', label: 'expiry_managed' },
]
export const prodColKey = ch => 'c_cols_products_' + ch
export const getProdVis = ch => {
  try {
    return JSON.parse(localStorage.getItem(prodColKey(ch)) || 'null')
  } catch {
    return null
  }
}

export const SUPPLIER_COLS = [
  // 2026-11-06 补渠道列: 商品页早有 channel 列, 供应商页漏了 —— 同一份 seed 数据
  //   按渠道分成 -JD/-OTHER 两套, 展示上却看不出归属。列标签走 colLabel('channel'),
  //   复用商品页已有词条, 不新增 i18n key。
  { id: 'channel', label: 'channel' },
  { id: 'brand', label: 'brand' },
  { id: 'code', label: 'code' },
  { id: 'name', label: 'name' },
  { id: 'contact', label: 'contact' },
  { id: 'phone', label: 'phone' },
  { id: 'score', label: 'score' },
]
export const suppColKey = ch => 'c_cols_suppliers_' + ch
export const getSuppVis = ch => {
  try {
    return JSON.parse(localStorage.getItem(suppColKey(ch)) || 'null')
  } catch {
    return null
  }
}

export const ORDER_COLS = [
  { id: 'date', label: 'date' },
  { id: 'order_no', label: 'order_no' },
  { id: 'barcode', label: 'barcode' },
  { id: 'store', label: 'store' },
  { id: 'warehouse', label: 'warehouse' },
  { id: 'product', label: 'product' },
  { id: 'quantity', label: 'quantity' },
  { id: 'unit_price', label: 'unit_price' },
  { id: 'amount', label: 'amount' },
  { id: 'status', label: 'status' },
  { id: 'paid_at', label: 'paid_at' },
]
// 订单状态**取值**必须与库里一致(中文是数据不是文案), 展示走 components/statusLabels.ts
// 的 orderStatusLabel(); 传空串表示"全部"。
export const ORDER_STATUSES = ['', '已完成', '待发货', '已发货', '待确认', '申请退款']
export const orderColKey = ch => 'c_cols_orders_' + ch
export const getOrderVis = ch => {
  try {
    return JSON.parse(localStorage.getItem(orderColKey(ch)) || 'null')
  } catch {
    return null
  }
}

export const INV_COLS = {
  own: [
    { id: 'warehouse', label: 'warehouse' },
    { id: 'brand', label: 'brand' },
    { id: 'sku', label: 'sku' },
    { id: 'barcode', label: 'barcode' },
    { id: 'name', label: 'name' },
    { id: 'price', label: 'price' },
    { id: 'begin', label: 'begin' },
    { id: 'transit', label: 'transit' },
    { id: 'month_in', label: 'month_in' },
    { id: 'month_out', label: 'month_out' },
    // 2026-11-04 平均库存法(用户定案): 期末 = 期初+入-出, 平均 = (期初+期末)/2,
    //   周转 = 平均 × 当月天数 / 当月出库 —— 三个数在同屏可对账, 缺列就只能"信结果"
    { id: 'end_stock', label: 'end_stock' },
    { id: 'avg_stock', label: 'avg_stock' },
    { id: 'prod_date', label: 'prod_date' },
    { id: 'exp_date', label: 'exp_date' },
    { id: 'batch_days', label: 'batch_days' },
    { id: 'eff_status', label: 'eff_status' },
    { id: 'over_third', label: 'over_third' },
    { id: 'avail', label: 'avail' },
    { id: 'keep_days', label: 'keep_days' },
    { id: 'turnover', label: 'turnover' },
    { id: 'note', label: 'note' },
    { id: 'stock_amount', label: 'stock_amount' },
  ],
  platform: [
    { id: 'channel', label: 'channel' },
    { id: 'warehouse', label: 'warehouse' },
    { id: 'brand', label: 'brand' },
    { id: 'sku', label: 'sku' },
    { id: 'barcode', label: 'barcode' },
    { id: 'name', label: 'name' },
    { id: 'price', label: 'price' },
    { id: 'transit', label: 'transit' },
    { id: 'prod_date', label: 'prod_date' },
    { id: 'exp_date', label: 'exp_date' },
    { id: 'batch_days', label: 'batch_days' },
    { id: 'eff_status', label: 'eff_status' },
    { id: 'avail', label: 'avail' },
    { id: 'note', label: 'note' },
    { id: 'stock_amount', label: 'stock_amount' },
  ],
  platform_b: [
    { id: 'channel', label: 'channel' },
    { id: 'warehouse', label: 'warehouse' },
    { id: 'brand', label: 'brand' },
    { id: 'sku', label: 'sku' },
    { id: 'barcode', label: 'barcode' },
    { id: 'name', label: 'name' },
    { id: 'price', label: 'price' },
    { id: 'transit', label: 'transit' },
    { id: 'c_transit', label: 'c_transit' },
    { id: 'prod_date', label: 'prod_date' },
    { id: 'exp_date', label: 'exp_date' },
    { id: 'batch_days', label: 'batch_days' },
    { id: 'eff_status', label: 'eff_status' },
    { id: 'avail', label: 'avail' },
    { id: 'note', label: 'note' },
    { id: 'stock_amount', label: 'stock_amount' },
  ],
}
export const INV_COL_KEY = 'c_cols_inventory'
export const getInvVis = (wt, ch) => {
  try {
    return JSON.parse(localStorage.getItem(INV_COL_KEY + '_' + ch + '_' + wt) || 'null')
  } catch {
    return null
  }
}
export const invColKey = (wt, ch) => INV_COL_KEY + '_' + ch + '_' + wt
export const INS_BBCC_COLS = [
  { id: 'seq', label: '' },
  { id: 'brand', label: 'brand' },
  { id: 'sku', label: 'sku' },
  { id: 'barcode', label: 'barcode' },
  { id: 'name', label: 'name' },
  { id: 'warehouse', label: 'warehouse' },
  { id: 'b_transit', label: 'b_transit' },
  { id: 'b_stock', label: 'b_stock' },
  { id: 'b_turn', label: 'b_turn' },
  { id: 'c_stock', label: 'c_stock' },
  { id: 'transit', label: 'transit' },
  { id: 'sales', label: 'sales' },
  { id: 'c_turn', label: 'c_turn' },
  { id: 'transit_turn', label: 'transit_turn' },
  { id: 'suggest', label: 'suggest' },
  { id: 'b_suggest', label: 'b_suggest' },
  { id: 'cur_turn', label: 'cur_turn' },
  { id: 'after_turn', label: 'after_turn' },
  { id: 'note', label: 'note' },
  { id: 'action', label: 'action' },
]
export const INS_TRAD_COLS = [
  { id: 'seq', label: '' },
  { id: 'brand', label: 'brand' },
  { id: 'sku', label: 'sku' },
  { id: 'barcode', label: 'barcode' },
  { id: 'name', label: 'name' },
  { id: 'store', label: 'store' },
  { id: 'avail', label: 'avail' },
  { id: 'transit', label: 'transit' },
  { id: 'sales', label: 'sales' },
  { id: 'safety', label: 'safety' },
  { id: 'turn', label: 'turn' },
  { id: 'after_turn', label: 'after_turn' },
  { id: 'suggest', label: 'suggest' },
  { id: 'note', label: 'note' },
]
export const INS_PURCHASE_COLS = [
  { id: 'brand', label: 'brand' },
  { id: 'barcode', label: 'barcode' },
  { id: 'sku', label: 'sku' },
  { id: 'name', label: 'name' },
  { id: 'warehouse', label: 'warehouse' },
  { id: 'sys_available', label: 'sys_available' },
  { id: 'sys_transit', label: 'sys_transit' },
  { id: 'daily_sales', label: 'daily_sales' },
  { id: 'actual_purchase', label: 'actual_purchase' },
  { id: 'after_turnover', label: 'after_turnover' },
  { id: 'note', label: 'note' },
  { id: 'timing', label: 'timing' },
]
export const INS_SLOW_COLS = [
  { id: 'processed', label: 'processed' },
  { id: 'brand', label: 'brand' },
  { id: 'sku', label: 'sku' },
  { id: 'name', label: 'name' },
  { id: 'warehouse', label: 'warehouse' },
  { id: 'days', label: 'days' },
  { id: 'stock', label: 'stock' },
  { id: 'level', label: 'level' },
  { id: 'note', label: 'note' },
]

export const insColKey = (m, ch) => 'c_cols_insights_' + ch + '_' + m
export const getInsVis = (m, ch) => {
  try {
    return JSON.parse(localStorage.getItem(insColKey(m, ch)) || 'null')
  } catch {
    return null
  }
}
export function insDefVis(cols) {
  return cols
    .map(c => c.id)
    .filter((_, i) => [0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 14, 15, 18].includes(i))
}
export function insDefVisTrad(cols) {
  return cols.map(c => c.id).filter((_, i) => [0, 1, 2, 3, 4, 5, 6, 10, 11].includes(i))
}
