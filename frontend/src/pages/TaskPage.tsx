import React, { useState, useEffect, useRef } from 'react'
import EmptyState from '../components/EmptyState'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../components/Toast'
import TaskCard from '../components/tasks/TaskCard'
import {
  IconRefresh,
  IconBroom,
  IconExport,
  IconClipboard,
  IconUndo,
  IconCheck,
  IconClose,
} from '../components/Icons'
import ErrorRetry from '../components/ErrorRetry'

const API = import.meta.env.VITE_API_BASE_URL || ''

const EXPORT_TYPE_NAME = {
  orders: '订单明细',
  inventory: '库存明细',
  slow: '滞销明细',
  purchase: '补货建议',
  purchase_suggestions: '采购建议',
}
const CLEAN_TARGET_NAME = {
  order: '订单表',
  inventory: '库存表(自有)',
  platform_inv: '库存表(平台)',
  inventory_b: '库存表(B仓)',
  inbound: '入库记录表',
  outbound: '出库记录表',
  product: '商品表',
}
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
  const doneTasks = useRef({}) // 已触发完成提示的任务
  const firstLoad = useRef(true) // 首载只记录历史完成, 不弹提示(避免进页一堆历史任务 toast)
  const loadTasks = async () => {
    try {
      const r = await fetch(API + '/api/tasks?channel=' + channel, {
        headers: {
          'X-Auth-Token': (() => {
            try {
              return localStorage.getItem('c_token')
            } catch {
              return ''
            }
          })(),
        },
      })
      const d = await r.json()
      if (d.ok && Array.isArray(d.data)) {
        setTasks(d.data)
        setLoadErr('')
        // 检测任务从未完成 → 完成：提示 + 全局刷新数据
        d.data.forEach(function (t) {
          if ((t.status === 'done' || t.status === 'error') && !doneTasks.current[t.task_id]) {
            doneTasks.current[t.task_id] = true
            if (firstLoad.current) return // 首载: 仅记录, 不弹历史完成提示
            if (t.status === 'done') {
              toast.success(
                '任务完成: ' +
                  (t.task_type === 'seed'
                    ? '种子填充'
                    : t.task_type === 'clean'
                      ? '清洗导入'
                      : t.task_type === 'export'
                        ? '导出'
                        : t.task_type) +
                  ' ✓',
              )
              // 数据已变更，通知各页面刷新；dashboard 强制同步重建拿最新值（不用旧值）
              useAppStore
                .getState()
                .loadAll(1, { refresh: true })
                .catch(() => {})
              window.dispatchEvent(new Event('rules-changed'))
              window.dispatchEvent(new Event('insights-refresh'))
            } else {
              toast.error('任务失败: ' + String(t.result || '').slice(0, 60))
            }
          }
        })
      } else if (r.status === 401) setLoadErr('登录已失效，请重新登录')
      else if (d.error === 'database_busy') {
        if (tasks.length === 0) setLoadErr('数据正在处理中...')
      } else setLoadErr('加载失败: ' + (d.error || 'HTTP ' + r.status))
    } catch (e) {
      if (tasks.length === 0) setLoadErr('网络异常，自动重试中...')
    }
    setLoading(false)
  }

  useEffect(() => {
    setTasks([])
    setLoading(true)
    loadTasks()
  }, [channel])
  useEffect(() => {
    const poll = setInterval(loadTasks, 3000)
    return () => clearInterval(poll)
  }, [channel])
  // 页面从后台回到前台时立即刷新（不等下一次轮询）
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === 'visible') loadTasks()
    }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', onVis)
    }
  }, [channel])

  const download = async (taskId, filename) => {
    setDownloading(p => ({ ...p, [taskId]: true }))
    try {
      const auth = {
        'X-Auth-Token': (() => {
          try {
            return localStorage.getItem('c_token')
          } catch {
            return ''
          }
        })(),
      }
      // 分片导出(EdgeOne 响应体 6MB 限制): 逐片下载合并(单片 ≤6MB), 文件名用正式名
      let parts: string[] = []
      let realName = filename
      try {
        const st = await fetch(API + '/api/exports/status?task_id=' + filename, { headers: auth })
        const sd = await st.json()
        if (sd.status === 'done') {
          parts = sd.parts || []
          realName = sd.name || filename
        }
      } catch {}
      let blob: Blob
      if (parts.length > 0) {
        const chunks: Blob[] = []
        for (const p of parts) {
          const r = await fetch(API + '/api/exports/download/' + p, { headers: auth })
          if (!r.ok) throw new Error('分片下载失败')
          chunks.push(await r.blob())
        }
        blob = new Blob(chunks, { type: 'text/csv' })
      } else {
        const dl = await fetch(API + '/api/exports/download/' + filename, { headers: auth })
        blob = await dl.blob()
      }
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = realName
      a.click()
      setDownloading(p => ({ ...p, [taskId]: false }))
    } catch {}
  }

  return (
    <div className="card">
      <div className="section-title">任务管理</div>
      <div className="small muted" style={{ padding: '0 0 8px 0', fontSize: 12 }}>
        {channel === 'jd' ? '京东' : '其他渠道'} · 异步任务
      </div>
      <div key={loading ? 'skeleton' : 'data'} className="fade-in">
        {loading ? (
          <div className="skeleton" style={{ height: 40 }} />
        ) : tasks.length === 0 ? (
          <div style={{ padding: 24 }}>
            {loadErr ? (
              <ErrorRetry error={'加载失败'} onRetry={loadTasks} />
            ) : (
              <EmptyState
                icon="package"
                title="暂无任务"
                action={
                  <button
                    className="btn btn-primary"
                    onClick={() => window.__setPage && window.__setPage('settings')}
                  >
                    去一键填充种子数据 →
                  </button>
                }
              />
            )}
          </div>
        ) : (
          tasks.map(task => {
            const type =
              task.task_type === 'export'
                ? 'export'
                : task.task_type === 'seed'
                  ? 'seed'
                  : task.task_type === 'reset' || task.task_id.startsWith('reset_')
                    ? 'reset'
                    : 'cleansing'
            const meta = TYPE_LABEL[type] || { label: '任务', Icon: IconClipboard }
            const result = task.result
              ? typeof task.result === 'string'
                ? safeParse(task.result)
                : task.result
              : null
            const sub =
              task.task_type === 'export'
                ? EXPORT_TYPE_NAME[result?.result?.type || result?.type] || '导出'
                : task.task_type === 'cleansing'
                  ? CLEAN_TARGET_NAME[result?.result?.target || result?.target] || '导入'
                  : task.task_id.slice(0, 22)
            return (
              <TaskCard
                key={task.task_id}
                task={task}
                label={meta.label}
                sub={sub}
                icon={meta.Icon ? <meta.Icon size={18} /> : undefined}
                downloading={downloading[task.task_id]}
                onDownload={download}
              />
            )
          })
        )}
      </div>
    </div>
  )
}

function safeParse(s) {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}
function toBeijing(utc) {
  if (!utc) return ''
  try {
    const d = new Date(utc.replace(' ', 'T') + 'Z')
    return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  } catch {
    return utc
  }
}
