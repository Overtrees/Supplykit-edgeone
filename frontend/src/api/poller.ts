/**
 * 常驻轮询统一调度器(2026-11-06 审计 P1-22 / 上轮步6)
 *
 * ## 为什么要有这个文件
 *
 * 改造前全仓有 6 处**常驻** setInterval(看板静默刷新 / 数据版本 / 健康探针 /
 * 台账已下单 / 任务页列表 / store.loadAll), 各自为政:
 *   · 5 处**完全不看** `document.visibilityState` —— 标签页挂在后台 8 小时,
 *     看板那处仍然每 30s 拉 summary+aux+stock-risk 三个重接口。用户没在看,
 *     RU 照烧, Makers 函数调用照计。
 *   · 每处自己 `setInterval(fn, N)`, 两个页面同时挂载时周期不同步,
 *     请求在时间轴上散开 —— 后端看到的是"持续的零散压力", 而不是"每 30s 一波"。
 *     散开比集中更贵: 每次都要冷实例/冷连接池。
 *   · 没有在途去重。上一轮还没回来(重接口实测 4~7s, 首次可达 40s),
 *     这一轮又发出去, 请求叠加 → 在途计数永远归不了零(用户报的"一直在同步中")。
 *   · 页面回到前台时只有 2 处做了补刷, 其余要等下一个周期 —— 用户切回来看到的
 *     可能是 30s 前的数字, 而他以为刚刷新过。
 *
 * ## 做法
 *
 * 一个模块级调度器 + 一个 1s tick(开销=遍历几个对象, 可忽略):
 *   · **分层**: 调用点显式声明自己的周期, 常量表只是给"这类数据该多久刷"一个约定,
 *     不强制改周期 —— 实时性是产品口径, 不该被一次性能优化顺手降掉。
 *   · **可见才跑**: hidden 时周期取 `max(自己的周期, 5min)`。不是完全停 ——
 *     用户可能开着看板等告警, 后台完全不刷会让"回来一看全是旧数"。
 *   · **回来立刻补一次**: visibilitychange/focus 可见 → 把到期的项 `lastRun` 归零,
 *     下一 tick 立即执行。刚跑过(<2s)的不补, 避免快速连切标签页时刷屏。
 *   · **在途去重**: 同 key 上一轮未结束, 本 tick 直接跳过(不排队、不叠加)。
 *     常驻轮询的语义是"尽力保证每 N 秒有一次新数据", 不是"每 N 秒必发一个请求"。
 *   · **合并**: 同一次 tick 里所有到期的项一起触发, 天然形成"一波请求",
 *     与后端的实例复用对齐。
 *
 * ## 与 taskWatch 的分工
 *
 * taskWatch 是**驱动**源(它的请求会推进后端任务), 必须串行、必须 1s 心跳、必须
 * 不受可见性影响(任务要在后台继续跑)。本模块只服务**只读**的常驻轮询, 两者不要合并。
 */

/** 分层周期约定(秒级粒度, 调用点直接引用, 便于统一调整) */
export const POLL_EVERY = {
  /** L1 任务态: 任务进度/操作态这类"秒级要准"的只读轮询 */
  task: 30_000,
  /** L2 看板: summary / aux / stock-risk 这一屏的全部数字 */
  dash: 30_000,
  /** L3 台账: 进销存/已下单这类"分钟级够用"的列表 */
  ledger: 300_000,
  /** L4 健康: 探针/数据版本, 只判断"要不要刷新", 不直接展示 */
  health: 30_000,
} as const

/** 页面隐藏时的最小周期: 5 分钟。比这更频繁的后台轮询对用户没有价值。 */
export const HIDDEN_EVERY = 300_000

/** 回到前台时"刚跑过就不补"的窗口: 2s 内跑过的不重复触发(防连切标签页刷屏) */
const RECENT_MS = 2_000

export type PollKey = string

type PollItem = {
  key: PollKey
  /** 可见时的执行周期(ms) */
  every: number
  /** 实际执行体。返回 Promise 时会等它结束才允许下一轮(在途去重) */
  run: () => void | Promise<void>
  /** 上一次**开始**执行的时刻。0 表示"立刻要跑一次" */
  lastRun: number
  /** 本轮是否在途 */
  busy: boolean
  /** 累计执行次数与失败次数(诊断用) */
  runs: number
  fails: number
}

