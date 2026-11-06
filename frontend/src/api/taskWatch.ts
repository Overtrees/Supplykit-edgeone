/**
 * 任务存活看守(2026-11-05)
 *
 * ## 为什么需要
 *
 * 后端的续跑是**前端轮询驱动**的:`GET /api/seed/fill/status` 每被调一次, 就推进一步。
 * 而这一步由服务端用租约独占(`lease_until` + `lease_token`, TTL 30s、每 10s 心跳),
 * 防止两次轮询并发跑同一步把数据写重。
 *
 * ⚠️ 本模块是全仓**唯一**的驱动源(2026-11-05)。其余任何"看状态"的调用都必须带 `probe=1`
 *    走纯读分支 —— 否则就会出现同一任务被 5~6 处轮询同时推进: 抢不到锁的白烧 RU,
 *    抢到的把用户可见的忙徽标拖成一直转(用户报「什么页面都在加载中/同步中」的真因)。
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

/** 续跑请求超时: 后端单段预算 90s + 10s 余量, 且必须小于网关硬上限 120s。
 *  旧值 115000 贴着网关 —— 后端一旦跑满 120s, 前端和网络层一起断, 更难判断是谁的问题。 */
export const DRIVE_TIMEOUT = 100000

/** 心跳: 每隔多久问一次状态(有在跑的任务时) */
const BEAT_MS = 3000

