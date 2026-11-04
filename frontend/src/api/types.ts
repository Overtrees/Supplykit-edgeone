import type { AxiosRequestConfig } from 'axios'

// 后端接口数据形状（与 cloud-functions 契约对齐）
// 约定: 已知字段枚举精确类型, 动态扩展字段一律 unknown——消费方在边界处收敛(Number/String/断言),
// 不使用 any(红线)。2026-11-04 全仓 any 治理: 统一在这里放共享契约, 页面按需 import。
// 依据: docs/DEVELOPMENT.md §4.1「避免 any 类型: 优先使用具体类型或泛型」/ §15.38「any 红线」。

/** 结构未知的响应体: 边界处用 String()/Number()/显式泛型收敛, 不假装字段已知 */
export type ApiObject = Record<string, unknown>

/** 通用列表响应(传具体行类型给 T, 别用裸 ApiObject 硬取字段) */
export interface ListResponse<T = ApiObject> {
  items: T[]
  total?: number
  [k: string]: unknown
}

/** 统一 {ok, data, error} 响应 */
export interface OkResponse<T = unknown> {
  ok: boolean
  data?: T
  error?: string
  [k: string]: unknown
}

/** api.get 的结果包装(headers/config 不参与业务, 保持 unknown) */
export interface ApiResult<T> {
  data: T
  status: number
  statusText: string
  headers: unknown
  config: unknown
}

/** GET 配置: axios 原生配置 + 本通道的 scope/自定义键 */
export interface ApiGetConfig extends AxiosRequestConfig {
  scope?: string
  [k: string]: unknown
}

/** 缓存条目 */
export interface CacheEntry {
  data: unknown
  ts: number
}

/** 在途请求: 原生 Promise 交叉挂起兜底元数据(不用 any 给 Promise 挂属性) */
export type InflightEntry<T = unknown> = Promise<T> & {
  __ts?: number
  __scope?: string
  __ac?: AbortController
  __dead?: boolean
}

/** 结构化备注片段(与 cloud-functions/api/core/note_segs.py 契约对齐) */
export type NoteSeg = { k: string } & Record<string, string | number>

/** 动态业务行的兜底(优先用 InventoryRow 等精确接口) */
export type DynamicRow = Record<string, unknown>

/** 库存/告警明细行（跨页共用的最小字段集, 按需扩展） */
export interface InventoryRow {
  sku?: string
  product_name?: string
  level?: string
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
