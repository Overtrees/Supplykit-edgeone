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

export function setLocale(locale: Locale) {
  currentLocale = locale
  localStorage.setItem('c_locale', locale)
}

export function getLocale(): Locale {
  return currentLocale
}
