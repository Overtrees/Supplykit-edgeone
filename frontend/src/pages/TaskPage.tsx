import React, { useState, useEffect, useRef } from 'react'
import { apiJson } from '../api/client'
import EmptyState from '../components/EmptyState'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../components/Toast'
import TaskCard from '../components/tasks/TaskCard'
import { IconRefresh, IconBroom, IconExport, IconClipboard, IconUndo } from '../components/Icons'
import ErrorRetry from '../components/ErrorRetry'
import { t } from '../locale'

const API = import.meta.env.VITE_API_BASE_URL || ''

// 词表化: 存键不存文案(渲染时 t() 解析, 切语言立即生效)
const EXPORT_TYPE_NAME = {
  orders: 'task.exp_orders',
  inventory: 'task.exp_inventory',
  slow: 'task.exp_slow',
  purchase: 'task.exp_purchase',
  purchase_suggestions: 'task.exp_purchase_suggestions',
}
const CLEAN_TARGET_NAME = {
  order: 'task.imp_order',
  inventory: 'task.imp_inventory',
  platform_inv: 'task.imp_platform_inv',
  inventory_b: 'task.imp_inventory_b',
  inbound: 'task.imp_inbound',
  outbound: 'task.imp_outbound',
  product: 'task.imp_product',
}
const TYPE_LABEL = {
  cleansing_eval: { label: 'task.type_eval', Icon: IconRefresh },
  seed: { label: 'task.type_seed', Icon: IconRefresh },
  cleansing: { label: 'task.type_cleansing', Icon: IconBroom },
  export: { label: 'task.type_export', Icon: IconExport },
  reset: { label: 'task.type_reset', Icon: IconUndo },
}
const STATUS_LABEL = {
  pending: 'task.st_pending',
  running: 'task.st_running',
  done: 'task.st_done',
  error: 'task.st_error',
}