const _items = new Map<PollKey, PollItem>()
let _timer: number | null = null
let _visHooked = false

function _hidden(): boolean {
  try {
    return typeof document !== 'undefined' && document.visibilityState === 'hidden'
  } catch {
    return false
  }
}

async function _fire(it: PollItem, at: number) {
  it.busy = true
  it.lastRun = at
  it.runs += 1
  try {
    await it.run()
  } catch {
    // 单个轮询体失败不影响其它项, 也不打断调度。
    //   各调用点内部本来就有自己的 catch(它们知道失败该怎么展示), 这里只计数。
    it.fails += 1
  } finally {
    it.busy = false
  }
}

function _tick() {
  if (!_items.size) return
  const hidden = _hidden()
  const now = Date.now()
  _items.forEach(it => {
    // 在途去重: 上一轮还没结束就跳过本 tick。
    //   注意**不是**排队 —— 若排队, 一个 40s 的重接口结束后会立刻补发, 反而更密。
    if (it.busy) return
    const every = hidden ? Math.max(it.every, HIDDEN_EVERY) : it.every
    if (now - it.lastRun < every) return
    void _fire(it, now)
  })
}

function _onVisible() {
  if (_hidden()) return
  const now = Date.now()
  _items.forEach(it => {
    if (it.busy) return
    // 刚跑过就跳过(连切标签页时不要每次切回来都刷一轮)
    if (now - it.lastRun < RECENT_MS) return
    it.lastRun = 0
  })
}

function _ensureTimer() {
  if (_timer != null) return
  _timer = window.setInterval(_tick, 1000)
  if (!_visHooked && typeof document !== 'undefined') {
    _visHooked = true
    document.addEventListener('visibilitychange', _onVisible)
    window.addEventListener('focus', _onVisible)
  }
}

function _dropTimer() {
  if (_timer != null) {
    window.clearInterval(_timer)
    _timer = null
  }
}

/**
 * 注册一个常驻轮询。**同 key 重复注册 = 覆盖**(用于依赖变化后换新的闭包, 如切渠道)。
 *
 * @param key     唯一标识, 建议 `'<模块>:<用途>'`, 例如 `'dash:silent'`
 * @param every   可见时的周期(ms), 用 `POLL_EVERY.*`
 * @param run     执行体。**不要自己再判断 visibilityState** —— 调度器已处理;
 *                也不要自己 setInterval。
 * @param immediate 注册后是否立刻跑一次(默认 true: 常驻轮询的调用点几乎都要求"进来先刷")。
 *                false 用于"别处已经拉过一次, 只想要后续周期"的场景。
 * @returns 注销函数(幂等)
 */
export function registerPoll(
  key: PollKey,
  every: number,
  run: () => void | Promise<void>,
  opts: { immediate?: boolean } = {},
): () => void {
  const prev = _items.get(key)
  const immediate = opts.immediate !== false
  _items.set(key, {
    key,
    every: Math.max(1_000, Number(every) || POLL_EVERY.dash),
    run,
    // 覆盖时保留"上次跑的时刻"(否则切渠道会连跑两次),
    //   但 immediate 且从未跑过 → 0, 保证首刷
    lastRun: prev ? prev.lastRun : immediate ? 0 : Date.now(),
    busy: false,
    runs: prev ? prev.runs : 0,
    fails: prev ? prev.fails : 0,
  })
  _ensureTimer()
  return () => unregisterPoll(key)
}

/** 注销。幂等: 不存在也返回成功。 */
export function unregisterPoll(key: PollKey) {
  _items.delete(key)
  if (!_items.size) _dropTimer()
}

/** 立即执行一次(不等周期)。页面在"写操作完成后"想马上看到新数据时用。 */
export function pollNow(key: PollKey) {
  const it = _items.get(key)
  if (!it || it.busy) return
  it.lastRun = 0
}

/** 诊断: 当前注册了哪些轮询、各自跑了多少次、失败几次 */
export function pollStats(): Array<{
  key: string
  every: number
  busy: boolean
  runs: number
  fails: number
}> {
  return [..._items.values()].map(it => ({
    key: it.key,
    every: it.every,
    busy: it.busy,
    runs: it.runs,
    fails: it.fails,
  }))
}

/** 仅供测试: 重置内部状态(生产代码不要调用) */
export function __resetPolls() {
  _items.clear()
  _dropTimer()
}
