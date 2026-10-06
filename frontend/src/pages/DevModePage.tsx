import React, { useState, useEffect } from 'react'
import { api } from '../api/client'

const VERSION = (typeof __APP_VERSION__ !== 'undefined' && __APP_VERSION__) ? __APP_VERSION__ : '2.0.0'
const BUILD = (typeof __BUILD_DATE__ !== 'undefined' && __BUILD_DATE__) ? __BUILD_DATE__ : ''

const DEV_LABEL: Record<string, string> = {
  api_error: '接口异常', slow_request: '慢请求', cache_error: '缓存降级', quiet_error: '静默降级',
  frontend_error: '前端上报', window_error: 'JS 错误', unhandled_rejection: 'Promise 拒绝',
  component_error: '组件渲染', api_http_error: '接口 HTTP',
}
const LEVEL_LABEL: Record<string, string> = { warning: '警告', error: '异常', info: '提示' }

/** 开发者模式页(设置页·点版本号 6 次开启): 版本/构建/环境信息 + 异常日志分析(开发者维度) */
export default function DevModePage() {
  const [mon, setMon] = useState<any>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [logTotal, setLogTotal] = useState(0)
  const [showLogs, setShowLogs] = useState(false)
  const [logPage, setLogPage] = useState(1)
  const [moreLoading, setMoreLoading] = useState(false)
  const [ld, setLd] = useState(true)

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
    api.get('/api/monitor').then((r: any) => setMon(r.data)).catch(() => {})
    loadLogs(1)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="container" style={{ paddingTop: 'calc(70px + env(safe-area-inset-top,0px))' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <button className="back-btn" onClick={() => { try { (window as any).__setPage && (window as any).__setPage('settings') } catch(e) {} }}>‹</button>
        <div style={{ fontSize: 'var(--font-18)', fontWeight: 700 }}>开发者模式</div>
      </div>

      {/* 环境信息 */}
      <div style={{ background: 'var(--card)', borderRadius: 'var(--radius-lg)', padding: 16, marginBottom: 12 }}>
        <div style={{ fontSize: 'var(--font-13)', fontWeight: 600, color: 'var(--muted2)', textTransform: 'uppercase', letterSpacing: 0.3, marginBottom: 8 }}>环境信息</div>
        <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)' }}><span>版本</span><span className="mono">v{VERSION}</span></div>
        <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)' }}><span>构建日期</span><span className="mono">{BUILD || '-'}</span></div>
        <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid var(--border)' }}><span>API</span><span className="mono" style={{ maxWidth: '60%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{(import.meta.env.VITE_API_BASE_URL || '同源')}</span></div>
        <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted)', display: 'flex', justifyContent: 'space-between', padding: '6px 0' }}><span>DB</span><span>TiDB · EdgeOne Makers</span></div>
      </div>

      {/* 运行概况(monitor 汇总) */}
      <div style={{ background: 'var(--card)', borderRadius: 'var(--radius-lg)', padding: 16, marginBottom: 12 }}>
        <div style={{ fontSize: 'var(--font-13)', fontWeight: 600, color: 'var(--muted2)', textTransform: 'uppercase', letterSpacing: 0.3, marginBottom: 8 }}>运行概况</div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
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
        <div style={{ marginTop: 10, fontSize: 'var(--font-10)', color: 'var(--muted2)', lineHeight: 1.5 }}>异常/慢请求留痕于 quality_logs(开发者维度), 控制台「日志分析」为平台请求级(24h 保留)</div>
      </div>

      {/* 日志分析入口 */}
      <div style={{ background: 'var(--card)', borderRadius: 'var(--radius-lg)', overflow: 'hidden', marginBottom: 12 }}>
        <div onClick={() => { setShowLogs(true); if (logPage === 1) loadLogs(1) }} className="clickable" style={{ padding: '0 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0', minHeight: 48 }}>
            <div>
              <div style={{ fontSize: 'var(--font-lg)', color: 'var(--text)' }}>日志分析</div>
              <div style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)', marginTop: 2 }}>开发者维度异常日志 · 共 {logTotal} 条</div>
            </div>
            <span style={{ color: 'var(--muted2)', fontSize: 18 }}>›</span>
          </div>
        </div>
      </div>

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
                  <div style={{ fontSize: 'var(--font-xs)', color: 'var(--muted)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={(x.message || '') + ((x.details || '').slice(0, 300) ? '\n' + (x.details || '').slice(0, 300) : '')}>{x.message || '-'}</div>
                  {x.details && <div style={{ fontSize: 9, color: 'var(--muted2)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{String(x.details).slice(0, 120)}</div>}
                </div>
              ))
            )}
            {logs.length < logTotal && (
              <button onClick={() => loadLogs(logPage + 1)} disabled={moreLoading} style={{ width: '100%', padding: '10px 0', border: 'none', background: 'transparent', color: 'var(--primary)', fontSize: 'var(--font-sm)', cursor: 'pointer' }}>
                {moreLoading ? '加载中...' : `加载更多 (${logs.length}/${logTotal})`}
              </button>
            )}
          </div>
        </div>
      </>}
    </div>
  )
}
