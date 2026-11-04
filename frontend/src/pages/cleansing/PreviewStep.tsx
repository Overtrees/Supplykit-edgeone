import { IconWarning } from '../../components/Icons'
import { TARGET_FIELDS, SYS_FIELDS } from './fields'
import { fieldLabel } from './fieldNames'
import { t } from '../../locale'

interface Props {
  pv: any
  mp: Record<string, any>
  cols: any[]
  tr: number
  tt: string
  bs: string
  /** 自定义字段(页面 state, 预览里用作兜底标签) */
  cf: any[]
  onBack: () => void
  onExecute: () => void
}

/** 第 3 步清洗预览。从 CleansingPage 外提: 只读 pv/mp 渲染, 两个出口动作上抛。
 *  口径: 缺 SKU / 重复 SKU 在预览层先标红, 写入前 doExecute 仍会硬拦。 */
export default function PreviewStep({ pv, mp, cols, tr, tt, bs, cf, onBack, onExecute }: Props) {
  return (
    <div className="clean-preview-page">
      {/* 2026-11-03 标题与映射页同构(flex 居中 + 14/11 + gap 6 + mb 10),
          替换原全局 .section-title(15px/700 + 12px baseline 混排), 两步标题口径统一。
          不带映射页源码里的 marginTop:2 —— 那在块流里与步骤条 margin 折叠(实际 14),
          这里父容器是 flex 不折叠, 写 2 会让间距变 16。
          主标题固定「清洗预览」, 行数/列数全放副标题: 原主标题带「前 N 行」+ 副标题
          「仅展示前 50 行」重复; 且 390 宽 + 大文件(5000 行/13 列)时两段总宽 360px
          超容器 326px → 副标题换行。合并后单行放得下(约 70+6+230)。 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginBottom: 10,
          flexWrap: 'wrap',
        }}
      >
        <span style={{ fontSize: 'var(--font-md)', fontWeight: "var(--fw-bold)" }}>{t('cw.preview_title')}</span>
        {(() => {
          const n = Object.keys(pv.preview[0] || {}).filter(k => k !== '_source').length
          const parts: string[] = [t('cw.pv_total', { n: pv.total })]
          if (pv.total > 50) parts.push(t('cw.pv_shown_50'))
          if (n > 0) parts.push(t('cw.pv_cols', { n }))
          return (
            <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
              {parts.join(' · ')}
            </span>
          )
        })()}
      </div>
      {(() => {
        // 脏数据预检: 缺SKU/重复SKU 统计(导入前发现)
        const skuCol = Object.entries(mp).find(([, cfg]) => cfg && cfg.target === 'sku')
        if (!skuCol) return null
        const skuVals = (pv.preview || []).map(r => String(r[skuCol[1].target] || ''))
        const seen = new Map()
        const dupSkus = new Set()
        skuVals.forEach(v => {
          if (v === '') return
          seen.set(v, (seen.get(v) || 0) + 1)
          if (seen.get(v) > 1) dupSkus.add(v)
        })
        const missing = skuVals.filter(v => v === '').length
        if (missing === 0 && dupSkus.size === 0) return null
        return (
          <div
            style={{
              fontSize: 'var(--font-xs)',
              color: 'var(--danger)',
              background: 'rgba(239,68,68,0.06)',
              border: '1px solid rgba(239,68,68,0.2)',
              borderRadius: 'var(--radius-card)',
              padding: '9px 12px',
              marginBottom: 10,
              display: 'flex',
              gap: 6,
              alignItems: 'flex-start',
              lineHeight: 1.5,
            }}
          >
            <IconWarning size={13} style={{ flexShrink: 0, marginTop: 1 }} />
            <span>
              {t('cw.pv_issue_pre')}<b>{missing}</b>{t('cw.pv_issue_sku')}<b>{dupSkus.size}</b>{t('cw.pv_issue_dup')}
              {t('cw.pv_issue_suf')}
            </span>
          </div>
        )
      })()}
      {(() => {
        if (!pv.preview?.length) return null
        // 按来源列显示：只显示有映射的列（target 非空），unmap 的列直接不出现
        const mappedSources = Object.entries(mp).filter(([, v]) => v && v.target)
        const cols = mappedSources.map(([src, cfg]) => {
          const sf =
            (TARGET_FIELDS[tt] || SYS_FIELDS).find(x => x.t === cfg.target) ||
            cf.find(x => x.t === cfg.target)
          return { src, target: cfg.target, label: sf ? sf.l : cfg.target }
        })
        // 脏数据预检: 重复 SKU 集合(表体行标红用)
        const _skuCol = cols.find(c => c.target === 'sku')
        const _skuVals = (pv.preview || []).map(r =>
          _skuCol ? String(r[_skuCol.target] || '') : '',
        )
        const _seen = new Map()
        const dupSkus = new Set()
        _skuVals.forEach(v => {
          if (!v) return
          _seen.set(v, (_seen.get(v) || 0) + 1)
          if (_seen.get(v) > 1) dupSkus.add(v)
        })
        if (cols.length === 0)
          return (
            <div className="small muted" style={{ padding: 20, textAlign: 'center' }}>
              {t('cw.pv_no_mapping')}
            </div>
          )
        return (
          <div className="clean-preview-body" style={{ marginBottom: 12 }}>
            <div
              className="clean-preview-hint"
              style={{ fontSize: 'var(--font-xs)', color: 'var(--muted2)', marginBottom: 4 }}
            >
              {t('cw.pv_swipe_hint', { n: cols.length })}
            </div>
            {/* 2026-10-28 安全区根治: 50 行表格曾把页面撑高 → iOS 地址栏联动 → 顶部露出 chrome 纯色条;
                    改表格内部限高滚动(避免整页高度突变), 纵向滚 table / 横向滚外层 */}
            <div
              className="clean-preview-scroll"
              style={{
                overflowX: 'auto',
                /* 2026-11-01 高度不再用 calc(100dvh - 340px)/clamp: 改由 .clean-preview-page
                       的 flex 分配(表格吃剩余空间, 底部按钮常驻)。dvh 随地址栏变化, iOS 首帧还可能
                       算出 0/负值 → 竖屏首屏不绘制、横屏与 min-height 打架撑出视口 → 顶部遮挡。 */
                minHeight: 0,
                overflowY: 'auto',
                WebkitOverflowScrolling:
                  'touch' /* 恢复: 隐藏滚动条+去惯性曾致 iOS 首帧不绘制内容 */,
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
              }}
            >
              <table>
                <thead>
                  <tr style={{ position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                    {cols.map(col => (
                      <th
                        key={col.src}
                        style={{ minWidth: 80, whiteSpace: 'nowrap', verticalAlign: 'top' }}
                      >
                        {fieldLabel(col.label)}
                        <div
                          style={{
                            fontSize: 10,
                            fontWeight: "var(--fw-regular)",
                            color: 'var(--muted2)',
                            marginTop: 1,
                          }}
                        >
                          ← {col.src}
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pv.preview.map((r, i) => {
                    // 脏数据预检: 关键字段(sku)缺失/重复 → 行标红(导入前发现, 非执行后才知道)
                    const skuCol = cols.find(c => c.target === 'sku')
                    const skuVal = skuCol ? String(r[skuCol.target] || '') : ''
                    const issue =
                skuVal === ''
                  ? t('cw.issue_no_sku')
                  : dupSkus.has(skuVal)
                    ? t('cw.issue_dup_sku')
                    : ''
                    return (
                      <tr
                        key={i}
                        style={issue ? { background: 'rgba(239,68,68,0.06)' } : undefined}
                      >
                        {cols.map(col => (
                          <td
                            key={col.src}
                            style={{
                              minWidth: 80,
                              whiteSpace: 'nowrap',
                              maxWidth: 200,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              color: issue && col.target === 'sku' ? 'var(--danger)' : undefined,
                            }}
                          >
                            {String(r[col.target] || '')}
                            {issue && col.target === 'sku' ? (
                              <span
                                style={{
                                  fontSize: 'var(--font-9)',
                                  color: 'var(--danger)',
                                  marginLeft: 4,
                                }}
                              >
                                <IconWarning size={11} style={{ verticalAlign: -2 }} />
                                {issue}
                              </span>
                            ) : null}
                          </td>
                        ))}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )
      })()}
      {/* 2026-10-27: 底部操作对齐其余步骤(左返回固定宽 + 右主操作 primary——写入非'成功'语义) */}
      <div className="clean-preview-actions" style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button
          className="clickable"
          onClick={() => {
            onBack()
          }}
          disabled={!!bs}
          style={{
            flex: '0 0 96px',
            minHeight: 44,
            borderRadius: 'var(--radius-full)',
            border: '1px solid var(--border)',
            background: 'var(--card)',
            color: 'var(--text)',
            fontSize: 'var(--font-md)',
            fontWeight: "var(--fw-semibold)",
            cursor: 'pointer',
          }}
        >
          {t('cw.back')}
        </button>
        <button
          className="clickable"
          onClick={() => onExecute()}
          disabled={!!bs}
          style={{
            flex: 1,
            minHeight: 44,
            borderRadius: 'var(--radius-full)',
            border: 'none',
            background: 'var(--primary)',
            color: '#fff',
            fontSize: 'var(--font-md)',
            fontWeight: "var(--fw-semibold)",
            cursor: 'pointer',
            opacity: bs ? 0.6 : 1,
          }}
        >
          {t('cw.confirm_write', { n: pv.total })}
        </button>
      </div>
    </div>
  )
}
