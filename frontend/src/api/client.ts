import axios from 'axios'

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
  const token = (() => { try { return localStorage.getItem('c_token') } catch { return null } })()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = 'Bearer ' + token
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
  (response) => {
    const { method, url, params } = response.config
    const key = cacheKey(method || 'get', url || '', params)
    if ((method || 'get').toLowerCase() === 'get') {
      cache.set(key, { data: response.data, ts: Date.now() })
    }
    inflight.delete(key)
    console.debug(`[API] ${(method||'get').toUpperCase()} ${url} → ${response.status}`, params && Object.keys(params).length ? params : '')

    // 自动解包统一响应格式 {ok, data, error}
    if (response.data && typeof response.data === 'object' && 'ok' in response.data) {
      if (!response.data.ok) {
        const err = new Error(response.data.error || '请求失败')
        err.status = response.status
        return Promise.reject(err)
      }
      // 有 data 字段才解包，否则保留原始响应（如清洗接口的 {ok,success,failed}）
      if ('data' in response.data) {
        response.data = response.data.data
      }
    }

    return response
  },
  (error) => {
    const cfg = (error.config || {}) as any
    const key = cacheKey(cfg.method || 'get', cfg.url || '', cfg.params)
    inflight.delete(key)
    console.debug(`[API] ${(cfg.method||'get').toUpperCase()} ${cfg.url} → ❌ ${error.message}`)
    return Promise.reject(error)
  }
)

// 导出带缓存的 get
const apiGet = async <T = any>(url: string, config: Record<string, any> = {}): Promise<{ data: T; status: number; statusText: string; headers: any; config: any }> => {
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
  if (inflight.has(key)) {
    const p: any = inflight.get(key)
    if (p && p.__ts && Date.now() - p.__ts > INFLIGHT_TTL) {
      inflight.delete(key)
    } else {
      return p
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
export const api = {
  get: apiGet,
  post: async (url, data, config) => {
    const merged = {timeout: 30000, ...config}
    const r = await instance.post(url, data, merged)
    invalidateCache()
    return r
  },
  // 批量/重写操作：宽松超时（PA 单 worker 排队时单请求可能 >30s）
  postHeavy: async (url, data, config) => {
    const merged = {timeout: 90000, ...config}
    const r = await instance.post(url, data, merged)
    invalidateCache()
    return r
  },
  put: async (url, data, config) => {
    const r = await instance.put(url, data, config)
    invalidateCache()
    return r
  },
  delete: async (url, config) => {
    const r = await instance.delete(url, config)
    invalidateCache()
    return r
  },
}

// 清除缓存
export function clearCache(pattern) {
  if (!pattern) { cache.clear(); return }
  for (const key of cache.keys()) {
    if (key.includes(pattern)) cache.delete(key)
  }
}

export function clearInflight() { inflight.clear() }

// 缓存统计
export function getCacheStats() {
  return { size: cache.size, inflight: inflight.size }
}
