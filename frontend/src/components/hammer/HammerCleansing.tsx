import React, { useState } from "react"
import { useAppStore } from "../../store/useAppStore"
import { IconTag } from "../../components/Icons"
import TemplateManageDialog from "../../components/TemplateManageDialog"
interface HammerCleansingProps { channel: string }

export default function HammerCleansing({ channel }: HammerCleansingProps) {
  const {hammerCleansingChannel, setHammerCleansingChannel, hammerCleansingTarget, hammerCleansingConflict, setHammerCleansingConflict, hammerCleansingStep} = useAppStore()
  const [tmplDlgOpen, setTmplDlgOpen] = useState(false)
  const inMapStep = hammerCleansingStep === 1  // 仅映射页(步骤2)显示 模版管理/状态映射
  const isOrder = hammerCleansingTarget === 'order'
  const target = hammerCleansingChannel === 'jd' ? '京东' : '其他渠道'
  const sameAsGlobal = hammerCleansingChannel === channel
  const isInOut = hammerCleansingTarget === 'inbound' || hammerCleansingTarget === 'outbound'
  return (
    <div>
      <div className="hammer-header">清洗导入 · 数据归入：<b>{target}</b></div>
      <div className="hammer-segmented">
        {[['jd','京东'],['other','其他渠道']].map(([id,label]) => (
          <span key={id} onClick={() => setHammerCleansingChannel(id)}
            className={'hammer-segment' + (hammerCleansingChannel === id ? ' active' : '')}>
            {label}
          </span>
        ))}
      </div>
      <div className="hammer-panel">
        {!sameAsGlobal && (
          <div style={{fontSize:10,color:'var(--warning)',textAlign:'center',background:'rgba(245,158,11,0.1)',borderRadius:32,padding:'4px 8px'}}>
            ⚠️ 当前全局主体是「{channel === 'jd' ? '京东' : '其他渠道'}」，导入后请切换主体查看该数据
          </div>
        )}
        {isInOut && (
          <div style={{marginTop:12}}>
            <div style={{fontSize:12,fontWeight:600,textAlign:'center',marginBottom:8}}>重复数据冲突处理</div>
            <div className="hammer-segmented">
              <span onClick={()=>setHammerCleansingConflict('sum')}
                className={'hammer-segment' + (hammerCleansingConflict==='sum' ? ' active' : '')}>
                累加求和
              </span>
              <span onClick={()=>setHammerCleansingConflict('overwrite')}
                className={'hammer-segment' + (hammerCleansingConflict==='overwrite' ? ' active' : '')}>
                覆盖
              </span>
            </div>
          </div>
        )}
        {inMapStep && (
          <div style={{marginTop:10,display:'flex',gap:8,flexWrap:'wrap'}}>
            <button onClick={() => setTmplDlgOpen(true)}
              className="hammer-btn btn-ghost"
              style={{flex:1,display:'flex',alignItems:'center',justifyContent:'center',gap:4,color:'var(--primary)',minHeight:36}}>
              模版管理
            </button>
            {isOrder && (
              <button onClick={() => window.dispatchEvent(new Event('cleansing-colmap-open'))}
                className="hammer-btn btn-ghost"
                style={{flex:1,display:'flex',alignItems:'center',justifyContent:'center',gap:4,color:'var(--primary)',minHeight:36}}>
                <IconTag size={13} /> 表格列状态映射
              </button>
            )}
          </div>
        )}
      </div>
      {tmplDlgOpen && <TemplateManageDialog tt={hammerCleansingTarget} onClose={() => setTmplDlgOpen(false)} />}
    </div>
  )
}