import { IconCheck, IconLightning } from '../Icons'
import React, { useState } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { useToast } from '../../components/Toast'
import { useDebouncedSearch } from '../../hooks/useDebounce'
import { api } from '../../api/client'
import { t } from '../../locale'
interface HammerRulesProps {
  channel: string
  onShowHistory?: (ch: string) => void
}

export default function HammerRules({ channel, onShowHistory }: HammerRulesProps) {
  const toast = useToast()
  const {
    hammerRulesTab,
    setHammerRulesTab,
    bumpHammerRuleNew,
    hammerRulesMode,
    setHammerRulesMode,
    hammerSearch,
    setHammerSearch,
    prodBatch,
    prodSelIds,
    batchStateMap,
  } = useAppStore()
  // 批量按钮状态判断: 所选全部启用→批量启用禁用; 所选全部停用→批量停用禁用; 混合→都可用
  const selSt = prodSelIds || []
  const stMap = batchStateMap || {}
  const allActive = selSt.length > 0 && selSt.every(id => stMap[id] === 1)
  const allInactive = selSt.length > 0 && selSt.every(id => stMap[id] === 0)
  const [localSearch, setLocalSearch] = useDebouncedSearch(hammerSearch, setHammerSearch)
  const [searchOpen, setSearchOpen] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchBusy, setBatchBusy] = useState(false)
  const [evaluating, setEvaluating] = useState(false)
  // 立即运行: 规则改动后即时全量评估(双渠道 SKU×仓), 不等每日 cron; 完成后看板/建议页刷新
  const runEvaluate = async () => {
    setEvaluating(true)
    try {
      const r = await api.postHeavy('/api/rules/evaluate', {
        // 2026-10-28 mode 生效: 按当前补货模式过滤规则(''=全部模式通用规则均生效)
        mode: useAppStore.getState().hammerReplenMode || '',
      })
      const ev = (r.data && r.data.evaluated) || {}
      toast.success(t('hr.eval_done', { jd: ev.jd ?? '-', ot: ev.other ?? '-' }))
      window.dispatchEvent(new Event('rules-changed'))
      window.dispatchEvent(new Event('insights-refresh'))
    } catch (e) {
      toast.error(t('hr.eval_failed', { msg: e.message || '' }))
    }
    setEvaluating(false)
  }
  const runBatch = async (action, label) => {
    const s = useAppStore.getState()
    const ids = s.prodSelIds || []
    if (ids.length === 0) {
      toast.error(t('hr.pick_rules'))
      return
    }
    if (action === 'delete' && !window.confirm(t('hr.confirm_delete', { n: ids.length }))) return
    setBatchBusy(true)
    try {
      // 统一走 api.post：自动注入 token/channel + 响应解包 + 缓存失效
      await api.postHeavy('/api/rules/batch', { action, ids })
      // 通知规则页刷新（绕过 api.get 缓存/去重，直接 fetch 重新加载）
      window.dispatchEvent(new Event('rules-changed'))
      if (action === 'delete') {
        toast.add({
          type: 'success',
          title: t('hr.batch_done', { label, n: ids.length }),
          duration: 5000,
          action: {
            label: t('hr.undo'),
            handler: async () => {
              try {
                await api.postHeavy('/api/rules/batch', { action: 'restore', ids })
                toast.success(t('hr.undone'))
                s.setProdBatchSel([])
                s.setProdBatch(false)
                s.bumpProdBatchVersion()
              } catch (e) {
                toast.error(t('hr.undo_failed', { msg: e.message || '' }))
              }
            },
          },
        })
      } else {
        toast.success(t('hr.batch_done', { label, n: ids.length }))
      }
      s.setProdBatchSel([])
      s.setProdBatch(false)
      s.bumpProdBatchVersion()
    } catch (e) {
      toast.error(t('hr.batch_failed', { label, msg: e.message || '' }))
    }
    setBatchBusy(false)
  }

  return (
    <div>
      <div className="hammer-header">
        {t('hr.header', { ch: channel === 'jd' ? t('hr.ch_jd') : t('hr.ch_other') })}
      </div>
      {/* tab 竖排 */}
      <div className="hammer-segmented" style={{ marginBottom: 8 }}>
        {[
          ['rules', 'hr.tab_rules'],
          ['params', 'hr.tab_params'],
          ['purchase', 'hr.tab_purchase'],
          ['slow', 'hr.tab_slow'],
        ].map(([id, label]) => (
          <span
            key={id}
            onClick={() => setHammerRulesTab(id)}
            className={'hammer-segment' + (hammerRulesTab === id ? ' active' : '')}
          >
            {t(label)}
          </span>
        ))}
      </div>
      {/* 规则 tab: 功能按钮竖排 */}
      {hammerRulesTab === 'rules' && (
        <>
          <div className="hammer-row-2" style={{ marginBottom: 8 }}>
            <button
              onClick={() => {
                setHammerRulesTab('rules')
                bumpHammerRuleNew()
              }}
              className="hammer-btn btn-primary"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}
            >
              {t('hr.new')}
            </button>
            <button
              onClick={() => setSearchOpen(!searchOpen)}
              className="hammer-btn btn-ghost"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}
            >
              {t('hr.search')}
              {hammerSearch ? <IconCheck size={12} style={{ verticalAlign: -2 }} /> : ''}
            </button>
          </div>
          <div className="hammer-row-2">
            <button
              onClick={() => {
                onShowHistory && onShowHistory(channel)
              }}
              className="hammer-btn btn-ghost"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}
            >
              {t('hr.history')}
            </button>
            <button
              onClick={() => setBatchOpen(!batchOpen)}
              className="hammer-btn btn-ghost"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 4,
                color: prodBatch ? 'var(--danger)' : undefined,
                borderColor: prodBatch ? 'var(--danger)' : undefined,
              }}
            >
              {t('hr.batch')}
            </button>
          </div>
          <button
            onClick={runEvaluate}
            disabled={evaluating}
            className="hammer-btn btn-ghost"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 4,
              width: '100%',
              color: 'var(--primary)',
              opacity: evaluating ? 0.6 : 1,
              marginTop: 8,
            }}
          >
            {evaluating ? (
              <span className="hammer-spinner" />
            ) : (
              <IconLightning size={14} style={{ verticalAlign: -2 }} />
            )}{' '}
            {evaluating ? t('hr.evaluating') : t('hr.run_all')}
          </button>
          {batchOpen && (
            <div className="hammer-panel">
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: 8,
                }}
              >
                <span className="text-12 muted2">
                  {t('hr.selected_n', {
                    n: (useAppStore.getState().prodSelIds || []).length,
                  })}
                </span>
                {useAppStore.getState().prodBatch ? (
                  <button
                    className="hammer-clear"
                    onClick={() => {
                      useAppStore.getState().setProdBatch(false)
                      useAppStore.getState().setProdBatchSel([])
                    }}
                  >
                    {t('hr.exit_batch')}
                  </button>
                ) : (
                  <button
                    className="hammer-clear"
                    onClick={() => useAppStore.getState().setProdBatch(true)}
                  >
                    {t('hr.enter_batch')}
                  </button>
                )}
              </div>
              <div className="hammer-btn-row">
                <button
                  className="hammer-btn btn-ghost"
                  onClick={() => {
                    const s = useAppStore.getState()
                    if (!s.prodBatch) s.setProdBatch(true)
                    s.requestProdBatchAll()
                  }}
                >
                  {t('hr.select_all_toggle')}
                </button>
              </div>
              <div className="hammer-btn-row" style={{ marginTop: 8 }}>
                <button
                  className="hammer-btn btn-ghost"
                  style={{
                    color: 'var(--success)',
                    opacity: batchBusy || selSt.length === 0 || allActive ? 0.4 : 1,
                  }}
                  disabled={batchBusy || selSt.length === 0 || allActive}
                  onClick={() => runBatch('active', t('hr.act_active'))}
                >
                  {t('hr.batch_enable')}
                </button>
                <button
                  className="hammer-btn btn-ghost"
                  style={{
                    color: 'var(--warning)',
                    opacity: batchBusy || selSt.length === 0 || allInactive ? 0.4 : 1,
                  }}
                  disabled={batchBusy || selSt.length === 0 || allInactive}
                  onClick={() => runBatch('inactive', t('hr.act_inactive'))}
                >
                  {t('hr.batch_disable')}
                </button>
                <button
                  className="hammer-btn btn-ghost"
                  style={{
                    color: 'var(--danger)',
                    opacity: batchBusy || selSt.length === 0 ? 0.4 : 1,
                  }}
                  disabled={batchBusy || selSt.length === 0}
                  onClick={() => runBatch('delete', t('hr.act_delete'))}
                >
                  {t('hr.batch_delete')}
                </button>
              </div>
              <div className="muted2 text-10" style={{ marginTop: 8 }}>
                {t('hr.batch_hint')}
                {allActive ? t('hr.all_active') : allInactive ? t('hr.all_inactive') : ''}
              </div>
            </div>
          )}
          {searchOpen && (
            <div className="hammer-panel">
              <input
                value={localSearch}
                onChange={e => setLocalSearch(e.target.value)}
                placeholder={t('hr.search_ph')}
                className="hammer-input"
              />
              {hammerSearch && (
                <div style={{ marginTop: 4, textAlign: 'right' }}>
                  <button className="hammer-clear" onClick={() => setHammerSearch('')}>
                    {t('hr.clear')}
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}
      {/* 补货参数 tab: 模式切换竖排 */}
      {hammerRulesTab === 'params' && (
        <div className="hammer-btn-row" style={{ marginTop: 8 }}>
          {channel === 'jd' && (
            <span
              onClick={() => setHammerRulesMode('bbcc')}
              className={'hammer-tab' + (hammerRulesMode === 'bbcc' ? ' active' : '')}
            >
              {t('hr.mode_bbcc')}
            </span>
          )}
          <span
            onClick={() => setHammerRulesMode('traditional')}
            className={'hammer-tab' + (hammerRulesMode === 'traditional' ? ' active' : '')}
          >
            {t('hr.mode_trad')}
          </span>
        </div>
      )}
    </div>
  )
}

/* 看板页: 锤子菜单 时间维度(今日/本周/本月) */
