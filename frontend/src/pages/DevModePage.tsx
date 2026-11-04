import React, { useState, useEffect } from 'react'
import { api, apiJson } from '../api/client'
import { useAppStore } from '../store/useAppStore'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { clearCache, clearInflight } from '../api/client'
import { Group, Row, LastRow } from '../components/ListGroup'
import LogFileList from '../components/LogFileList'
import { t } from '../locale'
import { todayCn } from '../utils/date'

const VERSION =
  typeof __APP_VERSION__ !== 'undefined' && __APP_VERSION__ ? __APP_VERSION__ : '2.0.0'
const BUILD = typeof __BUILD_DATE__ !== 'undefined' && __BUILD_DATE__ ? __BUILD_DATE__ : ''
const API = import.meta.env.VITE_API_BASE_URL || ''

/** 运行概况 / 质量日志行 / 种子操作响应(动态字段走 unknown 索引签名) */
interface MonitorResp {
  totals?: { error?: number; slow?: number }
  today?: { error?: number; slow?: number }
  [k: string]: unknown
}
interface QualityLogRow {
  created_at?: string
  [k: string]: unknown
}
interface SeedActionData {
  requires_reset?: boolean
  task_id?: string
  [k: string]: unknown
}
interface SeedActionResp {
  ok?: boolean
  error?: string
  data?: SeedActionData
  [k: string]: unknown
}

// 2026-11-04 日志排查清理: 原 DEV_LABEL / LEVEL_LABEL(12 个 dev.k_* / dev.lv_* 词条键)
// 是日志明细改成"按天文件 + Markdown 预览"后遗留的死代码 —— 两个常量定义后从未被引用,
// 对应 12 个词条也成了死键。类型/级别标签现在由后端 _export_md 在 Markdown 里直接给出。

