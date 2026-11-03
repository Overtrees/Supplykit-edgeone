import type { Dispatch, SetStateAction } from 'react'
import { IconSave, IconLoading } from '../../components/Icons'
import { api } from '../../api/client'
import { useToast } from '../../components/Toast'
import { IS } from './shared'

interface Props {
  channel: string
  slowCats: any[]
  setSlowCats: Dispatch<SetStateAction<any[]>>
  transitDays: any
  setTransitDays: Dispatch<SetStateAction<any>>
  fundThreshold: any
  setFundThreshold: Dispatch<SetStateAction<any>>
  saving: boolean
  setSaving: Dispatch<SetStateAction<boolean>>
  saveErr: any
}

export default function RulesSlowPanel(props: Props) {
  const {
    channel: globalChannel,
    slowCats,
    setSlowCats,
    transitDays,
    setTransitDays,
    fundThreshold,
    setFundThreshold,
    saving,
    setSaving,
    saveErr,
  } = props
  const toast = useToast()
  return (
    <div>
      <div className="small muted" style={{ marginBottom: 10, fontSize: 'var(--font-sm)' }}>
        按品类分组自定义滞销判定：每个品类设「滞销线(天)」和「临期线(月)」，品类名单匹配商品的分类字段
      </div>
      {slowCats.map((s, i) => (
        <div
          key={s.key || i}
          style={{
            padding: '10px 14px',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-lg)',
            marginBottom: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <input
              value={s.name}
              onChange={e =>
                setSlowCats(p => p.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))
              }
              placeholder="品类名(如 食品)"
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
                setSlowCats(p =>
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
                  <circle
                    cx="9"
                    cy="9"
                    r="8"
                    fill="none"
                    stroke="var(--border)"
                    strokeWidth="1.5"
                  />
                )}
              </svg>
            </label>
            <span
              onClick={() => setSlowCats(p => p.filter((_, j) => j !== i))}
              className="clickable"
              style={{
                color: 'var(--danger)',
                fontSize: 'var(--font-lg)',
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              ×
            </span>
          </div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <label style={{ fontSize: 'var(--font-sm)', flex: 1, minWidth: 80 }}>
              滞销线(天)
              <input
                type="number"
                value={s.slow_days}
                onChange={e =>
                  setSlowCats(p =>
                    p.map((x, j) =>
                      j === i ? { ...x, slow_days: parseInt(e.target.value) || 30 } : x,
                    ),
                  )
                }
                style={{ ...IS, marginTop: 2 }}
              />
            </label>
            <label style={{ fontSize: 'var(--font-sm)', flex: 1, minWidth: 80 }}>
              观察线(天) <span style={{ color: 'var(--muted2)' }}>留空自动</span>
              <input
                type="number"
                value={s.observe_days ?? ''}
                placeholder={String(Math.max(Math.floor(parseInt(s.slow_days || 30) / 2), 15))}
                onChange={e =>
                  setSlowCats(p =>
                    p.map((x, j) =>
                      j === i
                        ? {
                            ...x,
                            observe_days:
                              e.target.value === '' ? '' : parseInt(e.target.value) || '',
                          }
                        : x,
                    ),
                  )
                }
                style={{ ...IS, marginTop: 2 }}
              />
            </label>
            <label style={{ fontSize: 'var(--font-sm)', flex: 1, minWidth: 80 }}>
              临期线(月)
              <input
                type="number"
                value={s.shelf_months}
                onChange={e =>
                  setSlowCats(p =>
                    p.map((x, j) =>
                      j === i ? { ...x, shelf_months: parseInt(e.target.value) || 3 } : x,
                    ),
                  )
                }
                style={{ ...IS, marginTop: 2 }}
              />
            </label>
          </div>
          <label style={{ fontSize: 'var(--font-sm)', display: 'block' }}>
            品类名单（逗号分隔，匹配商品"分类"字段）
            <input
              value={s.cats || ''}
              onChange={e =>
                setSlowCats(p => p.map((x, j) => (j === i ? { ...x, cats: e.target.value } : x)))
              }
              placeholder="酱油,薯片,糖果..."
              style={{ ...IS, marginTop: 2 }}
            />
          </label>
        </div>
      ))}
      <div style={{ marginTop: 16 }}>
        <label
          style={{
            fontSize: 'var(--font-13)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          物流在途(天)
          <input
            value={transitDays}
            onChange={e => setTransitDays(e.target.value)}
            style={{ ...IS, width: 80, fontSize: 'var(--font-md)', textAlign: 'center' }}
          />
          <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            库存出库到客户/入仓的运输天数，默认 3（用于效期预警：已消耗 + 在途 &gt; 1/3 标临近）
          </span>
        </label>
        <label
          style={{
            fontSize: 'var(--font-13)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginTop: 8,
          }}
        >
          资金占用线(¥)
          <input
            value={fundThreshold}
            onChange={e => setFundThreshold(e.target.value)}
            style={{ ...IS, width: 80, fontSize: 'var(--font-md)', textAlign: 'center' }}
          />
          <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            滞销品资金占用(库存×单价)超此线 → 升级「处置」等级(默认 10000)
          </span>
        </label>
      </div>
      <div style={{ marginTop: 12 }}>
        <button
          onClick={() =>
            setSlowCats(p => [
              ...p,
              {
                key: 'new' + Date.now(),
                name: '新品类',
                slow_days: 30,
                observe_days: '',
                shelf_months: 3,
                cats: '',
                enabled: true,
              },
            ])
          }
          className="btn btn-ghost clickable"
          style={{
            fontSize: 'var(--font-13)',
            padding: '8px 16px',
            width: '100%',
            minHeight: 40,
          }}
        >
          + 添加品类
        </button>
      </div>
      <div style={{ marginTop: 12 }}>
        <button
          disabled={saving}
          onClick={async () => {
            setSaving(true)
            const ch = globalChannel
            try {
              const r = await api.put('/api/replenishment-config/slow-cats?channel=' + ch, {
                items: slowCats,
              })
              await api.put('/api/replenishment-config?channel=' + ch, {
                transit_days: transitDays || '',
                slow_fund_threshold: fundThreshold || '',
              })
              toast.success('已保存')
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
              <IconLoading size={14} /> 保存中...
            </>
          ) : (
            <>
              <IconSave size={14} /> 保存
            </>
          )}
        </button>
      </div>
    </div>
  )
}
