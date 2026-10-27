import axios from 'axios'
import { reportError } from './logger'

/* eslint-disable no-console -- API 调试日志(开发期保留, 便于追接口链路) */
const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

// 响应缓存（内存）
const cache = new Map()
const CACHE_TTL = 30_000 // 30s 内复用缓存

function cacheKey(method, url, params) {
  return method + ':' + url + ':' + JSON.stringify(params || {})
}

// 底层 axios 实例
const instance = axios.create({
  baseURL: BASE,
  timeout: 30000,
})

// 请求拦截器：自动注入全局 channel 参数 + 认证 token
instance.interceptors.request.use((config: any) => {
  if (!config.params || !config.params.channel) {
    const ch = localStorage.getItem('c_channel') || 'jd'
    if (config.params) {
      config.params.channel = ch
    } else {
      config.params = { channel: ch }
    }
  }
  // 注入认证 token
  const token = (() => {
    try {
      return localStorage.getItem('c_token')
    } catch {
      return null
    }
  })()
  if (token) {
    config.headers = config.headers || {}
    // 2026-10-13: 网关改写 Authorization(q-sign)——token 走 X-Auth-Token 自定义头
    config.headers['X-Auth-Token'] = token
  }
  return config
})

// 在途请求去重 Map(防同一 URL 并发重复请求)
// ⚠ 挂起兜底: Makers 函数慢/冷启动时请求可能 30s+ 未完成, 若无限复用挂起 promise,
//   后续同 URL 请求全部卡死(loadCfg 等永不返回 → UI 显示旧值, 重进页面才恢复)
//   → 超过 INFLIGHT_TTL 的在途视为挂起, 删除并重新发起
const inflight = new Map()
const INFLIGHT_TTL = 15000

// 响应拦截器：写缓存 + 清理在途 + 日志 + 自动解包 {ok,data}
instance.interceptors.response.use(
  response => {
    const { method, url, params } = response.config
    const key = cacheKey(method || 'get', url || '', params)
    if ((method || 'get').toLowerCase() === 'get') {
      cache.set(key, { data: response.data, ts: Date.now() })
    }
    inflight.delete(key)
    console.debug(
      `[API] ${(method || 'get').toUpperCase()} ${url} → ${response.status}`,
      params && Object.keys(params).length ? params : '',
    )

    // 自动解包统一响应格式 {ok, data, error}
    if (response.data && typeof response.data === 'object' && 'ok' in response.data) {
      if (!response.data.ok) {
        const err: Error & { status?: number } = new Error(response.data.error || '请求失败')
        err.status = response.status
        // 2026-10-13 业务错误收口: ok:false 且 HTTP 200(导入/预览/规则测试等)——曾因 status<400 不上报(导入异常无日志)
        if (response.data.error) {
          reportError(
            'business_error',
            ((response.data.error || '') + ' ' + (url || '')).slice(0, 200),
            'HTTP ' + response.status,
            'warning',
          )
        }
        return Promise.reject(err)
      }
      // 有 data 字段才解包，否则保留原始响应（如清洗接口的 {ok,success,failed}）
      if ('data' in response.data) {
        response.data = response.data.data
      }
    }

    return response
  },
  error => {
    const cfg = (error.config || {}) as any
    const key = cacheKey(cfg.method || 'get', cfg.url || '', cfg.params)
    inflight.delete(key)
    console.debug(`[API] ${(cfg.method || 'get').toUpperCase()} ${cfg.url} → ❌ ${error.message}`)
    // 统一收口: 网络错误/5xx 上报(4xx 业务错误不上报防噪音)
    const status = (error.response || {}).status
    // 401 先标记, 不立即踢(防偶发: 冷实例/慢网络下单接口 401 误伤——曾直接 auth:expired 踢出登录页清空输入)
    // App 收 may-expired 后调 /auth/check 确认, 真失效才 setLoggedIn(false)
    if (status === 401) {
      try {
        window.dispatchEvent(new Event('auth:may-expired'))
      } catch {}
    }
    // 收口: 网络错误 + 5xx(error) + 4xx 校验/业务错误(warning)——422/400 不再靠猜
    if (!status || status >= 400) {
      reportError(
        'api_http_error',
        ((error.message || '') + ' ' + (cfg.url || '')).slice(0, 200),
        status
          ? 'HTTP ' + status + ' ' + JSON.stringify(error.response?.data || '').slice(0, 400)
          : 'network-error',
        status && status >= 500 ? 'error' : 'warning',
      )
    }
    return Promise.reject(error)
  },
)

