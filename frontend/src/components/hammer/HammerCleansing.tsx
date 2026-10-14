import { IconTag, IconWarning } from '../../components/Icons'
import React, { useState } from 'react'
import { useAppStore } from '../../store/useAppStore'
import TemplateManageDialog from '../../components/TemplateManageDialog'
interface HammerCleansingProps {
  channel: string
}

export default function HammerCleansing({ channel }: HammerCleansingProps) {
  const {
    hammerCleansingChannel,
    setHammerCleansingChannel,
    hammerCleansingTarget,
    hammerCleansingConflict,
    setHammerCleansingConflict,
    hammerCleansingStep,
  } = useAppStore()
  const [tmplDlgOpen, setTmplDlgOpen] = useState(false)
  const inMapStep = hammerCleansingStep === 1 // 仅映射页(步骤2)显示 模版管理/状态映射
  const isOrder = hammerCleansingTarget === 'order'
  const target = hammerCleansingChannel === 'jd' ? '京东' : '其他渠道'
  const sameAsGlobal = hammerCleansingChannel === channel
  const isInOut = hammerCleansingTarget === 'inbound' || hammerCleansingTarget === 'outbound'
  return (
    <div>
      <div className="hammer-header">
        清洗导入 · 数据归入：<b>{target}</b>
      </div>
      <div className="hammer-segmented">
        {[
          ['jd', '京东'],
          ['other', '其他渠道'],
        ].map(([id, label]) => (
          <span
            key={id}
            onClick={() => setHammerCleansingChannel(id)}
            className={'hammer-segment' + (hammerCleansingChannel === id ? ' active' : '')}
          >
            {label}
          </span>
        ))}
      </div>
      {(!sameAsGlobal || isInOut || inMapStep) && (
        <div className="hammer-panel">
          {!sameAsGlobal && (
            <div
              style={{
                fontSize: 'var(--font-10)',
                color: 'var(--warning)',
                textAlign: 'center',
                background: 'rgba(245,158,11,0.1)',
                borderRadius: 'var(--radius-lg)',
                padding: '9px 12px',
                minHeight: 32,
                width: '100%' /* 防 flex 压缩(黄条被限 190 宽→3 行→底部 4px 裁切) */,
                lineHeight: 1.3 /* 行高收紧——内容完整不裁 */,
              }}
            >
              <IconWarning size={13} style={{ verticalAlign: -2 }} /> 当前全局主体是「
              {channel === 'jd' ? '京东' : '其他渠道'}」，导入后请切换主体查看该数据
            </div>
          )}
          {isInOut && (
            <div style={{ marginTop: 12 }}>
              <div
                style={{
                  fontSize: 'var(--font-sm)',
                  fontWeight: 600,
                  textAlign: 'center',
                  marginBottom: 8,
                }}
              >
                重复数据冲突处理
              </div>
              <div className="hammer-segmented" style={{ flexWrap: 'wrap' }}>
                <span
                  onClick={() => setHammerCleansingConflict('sum')}
                  className={
                    'hammer-segment' + (hammerCleansingConflict === 'sum' ? ' active' : '')
                  }
                >
                  累加求和
                </span>
                <span
                  onClick={() => setHammerCleansingConflict('overwrite')}
                  className={
                    'hammer-segment' + (hammerCleansingConflict === 'overwrite' ? ' active' : '')
                  }
                >
                  覆盖
                </span>
              </div>
            </div>
          )}
          {inMapStep && (
            <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                onClick={() => setTmplDlgOpen(true)}
                className="hammer-btn btn-ghost"
                style={{
                  flex: 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 4,
                  color: 'var(--primary)',
                  minHeight: 36,
                }}
              >
                模版管理
              </button>
              {isOrder && (
                <button
                  onClick={() => window.dispatchEvent(new Event('cleansing-colmap-open'))}
                  className="hammer-btn btn-ghost"
                  style={{
                    flex: 1,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 4,
                    color: 'var(--primary)',
                    minHeight: 36,
                  }}
                >
                  <IconTag size={13} /> 表格列状态映射
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {tmplDlgOpen && (
        <TemplateManageDialog tt={hammerCleansingTarget} onClose={() => setTmplDlgOpen(false)} />
      )}
    </div>
  )
}
