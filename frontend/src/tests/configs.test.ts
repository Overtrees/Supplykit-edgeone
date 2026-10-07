import { describe, it, expect } from 'vitest'
import {
  PRODUCT_COLS,
  SUPPLIER_COLS,
  ORDER_COLS,
  INV_COLS,
  INS_BBCC_COLS,
  INS_TRAD_COLS,
  INS_PURCHASE_COLS,
  INS_SLOW_COLS,
} from '../components/hammer/configs'

describe('列配置', () => {
  it('商品页列定义完整', () => {
    expect(PRODUCT_COLS.length).toBe(12)
    const ids = PRODUCT_COLS.map(c => c.id)
    expect(ids).toContain('barcode')
    expect(ids).toContain('sku')
    expect(ids).toContain('name')
  })

  it('供应商页列定义完整', () => {
    expect(SUPPLIER_COLS.length).toBe(5)
    expect(SUPPLIER_COLS.map(c => c.id)).toEqual(['code', 'name', 'contact', 'phone', 'score'])
  })

  it('订单页列定义完整', () => {
    expect(ORDER_COLS.length).toBe(9)
    const ids = ORDER_COLS.map(c => c.id)
    expect(ids).toContain('order_no')
    expect(ids).toContain('barcode')
    expect(ids).toContain('status')
  })

  it('进销存页三视图列定义完整', () => {
    expect(INV_COLS.own.length).toBe(10)
    expect(INV_COLS.platform.length).toBe(7)
    expect(INV_COLS.platform_b.length).toBe(8)
  })

  it('BBCC 列定义完整', () => {
    expect(INS_BBCC_COLS.length).toBe(18)
    const ids = INS_BBCC_COLS.map(c => c.id)
    expect(ids).toContain('b_stock')
    expect(ids).toContain('c_stock')
    expect(ids).toContain('b_suggest')
  })

  it('传统模式列定义完整', () => {
    expect(INS_TRAD_COLS.length).toBe(13)
    const ids = INS_TRAD_COLS.map(c => c.id)
    expect(ids).toContain('avail')
    expect(ids).toContain('safety')
    expect(ids).toContain('suggest')
  })

  it('采购建议列定义完整', () => {
    expect(INS_PURCHASE_COLS.length).toBe(10)
    const ids = INS_PURCHASE_COLS.map(c => c.id)
    expect(ids).toContain('actual_purchase')
    expect(ids).toContain('timing')
  })

  it('滞销预警列定义完整', () => {
    expect(INS_SLOW_COLS.length).toBe(9)
    const ids = INS_SLOW_COLS.map(c => c.id)
    expect(ids).toContain('level')
    expect(ids).toContain('days')
  })
})
