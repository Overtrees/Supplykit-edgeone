import { IconCheck, IconScale } from '../../components/Icons'
import ErrorRetry from '../../components/ErrorRetry'
import EmptyState from '../../components/EmptyState'
import { t } from '../../locale'
import { pc, fieldLbl, modeLbl, opLbl, renderTmpl, whLbl } from './labels'

/**
 * 规则列表(全受控) —— 自 RulesPage.tsx 外提(S3-1 续2)。
 * 点卡片进入编辑由 onEdit(rule) 回调页面; 空态新建由 onNew 回调。
 * sevCls/sevLbl 仅本列表使用, 随迁。
 */
export default function RuleList({
  filteredRules,
  rules,
  rulesErr,
  selIds,
  setSelIds,
  prodBatch,
  hammerSearch,
  load,
  globalChannel,
  onEdit,
  onNew,
}: {
  filteredRules: any[]
  rules: any[]
  rulesErr: string
  selIds: (number | string)[]
  setSelIds: (v: any) => void
  prodBatch: boolean
  hammerSearch: string
  load: (ch?: string) => void
  globalChannel: string
  onEdit: (rule: any) => void
  onNew: () => void
}) {
  const sevCls = s => (s === 'error' ? 'danger' : s === 'info' ? 'info' : 'warning')
  const sevLbl = s =>
    s === 'error' ? t('rules.severity_error') : s === 'info' ? t('rules.severity_info') : t('rules.severity_warning')

  return (
    <>
      {filteredRules.map(rule => {
        const condInfo = pc(rule.condition_json || '{}')
        const _whLbl = whLbl(condInfo.warehouse)
        const _modeLbl = modeLbl(rule.mode || '')
        // 2026-11-01 条件文案修正两处:
        //  ① 原固定前缀"当 <仓库> <字段> <op> <值>" —— 自有仓规则的前置是
        //     `inv.warehouse_type == own`(字段级), 拼出来变成"当 全部 仓库类型 等于 own",
        //     语义重复且"全部"与"own"自相矛盾。现在字段本身就是仓储维度时不再加仓前缀。
        //  ② and 子条件原**完全不显示** → 列表里只看得到前置、看不到真正的判据(缺口>阈值);
        //     现在把 and/or 串进文案, 与编辑区一致。
        const _isWhField = condInfo.left === 'inv.warehouse_type'
        const _fmtR = (c: any) =>
          c.rightType === 'pct'
            ? t('rules.pct_of', { field: fieldLbl(c.right), pct: c.pctValue })
            : c.rightType === 'field'
              ? fieldLbl(c.right)
              : c.right
        const condText =
          (condInfo.warehouse && !_isWhField ? t('rules.when_wh', { wh: _whLbl }) : t('rules.when')) +
          `${fieldLbl(condInfo.left)} ${opLbl(condInfo.op)} ${_fmtR(condInfo)}` +
          (condInfo.and
            ? ' ' +
              t('rules.and', {
                cond: `${fieldLbl(condInfo.and.left)} ${opLbl(condInfo.and.op)} ${_fmtR(condInfo.and)}`,
              })
            : '') +
          (Array.isArray(condInfo.or) && condInfo.or.length
            ? ' ' +
              t('rules.or', {
                cond: condInfo.or
                  .map((o: any) => `${fieldLbl(o.left)} ${opLbl(o.op)} ${_fmtR(o)}`)
                  .join(' / '),
              })
            : '')
        return (
          <div
            key={rule.id}
            onClick={() => onEdit(rule)}
            className={'rule-card' + (prodBatch && selIds.includes(rule.id) ? ' is-selected' : '')}
            style={{ cursor: prodBatch ? 'default' : 'pointer' }}
          >
            {prodBatch && (
              <span
                onClick={e => {
                  e.stopPropagation()
                  const ids = selIds
                  setSelIds(
                    ids.includes(rule.id) ? ids.filter(i => i !== rule.id) : [...ids, rule.id],
                  )
                }}
                className="clickable"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  marginBottom: 8,
                }}
              >
                <span className={'rule-check' + (selIds.includes(rule.id) ? ' on' : '')}>
                  {selIds.includes(rule.id) ? <IconCheck size={12} /> : ''}
                </span>
<span style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)' }}>
                  {t('rules.select')}
                </span>
              </span>
            )}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                gap: 10,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="rule-title">
                  {rule.name}
                  <span className={'pill ' + (rule.is_active ? 'success' : 'warning')}>
                    {rule.is_active ? t('rules.active') : t('rules.inactive')}
                  </span>
                  <span className={'pill ' + sevCls(rule.severity)}>{sevLbl(rule.severity)}</span>
                  {rule.mode && <span className="rule-mode">{_modeLbl}</span>}
                </div>
                <div className="rule-cond">
                  <IconScale size={12} style={{ marginTop: 2 }} />
                  <span>{condText}</span>
                </div>
                <div className="rule-desc">
                  {renderTmpl(rule.alert_title) || (
                    <span className="small muted">{t('rules.no_title')}</span>
                  )}
                  {rule.alert_desc ? (
                    <>
                      <span style={{ color: 'var(--muted2)', margin: '0 3px' }}>·</span>
                      {renderTmpl(rule.alert_desc)}
                    </>
                  ) : (
                    ''
                  )}
                </div>
              </div>
              <div
                style={{ display: 'flex', gap: 8, flexShrink: 0, alignItems: 'flex-start' }}
              ></div>
            </div>
          </div>
        )
      })}
      {filteredRules.length === 0 &&
        (rulesErr ? (
          <ErrorRetry error={rulesErr} onRetry={() => load(globalChannel)} />
        ) : (
          <EmptyState
            icon="tag"
            title={
              rules.length === 0
                ? t('rules.empty')
                : t('rules.no_match', { q: hammerSearch })
            }
            desc={rules.length === 0 ? t('rules.empty_desc') : undefined}
            action={
              rules.length === 0 ? (
                <button className="btn btn-primary" onClick={onNew}>
                  {t('rules.new_plus')}
                </button>
              ) : undefined
            }
          />
        ))}
    </>
  )
}
