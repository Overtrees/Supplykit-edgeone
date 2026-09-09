import React, { useState } from "react"
import { useAppStore } from "../../store/useAppStore"
import { useToast } from "../../components/Toast"
import { useDebouncedSearch } from "../../hooks/useDebounce"
import { api } from "../../api/client"
interface HammerRulesProps { channel: string; onShowHistory?: (ch: string) => void }

export default function HammerRules({ channel, onShowHistory }: HammerRulesProps) {
  const toast = useToast()
  const {hammerRulesTab, setHammerRulesTab, bumpHammerRuleNew, hammerRulesMode, setHammerRulesMode, hammerSearch, setHammerSearch, prodBatch, prodSelIds, batchStateMap} = useAppStore()
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
      const r = await api.postHeavy('/api/rules/evaluate', {})
      const ev = (r.data && r.data.evaluated) || {}
      toast.success('评估完成 · jd ' + (ev.jd ?? '-') + ' · other ' + (ev.other ?? '-'))
      window.dispatchEvent(new Event('rules-changed'))
      window.dispatchEvent(new Event('insights-refresh'))
    } catch(e) { toast.error('评估失败: ' + (e.message||'')) }
    setEvaluating(false)
  }
  const runBatch = async (action, label) => {
    const s = useAppStore.getState()
    const ids = s.prodSelIds || []
    if (ids.length === 0) { toast.error('请先勾选规则'); return }
    if (action === 'delete' && !window.confirm('删除 ' + ids.length + ' 条规则？可在回收站恢复')) return
    setBatchBusy(true)
    try {
      // 统一走 api.post：自动注入 token/channel + 响应解包 + 缓存失效
      await api.postHeavy('/api/rules/batch', {action, ids})
      // 通知规则页刷新（绕过 api.get 缓存/去重，直接 fetch 重新加载）
      window.dispatchEvent(new Event('rules-changed'))
      if (action === 'delete') {
        toast.add({type:'success', title: label + '完成: ' + ids.length + ' 项', duration: 5000, action: {label: '撤销', handler: async () => {
          try {
            await api.postHeavy('/api/rules/batch', {action:'restore', ids})
            toast.success('已撤销删除')
            s.setProdBatchSel([]); s.setProdBatch(false); s.bumpProdBatchVersion()
          } catch(e) { toast.error('撤销失败: ' + (e.message||'')) }
        }}})
      } else {
        toast.success(label + '完成: ' + ids.length + ' 项')
      }
      s.setProdBatchSel([]); s.setProdBatch(false); s.bumpProdBatchVersion()
    } catch(e) { toast.error(label + '失败: ' + (e.message||'')) }
    setBatchBusy(false)
  }

  return (
    <div>
      <div className="hammer-header">{channel === 'jd' ? '京东' : '其他'} · 规则参数</div>
      {/* tab 竖排 */}
      <div className="hammer-segmented" style={{marginBottom:8}}>
        {[['rules','规则'],['params','补货'],['purchase','采购'],['slow','滞销']].map(([id,label]) => (
          <span key={id} onClick={() => setHammerRulesTab(id)}
            className={'hammer-segment' + (hammerRulesTab === id ? ' active' : '')}>
            {label}
          </span>
        ))}
      </div>
      {/* 规则 tab: 功能按钮竖排 */}
      {hammerRulesTab === 'rules' && <>
        <div className="hammer-row-2" style={{marginBottom:8}}>
          <button onClick={() => { setHammerRulesTab('rules'); bumpHammerRuleNew() }} className="hammer-btn btn-primary"
            style={{display:'flex',alignItems:'center',justifyContent:'center',gap:4}}>
            + 新建
          </button>
          <button onClick={() => setSearchOpen(!searchOpen)} className="hammer-btn btn-ghost"
            style={{display:'flex',alignItems:'center',justifyContent:'center',gap:4}}>
            搜索{hammerSearch ? ' ✓' : ''}
          </button>
        </div>
        <div className="hammer-row-2">
          <button onClick={() => { onShowHistory && onShowHistory(channel) }} className="hammer-btn btn-ghost"
            style={{display:'flex',alignItems:'center',justifyContent:'center',gap:4}}>
            变更历史
          </button>
          <button onClick={() => setBatchOpen(!batchOpen)} className="hammer-btn btn-ghost"
            style={{display:'flex',alignItems:'center',justifyContent:'center',gap:4,color:prodBatch?'var(--danger)':undefined, borderColor:prodBatch?'var(--danger)':undefined}}>
            批量操作
          </button>
        </div>
        <button onClick={runEvaluate} disabled={evaluating}
          className="hammer-btn btn-ghost" style={{display:'flex',alignItems:'center',justifyContent:'center',gap:4,width:'100%',color:'var(--primary)',opacity:evaluating?0.6:1,marginTop:8}}>
          {evaluating ? <span className="hammer-spinner" /> : '⚡'} {evaluating ? '评估中(全量 SKU×仓)...' : '立即运行全部规则'}
        </button>
        {batchOpen && (
          <div className="hammer-panel">
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
              <span className="text-12 muted2">已选 <b style={{color:'var(--text)'}}>{(useAppStore.getState().prodSelIds||[]).length}</b> 项</span>
              {useAppStore.getState().prodBatch ? (
                <button className="hammer-clear" onClick={() => { useAppStore.getState().setProdBatch(false); useAppStore.getState().setProdBatchSel([]) }}>退出批量模式</button>
              ) : (
                <button className="hammer-clear" onClick={() => useAppStore.getState().setProdBatch(true)}>进入批量模式</button>
              )}
            </div>
            <div className="hammer-btn-row">
            <button className="hammer-btn btn-ghost" onClick={() => { const s = useAppStore.getState(); if (!s.prodBatch) s.setProdBatch(true); s.requestProdBatchAll() }}>全选/取消</button>
          </div>
          <div className="hammer-btn-row" style={{marginTop:8}}>
              <button className="hammer-btn btn-ghost" style={{color:'var(--success)', opacity:(batchBusy||selSt.length===0||allActive)?0.4:1}} disabled={batchBusy||selSt.length===0||allActive} onClick={() => runBatch('active','启用')}>批量启用</button>
              <button className="hammer-btn btn-ghost" style={{color:'var(--warning)', opacity:(batchBusy||selSt.length===0||allInactive)?0.4:1}} disabled={batchBusy||selSt.length===0||allInactive} onClick={() => runBatch('inactive','停用')}>批量停用</button>
              <button className="hammer-btn btn-ghost" style={{color:'var(--danger)', opacity:(batchBusy||selSt.length===0)?0.4:1}} disabled={batchBusy||selSt.length===0} onClick={() => runBatch('delete','删除')}>批量删除</button>
          </div>
            <div className="muted2 text-10" style={{marginTop:8}}>勾选规则后在此批量操作（删除可回收站恢复）{allActive?' · 所选规则已全部启用':allInactive?' · 所选规则已全部停用':''}</div>
          </div>
        )}
        {searchOpen && <div className="hammer-panel">
          <input value={localSearch} onChange={e=>setLocalSearch(e.target.value)}
            placeholder="搜索规则名称..." className="hammer-input" />
          {hammerSearch && <div style={{marginTop:4,textAlign:'right'}}>
            <button className="hammer-clear" onClick={()=>setHammerSearch('')}>清除</button>
          </div>}
        </div>}
      </>}
      {/* 补货参数 tab: 模式切换竖排 */}
      {hammerRulesTab === 'params' && (
        <div className="hammer-btn-row" style={{marginTop:8}}>
          {channel === 'jd' && (
            <span onClick={() => setHammerRulesMode('bbcc')}
              className={'hammer-tab' + (hammerRulesMode==='bbcc' ? ' active' : '')}>
              BBCC 送仓
            </span>
          )}
          <span onClick={() => setHammerRulesMode('traditional')}
            className={'hammer-tab' + (hammerRulesMode==='traditional' ? ' active' : '')}>
            传统多仓
          </span>
        </div>
      )}
    </div>
  )
}

/* 看板页: 锤子菜单 时间维度(今日/本周/本月) */
