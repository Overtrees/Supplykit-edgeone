import React, { useState, useEffect, useRef } from 'react'
import { useAppStore } from '../store/useAppStore'
import { clearCache, clearInflight } from '../api/client'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { Group, Row, LastRow } from '../components/ListGroup'

const VERSION = (typeof __APP_VERSION__ !== 'undefined' && __APP_VERSION__) ? __APP_VERSION__ : '2.0.0'  // 构建注入(package.json version), 发版改 package.json
const BUILD = new Date().toISOString().slice(0,10)
const API = import.meta.env.VITE_API_BASE_URL || ''


export default function SettingsPage() {
  const toast = useToast()
  const { channel, wsStatus } = useAppStore()
  const [, setDbSize] = useState('')
  const [confirm, setConfirm] = useState(null) // {type:'fill'|'reset'}
  // 开发者模式(彩蛋入口): 连续点版本号 6 次开启, localStorage 持久化(iOS/Android 惯例)
  const devTap = useRef(0)
  const [devMode, setDevMode] = useState(() => { try { return localStorage.getItem('c_dev_mode') === '1' } catch { return false } })





  // 种子填充状态: 由 App 全局轮询负责(续跑有后端并发锁防护), 完成事件恢复按钮状态
  useEffect(() => {
    const h = () => { setSeeding(false) }
    window.addEventListener('seed-done', h)
    window.addEventListener('seed-error', h)
    return () => { window.removeEventListener('seed-done', h); window.removeEventListener('seed-error', h) }
  }, [])


  const [seeding, setSeeding] = useState(() => { try { return !!localStorage.getItem('c_seed_task') } catch { return false } })
  const [resetting, setResetting] = useState(false)
  // 告警推送 webhook 配置（全局，存 replenishment_config.webhook_url）
  const [webhookUrl, setWebhookUrl] = useState('')
  const [webhookSaving, setWebhookSaving] = useState(false)
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(API + '/api/replenishment-config?channel=jd', { headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })() } })
        const d = await r.json()
        if (d?.data?.webhook_url) setWebhookUrl(d.data.webhook_url)
      } catch {}
    })()
  }, [])
  const saveWebhook = async () => {
    setWebhookSaving(true)
    try {
      const r = await fetch(API + '/api/replenishment-config?channel=jd', { method: 'PUT', headers: { 'Authorization': 'Bearer ' + (() => { try { return localStorage.getItem('c_token') } catch { return '' } })(), 'Content-Type': 'application/json' }, body: JSON.stringify({ webhook_url: webhookUrl.trim() }) })
      const d = await r.json()
      if (d.ok) toast.success(webhookUrl.trim() ? '已保存，新告警将推送到该地址' : '已保存，告警推送已关闭')
      else toast.error('保存失败: ' + (d.error || ''))
    } catch(e) { toast.error('保存失败: ' + e.message) }
    setWebhookSaving(false)
  }

  const doSeed = async () => {
    setConfirm(null); setSeeding(true)
    try {
      const r = await fetch(API + '/api/seed/fill', {method:'POST', headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok) {
        if (d.data?.requires_reset) { toast.error('已有数据，请先重置'); setSeeding(false); setConfirm('reset'); return }
        const taskId = d.data?.task_id
        if (taskId) { try { localStorage.setItem('c_seed_task', taskId) } catch {} }
        toast.add({type:'success', title:'填充任务已提交', duration:6000, action:{label:'查看进度 →', handler:()=>{ window.__setPage && window.__setPage('tasks') }}})
      } else { toast.error('填充失败: ' + (d.error || '')); setSeeding(false) }
    } catch { toast.error('填充失败'); setSeeding(false) }
  }

  const doReset = async () => {
    setConfirm(null)
    setResetting(true)
    try {
      const r = await fetch(API + '/api/seed/reset', {method:'POST', headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
      const d = await r.json()
      if (d.ok && d.data?.task_id) {
        try { localStorage.setItem('c_reset_task', d.data.task_id) } catch {}
        toast.success('重置任务已提交，后台清理中...')
        // 轮询等待重置完成
        const poll = setInterval(async () => {
          try {
            const sr = await fetch(API + '/api/seed/fill/status?task_id=' + d.data.task_id, {headers:{'Authorization':'Bearer ' + (()=>{try{return localStorage.getItem('c_token')}catch{return ''}})()}})
            const sd = await sr.json()
            if (sd.data?.status === 'done' || sd.data?.status === 'error') {
              clearInterval(poll)
              try { localStorage.removeItem('c_reset_task') } catch {}
              clearCache(); clearInflight()
              useAppStore.setState({ dashboard: null, alerts: [], stockRisk: [] })
              toast.success('数据已重置，即将刷新')
              setTimeout(() => window.location.reload(), 1500)
            }
          } catch { clearInterval(poll); setResetting(false) }
        }, 2000)
      } else {
        toast.error('重置失败: ' + (d.error || ''))
        setResetting(false)
      }
    } catch { toast.error('重置失败'); setResetting(false) }
  }

  return <>
    <div style={{padding:'16px 0',maxWidth:500,margin:'0 auto'}}>
      <Group title="操作">
        <LastRow label="回收站" sub="查看已删除的规则和订单，可恢复或永久删除" onClick={() => { try { (window as any).__setPage && (window as any).__setPage('recycle') } catch(e) {} }} />
        {devMode && <LastRow label="开发者模式" sub="版本/构建信息 · 异常日志分析" onClick={() => { try { (window as any).__setPage && (window as any).__setPage('devmode') } catch(e) {} }} />}
      </Group>

      <Group title="系统信息">
        <LastRow label="版本号" value={`v${VERSION}`} onClick={() => {
            devTap.current += 1
            if (devTap.current >= 6) { try { localStorage.setItem('c_dev_mode', '1') } catch {}; setDevMode(true); toast.success('开发者模式已开启') } else if (devTap.current >= 4) { toast.info(`再点 ${6 - devTap.current} 次开启开发者模式`) }
          }} />
      </Group>

      <Group title="界面">
        <LastRow label="重置欢迎页" sub="重新显示首次使用引导" onClick={() => { try { localStorage.removeItem('c_welcome_seen') } catch {} toast.success('欢迎页已重置') }} />
      </Group>

      <Group title="种子数据">
        <Row label="一键填充" sub="生成 2,000 SKU × 60 天 × 10 万条模拟数据" onClick={() => setConfirm('fill')} loading={seeding} />
        <LastRow label="一键重置" sub="清空所有数据恢复初始状态" onClick={() => setConfirm('reset')} danger loading={resetting} />
      </Group>

      <Group title="告警推送">
        <div style={{padding:14}}>
          <div style={{fontSize:'var(--font-13)',fontWeight:600,marginBottom:4}}>Webhook 地址</div>
          <div className="small muted" style={{fontSize:'var(--font-xs)',marginBottom:8}}>钉钉/企业微信机器人地址，新告警每 30 分钟推送到该地址（留空不推送）</div>
          <div style={{display:'flex',gap:8}}>
            <input value={webhookUrl} onChange={e=>setWebhookUrl(e.target.value)} placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." style={{flex:1,fontSize:'var(--font-md)',padding:'10px 12px',borderRadius:'var(--radius-full)',border:'1px solid var(--border)',background:'var(--card)',outline:'none',minWidth:0}} />
            <button onClick={saveWebhook} disabled={webhookSaving} className="btn btn-primary" style={{flexShrink:0,minHeight:40,padding:'0 18px',fontSize:'var(--font-md)'}}>{webhookSaving?'保存中...':'保存'}</button>
          </div>
        </div>
      </Group>

      <div style={{textAlign:'center',marginTop:24,fontSize:'var(--font-sm)',color:'var(--muted2)'}}>
        SupplyKit · 供应链数据工作台
      </div>

      {/* 确认弹窗 */}
      {confirm === 'fill' && (
        <ConfirmDialog
          open
          title="生成种子数据？"
          desc="将生成 160 个商品、60 天订单、9 个仓库库存等模拟数据，覆盖现有数据。"
          confirmLabel="生成"
          onConfirm={doSeed}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'reset' && (
        <ConfirmDialog
          open
          title="重置所有数据？"
          desc="此操作不可恢复。将清空订单、库存、商品、规则等全部数据。"
          confirmLabel="重置"
          onConfirm={doReset}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
</>
}