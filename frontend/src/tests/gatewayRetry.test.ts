import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'

/**
 * §14 网关白页根治 —— 有界退避重试的守门测试
 *
 * 线上事实（2026-11-09 S4）：实例冻结后冷启动 + 8 并发，边缘返回 503 + Tencent HTML 白页，
 * 请求根本没进应用（无 X-Srv-Ms）。用户端就是白屏。这里用自定义 adapter 复刻三种响应，
 * 验证 client.ts 的重试是**有界、只幂等、不碰超时**的 —— 别让它变成请求风暴放大器。
 */
vi.mock('../api/logger', () => ({ reportError: vi.fn() }))
vi.mock('../api/notify', () => ({
  notify: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))
vi.mock('../locale', () => ({ t: (k: string) => k }))

let calls = 0
let script: Array<'ok' | 503 | 500 | 'timeout'> = []
let seenConfigs: Array<Record<string, unknown>> = []

function makeErr(config: unknown, status: number | null, code?: string) {
  const e = new Error(
    status ? `Request failed with status code ${status}` : code || 'Network Error',
  ) as Error & { config?: unknown; response?: unknown; code?: string; isAxiosError?: boolean }
  e.config = config
  e.isAxiosError = true
  if (code) e.code = code
  if (status) {
    e.response = {
      status,
      statusText: 'ERR',
      headers: {},
      data: '<!doctype html><html><title>Tencent</title></html>',
      config,
    }
  }
  return e
}

axios.defaults.adapter = async (config: never) => {
  calls += 1
  seenConfigs.push(config as unknown as Record<string, unknown>)
  const step = script.shift() || 'ok'
  if (step === 'timeout') throw makeErr(config, null, 'ECONNABORTED')
  if (step === 503) throw makeErr(config, 503)
  if (step === 500) throw makeErr(config, 500)
  return {
    data: { ok: true, data: { v: calls } },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }
}

const { api, clearCache } = await import('../api/client')
const apiGet = api.get

describe('网关瞬时 503：有界退避重试', () => {
  beforeEach(() => {
    calls = 0
    script = []
    seenConfigs = []
    clearCache()
  })

  it('两次 503 后成功：共 3 次请求，最终拿到数据', async () => {
    script = [503, 503, 'ok']
    const r = await apiGet<{ v: number }>('/dashboard/boot')
    expect(calls).toBe(3)
    expect(r.status).toBe(200)
    expect(r.data).toEqual({ v: 3 })
  })

  it('一直 503：只重试 2 次就放弃（不无限放大）', async () => {
    script = [503, 503, 503, 503, 503]
    await expect(apiGet('/dashboard/boot')).rejects.toMatchObject({
      response: { status: 503 },
    })
    expect(calls).toBe(3) // 1 次原始 + 2 次重试
  })

  it('超时（ECONNABORTED）不重试：requests 只有 1 次', async () => {
    script = ['timeout', 'ok']
    await expect(apiGet('/dashboard/boot')).rejects.toBeTruthy()
    expect(calls).toBe(1)
  })

  it('500（非网关态）不重试：应用层错误重试没有意义', async () => {
    script = [500, 'ok']
    await expect(apiGet('/dashboard/boot')).rejects.toBeTruthy()
    expect(calls).toBe(1)
  })

  it('重试请求带 __bg（不点亮用户忙徽标）且计数递增', async () => {
    script = [503, 'ok']
    await apiGet('/dashboard/boot')
    expect(calls).toBe(2)
    expect(seenConfigs[0].__gwRetry).toBeUndefined()
    expect(seenConfigs[1].__gwRetry).toBe(1)
    expect(seenConfigs[1].__bg).toBe(true)
  })
})
