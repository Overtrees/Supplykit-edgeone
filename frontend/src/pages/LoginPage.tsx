import React, { useState } from 'react'
import { t } from '../locale'

const API = import.meta.env.VITE_API_BASE_URL || ''

export default function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const doLogin = async () => {
    if (!username || !password) { setError('请输入用户名和密码'); return }
    setLoading(true); setError('')
    try {
      const r = await fetch(API + '/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const d = await r.json()
      if (d.ok && d.token) {
        try { localStorage.setItem('c_token', d.token) } catch {}
        try { localStorage.setItem('c_user', d.user || username) } catch {}
        onLogin()
      } else {
        setError(d.detail || '用户名或密码错误')
      }
    } catch { setError('无法连接到服务器') }
    setLoading(false)
  }

  return (
    <div style={{display:'flex',flexDirection:'column',minHeight:'100svh',padding:'40px 24px',overflowY:'auto',boxSizing:'border-box',background:'var(--bg)'}}>
      <div style={{flex:1,display:'flex',flexDirection:'column',justifyContent:'center',maxWidth:320,margin:'0 auto',width:'100%'}}>
        <div style={{textAlign:'center',marginBottom:32}}>
          <div style={{fontSize:28,fontWeight:800,color:'var(--text)',marginBottom:8}}>SupplyKit</div>
          <div style={{fontSize:14,color:'var(--muted2)'}}>供应链数据工作台</div>
        </div>
        <div style={{background:'var(--card)',borderRadius:32,padding:24,boxShadow:'var(--shadow-card)'}}>
          <div style={{fontSize:16,fontWeight:600,marginBottom:20}}>登录</div>
          <input
            value={username} onChange={e => setUsername(e.target.value)}
            placeholder="用户名" autoFocus
            onKeyDown={e => e.key === 'Enter' && password && doLogin()}
            style={{width:'100%',padding:'12px 16px',fontSize:16,border:'1px solid var(--border)',borderRadius:32,marginBottom:12,outline:'none',background:'var(--bg)',color:'var(--text)',boxSizing:'border-box'}}
          />
          <input
            type="password" value={password} onChange={e => setPassword(e.target.value)}
            placeholder="密码"
            onKeyDown={e => e.key === 'Enter' && doLogin()}
            style={{width:'100%',padding:'12px 16px',fontSize:16,border:'1px solid var(--border)',borderRadius:32,marginBottom:16,outline:'none',background:'var(--bg)',color:'var(--text)',boxSizing:'border-box'}}
          />
          {error && <div style={{color:'#ef4444',fontSize:13,marginBottom:12,textAlign:'center'}}>{error}</div>}
          <button onClick={doLogin} disabled={loading}
            style={{width:'100%',padding:'14px',fontSize:16,fontWeight:600,border:'none',borderRadius:32,cursor:'pointer',background:'var(--primary)',color:'#fff',opacity:loading?0.6:1}}>
            {loading ? '登录中...' : '登录'}
          </button>
          <div style={{marginTop:16,padding:'10px 14px',background:'var(--bg)',borderRadius:16,fontSize:12,color:'var(--muted2)',textAlign:'center'}}>
            访客模式：<b>demo</b> / <b>demo123</b>（仅可查看，无需注册）
          </div>
          <div style={{marginTop:10,padding:'8px 12px',fontSize:10,color:'var(--muted2)',textAlign:'center',lineHeight:1.5,borderTop:'1px solid var(--border)'}}>
            ⚖️ 演示系统：所有品牌、商品、供应商及数据均为虚构示例，与任何真实企业或个人无关
          </div>
        </div>
      </div>
    </div>
  )
}