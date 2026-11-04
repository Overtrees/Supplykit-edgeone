import React, { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { IconCheck, IconClose, IconRefresh, IconClipboard } from '../Icons'
import { t } from '../../locale'

interface TaskStep {
  name?: string
  /** 2026-11-04 步名结构化: name_key+params 走 t() 出双语(后端 cleansing 写入); name 保留原文兜底历史 */
  name_key?: string
  p?: Record<string, string | number>
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

// 2026-10-27 相对时间(卡片右上角): 刚刚 / N分钟前 / N小时前 / MM-DD
const relTime = (iso?: string) => {
  if (!iso) return ''
  // 注意: 变量名不能叫 t —— 会遮蔽 locale 的 t(), 相对时间文案就永远出中文
  const _ts = new Date(
    String(iso).replace(' ', 'T') + (String(iso).includes('Z') ? '' : 'Z'),
  ).getTime()
  if (!_ts) return ''
  const diff = Math.max(0, Date.now() - _ts)
  const m = Math.floor(diff / 60000)
  if (m < 1) return t('tc.just_now')
  if (m < 60) return t('tc.mins_ago', { n: m })
  const h = Math.floor(m / 60)
  if (h < 24) return t('tc.hours_ago', { n: h })
  const d = new Date(_ts)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

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
    const _tid = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(_tid)
  }, [cur, st])

  const filename = String(resultInner?.filename || result?.filename || '')
  const lastStep = steps.length ? steps[steps.length - 1] : null
  // 2026-10-17 评估联动任务(cleansing_eval)专属: 窗口推进/评估计数/联动库存数——与导入任务同款
  const isEval = task.task_type === 'cleansing_eval'
  const evalTotal = Number(params.total || (params.sku_list as unknown[] | undefined)?.length || 0)
  const evalOff = Number(params.offset || 0)
  const evalRules = Number(resultInner?.rules_evaluated ?? 0)
  const evalInv = Number(resultInner?.inventory_adjusted ?? 0)
  const evalSeg =
    evalTotal > 0
      ? Math.min(Math.floor(evalOff / 1000) + 1, Math.max(Math.ceil(evalTotal / 1000), 1))
      : 0

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
              {params.driver === 'backend' ? t('tc.backend_run') : t('tc.frontend_poll')}
            </span>
          ) : null}
          {task.created_at ? (
            <span className="task-reltime">{relTime(task.created_at)}</span>
          ) : null}
          <span className={'task-status ' + st}>
            {st === 'running' && <IconRefresh size={11} className="task-spin" />}
            {st === 'done' && <IconCheck size={11} />}
            {st === 'error' && <IconClose size={11} />}
            {st === 'done'
              ? t('tc.done')
              : st === 'error'
                ? t('tc.failed')
                : st === 'running'
                  ? t('tc.running')
                  : t('tc.waiting')}
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
                <span className="task-step-name">
                  {(s.name_key ? t(s.name_key, s.p) : '') || s.name || t('tc.step', { n: i + 1 })}
                </span>
                <span className="task-step-time">
                  {s.elapsed != null ? s.elapsed.toFixed(1) + 's' : '—'}
                </span>
              </div>
            ))}
            {cur && st === 'running' && (
              <div className="task-step-row task-cur">
                <IconRefresh size={10} className="task-spin" />
                <span className="task-step-name">
                  {(cur.name_key ? t(cur.name_key, cur.p) : '') || cur.name || t('tc.processing')}
                </span>
                <span className="task-step-time task-cur-time">
                  {cur.started ? ((now - Number(cur.started) * 1000) / 1000).toFixed(0) + 's' : '…'}
                </span>
              </div>
            )}
          </div>
        )}

        {/* 结果信息(done/error) */}
        {st === 'done' && (
          <div className="task-card-meta task-num">
            {total > 0 && <span>{t('tc.rows', { n: total })}</span>}
            {(success > 0 || failed > 0) && (
              <span>
                {t('tc.success', { n: success })}
                {failed > 0 ? (
                  <span className="task-fail">{t('tc.failed_n', { n: failed })}</span>
                ) : null}
              </span>
            )}
            {elapsed > 0 && <span>{t('tc.elapsed', { n: elapsed })}</span>}
          </div>
        )}
        {isEval && (
          <div className="task-card-meta task-num">
            {st === 'running' && evalTotal > 0 && (
              <span>
                {t('tc.window', {
                  seg: evalSeg,
                  cur: Math.min(evalOff, evalTotal),
                  total: evalTotal,
                })}
              </span>
            )}
            {st === 'done' && (
              <span>
                <b>{evalRules}</b> {t('tc.eval_rules_suffix')}
                {evalInv > 0 ? <span> {t('tc.eval_inv', { n: evalInv })}</span> : null}
              </span>
            )}
          </div>
        )}
        {st === 'error' && (
          <div className="task-card-meta task-err">
            {String(resultInner?.error || result?.error || t('tc.task_failed')).slice(0, 120)}
          </div>
        )}
        {st === 'done' && task.task_type === 'export' && filename && (
          <div className="task-card-meta task-num">
            <button
              className="task-download"
              disabled={downloading}
              onClick={() => onDownload && onDownload(task.task_id, filename)}
            >
              {downloading ? t('tc.downloading') : t('tc.download')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
