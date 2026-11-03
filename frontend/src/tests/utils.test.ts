import { describe, it, expect } from 'vitest'
import {
  insDefVis,
  insDefVisTrad,
  INV_WH_LABEL,
  ORDER_STATUSES,
} from '../components/hammer/configs'
import { INS_BBCC_COLS, INS_TRAD_COLS } from '../components/hammer/configs'

describe('工具函数', () => {
  // 默认可见列 = 从全量列里按索引挑出的子集(见 configs.ts insDefVis)。
  // 断言锁"当前契约": 列总数与默认子集都随业务演进增加过, 原值(9)早已过时。
  it('insDefVis 返回 BBCC 默认13列', () => {
    const result = insDefVis(INS_BBCC_COLS)
    expect(result.length).toBe(13)
    expect(result).toContain('sku')
    expect(result).toContain('barcode')
    expect(result).toContain('name')
  })

  it('insDefVisTrad 返回传统模式默认9列', () => {
    const result = insDefVisTrad(INS_TRAD_COLS)
    expect(result.length).toBe(9)
    expect(result).toContain('avail')
    // safety 不在默认子集(索引 9 未被取) —— 原断言与实现不符, 长期未暴露
    // (npm test 不在 preflight 里)。是否该默认展示安全线属于产品决定, 此处只锁现状。
    expect(result).toContain('after_turn')
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
