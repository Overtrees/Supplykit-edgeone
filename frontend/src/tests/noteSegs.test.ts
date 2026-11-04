import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, it, expect } from 'vitest'
import { renderNoteSegs, renderNote } from '../pages/insights/shared'
import { t, setLocale } from '../locale'
import type { NoteSeg } from '../api/types'

/** 结构化备注(2026-11-04): 后端 note_segs 走 i18n, 旧 note 字符串兜底。
 *  重点锁「键名泄漏」—— 未知片段键绝不能把 'seg.xxx' 原文显示到界面上。 */
describe('结构化备注片段', () => {
  it('无 segs 时返回 null(调用方回落 note)', () => {
    expect(renderNoteSegs()).toBeNull()
    expect(renderNoteSegs([])).toBeNull()
    // 故意传非法入参: 验证运行时 Array.isArray 兜底(类型层已收紧, 用断言保留该边界用例)
    expect(renderNoteSegs('bad' as unknown as NoteSeg[])).toBeNull()
  })

  it('已知键正常渲染', () => {
    expect(renderNoteSegs([{ k: 'ok' }])).not.toBeNull()
    expect(renderNoteSegs([{ k: 'trend', t7: 'down', t14: 'flat', x: 'down7' }])).not.toBeNull()
    expect(
      renderNoteSegs([{ k: 'b_sug', n: 120, c_gap: 54, b_gap: 53, consume: 67, box: 12 }]),
    ).not.toBeNull()
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
    expect(t('seg.b_sug', { n: 120, c_gap: 54, b_gap: 53, consume: 67, box: 12 })).toContain(
      'B建议补120件',
    )
    setLocale('en')
    expect(t('seg.ok')).toBe('stock sufficient')
    expect(t('seg.b_sug', { n: 120, c_gap: 54, b_gap: 53, consume: 67, box: 12 })).toContain(
      'Suggest 120 to B',
    )
    expect(t('task.win', { n: 1 })).toBe('Window 1')
    setLocale('zh') // 还原, 避免污染后续用例
  })

  it('结构化片段里的 emoji 标记同样渲染成 SVG(不退化成原生 emoji)', () => {
    setLocale('zh')
    // 词条自带 🔴: 旧 note 路径会转 SVG, 结构化路径也必须转
    const critical = JSON.stringify(renderNoteSegs([{ k: 'critical' }]))
    expect(critical).not.toContain('🔴')
    expect(critical).toContain('circle') // 🔴 → <circle fill="var(--danger)">
    // 趋势 emoji 是 t() 参数注入的, 一样要被扫描到
    const trend = JSON.stringify(renderNoteSegs([{ k: 'trend', t7: 'down', t14: 'flat' }]))
    expect(trend).not.toContain('📉')
    expect(trend).not.toContain('➡️')
    expect(trend).toContain('svg')
    // 旧 note 字符串路径行为不变
    expect(JSON.stringify(renderNote('🔴 已濒临'))).not.toContain('🔴')
    // 英文词条同样不泄漏 emoji
    setLocale('en')
    expect(JSON.stringify(renderNoteSegs([{ k: 'critical' }]))).not.toContain('🔴')
    setLocale('zh')
  })

  it('真实 HTML 输出里备注是 <svg> 而非 emoji 字符', () => {
    const toHtml = (segs: NoteSeg[]) =>
      renderToStaticMarkup(React.createElement(React.Fragment, null, renderNoteSegs(segs)))
    setLocale('zh')
    const zh = toHtml([
      { k: 'critical' },
      { k: 'trend', t7: 'down', t14: 'flat', x: 'down7' },
      { k: 'safety_bc_near', d: 9, p: 12 },
    ])
    expect(zh).toContain('<svg')
    expect(zh).toContain('已濒临')
    expect(zh).toContain('7天走弱')
    expect(zh).not.toMatch(/🔴|📉|➡️|⚠️/)
    setLocale('en')
    const en = toHtml([{ k: 'p_urgent', d: 4, p: 8 }])
    expect(en).toContain('<svg')
    expect(en).toContain('stockout alert')
    expect(en).not.toMatch(/🔴|⚠️/)
    setLocale('zh')
  })
})
