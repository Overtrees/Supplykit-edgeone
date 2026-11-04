import React, { useState, useEffect, useRef } from 'react'
import { api, clearCache, clearInflight, freshStamp, apiJson } from '../api/client'
import { mutate } from '../api/mutate'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import { t } from '../locale'
import RuleTestSheet from './rules/TestSheet'
import { pc, defaultTestInv } from './rules/labels'
import RuleEditForm from './rules/EditForm'
import RuleList from './rules/List'
import RulesSlowPanel from './rules/SlowPanel'
import RulesReplenishPanel from './rules/ReplenishPanel'
import RulesPurchasePanel from './rules/PurchasePanel'
import RulesSeasonPanel from './rules/SeasonPanel'

const API = import.meta.env.VITE_API_BASE_URL || ''

export default function RulesPage() {
  const toast = useToast()
  const [saveLoading, setSaveLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [seasonsSaving, setSeasonsSaving] = useState(false)
  // 规则测试（可视化调试）：testRule 当前测试的规则 / testInv 模拟库存 / testResult 测试结果
  const [testRule, setTestRule] = useState(null)

  const [loading, setLoading] = useState(true)
  const [rules, setRules] = useState([])
  const [rulesErr, setRulesErr] = useState('')
  const [debugLog, setDebugLog] = useState([])
  const addDebug = (msg, data?) => {
    const _ts = new Date().toLocaleTimeString()
    setDebugLog(p => [{ t, msg, data }, ...p].slice(0, 50))
  }
  const [editing, setEditing] = useState(null)
  // 补货配置(后端动态键): 已知字段精确枚举, 动态键走 unknown 索引(边界用 String/Number 收敛)
  interface ReplenConfig {
    replenishment_mode?: string
    transit_days?: string
    slow_fund_threshold?: string
    [k: string]: unknown
  }
  const [cfg, setCfg] = useState<ReplenConfig>({})
  const [seasons, setSeasons] = useState([])
  // 滞销品类配置（自定义条目，仿活动系数）
  const [slowCats, setSlowCats] = useState([])
  const [transitDays, setTransitDays] = useState('3')
  const [fundThreshold, setFundThreshold] = useState('10000')
  useEffect(() => {
    if (cfg.transit_days) setTransitDays(cfg.transit_days)
  }, [cfg.transit_days])
  useEffect(() => {
    if (cfg.slow_fund_threshold) setFundThreshold(cfg.slow_fund_threshold)
  }, [cfg.slow_fund_threshold])
  const reqSeq = useRef(0)
  const [selectedSupplier, setSelectedSupplier] = useState('')
  // 保存错误统一提示（403 访客模式显示后端 detail）
  const saveErr = e => {
    const detail = e?.response?.data?.detail
    if (detail) toast.error(String(detail))
    else if (e?.response?.status === 403) toast.error(t('rp.guest_readonly'))
    else toast.error(t('rp.save_failed', { msg: e.message || '' }))
  }
  const [suppliers, setSuppliers] = useState([])

  const defaultF = {
    name: '',
    event: 'inventory.changed',
    alert_type: 'low_stock',
    alert_title: '',
    alert_desc: '',
    severity: 'warning',
    condition_json: '{}',
    mode: '',
  }
  const [f, setF] = useState(defaultF)
  // 条件编辑器状态(子条件 or 数组同构)
  interface CondState {
    left?: string
    op?: string
    right?: string
    rightType?: string
    pctValue?: number | string
    warehouse?: string
    or?: CondState[]
    [k: string]: unknown
  }
  const [cond, setCond] = useState<CondState>({
    left: 'inv.available_qty',
    op: '<',
    right: 'inv.safety_qty',
    rightType: 'field',
    pctValue: 100,
    warehouse: '',
  })
  const [rParams, setRParams] = useState<Record<string, string>>({})
  const {
    channel: globalChannel,
    setChannel: setGlobalChannel,
    hammerRulesTab: tab,
    hammerRuleNewVersion,
    hammerRulesMode,
    hammerSearch,
    prodBatch,
    prodSelIds,
    setProdBatchSel,
    setProdBatchFilterLen,
    prodBatchVersion,
    prodBatchAllReq,
  } = useAppStore()
  useEffect(() => {
    api
      .get('/api/replenishment-config/slow-cats?channel=' + globalChannel)
      .then(r => {
        if (Array.isArray(r.data)) setSlowCats(r.data)
      })
      .catch(() => {})
  }, [globalChannel])
  const selIds = prodSelIds || []
  const setSelIds = setProdBatchSel
  useEffect(() => {
    if (!prodBatch) setProdBatchSel([])
  }, [prodBatch])
  useEffect(() => {
    if (prodBatchVersion > 0) load(globalChannel)
  }, [prodBatchVersion])

  const load = async ch => {
    try {
      const c = ch || globalChannel
      addDebug('load 开始', { channel: c, 当前rules数: rules.length })
      const token = (() => {
        try {
          return localStorage.getItem('c_token')
        } catch {
          return ''
        }
      })()
      // S3-2 收口: raw 保留 r.status(addDebug 记录用), apiJson 补超时/token/BASE
      const r = await apiJson('GET', API + '/api/rules?channel=' + c, { raw: true })
      const d = await r.json()
      // 防御: 异常响应(401/500/结构异常)可能非数组 → setRules(object) 导致 rules.length
      // undefined → 渲染条件全 false → 空白无提示。数组校验+格式异常提示
      const rawData = d.data !== undefined ? d.data : d
      const newData = Array.isArray(rawData) ? rawData : []
      addDebug('load 返回', {
        status: r.status,
        条数: newData.length,
        ids: newData.map(x => x.id).slice(0, 10),
      })
      setRules(newData)
      // 同步批量面板状态判断数据(id → is_active)
      const _m = {}
      ;(newData || []).forEach(r => {
        if (r && r.id) _m[r.id] = r.is_active ? 1 : 0
      })
      useAppStore.setState({ batchStateMap: _m })
      setRulesErr(
        Array.isArray(rawData)
          ? ''
          : r.status !== 200
            ? t('rp.load_failed')
            : t('rp.bad_payload'),
      )
      addDebug('setRules 完成', { 条数: newData.length })
    } catch (e) {
      addDebug('load 异常', { error: e.message })
      setRulesErr(t('rp.load_failed'))
    }
  }
  useEffect(() => {
    const h = () => load(globalChannel)
    window.addEventListener('rules-changed', h)
    return () => window.removeEventListener('rules-changed', h)
  }, [globalChannel])
  const loadCfg = async (mode, ch?) => {
    try {
      const m = mode || cfg.replenishment_mode || 'bbcc'
      const c = ch || globalChannel
      const r = await api.get(
        '/api/replenishment-config?mode=' + m + '&channel=' + c + '&t=' + freshStamp(),
      )
      if (r.data && Object.keys(r.data).length > 0) {
        const mP = 'mode_' + m + '_'
        const mD = {}
        Object.entries(r.data).forEach(([k, v]) => {
          if (k.startsWith(mP)) mD[k.slice(mP.length)] = v
        })
        setCfg(p => ({ ...p, ...r.data, ...mD, replenishment_mode: m }))
      } else if (c !== 'jd') {
        const fallback = await api.get('/api/replenishment-config?mode=' + m + '&channel=jd')
        if (fallback.data) setCfg(p => ({ ...p, ...fallback.data, replenishment_mode: m }))
      }
      setCfg(p => ({ ...p, replenishment_mode: m }))
      return r.data || {}
    } catch (e) {
      return {}
    }
  }
  const loadSeasons = async (mode, ch?) => {
    try {
      const m = mode || cfg.replenishment_mode || 'bbcc'
      const c = ch || globalChannel
      const r = await api.get(
        '/api/replenishment-config/seasons?mode=' + m + '&channel=' + c + '&t=' + freshStamp(),
      )
      setSeasons(r.data || [])
    } catch (e) {}
  }
  const loadSlowCfg = async () => {
    try {
      const c = globalChannel
      const [rc, rf] = await Promise.all([
        api.get('/api/replenishment-config/slow-cats?channel=' + c + '&t=' + freshStamp()),
        api.get('/api/replenishment-config?channel=' + c + '&t=' + freshStamp()),
      ])
      if (rc.data) setSlowCats(Array.isArray(rc.data) ? rc.data : [])
      if (rf.data) {
        if (rf.data.transit_days) setTransitDays(rf.data.transit_days)
        if (rf.data.slow_fund_threshold) setFundThreshold(rf.data.slow_fund_threshold)
      }
    } catch (e) {}
  }
  const loadAll = async (ch?) => {
    setLoading(true)
    const c = ch || globalChannel
    const savedMode = (() => {
      try {
        return localStorage.getItem('c_replen_mode_' + c)
      } catch {
        return null
      }
    })()
    const m = c !== 'jd' ? 'traditional' : savedMode || 'bbcc'
    clearCache()
    clearInflight()
    try {
      const _s = localStorage.getItem('c_supplier_' + c)
      if (_s) setSelectedSupplier(_s)
    } catch {}
    await Promise.all([
      (async () => {
        try {
          await load(c)
        } catch (e) {}
      })(),
      (async () => {
        try {
          const flat = await api.get('/api/replenishment-config?channel=' + c)
          if (flat.data) {
            setCfg(p => {
              const mP = 'mode_' + m + '_'
              const mD = {}
              Object.entries(flat.data).forEach(([k, v]) => {
                if (k.startsWith(mP)) mD[k.slice(mP.length)] = v
              })
              return { ...p, ...flat.data, ...mD, replenishment_mode: m }
            })
            const sK = 'season_config_' + m
            try {
              const sd = JSON.parse(flat.data[sK] || '[]')
              setSeasons(Array.isArray(sd) ? sd : [])
            } catch {}
            try {
              if (flat.data.slow_cats) {
                const sc = JSON.parse(flat.data.slow_cats)
                setSlowCats(Array.isArray(sc) ? sc : [])
              }
              if (flat.data.transit_days) setTransitDays(flat.data.transit_days)
              if (flat.data.slow_fund_threshold) setFundThreshold(flat.data.slow_fund_threshold)
            } catch {}
          }
        } catch (e) {}
      })(),
      (async () => {
        try {
          const sr = await api.get('/api/suppliers?channel=' + c)
          if (sr.data) setSuppliers(sr.data.map(x => x.supplier_code).filter(Boolean))
        } catch (e) {}
      })(),
    ])
    setLoading(false)
  }
  useEffect(() => {
    loadAll()
  }, [globalChannel])
  // tab/模式切换时加载配置，补货参数页加骨架过渡
  useEffect(() => {
    const seq = ++reqSeq.current
    if (tab === 'params') {
      setLoading(true)
      Promise.all([loadCfg(hammerRulesMode), loadSeasons(hammerRulesMode)])
        .catch(() => {})
        .finally(() => {
          if (reqSeq.current === seq) setLoading(false)
        })
    } else if (tab === 'purchase') {
      loadCfg(hammerRulesMode)
    } else if (tab === 'slow') {
      loadSlowCfg()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, hammerRulesMode])
  // 锤子菜单t("rules.new")触发
  useEffect(() => {
    if (hammerRuleNewVersion > 0) resetForm()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hammerRuleNewVersion])

  const resetForm = () => {
    setEditing({})
    setF({
      ...defaultF,
      mode: hammerRulesMode || (globalChannel === 'jd' ? 'bbcc' : 'traditional'),
    })
    setRParams({})
    setCond({
      left: 'inv.available_qty',
      op: '<',
      right: 'inv.safety_qty',
      rightType: 'field',
      pctValue: 100,
      warehouse: '',
      or: [],
    })
  }
  const cancelEdit = () => {
    setEditing(null)
    setF({
      ...defaultF,
      mode: hammerRulesMode || (globalChannel === 'jd' ? 'bbcc' : 'traditional'),
    })
    setRParams({})
    setCond({
      left: 'inv.available_qty',
      op: '<',
      right: 'inv.safety_qty',
      rightType: 'field',
      pctValue: 100,
      warehouse: '',
      or: [],
    })
    useAppStore.setState({ hammerRuleNewVersion: 0 })
  }

  // 编辑表单「测试」按钮: 打包当前编辑态 → setTestRule(随 TestSheet 外提时的约定)
  const openRuleTest = () => {
    const _c = cond
    setTestRule({
      ...(editing || {}),
      condition_json: JSON.stringify(_c),
      __testParams:
        typeof rParams === 'string'
          ? (function () {
              try {
                return JSON.parse(rParams || '{}')
              } catch {
                return {}
              }
            })()
          : rParams || {},
      __testInv: defaultTestInv(pc(JSON.stringify(_c))),
    })
  }
  // 列表点卡片 → 进入编辑(回填表单状态)
  const openEdit = (rule: any) => {
    if (!prodBatch) {
      const c = pc(rule.condition_json || '{}')
      setEditing(rule)
      setF({
        name: rule.name,
        event: rule.event,
        alert_type: rule.alert_type || 'low_stock',
        alert_title: rule.alert_title || '',
        alert_desc: rule.alert_desc || '',
        severity: rule.severity || 'warning',
        mode: rule.mode || '',
        condition_json: rule.condition_json || '{}',
      })
      setRParams(rule.params || {})
      setCond(c)
    }
  }
  // 列表空态「新建规则」
  const newRuleBlank = () => {
    setEditing({})
    setF({
      name: '',
      event: 'inventory.changed',
      alert_type: 'low_stock',
      alert_title: '',
      alert_desc: '',
      severity: 'warning',
      mode: '',
      condition_json: '{}',
    })
    setCond({})
    setRParams({})
  }

  const save = async () => {
    setSaveLoading(true)
    addDebug('save 开始', { isNew: !editing || !editing.id })
    try {
      let rv: string | number = cond.right || ''
      if (cond.rightType === 'number') rv = parseFloat(cond.right || '') || 0
      else if (cond.rightType === 'field') rv = cond.right || ''
      else if (cond.rightType === 'pct')
        rv = `max(1,${cond.right}*${(Number(cond.pctValue) || 100) / 100})`
      const cj = JSON.stringify({
        left: cond.left,
        op: cond.op,
        right: rv,
        rightType: cond.rightType,
        warehouse: cond.warehouse,
        // 2026-11-01 保住 and 子条件 —— 后端 _check_condition 支持 and/or 两种嵌套,
        //   而内置自有仓规则(seed_fill)正是 `inv.warehouse_type==own and {own.gap>阈值}` 结构。
        //   原保存链路只序列化 or, 用户一编辑这类规则 and 就被**静默丢掉** →
        //   前半段 warehouse_type 前置没了 → 规则对所有仓型求值(own.* 只在 own 行注入,
        //   其他行恒 None 倒不误报, 但语义已被破坏), 且再保存一次就永久丢失。
        ...(cond.and ? { and: cond.and } : {}),
        or: (cond.or || []).map(o => ({
          left: o.left,
          op: o.op,
          right: o.rightType === 'number' ? parseFloat(o.right) || 0 : o.right,
          warehouse: o.warehouse,
        })),
      })
      const isNew = !editing || !editing.id
      const url = isNew ? API + '/api/rules' : API + '/api/rules/' + editing.id
      // 2026-11-03 统一走 mutate: 提示/缓存/事件三件套一次声明, 不再各写各的。
      //   op 的语义: HTTP 200 只代表"已受理", 真正的告警变更发生在后台评估 ——
      //   所以 op 必须活到 eval_tasks 变成终态(由 App 层 OpsWatcher 移除), 不能在这里 finally 清掉。
      const saved = await mutate({
        label: isNew ? t('rp.new_rule') : t('rp.save_rule'),
        run: async () => {
          // S3-2 收口: 保存是重操作, 超时对齐 postHeavy 90s; raw 保留 res.ok 判断
          const res = await apiJson(isNew ? 'POST' : 'PUT', url, {
            timeout: 90000,
            data: {
              ...f,
              // 2026-10-28 修复: '' 表示'全部'(合法值)——曾用 || 回退成旧 mode, 用户改回'全部'保存后又变 bbcc
              mode: f.mode ?? '',
              channel: globalChannel,
              condition_json: cj,
              params: rParams,
            },
          })
          if (!res.ok) {
            const err = await res.json().catch(() => ({}))
            throw new Error(err.detail || 'HTTP ' + res.status)
          }
          let tid = 0
          try {
            const body = await res.json()
            tid = Number(body?.data?.eval_task_id || 0)
          } catch {
            tid = 0
          }
          return { tid }
        },
        // 文案类保存不触发评估(tid=0) —— 提示要诚实, 不能一律说"正在重算"
        success: r => (r.tid ? t('rp.saved_recalc') : t('rp.saved')),
        events: ['rules-changed'],
        invalidate: '*',
        op: r =>
          r.tid
            ? {
                id: 'rules:' + r.tid,
                kind: 'rules',
                label: t('rp.recalc_op'),
                scope: globalChannel,
                taskId: r.tid,
                startedAt: Date.now(),
              }
            : null,
        opPersist: true,
      })
      if (!saved) {
        setSaveLoading(false)
        return
      }
      addDebug('save 成功', { isNew, id: editing?.id, tid: saved.tid })
      cancelEdit()
      await load(globalChannel)
      // 本地即时更新 mode 显示，不等 API 返回（避免旧 state 渲染导致 mode 显示"全部"）
      if (!isNew)
        setRules(prev =>
          prev.map(rl => (rl.id === editing.id ? { ...rl, mode: f.mode ?? '' } : rl)),
        )
      // 严谨性校验提示(不阻断, 防静默错): 条件引用 order.* 但事件非订单创建 / params.* 缺失
      const _cj2 =
        JSON.stringify(cond) +
        JSON.stringify((cond.or || []).map(o => ({ left: o.left, op: o.op, right: o.right })))
      const _evt = f.event || ''
      if (_cj2.includes('order.') && _evt !== 'order.created')
        toast.warning(t('rp.warn_order_event'))
      const _pm = _cj2.match(/params\.([\w]+)/g) || []
      if (_pm.length) {
        const _miss = _pm
          .map(m => m.split('.')[1])
          .filter(k => !(rParams[k] !== undefined && rParams[k] !== ''))
        if (_miss.length)
          toast.warning(t('rp.warn_param_missing', { keys: _miss.join('/') }))
      }
    } catch (e) {
      toast.error(t('rp.save_failed', { msg: e.message }))
    }
    setSaveLoading(false)
  }
  const del = async id => {
    addDebug('del 开始', { id })
    const okDel = await mutate({
      label: t('rp.delete_rule_op'),
      run: async () => {
        // S3-2 收口: raw 保留 res.ok 判断
        const res = await apiJson('DELETE', API + '/api/rules/' + id, { raw: true })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          throw new Error(err.detail || 'HTTP ' + res.status)
        }
        return true
      },
      // 不用 mutate 的 success: 删除的提示必须带"撤销", 由下方 toast.add 统一弹
      events: ['rules-changed'],
      invalidate: '*',
    })
    if (!okDel) return
    addDebug('del 删除请求完成')
    await load(globalChannel)
    addDebug('del load 完成')
    const timer = setTimeout(async function () {
      // no-link: 撤销窗口结束后的收尾删除 —— 规则行早已从列表消失, 无可观察联动
      await apiJson('POST', API + '/api/rules/' + id + '/permanent-delete')
    }, 5000)
    toast.add({
      type: 'success',
      title: t('common.delete'),
      duration: 5000,
      action: {
        label: t('undo.undo'),
        handler: async function () {
          clearTimeout(timer)
          // 2026-11-03 修: 原来只 load 自身, 既没清缓存也没发事件 —— 撤销后看板要等 30s
          //   或切页重挂才知道告警回来了。恢复同样走 mutate 三件套。
          const okUndo = await mutate({
            label: t('rp.restore_rule'),
            run: async () => {
              // S3-2 收口: raw 保留 res.ok 判断
              const res = await apiJson('POST', API + '/api/rules/' + id + '/restore', {
                raw: true,
              })
              if (!res.ok) throw new Error('HTTP ' + res.status)
              return true
            },
            success: t('rp.restored'),
            events: ['rules-changed'],
            invalidate: '*',
          })
          if (okUndo) load(globalChannel)
        },
      },
    })
  }

  const isBBCC = (cfg.replenishment_mode || 'bbcc') === 'bbcc'
  const filteredRules = hammerSearch
    ? rules.filter(function (r) {
        return (r.name || '').toLowerCase().includes(hammerSearch.toLowerCase())
      })
    : rules
  useEffect(() => {
    setProdBatchFilterLen(filteredRules.length)
  }, [filteredRules.length])
  useEffect(() => {
    if (prodBatchAllReq > 0) {
      const all = filteredRules.map(r => r.id)
      setProdBatchSel(selIds.length === all.length && all.length > 0 ? [] : all)
    }
  }, [prodBatchAllReq])
  const cParams = isBBCC
    ? [
        { k: 'b_to_c_days', l: 'rp.param_b_to_c', h: 'rp.param_b_to_c_h' },
        { k: 'c_safety_days', l: 'rp.param_c_safety', h: 'rp.param_c_safety_h' },
      ]
    : []
  const bParams = isBBCC
    ? [
        { k: 'ship_to_b_days', l: 'rp.param_ship_to_b' },
        { k: 'safety_multiplier', l: 'rp.param_safety_mult' },
        { k: 'turnover_warning_15', l: 'rp.param_turnover_15' },
        { k: 'turnover_warning_90', l: 'rp.param_turnover_90' },
      ]
    : []
  const paramFields = isBBCC
    ? []
    : [
        { k: 'lead_time_days', l: 'rp.param_lead_time' },
        { k: 'safety_multiplier', l: 'rp.param_safety_mult' },
        { k: 'turnover_warning_90', l: 'rp.param_turnover_90' },
      ]
  const purchaseFields = [
    { k: 'purchase_lead_days', l: 'rp.param_purchase_lead' },
    { k: 'purchase_safety_days', l: 'rp.param_purchase_safety' },
    { k: 'moq', l: 'rp.param_moq' },
    { k: 'max_turnover_days', l: 'rp.param_max_turnover' },
  ]

  if (loading)
    return (
      <div className="card">
        <div className="section-title">
          <div className="skeleton" style={{ width: 120, height: 20 }} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
          {[1, 2, 3, 4, 5, 6].map(i => (
            <div key={i}>
              <div className="skeleton" style={{ width: 64, height: 12, marginBottom: 6 }} />
              <div className="skeleton" style={{ width: '100%', height: 36 }} />
            </div>
          ))}
        </div>
        <div style={{ marginTop: 16 }}>
          <div
            className="skeleton"
            style={{ width: 80, height: 36, borderRadius: 'var(--radius-full)' }}
          />
        </div>
      </div>
    )

  return (
    <>
      {/* ── 调试面板（localStorage 设 c_debug_rules=1 启用） ── */}
      {(() => {
        try {
          return localStorage.getItem('c_debug_rules') === '1'
        } catch {
          return false
        }
      })() && (
        <div
          style={{
            marginBottom: 12,
            padding: 10,
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--warning)',
            background: 'rgba(245,158,11,0.08)',
            fontSize: 'var(--font-xs)',
            fontFamily: 'monospace',
          }}
        >
          <div style={{ fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
            {t('rp.debug_title')}
          </div>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>
            {t('rp.debug_state', { n: rules.length })} |{' '}
            {t('rp.debug_filtered', { n: filteredRules.length })} |{' '}
            {t('rp.debug_channel', { v: globalChannel })} | {t('rp.debug_tab', { v: tab })}
          </div>
          <button
            onClick={() => setDebugLog([])}
            style={{
              marginRight: 6,
              padding: '8px 10px',
              minHeight: 30,
              borderRadius: 'var(--radius-xs)',
              border: '1px solid var(--border)',
              background: 'transparent',
              fontSize: 'var(--font-xs)',
            }}
          >
            {t('rp.debug_clear')}
          </button>
          {debugLog.length === 0 ? (
            <div style={{ color: 'var(--muted2)' }}>{t('rp.debug_empty')}</div>
          ) : (
            debugLog.map((l, i) => (
              <div key={i} style={{ borderTop: '1px dashed var(--border)', padding: '2px 0' }}>
                <span style={{ color: 'var(--muted2)' }}>[{l.t}]</span> {l.msg}
                {l.data && (
                  <span style={{ color: 'var(--text-secondary)' }}> {JSON.stringify(l.data)}</span>
                )}
              </div>
            ))
          )}
        </div>
      )}

      <div className="card">
        <div className="section-title" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}></div>

        {/* ── 规则列表 ── */}
        {tab === 'rules' && (
          <>
            {editing !== null && (
              <RuleEditForm
                f={f}
                cond={cond}
                rParams={rParams}
                editing={editing}
                saveLoading={saveLoading}
                setF={setF}
                setCond={setCond}
                setRParams={setRParams}
                cancelEdit={cancelEdit}
                save={save}
                onTest={openRuleTest}
                globalChannel={globalChannel}
              />
            )}
            <RuleList
              filteredRules={filteredRules}
              rules={rules}
              rulesErr={rulesErr}
              selIds={selIds}
              setSelIds={setSelIds}
              prodBatch={prodBatch}
              hammerSearch={hammerSearch}
              load={load}
              globalChannel={globalChannel}
              onEdit={openEdit}
              onNew={newRuleBlank}
            />
          </>
        )}

        {/* ── 补货参数 ── */}
        {tab === 'params' && (
          <RulesReplenishPanel
            channel={globalChannel}
            isBBCC={isBBCC}
            cParams={cParams}
            bParams={bParams}
            paramFields={paramFields}
            cfg={cfg}
            setCfg={setCfg}
            saving={saving}
            setSaving={setSaving}
            saveErr={saveErr}
          />
        )}

        {/* ── 采购参数 ── */}
        {tab === 'purchase' && (
          <RulesPurchasePanel
            channel={globalChannel}
            selectedSupplier={selectedSupplier}
            setSelectedSupplier={setSelectedSupplier}
            suppliers={suppliers}
            purchaseFields={purchaseFields}
            cfg={cfg}
            setCfg={setCfg}
            saving={saving}
            setSaving={setSaving}
            saveErr={saveErr}
          />
        )}

        {/* ── 滞销参数（自定义品类条目，仿活动系数） ── */}
        {tab === 'slow' && (
          <RulesSlowPanel
            channel={globalChannel}
            slowCats={slowCats}
            setSlowCats={setSlowCats}
            transitDays={transitDays}
            setTransitDays={setTransitDays}
            fundThreshold={fundThreshold}
            setFundThreshold={setFundThreshold}
            saving={saving}
            setSaving={setSaving}
            saveErr={saveErr}
          />
        )}
        {tab === 'params' && (
          <RulesSeasonPanel
            channel={globalChannel}
            seasons={seasons}
            setSeasons={setSeasons}
            seasonsSaving={seasonsSaving}
            setSeasonsSaving={setSeasonsSaving}
            cfg={cfg}
            saveErr={saveErr}
            loadCfg={loadCfg}
          />
        )}

        {/* ── 规则测试弹窗（可视化调试：输入模拟数据判断是否触发）—— 底部 sheet 标准模板(2026-09-11 对齐看板弹窗) ── */}
        <RuleTestSheet
          rule={testRule}
          onClose={() => setTestRule(null)}
          globalChannel={globalChannel}
        />
      </div>
    </>
  )
}
