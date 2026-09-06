import React, { useState, useEffect, useRef } from 'react'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../components/Toast'
import { IconRefresh, IconBroom, IconExport, IconClipboard, IconUndo } from '../components/Icons'
import { t } from '../locale'
import ErrorRetry from '../components/ErrorRetry'

const API = import.meta.env.VITE_API_BASE_URL || ''

const EXPORT_TYPE_NAME = { orders: '订单明细', inventory: '库存明细', slow: '滞销明细', purchase: '补货建议', purchase_suggestions: '采购建议' }
const CLEAN_TARGET_NAME = { order: '订单表', inventory: '库存表(自有)', platform_inv: '库存表(平台)', inventory_b: '库存表(B仓)', inbound: '入库记录表', outbound: '出库记录表', product: '商品表' }
const TYPE_LABEL = {
  seed: { label: '种子数据填充', Icon: IconRefresh },
  cleansing: { label: '数据清洗导入', Icon: IconBroom },
  export: { label: '导出任务', Icon: IconExport },
  reset: { label: '数据重置', Icon: IconUndo },
}
const STATUS_LABEL = { pending: '等待中', running: '进行中', done: '已完成', error: '失败' }

export default function TaskPage() {
  const { channel } = useAppStore()
  const toast = useToast()
  const [tasks, setTasks] = useState([])
  const [downloading, setDownloading] = useState({})
  const [loading, setLoading] = useState(true)

  const [loadErr, setLoadErr] = useState('')
  const doneTasks = useRef({})  // 已触发完成提示的任务
  const loadTasks = async () => {
    try {
      const r = await fetch(API + '/api/tasks?channel=' + channel, { headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })() } })
      const d = await r.json()
      if (d.ok && Array.isArray(d.data)) {
        setTasks(d.data); setLoadErr('')
        // 检测任务从未完成 → 完成：提示 + 全局刷新数据
        d.data.forEach(function(t) {
          if ((t.status === 'done' || t.status === 'error') && !doneTasks.current[t.task_id]) {
            doneTasks.current[t.task_id] = true
            if (t.status === 'done') {
              toast.success('任务完成: ' + (t.task_type === 'seed' ? '种子填充' : t.task_type === 'clean' ? '清洗导入' : t.task_type === 'export' ? '导出' : t.task_type) + ' ✓')
              // 数据已变更，通知各页面刷新；dashboard 强制同步重建拿最新值（不用旧值）
              useAppStore.getState().loadAll(1, {refresh: true}).catch(() => {})
              window.dispatchEvent(new Event('rules-changed'))
              window.dispatchEvent(new Event('insights-refresh'))
            } else {
              toast.error('任务失败: ' + String(t.result || '').slice(0, 60))
            }
          }
        })
      }
      else if (r.status === 401) setLoadErr('登录已失效，请重新登录')
      else if (d.error === 'database_busy') { if (tasks.length === 0) setLoadErr('数据正在处理中...') }
      else setLoadErr('加载失败: ' + (d.error || ('HTTP ' + r.status)))
    } catch (e) { if (tasks.length === 0) setLoadErr('网络异常，自动重试中...') }
    setLoading(false)
  }

  useEffect(() => { setTasks([]); setLoading(true); loadTasks() }, [channel])
  useEffect(() => { const poll = setInterval(loadTasks, 3000); return () => clearInterval(poll) }, [channel])
  // 页面从后台回到前台时立即刷新（不等下一次轮询）
  useEffect(() => {
    const onVis = () => { if (document.visibilityState === 'visible') loadTasks() }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => { document.removeEventListener('visibilitychange', onVis); window.removeEventListener('focus', onVis) }
  }, [channel])

  const download = async (taskId, filename) => { setDownloading(p => ({...p, [taskId]: true}));
    try {
      const dl = await fetch(API + '/api/exports/download/' + filename, { headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })() } })
      const blob = await dl.blob(); const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
      a.download = filename; a.click(); setDownloading(p => ({...p, [taskId]: false}))
    } catch {}
  }

  return (
    <div className="card">
      <div className="section-title">任务管理</div>
      <div className="small muted" style={{ padding: '0 0 8px 0', fontSize: 12 }}>{channel === 'jd' ? '京东' : '其他渠道'} · 异步任务</div>
      {loading ? <div className="skeleton" style={{ height: 40 }} /> :
        tasks.length === 0 ? <div className="small muted" style={{ padding: 24, textAlign: 'center' }}>
          {loadErr ? <ErrorRetry error={'加载失败：' + loadErr} onRetry={loadTasks} /> : <><div style={{marginBottom:10}}>暂无任务</div>
          <button className="btn btn-primary" onClick={()=>window.__setPage&&window.__setPage('settings')}>去一键填充种子数据 →</button></>}
        </div> :
          tasks.map(task => {
            const type = task.task_type === 'export' ? 'export' :
              task.task_type === 'seed' ? 'seed' :
              task.task_type === 'reset' || task.task_id.startsWith('reset_') ? 'reset' : 'cleansing'
            const meta = TYPE_LABEL[type] || { label: '任务', Icon: IconClipboard }
            const st = task.status
            const result = task.result ? (typeof task.result === 'string' ? safeParse(task.result) : task.result) : null
            const filename = result?.result?.filename || result?.filename || ''
            return (
              <div key={task.task_id} className="task-card">
                <div className="task-card-icon">{meta.Icon ? <meta.Icon size={18} /> : <IconClipboard size={18} />}</div>
                <div className="task-card-body">
                  <div className="task-card-title">
                    <span className="ellipsis">{meta.label}</span>
                    <span className={'task-status ' + st}>{STATUS_LABEL[st]}</span>
                  </div>
                  <div className="task-card-sub">{task.task_type === "export" ? EXPORT_TYPE_NAME[result?.result?.type || result?.type] || "导出" : task.task_type === "cleansing" ? CLEAN_TARGET_NAME[result?.result?.target || result?.target] || "导入" : task.task_id.slice(0,22)}</div>
                  {Array.isArray(task.steps) && task.steps.length > 0 && (
                    <div style={{marginTop:6,display:'flex',flexDirection:'column',gap:3}}>
                      {task.steps.map((s, i) => (
                        <div key={i} style={{display:'flex',alignItems:'center',gap:6,fontSize:11}}>
                          <span style={{color: s.status === 'ok' ? 'var(--success)' : s.status === 'error' ? 'var(--danger)' : 'var(--muted2)'}}>
                            {s.status === 'ok' ? '✓' : s.status === 'error' ? '✗' : ''}
                          </span>
                          <span style={{color:'var(--text)',flex:1}}>{s.name}</span>
                          {s.status === 'ok' && <span style={{color:'var(--muted2)',fontSize:10}}>{s.elapsed}s</span>}
                          {s.status === 'error' && <span style={{color:'var(--danger)',fontSize:10}}>{String(s.error||'').slice(0,30)}</span>}
                          {s.status === 'running' && <><span style={{color:'var(--primary)',fontSize:10}}>进行中</span><span className="hammer-spinner" style={{width:10,height:10,borderWidth:1.5}} /></>}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="task-card-sub-row">
                    <div className="task-card-time">{task.created_at ? toBeijing(task.created_at) : ''}</div>
                    {st === 'done' && filename && task.task_type === 'export' && (
                      <button className="task-download" onClick={() => download(task.task_id, filename)} disabled={downloading[task.task_id]}>{downloading[task.task_id] ? '下载中...' : '下载'}</button>
                    )}
                  </div>
                  {st === 'running' && <div className="hero-progress"><div className="hero-progress-bar" /></div>}
                </div>
              </div>
            )
          })
      }
    </div>
  )
}

function safeParse(s) {
  try { return JSON.parse(s) } catch { return null }
}
function toBeijing(utc) {
  if (!utc) return ''
  try { const d = new Date(utc.replace(' ', 'T') + 'Z'); return d.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) } catch { return utc }
}