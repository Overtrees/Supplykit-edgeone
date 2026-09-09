import React from 'react'
import { IconAlert } from './Icons'
import { t } from '../locale'

/**
 * 加载失败提示：区分"加载失败(异常)"与"暂无数据(空态)"
 * 失败时显示错误 + 重试按钮，避免误导用户以为是"没数据"
 */
export default function ErrorRetry({ error = '加载失败', desc = '可能是网络异常或服务暂不可用，请重试', onRetry, onReload }) {
  return (
    <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--muted2)' }}>
      <div style={{ fontSize: 48, marginBottom: 12, display: 'flex', justifyContent: 'center', color: 'var(--danger)' }}>
        <IconAlert size={48} />
      </div>
      <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 6, color: 'var(--danger)' }}>{error}</div>
      {desc && <div style={{ fontSize: 13, marginBottom: 16 }}>{desc}</div>}
      <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
        {onRetry && (
          <button
            onClick={onRetry}
            style={{ padding: '8px 24px', borderRadius: 99, border: 'none', background: 'var(--primary)', color: '#fff', fontSize: 14, cursor: 'pointer', fontFamily: 'inherit' }}
          >
            {t("common.retry") || '重试'}
          </button>
        )}
        {onReload && (
          <button
            onClick={onReload}
            style={{ padding: '8px 24px', borderRadius: 99, border: '1px solid var(--border)', background: 'var(--card)', color: 'var(--text)', fontSize: 14, cursor: 'pointer', fontFamily: 'inherit' }}
          >
            刷新页面
          </button>
        )}
      </div>
      {onReload && <div style={{ fontSize: 11, marginTop: 10, color: 'var(--muted2)' }}>连续重试未恢复，建议刷新页面（重新连接服务）</div>}
    </div>
  )
}
