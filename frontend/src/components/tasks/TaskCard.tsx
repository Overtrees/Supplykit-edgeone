import React, { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { IconCheck, IconClose, IconRefresh, IconClipboard } from '../Icons'

interface TaskStep {
  name?: string
  elapsed?: number
  started?: number
}

interface TaskItem {
  task_id: string
  task_type: string
  status: string
  params?: string | Record<string, unknown>
  result?: string | Record<string, unknown> | null
  created_at?: string
}

interface TaskCardProps {
  task: TaskItem
  label: string
  sub: string
  icon?: ReactNode
  downloading?: boolean
  onDownload?: (taskId: string, filename: string) => void
}

function safeParse<T>(v: string | T | null | undefined, fallback: T): T {
  if (v == null) return fallback
  if (typeof v !== 'string') return v as T
  try {
    return JSON.parse(v) as T
  } catch {
    return fallback
  }
}

/** 2026-10-15 任务卡片可视化(进度条/步骤耗时/状态)——组件化不膨胀 TaskPage——CSS 全全局(styles.css .task-*) */
export default function TaskCard({
  task,
  label,
  sub,
  icon,
  downloading,
  onDownload,
}: TaskCardProps) {
  const st = task.status
  const params = safeParse(task.params, {} as Record<string, unknown>)
  const result = safeParse(task.result, null) as Record<string, unknown> | null
  const resultInner =
    result && typeof result === 'object' && 'result' in result
      ? (result.result as Record<string, unknown>)
      : result
  const steps = Array.isArray(params.steps) ? (params.steps as TaskStep[]) : []
  const cur = (params.cur_step as TaskStep) || null
  const total = Number(params.total || 0)
  const offset = Number(params.offset || 0)
  const progStep = Number(params.step || 0)
  const success = Number(resultInner?.success ?? params.success ?? 0)
  const failed = Number(resultInner?.failed ?? params.failed ?? 0)
  const elapsed = resultInner?.elapsed ? Number(resultInner.elapsed) : 0

  // 进度 %: cleansing/export 用 offset/total; seed 用 step/10
  let pct = 0
  if (st === 'done') pct = 100
  else if (total > 0) pct = Math.min(Math.round((offset / total) * 100), 99)
  else if (params.step !== undefined) pct = Math.min(Math.round((progStep / 10) * 100), 99)

  // 进行中步骤实时耗时(本地 1s 节流——now - started)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!cur || st !== 'running') return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [cur, st])

  const filename = String(resultInner?.filename || result?.filename || '')
  const lastStep = steps.length ? steps[steps.length - 1] : null

  return (
    <div className={`task-card${st === 'running' ? ' task-running' : ''}`}>
      <div className="task-card-icon">{icon || <IconClipboard size={18} />}</div>
      <div className="task-card-body">
        <div className="task-card-title">
          <span className="ellipsis">{label}</span>
          {st === 'running' && params.driver ? (
            <span
              className={
                'task-driver ' +
                (params.driver === 'backend' ? 'task-driver-back' : 'task-driver-front')
              }
            >
              {params.driver === 'backend' ? '后端推进' : '前端轮询'}
            </span>
          ) : null}
          <span className={'task-status ' + st}>
            {st === 'running' && <IconRefresh size={11} className="task-spin" />}
            {st === 'done' && <IconCheck size={11} />}
            {st === 'error' && <IconClose size={11} />}
            {st === 'done'
              ? '完成'
              : st === 'error'
                ? '失败'
                : st === 'running'
                  ? '进行中'
                  : '等待'}
          </span>
        </div>
        <div className="task-card-sub">{sub}</div>

        {/* 进度条(running 显示) */}
        {(st === 'running' || (steps.length > 0 && st === 'done')) && (
          <div className="task-progress-wrap">
            <div className="task-progress-track">
              <div className="task-progress-bar" style={{ width: pct + '%' }} />
            </div>
            <span className="task-progress-pct">{st === 'done' ? '100%' : pct + '%'}</span>
          </div>
        )}

        {/* 步骤耗时(每步完成/失败/进度中) */}
        {steps.length > 0 && (
          <div className="task-steps">
            {steps.map((s, i) => (
              <div key={i} className="task-step-row">
                <IconCheck size={10} className="task-step-ok" />
                <span className="task-step-name">{s.name || '步骤 ' + (i + 1)}</span>
                <span className="task-step-time">
                  {s.elapsed != null ? s.elapsed.toFixed(1) + 's' : '—'}
                </span>
              </div>
            ))}
            {cur && st === 'running' && (
              <div className="task-step-row task-cur">
                <IconRefresh size={10} className="task-spin" />
                <span className="task-step-name">{cur.name || '处理中'}</span>
                <span className="task-step-time task-cur-time">
                  {cur.started ? ((now - Number(cur.started) * 1000) / 1000).toFixed(0) + 's' : '…'}
                </span>
              </div>
            )}
          </div>
        )}

        {/* 结果信息(done/error) */}
        {st === 'done' && (
          <div className="task-card-meta">
            {total > 0 && <span>共 {total} 行</span>}
            {(success > 0 || failed > 0) && (
              <span>
                成功 {success}
                {failed > 0 ? <span className="task-fail"> / 失败 {failed}</span> : null}
              </span>
            )}
            {elapsed > 0 && <span>耗时 {elapsed}s</span>}
          </div>
        )}
        {st === 'error' && (
          <div className="task-card-meta task-err">
            {String(resultInner?.error || result?.error || '任务失败').slice(0, 120)}
          </div>
        )}
        {st === 'done' && task.task_type === 'export' && filename && (
          <div className="task-card-meta">
            <button
              className="task-download"
              disabled={downloading}
              onClick={() => onDownload && onDownload(task.task_id, filename)}
            >
              {downloading ? '下载中…' : '下载'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
