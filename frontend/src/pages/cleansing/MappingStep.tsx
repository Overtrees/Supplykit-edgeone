import { IconCheck, IconLightning } from '../../components/Icons'
import { ALIAS, PRESET_REQUIRED, SYS_FIELDS, TARGET_FIELDS } from './fields'

interface Props {
  cols: any[]
  mp: Record<string, any>
  setMp: (fn: (p: Record<string, any>) => Record<string, any>) => void
  tt: string
  /** 自定义字段(页面 state): 下拉末尾追加, 标签兜底 */
  cf: any[]
  onBack: () => void
  onPreview: () => void
  onQuick: () => void
  /** 记住列名→字段(页面写 localStorage 别名表) */
  saveAlias: (col: string, target: string) => void
}

/** 第 2 步列映射 + 底部动作。从 CleansingPage 外提: 映射状态仍留页面(受控)。
 *  口径: 未映射列导入时丢弃; 必选列未映射只警示不拦(硬拦在 preview/doExecute 两处)。 */
export default function MappingStep({
  cols,
  mp,
  setMp,
  tt,
  cf,
  onBack,
  onPreview,
  onQuick,
  saveAlias,
}: Props) {
  return (
    <>
      <div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            marginTop: 2,
            marginBottom: 10,
          }}
        >
          <span style={{ fontSize: 'var(--font-md)', fontWeight: 700 }}>列映射</span>
          <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            选择文件列对应的目标字段 · 未映射列导入时丢弃
          </span>
        </div>
        {cols.map(c => {
          const matched = ALIAS[c.name]
          // 2026-11-01 智能建议必须限定在当前导入类型内: 原回退全局 SYS_FIELDS
          //   → 订单文件里的"生产日期/到期日期"列会被提示"建议映射 prod_date/exp_date",
          //   但订单可选目标(TARGET_TARGETS.order)里没有这两项, 下拉根本选不到,
          //   自动映射也已按白名单过滤(见上) → 这是一条永远无法采纳的建议,
          //   只会让人误以为订单也要填效期。订单是销售事实, 不承载批次/效期。
          const sf = (TARGET_FIELDS[tt] || []).find(x => x.t === matched)
          // 统计每个目标被几个来源列映射
          const targetCounts = {}
          for (const [, cfg] of Object.entries(mp)) {
            if (cfg && cfg.target) targetCounts[cfg.target] = (targetCounts[cfg.target] || 0) + 1
          }
          const currentTarget = mp[c.name]?.target
          const isShared = currentTarget && targetCounts[currentTarget] > 1
          return (
            <div key={c.name} className="map-row">
              <div className="map-head">
                <span className="map-name">{c.name}</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  {mp[c.name]?.target ? (
                    <span
                      className="pill success"
                      style={{
                        fontSize: 'var(--font-9)',
                        padding: '1px 6px',
                        minHeight: 'auto',
                        lineHeight: '16px',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 3,
                      }}
                    >
                      <IconCheck size={11} style={{ verticalAlign: -1 }} /> 已映射
                    </span>
                  ) : (
                    <span
                      className="pill warning"
                      style={{
                        fontSize: 'var(--font-9)',
                        padding: '1px 6px',
                        minHeight: 'auto',
                        lineHeight: '16px',
                      }}
                    >
                      {/* 2026-11-01 建议目标是必选字段 → pill 文案升级为"必选"(复用同一 pill warning) */}
                      {matched && (PRESET_REQUIRED[tt] || []).includes(matched)
                        ? '未映射 · 必选'
                        : '未映射 · 导入时丢弃'}
                    </span>
                  )}
                </span>
                {mp[c.name]?.target ? (
                  (() => {
                    // 2026-11-01 显示文案优先取该导入类型的字段集(TARGET_FIELDS[tt]),
                    //   原只看 SYS_FIELDS → 类型专属新列(如 expiry_managed)已映射却不显示目标名
                    const sf2 =
                      (TARGET_FIELDS[tt] || []).find(x => x.t === mp[c.name].target) ||
                      SYS_FIELDS.find(x => x.t === mp[c.name].target) ||
                      cf.find(x => x.t === mp[c.name].target)
                    return sf2 ? (
                      <span className="map-sub">
                        → {sf2.l} ({sf2.t})
                      </span>
                    ) : null
                  })()
                ) : matched && sf ? (
                  <span className="map-sub">
                    智能建议 → {sf.l} ({sf.t})
                  </span>
                ) : null}
              </div>
              <div className="map-arrow">→</div>
              <div className="map-controls">
                <select
                  value={mp[c.name]?.target || ''}
                  onChange={e => {
                    const v = e.target.value
                    setMp(p => ({ ...p, [c.name]: { target: v, type: 'string' } }))
                    saveAlias(c.name, v)
                  }}
                  className="tpl-field"
                  style={{ flex: 1, minWidth: 0, fontSize: 'var(--font-13)' }}
                >
                  <option value="">-- 不映射 --</option>
                  {(TARGET_FIELDS[tt] || SYS_FIELDS).map(f => (
                    /* 2026-11-01 必选字段加"(必)"后缀——与下载模板表头同源口径(必/选) */
                    <option key={f.t} value={f.t}>
                      {f.l}
                      {(PRESET_REQUIRED[tt] || []).includes(f.t) ? '(必)' : ''}
                    </option>
                  ))}
                  {cf
                    .filter(f => f.t && f.l)
                    .map(f => (
                      <option key={f.t} value={f.t}>
                        {f.l}
                      </option>
                    ))}
                </select>
                <div className="map-tag">
                  {isShared ? (
                    <span
                      className="pill warning"
                      style={{
                        fontSize: 'var(--font-9)',
                        padding: '1px 6px',
                        minHeight: 'auto',
                        lineHeight: '16px',
                      }}
                    >
                      ×{targetCounts[currentTarget]}
                    </span>
                  ) : null}
                </div>
              </div>
            </div>
          )
        })}
      </div>{' '}
      <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
        <button
          onClick={() => onBack()}
          className="clickable"
          style={{
            flexShrink: 0,
            width: 88,
            padding: '11px 0',
            fontSize: 'var(--font-md)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-full)',
            background: 'var(--card)',
            cursor: 'pointer',
            fontWeight: 600,
            minHeight: 42,
            whiteSpace: 'nowrap',
          }}
        >
          ← 返回
        </button>
        <button
          onClick={() => onPreview()}
          className="clickable"
          style={{
            flex: 1,
            padding: '11px 12px',
            fontSize: 'var(--font-md)',
            border: 'none',
            borderRadius: 'var(--radius-full)',
            background: 'var(--primary)',
            color: '#fff',
            cursor: 'pointer',
            fontWeight: 600,
            minHeight: 42,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            justifyContent: 'center',
            whiteSpace: 'nowrap',
          }}
        >
          下一步 · 预览
        </button>
        <button
          onClick={() => onQuick()}
          className="clickable"
          style={{
            flex: 1,
            padding: '11px 12px',
            fontSize: 'var(--font-md)',
            border: 'none',
            borderRadius: 'var(--radius-full)',
            background: 'var(--success)',
            color: '#fff',
            cursor: 'pointer',
            fontWeight: 600,
            minHeight: 42,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            justifyContent: 'center',
            whiteSpace: 'nowrap',
          }}
        >
          <IconLightning size={14} /> 一键执行
        </button>
      </div>
    </>
  )
}
