import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useAppStore, type Channel } from './store/useAppStore'
import { clearCache, clearInflight } from './api/client'
import { reportError } from './api/logger'
import { ToastProvider, ToastAutoClear } from './components/Toast'
import { useFreshness } from './components/Freshness'
import OpPill from './components/OpPill'
import { notify } from './api/notify'
import ProductPage from './pages/ProductPage'
import SupplierPage from './pages/SupplierPage'
import InsightsPage from './pages/InsightsPage'
import CleansingPage from './pages/CleansingPage'
import RulesPage from './pages/RulesPage'
import DashboardPage from './pages/DashboardPage'
import ErrorBoundary from './components/ErrorBoundary'
import OrdersPage from './pages/OrdersPage'
import HistoryPage from './pages/HistoryPage'
import InventoryPage from './pages/InventoryPage'
import QualityPage from './pages/QualityPage'
import DevModePage from './pages/DevModePage'
import RecyclePage from './pages/RecyclePage'
import ChannelsPage from './pages/ChannelsPage'
import SettingsPage from './pages/SettingsPage'
import TaskPage from './pages/TaskPage'
import LoginPage from './pages/LoginPage'
import Sidebar from './components/Sidebar'
import Welcome from './components/Welcome'
import AppHeader from './components/AppHeader'
import HistorySheet from './components/hammer/HistorySheet'

import useKeyboard from './hooks/useKeyboard'
import useLightTracker from './hooks/useLightTracker'
import useGlassRefraction from './hooks/useGlassRefraction'
import { t } from './locale'

export const NAV = [
  { id: 'dash', label: t('nav.dash') },
  { id: 'products', label: t('nav.products') },
  { id: 'suppliers', label: t('nav.suppliers') },
  { id: 'orders', label: '订单明细' },
  { id: 'history', label: '历史归档' },
  { id: 'inv', label: t('nav.inv') },
  { id: 'insights', label: t('nav.insights') },
  { id: 'cleansing', label: '数据清洗及导入' },
  { id: 'rules', label: t('nav.rules') },
  { id: 'quality', label: t('nav.quality') },
  { id: 'settings', label: t('nav.settings') },
]

