import React, { useState, useEffect, useRef } from 'react'
import { apiJson } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../components/Toast'
import { Group, LastRow } from '../components/ListGroup'
import { t } from '../locale'
import { todayCn } from '../utils/date'

const VERSION =
  typeof __APP_VERSION__ !== 'undefined' && __APP_VERSION__ ? __APP_VERSION__ : '2.0.0' // 构建注入(package.json version), 发版改 package.json
const BUILD = todayCn() // 北京时间构建日期(UTC 会在 00:00-08:00 显示昨天)
const API = import.meta.env.VITE_API_BASE_URL || ''

export default function SettingsPage() {
  const toast = useToast()
  const { channel, wsStatus } = useAppStore()
  const [, setDbSize] = useState('')
  // 开发者模式(彩蛋入口): 连续点版本号 6 次开启, localStorage 持久化(iOS/Android 惯例)
  const devTap = useRef(0)
  const [devMode, setDevMode] = useState(() => {
    try {
      return localStorage.getItem('c_dev_mode') === '1'
    } catch {
      return false
    }
  })

  // 告警推送 webhook 配置（全局，存 replenishment_config.webhook_url）
  const [webhookUrl, setWebhookUrl] = useState('')
  const [webhookSaving, setWebhookSaving] = useState(false)
  useEffect(() => {
    ;(async () => {
      try {
        // S3-2 收口
        const d = await apiJson<{ data?: { webhook_url?: string } }>(
          'GET',
          API + '/api/replenishment-config?channel=jd',
        )
        if (d?.data?.webhook_url) setWebhookUrl(d.data.webhook_url)
      } catch {}
    })()
  }, [])
  const saveWebhook = async () => {
    setWebhookSaving(true)
    try {
      // S3-2 收口
      const d = await apiJson<{ ok?: boolean; error?: string }>(
        'PUT',
        API + '/api/replenishment-config?channel=jd',
        {
          data: { webhook_url: webhookUrl.trim() },
        },
      )
      if (d.ok)
        toast.success(webhookUrl.trim() ? t('set.webhook_saved_on') : t('set.webhook_saved_off'))
      else toast.error(t('set.save_failed', { msg: d.error || '' }))
    } catch (e) {
      toast.error(t('set.save_failed', { msg: e.message }))
    }
    setWebhookSaving(false)
  }

  return (
    <>
      <div className="form-page">
        <Group title={t('settings.actions')}>
          <LastRow
            label={t('settings.recycle_bin')}
            sub={t('set.recycle_sub')}
            onClick={() => {
              try {
                window.__setPage && window.__setPage('recycle')
              } catch (e) {}
            }}
          />
          <LastRow
            label={t('set.channels')}
            sub={t('set.channels_sub')}
            onClick={() => {
              try {
                window.__setPage && window.__setPage('channels')
              } catch (e) {}
            }}
          />
          {devMode && (
            <LastRow
              label={t('set.devmode')}
              sub={t('set.devmode_sub')}
              onClick={() => {
                try {
                  window.__setPage && window.__setPage('devmode')
                } catch (e) {}
              }}
            />
          )}
        </Group>

        <Group title={t('settings.system')}>
          <LastRow
            label={t('set.version')}
            value={`v${VERSION}`}
            onClick={() => {
              devTap.current += 1
              if (devTap.current >= 6) {
                try {
                  localStorage.setItem('c_dev_mode', '1')
                } catch {}
                setDevMode(true)
                toast.success(t('set.devmode_on'))
              } else if (devTap.current >= 4) {
                toast.info(t('set.devmode_left', { n: 6 - devTap.current }))
              }
            }}
          />
        </Group>

        <Group title={t('set.interface')}>
          <LastRow
            label={t('settings.reset_welcome')}
            sub={t('set.reset_welcome_sub')}
            onClick={() => {
              try {
                localStorage.removeItem('c_welcome_seen')
              } catch {}
              toast.success(t('set.welcome_reset'))
            }}
          />
        </Group>

        <Group title={t('set.alert_push')}>
          <div style={{ padding: 14 }}>
            <div
              style={{
                fontSize: 'var(--font-13)',
                fontWeight: 'var(--fw-semibold)',
                marginBottom: 4,
              }}
            >
              {t('set.webhook')}
            </div>
            <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
              {t('set.webhook_hint')}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={webhookUrl}
                onChange={e => setWebhookUrl(e.target.value)}
                placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
                style={{
                  flex: 1,
                  fontSize: 'var(--font-md)',
                  padding: '10px 12px',
                  borderRadius: 'var(--radius-full)',
                  border: '1px solid var(--border)',
                  background: 'var(--card)',
                  outline: 'none',
                  minWidth: 0,
                }}
              />
              <button
                onClick={saveWebhook}
                disabled={webhookSaving}
                className="btn btn-primary"
                style={{
                  flexShrink: 0,
                  minHeight: 40,
                  padding: '0 18px',
                  fontSize: 'var(--font-md)',
                }}
              >
                {webhookSaving ? t('set.saving') : t('common.save')}
              </button>
            </div>
          </div>
        </Group>

        <div
          style={{
            textAlign: 'center',
            marginTop: 24,
            fontSize: 'var(--font-sm)',
            color: 'var(--muted2)',
          }}
        >
          {t('set.footer')}
        </div>
      </div>
    </>
  )
}
