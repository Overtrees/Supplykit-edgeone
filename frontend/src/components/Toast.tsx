import React, { useState, createContext, useContext, useEffect, useRef } from 'react'

interface ToastItem {
  id: number
  type: 'success' | 'error'
  title: string
  duration?: number
  action?: { label: string; handler: () => void }
}

interface ToastContextValue {
  add: (t: Omit<ToastItem, 'id'>) => void
  success: (msg: string) => void
  error: (msg: string) => void
  clear: () => void
}

// 默认 no-op: Provider 外调用(如 App 组件体任务轮询)不崩, Provider 内正常 —— 防止 NullPointer 崩溃
const _noop = () => {}
const ToastContext = createContext<ToastContextValue>({ add: _noop, success: _noop, error: _noop, clear: _noop })

export function useToast() { return useContext(ToastContext) }

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const add = (t: Omit<ToastItem, 'id'>) => {
    const id = Date.now()
    setToasts(p => [...p, { ...t, id }])
    setTimeout(() => setToasts(p => p.filter(x => x.id !== id)), t.duration || 3000)
  }
  const success = (msg: string) => add({ type: 'success', title: msg })
  const error = (msg: string) => add({ type: 'error', title: msg })
  const clear = () => setToasts([])

  return <ToastContext.Provider value={{ add, success, error, clear }}>
    {children}
    <div style={{ position: 'fixed', top: 'calc(env(safe-area-inset-top) + 12px)', right: 16, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end' }}>
      {toasts.slice(-3).map(t => (
        <div key={t.id} style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 32,
          background: t.type === 'error' ? 'rgba(225,29,72,0.12)' : 'rgba(5,150,105,0.12)',
          border: '1px solid ' + (t.type === 'error' ? 'rgba(225,29,72,0.25)' : 'rgba(5,150,105,0.25)'),
          color: t.type === 'error' ? 'var(--danger)' : 'var(--success)',
          fontSize: 14, fontWeight: 500, boxShadow: '0 2px 8px rgba(0,0,0,0.1)', maxWidth: 360,
          backdropFilter: 'blur(var(--glass-blur))', WebkitBackdropFilter: 'blur(var(--glass-blur))'
        }}>
          <span style={{ flex: 1 }}>{t.title}</span>
          {t.action && (
            <span onClick={t.action.handler} className="clickable" style={{ fontSize: 13, fontWeight: 700, color: 'var(--primary)', cursor: 'pointer', flexShrink: 0 }}>
              {t.action.label}
            </span>
          )}
        </div>
      ))}
    </div>
  </ToastContext.Provider>
}

// 页面切换立即清 toast(在 ToastProvider 内使用, toast 有效) —— 任务完成提示不跨页残留
export function ToastAutoClear({ page }: { page: string }) {
  const toast = useToast()
  const prev = useRef(page)
  useEffect(() => { if (prev.current !== page) toast.clear(); prev.current = page }, [page, toast])
  return null
}

export default ToastProvider