import { IconCheck, IconLoading } from '../components/Icons'
import React, { useState, useEffect, useRef } from 'react'
import { api, uploadRawBytes, freshStamp } from '../api/client'
import { reportError } from '../api/logger'
import { useToast } from '../components/Toast'
import { useAppStore } from '../store/useAppStore'
import ConfirmDialog from '../components/ConfirmDialog'
import ColMapSheet from './cleansing/ColMapSheet'
import ResultStep from './cleansing/ResultStep'
import PreviewStep from './cleansing/PreviewStep'
import MappingStep from './cleansing/MappingStep'
import { t } from '../locale'
import { channelName } from '../components/channels'

import {
  SYS_FIELDS,
  ALIAS,
  ALIAS_EXT,
  TARGET_FIELDS,
  TARGET_TARGETS,
  missingRequired,
  _nk,
} from './cleansing/fields'
import type {
  CleanseData,
  ColumnInfo,
  ColMapItem,
  ColMapRow,
  CustomField,
} from './cleansing/fields'

export default function CleansingPage() {
  const toast = useToast()
  const [s, setS] = useState(0)
  const [f, setF] = useState(null)
  const [fBuf, setFBuf] = useState<ArrayBuffer | null>(null) // 间断 422 根治: iOS multipart body 空——raw bytes 直传
  const [cols, setCols] = useState<ColumnInfo[]>([])
  const [tr, setTr] = useState(0)
  const [tt, setTt] = useState('order')
  const { setHammerCleansingTarget, hammerCleansingConflict } = useAppStore()
  useEffect(() => {
    setHammerCleansingTarget(tt)
  }, [tt])
  const { hammerCleansingChannel: ch, setHammerCleansingChannel: setCh } = useAppStore()
  const goStep = n => {
    setS(n)
    useAppStore.getState().setHammerCleansingStep(n)
    // 2026-11-01 持久拦截提示(必填未映射)在离开当前步时清掉(用户流程: 补映射→跳转→提示消失)
    toast.clear()
  }
  const [mp, setMp] = useState<Record<string, ColMapItem>>({})
  // 映射页当前映射同步(模板管理弹窗保存用) + 模板应用/自定义字段变更事件
  useEffect(() => {
    window.__curMp = mp
  }, [mp])
  useEffect(() => {
    const onApply = (e: Event) => {
      const d = (e as CustomEvent<Record<string, ColMapItem>>).detail
      if (d && typeof d === 'object') setMp(d)
    }
    const onCfChanged = () => {
      try {
        setCf(JSON.parse(localStorage.getItem('c_cf') || '[]'))
      } catch {}
    }
    window.addEventListener('apply-template', onApply)
    window.addEventListener('custom-fields-changed', onCfChanged)
    return () => {
      window.removeEventListener('apply-template', onApply)
      window.removeEventListener('custom-fields-changed', onCfChanged)
    }
  }, [])
  // 2026-10-28 横竖屏切换: 只重置表格"横向"滚动(消除横条滞留)
  // ⚠️ 修复: 曾同时监听 resize 并清 scrollTop —— iOS 地址栏展开/收起会触发 resize,
  //    每次滑动页面都被强制 scrollTop=0 → 内容看起来"不出现, 需再滑一次"
  // 现在: 仅 orientationchange, 且只清 scrollLeft(保留纵向阅读位置)
  useEffect(() => {
    const resetH = () => {
      try {
        document.querySelectorAll<HTMLElement>('.clean-preview-scroll').forEach(el => {
          el.scrollLeft = 0
        })
      } catch {}
    }
    let _last = window.innerWidth > window.innerHeight ? 'land' : 'port'
    const onOrient = () => {
      const cur = window.innerWidth > window.innerHeight ? 'land' : 'port'
      if (cur === _last) return // 尺寸微变(地址栏伸缩)不处理, 仅真实横竖切换
      _last = cur
      resetH()
      setTimeout(resetH, 60)
      setTimeout(resetH, 300)
    }
    window.addEventListener('orientationchange', onOrient)
    return () => window.removeEventListener('orientationchange', onOrient)
  }, [])
  const [pv, setPv] = useState<CleanseData | null>(null)
  const [res, setRes] = useState<CleanseData | null>(null)
  const [bs, setBs] = useState('')
  const [cf, setCf] = useState<CustomField[]>(() => {
    try {
      return JSON.parse(localStorage.getItem('c_cf') || '[]')
    } catch {
      return []
    }
  })
  const [templates, setTemplates] = useState<unknown[]>([]) // 模板管理已移至锤子菜单弹窗(此处仅兼容引用)
  const saveCf = (v: CustomField[]) => {
    setCf(v)
    try {
      localStorage.setItem('c_cf', JSON.stringify(v))
    } catch {}
  }

  const loadTemplates = async () => {
    try {
      const r = await api.get<unknown[]>('/api/cleansing/templates')
      setTemplates(r.data || [])
    } catch (e) {}
  }
  useEffect(() => {
    loadTemplates()
  }, [])

  const addField = () =>
    saveCf([...cf, { t: 'field_' + Date.now(), l: t('cl.custom_field'), tp: 'string' }])
  const delField = i => saveCf(cf.filter((_, k) => k !== i))

  // 用户自定义别名记忆(核心): 手工映射过的 (列名→目标) 存 localStorage, 下次同列名自动识别
  const getAliasMap = () => {
    try {
      return JSON.parse(localStorage.getItem('c_alias_map') || '{}')
    } catch {
      return {}
    }
  }
  const saveAlias = (colName, target) => {
    try {
      const m = getAliasMap()
      if (target) m[colName] = target
      else delete m[colName]
      localStorage.setItem('c_alias_map', JSON.stringify(m))
    } catch {}
  }
  const detect = async (file, buf = null) => {
    // 2026-10-14 422 诊断: 发送前记录 file 状态(前端是否拿到/类型)——console 即时 + reportError 收口
    console.info(
      '[detect] file:',
      file && file.name,
      file && file.size,
      file && file.type,
      '| fisFile:',
      file instanceof File,
    )
    try {
      reportError(
        'detect_debug',
        'detect file: ' +
          (file ? String(file.name) + '|' + String(file.size) + '|' + String(file.type) : 'EMPTY'),
        'isFile=' + String(file instanceof File),
        'info',
      )
    } catch (err) {}
    if (!file) {
      console.warn('[detect] file 为空——未获取到文件对象')
    }
    setF(file)
    setBs(t('cl.detect_ing'))

    try {
      const r = await uploadRawBytes<CleanseData>(
        '/api/cleansing/detect',
        buf || (fBuf ? new Blob([fBuf]) : file),
        60000,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || t('cl.detect_failed'))
        setBs('')
        return
      }
      setCols(d.columns || [])
      setTr(d.total || 0)
      const a = {}
      const customAlias = getAliasMap()
      ;(d.columns || []).forEach(c => {
        // 识别优先级: 用户自定义别名(历史手工映射记忆) → 系统精确 ALIAS → 归一化别名(英文/变体)
        // 目标必须属于当前导入类型字段集(避免订单页识别出库存/出入库无关字段)
        let key = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
        if (key && TARGET_TARGETS[tt] && !TARGET_TARGETS[tt].includes(key)) {
          // 2026-11-01 同一概念在不同导入类型里字段名不同: 「单价」订单=unit_price / 商品=price。
          //   ALIAS 是全局表, 命中 unit_price 后被类型白名单清空 → 商品表的"单价"永远识别不出,
          //   用户只能手工点。按显示名在同类型字段集里再找一次(必须 label 完全一致), 找不到才放弃。
          const _lbl = (SYS_FIELDS.find(f => f.t === key) || {}).l
          const _alt = _lbl && (TARGET_FIELDS[tt] || []).find(f => f.l === _lbl)
          key = _alt ? _alt.t : ''
        }
        if (key) a[c.name] = { target: key, type: 'string' }
      })
      // 以用户已选类型为锚点展示对应字段集; 仅当当前类型 0 命中(上次类型残留/选错)时,
      // 按识别字段切到命中最多的类型——防订单文件显示库存字段集
      if (Object.keys(a).length === 0) {
        const _typeHits: Record<string, number> = {}
        for (const _t of Object.keys(TARGET_FIELDS)) _typeHits[_t] = 0
        ;(d.columns || []).forEach(c => {
          const _k0 = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
          if (_k0) {
            for (const _t of Object.keys(TARGET_FIELDS)) {
              if (TARGET_FIELDS[_t].some(_f => _f.t === _k0)) _typeHits[_t] += 1
            }
          }
        })
        const _best = Object.entries(_typeHits).sort((x, y) => y[1] - x[1])[0]
        if (_best && _best[1] >= 2) {
          setTt(_best[0])
          ;(d.columns || []).forEach(c => {
            let key = customAlias[c.name] || ALIAS[c.name] || ALIAS_EXT[_nk(c.name)]
            if (key && TARGET_TARGETS[_best[0]] && !TARGET_TARGETS[_best[0]].includes(key)) key = ''
            if (key) a[c.name] = { target: key, type: 'string' }
          })
        }
      }
      setMp(a)
      goStep(1) // 停步映射字段界面, 用户确认/补映射后手动预览(智能识别仅辅助)
      setBs('')
      if (Object.keys(a).length > 0) toast.success(t('cl.detected_n', { n: Object.keys(a).length }))
      else toast.warning(t('cl.detect_none'))
    } catch (e) {
      toast.error(
        t('cl.req_error', {
          msg:
            e.message +
            (e.response?.data?.detail
              ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
              : ''),
        }),
      )
    }
    setBs('')
  }

  const preview = async () => {
    if (!f) {
      toast.error(t('cl.need_file'))
      setBs('')
      return
    }
    setBs(t('cl.previewing'))
    try {
      const r = await uploadRawBytes<CleanseData>(
        '/api/cleansing/preview?q_target=' +
          tt +
          '&q_channel=' +
          ch +
          '&q_conflict=' +
          hammerCleansingConflict +
          '&q_mp=' +
          encodeURIComponent(JSON.stringify(mp)),
        fBuf ? new Blob([fBuf]) : f,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || t('cl.preview_failed'))
        setBs('')
        return
      }
      // 2026-11-01 必填未映射 → 持久提示 + 不跳转(用户定案: 拦在映射页, 不放行预览)。
      // duration:0 不自动消失; 补映射后点「下一步·预览」走 goStep(2) 内 toast.clear() 清掉
      const _miss = missingRequired(tt, mp)
      if (_miss.length) {
        toast.add({
          type: 'warning',
          title: t('cl.miss_required', { cols: _miss.join('、') }),
          duration: 0,
        })
        setBs('')
        return
      }
      setPv(d)
      goStep(2)
      // 竖屏首屏空白根因之一: 映射页(可能几十列)滚到底才点到"下一步", 切到预览后窗口
      // 停在旧滚动位置 → 上方是新内容、人在空白区, 要"手动滚动"才见到表格。进预览必回顶。
      window.scrollTo(0, 0)
    } catch (e) {
      const msg = e.response?.data?.error || e.message || t('cl.req_failed')
      toast.error(t('cl.preview_failed_msg', { msg }))
    }
    setBs('')
  }

  // 表格列状态映射(清洗页锤子菜单入口; 渠道级隔离; 通用列值→档位, 当前 order_status)
  const [colMapOpen, setColMapOpen] = useState(false)
  const [colMap, setColMap] = useState<Record<string, ColMapRow[]>>({})
  const [colMapSaving, setColMapSaving] = useState(false)
  const [colMapCol, setColMapCol] = useState('order_status')
  const loadColMap = async () => {
    try {
      const r = await api.get<Record<string, ColMapRow[]>>(
        '/api/replenishment-config/column-value-map?channel=' + ch + '&t=' + freshStamp(),
      )
      setColMap(r.data && typeof r.data === 'object' ? r.data : {})
    } catch (e) {}
  }
  useEffect(() => {
    const h = () => {
      loadColMap()
      setColMapOpen(true)
    }
    window.addEventListener('cleansing-colmap-open', h)
    return () => window.removeEventListener('cleansing-colmap-open', h)
  }, [ch])
  const saveColMap = async () => {
    setColMapSaving(true)
    try {
      await api.put('/api/replenishment-config/column-value-map?channel=' + ch, { items: colMap })
      toast.success(t('cl.colmap_saved'))
      setColMapOpen(false)
    } catch (e) {
      toast.error(t('cl.save_failed', { msg: e.message || '' }))
    }
    setColMapSaving(false)
  }
  const execLock = useRef(false)
  // 2026-11-02 S0: 导入进度轮询的 setInterval 只在"任务结束"分支清理, 组件卸载时不管 ——
  // 用户切页后轮询仍在跑(每秒一次 status 请求 + 对已卸载组件 setState)。
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(
    () => () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    },
    [],
  )
  const [overlapAsk, setOverlapAsk] = useState<{ message: string } | null>(null)
  const doExecute = async () => {
    if (execLock.current) return
    execLock.current = true
    try {
      setBs(t('cl.cleansing'))
      // 2026-11-01 通用硬拦: PRESET_REQUIRED 全量必填(此前仅 4 个专项检查, 商品的 sku/名称
      // 等漏拦——预览入口的预警同源, 两处只维护一份 PRESET_REQUIRED)
      const _miss = missingRequired(tt, mp)
      if (_miss.length) {
        toast.error(t('cl.miss_before_write', { cols: _miss.join('、') }))
        setBs('')
        execLock.current = false
        return
      }
      // 库存类型必须映射 warehouse 列（否则传统多仓无法按仓库核算）
      const isInvType = tt === 'inventory' || tt === 'platform_inv' || tt === 'inventory_b'
      if (isInvType) {
        const hasWarehouse = Object.values(mp || {}).some(
          m => m && (m.target === 'warehouse' || m.t === 'warehouse'),
        )
        if (!hasWarehouse) {
          toast.error(t('cl.need_wh_inv'))
          setBs('')
          execLock.current = false
          return
        }
      }
      // 订单类型同样必须映射 warehouse 列（否则传统补货按仓库维度算日销，该订单销量会丢失）
      if (tt === 'order') {
        const hasWh = Object.values(mp || {}).some(
          m => m && (m.target === 'warehouse' || m.t === 'warehouse'),
        )
        if (!hasWh) {
          toast.error(t('cl.need_wh_order'))
          setBs('')
          execLock.current = false
          return
        }
        // 2026-10-15 SKU 必映射(写入 key order_no+sku——sku 缺失整批跳过——5000 单无明细根因)
        const hasSku = Object.values(mp || {}).some(m => m && (m.target === 'sku' || m.t === 'sku'))
        if (!hasSku) {
          toast.error(t('cl.need_sku_order'))
          setBs('')
          execLock.current = false
          return
        }
      }
      // 2026-11-01 商品必填: 是否做效期管理(决定后续出入库扣减口径: 是=精确+FEFO,
      //   否=按到货顺序 FIFO 且批次不带日期)。列必填在此硬拦; 值非法由后端回显到结果(§15.94)
      if (tt === 'product') {
        const hasExp = Object.values(mp || {}).some(
          m => m && (m.target === 'expiry_managed' || m.t === 'expiry_managed'),
        )
        if (!hasExp) {
          toast.error(t('cl.need_expiry'))
          setBs('')
          execLock.current = false
          return
        }
      }
      // 2026-10-16 GMV 回款必填 key(platform+flow_no 去重键——缺失整批跳过——曾 failed=15 无因可查):
      // platform 平台 + flow_no 流水号 必须映射
      if (tt === 'gmv_flow') {
        const hasPlat = Object.values(mp || {}).some(
          m => m && (m.target === 'platform' || m.t === 'platform'),
        )
        if (!hasPlat) {
          toast.error(t('cl.need_platform'))
          setBs('')
          execLock.current = false
          return
        }
        const hasFlow = Object.values(mp || {}).some(
          m => m && (m.target === 'flow_no' || m.t === 'flow_no'),
        )
        if (!hasFlow) {
          toast.error(t('cl.need_flow'))
          setBs('')
          execLock.current = false
          return
        }
      }
      // 2026-10-17 前端提前检测(职责: 检测拦截在前端/后端只写入): 三键优先 order_no|sku|barcode,
      // 空 barcode 落二键兜底——dup-check 纯只读秒级; 全重→拦截提示, 部分重叠→force_new 弹窗
      if (tt === 'order') {
        try {
          const _dc = await uploadRawBytes<CleanseData>(
            '/api/cleansing/dup-check?q_target=order&q_channel=' +
              ch +
              '&q_mp=' +
              encodeURIComponent(JSON.stringify(mp)),
            fBuf ? new Blob([fBuf]) : f,
          )
          const dd = _dc.data
          // 2026-11-02 S2-5 订单写入改为四维键(channel|order_no|sku|barcode) upsert, 重导幂等 ——
          //   所以这里只做**提示**不做拦截。拦截会让"源文件改了再导一次"永远更新不进去,
          //   覆盖逻辑变成死代码(与 2026-11-01 库存那次是同一类错误)。
          if (dd && dd.ok !== false) {
            if (dd.duplicate) {
              toast.warning(t('cl.dup_all', { n: dd.new + dd.overlap || 0 }))
            } else if (dd.overlap) {
              setOverlapAsk({
                message: t('cl.overlap_ask', { o: dd.overlap, n: dd.new }),
              })
              setBs('')
              execLock.current = false
              return
            }
          }
        } catch (e) {
          /* dup-check 降级放行——后端二键兜底 */
        }
      }
      try {
        const r = await uploadRawBytes<CleanseData>(
          '/api/cleansing/execute-async?q_target=' +
            tt +
            '&q_channel=' +
            ch +
            '&q_conflict=' +
            hammerCleansingConflict +
            '&q_mp=' +
            encodeURIComponent(JSON.stringify(mp)),
          fBuf ? new Blob([fBuf]) : f,
        ) // raw 直传 90s
        const d = r.data
        if (!d.ok) {
          toast.error(d.error || t('cl.submit_failed'))
          setBs('')
          execLock.current = false
          return
        }
        // 同步结果(<1000行后端请求内完成返回success/failed——同步分支) → 直显, 不走轮询
        if (d.success !== undefined) {
          // 2026-10-15 重复提前检测: 后端返回 duplicate(全部同键已存在——二次导入同表)→ 跳过提示(非'清洗完成'——秒级拦截)
          if (d.duplicate) {
            setRes({
              success: d.success,
              failed: d.failed,
              message: d.message || t('cl.imported_before'),
            })
            goStep(3)
            setBs('')
            execLock.current = false
            toast.warning(d.message || t('cl.imported_skip', { n: d.failed }))
            return
          }
          // 2026-10-16 部分重叠(底部弹窗决策): X 条已存在(新增 Y 条)——确认只写新增?
          if (d.overlap_partial) {
            setOverlapAsk({ message: d.message || t('cl.has_duplicates') })
            setBs('')
            execLock.current = false
            return
          }
          setRes(d)
          goStep(3)
          setBs('')
          execLock.current = false
          toast.success(t('cl.done_into', { ch: channelName({ code: ch }) }))
          return
        }
        const totalRows = d.total_rows || d.total || '?'
        // 2026-10-15 异步连贯: 后台任务已创建 → 立即 toast"已提交" + 立即跳完成页(不留在预览页防误点"确认写入")
        // 完成页显示区分: 异步=已提交 N 条(指引任务页查看进度); 同步=成功 N 条(上方 d.success 分支)
        setRes({
          async: true,
          taskId: d.task_id,
          submitted: totalRows,
          message: t('cl.submitted_n', { n: totalRows }),
        })
        goStep(3)
        setBs('')
        execLock.current = false
        toast.add({
          type: 'success',
          title: t('cl.submitted_n', { n: totalRows }),
          duration: 6000,
          action: {
            label: t('cl.view_progress'),
            handler: () => {
              window.__setPage && window.__setPage('tasks')
            },
          },
        })
        // 2026-10-15 修复: 此处曾 return 提前(轮询死代码——全靠后端保活 5 分钟)——移除——页面停留轮询小窗推进(前端主导)
        // 离开页面: localStorage 持久化 + 回前台 visibilitychange/保活顺带续跑兜底——两机制并存
        try {
          localStorage.setItem('c_clean_task', JSON.stringify({ task_id: d.task_id, channel: ch }))
        } catch {}
        // 前端轮询驱动标记(后端 continue_tasks 写 params.driver=backend——卡片动态显示)
        try {
          api.post('/api/cleansing/driver?task_id=' + d.task_id + '&driver=frontend&channel=' + ch)
        } catch {}
        let finished = false
        const threshold = setTimeout(() => {
          if (!finished) {
            toast.add({
              type: 'success',
              title: t('cl.bg_running'),
              duration: 6000,
              action: {
                label: t('cl.view_progress'),
                handler: () => {
                  window.__setPage && window.__setPage('tasks')
                },
              },
            })
            setBs('')
            execLock.current = false
          }
        }, 8000)
        // 本地轮询（页面内）
        const poll = setInterval(async () => {
          if (finished) return
          try {
            // 接力式: status 端点触发续跑并返回进度(done/error/running+page,total)
            const sr = await api.get<CleanseData>(
              '/api/cleansing/status?task_id=' + d.task_id + '&page_size=1000',
              { timeout: 150000 },
            ) // 2026-10-15 回小窗(1000): 四维评估大窗 263s+轮询超时积压(9分钟) vs 小窗每窗<40s 实时进度无积压——小窗优; 联动批量修复已使每窗更快
            // 2026-10-15 driver 每轮标记: 轮询活跃=前端推进(覆盖保活 backend 标签——保活只在无轮询时生效)
            try {
              api.post(
                '/api/cleansing/driver?task_id=' + d.task_id + '&driver=frontend&channel=' + ch,
              )
            } catch {}
            const sd = sr.data
            if (sd.status === 'done') {
              finished = true
              clearTimeout(threshold)
              clearInterval(poll)
              setRes({
                success: sd.success,
                failed: sd.failed,
                message: sd.message,
                failed_details: sd.failed_details || [],
              })
              goStep(3)
              setBs('')
              toast.success(t('cl.done_into', { ch: channelName({ code: ch }) }))
            } else if (sd.status === 'error') {
              finished = true
              clearTimeout(threshold)
              clearInterval(poll)
              toast.error(t('cl.failed_msg', { msg: sd.error }))
              setBs('')
            } else if (sd.status === 'running' && sd.total) {
              const _pg = Math.min(((sd.page || 0) * 5000 + 5000) / Math.max(sd.total, 1), 1)
              const _pct = Math.round(_pg * 100)
              setBs(
                t('cl.progress', {
                  pct: _pct,
                  done: Math.min((sd.page || 0) * 5000 + 5000, sd.total),
                  total: sd.total,
                }),
              )
            }
          } catch {
            if (!finished) {
              finished = true
              clearTimeout(threshold)
              clearInterval(poll)
              setBs('')
            }
          }
        }, 1000)
        pollRef.current = poll
        // 全局持久化
        try {
          localStorage.setItem(
            'c_cleansing_task',
            JSON.stringify({ task_id: d.task_id, progress: 0 }),
          )
        } catch {}
      } catch (e) {
        toast.error(
          t('cl.req_error', {
            msg:
              e.message +
              (e.response?.data?.detail
                ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
                : ''),
          }),
        )
        setBs('')
      }
    } finally {
      execLock.current = false
    }
  }

  // 部分重叠确认(底部弹窗): 确认→继续写入(同键由后端四维 upsert 覆盖, 不会翻倍)
  const confirmOverlap = async () => {
    if (!overlapAsk) return
    setOverlapAsk(null)
    setBs(t('cl.executing'))
    execLock.current = false
    try {
      const r = await api.post(
        '/api/cleansing/execute-async?force_new=1&q_target=' +
          tt +
          '&q_channel=' +
          ch +
          '&q_conflict=' +
          hammerCleansingConflict +
          '&q_mp=' +
          encodeURIComponent(JSON.stringify(mp)),
        fBuf ? new Blob([fBuf]) : f,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || t('cl.submit_failed'))
        setBs('')
        return
      }
      if (d.success !== undefined) {
        setRes(d)
        goStep(3)
        setBs('')
        execLock.current = false
        toast.success(t('cl.done_into', { ch: channelName({ code: ch }) }))
        return
      }
      setRes({
        async: true,
        taskId: d.task_id,
        submitted: d.total_rows || d.total || '?',
        message: t('cl.submitted_n', { n: d.total_rows || d.total || '?' }),
      })
      goStep(3)
      setBs('')
      execLock.current = false
      toast.success(t('cl.submitted_bg'))
    } catch (e) {
      toast.error(t('cl.req_error', { msg: e.message || '' }))
      setBs('')
    }
  }
  const quickExecute = async () => {
    setBs(t('cl.executing'))
    try {
      const r = await uploadRawBytes<CleanseData>(
        '/api/cleansing/preview?q_target=' +
          tt +
          '&q_channel=' +
          ch +
          '&q_conflict=' +
          hammerCleansingConflict +
          '&q_mp=' +
          encodeURIComponent(JSON.stringify(mp)),
        fBuf ? new Blob([fBuf]) : f,
      )
      const d = r.data
      if (!d.ok) {
        toast.error(d.error || t('cl.submit_failed'))
        setBs('')
        return
      }
      setPv(d)
      doExecute()
    } catch (e) {
      toast.error(
        t('cl.req_error', {
          msg:
            e.message +
            (e.response?.data?.detail
              ? '：' + JSON.stringify(e.response.data.detail).slice(0, 200)
              : ''),
        }),
      )
      setBs('')
    }
  }

  useEffect(() => {
    try {
      const saved = localStorage.getItem('c_last_tt')
      if (saved) setTt(saved)
    } catch {}
  }, [])
  useEffect(() => {
    try {
      localStorage.setItem('c_last_tt', tt)
    } catch {}
  }, [tt])

  const btn = (label, onClick, color = 'primary') => (
    <button onClick={onClick} disabled={!!bs} className={`btn btn-${bs ? 'ghost' : color}`}>
      {label}
    </button>
  )

  return (
    <div className="card">
      {s > 0 && (
        <div className="step-indicator">
          {[t('cl.step_upload'), t('cl.step_map'), t('cl.step_preview'), t('cl.step_done')].map(
            (l, i) => (
              <span
                key={i}
                className={'step' + (s === i ? ' active' : '') + (s > i ? ' done' : '')}
              >
                {s > i ? (
                  <IconCheck
                    size={12}
                    style={{ display: 'inline', verticalAlign: 'middle', marginRight: 2 }}
                  />
                ) : (
                  ''
                )}
                {l}
              </span>
            ),
          )}
          {bs &&
            (bs.includes('%') ? (
              <div className="step w-full">
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 'var(--font-sm)',
                    marginBottom: 4,
                    color: 'var(--primary)',
                  }}
                >
                  <span>
                    <IconLoading
                      size={12}
                      style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }}
                    />
                    {bs.split('%')[0]}%
                  </span>
                  <span>{bs.split('(')[1]?.replace(')', '') || ''}</span>
                </div>
                <div
                  style={{
                    height: 6,
                    background: 'var(--border)',
                    borderRadius: 'var(--radius-full)',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{
                      height: '100%',
                      width: bs.split('%')[0] + '%',
                      background: 'var(--primary)',
                      borderRadius: 'var(--radius-full)',
                      transition: 'width 0.3s',
                    }}
                  ></div>
                </div>
              </div>
            ) : (
              <span className="step" style={{ color: 'var(--primary)' }}>
                <IconLoading
                  size={12}
                  style={{ display: 'inline', verticalAlign: 'middle', marginRight: 4 }}
                />
                {bs}...
              </span>
            ))}
        </div>
      )}

      {s === 0 && (
        <div style={{ textAlign: 'center', padding: '28px 16px' }}>
          <div style={{ fontSize: 16, fontWeight: 'var(--fw-bold)', marginBottom: 6 }}>
            {t('cl.pick_type')}
          </div>
          <div
            style={{
              fontSize: 'var(--font-xs)',
              color: 'var(--muted2)',
              marginBottom: 18,
              letterSpacing: 0.2,
            }}
          >
            {t('cl.pick_type_sub')}
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginBottom: 24 }}>
            <select
              value={tt}
              onChange={e => {
                setTt(e.target.value)
                setF(null)
                setFBuf(null)
              }}
              style={{
                fontSize: 'var(--font-15)',
                padding: '11px 16px',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-full)',
                outline: 'none',
                background: 'var(--card)',
                width: 260,
                minHeight: 48,
              }}
            >
              <option value="order">{t('cl.opt_order')}</option>
              <optgroup label={t('cl.opt_inv_group')}>
                <option value="inventory">{t('cl.opt_inv_own')}</option>
                <option value="platform_inv">{t('cl.opt_inv_platform')}</option>
                <option value="inventory_b">{t('cl.opt_inv_b')}</option>
              </optgroup>
              <optgroup label={t('cl.opt_flow_group')}>
                <option value="inbound">{t('cl.opt_inbound')}</option>
                <option value="outbound">{t('cl.opt_outbound')}</option>
              </optgroup>
              <option value="product">{t('cl.opt_product')}</option>
              <option value="supplier">{t('cl.opt_supplier')}</option>
              <option value="gmv_flow">{t('cl.opt_gmv')}</option>
            </select>
          </div>
          <label
            className="btn btn-primary"
            style={{
              minHeight: 48,
              width: 260,
              padding: 0,
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
              borderRadius: 'var(--radius-full)',
              fontSize: 'var(--font-15)',
              fontWeight: 'var(--fw-semibold)',
              opacity: bs ? 0.65 : 1,
              pointerEvents: bs ? 'none' : 'auto',
            }}
          >
            {bs ? t('cl.detecting') : t('cleansing.select_file')}
            <input
              type="file"
              accept=".csv,.xlsx"
              style={{ display: 'none' }}
              onChange={e => {
                const fi = e.target.files[0]
                if (fi) {
                  const rd = new FileReader()
                  rd.onload = () => {
                    const buf = rd.result as ArrayBuffer
                    setFBuf(buf)
                    detect(fi, buf)
                  }
                  rd.readAsArrayBuffer(fi)
                }
              }}
            />
          </label>
          {bs && (
            <div
              style={{
                marginTop: 14,
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                gap: 5,
                color: 'var(--primary)',
                fontSize: 'var(--font-13)',
              }}
            >
              <IconLoading size={14} /> {t('cl.detecting_file')}
            </div>
          )}
          <div
            style={{
              marginTop: bs ? 8 : 14,
              fontSize: 'var(--font-xs)',
              color: 'var(--muted2)',
              letterSpacing: 0.2,
            }}
          >
            {t('cl.upload_hint')}
          </div>
        </div>
      )}

      {s === 1 && (
        <MappingStep
          cols={cols}
          mp={mp}
          setMp={setMp}
          tt={tt}
          cf={cf}
          onBack={() => goStep(0)}
          onPreview={() => preview()}
          onQuick={() => quickExecute()}
          saveAlias={saveAlias}
        />
      )}

      {s === 2 && !pv && (
        <div style={{ padding: 24 }}>
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className="skeleton" style={{ height: 32, marginBottom: 6 }} />
          ))}
        </div>
      )}
      {s === 2 && pv && (
        <PreviewStep
          pv={pv}
          mp={mp}
          cols={cols}
          tr={tr}
          tt={tt}
          bs={bs}
          cf={cf}
          onBack={() => {
            goStep(1)
            setPv(null)
          }}
          onExecute={() => doExecute()}
        />
      )}

      {s === 3 && res && (
        <ResultStep
          res={res}
          fileName={f?.name || ''}
          onRestart={() => {
            goStep(0)
            setF(null)
            setCols([])
            setTr(0)
            setMp({})
            setPv(null)
            setRes(null)
          }}
          onFile={(fi, buf) => {
            setFBuf(buf)
            detect(fi, buf)
          }}
        />
      )}

      {colMapOpen && (
        <ColMapSheet
          colMap={colMap}
          setColMap={setColMap}
          colMapCol={colMapCol}
          setColMapCol={setColMapCol}
          colMapSaving={colMapSaving}
          channel={ch}
          onClose={() => setColMapOpen(false)}
          onSave={saveColMap}
        />
      )}
      {overlapAsk && (
        <ConfirmDialog
          open={!!overlapAsk}
          title={t('cl.detect_dup')}
          desc={overlapAsk.message}
          confirmLabel={t('cl.confirm_new')}
          onCancel={() => setOverlapAsk(null)}
          onConfirm={() => {
            confirmOverlap()
          }}
        />
      )}
    </div>
  )
}
