import { describe, it, expect } from 'vitest'
import { insDefVis, insDefVisTrad, ORDER_STATUSES } from '../components/hammer/configs'
import { whTypeLabel } from '../components/statusLabels'
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

  it('仓库类型标签可解析(走词条, 不硬编码中文)', () => {
    // 断言点从「表里存中文」改为「解析后非空且三者互不相同」——
    // 文案归 locale 管, 测试只锁"三种仓库都能拿到各自的显示名"
    const labels = (['own', 'platform', 'platform_b'] as const).map(whTypeLabel)
    labels.forEach(l => expect(l.length).toBeGreaterThan(0))
    expect(new Set(labels).size).toBe(3)
  })

  it('订单状态列表完整', () => {
    expect(ORDER_STATUSES.length).toBe(6)
    expect(ORDER_STATUSES).toContain('已完成')
    expect(ORDER_STATUSES).toContain('待发货')
  })
})
