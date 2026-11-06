/**
 * 统一轮询调度器的四维断言(2026-11-06 S1)
 *
 * 这个模块的价值全在"边界行为"上, 所以测的也是边界:
 *   · 在途去重 —— 上一轮没回来时不再发(可靠性: 请求不叠加)
 *   · 可见性 —— 隐藏时降到 5min(实时性有底线, 但不白烧 RU)
 *   · 回前台补跑 —— 切回来立刻有新数(实时性)
 *   · 注销幂等 —— 卸载后不再跑(不泄漏)
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  registerPoll,
  unregisterPoll,
  pollNow,
  pollStats,
  __resetPolls,
  POLL_EVERY,
  HIDDEN_EVERY,
} from '../api/poller'

/** jsdom 的 visibilityState 是只读的, 用 defineProperty 改 */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => (hidden ? 'hidden' : 'visible'),
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('poller', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    __resetPolls()
    setHidden(false)
  })

  afterEach(() => {
    __resetPolls()
    vi.useRealTimers()
  })

  it('注册后立刻跑一次, 之后按周期跑', async () => {
    const fn = vi.fn()
    registerPoll('t:basic', 5_000, fn)
    // immediate 默认 true → 首次 tick(t=1s) 就执行
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)
    // 周期从"开始执行时刻"算起 → 下一次要到 t=6s
    await vi.advanceTimersByTimeAsync(4_000)
    expect(fn).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('immediate:false 时首轮不跑, 等满一个周期', async () => {
    const fn = vi.fn()
    registerPoll('t:lazy', 5_000, fn, { immediate: false })
    await vi.advanceTimersByTimeAsync(3_000)
    expect(fn).toHaveBeenCalledTimes(0)
    await vi.advanceTimersByTimeAsync(3_000)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('在途去重: 上一轮没结束绝不叠加', async () => {
    let release: (() => void) | null = null
    const fn = vi.fn(
      () =>
        new Promise<void>(res => {
          release = res
        }),
    )
    registerPoll('t:slow', 1_000, fn)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)
    // 卡住 10 个周期 —— 一次都不该重发
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fn).toHaveBeenCalledTimes(1)
    // 放行后下一拍才允许再发
    release!()
    await vi.advanceTimersByTimeAsync(2_000)
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('页面隐藏时降到 5min, 回前台立刻补一次', async () => {
    const fn = vi.fn()
    registerPoll('t:vis', POLL_EVERY.dash, fn)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)

    setHidden(true)
    // 隐藏后 30s(原本的周期)不应该再跑
    await vi.advanceTimersByTimeAsync(POLL_EVERY.dash)
    expect(fn).toHaveBeenCalledTimes(1)
    // 但满 5min 仍会跑一次(不是完全停)
    await vi.advanceTimersByTimeAsync(HIDDEN_EVERY)
    expect(fn).toHaveBeenCalledTimes(2)

    // 回前台: 距上次已远超 2s → 立刻补跑
    setHidden(false)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(3)
  })

  it('隐藏时不会因回前台补跑而刷屏(2s 内跑过就跳过)', async () => {
    const fn = vi.fn()
    registerPoll('t:vis2', 60_000, fn)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)
    // 刚跑完就隐藏再显示 —— 不应重复触发
    setHidden(true)
    setHidden(false)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('同 key 重复注册 = 覆盖, 不产生第二条', async () => {
    const a = vi.fn()
    const b = vi.fn()
    registerPoll('t:dup', 5_000, a)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(a).toHaveBeenCalledTimes(1)
    registerPoll('t:dup', 5_000, b)
    await vi.advanceTimersByTimeAsync(5_000)
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)
    expect(pollStats().filter(x => x.key === 't:dup')).toHaveLength(1)
  })

  it('注销后不再执行, 且幂等', async () => {
    const fn = vi.fn()
    const stop = registerPoll('t:stop', 1_000, fn)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)
    stop()
    stop()
    unregisterPoll('t:stop')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(pollStats()).toHaveLength(0)
  })

  it('pollNow 让到期的项立刻跑(写操作后马上看新数)', async () => {
    const fn = vi.fn()
    registerPoll('t:now', 60_000, fn)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(1)
    pollNow('t:now')
    await vi.advanceTimersByTimeAsync(1_000)
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('单个轮询体抛错不影响其它项', async () => {
    const bad = vi.fn(() => {
      throw new Error('boom')
    })
    const good = vi.fn()
    registerPoll('t:bad', 1_000, bad)
    registerPoll('t:good', 1_000, good)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(bad).toHaveBeenCalledTimes(1)
    expect(good).toHaveBeenCalledTimes(1)
    const st = pollStats()
    expect(st.find(x => x.key === 't:bad')!.fails).toBe(1)
    expect(st.find(x => x.key === 't:good')!.fails).toBe(0)
    // 失败项下一轮照跑(不是熔断)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(bad).toHaveBeenCalledTimes(2)
  })
})
