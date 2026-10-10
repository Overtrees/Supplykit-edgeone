import React from 'react'
import { IconAlert } from './Icons'
import { t } from '../locale'
import { reportError } from '../api/logger'

interface ErrorBoundaryProps {
  children: React.ReactNode
}
interface ErrorBoundaryState {
  err: Error | null
  retryKey: number
}

/**
 * 渲染兜底: 捕获页面渲染异常(概率性网络波动/数据形状异常) → 显示错误 + 重试 + 回到看板
 * 避免用户卡死在空白/异常态(回到看板会触发完整重拉, 自愈恢复)
 */
class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { err: null, retryKey: 0 }
  static getDerivedStateFromError(err: Error): ErrorBoundaryState {
    return { err }
  }
  componentDidCatch(err: Error, info: React.ErrorInfo) {
    // 统一收口: 组件渲染错误上报(开发者层)
    reportError(
      'component_error',
      err.message || String(err),
      ((err.stack || '') + '\n' + (info.componentStack || '')).slice(0, 800),
    )
  }
  retry = () => this.setState(s2 => ({ err: null, retryKey: s2.retryKey + 1 }))
  render() {
    if (this.state.err) {
      return (
        <div
          style={{
            padding: 24,
            background: 'rgba(225,29,72,0.08)',
            border: '1px solid rgba(225,29,72,0.3)',
            borderRadius: 32,
            margin: 12,
          }}
        >
          <div style={{ fontSize: 20, marginBottom: 8 }}>
            <IconAlert size={20} />
          </div>
          <div style={{ fontWeight: 700, fontSize: 15, color: 'var(--danger)', marginBottom: 4 }}>
            组件渲染异常
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted2)', marginBottom: 10 }}>
            详细信息已记录日志
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              onClick={() => this.retry()}
              style={{
                padding: '6px 14px',
                fontSize: 12,
                border: '1px solid rgba(225,29,72,0.3)',
                borderRadius: 32,
                background: 'var(--card)',
                color: 'var(--danger)',
                cursor: 'pointer',
              }}
            >
              {t('common.retry')}
            </button>
            <button
              onClick={() => {
                this.setState({ err: null })
                try {
                  ;(window as any).__setPage && (window as any).__setPage('dash')
                } catch (e) {}
              }}
              style={{
                padding: '6px 14px',
                fontSize: 12,
                border: 'none',
                borderRadius: 32,
                background: 'var(--primary)',
                color: '#fff',
                cursor: 'pointer',
              }}
            >
              回到看板
            </button>
          </div>
        </div>
      )
    }
    return <div key={this.state.retryKey}>{this.props.children}</div>
  }
}

export default ErrorBoundary