export default function TaskPage() {
  const { channel } = useAppStore()
  const toast = useToast()
  const [tasks, setTasks] = useState([])
  const [downloading, setDownloading] = useState({})
  const [loading, setLoading] = useState(true)
  const [showAllDone, setShowAllDone] = useState(false) // 已完成折叠(默认 10 条)

  const [loadErr, setLoadErr] = useState('')
  const doneTasks = useRef({}) // 已触发完成提示的任务
  const firstLoad = useRef(true) // 首载只记录历史完成, 不弹提示(避免进页一堆历史任务 toast)
  const loadTasks = async () => {
    try {
      // S3-2 收口: raw 保留 r.status(401 判断), apiJson 补超时/token/BASE
      const r = await apiJson('GET', API + '/api/tasks?channel=' + channel, { raw: true })
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
                t('task.done_toast', {
                  what:
                    t.task_type === 'seed'
                      ? t('task.what_seed')
                      : t.task_type === 'clean'
                        ? t('task.what_cleansing')
                        : t.task_type === 'cleansing_eval'
                          ? t('task.what_eval')
                          : t.task_type === 'export'
                            ? t('task.what_export')
                            : t.task_type,
                }),
              )
              // 数据已变更，通知各页面刷新；dashboard 强制同步重建拿最新值（不用旧值）
              useAppStore
                .getState()
                .loadAll(1, { refresh: true })
                .catch(() => {})
              window.dispatchEvent(new Event('rules-changed'))
              window.dispatchEvent(new Event('insights-refresh'))
            } else {
              toast.error(t('task.failed_toast', { msg: String(t.result || '').slice(0, 60) }))
            }
          }
        })
      } else if (r.status === 401) setLoadErr(t('task.session_expired'))
      else if (d.error === 'database_busy') {
        if (tasks.length === 0) setLoadErr(t('task.db_busy'))
      } else setLoadErr(t('task.load_failed_msg', { msg: d.error || 'HTTP ' + r.status }))
    } catch (e) {
      if (tasks.length === 0) setLoadErr(t('task.net_retry'))
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
      // 分片导出(EdgeOne 响应体 6MB 限制): 逐片下载合并(单片 ≤6MB), 文件名用正式名
      let parts: string[] = []
      let realName = filename
      try {
        const sd = await apiJson('GET', API + '/api/exports/status?task_id=' + filename)
        if (sd.status === 'done') {
          parts = sd.parts || []
          realName = sd.name || filename
        }
      } catch {}
      let blob: Blob
      if (parts.length > 0) {
        const chunks: Blob[] = []
        for (const p of parts) {
          // S3-2 收口: blob 下载走 raw Response, apiJson 补超时(大文件 120s)
          const r = await apiJson('GET', API + '/api/exports/download/' + p, {
            raw: true,
            timeout: 120000,
          })
          if (!r.ok) throw new Error(t('task.chunk_failed'))
          chunks.push(await r.blob())
        }
        blob = new Blob(chunks, { type: 'text/csv' })
      } else {
        const dl = await apiJson('GET', API + '/api/exports/download/' + filename, {
          raw: true,
          timeout: 120000,
        })
        // 2026-10-30: 不检查 dl.ok 会静默下载"错误页 HTML"(文件名由前端命名, 内容却是 500 页)
        if (!dl.ok) throw new Error(t('task.dl_failed', { code: dl.status }))
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
      <div className="section-title">{t('task.page_title')}</div>
      <div className="small muted" style={{ padding: '0 0 8px 0', fontSize: 12 }}>
        {t('task.subtitle', { ch: channel === 'jd' ? t('task.ch_jd') : t('task.ch_other') })}
      </div>
      <div key={loading ? 'skeleton' : 'data'} className="fade-in">
        {loading ? (
          <div className="skeleton" style={{ height: 40 }} />
        ) : tasks.length === 0 ? (
          <div style={{ padding: 24 }}>
            {loadErr ? (
              <ErrorRetry error={t('task.load_failed')} onRetry={loadTasks} />
            ) : (
              <EmptyState
                icon="package"
                title={t('task.empty')}
                desc={t('task.empty_desc')}
                action={
                  <button
                    className="btn btn-primary"
                    onClick={() => window.__setPage && window.__setPage('cleansing')}
                  >
                    {t('task.go_import')}
                  </button>
                }
              />
            )}
          </div>
        ) : (
          (() => {
            /* 2026-10-27 已完成折叠: running/pending 全显, done/error 默认 10 条(列表曾无限增长) */
            const _active = tasks.filter(
              (t: any) => t.status === 'running' || t.status === 'pending',
            )
            const _finished = tasks.filter(
              (t: any) => t.status !== 'running' && t.status !== 'pending',
            )
            const _shown = showAllDone ? _finished : _finished.slice(0, 10)
            return (
              <>
                {[..._active, ..._shown].map(task => {
                  const type =
                    task.task_type === 'export'
                      ? 'export'
                      : task.task_type === 'seed'
                        ? 'seed'
                        : task.task_type === 'reset' || task.task_id.startsWith('reset_')
                          ? 'reset'
                          : task.task_type === 'cleansing_eval'
                            ? 'cleansing_eval'
                            : 'cleansing'
                  const meta = TYPE_LABEL[type] || { label: 'task.type_unknown', Icon: IconClipboard }
                  const result = task.result
                    ? typeof task.result === 'string'
                      ? safeParse(task.result)
                      : task.result
                    : null
                  const sub =
                    task.task_type === 'export'
                      ? t(EXPORT_TYPE_NAME[result?.result?.type || result?.type] || 'task.export')
                      : task.task_type === 'cleansing'
                        ? t(
                            CLEAN_TARGET_NAME[result?.result?.target || result?.target] ||
                              'task.import',
                          )
                        : task.task_type === 'cleansing_eval'
                          ? t('task.eval')
                          : task.task_id.slice(0, 22)
                  return (
                    <TaskCard
                      key={task.task_id}
                      task={task}
                      label={t(meta.label)}
                      sub={sub}
                      icon={meta.Icon ? <meta.Icon size={18} /> : undefined}
                      downloading={downloading[task.task_id]}
                      onDownload={download}
                    />
                  )
                })}
                {_finished.length > _shown.length && (
                  <div
                    className="clickable"
                    onClick={() => setShowAllDone(true)}
                    style={{
                      textAlign: 'center',
                      padding: '10px 0',
                      fontSize: 'var(--font-13)',
                      fontWeight: 600,
                      color: 'var(--primary)',
                      cursor: 'pointer',
                    }}
                  >
                    {t('task.show_more', { n: _finished.length - _shown.length })}
                  </div>
                )}
              </>
            )
          })()
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
