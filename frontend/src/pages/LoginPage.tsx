import { IconScale } from '../components/Icons'
import { apiJson } from '../api/client'
import React, { useState } from 'react'
import { t } from '../locale'

const API = import.meta.env.VITE_API_BASE_URL || ''

export default function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const doLogin = async () => {
    if (loading) return // 防重复点击/Enter 并发(慢网络时曾连发两次)
    if (!username || !password) {
      setError(t('login.need_both'))
      return
    }
    setLoading(true)
    setError('')
    // 超时(15s): 无痕/弱网下曾挂起 30s+ 无反馈, 用户重复点击(S3-2 收口: apiJson timeout)
    try {
      const d = await apiJson('POST', API + '/api/auth/login', {
        timeout: 15000,
        data: { username, password },
      })
      if (d.ok && d.token) {
        try {
          localStorage.setItem('c_token', d.token)
        } catch {}
        try {
          localStorage.setItem('c_user', d.user || username)
        } catch {}
        onLogin()
      } else {
        setError(d.detail || t('login.bad_cred'))
      }
    } catch {
      setError(t('login.net_fail'))
    }
    setLoading(false)
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100svh',
        padding: '40px 24px',
        overflowY: 'auto',
        boxSizing: 'border-box',
        background: 'var(--bg)',
      }}
    >
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          maxWidth: 320,
          margin: '0 auto',
          width: '100%',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{ fontSize: 28, fontWeight: "var(--fw-bold)", color: 'var(--text)', marginBottom: 8 }}>
            SupplyKit
          </div>
          <div style={{ fontSize: 'var(--font-md)', color: 'var(--muted2)' }}>{t('login.subtitle')}</div>
        </div>
        <div
          style={{
            background: 'var(--card)',
            borderRadius: 'var(--radius-lg)',
            padding: 24,
            boxShadow: 'var(--shadow-card)',
          }}
        >
          <div style={{ fontSize: 'var(--font-lg)', fontWeight: "var(--fw-semibold)", marginBottom: 20 }}>{t('login.title')}</div>
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder={t('login.user_ph')}
            autoFocus
            onKeyDown={e => e.key === 'Enter' && password && doLogin()}
            style={{
              width: '100%',
              padding: '12px 16px',
              fontSize: 'var(--font-lg)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              marginBottom: 12,
              outline: 'none',
              background: 'var(--bg)',
              color: 'var(--text)',
              boxSizing: 'border-box',
            }}
          />
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder={t('login.pwd_ph')}
            onKeyDown={e => e.key === 'Enter' && doLogin()}
            style={{
              width: '100%',
              padding: '12px 16px',
              fontSize: 'var(--font-lg)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              marginBottom: 16,
              outline: 'none',
              background: 'var(--bg)',
              color: 'var(--text)',
              boxSizing: 'border-box',
            }}
          />
          {error && (
            <div
              style={{
                color: 'var(--danger)',
                fontSize: 'var(--font-13)',
                marginBottom: 12,
                textAlign: 'center',
              }}
            >
              {error}
            </div>
          )}
          <button
            onClick={doLogin}
            disabled={loading}
            style={{
              width: '100%',
              padding: '14px',
              fontSize: 'var(--font-lg)',
              fontWeight: "var(--fw-semibold)",
              border: 'none',
              borderRadius: 'var(--radius-lg)',
              cursor: 'pointer',
              background: 'var(--primary)',
              color: '#fff',
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? t('login.loading') : t('login.title')}
          </button>
          <div
            style={{
              marginTop: 16,
              padding: '10px 14px',
              background: 'var(--bg)',
              borderRadius: 'var(--radius-md)',
              fontSize: 'var(--font-sm)',
              color: 'var(--muted2)',
              textAlign: 'center',
            }}
          >
            {t('login.guest')}<b>demo</b> / <b>demo123</b>{t('login.guest_hint')}
          </div>
          <div
            style={{
              marginTop: 10,
              padding: '8px 12px',
              fontSize: 'var(--font-10)',
              color: 'var(--muted2)',
              textAlign: 'center',
              lineHeight: 1.5,
              borderTop: '1px solid var(--border)',
            }}
          >
            <IconScale size={12} style={{ verticalAlign: -2 }} />{' '}
            {t('login.disclaimer')}
          </div>
        </div>
      </div>
    </div>
  )
}
