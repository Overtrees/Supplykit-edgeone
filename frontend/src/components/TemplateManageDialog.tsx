// 模板管理弹窗(清洗页锤子菜单入口): 映射模板加载/应用/保存 + 自定义字段(合并)
// 与映射页分离前样式一致; 应用模板/字段变更通过事件通知映射页
import React, { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import { useToast } from '../components/Toast'
import {
  presetMapping,
  presetExample,
  presetRequired,
  docTypeLabel,
} from '../pages/cleansing/fields'
import { t } from '../locale'

interface Props {
  tt: string
  onClose: () => void
}

export default function TemplateManageDialog({ tt, onClose }: Props) {
  const toast = useToast()
  const [templates, setTemplates] = useState([])
  const [tmplName, setTmplName] = useState('')
  const [selId, setSelId] = useState<string | number>('')
  const [cfOpen, setCfOpen] = useState(false) // 自定义字段默认收起(与模板语义分离)
  const [cf, setCf] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('c_cf') || '[]')
    } catch {
      return []
    }
  })
  const saveCf = v => {
    setCf(v)
    try {
      localStorage.setItem('c_cf', JSON.stringify(v))
    } catch {}
  }

  const loadTemplates = async () => {
    try {
      const r = await api.get('/api/cleansing/templates')
      setTemplates(r.data || [])
    } catch (e) {}
  }
  useEffect(() => {
    loadTemplates()
  }, [])

  const changeCf = next => {
    saveCf(next)
    window.dispatchEvent(new Event('custom-fields-changed'))
  }
  // 2026-10-27: 模板应用抽为函数(下拉 + 列表点击共用)
  const applyTemplate = (tid: any) => {
    const tpl: any = templates.find((t: any) => t.id == tid)
    if (!tpl) return
    try {
      const m = typeof tpl.mapping === 'string' ? JSON.parse(tpl.mapping) : tpl.mapping
      if (!m || typeof m !== 'object') return
      window.dispatchEvent(new CustomEvent('apply-template', { detail: m }))
      const _cfm = tpl.cf_meta
      if (Array.isArray(_cfm) && _cfm.length) {
        const _cur = (() => {
          try {
            return JSON.parse(localStorage.getItem('c_cf') || '[]')
          } catch {
            return []
          }
        })()
        const _merged = [..._cur]
        for (const f of _cfm) {
          if (f?.t && !_merged.some((x: any) => x.t === f.t)) _merged.push(f)
        }
        localStorage.setItem('c_cf', JSON.stringify(_merged))
        window.dispatchEvent(new Event('custom-fields-changed'))
      }
      setSelId(tid)
      toast.success(t('tpl.applied'))
    } catch {}
  }
  const delTemplate = async (tid: any, name: string) => {
    if (!window.confirm(t('tpl.del_confirm', { name }))) return
    try {
      await api.delete('/api/cleansing/templates/' + tid)
      setSelId('')
      loadTemplates()
      toast.success(t('common.deleted'))
    } catch {
      toast.error(t('common.delete_failed'))
    }
  }
  const addField = () =>
    changeCf([
      ...cf,
      { t: 'field_' + Date.now(), l: t('tpl.cf'), tp: 'string' },
    ])
  const delField = i => changeCf(cf.filter((_, k) => k !== i))

  const fieldStyle: any = {
    fontSize: 15,
    padding: '9px 12px',
    border: '1px solid var(--border)',
    borderRadius: 99,
    outline: 'none',
    background: 'var(--card)',
  }
  const btnG: any = { fontSize: 13, borderRadius: 99, cursor: 'pointer', minHeight: 38 }

  return createPortal(
    <>
      <div
        onClick={onClose}
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
            borderRadius: 32,
            padding: '18px 16px calc(16px + env(safe-area-inset-bottom))',
            boxShadow: 'var(--shadow-sheet)',
            pointerEvents: 'auto',
            maxHeight: '70vh',
            overflowY: 'auto',
          }}
        >
          <div className="tpl-title">
            <span>{t('tpl.title')}</span>
          </div>

          {/* 预设模块(2026-10-27): 单字段源派生——内置预设+下载模板同源, 新列自动同步 */}
          <div className="tpl-section-title">
            <span>{t('tpl.preset_title', { type: docTypeLabel(tt) })}</span>
            <button
              onClick={() => {
                // 表头=预设列(label 全字段, 与内置映射同源)
                const _src = presetMapping(tt) as Record<string, string>
                const _req = presetRequired(tt)
                const _ex = presetExample(tt)
                const _cols = Object.entries(_src) // [label, target]
                if (!_cols.length) return toast.error(t('tpl.no_preset'))
                // 表头: 列名(必)/(选)——必选按业务导入链去重键/汇总必需字段
                const head = _cols.map(
                  ([label, target]) =>
                    label +
                    (_req.includes(String(target)) ? t('tpl.req') : t('tpl.opt')),
                )
                // 第二行示例: 仅必选列给样例, 可选列留空
                const sample = _cols.map(([, target]) => _ex[String(target)] || '')
                const csv = '\uFEFF' + head.join(',') + '\n' + sample.join(',') + '\n'
                const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = t('tpl.csv_name', { type: docTypeLabel(tt) })
                a.click()
                setTimeout(() => URL.revokeObjectURL(url), 1000)
                toast.success(t('tpl.downloaded'))
              }}
              className="clickable tpl-btn tpl-btn-primary"
              style={{ padding: '7px 14px' }}
            >
              {t('tpl.dl_csv')}
            </button>
          </div>
          {/* 模板区 */}
          <div className="tpl-section-title">{t('tpl.mine')}</div>
          <div style={{ marginBottom: 8 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <select
                value={selId as any}
                onChange={e => {
                  const v = e.target.value
                  if (v) applyTemplate(v)
                }}
                style={{
                  flex: 1,
                }}
                className="tpl-field"
              >
                <option value="">{t('tpl.pick_ph')}</option>
                {templates
                  .filter(t => t.doc_type === tt)
                  .map(t => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                {templates.filter(t => t.doc_type !== tt).length > 0 && (
                  <option disabled style={{ color: 'var(--muted2)', fontSize: 11 }}>
                    {t('tpl.other_group', {
                      type: docTypeLabel(tt === 'order' ? 'inventory' : 'order'),
                      n: templates.filter(x => x.doc_type !== tt).length,
                    })}
                  </option>
                )}
              </select>
            </div>
            {/* 2026-10-27: 已存模板列表移除(下拉即选即应用, 列表冗余); 仅保留空态引导 */}
            {templates.filter((t: any) => t.doc_type === tt).length === 0 && (
              <div className="tpl-empty">{t('tpl.empty')}</div>
            )}
          </div>
          <div
            className="tpl-section"
            style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}
          >
            <input
              value={tmplName}
              onChange={e => setTmplName(e.target.value)}
              placeholder={t('tpl.name_ph')}
              className="tpl-field"
              style={{ flex: '1 1 150px', minWidth: 120 }}
            />
            <button
              onClick={async () => {
                const n = tmplName.trim()
                if (!n) return toast.error(t('tpl.need_name'))
                const _mp = (window as any).__curMp || {}
                if (!_mp || Object.keys(_mp).length === 0) {
                  return toast.error(t('tpl.need_mapping'))
                }
                // 2026-10-27 根治: 模板内嵌被引用的自定义字段定义——跨设备/清缓存后仍能还原字段名
                // (曾只存 mapping, cf 存本机 localStorage, 换设备后 target 无中文名)
                const _usedTargets = Object.values(_mp)
                  .map((v: any) => v && v.target)
                  .filter(Boolean)
                const cfMeta = cf.filter(f => f.t && _usedTargets.includes(f.t))
                // 2026-10-27 同名覆盖二次确认(曾静默覆盖已有模板——手滑改名即改掉别人/自己的模板)
                const _dup = templates.find((t: any) => t.name === n && t.doc_type === tt)
                if (_dup) {
                  const okOverwrite = window.confirm(
                    t('tpl.dup_confirm', { name: n, type: docTypeLabel(tt) }),
                  )
                  if (!okOverwrite) return
                }
                try {
                  const r = await api.post('/api/cleansing/templates', {
                    name: n,
                    doc_type: tt,
                    mapping: _mp,
                    cf_meta: cfMeta,
                  })
                  const msg = r?.data?.message || t('tpl.saved')
                  setTmplName('')
                  // 一次拉取: 刷新列表 + 自动选中刚保存的模板(name+doc_type 定位)
                  try {
                    const rr = await api.get('/api/cleansing/templates')
                    const _list = rr.data || []
                    setTemplates(_list)
                    const _hit = _list.find((t: any) => t.name === n && t.doc_type === tt)
                    if (_hit) setSelId(_hit.id)
                  } catch {
                    loadTemplates()
                  }
                  toast.success(msg)
                } catch (e) {
                  toast.error(
                    t('tpl.save_failed', { msg: e.response?.data?.detail || e.message }),
                  )
                }
              }}
              className="clickable tpl-btn tpl-btn-primary"
              style={{ padding: '10px 18px', flexShrink: 0 }}
            >
              {t('tpl.save_btn')}
            </button>
          </div>
          <div className="tpl-hint">
            <span title={t('tpl.save_hint_title')}>{t('tpl.save_hint')}</span>
          </div>

          {/* 自定义字段区 */}
          {/* 2026-10-27 UX: 自定义字段与模板无关(本机偏好), 默认收起避免喧宾夺主 */}
          <div
            onClick={() => setCfOpen(v => !v)}
            className="clickable tpl-section-title"
            style={{ marginBottom: cfOpen ? 8 : 0, marginTop: 4 }}
          >
            <span>
              {t('tpl.cf')}
              <span
                className="small muted"
                style={{ fontSize: 11, fontWeight: 400, marginLeft: 6 }}
              >
                {cf.length ? t('tpl.cf_n', { n: cf.length }) : t('tpl.cf_none')}
              </span>
            </span>
            <span style={{ fontSize: 12, color: 'var(--muted2)' }}>
              {cfOpen ? t('tpl.collapse') : t('tpl.expand')}
            </span>
          </div>
          <div className="tpl-cf-box" style={{ display: cfOpen ? 'block' : 'none' }}>
            {cf.map((f, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginBottom: 8,
                  flexWrap: 'wrap',
                }}
              >
                <input
                  value={f.l}
                  onChange={e =>
                    changeCf(cf.map((x, k) => (k === i ? { ...x, l: e.target.value } : x)))
                  }
                  placeholder={t('tpl.field_name_ph')}
                  className="tpl-field"
                  style={{ flex: '1 1 150px', minWidth: 120 }}
                />
                <select
                  value={f.tp}
                  onChange={e =>
                    changeCf(cf.map((x, k) => (k === i ? { ...x, tp: e.target.value } : x)))
                  }
                  className="tpl-field"
                  style={{ flexShrink: 0, padding: '9px 10px' }}
                >
                  <option value="string">{t('tpl.tp_string')}</option>
                  <option value="number">{t('tpl.tp_number')}</option>
                  <option value="date">{t('tpl.tp_date')}</option>
                </select>
                <button
                  onClick={() => delField(i)}
                  className="clickable tpl-btn tpl-btn-danger"
                  style={{ padding: '9px 14px', flexShrink: 0 }}
                >
                  {t('common.delete')}
                </button>
              </div>
            ))}
            <button
              onClick={addField}
              className="clickable tpl-btn tpl-btn-ghost"
              style={{ borderStyle: 'dashed', color: 'var(--muted)', width: '100%' }}
            >
              {t('tpl.add_cf')}
            </button>
          </div>

          {/* 2026-10-27 UX: 关闭降为次要按钮(曾主色蓝底与'保存模板'同级 → 视觉上关闭是主操作) */}
          <div onClick={onClose} className="clickable sheet-cancel" style={{ marginTop: 10 }}>
            {t('tpl.close')}
          </div>
        </div>
      </div>
    </>,
    document.body,
  )
}
