// 模板管理弹窗(清洗页锤子菜单入口): 映射模板加载/应用/保存 + 自定义字段(合并)
// 与映射页分离前样式一致; 应用模板/字段变更通过事件通知映射页
import React, { useState, useEffect } from "react"
import { api } from "../api/client"
import { useToast } from "../components/Toast"

interface Props { tt: string; onClose: () => void }

export default function TemplateManageDialog({ tt, onClose }: Props) {
  const toast = useToast()
  const [templates, setTemplates] = useState([])
  const [tmplName, setTmplName] = useState('')
  const [cf, setCf] = useState(() => { try { return JSON.parse(localStorage.getItem('c_cf') || '[]') } catch { return [] } })
  const saveCf = (v) => { setCf(v); try { localStorage.setItem('c_cf', JSON.stringify(v)) } catch {} }

  const loadTemplates = async () => { try { const r = await api.get('/api/cleansing/templates'); setTemplates(r.data || []) } catch(e) {} }
  useEffect(() => { loadTemplates() }, [])

  const changeCf = (next) => {
    saveCf(next)
    window.dispatchEvent(new Event('custom-fields-changed'))
  }
  const addField = () => changeCf([...cf, { t: 'field_' + Date.now(), l: '自定义字段', tp: 'string' }])
  const delField = (i) => changeCf(cf.filter((_, k) => k !== i))

  const fieldStyle: any = { fontSize: 15, padding: '9px 12px', border: '1px solid var(--border)', borderRadius: 99, outline: 'none', background: 'var(--card)' }
  const btnG: any = { fontSize: 13, borderRadius: 99, cursor: 'pointer', minHeight: 38 }

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'transparent' }} />
      <div style={{ position: 'fixed', left: 0, right: 0, bottom: 'calc(env(safe-area-inset-bottom) + 14px)', zIndex: 9999, display: 'flex', justifyContent: 'center', padding: '0 14px', pointerEvents: 'none' }}>
        <div onClick={(e) => e.stopPropagation()} className="material-regular" style={{ width: '100%', maxWidth: 600, borderRadius: 32, padding: '18px 14px calc(14px + env(safe-area-inset-bottom))', boxShadow: 'var(--shadow-sheet), inset 0 1px 0 rgba(255,255,255,0.25)', pointerEvents: 'auto', maxHeight: '70vh', overflowY: 'auto' }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 12, textAlign: 'center', color: 'var(--text)' }}>模板管理</div>

        {/* 模板区 */}
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>映射模板</div>
        <div style={{ background: 'var(--card)', borderRadius: 16, padding: 12, marginBottom: 8 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <select value="" onChange={(e) => { const v = e.target.value; if (v) { try { const m = typeof v === 'string' && v.startsWith('{') ? JSON.parse(v) : v; if (m && typeof m === 'object') { window.dispatchEvent(new CustomEvent('apply-template', { detail: m })); toast.success('模板已应用') } } catch (e2) {} } }}
              style={{ flex: 1, fontSize: 14, padding: '9px 12px', border: '1px solid var(--border)', borderRadius: 99, minHeight: 40, outline: 'none', background: 'var(--card)' }}>
              <option value="">选择映射模板...</option>
              {(templates.filter(t => t.doc_type === tt)).map(t => <option key={t.id} value={t.mapping}>{t.name}</option>)}
              {templates.filter(t => t.doc_type !== tt).length > 0 && <option disabled style={{ color: 'var(--muted2)', fontSize: 11 }}>── {tt === 'order' ? '库存' : '订单'}模板（{templates.filter(t => t.doc_type !== tt).length}个） ──</option>}
            </select>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 4 }}>
          <input value={tmplName} onChange={e => setTmplName(e.target.value)} placeholder="新模板名称" style={{ ...fieldStyle, flex: '1 1 150px', minWidth: 120 }} />
          <button onClick={async () => {
            const n = tmplName.trim(); if (!n) return toast.error('请输入模板名称')
            try {
              const r = await api.post('/api/cleansing/templates', { name: n, doc_type: tt, mapping: (window as any).__curMp || {} })
              const msg = r?.data?.message || '模板已保存'
              setTmplName(''); loadTemplates(); toast.success(msg)
            } catch (e) { toast.error('模板保存失败: ' + (e.response?.data?.detail || e.message)) }
          }} className="clickable" style={{ ...btnG, padding: '10px 18px', background: 'var(--primary)', color: '#fff', border: 'none', flexShrink: 0 }}>保存模板</button>
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted2)', marginBottom: 12 }}>提示：保存前先在映射页完成字段映射，模板会记录当前映射关系</div>

        {/* 自定义字段区 */}
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, marginTop: 4 }}>自定义字段 <span className="small muted" style={{ fontSize: 11, fontWeight: 400 }}>可自定义映射目标字段名</span></div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 20, padding: 12, background: 'var(--bg)' }}>
          {cf.map((f, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
              <input value={f.l} onChange={e => changeCf(cf.map((x, k) => k === i ? { ...x, l: e.target.value } : x))} placeholder="字段名" style={{ ...fieldStyle, flex: '1 1 150px', minWidth: 120 }} />
              <select value={f.tp} onChange={e => changeCf(cf.map((x, k) => k === i ? { ...x, tp: e.target.value } : x))} style={{ ...fieldStyle, flexShrink: 0, padding: '9px 10px' }}>
                <option value="string">文本</option><option value="number">数字</option><option value="date">日期</option>
              </select>
              <button onClick={() => delField(i)} className="clickable" style={{ background: 'rgba(225,29,72,0.12)', border: 'none', borderRadius: 99, cursor: 'pointer', padding: '9px 14px', fontSize: 13, color: 'var(--danger)', flexShrink: 0 }}>删除</button>
            </div>
          ))}
          <button onClick={addField} className="clickable" style={{ padding: '9px 16px', fontSize: 13, border: '1px dashed #94a3b8', borderRadius: 99, background: 'var(--card)', cursor: 'pointer', color: 'var(--muted)', width: '100%' }}>+ 添加自定义字段</button>
        </div>

        <div onClick={onClose} className="clickable" style={{ borderRadius: 22, padding: 12, marginTop: 10, background: 'var(--primary)', textAlign: 'center', cursor: 'pointer' }}>
            <span style={{ fontSize: 15, fontWeight: 600, color: '#fff' }}>关闭</span>
          </div>
        </div>
      </div>
    </>
  )
}