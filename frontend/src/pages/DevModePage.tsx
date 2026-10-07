import React, { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { clearCache, clearInflight } from '../api/client'
import { Group, Row, LastRow } from '../components/ListGroup'
import LogFileList from '../components/LogFileList'

const VERSION = (typeof __APP_VERSION__ !== 'undefined' && __APP_VERSION__) ? __APP_VERSION__ : '2.0.0'
const BUILD = (typeof __BUILD_DATE__ !== 'undefined' && __BUILD_DATE__) ? __BUILD_DATE__ : ''
const API = import.meta.env.VITE_API_BASE_URL || ''

const DEV_LABEL: Record<string, string> = {
  api_error: '接口异常', slow_request: '慢请求', cache_error: '缓存降级', quiet_error: '静默降级',
  frontend_error: '前端上报', window_error: 'JS 错误', unhandled_rejection: 'Promise 拒绝',
  component_error: '组件渲染', api_http_error: '接口 HTTP',
}
const LEVEL_LABEL: Record<string, string> = { warning: '警告', error: '异常', info: '提示' }


/** 开发者模式页(设置页·点版本号 6 次开启): 连接状态/环境信息/运行概况/日志分析/种子数据(开发工具) */
export default function DevModePage() {
  const toast = useToast()  // DevModePage 在 App ToastProvider 内, 可用
  const { wsStatus } = useAppStore()
  const [status, setStatus] = useState('检查中...')
  const [ping, setPing] = useState(0)
  const [lastCheck, setLastCheck] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [mon, setMon] = useState<any>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [logTotal, setLogTotal] = useState(0)
  const [showLogs, setShowLogs] = useState(false)
  const [logPage, setLogPage] = useState(1)
  const [moreLoading, setMoreLoading] = useState(false)
  const [ld, setLd] = useState(true)
  // 种子数据(从设置页移入): 填充/重置为数据操作工具, 归开发者维度
  const [confirm, setConfirm] = useState(null) // {type:'fill'|'reset'}
  const [seeding, setSeeding] = useState(() => { try { return !!localStorage.getItem('c_seed_task') } catch { return false } })
  const [resetting, setResetting] = useState(false)
  useEffect(() => {
    const h = () => { setSeeding(false) }
    window.addEventListener('seed-done', h)
    window.addEventListener('seed-error', h)
    return () => { window.removeEventListener('seed-done', h); window.removeEventListener('seed-error', h) }
  }, [])

  const checkConnection = async () => {
    setRefreshing(true)
    const start = performance.now()
    try {
      const r = await fetch(API + '/api/insights/ping')
      const ms = Math.round(performance.now() - start)
      const d = await r.json()
      setStatus(d.ok ? '正常' : '异常')
      setPing(ms)
      setLastCheck(new Date().toLocaleTimeString())
    } catch {
      setStatus('无法连接')
      setPing(0)
      setLastCheck(new Date().toLocaleTimeString())
    }
    setRefreshing(false)
  }

  const loadLogs = (p: number) => {
    if (p === 1) setLd(true); else setMoreLoading(true)
    api.get('/api/quality-logs?page=' + p + '&page_size=100&scope=dev')
      .then((r: any) => {
        const d = r.data || {}
        const items = Array.isArray(d) ? d : (d.items || [])
        setLogs(prev => p === 1 ? items : [...prev, ...items])
        setLogTotal(Array.isArray(d) ? items.length : (d.total || items.length))
        setLogPage(p); setLd(false); setMoreLoading(false)
      })
      .catch(() => { setLd(false); setMoreLoading(false) })
  }

  useEffect(() => {
    checkConnection()
    api.get('/api/monitor').then((r: any) => setMon(r.data)).catch(() => {})
    loadLogs(1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const doSeed = async () => {
    setConfirm(null); setSeeding(true)
    try {
      const r = await fetch(API + '/api/seed/fill', {method:'POST', headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok) {
        if (d.data?.requires_reset) { toast.error('已有数据，请先重置'); setSeeding(false); setConfirm('reset'); return }
        const taskId = d.data?.task_id
        if (taskId) { try { localStorage.setItem('c_seed_task', taskId) } catch {} }
        toast.add({type:'success', title:'填充任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
      } else { toast.error('填充失败: ' + (d.error || '')); setSeeding(false) }
    } catch { toast.error('填充失败'); setSeeding(false) }
  }

  const doReset = async () => {
    setConfirm(null)
    setResetting(true)
    try {
      const r = await fetch(API + '/api/seed/reset', {method:'POST', headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok && d.data?.task_id) {
        try { localStorage.setItem('c_reset_task', d.data.task_id) } catch {}
        toast.success('重置任务已提交，后台清理中...')
        // 轮询等待重置完成
        const poll = setInterval(async () => {
          try {
            const sr = await fetch(API + '/api/seed/fill/status?task_id=' + d.data.task_id, {headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
            const sd = await sr.json()
            if (sd.data?.status === 'done' || sd.data?.status === 'error') {
              clearInterval(poll)
              try { localStorage.removeItem('c_reset_task') } catch {}
              clearCache(); clearInflight()
              useAppStore.setState({ dashboard: null, alerts: [], stockRisk: [] })
              toast.success('数据已重置，即将刷新')
              setTimeout(() => window.location.reload(), 1500)
            }
          } catch { clearInterval(poll); setResetting(false) }
        }, 2000)
      } else {
        toast.error('重置失败: ' + (d.error || ''))
        setResetting(false)
      }
    } catch { toast.error('重置失败'); setResetting(false) }
  }

  return (
    <div style={{ padding: '16px 0', maxWidth: 500, margin: '0 auto' }}>
      {/* 连接状态(从设置页移入) */}
      <Group title="连接状态">
        <Row label="后端服务" value={status} sub={`${ping}ms · ${lastCheck}`} onClick={checkConnection} loading={refreshing} />
        <LastRow label="实时连接" value={wsStatus === 'connected' ? '已连接' : wsStatus === 'polling' ? '轮询中' : '已断开'} />
      </Group>

      {/* 环境信息(版本已在设置页, 此处只留构建/API/DB) */}
      <Group title="环境信息">
        <Row label="构建日期" value={BUILD || '-'} />
        <Row label="API" value={API || '同源'} />
        <LastRow label="DB" value="TiDB · EdgeOne Makers" />
      </Group>

      {/* 运行概况(monitor 汇总) */}
      <Group title="运行概况">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', padding: '14px 16px' }}>
          <div style={{ flex: 1, minWidth: 90, background: 'var(--bg)', borderRadius: 'var(--radius-md)', padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, color: 'var(--danger)', fontVariantNumeric: 'tabular-nums' }}>{mon ? (mon.totals?.error || 0) : '-'}</div>
            <div style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>异常总数</div>
          </div>
          <div style={{ flex: 1, minWidth: 90, background: 'var(--bg)', borderRadius: 'var(--radius-md)', padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, color: 'var(--warning)', fontVariantNumeric: 'tabular-nums' }}>{mon ? (mon.totals?.slow || 0) : '-'}</div>
            <div style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>慢请求总数</div>
          </div>
          <div style={{ flex: 1, minWidth: 90, background: 'var(--bg)', borderRadius: 'var(--radius-md)', padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{mon ? (mon.today?.error || 0) : '-'}</div>
            <div style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>今日异常</div>
          </div>
          <div style={{ flex: 1, minWidth: 90, background: 'var(--bg)', borderRadius: 'var(--radius-md)', padding: '10px 12px', textAlign: 'center' }}>
            <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{mon ? (mon.today?.slow || 0) : '-'}</div>
            <div style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)' }}>今日慢请求</div>
          </div>
        </div>
        <div style={{ padding: '0 16px 14px', fontSize: 'var(--font-10)', color: 'var(--muted2)', lineHeight: 1.5 }}>异常/慢请求留痕于 quality_logs(开发者维度), 控制台「日志分析」为平台请求级(24h 保留)</div>
      </Group>

      {/* 日志分析入口 */}
      <Group title="日志分析">
        <LastRow label="开发者异常日志" sub={`共 ${logTotal} 条`} onClick={() => { setShowLogs(true); if (logPage === 1) loadLogs(1) }} />
      </Group>

      {/* 种子数据(从设置页移入): 开发/运维数据工具 */}
      <Group title="种子数据">
        <Row label="一键填充" sub="生成 2,000 SKU × 60 天 × 10 万条模拟数据" onClick={() => setConfirm('fill')} loading={seeding} />
        <LastRow label="一键重置" sub="清空所有数据恢复初始状态" onClick={() => setConfirm('reset')} danger loading={resetting} />
      </Group>

      {/* 日志分析底部弹窗(标准 sheet) */}
      {showLogs && <>
        <div onClick={() => setShowLogs(false)} style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }} />
        <div style={{ position: 'fixed', left: 0, right: 0, bottom: 'calc(env(safe-area-inset-bottom) + 14px)', zIndex: 9999, display: 'flex', justifyContent: 'center', padding: '0 14px', pointerEvents: 'none' }}>
          <div onClick={e => e.stopPropagation()} className="material-regular" style={{ width: '100%', maxWidth: 600, borderRadius: 'var(--radius-lg)', padding: '18px 14px calc(14px + env(safe-area-inset-bottom))', boxShadow: 'var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)', pointerEvents: 'auto', maxHeight: '70vh', overflowY: 'auto' }}>
            <div style={{ fontSize: 'var(--font-18)', fontWeight: 700, marginBottom: 12, textAlign: 'center', color: 'var(--text)' }}>日志分析 · 开发者维度</div>
            {ld ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted2)' }}>加载中...</div>
            ) : logs.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted2)' }}>暂无开发者日志</div>
            ) : (
              logs.map((x: any, i: number) => (
                <div key={x.id || i} style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span className={'pill ' + (x.level === 'error' ? 'danger' : x.level === 'warning' ? 'warning' : 'info')} style={{ fontSize: 'var(--font-9)', padding: '1px 6px', minHeight: 'auto', lineHeight: '16px', flexShrink: 0 }}>{LEVEL_LABEL[x.level] || x.level}</span>
                    <span style={{ fontSize: 'var(--font-xs)', fontWeight: 600, flexShrink: 0 }}>{DEV_LABEL[x.log_type] || x.log_type}</span>
                    <span className="mono" style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)', marginLeft: 'auto', flexShrink: 0 }}>{String(x.created_at || '').slice(5, 16)}</span>
                  </div>
                  <div style={{ fontSize: 'var(--font-xs)', color: 'var(--muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={(x.message || '') + (x.details ? '\n' + String(x.details).slice(0, 300) : '')}>{x.message || '-'}</div>
                  {x.details && <div style={{ fontSize: 9, color: 'var(--muted2)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{String(x.details).slice(0, 120)}</div>}
                </div>
              ))
            )}
            {logs.length < logTotal && (
              <button onClick={() => loadLogs(logPage + 1)} disabled={moreLoading} style={{ width: '100%', padding: '10px 0', border: 'none', background: 'transparent', color: 'var(--primary)', fontSize: 'var(--font-sm)', cursor: 'pointer' }}>
                {moreLoading ? '加载中...' : `加载更多 (${logs.length}/${logTotal})`}
              </button>
            )}
            <div style={{ fontSize: 'var(--font-16)', fontWeight: 700, margin: '14px 0 6px', textAlign: 'center', color: 'var(--text)' }}>历史文件 · 开发者维度</div>
            <LogFileList scope="dev" />
          </div>
        </div>
      </>}

      {/* 种子数据确认弹窗 */}
      {confirm === 'fill' && (
        <ConfirmDialog
          open
          title="生成种子数据？"
          desc="将生成 160 个商品、60 天订单、9 个仓库库存等模拟数据，覆盖现有数据。"
          confirmLabel="生成"
          onConfirm={doSeed}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'reset' && (
        <ConfirmDialog
          open
          title="重置所有数据？"
          desc="此操作不可恢复。将清空订单、库存、商品、规则等全部数据。"
          confirmLabel="重置"
          onConfirm={doReset}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
