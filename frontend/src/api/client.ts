import axios from 'axios'
import type { AxiosError, AxiosRequestConfig } from 'axios'
import { reportError } from './logger'
import type { ApiResult, ApiGetConfig, CacheEntry, InflightEntry } from './types'

/* eslint-disable no-console -- API 调试日志(开发期保留, 便于追接口链路) */
const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

// 响应缓存（内存）
const cache = new Map<string, CacheEntry>()
const CACHE_TTL = 30_000 // 30s 内复用缓存
// 缓存条目上限: 以前 cache 只写不删, 而看板 URL 带 t=<时间戳>(每轮询一次就多一个 key),
// 一天下来上万个条目常驻内存。这里在写入路径上做 LRU 回收(见 pruneCache)。
const CACHE_MAX = 200

/**
 * 破缓存时间戳 —— 时间窗分桶(2026-11-02)
 *
 * 原先各页面用 `t=Date.now()` 拼 URL 绕过缓存, 目的是保证数据实时性(不能让用户看着
 * 30s 前的数字而毫不知情)。但这让每次请求的 URL 都唯一: 缓存永远 miss、在途去重永远
 * 失效、cache Map 无限膨胀 —— 同一屏的三个请求即便在同一毫秒内并发也合并不了。
 *
 * 改成按 15s 分桶: 新鲜度上限仍是 15s(与看板轮询周期同量级, 用户感知无差别),
 * 但同一窗口内的重复请求 URL 相同 → 命中缓存/在途去重, 真正的"刷新"仍会在下一个
 * 窗口发生。窗口大小可传参, 但调用方应保持"≤ 最短轮询周期"这一约束。
 */
export function freshStamp(windowMs = 15_000): number {
  return Math.floor(Date.now() / windowMs)
}

// 写入缓存后的回收: 先清过期, 仍超额则按插入顺序淘汰最旧(Map 保持插入序)
function pruneCache() {
  if (cache.size <= CACHE_MAX) return
  const now = Date.now()
  for (const [k, v] of cache) {
    if (!v || now - v.ts >= CACHE_TTL) cache.delete(k)
  }
  if (cache.size <= CACHE_MAX) return
  let over = cache.size - CACHE_MAX
  for (const k of cache.keys()) {
    if (over-- <= 0) break
    cache.delete(k)
  }
}

function cacheKey(method?: string, url?: string, params?: unknown) {
  return (method ?? '') + ':' + (url ?? '') + ':' + JSON.stringify(params || {})
}

/**
 * 在途请求登记(2026-11-05 根治「多页面卡加载中/同步中」)
 *
 * 为什么必须放在网络层, 而不是继续让每个页面自己 setSyncing(1)/setSyncing(-1):
 *   全仓有 9 个 setSyncing 调用点(Dashboard 3 处、Insights 3 处、Inventory、History、store),
 *   每处都得自己在**所有**退出路径上配对 -1 —— 包括 early return、abort、catch、finally。
 *   实测这些地方确实漏了: InsightsPage 的 `if (seq !== replenSeq.current) return` 在
 *   try 块里, 而 -1 在 finally 里(这条没漏); 但 HistoryPage 的 _settle 挂在 .finally,
 *   一旦请求被 abortScope 取消, axios 抛 CanceledError 进 rejected 分支 → 走 finally 是能减。
 *   真正的漏洞是**没有超时兜底时的等待**: 请求挂 90s, 胶囊就转 90s, 用户看到的就是
 *   "一直在加载中/同步中"。
 *
 * 所以改成: 每个真发出的 HTTP 请求自动登记一次占用, 无论成功/失败/取消都在 finally 注销。
 *   页面的 setSyncing 调用保留(它们表达"这一整轮取数", 比单请求粒度更粗), 但从此即使
 *   某个页面忘了配对, 胶囊也不会永久卡住 —— 计数由网络层保证归零。
 */
