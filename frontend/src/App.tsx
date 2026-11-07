import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useAppStore, type Channel } from './store/useAppStore'
import {
  clearCache,
  clearInflight,
  apiJson,
  activeRequests,
  onActiveRequests,
  abortAll,
  setView,
  currentView,
} from './api/client'
import { adoptRunning, onWatchChange, watchCount } from './api/taskWatch'
import { registerPoll, POLL_EVERY } from './api/poller'
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
import { channelName } from './components/channels'

// 轮询/健康检查等接口响应的最小形状(纯类型层; API 动态数据不造假严格)
// 两种包裹口径并存: seed 系返回 {data:{status}}; cleansing/export 系返回顶层 {ok,status}
type JobStatusResp = { data?: { status?: string }; status?: string }

export const NAV = [
  { id: 'dash', label: t('nav.dash') },
  { id: 'products', label: t('nav.products') },
  { id: 'suppliers', label: t('nav.suppliers') },
  { id: 'orders', label: t('nav.orders') },
  { id: 'history', label: t('nav.history') },
  { id: 'inv', label: t('nav.inv') },
  { id: 'insights', label: t('nav.insights') },
  { id: 'cleansing', label: t('nav.cleansing') },
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
    // 2026-11-07 根治「切页后每页都慢」:
    //   原来这里 clearCache() + clearInflight()。前者把**全站**缓存一次清空(切到哪页都要重新
    //   拉全部接口, 连没离开过的页面也失效); 后者只清 Map, **一个请求都没取消** —— 旧页 4~6 个
    //   请求继续跑完(每个 1~8s), 占着 Makers 的 DB 连接与执行槽, 新页只能排队。
    //   现在: 真正 abort 旧页在途(浏览器断开 → 服务端连接提前释放) + 视图切到新页(胶囊归零);
    //   缓存按 30s TTL 自然过期, 不再"切一次页全站归零"。
    setView(p)
    abortAll()
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
    qualityLogs,
    startPolling,
    stopAll,
    channel,
    setChannel,
    channels,
    setChannels,
    hammerData,
    setHammerPanel,
    _stockOverview,
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
            // S3-2 收口: apiJson(超时+token 注入+BASE 口径), 网络错误照常 throw → 重试逻辑不变
            const d: { data?: Channel[] } = await apiJson('GET', '/api/channels?enabled_only=1')
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
  const _chLabel = channelName(channels.find(c => c.code === channel)) || channel
  const fresh = useFreshness(channel) // 2026-10-29 数据新鲜度(看板胶囊左侧圆点)
  const [freshOpen, setFreshOpen] = useState(false)
  const syncOld = useAppStore(s => s.syncOld)
  // 2026-11-05 根治「多页面卡加载中/同步中」: 胶囊此前只看 store.syncing ——
  //   那是**页面手动配对**的计数器(全仓 20+ 处 setSyncing), 任何一处漏配对或多配一次, 胶囊就卡住。
  //   历史修法是"20s 强制归零"补丁, 但那只是让徽标消失, 页面自己的 loading 还是卡着。
  //   现在补一条来自网络层的硬信号 reqBusy: 只要**真有前台 HTTP 请求在途**就转, 全都回来了就停。
  //   两者取或 —— 页面声明的整轮取数(粒度更粗, 含请求间隙) ∪ 真实在途请求(不可能漏配对)。
  //   ⚠️ 后台请求(任务看守 taskWatch)在 client 层已标 background, 不计入 reqBusy ——
  //   否则一个长任务在跑时"每个页面都在同步中", 用户会以为是页面卡了(问题 4 的根)。
  const [reqBusy, setReqBusy] = useState(0)
  useEffect(() => {
    const sync = () => setReqBusy(activeRequests())
    sync()
    return onActiveRequests(sync)
  }, [])
  // 2026-11-07: 胶囊只认「当前视图」的真实在途(reqBusy, 已按页面分桶)。
  //   不再叠加全局 syncing —— 那是页面手工计数, 切页后旧页的 -1 还没执行, 新页会莫名转圈。
  const badgeBusy = reqBusy > 0
  // 2026-11-05 任务看守(回答「持锁期间别的页面怎么办 / 多次切页面怎么办」):
  //   后端续跑是**前端轮询驱动**的, 每推进一窗服务端持一次租约防并发重写。
  //   租约现为 **TTL 30s + 每 10s 心跳 + lease_token 归属令牌**(旧实现是 600s 死等,
  //   所以「停止任务要等十来分钟」—— 那个数字已经过期, 见 task_seed.py 的 _start_heartbeat)。
  //   原来驱动只挂在任务页 —— 一切走就没人推进; 而用户根本不知道这个任务还在跑。
  //   现在改成 App 层看守: 任何页面停留期间都持续推进(与页面无关注视), 且
  //   ① 全局串行(同一时刻只有一个续跑请求, 不并发抢锁、不把胶囊拖成一直转);
  //   ② 单请求超时 100s(< 网关 120s 硬上限), 被掐断也不会让服务端留半个事务;
  //   ③ 无进展时按指数退避(3s→15s), 租约被别人持有时不硬撞、不烧 RU;
  //   ④ 切页面不影响: 看守在模块层, 组件卸载不摘监听(见 taskWatch 的 _watches);
  //   ⑤ 它是**唯一**驱动源: 其余状态查询一律 probe=1 纯读(只看看, 不推进)。
  const [watching, setWatching] = useState(0)
  useEffect(() => onWatchChange(() => setWatching(watchCount())), [])
  // 首次挂载 + 每次页面可见: 轻轻拉一次任务列表, 把 running 的接管进来
  //   (轻量: limit=8, 只为拿"还有哪些任务在跑", 不参与业务数据)
  useEffect(() => {
    let alive = true
    const adopt = async () => {
      try {
        const d = await apiJson<{ data?: Array<Record<string, unknown>> }>(
          'GET',
          API + '/api/tasks?channel=' + useAppStore.getState().channel + '&limit=8',
          { timeout: 15000 },
        )
        if (!alive) return
        adoptRunning((d?.data || []) as Array<{ task_id?: string; status?: string }>, {
          onDone: () => {
            window.dispatchEvent(new Event('seed-done'))
            useAppStore
              .getState()
              .loadAll(1, { refresh: true })
              .catch(() => {})
          },
        })
      } catch {
        /* 网络抖动: 下次可见时再试, 不打断 */
      }
    }
    void adopt()
    const onVis = () => {
      if (document.visibilityState === 'visible') void adopt()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      alive = false
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [API])
  // 2026-10-27 徽标兜底: syncing 卡住(任何遗漏路径)20s 强制归零——防徽标永久显示需手动刷新
  //   2026-11-05 保留但收窄作用域: 只兜 syncing(页面声明), reqBusy 由网络层 finally 保证归零,
  //   不需要也不应该被它清掉(否则真慢的请求会被"显示完成")。
  useEffect(() => {
    if (syncing <= 0) return
    const _tid = setTimeout(() => useAppStore.setState({ syncing: 0 }), 20000)
    return () => clearTimeout(_tid)
  }, [syncing])
  // 2026-11-03 操作态看守(OpsWatcher): 后端任务不靠前端计时猜, 轮询真实状态。
  //   为什么必须在 App 层而不是规则页: 用户保存后切页, 规则页卸载 → 页面内轮询就没了,
  //   胶囊会永远停在"规则重算中"。挂在 App 层才能跨页 + 刷新后恢复(localStorage)。
  const opsKey = ops.map(o => o.id + ':' + String(o.taskId ?? '')).join('|')
  useEffect(() => {
    const targets = ops.filter(o => o.kind === 'rules' && o.taskId)
    if (!targets.length) return
    let alive = true
    const check = async () => {
      for (const o of targets) {
        try {
          const j = await apiJson<JobStatusResp>(
            'GET',
            API + '/api/rules/eval-status?task_id=' + o.taskId,
          )
          if (!alive) return
          const st = j?.data?.status
          if (st === 'done' || st === 'expired') {
            useAppStore.getState().opDone(o.id)
            // 评估写完 alerts 才算"变了" —— 此时通知各页重拉, 看板的"重算中"同步消失
            window.dispatchEvent(new Event('rules-changed'))
          } else if (st === 'error') {
            useAppStore.getState().opDone(o.id)
            notify.error(t('op.rules_fail'))
          }
        } catch {
          /* 网络抖动: 保留 op 等下一轮, 不误清 */
        }
      }
    }
    check()
    const _tid = setInterval(check, 2500)
    return () => {
      alive = false
      clearInterval(_tid)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opsKey, API])
  // 陈旧兜底: 刷新恢复的 op 若后端任务早已消失, 15 分钟后自动清 —— 防"永远处理中"
  useEffect(() => {
    if (!ops.length) return
    const _tid = setInterval(() => {
      const now = Date.now()
      useAppStore.getState().ops.forEach(o => {
        if (now - o.startedAt > 15 * 60 * 1000) useAppStore.getState().opDone(o.id)
      })
    }, 30000)
    return () => clearInterval(_tid)
  }, [ops.length])
  // 全局同步徽标(2026-11-07 改版: 只在**当前页**显示)
  //   文案判断机制不变: syncOld=true(有旧值渲染, 后台拉新)=「同步中」; false(首载无旧值, 骨架)=「加载中」
  //   变化只在"谁点亮它": 原来 = 全站在途(切页后旧页请求还亮着), 现在 = 当前视图在途(见 client._fgByView)。
  //   页面自己的骨架/局部 loading 不受影响 —— 胶囊只是同一状态在 header 的镜像。
  const taskNote = watching > 0 && page !== 'tasks' ? t('op.watching', { n: watching }) : ''
  // 任务在跑时即使当前页没有取数也要显示(全局可见); 只有任务页例外(自身即进度页)
  const syncBadge =
    badgeBusy || taskNote ? (
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
        {badgeBusy ? (
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
        ) : null}
        {badgeBusy ? (syncOld ? t('op.sync_old') : t('common.loading')) : taskNote}
        {badgeBusy && taskNote ? <span style={{ opacity: 0.75 }}>· {taskNote}</span> : null}
      </span>
    ) : null
  // 2026-11-03 操作态优先于取数态: 后端正在算时不显示"同步中"(那是前端在取数),
  //   两者叠加会让用户以为"一直在加载"。ops 空时回落到原来的同步徽标。
  const headerPill = ops.length > 0 ? <OpPill /> : syncBadge
  // 2026-11-03 顶层提示统一走 notify(Provider 在下方, 这里的 useToast 只会拿到 no-op)
  // 任务完成统一静默刷新(不整页 reload —— 消除闪烁): 清前端缓存 + store 重载 + 事件通知各页
  // 2026-11-05 修「重置后自有仓健康卡片还有数据」: 原来只 clearCache/clearInflight,
  //   清的是**请求缓存**, 而 store 里的 `ownHealth` / `dashboard` / `stockRisk` 是上一轮
  //   已经渲染出去的**快照**, 一个字节都没动 —— 于是重置完页面照旧显示旧数字,
  //   直到下一次 loadAll 真正把每个字段覆盖一遍(而 loadAll 对 dashboard 是浅合并,
  //   新响应里没带的字段会**永远保留旧值**)。
  //   所以这里必须显式把"服务端已变但可能不再返回"的分片清空, 再重载。
  //   reset 语义最重(整库清空重建), 单独走 resetStore()。
  const refreshAll = (opts?: { storeReset?: boolean }) => {
    clearCache()
    clearInflight()
    const st = useAppStore.getState()
    if (opts?.storeReset) {
      st.invalidateSnapshots()
    }
    st.loadAll(1, { refresh: true }).catch(() => {})
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
        label: 'op.seed',
        taskId: seedTask,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        // 2026-11-06 S1: 隐藏时直接不发请求。原来这行在 await **之后** —— 等于每次都先把
        //   请求打出去、拿到响应再丢弃, 挂后台 8h 白打 5760 次/任务。任务推进由 taskWatch
        //   负责(它不受可见性影响), 这里只是看进度, 回来后 5s 内补上完全够。
        if (document.visibilityState === 'hidden') return
        try {
          // 2026-11-05 竞态根治: 这里只**看**进度, 一律走 probe=1 纯读。
          //   本端点原是"查一次 = 抢锁 + 推进一步 + 写库"的副作用端点, 而 App 层 3 个老轮询
          //   (seed/reset/export) + 任务页 + 各页面又各打一遍 —— 6 个驱动源抢同一把租约,
          //   抢不到的白烧 RU, 抢到的把胶囊拖成一直转。现在仅 taskWatch 看守负责推进。
          const d = await apiJson<JobStatusResp>(
            'GET',
            API + '/api/seed/fill/status?task_id=' + seedTask + '&probe=1',
          )
          if (d.data?.status === 'done') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_seed_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.success(t('op.seed_done'))
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
            if (d.data?.status === 'error') notify.error(t('op.seed_fail'))
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
        label: 'op.clean',
        scope: cleansingTask.channel || '',
        taskId: cleansingTask.task_id,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        // 2026-11-06 S1: 隐藏时直接不发请求。原来这行在 await **之后** —— 等于每次都先把
        //   请求打出去、拿到响应再丢弃, 挂后台 8h 白打 5760 次/任务。任务推进由 taskWatch
        //   负责(它不受可见性影响), 这里只是看进度, 回来后 5s 内补上完全够。
        if (document.visibilityState === 'hidden') return
        try {
          // 2026-11-05 竞态根治: 本处原为「GET /cleansing/status + 并行 POST /cleansing/driver」
          //   两个请求, 而两者**都会推进任务**; taskWatch 看守再加一份 → 同一任务 3 个驱动源。
          //   现在只读: status 走 probe=1, 并删掉那次 driver POST(推进由看守唯一负责)。
          const d = await apiJson<{ status?: string }>(
            'GET',
            API +
              '/api/cleansing/status?task_id=' +
              cleansingTask.task_id +
              '&page_size=1000&probe=1',
          )
          if (d.status === 'done') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_cleansing_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.success(t('op.clean_done'))
            refreshAll()
          } else if (d.status === 'error') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_cleansing_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.error(t('op.clean_fail'))
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
        label: 'op.reset',
        taskId: resetTask,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        // 2026-11-06 S1: 隐藏时直接不发请求。原来这行在 await **之后** —— 等于每次都先把
        //   请求打出去、拿到响应再丢弃, 挂后台 8h 白打 5760 次/任务。任务推进由 taskWatch
        //   负责(它不受可见性影响), 这里只是看进度, 回来后 5s 内补上完全够。
        if (document.visibilityState === 'hidden') return
        try {
          // 2026-11-05: 只读进度(probe=1), 推进交给 taskWatch —— 见上方 seed 轮询的说明
          const d = await apiJson<JobStatusResp>(
            'GET',
            API + '/api/seed/fill/status?task_id=' + resetTask + '&probe=1',
          )
          if (d.data?.status === 'done' || d.data?.status === 'error') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_reset_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            if (d.data?.status === 'done') {
              notify.success(t('op.reset_done'))
              // reset = 整库清空重建, 旧的健康卡/看板快照全都作废 → 必须整体失效而不是"再拉一遍"
              refreshAll({ storeReset: true })
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
        label: 'op.export',
        taskId: exportTask.task_id,
        startedAt: Date.now(),
      })
      const poll = setInterval(async () => {
        // 2026-11-06 S1: 隐藏时直接不发请求。原来这行在 await **之后** —— 等于每次都先把
        //   请求打出去、拿到响应再丢弃, 挂后台 8h 白打 5760 次/任务。任务推进由 taskWatch
        //   负责(它不受可见性影响), 这里只是看进度, 回来后 5s 内补上完全够。
        if (document.visibilityState === 'hidden') return
        try {
          // 2026-11-06 P0-1: 原为 /seed/fill/status?probe=1 —— 那是 seed 的结构
          //   ({data:{step,total_step}}), 拿它读导出任务只能读到 status, 进度字段全是 0,
          //   而且一旦 seed 端点按 task_type 收紧就会直接 not_found。导出有自己的端点。
          const d = await apiJson<JobStatusResp>(
            'GET',
            API + '/api/exports/status?task_id=' + exportTask.task_id + '&probe=1',
          )
          const _st = d?.data?.status || d?.status
          if (_st === 'done') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_export_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.success(t('op.export_done'))
          } else if (_st === 'error' || _st === 'not_found') {
            clearInterval(poll)
            try {
              localStorage.removeItem('c_export_task')
            } catch {}
            useAppStore.getState().opDone(_opId)
            notify.error(t('op.export_fail'))
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
        const r = await apiJson<Response>('GET', API + '/api/auth/check', { raw: true })
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
    apiJson<Response>('GET', API + '/api/auth/check', { raw: true })
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
        const d = await apiJson<{ data?: unknown[] }>(
          'GET',
          API + '/api/replenishment-config/history?channel=' + (ch || channel) + '&limit=50',
        )
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
      const d = await apiJson<{ ok?: boolean }>('GET', API + '/api/insights/ping', {
        timeout: 5000,
      })
      setApiStatus(d.ok ? 'ok' : 'slow')
    } catch {
      setApiStatus('error')
    }
  }, [])
  useEffect(() => {
    // 2026-11-06 P1-22: 交给统一调度器 —— 原来这里裸 setInterval 30s, 不看可见性,
    //   标签页挂后台 8 小时照样每 30s 打一次探针。调度器接管后: 隐藏时升到 5min,
    //   回前台立刻补一次, 在途未结束不叠加。
    return registerPoll('app:ping', POLL_EVERY.health, checkApi)
  }, [checkApi])

  // 数据版本轮询：后端 _cache_version 变化时自动刷新
  const [, setDbVersion] = useState(0)
  const versionRef = useRef(0)
  useEffect(() => {
    // 2026-11-06 P1-22: 同上, 走统一调度器。
    //   注意这里保留"只**判断**要不要刷"的语义: 版本没变就不动, 变了才 clearCache + loadAll。
    //   (根治"version 一动就全量 loadAll"需要后端按域返回, 属于另一项, 不在本包。)
    return registerPoll(
      'app:version',
      POLL_EVERY.health,
      async () => {
        const d = await apiJson<{ version?: number }>('GET', API + '/api/health')
        const v = d.version || 0
        if (versionRef.current !== 0 && versionRef.current !== v) {
          clearCache()
          useAppStore
            .getState()
            .loadAll()
            .catch(() => {})
        }
        versionRef.current = v
        setDbVersion(v)
      },
      { immediate: false },
    )
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
    // 2026-11-07 防雪崩(第 2 处): 按当前视图只拉这一页真正需要的域。
    //   看板: 看板域(summary/aux/stock-risk)由 DashboardPage 自拉(权威、带 t= 破缓存),
    //         这里只补订单域 + 角标; 订单页: 全量(订单是主角); 其它页: 仅角标。
    const st = useAppStore.getState()
    const v = currentView()
    if (v === 'dash') {
      st.loadOrdersChrome().catch(() => {})
    } else if (v === 'orders') {
      st.loadAll().catch(() => {})
    } else {
      st.loadChrome().catch(() => {})
    }
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

  // 与看板卡同源(2026-11-06): 后端口径 low_stock_count, 不再前端自比静态 safety_qty
  const lowStock = Number(
    (_stockOverview as { low_stock_count?: number } | null)?.low_stock_count ?? 0,
  )
  const errCount = (qualityLogs || []).length

  // 2026-10-14: 回前台立即续跑 running 导入/填充/导出任务(后端 continue-tasks 兜底 + 前端恢复接力)
  useEffect(() => {
    const cont = () => {
      apiJson('POST', API + '/api/cron/continue-tasks?task=manual').catch(() => {})
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
