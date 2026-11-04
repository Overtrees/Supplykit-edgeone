import type { Dispatch, SetStateAction } from 'react'
import { IconFactory, IconLoading, IconPackage, IconSave } from '../../components/Icons'
import { api } from '../../api/client'
import { useToast } from '../../components/Toast'
import { IS } from './shared'
import { t } from '../../locale'

interface Props {
  channel: string
  isBBCC: boolean
  cParams: any
  bParams: any
  paramFields: any[]
  cfg: any
  setCfg: Dispatch<SetStateAction<any>>
  saving: boolean
  setSaving: Dispatch<SetStateAction<boolean>>
  saveErr: any
}

export default function RulesReplenishPanel(props: Props) {
  const {
    channel: globalChannel,
    isBBCC,
    cParams,
    bParams,
    paramFields,
    cfg,
    setCfg,
    saving,
    setSaving,
    saveErr,
  } = props
  const toast = useToast()
  return (
    <div>
      {isBBCC ? (
        <>
          <div
            className="section-title"
            style={{
              fontSize: 'var(--font-md)',
              marginBottom: 10,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <IconPackage size={14} /> {t('rpn.c_wh')}
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 14,
              marginBottom: 20,
            }}
          >
            {cParams.map(({ k, l, h }) => (
              <label key={k} style={{ fontSize: 'var(--font-13)' }}>
                {t(l)}
                <input
                  value={String(cfg[k] || '')}
                  onChange={e => setCfg(p => ({ ...p, [k]: e.target.value }))}
                  style={IS}
                />
                {h && (
                  <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginTop: 2 }}>
                    {t(h)}
                  </div>
                )}
              </label>
            ))}
          </div>
          <div
            className="section-title"
            style={{
              fontSize: 'var(--font-md)',
              marginBottom: 10,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            <IconFactory size={14} /> {t('rpn.b_wh')}
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 1fr 1fr',
              gap: 14,
              marginBottom: 20,
            }}
          >
            {bParams.map(({ k, l }) => (
              <label key={k} style={{ fontSize: 'var(--font-13)' }}>
                {t(l)}
                <input
                  value={String(cfg[k] || '')}
                  onChange={e => setCfg(p => ({ ...p, [k]: e.target.value }))}
                  style={IS}
                />
              </label>
            ))}
          </div>
        </>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr',
            gap: 14,
            marginBottom: 20,
          }}
        >
          {paramFields.map(({ k, l }) => (
            <label key={k} style={{ fontSize: 'var(--font-13)' }}>
              {t(l)}
              <input
                value={String(cfg[k] || '')}
                onChange={e => setCfg(p => ({ ...p, [k]: e.target.value }))}
                style={IS}
              />
            </label>
          ))}
        </div>
      )}
      <button
        disabled={saving}
        onClick={async () => {
          setSaving(true)
          const m = cfg.replenishment_mode || 'bbcc'
          const ch = globalChannel
          try {
            const toSave = {}
            ;[...cParams, ...bParams, ...paramFields].forEach(f => {
              if (cfg[f.k] !== undefined) toSave[f.k] = cfg[f.k]
            })
            await api.put('/api/replenishment-config?mode=' + m + '&channel=' + ch, toSave)
            setCfg(p => ({ ...p, ...toSave }))
            toast.success(t('rp.saved'))
            window.dispatchEvent(new Event('rules-changed'))
          } catch (e) {
            saveErr(e)
          }
          setSaving(false)
        }}
        className="btn btn-primary"
        style={{
          width: '100%',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 4,
          justifyContent: 'center',
          minHeight: 42,
        }}
      >
        {saving ? (
          <>
            <IconLoading size={14} /> {t('rfm.saving')}
          </>
        ) : (
          <>
            <IconSave size={14} /> {t('common.save')}
          </>
        )}
      </button>
    </div>
  )
}
