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
  /** 2026-11-05 进度心跳: 每续跑一步由后端 NOW() 刷新(见 task_seed.seed_fill_status) */
  updated_at?: string
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
const relTime = (iso?: string, nowMs?: number) => {
  if (!iso) return ''
  // 注意: 变量名不能叫 t —— 会遮蔽 locale 的 t(), 相对时间文案就永远出中文
  // 2026-11-05 修「跑了很久还显示刚刚」的第三种成因: 后端列是 DATETIME, 驱动/proxy 可能
  //   把它序列化成 "2026-11-05T08:30:00"(带 T 但不带时区后缀), 也可能带 "+08:00"。
  //   原实现只判了 'Z': 带 T 的会被下面补一个 'Z' → 当成 UTC 解析 → 差 8 小时;
  //   而如果字符串里已经有 +08:00, 再补 'Z' 就直接解析失败, getTime() 得 NaN, 塌成
  //   「刚刚」。统一做法: 先归一成标准形式, 再按"本地时区无偏移"处理(服务端已按北京时间)。
  const raw = String(iso).trim()
  let s = raw.replace(' ', 'T')
  if (!/(?:Z|[+-]\d{2}:?\d{2})$/.test(s)) {
    // 无时区后缀 → 语义上是北京时间, 显式补 +08:00(不能用 Z, 那是 UTC)
    s += '+08:00'
  }
  const _ts = new Date(s).getTime()
  if (!_ts) return ''
  const diff = Math.max(0, (nowMs ?? Date.now()) - _ts)
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
  // 2026-11-05 修「卡片一直显示刚刚」的**真正主因**: 任务还挂着(running)但这次轮询没有推进
  //   (卡在长步骤里 / 另一个实例持有租约 / 等前端下一次续跑), 后端 params 里就没有 cur_step,
  //   而卡片只认 params.step —— 此时卡片什么都不显示, 用户只看到右上角一个不动的「刚刚」。
  //   真正的问题是: 一条在跑的任务, 卡片必须能表达"它现在走到哪了、跑了多久", 不能依赖
  //   "这一秒刚好在推进"。steps 里最后一条就是已完成的最新步骤; 没有 steps 时用 title 兜底。
  const _lastStepName = steps.length
    ? steps[steps.length - 1].name_key
      ? t(String(steps[steps.length - 1].name_key), steps[steps.length - 1].p)
      : String(steps[steps.length - 1].name || '')
    : ''

  // 进度 %: cleansing/export 用 offset/total; seed 用 step/总段数
  // 2026-11-05 修: seed 总段数写死 10 —— 段号重排后已完成值是 12(订单 6 段 + 库存/流水/落账/
  //   月汇总/尾段), 写死 10 会让进度条在 step>10 时顶到 99% 停住、看起来"卡在最后一步"。
  //   后端把总步数放在 params.total_step 里下发; 没有该字段(老任务)时按 10 兜底。
  const seedTotalStep = Number(params.total_step || 0) || 10
  let pct = 0
  if (st === 'done') pct = 100
  else if (total > 0) pct = Math.min(Math.round((offset / total) * 100), 99)
  else if (params.step !== undefined)
    pct = Math.min(Math.round((progStep / seedTotalStep) * 100), 99)

  // 进行中步骤实时耗时(本地 1s 节流——now - started)
  // 2026-11-05: 这个 now 同时驱动右上角相对时间 —— 一张卡片只挂**一个**定时器,
  //   而不是"耗时用 1s、相对时间用 30s"两个(卡多时定时器翻倍)。
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (st !== 'running') return
    const _tid = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(_tid)
  }, [st])
  // 2026-11-05 修「任务跑了几分钟还显示刚刚」的两层根因:
  //   ① 数据层: 卡片读的是 created_at(任务**创建**时刻), seed 分步长跑每步推进不改它 →
  //      无论前端刷多勤, 这个时间永远是"创建时"的那个时刻, 差值为 0 → 恒「刚刚」。
  //      后端 /api/tasks 已补 updated_at(每续跑一步 NOW() 刷新 = 进度心跳), 优先用它。
  //   ② 渲染层: 组件不重渲染时 relTime 不会重算。上面那个 1s 的 now 一旦进函数体,
  //      组件每秒都会重渲染, 相对时间自然跟着走。
  //   idle/pending 没有心跳, 退回 created_at(此时确实只有创建时刻可讲)。
  const _stamp = st === 'running' && task.updated_at ? task.updated_at : task.created_at
  // 心跳年龄(秒): 由 1s 时钟驱动, 与 relTime 共用同一个 now —— 只服务"不推进时仍能看见在跑"
  const _stampMs = (() => {
    const _r = String(_stamp || '').trim()
    if (!_r) return 0
    const _s = _r.replace(' ', 'T') + (/(?:Z|[+-]\d{2}:?\d{2})$/.test(_r) ? '' : '+08:00')
    const _v = new Date(_s).getTime()
    return _v || 0
  })()
  const _beatAge = _stampMs ? Math.max(0, Math.round((now - _stampMs) / 1000)) : 0
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
            <span className="task-reltime" title={task.updated_at || task.created_at}>
              {st === 'running' && _beatAge >= 1
                ? t('tc.running_for', { n: _beatAge })
                : relTime(_stamp, now)}
            </span>
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
            {/* 2026-11-05: running 但没有 cur_step(本次轮询没推进) → 显示"最后完成的步骤 + 已运行时长",
                而不是一片空白。空白的后果就是用户只能盯着右上角那个相对时间, 误以为卡住了。 */}
            {!cur && st === 'running' && _lastStepName && (
              <div className="task-step-row task-cur">
                <IconRefresh size={10} className="task-spin" />
                <span className="task-step-name">{_lastStepName}</span>
                <span className="task-step-time task-cur-time">
                  {task.updated_at || task.created_at
                    ? Math.max(
                        0,
                        Math.round(
                          (now -
                            new Date(
                              String(task.updated_at || task.created_at)
                                .trim()
                                .replace(' ', 'T') +
                                (/(?:Z|[+-]\d{2}:?\d{2})$/.test(
                                  String(task.updated_at || task.created_at),
                                )
                                  ? ''
                                  : '+08:00'),
                            ).getTime()) /
                            1000,
                        ),
                      ) + 's'
                    : '…'}
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
