// 共享列配置和工具函数
export interface ColumnDef {
  id: string
  label: string
}

export type WarehouseType = 'own' | 'platform' | 'platform_b'

export const PRODUCT_COLS: ColumnDef[] = [
  { id: 'barcode', label: '69码' },
  { id: 'channel', label: '平台' },
  { id: 'brand', label: '品牌' },
  { id: 'batch_days', label: '总效期/天' },
  { id: 'sku', label: 'SKU' },
  { id: 'name', label: '名称' },
  { id: 'store', label: '店铺' },
  { id: 'cat', label: '分类' },
  { id: 'price', label: '单价' },
  { id: 'box', label: '箱规' },
  { id: 'unit', label: '单位' },
  { id: 'weight', label: '箱重/KG' },
  { id: 'volume', label: '体积/方' },
  { id: 'status', label: '状态' },
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
  { id: 'brand', label: '品牌' },
  { id: 'code', label: '编号' },
  { id: 'name', label: '名称' },
  { id: 'contact', label: '联系人' },
  { id: 'phone', label: '手机' },
  { id: 'score', label: '评分' },
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
  { id: 'date', label: '下单日期' },
  { id: 'order_no', label: '订单号' },
  { id: 'barcode', label: '69码' },
  { id: 'store', label: '店铺' },
  { id: 'warehouse', label: '仓库' },
  { id: 'product', label: '商品' },
  { id: 'quantity', label: '数量' },
  { id: 'unit_price', label: '单价' },
  { id: 'amount', label: '金额' },
  { id: 'status', label: '状态' },
  { id: 'paid_at', label: '入库日期' },
]
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
    { id: 'warehouse', label: '仓库' },
    { id: 'brand', label: '品牌' },
    { id: 'sku', label: 'SKU' },
    { id: 'barcode', label: '69码' },
    { id: 'name', label: '商品' },
    { id: 'price', label: '单价' },
    { id: 'begin', label: '期初库存' },
    { id: 'transit', label: '在途' },
    { id: 'month_in', label: '当月采购入库' },
    { id: 'month_out', label: '当月出库' },
    { id: 'prod_date', label: '生产日期' },
    { id: 'exp_date', label: '截止日期' },
    { id: 'batch_days', label: '总效期/天' },
    { id: 'eff_status', label: '效期状态' },
    { id: 'over_third', label: '超1/3' },
    { id: 'avail', label: '可用' },
    { id: 'turnover', label: '在库周转' },
    { id: 'note', label: '备注' },
    { id: 'stock_amount', label: '在库金额' },
  ],
  platform: [
    { id: 'channel', label: '平台' },
    { id: 'warehouse', label: '仓库' },
    { id: 'brand', label: '品牌' },
    { id: 'sku', label: 'SKU' },
    { id: 'barcode', label: '69码' },
    { id: 'name', label: '商品' },
    { id: 'price', label: '单价' },
    { id: 'transit', label: '在途' },
    { id: 'prod_date', label: '生产日期' },
    { id: 'exp_date', label: '截止日期' },
    { id: 'batch_days', label: '总效期/天' },
    { id: 'eff_status', label: '效期状态' },
    { id: 'avail', label: '可用' },
    { id: 'note', label: '备注' },
    { id: 'stock_amount', label: '在库金额' },
  ],
  platform_b: [
    { id: 'channel', label: '平台' },
    { id: 'warehouse', label: '仓库' },
    { id: 'brand', label: '品牌' },
    { id: 'sku', label: 'SKU' },
    { id: 'barcode', label: '69码' },
    { id: 'name', label: '商品' },
    { id: 'price', label: '单价' },
    { id: 'transit', label: '供应商-B仓' },
    { id: 'c_transit', label: 'B-C调拨在途' },
    { id: 'prod_date', label: '生产日期' },
    { id: 'exp_date', label: '截止日期' },
    { id: 'batch_days', label: '总效期/天' },
    { id: 'eff_status', label: '效期状态' },
    { id: 'avail', label: '可用' },
    { id: 'note', label: '备注' },
    { id: 'stock_amount', label: '在库金额' },
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
export const INV_WH_LABEL = { own: '自有仓', platform: '平台仓', platform_b: 'B仓' }

export const INS_BBCC_COLS = [
  { id: 'seq', label: '' },
  { id: 'brand', label: '品牌' },
  { id: 'sku', label: 'SKU' },
  { id: 'barcode', label: '69码' },
  { id: 'name', label: '商品' },
  { id: 'warehouse', label: '仓库' },
  { id: 'b_transit', label: '供应商-B仓' },
  { id: 'b_stock', label: 'B仓可用库存' },
  { id: 'b_turn', label: 'B仓周转' },
  { id: 'c_stock', label: 'C仓总和可用' },
  { id: 'transit', label: 'B-C调拨在途' },
  { id: 'sales', label: 'C仓日销' },
  { id: 'c_turn', label: 'C仓周转' },
  { id: 'transit_turn', label: 'B→C调拨周转' },
  { id: 'suggest', label: 'C仓建议补' },
  { id: 'b_suggest', label: 'B仓需补' },
  { id: 'cur_turn', label: '当前综转' },
  { id: 'after_turn', label: '补后综转' },
  { id: 'note', label: '备注' },
  { id: 'action', label: '标记操作（用于B仓统计入库批次）' },
]
export const INS_TRAD_COLS = [
  { id: 'seq', label: '' },
  { id: 'brand', label: '品牌' },
  { id: 'sku', label: 'SKU' },
  { id: 'barcode', label: '69码' },
  { id: 'name', label: '商品' },
  { id: 'store', label: '仓库' },
  { id: 'avail', label: '现有' },
  { id: 'transit', label: '在途' },
  { id: 'sales', label: '日销' },
  { id: 'safety', label: '安全线' },
  { id: 'turn', label: '在库周转' },
  { id: 'after_turn', label: '补后周转' },
  { id: 'suggest', label: '建议补' },
  { id: 'note', label: '备注' },
]
export const INS_PURCHASE_COLS = [
  { id: 'brand', label: '品牌' },
  { id: 'barcode', label: '69码' },
  { id: 'sku', label: 'SKU' },
  { id: 'name', label: '商品' },
  { id: 'warehouse', label: '仓库' },
  { id: 'sys_available', label: '可用' },
  { id: 'sys_transit', label: '在途' },
  { id: 'daily_sales', label: '日销(融合/14/28)' },
  { id: 'actual_purchase', label: '建议采购(含箱规取整)' },
  { id: 'after_turnover', label: '补后周转' },
  { id: 'note', label: '备注' },
  { id: 'timing', label: '采购时机' },
]
export const INS_SLOW_COLS = [
  { id: 'processed', label: '处理' },
  { id: 'brand', label: '品牌' },
  { id: 'sku', label: 'SKU' },
  { id: 'name', label: '商品' },
  { id: 'warehouse', label: '仓库' },
  { id: 'days', label: '未售天数' },
  { id: 'stock', label: '库存' },
  { id: 'level', label: '等级' },
  { id: 'note', label: '备注' },
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