type Watch = {
  taskId: string
  kind: WatchKind
  /** 最近一次续跑返回的时间戳(本地) */
  lastDrive: number
  /** 连续多少次"没推进" */
  stalls: number
  /** 最近一次看到的进度指纹(step/offset), 用来判断有没有推进 */
  sig: string
  /** 最近一次 status 返回的进度快照(供胶囊/全局提示显示"第 N/M 段") */
  lastTick: Record<string, unknown> | null
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

/**
 * 看守类型: 三种任务各有自己的续跑端点, 打错端点 = 每轮 `{ok:false,"任务不存在"}`,
 * 看守看得见任务却永远推不动它。
 *
 * 2026-11-06 修: 原实现是 `task_type === 'seed' ? 'seed' : 'cleansing'` ——
 * `cleansing_eval` / `export` 全被归进 `'cleansing'`, 于是去打 `/cleansing/status?task_id=<id>`,
 * 而那个端点按"清洗任务"解析任意 id: 读 `clean_<id>` 找不到 → 抛"源文件不存在" →
 * 它的 except 分支执行 `UPDATE status='error'`。**导出任务 3 秒内就被判死**,
 * 用户看到假的「导出失败」; 评估任务被中断则告警不更新。
 *
 * 现在改为按 task_type 精确路由, **认不出的类型一律不接管**(绝不用猜的端点去推别人的任务)。
 */
export type WatchKind = 'seed' | 'cleansing' | 'cleansing_eval' | 'export'

function _kindOf(taskType: string): WatchKind | null {
  const tt = String(taskType || '')
  if (tt === 'seed') return 'seed'
  if (tt === 'cleansing') return 'cleansing'
  if (tt === 'cleansing_eval') return 'cleansing_eval'
  if (tt === 'export') return 'export'
  // 未登记的类型(reset 等一次性任务 / 未来新增): 不接管。
  //   宁可不推进(它有自己的驱动方), 也不能拿错端点把别人的任务写成 error。
  return null
}

function _url(kind: WatchKind, id: string): string {
  if (kind === 'seed') {
    // driver=frontend 是**写**进来的: 本端点是驱动端点(查一次推进一窗),
    //   绝不能加 probe —— probe 是给"只看进度"的调用方用的纯读分支。
    return API + '/api/seed/fill/status?task_id=' + encodeURIComponent(id) + '&driver=frontend'
  }
  if (kind === 'cleansing_eval') {
    return API + '/api/cleansing/eval-status?task_id=' + encodeURIComponent(id) + '&driver=frontend'
  }
  if (kind === 'export') {
    return API + '/api/exports/status?task_id=' + encodeURIComponent(id) + '&driver=frontend'
  }
  return (
    API + '/api/cleansing/status?task_id=' + encodeURIComponent(id) + '&driver=frontend&probe=0'
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
      // 2026-11-05: 看守请求是**后台**的 —— 它不该点亮"加载中/同步中"徽标。
      //   一个长任务在跑时本守卫几乎永远有一个请求在途(每 3s 一次、单次预算 75s),
      //   计入前台计数就会让用户在任何页面都看到"同步中", 误以为页面在卡。
      background: true,
    })
    // 后端两种形状: seed 是 {data:{status,...}}, cleansing 是 {ok,status,...}
    const body = ((d && ((d as Record<string, unknown>).data || d)) || {}) as Record<
      string,
      unknown
    >
    const st = String(body.status || '')
    // 指纹只看「进度是否前进」, 不能把 stalled_s 放进来 —— 它每秒都在变,
    //   会让 stalls 永远归零, 退避失效, 租约被别人持有时前端反而刷得更凶(空烧 RU)。
    const sig = [body.step ?? '', body.offset ?? '', st].join('|')
    if (sig !== w.sig) {
      w.sig = sig
      w.stalls = 0
    } else {
      w.stalls += 1
    }
    w.lastTick = body
    w.onTick && w.onTick(w.taskId, body)
    if (st === 'done') {
      unwatch(w.taskId)
      w.onDone && w.onDone(w.taskId)
    } else if (st === 'error' || st === 'not_found') {
      unwatch(w.taskId)
      w.onFail && w.onFail(w.taskId, String(body.error || st))
    }
  } catch {
    // 超时/断网 **绝不摘掉看守, 也不报"任务失败"**: 后端单段可能被网关切断,
    //   但租约(30s TTL + 心跳)停止续期后会自动释放并重跑(每段幂等), 任务最终会由
    //   status 明确落到 done / error。旧实现连续失败 40 次就 unwatch + onFail, 会把
    //   "响应丢了但任务还在跑"误报成失败, 用户看到的就是"刚才还好好的怎么突然失败了"。
    //   这里只累加 stalls 触发指数退避(最多 15s), 保持看守直到后端给终态。
    w.stalls += 1
    w.lastTick = {
      ...(w.lastTick || {}),
      status: 'running',
      network_stalled: true,
      stalled_s: w.stalls * 3,
    }
    w.onTick && w.onTick(w.taskId, w.lastTick)
    _notify()
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
  kind: WatchKind,
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
    lastTick: prev ? prev.lastTick : null,
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
 * 全局看守摘要(胶囊/全局提示用)
 *
 * 回答「持锁期间别的页面怎么办 / 多次切页面怎么办」: 看守是模块级的, 与页面无关,
 * 任何页面都能从这里读到同一份真实进度 —— 不需要每个页面自己去轮询一遍任务表,
 * 也不需要在页面上假装有 loading。后端抢不到锁时也会返回 step/stalled_s,
 * 所以这里拿到的永远是「第几段、卡了多久」, 而不是一个空泛的"同步中"。
 */
export function watchSummary(): {
  count: number
  step: number
  total: number
  stalled: number
  lease: boolean
} {
  const w = _watches.size ? [..._watches.values()][0] : null
  // 不能叫 t —— i18n 门禁会把它当成遮蔽翻译函数 t() 的局部变量(红线判定)
  const tick = (w?.lastTick || {}) as Record<string, unknown>
  return {
    count: _watches.size,
    step: Number(tick.step ?? 0),
    total: Number(tick.total_step ?? 0),
    stalled: Number(tick.stalled_s ?? w?.stalls ?? 0),
    lease: Boolean(tick.lease_held),
  }
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
    // 2026-11-06: 原为 seed/cleansing 二元 —— cleansing_eval 与 export 被误归 cleansing,
    //   打错端点会把任务写成 error(见 _kindOf 注释)。认不出的类型直接跳过, 不当 cleansing。
    const kind = _kindOf(String(t.task_type || ''))
    if (!kind) return
    watch(id, kind, cb)
  })
}
