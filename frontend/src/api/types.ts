// 后端接口数据形状（与 cloud-functions 契约对齐）
// 约定: 已知字段枚举精确类型, 动态扩展字段一律 unknown——消费方在边界处收敛(Number/String/断言),
// 不使用 any(红线)

/** 库存/告警明细行（跨页共用的最小字段集, 按需扩展） */
export interface InventoryRow {
  sku?: string
  product_name?: string
  warehouse?: string
  warehouse_type?: string
  barcode?: string
  available_qty?: number
  safety_qty?: number
  in_transit_qty?: number
  locked_qty?: number
  days_since_last?: number
  days_to_empty?: number
  expiry_date?: string
  exp_date?: string
  prod_date?: string
  status?: string
  order_quantity?: number
  adj_dos?: number
  buffer?: number
  health_score?: number
  [k: string]: unknown
}

/**
 * /api/dashboard/stock-risk 双形态响应:
 * 数组 = 旧版(仅 items/total); 对象 = 新版(bc/c/own 三级分类维度)
 */
export interface StockRiskShim {
  items: InventoryRow[]
  total: number
  critical?: number
  warning?: number
  bcItems?: InventoryRow[]
  bcTotal?: number
  bcCritical?: number
  bcWarning?: number
  cItems?: InventoryRow[]
  cTotal?: number
  cCritical?: number
  cWarning?: number
  ownItems?: InventoryRow[]
  ownTotal?: number
  ownCritical?: number
  ownWarning?: number
  _full?: InventoryRow[]
}
