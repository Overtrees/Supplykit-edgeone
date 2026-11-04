import { t } from '../locale'
import { apiJson } from '../api/client'
import { notify } from '../api/notify'

const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

/**
 * 首次使用欢迎页 —— 自 App.tsx 外提(纯展示)。
 * 「开始使用」额外触发种子数据填充(原逻辑原样随迁); 两个按钮结束时均回调 close()。
 */
export default function Welcome({ close }: { close: () => void }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100svh',
        padding:
          'calc(env(safe-area-inset-top, 0px) + 40px) 24px calc(24px + env(safe-area-inset-bottom, 20px))',
        overflowY: 'auto',
        boxSizing: 'border-box',
      }}
    >
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          maxWidth: 360,
          margin: '0 auto',
          width: '100%',
        }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div
            style={{
              fontSize: 32,
              fontWeight: "var(--fw-bold)",
              color: 'var(--text)',
              marginBottom: 8,
              letterSpacing: '-0.5px',
            }}
          >
            {t('welcome.title')}
          </div>
          <div style={{ fontSize: 'var(--font-15)', color: 'var(--muted2)', lineHeight: 1.5 }}>
            {t('welcome.tagline1')}
            <br />
            {t('welcome.tagline2')}
          </div>
        </div>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 14,
            marginBottom: 32,
          }}
        >
          {[
            {
              svg: 'M3 12h4l2-9 4 18 2-9h4',
              title: t('welcome.dash'),
              desc: t('welcome.dash_desc'),
              page: 'dash',
            },
            {
              svg: 'M9 18h6M10 22h4M15.09 14c.6-.77 1.05-1.6 1.32-2.5A5.4 5.4 0 0 0 12 6a5.4 5.4 0 0 0-4.41 5.5c.27.9.72 1.73 1.32 2.5M9 18c0-1.5.5-2.9 1.5-4h3c1 1.1 1.5 2.5 1.5 4',
              title: t('welcome.insights'),
              desc: t('welcome.insights_desc'),
              page: 'insights',
            },
            {
              svg: 'M20 4 8 16M16 20 4 8M14 6a3 3 0 0 0-6 0v5h6V6ZM6 14c0 2 1.5 4 3 5M14 14c0 2-1.5 4-3 5M4 8h16',
              title: t('welcome.cleansing'),
              desc: t('welcome.cleansing_desc'),
              page: 'cleansing',
            },
            {
              svg: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42',
              title: t('welcome.rules'),
              desc: t('welcome.rules_desc'),
              page: 'rules',
            },
          ].map(function (card) {
            return (
              <div
                key={card.page}
                className="clickable"
                style={{
                  background: 'var(--card)',
                  borderRadius: 'var(--radius-card-sm)',
                  padding: 16,
                  textAlign: 'center',
                  cursor: 'default',
                  border: '1px solid var(--border)',
                }}
              >
                <div style={{ marginBottom: 8 }}>
                  <svg
                    width="30"
                    height="30"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--primary)"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d={card.svg} />
                  </svg>
                </div>
                <div
                  style={{
                    fontSize: 'var(--font-15)',
                    fontWeight: "var(--fw-semibold)",
                    color: 'var(--text)',
                    marginBottom: 3,
                  }}
                >
                  {card.title}
                </div>
                <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)' }}>
                  {card.desc}
                </div>
              </div>
            )
          })}
        </div>
        <button
          onClick={async function () {
            try {
              localStorage.setItem('c_welcome_seen', '1')
            } catch {}
            close()
            try {
              // S3-2 收口: 填充是重操作, 超时 90s
              const d = await apiJson('POST', API + '/api/seed/fill', { timeout: 90000 })
              if (d.ok) {
                if (d.data?.requires_reset) {
                  notify.error(t('welcome.seed_exists'))
                  return
                }
                const taskId = d.data?.task_id
                if (taskId) {
                  try {
                    localStorage.setItem('c_seed_task', taskId)
                  } catch {}
                  notify.success(t('welcome.seed_running'))
                }
              } else notify.error(t('welcome.seed_failed', { msg: d.error || '' }))
            } catch (e) {}
          }}
          className="btn btn-primary"
          style={{
            width: '100%',
            padding: '14px',
            fontSize: 'var(--font-lg)',
            fontWeight: "var(--fw-semibold)",
            marginBottom: 10,
          }}
        >
          {t('welcome.start')}
        </button>
        <button
          onClick={function () {
            localStorage.setItem('c_welcome_seen', '1')
            close()
          }}
          className="btn btn-ghost clickable"
          style={{
            width: '100%',
            padding: '10px',
            fontSize: 'var(--font-md)',
            color: 'var(--muted2)',
          }}
        >
          {t('welcome.skip')}
        </button>
      </div>
    </div>
  )
}
