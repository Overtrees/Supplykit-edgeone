import React from "react"
import { useAppStore } from "../../store/useAppStore"
import { IconTag } from "../../components/Icons"
interface HammerCleansingProps { channel: string }

export default function HammerCleansing({ channel }: HammerCleansingProps) {
  const {hammerCleansingChannel, setHammerCleansingChannel, hammerCleansingTarget, hammerCleansingConflict, setHammerCleansingConflict} = useAppStore()
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
        <button onClick={() => window.dispatchEvent(new Event('cleansing-colmap-open'))}
          className="hammer-btn btn-ghost"
          style={{width:'100%',display:'flex',alignItems:'center',justifyContent:'center',gap:4,color:'var(--primary)',marginTop:10,minHeight:36}}>
          <IconTag size={13} /> 表格列状态映射
        </button>
      </div>
    </div>
  )
}