/**
 * 任务存活看守(2026-11-05)
 *
 * ## 为什么需要
 *
 * 后端的续跑是**前端轮询驱动**的:`GET /api/seed/fill/status` 每被调一次, 就推进一步。
 * 而这一步由服务端用 `lease_until` 独占租约(600s), 防止两次轮询并发跑同一步把数据写重。
 *
 * 三件事必须同时成立, 才不会出现"租约把任务锁死":
 *   1. **单次续跑必须短**: 每步都要在自己的 `timeout` 内结束。请求被中断(切走/关页/网关掐断)
 *      而服务端还在跑, 租约就会一直生效到过期;
 *   2. **页面切走不能等于任务停**: 用户在看板看进销存的时候, 那个 seed 仍在跑。
 *      原来续跑只挂在 TaskPage 上, 一离开就没人推进, 回来才发现"跑了十分钟还是刚刚";
 *   3. **卡住要能看见**: 租约过期后要能重新抢; 持续无进展要明说, 而不是静默等人重试。
 *
 * 所以把"驱动"从任务页搬到一个模块级看守:谁在页面上都能推进, 且同一时刻只有一个请求。
 * 租约那层仍然是最终防线(多标签/多实例也能保证同一步只跑一次)。
 */
import { apiJson } from '../api/client'

const API = import.meta.env.VITE_API_BASE_URL || ''

/** 续跑请求超时: 必须小于后端单步预算(平台建议 ≤90s)与网关硬上限 120s */
export const DRIVE_TIMEOUT = 115000

/** 心跳: 每隔多久问一次状态(有在跑的任务时) */
const BEAT_MS = 3000

type Watch = {
  taskId: string
  kind: 'seed' | 'cleansing'
  /** 最近一次续跑返回的时间戳(本地) */
  lastDrive: number
  /** 连续多少次"没推进" */
  stalls: number
  /** 最近一次看到的进度指纹(step/offset), 用来判断有没有推进 */
  sig: string
  onDone?: (id: string) => void
  onFail?: (id: string, err: string) => void
  onTick?: (id: string, payload: unknown) => void
}

const _watches = new Map<string, Watch>()
let _timer: number | null = null
let _inflight = false
const _listeners = new Set<() => void>()

function _notify() {
  _listeners.forEach(fn => {
    try {
      fn()
    } catch {
      /* 单个监听抛错不影响其它 */
    }
  })
}

/** 订阅看守状态变化(胶囊/OperationBar 用) */
export function onWatchChange(fn: () => void): () => void {
  _listeners.add(fn)
  return () => {
    _listeners.delete(fn)
  }
}

export function watchCount(): number {
  return _watches.size
}

function _url(kind: 'seed' | 'cleansing', id: string): string {
  return (
    API +
    (kind === 'seed'
      ? '/api/seed/fill/status?task_id=' + encodeURIComponent(id) + '&driver=frontend'
      : '/api/cleansing/status?task_id=' + encodeURIComponent(id))
  )
}

/**
 * 一次续跑: 成功推进 → 记 lastDrive; 无进展 → stalls+1
 *
 * 串行化: 全局只允许一个请求在途。后端本来有租约, 但前端并发只会让"抢不到锁"的请求
 * 白白占住一个连接、把续跑间隔拉长(而且会把 In-flight 计数打高, 胶囊一直转)。
 */
