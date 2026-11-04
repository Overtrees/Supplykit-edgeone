LOCALE = {
    # ── 看板通用 ──
    "dash.unit_yi": ("亿", "B"),
    "dash.unit_wan": ("万", "K"),
    "dash.nationwide": ("全国", "Nationwide"),
    "dash.wh_more": ("{a}等{n}仓", "{a} +{n}"),
    "dash.alert_age": (" · 持续{n}天", " · {n}d"),
    "dash.risk_red": ("紧急", "Critical"),
    "dash.risk_orange": ("预警", "Warning"),
    "dash.risk_yellow": ("关注", "Watch"),
    "dash.risk_title": ("濒临断货预警", "Stockout risk"),
    "dash.risk_title_bc": ("濒临断货预警（BC）", "Stockout risk (BC)"),
    "dash.risk_n": ("{n} 条", "{n} rows"),
    "dash.close": ("关闭", "Close"),
    "dash.daily_sales_avail": ("日销 {a} · 可用 {b}", "Daily {a} · available {b}"),
    "dash.days_n": ("{n} 天", "{n} d"),
    "dash.wh_c": ("C仓", "WH C"),
    "dash.wh_b": ("B仓", "WH B"),
    "dash.own": ("自有", "Own"),
    # ── 自有仓健康卡 ──
    "own.title": ("自有仓健康", "Own-warehouse health"),
    "own.all_ok": ("自有仓均正常", "Own warehouses are healthy"),
    "own.gap": ("调拨缺口", "Transfer gap"),
    "own.gap_desc": ("下游要货超出自有仓可发量", "Downstream demand exceeds shippable stock"),
    "own.expiry": ("效期", "Expiry"),
    "own.expiry_desc": ("批次将过期/已过期，未及调拨消化", "Batches expiring — not cleared by transfers"),
    "own.overstock": ("积压", "Overstock"),
    "own.overstock_desc": ("可撑天数超阈值，占用资金库位", "Days of cover above threshold — ties up cash"),
    "own.lack": ("缺 {n}", "Short {n}"),
    "own.expired_d": ("过期 {n}天", "Expired {n}d"),
    "own.left_d": ("剩 {n}天", "Left {n}d"),
    "own.cover_d": ("撑 {n}天", "{n}d cover"),
    "own.need_can": ("需 {a} / 可发 {b}", "Need {a} / can ship {b}"),
    "own.warn": ("预警", "Warning"),
    # ── 看板主页面 ──
    "dash.title": ("多维数据看板", "Dashboard"),
    "dash.all": ("全部", "All"),
    "dash.urgent": ("紧急", "Critical"),
    "dash.watch": ("观察", "Watch"),
    "dash.load_fail_processing": ("加载失败，数据可能正在处理中", "Load failed — data may still be processing"),
    "dash.net_gmv": ("净GMV", "Net GMV"),
    "dash.payout": ("回款", "Settlement"),
    "dash.date": ("日期", "Date"),
    "dash.order_count": ("订单数", "Orders"),
    "dash.funnel_tip": (
        "{name}<br/>数量: {v}单<br/>占比: {p}%<br/>转化率: {c}%",
        "{name}<br/>Orders: {v}<br/>Share: {p}%<br/>CVR: {c}%",
    ),
    "dash.unit_wan_orders": ("万单", "0k orders"),
    "dash.recalc_hint": ("规则重算中，告警数据稍后自动更新", "Recalculating rules — alerts refresh automatically"),
    "dash.purchase": ("采购", "Purchase"),
    "dash.replenish_short": ("补货", "Replenish"),
    # ── StatCards ──
    "dash.custom": ("自定义", "Custom"),
    "dash.gross": ("总", "Gross"),
    "dash.net": ("净", "Net"),
    "dash.payout_pending": ("回款待接入资金流水", "Settlement pending — no cash-flow data yet"),
    "dash.payout_tip": ("回款按到账时间归属——仅月度/完整月可用", "Settlement is dated by arrival — monthly / full months only"),
    "dash.orders_n": ("{n} 单", "{n} orders"),
    "dash.no_compare": ("无可比数据", "No comparable data"),
    "dash.vs_yesterday": ("较昨日", "vs yesterday"),
    "dash.vs_last2w": ("较上上周", "vs 2 weeks ago"),
    "dash.vs_last2m": ("较上上月", "vs 2 months ago"),
    "dash.daily_avg": (" · 日均 ¥", " · daily avg ¥"),
    "dash.proc_buy": ("● 采购 {n}", "● Purchase {n}"),
    "dash.proc_repl": ("● 补货 {n}", "● Replenish {n}"),
    "dash.low_stock_n": ("● 低库存 {n}", "● Low stock {n}"),
    "dash.other_n": ("● 其他 {n}", "● Other {n}"),
    "dash.other_zero": ("其他 0", "Other 0"),
    "dash.slow_n": ("● 滞销 {n}", "● Slow moving {n}"),
    "dash.health_of": ("库存{t('dash.healthy')}度", "Stock health"),
    "dash.health_title": ("库存健康度", "Stock health"),
    "dash.healthy_n": ("● {n}健康", "● {n} healthy"),
    "dash.score_tip": ("{d} {v}分", "{d} {v} pts"),
    "dash.pts": ("分", "pts"),
    # ── ChartRows ──
    "dash.brand_gmv": ("品牌GMV", "Brand GMV"),
    "dash.store": ("店铺", "Store"),
    "dash.brand": ("品牌", "Brand"),
    "dash.more_n": ("更多 {n}", "More {n}"),
    "dash.more_aria": ("还有 {n} 条", "{n} more"),
    "dash.proc_alert_title": ("采购&补货告警", "Purchase & replenishment alerts"),
    "dash.no_proc_need": ("暂无采购/补货需求", "No purchase or replenishment needed"),
    "dash.stock_enough": ("库存充足", "Stock sufficient"),
    "dash.can_cover": ("可撑", "Cover"),
    "dash.warning": ("警告", "Warning"),
    "dash.dup_detail": ("重复的明细", "Duplicate rows"),
}

