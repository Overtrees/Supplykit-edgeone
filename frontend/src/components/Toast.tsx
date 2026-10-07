import React, { useState, createContext, useContext, useEffect, useRef } from 'react'

interface ToastItem {
  id: number
  type: 'success' | 'error' | 'warning' | 'info'
  title: string
  duration?: number
  action?: { label: string; handler: () => void }
}

interface ToastContextValue {
  add: (t: Omit<ToastItem, 'id'>) => void
  success: (msg: string) => void
  error: (msg: string) => void
  warning: (msg: string) => void
  info: (msg: string) => void
  clear: () => void
}

// 默认 no-op: Provider 外调用(如 App 组件体任务轮询)不崩, Provider 内正常 —— 防止 NullPointer 崩溃
const _noop = () => {}
const ToastContext = createContext<ToastContextValue>({
  add: _noop,
  success: _noop,
  error: _noop,
  warning: _noop,
  info: _noop,
  clear: _noop,
})

export function useToast() {
  return useContext(ToastContext)
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const add = (t: Omit<ToastItem, 'id'>) => {
    const id = Date.now()
    setToasts(p => [...p, { ...t, id }])
    setTimeout(() => setToasts(p => p.filter(x => x.id !== id)), t.duration || 3000)
  }
  const success = (msg: string) => add({ type: 'success', title: msg })
  const error = (msg: string) => add({ type: 'error', title: msg })
  const warning = (msg: string) => add({ type: 'warning', title: msg })
  const info = (msg: string) => add({ type: 'info', title: msg })
  const clear = () => setToasts([])

  // 各类型配色（与全局语义色对齐: success 绿 / error 红 / warning 琥珀 / info 蓝）
  const TONE = {
    success: { bg: 'rgba(5,150,105,0.12)', bd: 'rgba(5,150,105,0.25)', fg: 'var(--success)' },
    error: { bg: 'rgba(225,29,72,0.12)', bd: 'rgba(225,29,72,0.25)', fg: 'var(--danger)' },
    warning: {
      bg: 'rgba(217,119,6,0.12)',
      bd: 'rgba(217,119,6,0.3)',
      fg: 'var(--warning, #d97706)',
    },
    info: { bg: 'rgba(14,165,233,0.12)', bd: 'rgba(14,165,233,0.3)', fg: 'var(--primary)' },
  }

  return (
    <ToastContext.Provider value={{ add, success, error, warning, info, clear }}>
      {children}
      <div
        style={{
          position: 'fixed',
          top: 'calc(env(safe-area-inset-top) + 12px)',
          right: 16,
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          alignItems: 'flex-end',
        }}
      >
        {toasts.slice(-3).map(t => (
          <div
            key={t.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 16px',
              borderRadius: 32,
              background: (TONE[t.type] || TONE.success).bg,
              border: '1px solid ' + (TONE[t.type] || TONE.success).bd,
              color: (TONE[t.type] || TONE.success).fg,
              fontSize: 14,
              fontWeight: 500,
              boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
              maxWidth: 360,
              backdropFilter: 'blur(var(--glass-blur))',
              WebkitBackdropFilter: 'blur(var(--glass-blur))',
            }}
          >
            <span style={{ flex: 1 }}>{t.title}</span>
            {t.action && (
              <span
                onClick={t.action.handler}
                className="clickable"
                style={{
                  fontSize: 13,
                  fontWeight: 700,
                  color: 'var(--primary)',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                {t.action.label}
              </span>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

// 页面切换立即清 toast(在 ToastProvider 内使用, toast 有效) —— 任务完成提示不跨页残留
export function ToastAutoClear({ page }: { page: string }) {
  const toast = useToast()
  const prev = useRef(page)
  useEffect(() => {
    if (prev.current !== page) toast.clear()
    prev.current = page
  }, [page, toast])
  return null
}

export default ToastProvider
