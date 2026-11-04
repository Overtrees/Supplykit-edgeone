import { describe, it, expect, vi, beforeEach } from 'vitest'

// mutate 的三件套依赖两个外部动作: 缓存失效 + 全局提示。这里只验"是否被调用",
// 不验 Toast 长什么样(那是 Toast.test.tsx 的事)。
vi.mock('../api/client', () => ({ clearCache: vi.fn() }))
vi.mock('../api/notify', () => ({
  notify: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

import { clearCache } from '../api/client'
import { notify } from '../api/notify'
import { mutate } from '../api/mutate'
import { useAppStore } from '../store/useAppStore'
import { setLocale } from '../locale'

describe('mutate 写操作统一出口', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ ops: [] })
    localStorage.clear()
    // 断言锁中文文案: jsdom 的 navigator.language 是 en-US, 不显式设置会落到英文词条
    setLocale('zh')
  })

  it('成功: 清缓存 + 发事件 + 提示 + 登记操作态', async () => {
    const hits: string[] = []
    const h = () => hits.push('rules-changed')
    window.addEventListener('rules-changed', h)

    const r = await mutate({
      label: '保存规则',
      run: async () => ({ tid: 7 }),
      success: '已保存',
      events: ['rules-changed'],
      invalidate: '*',
      op: x => ({
        id: 'rules:' + x.tid,
        kind: 'rules',
        label: '规则重算',
        taskId: x.tid,
        startedAt: Date.now(),
      }),
      opPersist: true,
    })

    window.removeEventListener('rules-changed', h)
    expect(r).toEqual({ tid: 7 })
    expect(clearCache).toHaveBeenCalledWith(undefined) // '*' = 全清
    expect(hits).toEqual(['rules-changed'])
    expect(notify.success).toHaveBeenCalledWith('已保存')
    // 后端异步任务: HTTP 200 只代表已受理, op 必须留到评估真正结束
    expect(useAppStore.getState().ops.map(o => o.id)).toEqual(['rules:7'])
  })

  it('失败: 报错带 detail, 且不留下"处理中"', async () => {
    const r = await mutate({
      label: '删除规则',
      run: async () => {
        throw { response: { data: { detail: '规则被引用，无法删除' } } }
      },
      op: () => ({
        id: 'x',
        kind: 'write',
        label: '删除',
        startedAt: Date.now(),
      }),
    })

    expect(r).toBeNull()
    expect(notify.error).toHaveBeenCalledWith('删除规则失败: 规则被引用，无法删除')
    expect(useAppStore.getState().ops).toEqual([])
  })

  it('非持久 op 在成功后被移除(一次性写操作不制造残留)', async () => {
    await mutate({
      label: '下单',
      run: async () => true,
      op: () => ({ id: 'po:1', kind: 'write', label: '下单', startedAt: Date.now() }),
    })
    expect(useAppStore.getState().ops).toEqual([])
  })

  it('不传 events 时不会误发事件(联动必须显式声明)', async () => {
    const h = vi.fn()
    window.addEventListener('rules-changed', h)
    await mutate({ label: 'noop', run: async () => 1 })
    window.removeEventListener('rules-changed', h)
    expect(h).not.toHaveBeenCalled()
  })
})

describe('ops 与 syncing 分离', () => {
  beforeEach(() => {
    useAppStore.setState({ ops: [], syncing: 0 })
    localStorage.clear()
  })

  it('opAdd 不改变 syncing(后端在算 ≠ 前端在拉)', () => {
    useAppStore.getState().opAdd({
      id: 'rules:9',
      kind: 'rules',
      label: '规则重算',
      startedAt: Date.now(),
    })
    expect(useAppStore.getState().syncing).toBe(0)
    expect(useAppStore.getState().ops).toHaveLength(1)
  })

  it('同 id 重复登记保留原始开始时间(轮询 effect 重建不重置耗时)', () => {
    const t0 = Date.now() - 5000
    useAppStore.getState().opAdd({ id: 'a', kind: 'ingest', label: '导入', startedAt: t0 })
    useAppStore.getState().opAdd({ id: 'a', kind: 'ingest', label: '导入', startedAt: Date.now() })
    expect(useAppStore.getState().ops[0].startedAt).toBe(t0)
  })

  it('opDone 只移除目标项', () => {
    useAppStore.getState().opAdd({ id: 'a', kind: 'ingest', label: 'A', startedAt: Date.now() })
    useAppStore.getState().opAdd({ id: 'b', kind: 'ingest', label: 'B', startedAt: Date.now() })
    useAppStore.getState().opDone('a')
    expect(useAppStore.getState().ops.map(o => o.id)).toEqual(['b'])
  })
})
