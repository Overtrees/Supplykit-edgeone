import { describe, it, expect } from 'vitest'
import { renderNoteSegs, renderNote } from '../pages/insights/shared'
import { t, setLocale } from '../locale'

/** 结构化备注(2026-11-04): 后端 note_segs 走 i18n, 旧 note 字符串兜底。
 *  重点锁「键名泄漏」—— 未知片段键绝不能把 'seg.xxx' 原文显示到界面上。 */
describe('结构化备注片段', () => {
  it('无 segs 时返回 null(调用方回落 note)', () => {
    expect(renderNoteSegs()).toBeNull()
    expect(renderNoteSegs([])).toBeNull()
    expect(renderNoteSegs('bad')).toBeNull()
  })

  it('已知键正常渲染', () => {
    expect(renderNoteSegs([{ k: 'ok' }])).not.toBeNull()
    expect(renderNoteSegs([{ k: 'trend', t7: 'down', t14: 'flat', x: 'down7' }])).not.toBeNull()
    expect(renderNoteSegs([{ k: 'b_sug', n: 120, c_gap: 54, b_gap: 53, consume: 67, box: 12 }]))
      .not.toBeNull()
  })

  it('未知键不得泄漏英文键名: 整段回落', () => {
    // 前后端部署不同步 / 后端未来新增片段类型时, 宁可显示完整中文 note, 不显示 'seg.xxx'
    expect(renderNoteSegs([{ k: 'future_kind', n: 1 }])).toBeNull()
    expect(renderNoteSegs([{ k: 'ok' }, { k: 'future_kind' }])).toBeNull()
  })

  it('trend 的未知趋势后缀也回落', () => {
    expect(renderNoteSegs([{ k: 'trend', t7: 'up', t14: 'flat', x: 'brand_new' }])).toBeNull()
  })

  it('renderNote: segs 不可用时回落原 note 文本(不出现 seg. 键名)', () => {
    const out = renderNote('库存充足', [{ k: 'future_kind' }])
    expect(out).toBeTruthy()
    expect(JSON.stringify(out)).toContain('库存充足')
    expect(JSON.stringify(out)).not.toContain('seg.future_kind')
  })

  it('seg 词条 zh/en 双语齐备(部署后英文界面不再显示中文备注)', () => {
    setLocale('zh')
    expect(t('seg.ok')).toBe('库存充足')
    expect(t('seg.b_sug', { n: 120, c_gap: 54, b_gap: 53, consume: 67, box: 12 }))
      .toContain('B建议补120件')
    setLocale('en')
    expect(t('seg.ok')).toBe('stock sufficient')
    expect(t('seg.b_sug', { n: 120, c_gap: 54, b_gap: 53, consume: 67, box: 12 }))
      .toContain('Suggest 120 to B')
    expect(t('task.win', { n: 1 })).toBe('Window 1')
    setLocale('zh') // 还原, 避免污染后续用例
  })
})