let _activeReq = 0
const _reqListeners = new Set<() => void>()
function _notifyReq() {
  _reqListeners.forEach(fn => {
    try {
      fn()
    } catch {}
  })
}
function _reqStart() {
  _activeReq += 1
  _notifyReq()
}
function _reqEnd() {
  _activeReq = Math.max(0, _activeReq - 1)
  _notifyReq()
}
/** 订阅"当前有多少个 HTTP 请求在途"(0 = 没有任何取数) */
export function onActiveRequests(fn: () => void): () => void {
  _reqListeners.add(fn)
  return () => {
    _reqListeners.delete(fn)
  }
}
export function activeRequests(): number {
  return _activeReq
}

// 底层 axios 实例
const instance = axios.create({
  baseURL: BASE,
  timeout: 30000,
})

// 请求拦截器：自动注入全局 channel 参数 + 认证 token
instance.interceptors.request.use(config => {
  // 在途登记放在拦截器里 = 所有 axios 请求自动覆盖, 页面无需逐个配对
  ;(config as unknown as Record<string, unknown>).__tracked = true
  _reqStart()
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
    // 2026-10-13: 网关改写 Authorization(q-sign)——token 走 X-Auth-Token 自定义头
    // headers 由 axios 提供, 这里只在原生索引签名上赋值(不引入 any)
    const headers = (config.headers || {}) as unknown as Record<string, string>
    headers['X-Auth-Token'] = token
    config.headers = headers as unknown as typeof config.headers
  }
  return config
})

// 在途请求去重 Map(防同一 URL 并发重复请求)
// ⚠ 挂起兜底: Makers 函数慢/冷启动时请求可能 30s+ 未完成, 若无限复用挂起 promise,
//   后续同 URL 请求全部卡死(loadCfg 等永不返回 → UI 显示旧值, 重进页面才恢复)
//   → 超过 INFLIGHT_TTL 的在途视为挂起, 删除并重新发起
const inflight = new Map<string, InflightEntry<ApiResult<unknown>>>()
const INFLIGHT_TTL = 15000

// 响应拦截器：写缓存 + 清理在途 + 日志 + 自动解包 {ok,data}
instance.interceptors.response.use(
  response => {
    _reqEnd()
    const { method, url, params } = response.config
    const key = cacheKey(method || 'get', url || '', params)
    if ((method || 'get').toLowerCase() === 'get') {
      cache.set(key, { data: response.data, ts: Date.now() })
      pruneCache()
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
    _reqEnd()
    const err = error as AxiosError
    // 2026-10-27 页面卸载 abort 取消: 取消不是错误——静默, 不上报不置 error
    if (axios.isCancel(err)) {
      return Promise.reject(err)
    }
    const cfg: AxiosRequestConfig = err.config || {}
    const key = cacheKey(cfg.method || 'get', cfg.url || '', cfg.params)
    inflight.delete(key)
    console.debug(`[API] ${(cfg.method || 'get').toUpperCase()} ${cfg.url} → ❌ ${err.message}`)
    // 统一收口: 网络错误/5xx 上报(4xx 业务错误不上报防噪音)
    const status = err.response?.status
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
        ((err.message || '') + ' ' + (cfg.url || '')).slice(0, 200),
        status
          ? 'HTTP ' + status + ' ' + JSON.stringify(err.response?.data || '').slice(0, 400)
          : 'network-error',
        status && status >= 500 ? 'error' : 'warning',
      )
    }
    return Promise.reject(err)
  },
)

