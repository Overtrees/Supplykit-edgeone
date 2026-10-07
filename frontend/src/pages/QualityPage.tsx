import React from 'react'
import { t } from "../locale"
import LogFileList from '../components/LogFileList'

/** 质量日志页: 统一以"日志文件"形式展示(按天平铺), 点击日期 → 底部弹窗预览当日明细
 * (明细不再直接铺页面——想看明细点对应天数文件) */
export default function QualityPage() {
  return (
    <div className="card">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        {t("nav.quality")}
        <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>日志文件 · 点击日期查看当日明细</span>
      </div>
      <LogFileList scope="user" />
    </div>
  )
}