/** 开发者模式页(设置页·点版本号 6 次开启): 连接状态/环境信息/运行概况/日志分析/种子数据(开发工具) */
export default function DevModePage() {
  const toast = useToast() // DevModePage 在 App ToastProvider 内, 可用
  const { wsStatus } = useAppStore()
  const [status, setStatus] = useState(() => t('dev.checking'))
  const [ping, setPing] = useState(0)
  const [lastCheck, setLastCheck] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [mon, setMon] = useState<MonitorResp | null>(null)
  const [showLogs, setShowLogs] = useState(false)
  const [todayLogs, setTodayLogs] = useState<number | null>(null) // 2026-10-27 日志分析角标(今日条数)
  // 种子数据(从设置页移入): 填充/重置为数据操作工具, 归开发者维度
  const [confirm, setConfirm] = useState(null) // {type:'fill'|'reset'}
  const [seeding, setSeeding] = useState(() => {
    try {
      return !!localStorage.getItem('c_seed_task')
    } catch {
      return false
    }
  })
  const [resetting, setResetting] = useState(false)
  useEffect(() => {
    const h = () => {
      setSeeding(false)
    }
    window.addEventListener('seed-done', h)
    window.addEventListener('seed-error', h)
    return () => {
      window.removeEventListener('seed-done', h)
      window.removeEventListener('seed-error', h)
    }
  }, [])

  const checkConnection = async () => {
    setRefreshing(true)
    const start = performance.now()
    try {
      // S3-2 收口: 探测 5s 超时(原裸 fetch 无超时)
      const d = await apiJson<{ ok?: boolean }>('GET', API + '/api/insights/ping', {
        timeout: 5000,
      })
      const ms = Math.round(performance.now() - start)
      setStatus(d.ok ? t('dev.ok') : t('dev.abnormal'))
      setPing(ms)
      setLastCheck(new Date().toLocaleTimeString())
    } catch {
      setStatus(t('dev.unreachable'))
      setPing(0)
      setLastCheck(new Date().toLocaleTimeString())
    }
    setRefreshing(false)
  }

  useEffect(() => {
    checkConnection()
    api
      .get<MonitorResp>('/api/monitor')
      .then(r => setMon(r.data))
      .catch(() => {})
    // 今日开发者日志条数(角标, 提升日志分析可发现性)
    api
      .get<QualityLogRow[]>('/api/quality-logs?scope=dev&limit=200')
      .then(r => {
        const _today = todayCn()
        const _n = (r.data || []).filter(
          x => String(x.created_at || '').slice(0, 10) === _today,
        ).length
        setTodayLogs(_n)
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const doSeed = async () => {
    setConfirm(null)
    setSeeding(true)
    try {
      // S3-2 收口: 填充是重操作, 超时对齐 postHeavy 90s
      const d = await apiJson<SeedActionResp>('POST', API + '/api/seed/fill', { timeout: 90000 })
      if (d.ok) {
        if (d.data?.requires_reset) {
          toast.error(t('dev.has_data'))
          setSeeding(false)
          setConfirm('reset')
          return
        }
        const taskId = d.data?.task_id
        if (taskId) {
          try {
            localStorage.setItem('c_seed_task', taskId)
          } catch {}
        }
        toast.add({
          type: 'success',
          title: t('dev.seed_submitted'),
          duration: 6000,
          action: {
            label: t('dev.view_progress'),
            handler: () => {
              window.__setPage && window.__setPage('tasks')
            },
          },
        })
      } else {
        toast.error(t('dev.seed_failed', { msg: d.error || '' }))
        setSeeding(false)
      }
    } catch {
      toast.error(t('dev.seed_failed_plain'))
      setSeeding(false)
    }
  }

  const doReset = async () => {
    setConfirm(null)
    setResetting(true)
    try {
      // S3-2 收口: 重置是重操作, 超时对齐 postHeavy 90s
      const d = await apiJson<SeedActionResp>('POST', API + '/api/seed/reset', { timeout: 90000 })
      if (d.ok && d.data?.task_id) {
        try {
          localStorage.setItem('c_reset_task', d.data.task_id)
        } catch {}
        toast.success(t('dev.reset_submitted'))
        // 轮询等待重置完成
        const poll = setInterval(async () => {
          try {
            const sr = await apiJson<Response>(
              'GET',
              API + '/api/seed/fill/status?task_id=' + d.data.task_id,
            )
            const sd = await sr.json()
            if (sd.data?.status === 'done' || sd.data?.status === 'error') {
              clearInterval(poll)
              try {
                localStorage.removeItem('c_reset_task')
              } catch {}
              clearCache()
              clearInflight()
              // 2026-10-29 修复: 仅清 dashboard/alerts/stockRisk 不够 ——
              // ・procList(补货采购卡)、replen(建议页)是页面内 state, 不在 store
              // ・未递增 pageVersion → 页面不会重新拉取 → 显示旧数据(用户需手动刷新)
              useAppStore.setState({
                dashboard: null,
                alerts: [],
                stockRisk: [],
                qualityLogs: [],
                dataLoaded: false,
                pageVersion: Date.now(), // 触发各页 useEffect 重新拉取
              })
              try {
                // 通知所有页面级快照失效(建议页/进销存的 module 快照)
                window.dispatchEvent(new CustomEvent('insights-refresh'))
                window.dispatchEvent(new CustomEvent('orders-changed'))
              } catch {}
              toast.success(t('dev.reset_done'))
              setTimeout(() => window.location.reload(), 400)
            }
          } catch {
            clearInterval(poll)
            setResetting(false)
          }
        }, 2000)
      } else {
        toast.error(t('dev.reset_failed', { msg: d.error || '' }))
        setResetting(false)
      }
    } catch {
      toast.error(t('dev.reset_failed_plain'))
      setResetting(false)
    }
  }

  return (
    <div className="form-page">
      {/* 连接状态(从设置页移入) */}
      <Group title={t('dev.conn')}>
        <Row
          label={t('dev.backend')}
          value={status}
          sub={`${ping}ms · ${lastCheck}`}
          onClick={checkConnection}
          loading={refreshing}
        />
        <LastRow
          label={t('dev.realtime')}
          value={
            wsStatus === 'connected'
              ? t('dev.connected')
              : wsStatus === 'polling'
                ? t('dev.polling')
                : t('dev.disconnected')
          }
        />
      </Group>

      {/* 环境信息(版本已在设置页, 此处只留构建/API/DB) */}
      <Group title={t('dev.env')}>
        <Row label={t('dev.build_date')} value={BUILD || '-'} />
        <Row label="API" value={API || t('dev.same_origin')} />
        <LastRow label="DB" value="TiDB · EdgeOne Makers" />
      </Group>

      {/* 运行概况(monitor 汇总) —— 2026-10-27 抽 StatsRow 组件(曾 4×40 行重复内联) */}
      <Group title={t('dev.overview')}>
        <StatsRow
          items={[
            {
              n: mon ? mon.totals?.error || 0 : '-',
              c: t('dev.total_errors'),
              color: 'var(--danger)',
            },
            { n: mon ? mon.totals?.slow || 0 : '-', c: t('dev.slow_req'), color: 'var(--warning)' },
            {
              n: mon ? mon.today?.error || 0 : '-',
              c: t('dev.today_errors'),
              color: 'var(--text)',
            },
            { n: mon ? mon.today?.slow || 0 : '-', c: t('dev.today_slow'), color: 'var(--text)' },
          ]}
        />
        <div
          style={{
            padding: '0 16px 14px',
            fontSize: 'var(--font-10)',
            color: 'var(--muted2)',
            lineHeight: 1.5,
          }}
        >
          {t('dev.overview_note')}
        </div>
      </Group>

      {/* 日志分析入口 */}
      <Group title={t('dev.log_analysis')}>
        <LastRow
          label={t('dev.log_analysis')}
          sub={t('dev.log_analysis_sub')}
          onClick={() => setShowLogs(true)}
        />
      </Group>

      {/* 种子数据(从设置页移入): 开发/运维数据工具 */}
      <Group title={t('dev.seed')}>
        <Row
          label={t('dev.seed_fill')}
          sub={t('dev.seed_fill_sub')}
          onClick={() => setConfirm('fill')}
          loading={seeding}
        />
        <LastRow
          label={t('dev.seed_reset')}
          sub={t('dev.seed_reset_sub')}
          onClick={() => setConfirm('reset')}
          danger
          loading={resetting}
        />
      </Group>

      {/* 日志分析底部弹窗(标准 sheet) */}
      {showLogs && (
        <>
          <div
            onClick={() => setShowLogs(false)}
            style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }}
          />
          <div
            style={{
              position: 'fixed',
              left: 0,
              right: 0,
              bottom: 'calc(env(safe-area-inset-bottom) + 14px)',
              zIndex: 9999,
              display: 'flex',
              justifyContent: 'center',
              padding: '0 14px',
              pointerEvents: 'none',
            }}
          >
            <div
              onClick={e => e.stopPropagation()}
              className="material-regular"
              style={{
                width: '100%',
                maxWidth: 600,
                borderRadius: 'var(--radius-lg)',
                padding: '18px 14px calc(14px + env(safe-area-inset-bottom))',
                boxShadow: 'var(--shadow-sheet)',
                pointerEvents: 'auto',
                maxHeight: '70vh',
                overflowY: 'auto',
              }}
            >
              <div
                style={{
                  fontSize: 'var(--font-18)',
                  fontWeight: 'var(--fw-bold)',
                  marginBottom: 12,
                  textAlign: 'center',
                  color: 'var(--text)',
                }}
              >
                {t('dev.log_analysis')}
              </div>
              <LogFileList scope="dev" />
            </div>
          </div>
        </>
      )}

      {/* 种子数据确认弹窗 */}
      {confirm === 'fill' && (
        <ConfirmDialog
          open
          title={t('dev.confirm_fill_title')}
          desc={t('dev.confirm_fill_desc')}
          confirmLabel={t('dev.confirm_fill_ok')}
          onConfirm={doSeed}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'reset' && (
        <ConfirmDialog
          open
          title={t('dev.confirm_reset_title')}
          desc={t('dev.confirm_reset_desc')}
          confirmLabel={t('dev.confirm_reset_ok')}
          onConfirm={doReset}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}

/** 运行概况统计块(2026-10-27 抽离: 数字 24px tabular + 标签 11px, 消除 4 处重复内联) */
function StatsRow({ items }: { items: { n: number | string; c: string; color: string }[] }) {
  return (
    <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', padding: '10px 16px 12px' }}>
      {items.map((it, i) => (
        <div
          key={i}
          style={{
            flex: 1,
            minWidth: 80,
            background: 'var(--bg)',
            borderRadius: 'var(--radius-md)',
            padding: '10px 8px',
            textAlign: 'center',
          }}
        >
          <div
            style={{
              fontSize: 24,
              fontWeight: 'var(--fw-bold)',
              lineHeight: 1.1,
              color: it.color,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {it.n}
          </div>
          <div style={{ fontSize: 'var(--font-10)', color: 'var(--muted2)', marginTop: 3 }}>
            {it.c}
          </div>
        </div>
      ))}
    </div>
  )
}
