import axios from 'axios'
import type { AxiosError, AxiosRequestConfig } from 'axios'
import { t } from '../locale'
import { notify } from './notify'
import { reportError } from './logger'
import type { ApiResult, ApiGetConfig, CacheEntry, InflightEntry } from './types'

/* eslint-disable no-console -- API 调试日志(开发期保留, 便于追接口链路) */
const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

/**
 * 预期内的鉴权结果(2026-11-05 非真异常收口): 401 未登录 / 403 访客只读。
 * 二者不是异常, 不该进 quality_logs:
 *   · 401 有专门的认证态链路 —— 拦截器派发 auth:may-expired, App 调 /auth/check
 *     确认后才 setLoggedIn(false), 冷实例偶发 401 不会误踢(见下方 401 分支);
 *   · 403 是 demo 访客「仅可查看」的既定行为, 每次点写操作都会返回。
 * 匿名访问部署预览域名(EdgeOne 构建期自动抓取)必然 401 —— 旧实现把它和 5xx、
 * 网络错误一起上报成 warning, 每次部署都刷噪音, 把真错误淹掉。
 * 5xx / 网络错误 / 400·422 校验错误照常上报, 真异常留痕不削弱。
 */
const isExpectedAuth = (status?: number) => status === 401 || status === 403

/**
 * §14（2026-11-09）网关白页根治：**有界**退避重试
 *
 * 背景（线上实测 S4：4 读 4 写，静默 43 分钟后开跑）：
 *   实例被 Makers 冻结 → 冷启动窗口 + 8 并发涌入 → 边缘保护性 503，响应是
 *   Tencent HTML 白页、无 `X-Srv-Ms`（请求根本没进应用）。用户端表现就是**白屏**。
 *
 * 这不该由用户承担：503/502/504 是**瞬时、可重试**的（冷启动窗口通常 <1s），
 * 所以只对 **GET（幂等）** 做 ≤2 次指数退避，总预算 ≤2s。
 * 红线（老债，别踩）：
 *   · 超时（ECONNABORTED）**不重试** —— 那是 timeout→retry→更多 miss 的
 *     metastable failure 放大器（§15.161 已否决；见 SWR 那段注释）；
 *   · 写请求（POST/PUT/DELETE）不重试 —— 非幂等，重复提交比失败更糟；
 *   · 401/403/主动取消不重试（各自有专门链路）；
 *   · `__swr` 有界探测不重试（它本身就在预算里，重试会双层放大）。
 * 重试期间走 `__bg`：不点亮用户可见的忙徽标，避免"转圈闪两下"。
 */
const GW_RETRY_MAX = 2
const GW_RETRY_BUDGET = 2000 // ms，含所有重试的等待与请求本身（503 是快返回，实测 <100ms/次）

function isGatewayRetryable(err: AxiosError): boolean {
  const st = err.response?.status
  if (st === 502 || st === 503 || st === 504) return true
  // 无响应 + 非取消 + 非超时 = 连接被边缘直接掐断（白页/连接重置/冷启动拒绝）
  if (!st && !axios.isCancel(err) && (err as { code?: string }).code !== 'ECONNABORTED') return true
  return false
}

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

/**
 * 请求参数的稳定序列化(2026-11-06 审计 P0-7)
 *
 * 原实现直接 `JSON.stringify(params)`, 键顺序一变 key 就变: 调用方写 `{t:1}` 时
 * 注入 channel 后是 `{t:1,channel:'jd'}`, 而自己写 `{channel:'jd',t:1}` 的调用方是
 * 另一种顺序 —— 同一份数据两个 key, 缓存命中率凭空归零。按 key 排序后消除这个差异。
 */
function _stable(v: unknown): string {
  if (v === null || v === undefined) return 'null'
  if (typeof v !== 'object') return JSON.stringify(v) ?? 'null'
  if (Array.isArray(v)) return '[' + v.map(_stable).join(',') + ']'
  const o = v as Record<string, unknown>
  return (
    '{' +
    Object.keys(o)
      .sort()
      .map(k => JSON.stringify(k) + ':' + _stable(o[k]))
      .join(',') +
    '}'
  )
}

function cacheKey(method?: string, url?: string, params?: unknown) {
  return (method ?? '').toLowerCase() + ':' + (url ?? '') + ':' + _stable(params)
}

