import React from 'react'
import ReactDOM from 'react-dom/client'
import * as Sentry from '@sentry/react'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './styles.css'

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN || '',
  environment: import.meta.env.VITE_SENTRY_ENV || 'production',
  integrations: [Sentry.browserTracingIntegration()],
  tracesSampleRate: 0.1,
})

// 最后一道空态防线: 未捕获错误且 React 整树卸载(root 清空)时显示'系统正在维护中'覆盖层
// (ErrorBoundary 兜住渲染错误显示兜底页; ErrorBoundary 也崩/挂载前 JS 失败 → 本层接管)
function showMaintenance() {
  if (document.getElementById('app-maintenance')) return
  const d = document.createElement('div')
  d.id = 'app-maintenance'
  d.style.cssText = "position:fixed;inset:0;z-index:99999;background:#f2f2f7;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;font-family:-apple-system,sans-serif"
  d.innerHTML = '<div style="font-size:26px;font-weight:800;color:#0f172a">SupplyKit</div>'
    + '<div style="font-size:14px;color:#64748b">系统正在维护中，请稍后重试</div>'
    + '<button id="app-reload" style="margin-top:6px;padding:8px 24px;border:none;border-radius:99px;background:#007AFF;color:#fff;font-size:14px;cursor:pointer">刷新</button>'
  document.body.appendChild(d)
  document.getElementById('app-reload').onclick = () => { location.reload() }
}
// 防误报: 仅 React 已挂载(render 发起后) + root 持续为空(挂载/重渲染瞬态排除) + 排除资源加载错误
let _rendered = false
function _maybeShowMaintenance() {
  if (!_rendered) return  // React 挂载前不动(JS 未加载场景由 index.html 静态 fallback 兜底)
  setTimeout(() => {
    const root = document.getElementById('root')
    if (!root || root.childElementCount !== 0) return
    // 复查确认: StrictMode 双渲染/登录态切换等瞬态 root 可能短暂清空, 2.5s 后仍空才判整树崩溃
    setTimeout(() => {
      const r2 = document.getElementById('root')
      if (r2 && r2.childElementCount === 0) showMaintenance()
    }, 2500)
  }, 500)
}
window.addEventListener('error', (e) => {
  if (e.target && e.target !== window) return  // 资源加载失败(img/script/src)非致命, 不触发
  _maybeShowMaintenance()
})
window.addEventListener('unhandledrejection', _maybeShowMaintenance)

_rendered = true  // render 已发起: 之后 root 持续为空才算整树崩溃
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary><App /></ErrorBoundary>
  </React.StrictMode>,
)

// Service Worker 注册
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
