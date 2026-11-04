import { clearCache } from './client'
import { notify } from './notify'
import { useAppStore } from '../store/useAppStore'
import type { AppOp } from '../store/useAppStore'
import { t } from '../locale'

/**
 * 写操作统一出口(2026-11-03)
 *
 * 要解决的问题: 一次"成功的写"其实有三件事必须一起发生 ——
 *   ① 提示用户(Toast)  ② 让缓存失效  ③ 通知别的页面重拉(事件)
 * 过去这三件事由每个页面手写, 于是必然漏: 规则撤销只 load 了自身、没发事件;
 * 采购下单只弹 toast、既不清缓存也不通知; 而"跨页任务完成"的提示因为 App 在
 * Provider 外, 调的是 no-op —— 从来没显示过。
 *
 * 约定:
 *   · 所有写操作(POST/PUT/DELETE)都走 mutate, 三件套一次声明完
 *   · `events` 是联动契约, 不是可选装饰 —— 谁改了数据都要说清"哪些页面会变"
 *   · `invalidate` 只对裸 fetch 有意义(api.post/put/delete 内部已整表清缓存)
 *   · `op` 返回的 AppOp 进入全局胶囊; opPersist=true 表示由 OpsWatcher 移除
 *     (用于后端异步任务: HTTP 200 只代表"已受理", 不代表"算完")
 */
export type MutateOptions<T> = {
  /** 动作名(用于错误前缀与 ops 标签), 如 '保存规则' */
  label: string
  run: () => Promise<T>
  /** 成功提示; 函数形式可从返回值拼文案 */
  success?: string | ((r: T) => string)
  /** 失败提示前缀, 默认用 label */
  errorPrefix?: string
  /** 需要通知的页面事件(rules-changed / insights-refresh / orders-changed ...) */
  events?: string[]
  /** 裸 fetch 场景的缓存失效关键字(pattern); '*' = 全清 */
  invalidate?: string
  /** 返回非空则登记一个全局操作态 */
  op?: (r: T) => AppOp | null
  /** true: 不在 mutate 结束时移除 op, 交给 OpsWatcher(后端任务) */
  opPersist?: boolean
}

function _errMsg(e: unknown): string {
  const anyE = e as {
    response?: { data?: { detail?: unknown; error?: unknown } }
    detail?: unknown
    message?: unknown
  }
  const raw =
    anyE?.response?.data?.detail ??
    anyE?.response?.data?.error ??
    anyE?.detail ??
    anyE?.message ??
    ''
  const s = typeof raw === 'string' ? raw.trim() : ''
  return s.length > 140 ? s.slice(0, 140) + '…' : s
}

/**
 * 执行一次写操作, 统一处理提示/缓存/事件/操作态。
 * 返回 null 表示失败(已提示调用方无需再弹); 调用方仍需自行处理"成功后本地状态"。
 */
export async function mutate<T>(o: MutateOptions<T>): Promise<T | null> {
  const st = useAppStore.getState()
  let opId = ''
  try {
    const r = await o.run()
    if (o.op) {
      const op = o.op(r)
      if (op && op.id) {
        st.opAdd(op)
        opId = op.id
      }
    }
    // 缓存: api.* 已整体失效; 裸 fetch 走这里('*' = 全清, 与旧代码 clearCache() 等价)
    if (o.invalidate) clearCache(o.invalidate === '*' ? undefined : o.invalidate)
    // 事件放在提示之前: 页面先进入"重拉中", 与 toast 同时被看到
    ;(o.events || []).forEach(ev => window.dispatchEvent(new Event(ev)))
    if (o.success) notify.success(typeof o.success === 'function' ? o.success(r) : o.success)
    return r
  } catch (e) {
    const msg = _errMsg(e)
    // 失败前缀: 有 errorPrefix 用它, 否则用整句模板 kz.fail(避免 label + '失败' 的中英拼接)
    const _prefix = o.errorPrefix ? t(o.errorPrefix) : t('kz.fail', { label: t(o.label) })
    notify.error(_prefix + (msg ? t('kz.fail_detail', { msg }) : ''))
    return null
  } finally {
    if (opId && !o.opPersist) useAppStore.getState().opDone(opId)
  }
}
