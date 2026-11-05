// 国际化运行时 - 轻量级 i18n，无外部依赖。词条本体见 locale.zh.ts / locale.en.ts。
import { en } from './locale.en'
import { zh } from './locale.zh'
export { zh, en }

type Locale = 'zh' | 'en'
const locales: Record<Locale, Record<string, string>> = { zh, en }

function detectLocale(): Locale {
  const saved = localStorage.getItem('c_locale') as Locale | null
  if (saved === 'zh' || saved === 'en') return saved
  // 跟随系统语言
  const lang = (navigator.language || navigator.languages?.[0] || '').toLowerCase()
  if (lang.startsWith('zh') || lang.startsWith('yue') || lang.startsWith('wuu')) return 'zh'
  return 'en'
}

let currentLocale: Locale = detectLocale()

export function t(key: string, params?: Record<string, string | number>): string {
  const text = locales[currentLocale]?.[key] || locales['zh'][key] || key
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (_, k) => String(params[k] ?? ''))
}

/**
 * 把语言同步到 **文档级** 的地方 —— 这些位置不在 React 树里, `t()` 换了字典它们不会跟着变。
 *
 * 2026-11-05 补: 此前全仓没有一个地方读过 `app.name` / `app.desc`, 标题写死在 index.html,
 * `document.title` 也从不更新 —— 切到 EN 时浏览器标签页、PWA 名、分享卡片仍是中文。
 * 这不是"两个多余词条", 而是一处门禁查不出的 i18n 死角(静态 HTML 不在 t() 的覆盖面上)。
 *
 * 只在有 document 的环境执行: 回归/单测跑在 jsdom 里没问题, 但 SSR 或纯 node 脚本会没有它。
 */
function applyDocumentLang(locale: Locale) {
  if (typeof document === 'undefined') return
  // title: 标签页 / 书签 / 分享卡片标题。屏幕阅读器也会读它(语言切换后读法要跟着变)
  const name = t('app.name')
  // t() 在缺词条时回落到键名(如 'app.name'), 那种情况宁可不动, 也别把键名显示到标题栏
  if (name && name !== 'app.name') document.title = name
  // meta description: 全局设置, 不进 React 树
  const desc = t('app.desc')
  if (desc && desc !== 'app.desc') {
    let meta = document.querySelector('meta[name="description"]')
    if (!meta) {
      meta = document.createElement('meta')
      meta.setAttribute('name', 'description')
      document.head.appendChild(meta)
    }
    meta.setAttribute('content', desc)
  }
  // <html lang>: 决定屏幕阅读器发音、浏览器翻译提示、CJK 字体回退。
  //   zh-CN 用地区码(中文简体的标准写法); en 用 en 而不是 en-US, 与字典"界面用语"口径一致
  document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
}

export function setLocale(locale: Locale) {
  currentLocale = locale
  localStorage.setItem('c_locale', locale)
  applyDocumentLang(locale)
}

export function getLocale(): Locale {
  return currentLocale
}

/**
 * 启动时同步一次 —— 必须显式调用, 不能只在 setLocale 里做:
 * 首次进入时语言是从 localStorage / navigator 探测出来的, 走不到 setLocale,
 * 而 index.html 的 <title>/<html lang> 是静态中文。
 */
export function syncDocumentLang() {
  applyDocumentLang(currentLocale)
}
