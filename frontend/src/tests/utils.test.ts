import { describe, it, expect } from 'vitest'
import {
  insDefVis,
  insDefVisTrad,
  INV_WH_LABEL,
  ORDER_STATUSES,
} from '../components/hammer/configs'
import { INS_BBCC_COLS, INS_TRAD_COLS } from '../components/hammer/configs'

describe('工具函数', () => {
  it('insDefVis 返回 BBCC 默认9列', () => {
    const result = insDefVis(INS_BBCC_COLS)
    expect(result.length).toBe(9)
    expect(result).toContain('sku')
    expect(result).toContain('barcode')
    expect(result).toContain('name')
  })

  it('insDefVisTrad 返回传统模式默认9列', () => {
    const result = insDefVisTrad(INS_TRAD_COLS)
    expect(result.length).toBe(9)
    expect(result).toContain('avail')
    expect(result).toContain('safety')
  })

  it('仓库类型标签定义完整', () => {
    expect(INV_WH_LABEL.own).toBe('自有仓')
    expect(INV_WH_LABEL.platform).toBe('平台仓')
    expect(INV_WH_LABEL.platform_b).toBe('B仓')
  })

  it('订单状态列表完整', () => {
    expect(ORDER_STATUSES.length).toBe(6)
    expect(ORDER_STATUSES).toContain('已完成')
    expect(ORDER_STATUSES).toContain('待发货')
  })
})