// 导出带缓存的 get
// 2026-11-04 any 治理: 泛型默认值 unknown(不再是 any), 调用方用 api.get<T>(...) 显式声明形状,
// 或在边界处 String()/Number() 收敛 —— 符合 docs/DEVELOPMENT.md §15.38「不造假严格」。
const apiGet = async <T = unknown>(
  url: string,
  config: ApiGetConfig = {},
): Promise<ApiResult<T>> => {
  const key = cacheKey('get', url, config.params)

  // 1) 缓存命中
  const hit = cache.get(key)
  if (hit && Date.now() - hit.ts < CACHE_TTL) {
    // 缓存命中也需解包统一格式 {ok,data}（与拦截器一致）
    let _d: unknown = hit.data
    if (_d && typeof _d === 'object' && 'ok' in _d && 'data' in _d) {
      _d = (_d as Record<string, unknown>).data
    }
    return { data: _d as T, status: 200, statusText: 'OK', headers: {}, config }
  }

  // 2) 在途去重(挂起兜底: 超 INFLIGHT_TTL 的旧在途不再复用, 防慢请求卡死后续刷新)
  // 2026-10-27 根治跨卸载复用卡骨架: 快速切走/切回(数秒内)会复用到旧页面实例的挂起 promise
  // (旧接口 30-60s 才超时) → 新页卡骨架等旧 promise。对复用 promise 设等待上限: 剩余 TTL 内未完成
  // → 自动发起新请求替换(旧 promise 作废), 保证"切回最多等~15s 后必成功刷新", 不再无限期挂。
  if (inflight.has(key)) {
    const p = inflight.get(key)
    if (p && p.__ts && Date.now() - p.__ts > INFLIGHT_TTL) {
      inflight.delete(key)
    } else if (p) {
      const remain = INFLIGHT_TTL - (Date.now() - (p.__ts || Date.now()))
      const waiter = Promise.race([
        p,
        new Promise<never>((_, rej) =>
          setTimeout(() => rej(new Error('INFLIGHT_STALE')), Math.max(remain, 500)),
        ),
      ])
      return waiter.catch(() => {
        if (!p.__dead && inflight.get(key) === p) {
          p.__dead = true
          inflight.delete(key)
          // Object.assign 给原生 Promise 挂元数据, 与旧版 `promise.__ts = ...` 同一对象、零行为差异
          const np = Object.assign(
            instance.get(url, config).then(r => ({
              data: r.data as unknown,
              status: r.status,
              statusText: r.statusText,
              headers: r.headers as unknown,
              config: r.config as unknown,
            })),
            {
              __ts: Date.now(),
              __scope: '',
              __ac: undefined as AbortController | undefined,
              __dead: false,
            },
          )
          inflight.set(key, np)
          return np
        }
        return p
      }) as unknown as Promise<ApiResult<T>>
    }
  }

  // 3) 发起新请求
  const ac = new AbortController()
  const promise = Object.assign(
    instance.get(url, { ...config, signal: ac.signal }).then(r => ({
      data: r.data as unknown,
      status: r.status,
      statusText: r.statusText,
      headers: r.headers as unknown,
      config: r.config as unknown,
    })),
    {
      __ts: Date.now(),
      __scope: config.scope || '',
      __ac: ac,
      __dead: false,
    },
  )
  inflight.set(key, promise)
  return promise as unknown as Promise<ApiResult<T>>
}

// 2026-10-27 页面卸载取消在途(根治快速切换卡骨架): 切走页面调用 abortScope(scope)
// → 取消该页面全部在途请求(axios Abort, 不入上报/不报错) + 移出 inflight(切回不再复用旧 promise),
// 切回即全新请求; RU 不白跑(旧请求被真正取消)
export function abortScope(scope: string) {
  if (!scope) return
  for (const [k, p] of inflight) {
    if (p && p.__scope === scope) {
      try {
        if (p.__ac && typeof p.__ac.abort === 'function') p.__ac.abort()
      } catch {}
      inflight.delete(k)
    }
  }
}

// 非 GET 请求后清空缓存（数据变了，缓存失效）
function invalidateCache() {
  cache.clear()
}

