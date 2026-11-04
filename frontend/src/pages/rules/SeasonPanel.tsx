import type { Dispatch, SetStateAction } from 'react'
import { IconLoading, IconSave, IconTag } from '../../components/Icons'
import { api } from '../../api/client'
import { useToast } from '../../components/Toast'
import { t } from '../../locale'

interface Props {
  channel: string
  seasons: any[]
  setSeasons: Dispatch<SetStateAction<any[]>>
  seasonsSaving: boolean
  setSeasonsSaving: Dispatch<SetStateAction<boolean>>
  cfg: any
  saveErr: any
  loadCfg: (mode?: any, ch?: any) => Promise<any>
}

export default function RulesSeasonPanel(props: Props) {
  const {
    channel: globalChannel,
    seasons,
    setSeasons,
    seasonsSaving,
    setSeasonsSaving,
    cfg,
    saveErr,
    loadCfg,
  } = props
  const toast = useToast()
  return (
    <>
      <div
        className="section-title"
        style={{
          marginTop: 16,
          marginBottom: 8,
          display: 'flex',
          alignItems: 'center',
          gap: 4,
        }}
      >
        <IconTag size={14} /> {t('sp.title')}
      </div>
      {seasons.map((s, i) => (
        <div
          key={s.key || i}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '10px 14px',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
            marginBottom: 8,
          }}
        >
          <input
            value={s.name}
            onChange={e =>
              setSeasons(p => p.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))
            }
            placeholder={t('sp.name_ph')}
            style={{
              flex: 1,
              minWidth: 80,
              fontSize: 'var(--font-lg)',
              padding: '6px 10px',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              outline: 'none',
            }}
          />
          <span className="small muted">×</span>
          <input
            type="number"
            value={s.factor}
            onChange={e =>
              setSeasons(p =>
                p.map((x, j) => (j === i ? { ...x, factor: parseFloat(e.target.value) || 1 } : x)),
              )
            }
            step="0.1"
            min="1"
            max="3"
            style={{
              width: 70,
              fontSize: 'var(--font-lg)',
              padding: '6px 10px',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-lg)',
              outline: 'none',
            }}
          />
          <span className="small muted">{t('sp.unit_times')}</span>
          <label
            style={{
              fontSize: 'var(--font-sm)',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              cursor: 'pointer',
              flexShrink: 0,
            }}
            onClick={() =>
              setSeasons(p =>
                p.map((x, j) => (j === i ? { ...x, enabled: !(x.enabled !== false) } : x)),
              )
            }
          >
            <svg width="20" height="20" viewBox="0 0 18 18" style={{ flexShrink: 0 }}>
              {s.enabled !== false ? (
                <>
                  <circle cx="9" cy="9" r="8" fill="var(--primary)" />
                  <path
                    d="M5.5 9.5l2 2 3.5-3.5"
                    stroke="#fff"
                    strokeWidth="2"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </>
              ) : (
                <circle cx="9" cy="9" r="8" fill="none" stroke="var(--border)" strokeWidth="1.5" />
              )}
            </svg>
            {t('sp.enable')}
          </label>
          <button
            onClick={() => setSeasons(p => p.filter((_, j) => j !== i))}
            className="clickable"
            style={{
              fontSize: 'var(--font-13)',
              padding: '6px 14px',
              minHeight: 36,
              borderRadius: 'var(--radius-full)',
              border: 'none',
              background: 'var(--danger)',
              color: '#fff',
              cursor: 'pointer',
              fontWeight: "var(--fw-semibold)",
              flexShrink: 0,
            }}
          >
            {t('common.delete')}
          </button>
        </div>
      ))}
      <button
        onClick={() =>
          setSeasons(p => [...p, { key: 'new', name: t('sp.new_name'), factor: 1.2, enabled: true }])
        }
        className="btn btn-ghost clickable"
        style={{
          fontSize: 'var(--font-13)',
          padding: '8px 16px',
          width: '100%',
          minHeight: 40,
        }}
      >
        {t('sp.add')}
      </button>
      <div style={{ marginTop: 12 }}>
        <button
          disabled={seasonsSaving}
          onClick={async () => {
            setSeasonsSaving(true)
            const m = cfg.replenishment_mode || 'bbcc'
            const ch = globalChannel
            try {
              await api.put('/api/replenishment-config/seasons?mode=' + m + '&channel=' + ch, {
                items: seasons,
              })
              await loadCfg(m, ch)
              toast.success(t('sp.saved'))
              window.dispatchEvent(new Event('rules-changed'))
            } catch (e) {
              saveErr(e)
            }
            setSeasonsSaving(false)
          }}
          className="btn btn-primary"
          style={{
            width: '100%',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            justifyContent: 'center',
            minHeight: 42,
            opacity: seasonsSaving ? 0.6 : 1,
          }}
        >
          {seasonsSaving ? (
            <>
              <IconLoading size={14} /> {t('sp.saving')}
            </>
          ) : (
            <>
              <IconSave size={14} /> {t('sp.save')}
            </>
          )}
        </button>
      </div>
    </>
  )
}
