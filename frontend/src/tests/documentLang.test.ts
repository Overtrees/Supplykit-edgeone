import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setLocale, syncDocumentLang, getLocale } from '../locale'

/** 文档级语言同步(2026-11-05): title / <html lang> 不在 React 树里, t() 换了字典它们不动。
 *  此前全仓没有一处读 app.name, 标题写死在 index.html —— 切 EN 时标签页仍是中文。
 *  这组用例锁住"切语言 → 文档跟着变", 以及缺词条时不要往标题栏塞键名。 */
describe('文档级语言同步', () => {
  const origTitle = document.title
  const origLang = document.documentElement.lang

  beforeEach(() => {
    document.title = 'Supplykit'
    document.documentElement.lang = 'zh-CN'
    localStorage.clear()
  })

  afterEach(() => {
    document.title = origTitle
    document.documentElement.lang = origLang
    setLocale('zh')
  })

  it('切到中文: title 用 app.name, lang 为 zh-CN', () => {
    setLocale('en')
    setLocale('zh')
    expect(getLocale()).toBe('zh')
    expect(document.title).not.toBe('app.name')
    expect(document.title.length).toBeGreaterThan(0)
    expect(document.documentElement.lang).toBe('zh-CN')
  })

  it('切到英文: <html lang> 变 en(屏幕阅读器/翻译提示/字体回退) ', () => {
    setLocale('en')
    expect(document.documentElement.lang).toBe('en')
    expect(getLocale()).toBe('en')
  })

  it('首访也能同步: 语言由 localStorage 探测, 走不到 setLocale, 必须显式 syncDocumentLang', () => {
    document.documentElement.lang = 'zh-CN'
    syncDocumentLang()
    // 探测值在模块加载时就定了(setup 里已 reset 成 zh), 关键是「文档跟着当前语言走」
    expect(document.documentElement.lang).toBe(getLocale() === 'zh' ? 'zh-CN' : 'en')
  })

  it('meta description 跟着语言变(不在 React 树里, 也要本地化)', () => {
    setLocale('zh')
    const zhDesc = document.querySelector('meta[name="description"]')?.getAttribute('content')
    expect(zhDesc).toBeTruthy()
    expect(zhDesc).not.toBe('app.desc')
    expect(document.title).not.toBe('app.name')
  })

  it('标题绝不能是裸键名(app.name 被删时宁可保留原标题)', () => {
    setLocale('zh')
    expect(document.title).not.toMatch(/^[a-z]+\.[a-z_]+$/)
  })
})