async function _drive(w: Watch) {
  if (_inflight) return
  _inflight = true
  w.lastDrive = Date.now()
  try {
    const d = await apiJson<Record<string, unknown>>('GET', _url(w.kind, w.taskId), {
      timeout: DRIVE_TIMEOUT,
    })
    // 后端两种形状: seed 是 {data:{status,...}}, cleansing 是 {ok,status,...}
    const body = ((d && ((d as Record<string, unknown>).data || d)) || {}) as Record<
      string,
      unknown
    >
    const st = String(body.status || '')
    const sig = [body.step ?? '', body.offset ?? '', st].join('|')
    if (sig !== w.sig) {
      w.sig = sig
      w.stalls = 0
    } else {
      w.stalls += 1
    }
    w.onTick && w.onTick(w.taskId, body)
    if (st === 'done') {
      unwatch(w.taskId)
      w.onDone && w.onDone(w.taskId)
    } else if (st === 'error' || st === 'not_found') {
      unwatch(w.taskId)
      w.onFail && w.onFail(w.taskId, String(body.error || st))
    }
  } catch (e) {
    // 超时/断网都不摘掉看守: 服务端可能仍在跑(租约保护着), 下次心跳会看到真实状态。
    // 只在连续失败很多次后放弃, 并把原因交给调用方提示。
    w.stalls += 1
    if (w.stalls >= 40) {
      unwatch(w.taskId)
      w.onFail && w.onFail(w.taskId, String((e as Error)?.message || 'network'))
    }
  } finally {
    _inflight = false
  }
}

function _beat() {
  if (!_watches.size) return
  // 若某个任务"租约被人持有、这轮没推进", 别在 3s 节奏上硬撞 —— 按 stalls 退避,
  //   最多退到 15s。这样单任务卡住时不会刷请求(也不烧 RU), 但一旦解锁马上接上。
  const now = Date.now()
  _watches.forEach(w => {
    const gap = Math.min(BEAT_MS * Math.pow(1.25, Math.min(w.stalls, 8)), 15000)
    if (now - w.lastDrive >= gap) void _drive(w)
  })
  _notify()
}

function _ensureTimer() {
  if (_timer != null) return
  _timer = window.setInterval(_beat, 1000)
}

function _dropTimer() {
  if (_timer == null) return
  window.clearInterval(_timer)
  _timer = null
}

/** 开始看守(幂等: 同一个 taskId 重复调用只更新回调) */
export function watch(
  taskId: string,
  kind: 'seed' | 'cleansing',
  cb: { onDone?: Watch['onDone']; onFail?: Watch['onFail']; onTick?: Watch['onTick'] } = {},
) {
  if (!taskId) return
  const prev = _watches.get(taskId)
  _watches.set(taskId, {
    taskId,
    kind,
    lastDrive: prev ? prev.lastDrive : 0,
    stalls: prev ? prev.stalls : 0,
    sig: prev ? prev.sig : '',
    onDone: cb.onDone || prev?.onDone,
    onFail: cb.onFail || prev?.onFail,
    onTick: cb.onTick || prev?.onTick,
  })
  _ensureTimer()
  _notify()
}

export function unwatch(taskId: string) {
  _watches.delete(taskId)
  if (!_watches.size) _dropTimer()
  _notify()
}

/** 该任务是否正在被看守(用于任务卡显示"前端驱动中") */
export function isWatched(taskId: string): boolean {
  return _watches.has(taskId)
}

/**
 * 从任务列表批量接管 running 的任务
 *
 * 这是"随手切页面也不会把任务卡死"的关键: 任何页面拿到任务列表(或者只是 App 层的
 * 一次轻量查询)都可以调它, 进来的 running 任务全部有人推进。
 */
export function adoptRunning(
  list: Array<{ task_id?: string; status?: string; task_type?: string }>,
  cb: {
    onDone?: (id: string) => void
    onFail?: (id: string, err: string) => void
    onTick?: (id: string, payload: unknown) => void
  } = {},
) {
  ;(list || []).forEach(t => {
    const id = String(t?.task_id || '')
    if (!id) return
    if (String(t.status) !== 'running') {
      // 已结束的不再占用看守名额(避免回调不触发导致残留)
      if (_watches.has(id)) unwatch(id)
      return
    }
    const kind: 'seed' | 'cleansing' = String(t.task_type || '') === 'seed' ? 'seed' : 'cleansing'
    watch(id, kind, cb)
  })
}
