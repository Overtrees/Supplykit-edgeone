import React from 'react'
import { IconEmpty, IconPackage, IconClipboard, IconTag, IconFactory, IconAlert } from './Icons'
import { t } from "../locale"

const ICON_MAP = {
  'package': IconPackage,
  'clipboard': IconClipboard,
  'tag': IconTag,
  'factory': IconFactory,
  'alert': IconAlert,
}

export default function EmptyState({icon,title=t("common.empty"),desc='',action}) {
  const IconComp = icon ? (ICON_MAP[icon] || IconEmpty) : IconEmpty
  return <div style={{textAlign:'center',padding:'60px 20px',color:'var(--muted2)'}}>
    <div style={{fontSize:48,marginBottom:12,display:'flex',justifyContent:'center',color:'var(--muted2)'}}><IconComp size={48} /></div>
    <div style={{fontWeight:600,fontSize:'var(--font-lg)',marginBottom:6,color:'var(--muted)'}}>{title}</div>
    {desc && <div style={{fontSize:'var(--font-13)',marginBottom:16}}>{desc}</div>}
    {action}
  </div>
}
