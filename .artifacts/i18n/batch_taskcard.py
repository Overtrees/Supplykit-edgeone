LOCALE = {
    "tc.not_started": ("—", "—"),
    "tc.eval_rules_suffix": ("SKU 已评估", "SKU evaluated"),
}

REPL = [
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """const relTime = (iso?: string) => {
  if (!iso) return ''
  const t = new Date(
    String(iso).replace(' ', 'T') + (String(iso).includes('Z') ? '' : 'Z'),
  ).getTime()
  if (!t) return ''
  const diff = Math.max(0, Date.now() - t)
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return m + ' 分钟前'
  const h = Math.floor(m / 60)
  if (h < 24) return h + ' 小时前'
  const d = new Date(t)
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}""",
        """const relTime = (iso?: string) => {
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
}""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """              {params.driver === 'backend' ? '后端推进' : '前端轮询'}""",
        """              {params.driver === 'backend' ? t('tc.backend_run') : t('tc.frontend_poll')}""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """            {st === 'done'
              ? '完成'
              : st === 'error'
                ? '失败'
                : st === 'running'
                  ? '进行中'
                  : '等待'}""",
        """            {st === 'done'
              ? t('tc.done')
              : st === 'error'
                ? t('tc.failed')
                : st === 'running'
                  ? t('tc.running')
                  : t('tc.waiting')}""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """                <span className="task-step-name">{s.name || '步骤 ' + (i + 1)}</span>""",
        """                <span className="task-step-name">
                  {s.name || t('tc.step', { n: i + 1 })}
                </span>""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """                <span className="task-step-name">{cur.name || '处理中'}</span>""",
        """                <span className="task-step-name">{cur.name || t('tc.processing')}</span>""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """            {total > 0 && <span>共 {total} 行</span>}
            {(success > 0 || failed > 0) && (
              <span>
                成功 {success}
                {failed > 0 ? <span className="task-fail"> / 失败 {failed}</span> : null}
              </span>
            )}
            {elapsed > 0 && <span>耗时 {elapsed}s</span>}""",
        """            {total > 0 && <span>{t('tc.rows', { n: total })}</span>}
            {(success > 0 || failed > 0) && (
              <span>
                {t('tc.success', { n: success })}
                {failed > 0 ? (
                  <span className="task-fail">{t('tc.failed_n', { n: failed })}</span>
                ) : null}
              </span>
            )}
            {elapsed > 0 && <span>{t('tc.elapsed', { n: elapsed })}</span>}""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """              <span>
                窗口 {evalSeg} · 已评估 {Math.min(evalOff, evalTotal)}/{evalTotal} SKU
              </span>""",
        """              <span>
                {t('tc.window', {
                  seg: evalSeg,
                  cur: Math.min(evalOff, evalTotal),
                  total: evalTotal,
                })}
              </span>""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """              <span>
                评估 <b>{evalRules}</b> SKU
                {evalInv > 0 ? (
                  <span>
                    {' '}
                    · 联动 <b>{evalInv}</b> 库存
                  </span>
                ) : null}
              </span>""",
        """              <span>
                <b>{evalRules}</b> {t('tc.eval_rules_suffix')}
                {evalInv > 0 ? <span> {t('tc.eval_inv', { n: evalInv })}</span> : null}
              </span>""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """            {String(resultInner?.error || result?.error || '任务失败').slice(0, 120)}""",
        """            {String(resultInner?.error || result?.error || t('tc.task_failed')).slice(0, 120)}""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """              {downloading ? '下载中…' : '下载'}""",
        """              {downloading ? t('tc.downloading') : t('tc.download')}""",
    ),
    (
        "frontend/src/components/tasks/TaskCard.tsx",
        """import { IconCheck, IconClose, IconRefresh, IconClipboard } from '../Icons'""",
        """import { IconCheck, IconClose, IconRefresh, IconClipboard } from '../Icons'
import { t } from '../../locale'""",
    ),
]