// 导出带缓存的 get
const apiGet = async <T = any>(
  url: string,
  config: Record<string, any> = {},
): Promise<{ data: T; status: number; statusText: string; headers: any; config: any }> => {
  const key = cacheKey('get', url, config.params)

  // 1) 缓存命中
  const hit = cache.get(key)
  if (hit && Date.now() - hit.ts < CACHE_TTL) {
    // 缓存命中也需解包统一格式 {ok,data}（与拦截器一致）
    let _d = hit.data
    if (_d && typeof _d === 'object' && 'ok' in _d && 'data' in _d) {
      _d = _d.data
    }
    return { data: _d, status: 200, statusText: 'OK', headers: {}, config }
  }

  // 2) 在途去重(挂起兜底: 超 INFLIGHT_TTL 的旧在途不再复用, 防慢请求卡死后续刷新)
  // 2026-10-27 根治跨卸载复用卡骨架: 快速切走/切回(数秒内)会复用到旧页面实例的挂起 promise
  // (旧接口 30-60s 才超时) → 新页卡骨架等旧 promise。对复用 promise 设等待上限: 剩余 TTL 内未完成
  // → 自动发起新请求替换(旧 promise 作废), 保证"切回最多等~15s 后必成功刷新", 不再无限期挂。
  if (inflight.has(key)) {
    const p: any = inflight.get(key)
    if (p && p.__ts && Date.now() - p.__ts > INFLIGHT_TTL) {
      inflight.delete(key)
    } else if (p) {
      const remain = INFLIGHT_TTL - (Date.now() - (p.__ts || Date.now()))
      const waiter = Promise.race([
        p,
        new Promise((_, rej) =>
          setTimeout(() => rej(new Error('INFLIGHT_STALE')), Math.max(remain, 500)),
        ),
      ])
      return waiter.catch(() => {
        if (!p.__dead && inflight.get(key) === p) {
          p.__dead = true
          inflight.delete(key)
          const np: any = instance.get(url, config).then(r => ({
            data: r.data,
            status: r.status,
            statusText: r.statusText,
            headers: r.headers,
            config: r.config,
          }))
          np.__ts = Date.now()
          inflight.set(key, np)
          return np
        }
        return p
      })
    }
  }

  // 3) 发起新请求
  const promise: any = instance.get(url, config).then(r => ({
    data: r.data,
    status: r.status,
    statusText: r.statusText,
    headers: r.headers,
    config: r.config,
  }))
  promise.__ts = Date.now()
  inflight.set(key, promise)
  return promise
}

// 非 GET 请求后清空缓存（数据变了，缓存失效）
function invalidateCache() {
  cache.clear()
}

// 导出 api 对象，保持与原接口兼容
// 2026-10-14 422 根治: iOS Safari 移动端 axios XHR FormData 发送 body 空(boundary 有内容无)——上传类改 fetch 原生(Safari fetch multipart 可靠)
export async function uploadForm(url: string, fd: FormData, timeout = 90000): Promise<any> {
  const token = (() => {
    try {
      return localStorage.getItem('c_token')
    } catch {
      return ''
    }
  })()
  const controller = new AbortController()
  const t = setTimeout(() => controller.abort(), timeout)
  try {
    const resp = await fetch(url, {
      method: 'POST',
      body: fd,
      headers: { 'X-Auth-Token': token || '' },
      signal: controller.signal,
    })
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const err: Error & { status?: number } = new Error(
        'Request failed with status code ' +
          resp.status +
          (data.detail ? ': ' + JSON.stringify(data.detail).slice(0, 120) : ''),
      )
      err.status = resp.status
      reportError(
        'api_http_error',
        (err.message + ' ' + url).slice(0, 200),
        'HTTP ' + resp.status,
        'warning',
      )
      throw err
    }
    if (data && typeof data === 'object' && 'ok' in data && !data.ok) {
      const err: Error = new Error(data.error || '请求失败')
      if (data.error)
        reportError('business_error', (data.error + ' ' + url).slice(0, 200), 'HTTP 200', 'warning')
      throw err
    }
    return { data: 'data' in data ? data.data : data, status: resp.status }
  } finally {
    clearTimeout(t)
  }
}

// 2026-10-14 间断性 422 根治: iOS Safari multipart body 空(File 引用时效/Blob 序列化)——raw bytes 直传(CT octet-stream)
export async function uploadRawBytes(
  url: string,
  data: ArrayBuffer | Blob,
  timeout = 90000,
): Promise<any> {
  const token = (() => {
    try {
      return localStorage.getItem('c_token')
    } catch {
      return ''
    }
  })()
  const controller = new AbortController()
  const t = setTimeout(() => controller.abort(), timeout)
  try {
    const resp = await fetch(url, {
      method: 'POST',
      body: data,
      headers: { 'X-Auth-Token': token || '', 'Content-Type': 'application/octet-stream' },
      signal: controller.signal,
    })
    const d = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      const err: Error & { status?: number } = new Error(
        'Request failed with status code ' + resp.status,
      )
      err.status = resp.status
      reportError(
        'api_http_error',
        (err.message + ' ' + url).slice(0, 200),
        'HTTP ' + resp.status,
        'warning',
      )
      throw err
    }
    if (d && typeof d === 'object' && 'ok' in d && !d.ok) {
      const err: Error = new Error(d.error || '请求失败')
      if (d.error)
        reportError('business_error', (d.error + ' ' + url).slice(0, 200), 'HTTP 200', 'warning')
      throw err
    }
    return { data: 'data' in d ? d.data : d, status: resp.status }
  } finally {
    clearTimeout(t)
  }
}

export const api = {
  get: apiGet,
  post: async (url, data?, config = {}) => {
    const merged = { timeout: 30000, ...config }
    const r = await instance.post(url, data, merged)
    invalidateCache()
    return r
  },
  // 批量/重写操作：宽松超时（PA 单 worker 排队时单请求可能 >30s）
  postHeavy: async (url, data?, config = {}) => {
    const merged = { timeout: 90000, ...config }
    const r = await instance.post(url, data, merged)
    invalidateCache()
    return r
  },
  put: async (url, data, config = {}) => {
    const r = await instance.put(url, data, config)
    invalidateCache()
    return r
  },
  delete: async (url, config = {}) => {
    const r = await instance.delete(url, config)
    invalidateCache()
    return r
  },
}

// 清除缓存
export function clearCache(pattern?) {
  if (!pattern) {
    cache.clear()
    return
  }
  for (const key of cache.keys()) {
    if (key.includes(pattern)) cache.delete(key)
  }
}

export function clearInflight() {
  inflight.clear()
}

// 缓存统计
export function getCacheStats() {
  return { size: cache.size, inflight: inflight.size }
}
