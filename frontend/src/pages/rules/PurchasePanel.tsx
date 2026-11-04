import type { Dispatch, SetStateAction } from 'react'
import { IconLoading, IconSave } from '../../components/Icons'
import { api } from '../../api/client'
import { useToast } from '../../components/Toast'
import { t } from '../../locale'

interface Props {
  channel: string
  selectedSupplier: any
  setSelectedSupplier: Dispatch<SetStateAction<any>>
  suppliers: any[]
  purchaseFields: any[]
  cfg: any
  setCfg: Dispatch<SetStateAction<any>>
  saving: boolean
  setSaving: Dispatch<SetStateAction<boolean>>
  saveErr: any
}

export default function RulesPurchasePanel(props: Props) {
  const {
    channel: globalChannel,
    selectedSupplier,
    setSelectedSupplier,
    suppliers,
    purchaseFields,
    cfg,
    setCfg,
    saving,
    setSaving,
    saveErr,
  } = props
  const toast = useToast()
  return (
    <div>
      <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)', marginBottom: 8 }}>
        {t('rpn.supplier_section')}
      </div>
      <div className="hammer-btn-row" style={{ marginBottom: 12 }}>
        <select
          value={selectedSupplier}
          onChange={e => {
            setSelectedSupplier(e.target.value)
            try {
              localStorage.setItem('c_supplier_' + globalChannel, e.target.value)
            } catch {}
          }}
          className="hammer-select"
        >
          <option value="">{t('rpn.supplier_all')}</option>
          {suppliers.map(s => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      <div className="hammer-params-grid">
        {purchaseFields.map(({ k, l }) => {
          const isSupKey = k === 'moq' || k === 'purchase_lead_days' || k === 'purchase_safety_days'
          const actualKey = selectedSupplier && isSupKey ? `${k}_${selectedSupplier}` : k
          return (
            <label key={actualKey} style={{ fontSize: 'var(--font-13)' }}>
              {t(l)}
              {selectedSupplier && isSupKey && (
                <span className="small muted">（{selectedSupplier}）</span>
              )}
              <input
                value={String(cfg[actualKey] || '')}
                onChange={e => setCfg(p => ({ ...p, [actualKey]: e.target.value }))}
                className="hammer-input"
              />
            </label>
          )
        })}
      </div>
      <button
        disabled={saving}
        onClick={async () => {
          setSaving(true)
          const ch = globalChannel
          try {
            const toSave = {}
            purchaseFields.forEach(f => {
              const isSupKey =
                f.k === 'moq' || f.k === 'purchase_lead_days' || f.k === 'purchase_safety_days'
              const k = selectedSupplier && isSupKey ? `${f.k}_${selectedSupplier}` : f.k
              if (cfg[k] !== undefined) toSave[k] = cfg[k]
            })
            await api.put('/api/replenishment-config?channel=' + ch, toSave)
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