/**
 * 全局渠道参数注入(2026-11-06 审计 P0-7 的根)
 *
 * 缓存键必须**在注入 channel 之后**才算, 否则两侧不同源:
 *   写侧(响应拦截器)拿的是 axios 的 config.params —— 已被拦截器塞了 channel;
 *   读侧(apiGet)拿的是调用方原始 config.params —— 没有 channel。
 * 结果 `get:/api/x:{"channel":"jd","t":9}` 与 `get:/api/x:{"t":9}` 两个 key,
 * **30s 缓存永远不命中**(实测: 切页/返回都要重新请求)。
 * 现在两侧都走同一个函数, 键天然一致。
 */
function _channelOf(): string {
  try {
    return localStorage.getItem('c_channel') || 'jd'
  } catch {
    return 'jd'
  }
}
function _withChannel(params?: unknown): Record<string, unknown> {
  const p =
    params && typeof params === 'object' && !Array.isArray(params)
      ? { ...(params as Record<string, unknown>) }
      : {}
  if (!p.channel) p.channel = _channelOf()
  return p
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
/**
 * 「后台请求」计数(2026-11-05 根治「任务在跑时胶囊一直转」)
 *
 * 上面那条 45s 超时的教训只解决了"请求叠加", 还差一层: **后台看守的续跑请求不该算用户可见的忙**。
 *   taskWatch 每 3s 打一次驱动端点, 单次预算 75s —— 也就是说一个长任务在跑时, 网络层
 *   几乎**永远有一个请求在途**, `activeRequests()` 恒 > 0, 胶囊就从"任务在跑"被误读成"页面在拉数",
 *   用户看到的是"什么页面都在加载中/同步中"(问题 4 的另一半)。
 * 所以把在途分成两档: 前台(页面取数) 与 后台(看守/轮询)。徽标只认前台;
 *   后台请求仍照常登记/注销, 只是不进徽标分子 —— 将来要做"任务在跑"的独立指示也有数据可用。
 *
 * 2026-11-06 审计 P1-32: 原来是模块级布尔 `_reqBg`(markBackground 设、_reqEnd 复位),
 *   并发时就串了 —— A 请求标了后台, B 紧跟着进来也读到 true, 于是**前台请求被当成后台**
 *   (徽标不亮, 用户看着页面不动); 反过来 A 复位后 B 的 _reqEnd 又少减一次, 计数漏减。
 *   改为按请求传参: axios 走 config.__bg, apiJson 走局部变量, 不再有共享可变状态。
 */
let _bgReq = 0
/**
 * 当前视图(页面)标识 + 按视图的前台在途计数(2026-11-07)
 *
 * 用户实测: 从一个页切到另一个页, header 胶囊仍然"加载中/同步中" —— 因为旧页面的请求
 * 还在跑, 而徽标只认"全站在途数"。切页时旧页的骨架已经卸载, 用户看到新页 + 转圈,
 * 会以为新页卡住。这里把前台在途按**发起请求时所在的页面**分桶:
 *   · 徽标只显示当前页的在途 → 切到新页即刻归零(旧页请求仍在后台跑完/被 abort);
 *   · 后台任务(看守/轮询)仍单独计数, 由 App 决定是否全局显示"n 条任务运行中"。
 */
let _curView = ''
const _fgByView = new Map<string, number>()
/** 当前视图(页面 id)。store/App 用它决定"这一页到底需要哪些域的数据"。 */
export function currentView(): string {
  return _curView
}
export function setView(v: string) {
  _curView = String(v || '')
  _notifyReq()
}
const _reqListeners = new Set<() => void>()
function _notifyReq() {
  _reqListeners.forEach(fn => {
    try {
      fn()
    } catch {}
  })
}
/** 标记一个请求为"后台"(不点亮用户可见的忙徽标)。
 *  @deprecated 2026-11-06 审计 P1-32: 全局开关会让并发请求互相串味(见上)。
 *  已在全仓核实**无调用方**, 仅保留空实现一版防止外部脚本引用报错; 新代码请用请求级参数
 *  (`apiJson` 的 `background: true` / axios 的 `config.__bg`), 下个大版本删除。 */
export function markBackground(_on = true) {
  /* no-op: 全局后台标记已废弃, 见 client.ts 顶部 P1-32 注释 */
}
function _reqStart(isBg = false, view = '') {
  _activeReq += 1
  if (isBg) {
    _bgReq += 1
  } else {
    const k = view || _curView
    _fgByView.set(k, (_fgByView.get(k) || 0) + 1)
  }
  _notifyReq()
}
function _reqEnd(isBg = false, view = '') {
  if (isBg) {
    _bgReq = Math.max(0, _bgReq - 1)
  } else {
    const k = view || _curView
    const n = (_fgByView.get(k) || 0) - 1
    if (n > 0) _fgByView.set(k, n)
    else _fgByView.delete(k)
  }
  _activeReq = Math.max(0, _activeReq - 1)
  _notifyReq()
}
/**
 * 订阅"当前视图有多少个**前台** HTTP 请求在途"(0 = 当前页取数已全部结束)
 *   后台请求(看守/轮询)不计入; 其它页的请求也不计入 —— 见 _fgByView。
 */
export function onActiveRequests(fn: () => void): () => void {
  _reqListeners.add(fn)
  return () => {
    _reqListeners.delete(fn)
  }
}
export function activeRequests(): number {
  return Math.max(0, _fgByView.get(_curView) || 0)
}
/** 后台在途数(诊断用: 胶囊不显示, 但可在 devtools 里看到看守是否真的在跑) */
export function backgroundRequests(): number {
  return _bgReq
}

// 底层 axios 实例
const instance = axios.create({
  baseURL: BASE,
  // 2026-11-05: 30s 太短 —— seed 期间线上实测 `/insights/replenishment` 40.8s、
  //   `/dashboard/stock-risk` 38.3s, 30s 必然 abort → 页面拿到失败态; 而下面的在途
  //   「挂起兜底」又会自动补发, 于是同一个 URL 每 15s 叠一个新请求、旧的还挂着,
  //   胶囊(真实在途计数)永远归不了零 —— 这就是用户说的「多页面卡加载中/同步中」。
  //   统一放宽到 45s(覆盖实测最慢 + 余量), 且挂起阈值同步 ≥ 超时(见下)。
  timeout: 45000,
})

/**
 * §15.161 P2 —— 冷态 SWR(Stale-While-Revalidate) 读模型状态
 *
 * 后端冷态(fp 不等)不再让用户等 15~30s 重建: 立即返回**上一版完整物化**,
 * 并在响应**顶层**带 __swr 新鲜度状态(data 形状一字不变)。前端要做三件事:
 *   ① 不把这份旧快照写进 30s 缓存 —— 否则用户会盯着 30 秒前的数字浑然不知;
 *   ② 实时播报「当前是 X 秒前的完整快照, 正在更新」, 刷新完成后收口;
 *   ③ 有界探测刷新: 最多 2 次、总预算 ≤3s、带 jitter。绝不 16×1.8s 盲轮询 ——
 *      那是 timeout→retry→更多 miss 的 metastable failure 放大器(已否决)。
 * 视图更新由既有 15s 分桶轮询(freshStamp)完成; 探测命中会把新数据写回缓存,
 * 下一次取数即为新数据, 页面层零改动。
 */
const SWR_TOTAL_BUDGET = 3000
const SWR_MAX_RETRY = 2
let _swrActive = false
let _swrDeadline = 0

interface SwrMeta {
  state?: string
  freshness_s?: number
  built_at?: string
  fp_changed?: boolean
  rebuild?: string
  retry_after_ms?: number
}

function _swrOf(raw: unknown): SwrMeta | null {
  if (!raw || typeof raw !== 'object') return null
  const m = (raw as Record<string, unknown>).__swr
  return m && typeof m === 'object' ? (m as SwrMeta) : null
}

function _swrAnnounce(m: SwrMeta) {
  const age = Number(m.freshness_s ?? -1)
  notify.info(t('ins.data_refreshing', { s: age >= 0 ? age : 0 }))
  _swrActive = true
  _swrDeadline = Date.now() + SWR_TOTAL_BUDGET
  try {
    window.dispatchEvent(new CustomEvent('insights-rebuilding', { detail: m }))
  } catch {
    // 非浏览器环境(单测)忽略; 播报主通道 notify 不受影响
  }
}

function _swrFinish() {
  if (!_swrActive) return
  _swrActive = false
  notify.success(t('ins.data_updated'))
  try {
    window.dispatchEvent(new Event('insights-refreshed'))
  } catch {
    // 同上
  }
}

/** 有界探测: 变参避开 30s 缓存与在途去重; __swrProbe 防止拦截器递归调度。 */
function _swrProbe(cfg: AxiosRequestConfig, key: string, attempt: number) {
  if (attempt >= SWR_MAX_RETRY || Date.now() >= _swrDeadline) return
  const delay = 500 + attempt * 400 + Math.floor(Math.random() * 300)
  if (Date.now() + delay >= _swrDeadline) return
  setTimeout(() => {
    const params = {
      ...((cfg.params as Record<string, unknown>) || {}),
      __swr: String(attempt) + '_' + String(Date.now()),
    }
    const probeCfg = {
      params,
      __swrProbe: true,
      __bg: true, // 后台探测, 不点亮用户可见的忙徽标
      timeout: 10000,
    } as unknown as AxiosRequestConfig
    instance
      .get(String(cfg.url || ''), probeCfg)
      .then(r => {
        const got = (r.config as unknown as Record<string, unknown>).__swrResult as
          SwrMeta | undefined
        if (!got) {
          // 已切到新 gen: 写回原 key(解包后的新数据), 供下一次取数/轮询命中
          cache.set(key, { data: r.data, ts: Date.now() })
          _swrFinish()
        } else {
          _swrProbe(cfg, key, attempt + 1)
        }
      })
      .catch(() => {
        _swrProbe(cfg, key, attempt + 1)
      })
  }, delay)
}

// 请求拦截器：自动注入全局 channel 参数 + 认证 token
instance.interceptors.request.use(config => {
  const c = config as unknown as Record<string, unknown>
  // 在途登记放在拦截器里 = 所有 axios 请求自动覆盖, 页面无需逐个配对
  c.__tracked = true
  const isBg = Boolean(c.__bg)
  // 2026-11-07: 记录发起时所在视图 —— 切页后旧页请求不再点亮新页胶囊(见 _fgByView)
  c.__view = _curView
  _reqStart(isBg, _curView)
  // channel 注入与缓存键顺序(2026-11-06 P0-7): 先注入再算键, 并把键挂到 config 上。
  //   读侧(apiGet)用同一个 _withChannel 算键 → 两侧必然一致。
  config.params = _withChannel(config.params)
  c.__cacheKey = cacheKey(config.method || 'get', config.url || '', config.params)
  c.__epoch = _writeEpoch
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
  // 2026-11-07 P2: 响应压缩要靠显式头 —— 边缘网关会把 `Accept-Encoding` 整个剥掉再转发,
  //   后端永远看不到浏览器本来的 gzip 请求。约定 `X-Accept-Encoding: gzip`(X- 自定义头可
  //   透传), 后端据此决定是否压缩(80~114KB 的列表页压到 8~12KB, 实测这环境出网带宽只有
  //   约 130KB/s, 体积就是延迟)。浏览器 fetch/XHR 自动解压, 调用方零感知。
  const headers2 = (config.headers || {}) as unknown as Record<string, string>
  headers2['X-Accept-Encoding'] = headers2['X-Accept-Encoding'] || 'gzip'
  config.headers = headers2 as unknown as typeof config.headers
  return config
})

// 在途请求去重 Map(防同一 URL 并发重复请求)
// ⚠ 挂起兜底: Makers 函数慢/冷启动时请求可能 30s+ 未完成, 若无限复用挂起 promise,
//   后续同 URL 请求全部卡死(loadCfg 等永不返回 → UI 显示旧值, 重进页面才恢复)
//   → 超过 INFLIGHT_TTL 的在途视为挂起, 删除并重新发起
const inflight = new Map<string, InflightEntry<ApiResult<unknown>>>()
// 2026-11-06 审计 P0-7: 在途表加硬上限。原来只靠响应/错误/abortScope 三路 delete,
//   任何一条路径漏了(拦截器抛错、页面卸载不走 abortScope)条目就永久驻留, 且**永远复用
//   那个 promise** —— 同一 URL 从此再也发不出新请求。上限 + 插入序淘汰, 最坏退化为多一次请求。
const INFLIGHT_MAX = 120
function _setInflight(k: string, p: InflightEntry<ApiResult<unknown>>) {
  if (!inflight.has(k) && inflight.size >= INFLIGHT_MAX) {
    const oldest = inflight.keys().next()
    if (!oldest.done) inflight.delete(oldest.value)
  }
  inflight.set(k, p)
}

/**
 * 写操作代次(2026-11-06 审计 P0-7)
 *
 * 只清 cache 不够: 写之前发出的 GET 还在路上, 写完之后它才回来, 响应拦截器会把它
 * **无条件写进刚清空的缓存** —— 用户点完保存看到的还是旧数字, 而且这个旧值要挂 30s。
 * 所以每次写操作把代次 +1, GET 发出时记下当时的代次, 回来时不一致就**丢弃这次缓存写入**
 * (请求结果照常返回给调用方, 只是不进缓存)。
 */
let _writeEpoch = 0
// 2026-11-05: 必须 ≥ axios timeout。原来 15s 会在一个正常但偏慢的请求(40s)还没回来时
//   就判定"挂起"并**自动补发**新请求 → 请求风暴 + 胶囊永久转圈。现在在途请求在自己的
//   超时内一律复用, 超时后由 axios 明确 reject(页面显示可重试的错误态), 不再偷偷补发。
const INFLIGHT_TTL = 50000

// 响应拦截器：写缓存 + 清理在途 + 日志 + 自动解包 {ok,data}
instance.interceptors.response.use(
  response => {
    const cfg = response.config as unknown as Record<string, unknown>
    _reqEnd(Boolean(cfg.__bg), String(cfg.__view || ''))
    const { method, url, params } = response.config
    // 2026-11-06 P0-7: 键由请求拦截器算好(已含注入的 channel), 这里直接用 ——
    //   原来在这里重算, 拿到的 params 已带 channel, 与 apiGet 读侧不含 channel 的键不同源。
    const key = String(cfg.__cacheKey || cacheKey(method || 'get', url || '', params))
    // §15.161: __swr 在响应顶层(不在 data 里), 解包前先摘; 旧快照绝不进 30s 缓存。
    const swr = _swrOf(response.data)
    const isProbe = Boolean(cfg.__swrProbe)
    // 2026-11-09 统一降级态: 顶层 __degraded(与 __swr 同款) —— DB 不可用时后端
    //   仍返回 200 + 全 0 空壳, 不标记的话用户会误判成「没数据」。这里一处收口:
    //   不写 30s 缓存(空壳不能驻留), 并派发全局事件让 App 显示常驻横幅。
    const degHeader =
      response.headers?.['X-App-Degraded'] === '1' || response.headers?.['x-app-degraded'] === '1'
    const deg = degHeader || (response.data as Record<string, unknown> | undefined)?.__degraded
    if (deg) {
      cache.delete(key)
      try {
        window.dispatchEvent(new CustomEvent('app:degraded', { detail: deg }))
      } catch {}
    } else if (!isProbe) {
      try {
        window.dispatchEvent(new Event('app:degraded-clear'))
      } catch {}
    }
    if (swr) {
      cfg.__swrResult = swr
      cache.delete(key)
      if (!isProbe) {
        _swrAnnounce(swr)
        _swrProbe(response.config, key, 0)
      }
    } else {
      // §15.163: 收口判定从 insights 扩到看板 —— 看板 SWR 的普通轮询拿到新数据时也结束播报
      //   (否则只靠 ≤3s 有界探测, 后台重建超过预算时播报要等下次访问 insights 才收)。
      const isSwrPath =
        String(url || '').includes('/insights/') || String(url || '').includes('/dashboard/')
      if (_swrActive && !isProbe && isSwrPath) _swrFinish()
      if ((method || 'get').toLowerCase() === 'get' && cfg.__epoch === _writeEpoch) {
        // 代次一致才写缓存: 写操作之后回来的旧响应不许回灌(见 _writeEpoch 注释)
        cache.set(key, { data: response.data, ts: Date.now() })
        pruneCache()
      }
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
    const cfg0 = (error as AxiosError).config as unknown as Record<string, unknown> | undefined
    const isProbe = Boolean(cfg0 && cfg0.__swrProbe)
    _reqEnd(Boolean(cfg0 && cfg0.__bg), String((cfg0 && cfg0.__view) || ''))
    const err = error as AxiosError
    // 2026-10-27 页面卸载 abort 取消: 取消不是错误——静默, 不上报不置 error
    if (axios.isCancel(err)) {
      return Promise.reject(err)
    }
    const cfg: AxiosRequestConfig = err.config || {}
    const key = String(
      (cfg as unknown as Record<string, unknown>).__cacheKey ||
        cacheKey(cfg.method || 'get', cfg.url || '', cfg.params),
    )
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
    // §14: 网关/冷启动瞬时 503 → 有界退避重试（GET 幂等；超时/写/探测/取消都不碰）
    const _gwCfg = cfg as unknown as Record<string, unknown>
    const _gwN = Number(_gwCfg.__gwRetry || 0)
    const _gwT0 = Number(_gwCfg.__gwT0 || 0) || Date.now()
    if (
      !isProbe &&
      isGatewayRetryable(err) &&
      String(cfg.method || 'get').toLowerCase() === 'get' &&
      _gwN < GW_RETRY_MAX &&
      Date.now() - _gwT0 < GW_RETRY_BUDGET
    ) {
      // 300ms / 600ms + jitter：覆盖"实例刚拉起"的窗口，又不把用户按在首屏
      const _delay = 300 * Math.pow(2, _gwN) + Math.floor(Math.random() * 200)
      const _nextCfg = {
        ...cfg,
        __gwRetry: _gwN + 1,
        __gwT0: _gwT0,
        __bg: true, // 后台重试：不点亮/熄灭用户的忙徽标
      } as unknown as AxiosRequestConfig
      console.debug(
        `[API] ${String(cfg.method || 'get').toUpperCase()} ${cfg.url} → gateway ${status || 'network'}, retry in ${_delay}ms (${_gwN + 1}/${GW_RETRY_MAX})`,
      )
      return new Promise<void>(resolve => setTimeout(resolve, _delay)).then(() =>
        instance.request(_nextCfg),
      )
    }
    // 收口: 网络错误 + 5xx(error) + 4xx 校验/业务错误(warning)——422/400 不再靠猜。
    // 2026-11-05: 401/403 是预期鉴权态(见 isExpectedAuth), 有专门的 auth:may-expired
    // → /auth/check 链路处理, 不再重复上报一条 warning(匿名访问部署预览域名必然 401)。
    if ((!status || status >= 400) && !isExpectedAuth(status) && !isProbe) {
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
  // 2026-11-06 P0-7: 与请求拦截器同源 —— 都用 _withChannel 补 channel 后再算键
  const key = cacheKey('get', url, _withChannel(config.params))

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

  // 2) 在途去重: 同一 URL 只发一次, 正常在途一律复用
  // 2026-10-27 快速切走/切回会复用到旧页面实例的挂起 promise → 那时靠 abortScope(见下)
  //   在卸载时把该 scope 的请求从 inflight 移除并 abort, 切回即全新请求, 不会卡骨架。
  // 2026-11-05 撤掉「等待上限到了就自动补发」: 它曾让一个 40s 的慢接口每 15s 叠一个新请求,
  //   旧请求仍挂着 → 在途计数永不归零(胶囊一直转)。现在只复用, 超时由 axios 明确失败。
  if (inflight.has(key)) {
    const p = inflight.get(key)
    if (p && p.__ts && Date.now() - p.__ts > INFLIGHT_TTL) {
      // 超过自身超时仍未结束 = 真挂死: 作废, 下面重新发一次(只一次)
      inflight.delete(key)
    } else if (p) {
      // 正常在途: 直接复用, 不再 race + 自动补发。
      //   旧实现每 15s 判定"挂起"就补发一个新请求, 旧请求还挂着 → 同一 URL 请求越叠越多,
      //   网络层在途计数永远归不了零(胶囊一直转), 页面也一直停在"加载中"。
      //   axios 45s timeout 保证 p 一定会 reject/resolve, 不需要前端再猜一个更短的 TTL。
      return p as unknown as Promise<ApiResult<T>>
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
  _setInflight(key, promise as unknown as InflightEntry<ApiResult<unknown>>)
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

/**
 * 取消**所有**在途请求(2026-11-07)
 *
 * 原来切页调 `clearInflight()` —— 它只清 Map, **一个请求都没取消**。旧页面的
 * 4~6 个请求继续跑完(每个 1~8s), 占着 Makers 实例的 DB 连接与执行槽, 新页面的请求
 * 排在它们后面, 于是"切页后每页都要等好几秒"。用户说的"全面退化"有一半是这个:
 * 切页不是取消, 而是又叠了一层。
 * 现在: 切页真正 abort 旧请求(浏览器断开 → 服务端连接提前释放), 再发新页的。
 */
export function abortAll() {
  for (const [k, p] of inflight) {
    try {
      if (p && p.__ac && typeof p.__ac.abort === 'function') p.__ac.abort()
    } catch {}
    inflight.delete(k)
  }
}

/** 是否为"主动取消"(页面切换/最新优先), 页面 catch 应静默忽略, 不当成加载失败 */
export function isCanceled(e: unknown): boolean {
  if (!e || typeof e !== 'object') return false
  const o = e as { code?: string; name?: string }
  return o.code === 'ERR_CANCELED' || o.name === 'CanceledError' || o.name === 'AbortError'
}

// 非 GET 请求后清空缓存（数据变了，缓存失效）
function invalidateCache() {
  cache.clear()
  // 2026-11-06 P0-7: 代次 +1 → 写之前发出的 GET 回来后不再写缓存(否则旧值回灌 30s);
  //   同时清在途表, 避免后续请求复用"写之前的那个 promise"(它带的是旧数据)。
  _writeEpoch += 1
  inflight.clear()
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
  opts: {
    data?: unknown
    timeout?: number
    signal?: AbortSignal
    raw?: boolean
    /** true = 后台请求(看守/轮询): 照常超时与鉴权, 但不点亮用户可见的忙徽标 */
    background?: boolean
  } = {},
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
  // 2026-11-06 P1-32: 按请求传参, 不再用全局 markBackground —— 并发时全局标记会串味
  const _bg = Boolean(opts.background)
  const _v = _curView
  _reqStart(_bg, _v)
  try {
    const resp = await fetch(target, {
      method,
      body: hasBody ? JSON.stringify(opts.data) : undefined,
      headers: {
        ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
        ...(token ? { 'X-Auth-Token': token } : {}),
        // 与 axios 通道同口径: 网关剥 Accept-Encoding, 显式头告诉后端"可以压缩"
        'X-Accept-Encoding': 'gzip',
      },
      signal: ctrl.signal,
    })
    // 2026-11-06 P0-7: 写操作也要失效缓存 —— 本通道不吃缓存, 但它改的数据被 axios 缓存着
    if (method !== 'GET') invalidateCache()
    const isDeg = resp.headers.get('X-App-Degraded') === '1'
    const parsed = (opts.raw ? resp : await resp.json()) as T
    // 裸 fetch 通道(登录 / health / 轮询)也要识别降级标记, 否则登录页只会看到
    //   @traced 回传的 "OperationalError" 这类异常类型名, 用户完全不知道是服务降级。
    //   后端只给响应头(BaseHTTPMiddleware body 不可写), 这里把同样的 __degraded 注入
    //   解析后的对象, LoginPage 与 axios 通道共用一套判定。
    if (!opts.raw && parsed && typeof parsed === 'object') {
      const body = parsed as Record<string, unknown>
      if (isDeg && !body.__degraded) body.__degraded = { state: 'db_unavailable' }
      try {
        if (body.__degraded) {
          window.dispatchEvent(new CustomEvent('app:degraded', { detail: body.__degraded }))
        } else if (method === 'GET') {
          window.dispatchEvent(new Event('app:degraded-clear'))
        }
      } catch {}
    }
    return parsed
  } catch (e: unknown) {
    try {
      const msg = e && typeof e === 'object' && 'message' in e ? String(e.message) : ''
      reportError('api_http_error', (msg + ' ' + url).slice(0, 200), 'network-error', 'warning')
    } catch {}
    throw e
  } finally {
    _reqEnd(_bg, _v)
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
      // 2026-11-05: 401/403 预期鉴权态不上报(见 isExpectedAuth), 但 throw 保留 ——
      // 调用方仍需感知失败, 只是不再往质量日志写一条非真 warning。
      if (!isExpectedAuth(resp.status)) {
        reportError(
          'api_http_error',
          (err.message + ' ' + url).slice(0, 200),
          'HTTP ' + resp.status,
          'warning',
        )
      }
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
      // 2026-11-05: 401/403 预期鉴权态不上报(见 isExpectedAuth), throw 保留
      if (!isExpectedAuth(resp.status)) {
        reportError(
          'api_http_error',
          (err.message + ' ' + url).slice(0, 200),
          'HTTP ' + resp.status,
          'warning',
        )
      }
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
