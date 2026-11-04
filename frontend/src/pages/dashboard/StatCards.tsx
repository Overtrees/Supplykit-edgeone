import { t } from '../../locale'
import { periodLabel, fmtBig, fmtInt } from './format'

/**
 * 看板三张 stat 卡(全受控) —— 自 DashboardPage.tsx 外提(S3-1 续3)。
 * 状态一行不搬: gmvView/healthTab/bcMenuOpen 等 set* 与派生值全部同名 props 注入。
 */

/** 1. GMV 卡 — 加环比微趋势线 + 日均 */
export function GmvCard({
  dashboard,
  gmvView,
  setGmvView,
  gmvColor,
  gmvKey,
  gmvPayoutOk,
  periodMeta,
  periodTrend,
  periodDays,
  periodTab,
}: any) {
  return (
    <div className="card stat-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="small muted" style={{ fontSize: 'var(--font-sm)', lineHeight: 1.2 }}>
          {t('dash.gmv_card_title', {
            p: periodTab === 'custom' ? t('dash.custom') : periodLabel[periodTab],
          })}
        </div>
        {/* GMV 视角切换(总/净/回款), 样式对齐健康小卡 tab: 紧凑segmented pill + 短标签 */}
        <div
          style={{
            display: 'flex',
            gap: 2,
            background: 'var(--bg)',
            borderRadius: 'var(--radius-full)',
            padding: 2,
          }}
        >
          {[
            ['total', t('dash.view_total')],
            ['net', t('dash.view_net')],
            ['payout', t('dash.view_payout')],
          ].map(([v, l]) => (
            <span
              key={v}
              onClick={function () {
                if (v === 'payout' && !gmvPayoutOk) return // 回款仅月度可用——弱化禁点
                setGmvView(v)
              }}
              onMouseEnter={function () {}}
              className="clickable"
              title={v === 'payout' && !gmvPayoutOk ? t('dash.view_payout_tip') : undefined}
              style={{
                fontSize: 'var(--font-9)',
                padding: '2px 6px',
                borderRadius: 'var(--radius-full)',
                cursor: v === 'payout' && !gmvPayoutOk ? 'not-allowed' : 'pointer',
                fontWeight: gmvView === v ? 600 : 400,
                background: gmvView === v ? 'var(--card)' : 'transparent',
                color:
                  v === 'payout' && !gmvPayoutOk
                    ? 'var(--muted2)'
                    : gmvView === v
                      ? 'var(--text)'
                      : 'var(--muted2)',
                opacity: v === 'payout' && !gmvPayoutOk ? 0.4 : 1,
                whiteSpace: 'nowrap',
              }}
            >
              {l}
            </span>
          ))}
        </div>
      </div>
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'flex-end',
          // 动态兜底: 微趋势条(底部)缺失时内容区是最后——下沉对齐图柱视觉
          marginBottom: 4,
        }}
      >
        <div
          className="card-value"
          style={{
            fontSize: 'clamp(17px,8cqi,28px)',
            fontWeight: "var(--fw-bold)",
            lineHeight: 1.15,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {(() => {
            const _g =
              gmvView === 'net'
                ? periodMeta.net_gmv != null
                  ? periodMeta.net_gmv
                  : (periodMeta.gmv || 0) - (dashboard?.summary?.refund_amount || 0)
                : gmvView === 'payout'
                  ? periodMeta.payout != null
                    ? periodMeta.payout
                    : null // 2026-10-16 pending: 无真实资金流水——不显示推算——下方提示待接入
                  : periodMeta.gmv
            if (gmvView === 'payout' && _g == null)
              return (
                <span style={{ fontSize: 13, color: 'var(--muted2)', fontWeight: "var(--fw-medium)" }}>
                    {t('dash.payout_pending_short')}
                  </span>
              )
            return '¥' + fmtBig(_g || 0)
          })()}
        </div>
        <div
          className="card-sub"
          style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 8 }}
        >
          <span>{t('dash.orders_n_plain', { n: periodMeta.orders })}</span>
          {(() => {
            // 2026-10-16 环比跟随维度(总/净/回款分别取各自 last/prev);
            // 后端 prev_net_gmv/prev_payout 缺失时(净/回款无环比数据源)不显示——有就有没有就没有
            const _last =
              gmvView === 'net'
                ? periodMeta.net_gmv != null
                  ? periodMeta.net_gmv
                  : periodMeta.gmv || 0
                : gmvView === 'payout'
                  ? periodMeta.payout != null
                    ? periodMeta.payout
                    : null
                  : periodMeta.gmv || 0
            const _prev =
              gmvView === 'net'
                ? periodMeta.prev_net_gmv
                : gmvView === 'payout'
                  ? periodMeta.prev_payout
                  : periodMeta.prev_gmv
            if (_last == null || _prev == null) return null
            // 2026-10-30: 无基准(prev=0/缺失)时明确提示, 不再静默不渲染(曾被误判"环比功能坏了")
            if (!_prev)
              return (
                <span style={{ fontSize: 'var(--font-9)', color: 'var(--muted2)' }}>
                  {t('dash.no_basis')}
                </span>
              )
            const pct = ((Number(_last) - Number(_prev)) / Number(_prev)) * 100
            // 2026-10-16 无歧义: 窗口=上周/上月(完整自然周/月), 环比基准=上上周/上上月
            const _cmpLabel =
              periodTab === 'today'
                ? t('dash.vs_prev_day')
                : periodTab === 'week'
                  ? t('dash.vs_prev_week')
                  : t('dash.vs_prev_month')
            return (
              <span
                style={{
                  fontSize: 'var(--font-xs)',
                  fontWeight: "var(--fw-semibold)",
                  color: pct >= 0 ? 'var(--success)' : 'var(--danger)',
                }}
              >
                {pct >= 0 ? '↑' : '↓'} {Math.abs(pct).toFixed(1)}%{' '}
                <span
                  style={{ fontSize: 'var(--font-9)', fontWeight: "var(--fw-regular)", color: 'var(--muted2)' }}
                >
                  {_cmpLabel}
                </span>
              </span>
            )
          })()}
          <span style={{ color: 'var(--muted2)', fontSize: 'var(--font-10)' }}>
            {t('dash.daily_avg_prefix')}
            {(() => {
              // 2026-10-16 去推算兜底: 回款维度无真实流水时显示待接入(不拿 gmv-refund-subsidy 凑)
              const _g =
                gmvView === 'net'
                  ? periodMeta.net_gmv != null
                    ? periodMeta.net_gmv
                    : (periodMeta.gmv || 0) - (dashboard?.summary?.refund_amount || 0)
                  : gmvView === 'payout'
                    ? periodMeta.payout != null
                      ? periodMeta.payout
                      : null
                    : periodMeta.gmv
              if (_g == null)
                return (
                  <span style={{ color: 'var(--muted2)', fontWeight: "var(--fw-medium)" }}>
                    {t('dash.payout_pending_short')}
                  </span>
                )
              return fmtBig((_g || 0) / periodDays)
            })()}
          </span>
        </div>
      </div>
      {/* 微趋势线(维度切换淡入) */}
      {periodTrend.length >= 3 && (
        <div
          key={gmvView}
          className="fade-in"
          style={{
            height: 22,
            marginTop: 8,
            display: 'flex',
            alignItems: 'flex-end',
            gap: 1.5,
          }}
        >
          {periodTrend.map((i, idx) => {
            const v = Number(i[gmvKey]) || 0
            const max = Math.max(...periodTrend.map(x => Number(x[gmvKey]) || 0), 1)
            const h = Math.max((v / max) * 18, 2)
            return (
              <div
                key={idx}
                style={{
                  flex: 1,
                  height: h,
                  borderRadius: '2px 2px 0 0',
                  background: gmvColor,
                  opacity: 0.3 + 0.7 * (v / max),
                }}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 2. 待处理卡 — iOS 18 天气风: 数字主角/语义状态行/分级明细 */
export function AlertSummaryCard({
  dashboard,
  detailFont,
  loadFullAlerts,
  lowStockTotal,
  otherTotal,
  pendingTotal,
  procBuyN,
  procRepN,
  setShowAllOther,
  slowMovingTotal,
}: any) {
  return (
    <div className="card stat-card">
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          minHeight: 14,
        }}
      >
        <div
          style={{
            fontSize: 'var(--font-sm)',
            fontWeight: "var(--fw-semibold)",
            color: 'var(--muted)',
            letterSpacing: 0.2,
            lineHeight: 1.2,
          }}
        >
          {t('dash.pending')}
        </div>
        {/* 右上角"紧急 N"移除(2026-10-09): severity=error 是"● 告警 N"子集——包含冗余 */}
      </div>
      {pendingTotal === 0 ? (
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
          }}
        >
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: '50%',
              background: 'var(--bg)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--success)',
            }}
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
              <polyline points="22 4 12 14.01 9 11.01" />
            </svg>
          </div>
          <div style={{ fontSize: 'var(--font-sm)', fontWeight: "var(--fw-medium)", color: 'var(--muted2)' }}>
            {t('dash.no_alerts')}
          </div>
        </div>
      ) : (
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'flex-end',
          }}
        >
          <div style={{ marginTop: 4 }}>
            <div
              className="card-value"
              style={{
                fontSize: 'clamp(17px,8cqi,28px)',
                fontWeight: "var(--fw-bold)",
                lineHeight: 1.15,
                color:
                  pendingTotal > 10
                    ? 'var(--danger)'
                    : pendingTotal > 5
                      ? 'var(--warning)'
                      : 'var(--text)',
                fontVariantNumeric: 'tabular-nums',
                marginBottom: 1,
              }}
            >
              {fmtInt(pendingTotal)}
            </div>
            <div
              className="card-sub"
              style={{
                // 总览第二行(采购/补货): 数字下间距 6
                marginTop: 6,
                display: 'flex',
                gap: 6,
                // 卡片是固定正方形(aspect-ratio:1 + overflow:hidden), 换行会被裁 ——
                // 所以不换行, 改由英文词条取短写(Buy/Replen/Low)来适配宽度
                flexWrap: 'nowrap',
                whiteSpace: 'nowrap',
                fontSize: detailFont,
                lineHeight: 1.0,
                // glyph 度量补偿: 字身 gap 15.6 vs 图柱 16——transform 上移 0.4(静态偏移, flex margin 不可靠)
                transform: 'translateY(-0.4px)',
                // 动态最后行下沉(定版铁律, 一比一保留): 当第二行(采购/补货)是最后一行(无第三行细分时),
                // 字身底须与 GMV 图柱底对齐——transform 上移 0.4(glyph 度量补偿: 字身 gap 15.6 vs 图柱 16,
                // flex margin 不可靠, 静态偏移为浏览器亚像素极限)
              }}
            >
              {/* 2026-10-27 总览第二行: 采购/补货条数(实时接口同源)
                  2026-11-04 允许换行: 中文「● 采购 7 ● 补货 137」一行放得下, 英文
                  「● Purchase 7 ● Replenish 137」比中文长约 60%, 强制 nowrap 会被卡片裁掉
                  (4 视口验证发现 "Replenish 1…"), 故这里 wrap、收紧 gap。 */}
              <span style={{ fontWeight: "var(--fw-semibold)", color: 'var(--warning)' }}>
                {t('dash.line_purchase', { n: fmtInt(procBuyN) })}
              </span>
              <span style={{ fontWeight: "var(--fw-semibold)", color: 'var(--warning)' }}>
                {t('dash.line_replenish', { n: fmtInt(procRepN) })}
              </span>
            </div>
          </div>
          <div
            style={{
              fontSize: detailFont,
              display: 'flex',
              gap: 10,
              marginTop: 4,
              flexWrap: 'wrap',
              lineHeight: 1.0,
              alignItems: 'center',
              // glyph 度量补偿: transform 上移 0.4
              transform: 'translateY(-0.4px)',
            }}
          >
            {/* 总览第三行: 低库存 + 其他(聚合超卖等规则评估, 点击弹窗明细) */}
            <span style={{ fontWeight: "var(--fw-semibold)", color: 'var(--warning)' }}>
              {t('dash.line_low_stock', { n: fmtInt(lowStockTotal) })}
            </span>
            {otherTotal > 0 ? (
              <span
                onClick={function (e) {
                  e.stopPropagation()
                  loadFullAlerts()
                  setShowAllOther(true)
                }}
                className="clickable"
                style={{ color: '#007AFF', fontWeight: "var(--fw-semibold)", cursor: 'pointer' }}
              >
                {t('dash.line_other', { n: fmtInt(otherTotal) })}
              </span>
            ) : (
              <span style={{ color: 'var(--muted)' }}>{t('dash.other_zero_plain')}</span>
            )}
            {slowMovingTotal > 0 && (
              <span style={{ color: 'var(--muted2)' }}>
                {t('dash.line_slow', { n: fmtInt(slowMovingTotal) })}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** 3. 健康卡 — 加总 SKU 数(随 healthTab 切换, key 保持重挂动画) */
export function HealthCard({
  dashboard,
  healthTab,
  healthTrend,
  channel,
  bcMenuOpen,
  setBcMenuOpen,
  setHealthWithSave,
  setShowAllOut,
  loadFullOut,
  _oosSrc,
  _replMode,
}: any) {
  return (
    <div key={'hlth' + healthTab} className="card stat-card fade-in">
      {(() => {
        const healthData = dashboard?.health_index?.[healthTab] || {}
        const isJd = channel === 'jd'
        // 健康图柱是否渲染(近14天当前维度 ≥2 点才显示)——决定明细行是否兜底下沉
        const _hk = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : 'platform'
        const healthTrendBar =
          (Array.isArray(healthTrend) ? healthTrend : []).filter(x => x[_hk] != null && x[_hk] > 0)
            .length >= 2
        const bcActive = healthTab === 'bc' || healthTab === 'platform'
        const bcLabel = healthTab === 'platform' ? t('dash.tab_c') : 'BC'
        return (
          <>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 4,
              }}
            >
              <div className="small muted" style={{ fontSize: 'var(--font-sm)', lineHeight: 1.2 }}>
                {t('dash.health_card_title')}
              </div>
              <div
                style={{
                  display: 'flex',
                  gap: 2,
                  background: 'var(--bg)',
                  borderRadius: 'var(--radius-full)',
                  padding: 2,
                  position: 'relative',
                }}
              >
                <span
                  onClick={function () {
                    setHealthWithSave('own')
                  }}
                  className="clickable"
                  style={{
                    fontSize: 'var(--font-9)',
                    padding: '2px 6px',
                    borderRadius: 'var(--radius-full)',
                    cursor: 'pointer',
                    fontWeight: healthTab === 'own' ? 600 : 400,
                    background: healthTab === 'own' ? 'var(--card)' : 'transparent',
                    color: healthTab === 'own' ? 'var(--text)' : 'var(--muted2)',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {t('dash.tab_own')}
                </span>
                {isJd && _replMode === 'bbcc' && (
                  <span
                    onClick={function () {
                      if (healthTab === 'bc' || healthTab === 'platform') {
                        setBcMenuOpen(!bcMenuOpen)
                      } else {
                        setHealthWithSave('bc')
                      }
                    }}
                    className="clickable"
                    style={{
                      fontSize: 'var(--font-9)',
                      padding: '2px 6px',
                      borderRadius: 'var(--radius-full)',
                      cursor: 'pointer',
                      fontWeight: bcActive ? 600 : 400,
                      background: bcActive ? 'var(--card)' : 'transparent',
                      color: bcActive ? 'var(--text)' : 'var(--muted2)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {bcLabel}
                    {bcActive && (
                      <svg
                        width="6"
                        height="6"
                        viewBox="0 0 8 8"
                        fill="none"
                        style={{
                          transform: 'rotate(' + (bcMenuOpen ? '180' : '0') + 'deg)',
                          transition: 'transform 0.15s',
                        }}
                      >
                        <path
                          d="M2 3l2 2 2-2"
                          stroke="currentColor"
                          strokeWidth="1.2"
                          strokeLinecap="round"
                        />
                      </svg>
                    )}
                  </span>
                )}
                {(!isJd || _replMode !== 'bbcc') && (
                  <span
                    onClick={function () {
                      setHealthWithSave('platform')
                    }}
                    className="clickable"
                    style={{
                      fontSize: 'var(--font-9)',
                      padding: '2px 6px',
                      borderRadius: 'var(--radius-full)',
                      cursor: 'pointer',
                      fontWeight: healthTab === 'platform' ? 600 : 400,
                      background: healthTab === 'platform' ? 'var(--card)' : 'transparent',
                      color: healthTab === 'platform' ? 'var(--text)' : 'var(--muted2)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t('dash.tab_platform')}
                  </span>
                )}
                {bcMenuOpen && (
                  <div
                    onClick={function () {
                      setBcMenuOpen(false)
                    }}
                    style={{ position: 'fixed', inset: 0, zIndex: 9 }}
                  />
                )}
                {bcMenuOpen && (
                  <div
                    style={{
                      position: 'absolute',
                      top: 'calc(100% + 4px)',
                      right: 0,
                      background: 'var(--card)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border)',
                      overflow: 'hidden',
                      minWidth: 64,
                      zIndex: 10,
                    }}
                  >
                    {healthTab === 'bc' ? (
                      <div
                        onClick={function () {
                          setHealthWithSave('platform')
                          setBcMenuOpen(false)
                        }}
                        className="clickable"
                        style={{
                          padding: '6px 12px',
                          fontSize: 'var(--font-xs)',
                          color: 'var(--text)',
                          cursor: 'pointer',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {t('dash.tab_c')}
                      </div>
                    ) : (
                      <div
                        onClick={function () {
                          setHealthWithSave('bc')
                          setBcMenuOpen(false)
                        }}
                        className="clickable"
                        style={{
                          padding: '6px 12px',
                          fontSize: 'var(--font-xs)',
                          color: 'var(--text)',
                          cursor: 'pointer',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        BC
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
            <div
              key={'h' + healthTab}
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'flex-end',
                animation: 'fadeIn 0.18s ease',
              }}
            >
              <div
                className="card-value"
                style={{
                  fontSize: 'clamp(17px,8cqi,28px)',
                  fontWeight: "var(--fw-bold)",
                  lineHeight: 1.15,
                  fontVariantNumeric: 'tabular-nums',
                  color:
                    healthData.level === 'danger'
                      ? 'var(--danger)'
                      : healthData.level === 'warning'
                        ? 'var(--warning)'
                        : 'var(--success)',
                }}
              >
                {healthData.score != null ? t('dash.score_value', { n: healthData.score }) : '—'}
              </div>
              <div
                className="card-sub"
                style={{
                  marginTop: 4,
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    lineHeight: 1.0,
                    alignItems: 'center',
                    gap: 8,
                    marginTop: 2,
                    flexWrap: 'wrap',
                  }}
                >
                  <span
                    style={{
                      fontSize: 'var(--font-xs)',
                      fontWeight: "var(--fw-semibold)",
                      color: 'var(--success)',
                    }}
                  >
                    {t('dash.healthy_row', { n: healthData.healthy || 0 })}
                  </span>
                  <span
                    style={{
                      fontSize: 'var(--font-xs)',
                      fontWeight: "var(--fw-semibold)",
                      color: 'var(--warning)',
                    }}
                  >
                    {t('dash.warning_row', { n: healthData.warning || 0 })}
                  </span>
                  <span
                    onClick={function () {
                      // 入口点击时按当前维度拉全量(防 healthTab 异步竞态互串)
                      loadFullOut()
                      setShowAllOut(true)
                    }}
                    className="clickable"
                    style={{
                      fontSize: 'var(--font-xs)',
                      fontWeight: "var(--fw-semibold)",
                      color: 'var(--danger)',
                      cursor: _oosSrc.length > 0 ? 'pointer' : 'default',
                    }}
                  >
                    ● {healthData.out_of_stock || 0}
                    {t('dash.out_of_stock')}
                  </span>
                </div>
                <div style={{ fontSize: 'var(--font-10)', marginTop: 2, color: 'var(--muted2)' }}>
                  {healthData.total || 0} SKU
                </div>
                {/* 健康分数趋势(近14天, 按当前维度) —— CSS 柱状条: 绝对基础高度(分数语义) + 超5分波动叠加相对补偿(分段敏感度), 柱色=绝对档位 */}
                {(() => {
                  const _key = healthTab === 'own' ? 'own' : healthTab === 'bc' ? 'bc' : 'platform'
                  const _trend = (Array.isArray(healthTrend) ? healthTrend : []).filter(
                    x => x[_key] != null && x[_key] > 0,
                  )
                  if (_trend.length < 2) return null
                  const _hColor = s =>
                    s >= 85 ? 'var(--success)' : s >= 60 ? 'var(--warning)' : 'var(--danger)'
                  const _vals = _trend.map(x => Number(x[_key]) || 0)
                  const _min = Math.min(..._vals),
                    _span = Math.max(..._vals) - _min || 1
                  return (
                    <div
                      style={{
                        height: 22,
                        marginTop: 8,
                        display: 'flex',
                        alignItems: 'flex-end',
                        gap: 1.5,
                      }}
                    >
                      {_trend.map((x, idx) => {
                        const v = Number(x[_key]) || 0
                        const base = 5 + (v / 100) * 13 // 绝对基础高度(0分5px / 100分18px)
                        const rel = ((v - _min) / _span) * 7 // 相对波动补偿(≤7px)
                        const h = _span > 5 ? Math.min(base + rel, 20) : base // 跨度>5分叠加补偿(大波动可见), ≤5分如实
                        const isLast = idx === _trend.length - 1
                        return (
                          <div
                            key={idx}
                            title={t('dash.score_tip', { d: String(x.date).slice(5), v })}
                            style={{
                              flex: 1,
                              height: h,
                              borderRadius: '2px 2px 0 0',
                              background: _hColor(v),
                              opacity: isLast ? 1 : 0.6,
                            }}
                          />
                        )
                      })}
                    </div>
                  )
                })()}
              </div>
            </div>
          </>
        )
      })()}
    </div>
  )
}
