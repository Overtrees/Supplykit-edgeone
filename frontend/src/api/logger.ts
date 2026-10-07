// 前端异常统一上报(收口: POST /api/logs/frontend → quality_logs source=frontend, 2026-09-15)
// 限频: 同 (type+message 前 40 字) 5 分钟内仅 1 条(内存 Map, 防噪音)
const _dedup = new Map<string, number>()
const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

export function reportError(type: string, message: string, details?: string, level = 'error') {
  try {
    const key = type + ':' + String(message || '').slice(0, 40)
    const now = Date.now()
    const last = _dedup.get(key)
    if (last && now - last < 5 * 60 * 1000) return
    _dedup.set(key, now)
    if (_dedup.size > 100) _dedup.clear()
    fetch(API + '/api/logs/frontend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        log_type: type,
        level,
        message: String(message || '').slice(0, 200),
        details: String(details || '').slice(0, 800),
        url: typeof location !== 'undefined' ? location.href : '',
      }),
    }).catch(() => {})
  } catch (e) {
    /* 上报失败静默 */
  }
}