// ── S3-2/S3-3 收口: 裸 fetch 统一通道 ──
// 适用: 轮询/状态探测/文件下载/登录等**不吃缓存、不注入 channel**的场景。
// 语义与裸 fetch 等价: 返回原始 body(await r.json()) 或 raw:true 时返回 Response,
// 不解包 {ok,data}、不读写缓存、不进在途去重、不自动注入 channel(URL 由调用方拼全)。
// 只补齐裸 fetch 缺的三件事: ①超时(默认 30s, 原生 fetch 无超时曾挂起 30s+)
// ②X-Auth-Token 注入(省掉各调用点复制的 IIFE) ③与 axios 同口径拼 BASE(相对路径不再打到前端 origin)。
// 网络层异常(含超时 AbortError)照常 throw → 调用方 catch 原逻辑不变, 且上报 api_http_error。
// logger.reportError 仍用裸 fetch(client→logger 单向依赖, 收口会成环)。
export async function apiJson<T = unknown>(
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  url: string,
  opts: { data?: unknown; timeout?: number; signal?: AbortSignal; raw?: boolean } = {},
): Promise<T> {
  const token = (() => {
    try {
      return localStorage.getItem('c_token')
    } catch {
      return ''
    }
  })()
  const target = /^https?:\/\//i.test(url) ? url : BASE + url
  const ctrl = new AbortController()
  const _tid = setTimeout(() => ctrl.abort(), opts.timeout ?? 30000)
  const onAbort = () => ctrl.abort()
  if (opts.signal) {
    if (opts.signal.aborted) ctrl.abort()
    else opts.signal.addEventListener('abort', onAbort)
  }
  const hasBody = opts.data !== undefined
  _reqStart()
  try {
    const resp = await fetch(target, {
      method,
      body: hasBody ? JSON.stringify(opts.data) : undefined,
      headers: {
        ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { 'X-Auth-Token': token } : {}),
      },
      signal: ctrl.signal,
    })
    return (opts.raw ? resp : await resp.json()) as T
  } catch (e: unknown) {
    try {
      const msg = e && typeof e === 'object' && 'message' in e ? String(e.message) : ''
      reportError('api_http_error', (msg + ' ' + url).slice(0, 200), 'network-error', 'warning')
    } catch {}
    throw e
  } finally {
    _reqEnd()
    clearTimeout(_tid)
    if (opts.signal) opts.signal.removeEventListener('abort', onAbort)
  }
}

// 导出 api 对象，保持与原接口兼容
// 2026-10-14 422 根治: iOS Safari 移动端 axios XHR FormData 发送 body 空(boundary 有内容无)——上传类改 fetch 原生(Safari fetch multipart 可靠)
export async function uploadForm<T = unknown>(
  url: string,
  fd: FormData,
  timeout = 90000,
): Promise<{ data: T; status: number }> {
  const token = (() => {
    try {
      return localStorage.getItem('c_token')
    } catch {
      return ''
    }
  })()
  const controller = new AbortController()
  const _tid = setTimeout(() => controller.abort(), timeout)
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
    return { data: ('data' in data ? data.data : data) as T, status: resp.status }
  } finally {
    clearTimeout(_tid)
  }
}

// 2026-10-14 间断性 422 根治: iOS Safari multipart body 空(File 引用时效/Blob 序列化)——raw bytes 直传(CT octet-stream)
export async function uploadRawBytes<T = unknown>(
  url: string,
  data: ArrayBuffer | Blob,
  timeout = 90000,
): Promise<{ data: T; status: number }> {
  const token = (() => {
    try {
      return localStorage.getItem('c_token')
    } catch {
      return ''
    }
  })()
  const controller = new AbortController()
  const _tid = setTimeout(() => controller.abort(), timeout)
  // 2026-11-03 修: 原样 fetch(url) 只对"前端与 API 同源"成立。只要设了
  // VITE_API_BASE_URL(本地 dev 连测试环境/前后端分离部署), 上传就会打到前端自己的
  // origin → 404, 且被上层 catch 吞掉只留"识别失败"。与 axios 实例同口径拼 BASE。
  const target = /^https?:\/\//i.test(url) ? url : BASE + url
  try {
    const resp = await fetch(target, {
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
    return { data: ('data' in d ? d.data : d) as T, status: resp.status }
  } finally {
    clearTimeout(_tid)
  }
}

export const api = {
  get: apiGet,
  post: async (url: string, data?: unknown, config: AxiosRequestConfig = {}) => {
    const merged = { timeout: 30000, ...config }
    const r = await instance.post(url, data, merged)
    invalidateCache()
    return r
  },
  // 批量/重写操作：宽松超时（PA 单 worker 排队时单请求可能 >30s）
  postHeavy: async (url: string, data?: unknown, config: AxiosRequestConfig = {}) => {
    const merged = { timeout: 90000, ...config }
    const r = await instance.post(url, data, merged)
    invalidateCache()
    return r
  },
  put: async (url: string, data?: unknown, config: AxiosRequestConfig = {}) => {
    const r = await instance.put(url, data, config)
    invalidateCache()
    return r
  },
  delete: async (url: string, config: AxiosRequestConfig = {}) => {
    const r = await instance.delete(url, config)
    invalidateCache()
    return r
  },
}

// 清除缓存
export function clearCache(pattern?: string) {
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