REPL = [
    # 自有仓卡
    (
        "frontend/src/components/dash/OwnHealthCard.tsx",
        """  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + '万'""",
        """  if (Math.abs(v) >= (isNarrow() ? 1e4 : 1e5)) return Math.round(v / 1e4) + t('dash.unit_wan')""",
    ),
    (
        "frontend/src/components/dash/OwnHealthCard.tsx",
        """  own_transfer_gap: {
    tag: '调拨缺口',
    cls: 'danger',
    c: 'var(--danger)',
    order: 0,
    desc: '下游要货超出自有仓可发量',
  },
  own_expiry_risk: {
    tag: '效期',
    cls: 'danger',
    c: 'var(--danger)',
    order: 1,
    desc: '批次将过期/已过期，未及调拨消化',
  },
  own_overstock: {
    tag: '积压',
    cls: 'warning',
    c: 'var(--warning)',
    order: 2,
    desc: '可撑天数超阈值，占用资金库位',
  },""",
        """  own_transfer_gap: {
    tag: t('own.gap'),
    cls: 'danger',
    c: 'var(--danger)',
    order: 0,
    desc: t('own.gap_desc'),
  },
  own_expiry_risk: {
    tag: t('own.expiry'),
    cls: 'danger',
    c: 'var(--danger)',
    order: 1,
    desc: t('own.expiry_desc'),
  },
  own_overstock: {
    tag: t('own.overstock'),
    cls: 'warning',
    c: 'var(--warning)',
    order: 2,
    desc: t('own.overstock_desc'),
  },""",
    ),
    (
        "frontend/src/components/dash/OwnHealthCard.tsx",
        """  if (x.alert_type === 'own_transfer_gap') return { v: '缺 ' + fmtInt(x.gap_qty), hint: fn(x) }
  if (x.alert_type === 'own_expiry_risk') {
    const d = x.days_to_expiry
    if (d == null) return { v: '—', hint: '' }
    return { v: d < 0 ? '过期 ' + Math.abs(d) + '天' : '剩 ' + fmtInt(d) + '天', hint: '' }
  }
  return { v: '撑 ' + Math.round(Number(x.cover_days || 0)) + '天', hint: '' }
}
const fn = (x: OwnHealthItem) =>
  x.need_qty != null && x.available_qty != null
    ? '需 ' + fmtInt(x.need_qty) + ' / 可发 ' + fmtInt(x.available_qty)
    : ''""",
        """  if (x.alert_type === 'own_transfer_gap')
    return { v: t('own.lack', { n: fmtInt(x.gap_qty) }), hint: fn(x) }
  if (x.alert_type === 'own_expiry_risk') {
    const d = x.days_to_expiry
    if (d == null) return { v: '—', hint: '' }
    return {
      v: d < 0 ? t('own.expired_d', { n: Math.abs(d) }) : t('own.left_d', { n: fmtInt(d) }),
      hint: '',
    }
  }
  return { v: t('own.cover_d', { n: Math.round(Number(x.cover_days || 0)) }), hint: '' }
}
const fn = (x: OwnHealthItem) =>
  x.need_qty != null && x.available_qty != null
    ? t('own.need_can', { a: fmtInt(x.need_qty), b: fmtInt(x.available_qty) })
    : ''""",
    ),
    (
        "frontend/src/components/dash/OwnHealthCard.tsx",
        """        自有仓健康
        {d.total > 0 ? ` (${fmtInt(d.total)})` : ''}""",
        """        {t('own.title')}
        {d.total > 0 ? ` (${fmtInt(d.total)})` : ''}""",
    ),
    (
        "frontend/src/components/dash/OwnHealthCard.tsx",
        """          自有仓均正常""",
        """          {t('own.all_ok')}""",
    ),
    (
        "frontend/src/components/dash/OwnHealthCard.tsx",
        """            const m = TYPE_META[x.alert_type] || {
              tag: '预警',""",
        """            const m = TYPE_META[x.alert_type] || {
              tag: t('own.warn'),""",
    ),
    # 风险卡
    (
        "frontend/src/components/dash/RiskCard.tsx",
        """  return p.slice(0, 2).join(',') + '等' + p.length + '仓'""",
        """  return t('dash.wh_more', { a: p.slice(0, 2).join(','), n: p.length })""",
    ),
    (
        "frontend/src/components/dash/RiskCard.tsx",
        """          濒临断货预警{replMode === 'bbcc' ? '（BC）' : ''}""",
        """          {replMode === 'bbcc' ? t('dash.risk_title_bc') : t('dash.risk_title')}""",
    ),
    (
        "frontend/src/components/dash/RiskCard.tsx",
        """            ● {total - riskCritical - riskWarning} 观察""",
        """            ● {total - riskCritical - riskWarning} {t('dash.risk_yellow')}""",
    ),
    (
        "frontend/src/components/dash/RiskCard.tsx",
        """                    ? '自有'
                    : x.type === 'B'
                      ? 'B仓'
                      : replMode === 'bbcc'
                        ? 'BC'
                        : 'C仓')""",
        """                    ? t('inv.own')
                    : x.type === 'B'
                      ? t('dash.wh_b')
                      : replMode === 'bbcc'
                        ? 'BC'
                        : t('dash.wh_c'))""",
    ),
    # 风险弹窗
    (
        "frontend/src/pages/dashboard/sheets/RiskSheet.tsx",
        """            濒临断货预警{_replMode === 'bbcc' ? '（BC）' : ''} ·{' '}
            {riskFilter === 'red'
              ? '紧急'
              : riskFilter === 'orange'
                ? '预警'
                : riskFilter === 'yellow'
                  ? '观察'
                  : '全部'}{' '}
            {fmtInt(
              (riskFilter === 'red'
                ? riskCritical
                : riskFilter === 'orange'
                  ? riskWarning
                  : riskFilter === 'yellow'
                    ? _r.total - riskCritical - riskWarning
                    : _r.total) || 0,
            )}{' '}
            条""",
        """            {_replMode === 'bbcc' ? t('dash.risk_title_bc') : t('dash.risk_title')} ·{' '}
            {riskFilter === 'red'
              ? t('dash.risk_red')
              : riskFilter === 'orange'
                ? t('dash.risk_orange')
                : riskFilter === 'yellow'
                  ? t('dash.risk_yellow')
                  : t('dash.all')}{' '}
            {t('dash.risk_n', {
              n: fmtInt(
                (riskFilter === 'red'
                  ? riskCritical
                  : riskFilter === 'orange'
                    ? riskWarning
                    : riskFilter === 'yellow'
                      ? _r.total - riskCritical - riskWarning
                      : _r.total) || 0,
              ),
            })}""",
    ),
    (
        "frontend/src/pages/dashboard/sheets/RiskSheet.tsx",
        """                  ? 'C仓'
                  : x.type === 'OWN'
                    ? '自有'
                    : x.type === 'B'
                      ? 'B仓'""",
        """                  ? t('dash.wh_c')
                  : x.type === 'OWN'
                    ? t('inv.own')
                    : x.type === 'B'
                      ? t('dash.wh_b')""",
    ),
    (
        "frontend/src/pages/dashboard/sheets/RiskSheet.tsx",
        """                      日销 {x.daily_sales} · 可用 {x.available_qty}""",
        """                      {t('dash.daily_sales_avail', { a: x.daily_sales, b: x.available_qty })}""",
    ),
    (
        "frontend/src/pages/dashboard/sheets/RiskSheet.tsx",
        """                    {x.days_to_empty} 天""",
        """                    {t('dash.days_n', { n: x.days_to_empty })}""",
    ),
    (
        "frontend/src/pages/dashboard/sheets/RiskSheet.tsx",
        """<span style={{ fontSize: 'var(--font-15)', fontWeight: 600, color: '#fff' }}>关闭</span>""",
        """<span style={{ fontSize: 'var(--font-15)', fontWeight: 600, color: '#fff' }}>
                  {t('dash.close')}
                </span>""",
    ),
]
