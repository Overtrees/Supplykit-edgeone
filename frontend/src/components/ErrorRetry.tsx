import React from 'react'
import { IconAlert } from './Icons'
import { t } from '../locale'

/**
 * 错误文案统一判断(供 ErrorRetry 与各状态共用):
 * 网络层失败(axios 无 response: 断网/超时) → "网络波动"; 服务端/业务错误(有 response) → "加载失败"
 */
export function errText(e?: any): string {
  return e && e.response ? '加载失败' : '网络波动'
}

/**
 * 加载失败提示：区分"加载失败(异常)"与"暂无数据(空态)"
 * 失败时显示错误 + 重试按钮，避免误导用户以为是"没数据"
 * err 传入时 desc 自动按错误类型选择(网络波动/加载失败)——一个组件承担两种职责
 */
export default function ErrorRetry({ error = '加载失败', desc, err, onRetry, onReload = null }) {
  const finalDesc = desc ?? (err ? errText(err) : '网络异常或服务不可用')
  return (
    <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted2)' }}>
      <div
        style={{
          fontSize: 48,
          marginBottom: 12,
          display: 'flex',
          justifyContent: 'center',
          color: 'var(--danger)',
        }}
      >
        <IconAlert size={48} />
      </div>
      <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 6, color: 'var(--danger)' }}>
        {error}
      </div>
      {finalDesc && <div style={{ fontSize: 13, marginBottom: 16 }}>{finalDesc}</div>}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
        {onRetry && (
          <button
            onClick={onRetry}
            style={{
              padding: '8px 24px',
              borderRadius: 99,
              border: 'none',
              background: 'var(--primary)',
              color: '#fff',
              fontSize: 14,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {t('common.retry') || '重试'}
          </button>
        )}
        {onReload && (
          <button
            onClick={onReload}
            style={{
              padding: '8px 24px',
              borderRadius: 99,
              border: '1px solid var(--border)',
              background: 'var(--card)',
              color: 'var(--text)',
              fontSize: 14,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            刷新页面
          </button>
        )}
      </div>
      {onReload && (
        <div style={{ fontSize: 11, marginTop: 10, color: 'var(--muted2)' }}>
          连续重试未恢复，建议刷新页面（重新连接服务）
        </div>
      )}
    </div>
  )
}