export default function App() {
  useLightTracker()
  useGlassRefraction()
  const [page, setPage] = useState('dash')
  window.__page = page // 渲染时同步当前页(首次加载即正确——ErrorBoundary 隐藏'回到看板'判定)
  const navigateTo = (p: string) => {
    setPage(p)
    clearCache()
    clearInflight()
    const _s = useAppStore.getState()
    if (_s.prodBatch || _s.prodSelIds?.length) {
      _s.setProdBatch(false)
      _s.setProdBatchSel([])
    }
    if (p === 'dash') {
      useAppStore.getState().bumpPageVersion()
    }
    // 离开进销存页清除跳转高亮(一次性定位语义) —— 重进页面不再触发定位检查/残留兜底提示
    if (p !== 'inv') {
      setHighlightSku('')
      setHighlightWarehouse('')
    }
    // 跳转定位用的搜索词(loc_search 标记)在离开进销存页时清理 —— 避免污染产品/供应商等共享搜索的页面
    try {
      if (p !== 'inv' && sessionStorage.getItem('loc_search')) {
        sessionStorage.removeItem('loc_search')
        useAppStore.getState().setHammerSearch('')
      }
    } catch (e) {}
  }
  window.__setPage = (p: string) => {
    window.__page = p

    navigateTo(p)
    closeHammerMenu()
  }
  const [highlightSku, setHighlightSku] = useState('')
  const [highlightWarehouse, setHighlightWarehouse] = useState('')
  const {
    inventory,
    qualityLogs,
    startPolling,
    stopAll,
    channel,
    setChannel,
    channels,
    setChannels,
    hammerData,
    setHammerPanel,
  } = useAppStore()
  const API = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')
  const syncing = useAppStore(s => s.syncing)
  const ops = useAppStore(s => s.ops)
  // 2026-10-30 渠道注册表: 走项目既有「同步中/加载中」机制 + 失败重试 2 次 + 留痕
  //   (配置类数据不缓存 → 禁用/改名即时生效; 首屏文案由 _chLabel 兜底保证正确)
  useEffect(() => {
    let alive = true
    const _chBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')
    // syncing 只 +1/-1 各一次: 重试期间持续显示"同步中"(原实现逐次加减会导致胶囊闪)
    const run = async () => {
      useAppStore.getState().setSyncing(1)
      try {
        for (let attempt = 0; attempt <= 2; attempt += 1) {
          try {
            const r = await fetch(_chBase + '/api/channels?enabled_only=1', {
              headers: { 'X-Auth-Token': localStorage.getItem('c_token') || '' },
            })
            if (!r.ok) throw new Error('HTTP ' + r.status)
            const d: { data?: Channel[] } = await r.json()
            const list: Channel[] = Array.isArray(d && d.data)
              ? (d as { data: Channel[] }).data
              : []
            if (alive && list.length) setChannels(list)
            return
          } catch (e) {
            if (attempt >= 2) {
              if (alive) reportError('api_http_error', '渠道列表加载失败', '', 'warning')
              return
            }
            await new Promise(res => setTimeout(res, 1500 * (attempt + 1)))
          }
        }
      } finally {
        useAppStore.getState().setSyncing(-1)
      }
    }
    run()
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // 当前渠道展示名(数据驱动; 未拉到配置时按 code 兜底)
  // 2026-10-30 兜底文案必须与 channels 注册表一致: 原兜底写 '京东' → 首屏/接口未返回时
  //   胶囊显示"京东", 数据到了才变"京东渠道"(用户感知为"有概率变成京东")
  const _chLabel =
    (channels.find(c => c.code === channel) || {}).name ||
    (channel === 'jd' ? '京东渠道' : channel === 'other' ? '其他渠道' : channel)
  const fresh = useFreshness(channel) // 2026-10-29 数据新鲜度(看板胶囊左侧圆点)
  const [freshOpen, setFreshOpen] = useState(false)
  const syncOld = useAppStore(s => s.syncOld)
  // 2026-10-27 徽标兜底: syncing 卡住(任何遗漏路径)20s 强制归零——防徽标永久显示需手动刷新
  useEffect(() => {
    if (syncing <= 0) return
    const t = setTimeout(() => useAppStore.setState({ syncing: 0 }), 20000)
    return () => clearTimeout(t)
  }, [syncing])
  // 2026-11-03 操作态看守(OpsWatcher): 后端任务不靠前端计时猜, 轮询真实状态。
  //   为什么必须在 App 层而不是规则页: 用户保存后切页, 规则页卸载 → 页面内轮询就没了,
  //   胶囊会永远停在"规则重算中"。挂在 App 层才能跨页 + 刷新后恢复(localStorage)。
  const opsKey = ops.map(o => o.id + ':' + String(o.taskId ?? '')).join('|')
  useEffect(() => {
    const targets = ops.filter(o => o.kind === 'rules' && o.taskId)
    if (!targets.length) return
    let alive = true
    const _hdr = () => {
      try {
        return { 'X-Auth-Token': localStorage.getItem('c_token') || '' }
      } catch {
        return {}
      }
    }
    const check = async () => {
      for (const o of targets) {
        try {
          const r = await fetch(API + '/api/rules/eval-status?task_id=' + o.taskId, {
            headers: _hdr(),
          })
          const j = await r.json()
          if (!alive) return
          const st = j?.data?.status
          if (st === 'done' || st === 'expired') {
            useAppStore.getState().opDone(o.id)
            // 评估写完 alerts 才算"变了" —— 此时通知各页重拉, 看板的"重算中"同步消失
            window.dispatchEvent(new Event('rules-changed'))
          } else if (st === 'error') {
            useAppStore.getState().opDone(o.id)
            notify.error('规则重算失败，告警可能未更新（详情见质量页日志）')
          }
        } catch {
          /* 网络抖动: 保留 op 等下一轮, 不误清 */
        }
      }
    }
    check()
    const t = setInterval(check, 2500)
    return () => {
      alive = false
      clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opsKey, API])
  // 陈旧兜底: 刷新恢复的 op 若后端任务早已消失, 15 分钟后自动清 —— 防"永远处理中"
  useEffect(() => {
    if (!ops.length) return
    const t = setInterval(() => {
      const now = Date.now()
      useAppStore.getState().ops.forEach(o => {
        if (now - o.startedAt > 15 * 60 * 1000) useAppStore.getState().opDone(o.id)
      })
    }, 30000)
    return () => clearInterval(t)
  }, [ops.length])
  // 全局同步徽标(看板页/其他页 header-status 共用——任一拉新/30s 静默进行时显示, 完成自动消失)
  // 文案按场景: syncOld=true(有旧值渲染, 后台拉新)=「同步中」; syncOld=false(首载无旧值, 骨架)=「加载中」
  // 仅图标旋转(animation 挂 svg, 文字不转); 字号继承渠道文本(inherit)
  const syncBadge =
    syncing > 0 ? (
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 5,
          fontSize: 'inherit',
          fontWeight: 'inherit',
          color: 'var(--muted)',
          whiteSpace: 'nowrap',
        }}
      >
        <svg
          width={12}
          height={12}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={3}
          strokeLinecap="round"
          style={{ animation: 'spin 0.9s linear infinite', flexShrink: 0 }}
        >
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
        {syncOld ? '同步中' : '加载中'}
      </span>
    ) : null
  // 2026-11-03 操作态优先于取数态: 后端正在算时不显示"同步中"(那是前端在取数),
  //   两者叠加会让用户以为"一直在加载"。ops 空时回落到原来的同步徽标。
  const headerPill = ops.length > 0 ? <OpPill /> : syncBadge
  // 2026-11-03 顶层提示统一走 notify(Provider 在下方, 这里的 useToast 只会拿到 no-op)
  // 任务完成统一静默刷新(不整页 reload —— 消除闪烁): 清前端缓存 + store 重载 + 事件通知各页
  const refreshAll = () => {
    clearCache()
    clearInflight()
    useAppStore
      .getState()
      .loadAll(1, { refresh: true })
      .catch(() => {})
    window.dispatchEvent(new Event('rules-changed'))
    window.dispatchEvent(new Event('insights-refresh'))
  }
  // 全局后台任务轮询（跨页面、挂后台均有效）
  // 每 3 秒检查 localStorage 任务标记变化，设置页/清洗页提交任务后自动感知启动轮询
  const [taskVersion, setTaskVersion] = useState(0)
  const lastTaskSig = useRef('')
  // 全局异常统一收口(2026-09-15): JS 错误/未处理 Promise 拒绝 → 上报 quality_logs(source=frontend, 开发者层)
  useEffect(() => {
    const onErr = (e: ErrorEvent) => {
      if (e.message)
        reportError('window_error', e.message, (e.filename || '') + ':' + (e.lineno || ''))
    }
    const onRej = (e: PromiseRejectionEvent) => {
      const r = e.reason
      reportError(
        'unhandled_rejection',
        (r && (r.message || String(r))) || 'Promise rejected',
        r && r.stack ? String(r.stack).slice(0, 800) : '',
      )
    }
    window.addEventListener('error', onErr)
    window.addEventListener('unhandledrejection', onRej)
    return () => {
      window.removeEventListener('error', onErr)
      window.removeEventListener('unhandledrejection', onRej)
    }
  }, [])
  useEffect(() => {
    const check = setInterval(() => {
      const seed = (() => {
        try {
          return localStorage.getItem('c_seed_task')
        } catch {
          return null
        }
      })()
      const cleansing = (() => {
        try {
          return JSON.parse(localStorage.getItem('c_cleansing_task') || 'null')
        } catch {
          return null
        }
      })()
      const sig = (seed || '') + '|' + (cleansing ? cleansing.task_id : '')
      if (sig !== lastTaskSig.current) {
        lastTaskSig.current = sig
        setTaskVersion(v => v + 1) // 任务变化时重启轮询
      }
    }, 5000)
    return () => clearInterval(check)
  }, [])
  useEffect(() => {
    const polls = []
    // 种子填充任务
    const seedTask = (() => {
      try {
        return localStorage.getItem('c_seed_task')
      } catch {
        return null
      }
    })()
    if (seedTask) {
      const _opId = 'seed:' + seedTask
      // 2026-11-03 进全局操作态: 跨页可见, 不再只有任务卡能看到
      useAppStore.getState().opAdd({
        id: _opId,
        kind: 'ingest',
        label: '种子数据填充',
        taskId: seedTask,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/seed/fill/status?task_id=' + seedTask, {
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
          if (document.visibilityState === 'hidden') return // 挂后台暂停轮询, 回前台立即补查
          if (d.data?.status === 'done') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_seed_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.success('种子数据填充完成')
            window.dispatchEvent(new Event('seed-done'))
            refreshAll()
          } else if (d.data?.status === 'error' || d.data?.status === 'not_found') {
            // not_found 容错：任务可能刚提交数据库写入有延迟，重试 3 次才清理
            const missCount = (window.__seedMissCount || 0) + 1
            window.__seedMissCount = missCount
            if (d.data?.status === 'not_found' && missCount < 3) {
              return
            }
            clearInterval(poll)
            try {
              localStorage.removeItem('c_seed_task')
            } catch {}
            window.__seedMissCount = 0
            useAppStore.getState().opDone(_opId)
            if (d.data?.status === 'error') notify.error('种子数据填充失败')
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 清洗导入任务
    const cleansingTask = (() => {
      try {
        return JSON.parse(localStorage.getItem('c_cleansing_task') || 'null')
      } catch {
        return null
      }
    })()
    if (cleansingTask && cleansingTask.task_id) {
      const _opId = 'clean:' + cleansingTask.task_id
      useAppStore.getState().opAdd({
        id: _opId,
        kind: 'ingest',
        label: '数据清洗',
        scope: cleansingTask.channel || '',
        taskId: cleansingTask.task_id,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        try {
          const r = await fetch(
            API + '/api/cleansing/status?task_id=' + cleansingTask.task_id + '&page_size=1000', // 小窗(实时进度——大窗 263s 超时积压教训)
            {
              headers: {
                'X-Auth-Token': (() => {
                  try {
                    return localStorage.getItem('c_token')
                  } catch {
                    return ''
                  }
                })(),
              },
            },
          )
          const d = await r.json()
          if (document.visibilityState === 'hidden') return
          // 2026-10-15 App 级续跑: status 续跑一窗 + 写 driver=frontend——任意页面在线=前端推进(不再全靠后端保活)
          try {
            fetch(
              API +
                '/api/cleansing/driver?task_id=' +
                cleansingTask.task_id +
                '&driver=frontend&channel=' +
                (cleansingTask.channel || 'jd'),
              {
                method: 'POST',
                headers: { 'X-Auth-Token': localStorage.getItem('c_token') || '' },
              },
            )
          } catch {}
          if (d.status === 'done') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_cleansing_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.success('数据清洗完成')
            refreshAll()
          } else if (d.status === 'error') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_cleansing_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.error('数据清洗失败')
          } else if (d.status === 'not_found') {
            // 容错：刚提交的任务可能还没入库，重试 3 次
            const missCount = (window.__cleanMissCount || 0) + 1
            window.__cleanMissCount = missCount
            if (missCount < 3) return
            clearInterval(poll)
            try {
              localStorage.removeItem('c_cleansing_task')
            } catch {}
            window.__cleanMissCount = 0
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 重置任务轮询
    const resetTask = (() => {
      try {
        return localStorage.getItem('c_reset_task')
      } catch {
        return null
      }
    })()
    if (resetTask) {
      const _opId = 'reset:' + resetTask
      useAppStore.getState().opAdd({
        id: _opId,
        kind: 'ingest',
        label: '数据重置',
        taskId: resetTask,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/seed/fill/status?task_id=' + resetTask, {
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
          if (document.visibilityState === 'hidden') return
          if (d.data?.status === 'done' || d.data?.status === 'error') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_reset_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            if (d.data?.status === 'done') {
              notify.success('数据重置完成')
              refreshAll()
            }
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 导出任务轮询
    const exportTask = (() => {
      try {
        return JSON.parse(localStorage.getItem('c_export_task') || 'null')
      } catch {
        return null
      }
    })()
    if (exportTask && exportTask.task_id) {
      const _opId = 'export:' + exportTask.task_id
      useAppStore.getState().opAdd({
        id: _opId,
        kind: 'ingest',
        label: '数据导出',
        taskId: exportTask.task_id,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        try {
          const r = await fetch(API + '/api/seed/fill/status?task_id=' + exportTask.task_id, {
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
          if (document.visibilityState === 'hidden') return
          if (d.data?.status === 'done') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_export_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.success('导出完成，可在质量日志查看下载')
          } else if (d.data?.status === 'error') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_export_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.error('导出失败')
          }
        } catch {}
      }, 5000)
      polls.push(poll)
    }
    // 页面从后台回到前台时立即检查任务状态（不等下一次轮询）
    const onVis = () => {
      if (document.visibilityState === 'visible') setTaskVersion(v => v + 1)
    }
    document.addEventListener('visibilitychange', onVis)
    window.addEventListener('focus', onVis)
    return () => {
      polls.forEach(p => clearInterval(p))
      document.removeEventListener('visibilitychange', onVis)
      window.removeEventListener('focus', onVis)
    }
  }, [taskVersion])
  const [apiStatus, setApiStatus] = useState('checking')
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState([])
  const [histLoading, setHistLoading] = useState(false)
  const [showWelcome, setShowWelcome] = useState(() => {
    try {
      return !localStorage.getItem('c_welcome_seen')
    } catch {
      return false
    }
  })
  const [loggedIn, setLoggedIn] = useState(() => {
    try {
      return !!localStorage.getItem('c_token')
    } catch {
      return false
    }
  })
  // 401 确认后再回登录页(10-26: 防偶发 401 误踢——无痕冷实例/慢网络单接口 401 曾直接踢出清空输入;
  // 任何接口标记 may-expired → check 确认(真失效才 setLoggedIn(false)), 10s 限频防并发风暴)
  useEffect(() => {
    let _checking = false
    let _last = 0
    const onMay = async () => {
      const now = Date.now()
      if (_checking || now - _last < 10000) return
      _checking = true
      _last = now
      try {
        const token = (() => {
          try {
            return localStorage.getItem('c_token')
          } catch {
            return ''
          }
        })()
        if (!token) return
        const r = await fetch(API + '/api/auth/check', { headers: { 'X-Auth-Token': token } })
        let ok = r.ok
        if (r.status === 200) {
          try {
            const d = await r.json()
            if (d && d.ok === false) ok = false
          } catch {}
        }
        if (!ok) {
          try {
            localStorage.removeItem('c_token')
          } catch {}
          setLoggedIn(false)
        }
      } catch {
        // check 网络失败不判定(偶发网络——不误踢)
      } finally {
        _checking = false
      }
    }
    window.addEventListener('auth:may-expired', onMay)
    return () => window.removeEventListener('auth:may-expired', onMay)
  }, [])

  // 启动时验证 token 有效性（失效则清除并显示登录页）
  useEffect(() => {
    if (!loggedIn) return
    const token = (() => {
      try {
        return localStorage.getItem('c_token')
      } catch {
        return null
      }
    })()
    if (!token) {
      setLoggedIn(false)
      return
    }
    fetch(API + '/api/auth/check', { headers: { 'X-Auth-Token': token } })
      .then(async r => {
        // 401/500/SW offline 兜底(200 {ok:false}) 一律按失效处理 → 回登录页
        let ok = r.ok
        if (r.status === 200) {
          try {
            const d = await r.json()
            if (d && d.ok === false) ok = false
          } catch {}
        }
        if (!ok) {
          try {
            localStorage.removeItem('c_token')
          } catch {}
          setLoggedIn(false)
        }
      })
      .catch(() => {
        // 网络失败(挂机/后台恢复瞬间)也回登录页——避免卡'加载失败'反复提示
        try {
          localStorage.removeItem('c_token')
        } catch {}
        setLoggedIn(false)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const loadHistory = useCallback(
    async ch => {
      setShowHistory(true)
      setHistLoading(true)
      try {
        const API = import.meta.env.VITE_API_BASE_URL || ''
        const r = await fetch(
          API + '/api/replenishment-config/history?channel=' + (ch || channel) + '&limit=50',
          {
            headers: {
              'X-Auth-Token': (() => {
                try {
                  return localStorage.getItem('c_token')
                } catch {
                  return ''
                }
              })(),
            },
          },
        )
        const d = await r.json()
        setHistory(d.data || [])
      } catch (e) {
        setHistory([])
      }
      setHistLoading(false)
    },
    [channel],
  )
  const checkApi = useCallback(async () => {
    try {
      const ctrl = new AbortController()
      setTimeout(() => ctrl.abort(), 5000)
      const r = await fetch(API + '/api/insights/ping', { signal: ctrl.signal })
      const d = await r.json()
      setApiStatus(d.ok ? 'ok' : 'slow')
    } catch {
      setApiStatus('error')
    }
  }, [])
  useEffect(() => {
    checkApi()
    const t = setInterval(checkApi, 30000)
    return () => clearInterval(t)
  }, [checkApi])

  // 数据版本轮询：后端_ cache_version 变化时自动刷新
  const [, setDbVersion] = useState(0)
  const versionRef = useRef(0)
  useEffect(() => {
    const poll = setInterval(async () => {
      try {
        const r = await fetch(API + '/api/health')
        const d = await r.json()
        const v = d.version || 0
        if (versionRef.current !== 0 && versionRef.current !== v) {
          // 版本变化，清除前端缓存
          clearCache()
          useAppStore
            .getState()
            .loadAll()
            .catch(() => {})
        }
        versionRef.current = v
        setDbVersion(v)
      } catch {}
    }, 30000)
    return () => clearInterval(poll)
  }, [])

  const [showMenu, setShowMenu] = useState(false)
  const [menuClosing, setMenuClosing] = useState(false)
  const menuCloseTimerRef = useRef(null)
  const [showHammerMenu, setShowHammerMenu] = useState(false)
  const [hammerMenuClosing, setHammerMenuClosing] = useState(false)
  const hammerMenuTimerRef = useRef(null)

  const openEditorMenu = useCallback(() => {
    clearTimeout(menuCloseTimerRef.current)
    setMenuClosing(true)
    setShowMenu(true)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setMenuClosing(false))
    })
  }, [])

  const closeEditorMenu = useCallback(() => {
    clearTimeout(menuCloseTimerRef.current)
    setMenuClosing(true)
    menuCloseTimerRef.current = setTimeout(() => {
      setShowMenu(false)
      setMenuClosing(false)
    }, 220)
  }, [])

  const toggleEditorMenu = useCallback(() => {
    if (showMenu && !menuClosing) closeEditorMenu()
    else openEditorMenu()
  }, [showMenu, menuClosing, closeEditorMenu, openEditorMenu])

  const openHammerMenu = useCallback(() => {
    clearTimeout(hammerMenuTimerRef.current)
    setHammerMenuClosing(true)
    setShowHammerMenu(true)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => setHammerMenuClosing(false))
    })
  }, [])

  const closeHammerMenu = useCallback(() => {
    clearTimeout(hammerMenuTimerRef.current)
    setHammerMenuClosing(true)
    setHammerPanel(null)
    hammerMenuTimerRef.current = setTimeout(() => {
      setShowHammerMenu(false)
      setHammerMenuClosing(false)
    }, 220)
  }, [setHammerPanel])

  const toggleHammerMenu = useCallback(() => {
    if (showHammerMenu && !hammerMenuClosing) closeHammerMenu()
    else openHammerMenu()
  }, [showHammerMenu, hammerMenuClosing, closeHammerMenu, openHammerMenu])

  const hammerMenuRef = useRef(null)

  useEffect(() => {
    if (!showHammerMenu) return
    const handler = e => {
      if (
        hammerMenuRef.current &&
        !hammerMenuRef.current.contains(e.target) &&
        !e.target.closest('.hammer-icon-btn') &&
        !e.target.closest('.history-sheet')
      ) {
        closeHammerMenu()
      }
    }
    setTimeout(() => document.addEventListener('pointerdown', handler), 0)
    return () => document.removeEventListener('pointerdown', handler)
  }, [showHammerMenu, closeHammerMenu])

  useEffect(() => {
    if (!showMenu) return
    const close = () => closeEditorMenu()
    window.addEventListener('scroll', close, { passive: true })
    return () => window.removeEventListener('scroll', close)
  }, [showMenu, closeEditorMenu])

  const navAndClose = useCallback(
    (id, sku?) => {
      closeEditorMenu()
      if (sku) setHighlightSku(sku)
      navigateTo(id)
    },
    [closeEditorMenu],
  )

  useKeyboard({
    'meta+b': () => toggleEditorMenu(),
    esc: () => {
      if (showMenu) closeEditorMenu()
    },
  })
  useEffect(() => {
    startPolling()
    return () => stopAll()
  }, [])
  // channel 切换时自动加载数据（不再依赖 page——切页由各页面挂载自拉, 避免每次切页全量 loadAll 含慢接口）
  useEffect(() => {
    useAppStore
      .getState()
      .loadAll()
      .catch(() => {})
  }, [channel])

  // 同步 html/body 背景色 + browser chrome 色
  useEffect(() => {
    const themeMeta = document.querySelector('meta[name="theme-color"]')
    if (themeMeta) {
      const resolved = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
      themeMeta.setAttribute('content', resolved)
    }
  }, [])
  // 监听系统主题变化，更新 theme-color
  useEffect(() => {
    const syncMeta = () => {
      const themeMeta = document.querySelector('meta[name="theme-color"]')
      if (themeMeta) {
        const resolved = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
        themeMeta.setAttribute('content', resolved)
      }
    }
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    mq.addEventListener('change', syncMeta)
    return () => mq.removeEventListener('change', syncMeta)
  }, [])

  const navigate = useCallback((newPage, sku, whType, wh) => {
    if (sku) {
      setHighlightSku(sku)
      // 进销存按 SKU 搜索定位: 目标行可能在分页深处(100条/页), 搜索后必在当前结果, 高亮+滚动才可达
      useAppStore.getState().setHammerSearch(sku)
      // 标记定位搜索(离开进销存页时自动清理, 不污染其他共享搜索的页面)
      try {
        sessionStorage.setItem('loc_search', sku)
      } catch (e) {}
    }
    if (wh !== undefined) setHighlightWarehouse(wh || '') // ''=聚合高亮(bc 行跳 C 仓全仓行), 空则清残留
    // 从告警跳进销存时同步切到对应仓库维度(own/platform/platform_b), 保证高亮可见
    if (whType === 'own' || whType === 'platform' || whType === 'platform_b') {
      useAppStore.getState().setHammerWhType(whType)
    }
    navigateTo(newPage)
  }, [])

  const lowStock = (inventory || []).filter(
    x => Number(x.available_qty) < Number(x.safety_qty),
  ).length
  const errCount = (qualityLogs || []).length

  // 2026-10-14: 回前台立即续跑 running 导入/填充/导出任务(后端 continue-tasks 兜底 + 前端恢复接力)
  useEffect(() => {
    const cont = () => {
      fetch(API + '/api/cron/continue-tasks?task=manual', {
        headers: {
          'X-Auth-Token': (() => {
            try {
              return localStorage.getItem('c_token') || ''
            } catch {
              return ''
            }
          })(),
        },
      }).catch(() => {})
    }
    // 2026-11-02 S0: 原写 addEventListener(..., 匿名函数) 但 removeEventListener(..., cont) ——
    // 引用不同, 移除永不生效。每次 App 重挂载都多一个常驻监听。
    const onVisible = () => {
      if (document.visibilityState === 'visible') cont()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])
  const renderPage = pageId => {
    const wrap = el => <ErrorBoundary key={pageId}>{el}</ErrorBoundary>
    switch (pageId) {
      case 'dash':
        return wrap(
          <DashboardPage
            key={pageId}
            onAlert={(s, wt, wh) => {
              navigate('inv', s, wt, wh)
            }}
            onGoInsights={(tab, sku) => {
              useAppStore.getState().setHammerInsightsTab(tab)
              if (sku) {
                const _m = useAppStore.getState().hammerReplenMode
                const _k = tab === 'purchase' ? 'purchase' : tab === 'slow' ? 'slow' : _m
                useAppStore.getState().setHammerData('insights_search_' + _k, sku)
              }
              navigateTo('insights')
            }}
          />,
        )
      case 'products':
        return wrap(<ProductPage key={pageId} />)
      case 'suppliers':
        return wrap(<SupplierPage key={pageId} />)
      case 'orders':
        return wrap(<OrdersPage key={pageId} />)
      case 'history':
        return wrap(<HistoryPage key={pageId} />)
      case 'inv':
        return wrap(
          <InventoryPage
            key={pageId}
            highlightSku={highlightSku || ''}
            highlightWarehouse={highlightWarehouse || ''}
          />,
        )
      case 'insights':
        return wrap(<InsightsPage key={pageId} />)
      case 'cleansing':
        return wrap(<CleansingPage key={pageId} />)
      case 'rules':
        return wrap(<RulesPage key={pageId} />)
      case 'quality':
        return wrap(<QualityPage key={pageId} />)
      case 'tasks':
        return wrap(<TaskPage key={pageId} />)
      case 'settings':
        return wrap(<SettingsPage key={pageId} />)
      case 'devmode':
        return wrap(<DevModePage key={pageId} />)
      case 'channels':
        return wrap(<ChannelsPage key={pageId} />)
      case 'recycle':
        return wrap(<RecyclePage key={pageId} />)
      default:
        return null
    }
  }

  return (
    <>
      {!loggedIn ? (
        <LoginPage
          onLogin={() => {
            try {
              localStorage.removeItem('c_welcome_seen')
            } catch {}
            setShowWelcome(true)
            setLoggedIn(true)
            clearCache()
            clearInflight()
            useAppStore
              .getState()
              .loadAll()
              .catch(() => {})
          }}
        />
      ) : (
        <ToastProvider>
          <ToastAutoClear page={page} />
          {/* 主内容 — 侧边栏打开时显示菜单，关闭时显示页面 */}
          <AppHeader
            page={page}
            channel={channel}
            setChannel={setChannel}
            channels={channels}
            fresh={fresh}
            freshOpen={freshOpen}
            setFreshOpen={setFreshOpen}
            headerPill={headerPill}
            showWelcome={showWelcome}
            showHammerMenu={showHammerMenu}
            hammerMenuClosing={hammerMenuClosing}
            hammerMenuRef={hammerMenuRef}
            toggleHammerMenu={toggleHammerMenu}
            closeHammerMenu={closeHammerMenu}
            toggleEditorMenu={toggleEditorMenu}
            navigateTo={navigateTo}
            loadHistory={loadHistory}
            hammerData={hammerData}
            _chLabel={_chLabel}
          />
          <Sidebar
            page={page}
            onClose={closeEditorMenu}
            onNavigate={navAndClose}
            lowStock={lowStock}
            errCount={errCount}
            apiStatus={apiStatus}
            open={showMenu}
            menuClosing={menuClosing}
            onBackdrop={closeEditorMenu}
          />
          {showWelcome && <Welcome close={() => setShowWelcome(false)} />}
          <main
            className="container"
            style={{ display: showWelcome ? 'none' : '', animation: 'fadeIn 0.2s ease' }}
            key={page}
          >
            {renderPage(page)}
          </main>

          {/* 变更历史底部弹窗 */}
          <HistorySheet
            show={showHistory}
            loading={histLoading}
            data={history}
            onClose={() => setShowHistory(false)}
          />
        </ToastProvider>
      )}
    </>
  )
}
