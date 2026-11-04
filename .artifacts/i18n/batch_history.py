LOCALE = {
    "hist.title": ("历史归档", "History"),
    "hist.summary": ("区间汇总", "Period summary"),
    "hist.kpi_amount": ("金额", "Amount"),
    "hist.kpi_orders": ("单数", "Orders"),
    "hist.kpi_qty": ("件数", "Units"),
    "hist.kpi_avg": ("日均", "Daily avg"),
    "hist.unit_yuan": ("元", "¥"),
    "hist.unit_order": ("单", ""),
    "hist.unit_piece": ("件", ""),
    "hist.unit_yuan_day": ("元/天", "¥/day"),
    "hist.empty_range": ("该区间没有数据", "No data in this period"),
    "hist.empty_short": ("无数据", "No data"),
    "hist.loading": ("加载中…", "Loading…"),
    "hist.top20": ("Top 20", "Top 20"),
    "hist.dim_sku": ("SKU", "SKU"),
    "hist.dim_store": ("店铺", "Store"),
    "hist.blank_key": ("(空)", "(empty)"),
    "hist.yoy": ("同比", "Year over year"),
    "hist.yoy_meta": ("去年同期 {a} ~ {b}", "Same period last year {a} ~ {b}"),
    "hist.yoy_none_dated": ("去年同期 {a} ~ {b} 无数据，暂不做同比", "No data for {a} ~ {b} last year — YoY unavailable"),
    "hist.yoy_none": ("去年同期无数据，暂不做同比", "No data for the same period last year — YoY unavailable"),
    "hist.yoy_last": ("去年 {v}", "Last year {v}"),
    "hist.tip_axis": ("{d}<br/>金额: {g}<br/>单数: {o}<br/>件数: {q}", "{d}<br/>Amount: {g}<br/>Orders: {o}<br/>Units: {q}"),
}


def _fmt_uses_units():
    """金额/数量格式化的 万/亿 与千分位也随语言: 中文看万/亿, 英文看 K/M。"""
    return None


REPL = [
    (
        "frontend/src/pages/HistoryPage.tsx",
        """  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })""",
        """  if (abs >= 1e8) return (v / 1e8).toFixed(2) + t('dash.unit_yi')
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + t('dash.unit_wan')
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """  if (abs >= 1e8) return (v / 1e8).toFixed(1) + '亿'
  if (abs >= 1e4) return Math.round(v / 1e4) + '万'""",
        """  if (abs >= 1e8) return (v / 1e8).toFixed(1) + t('dash.unit_yi')
  if (abs >= 1e4) return Math.round(v / 1e4) + t('dash.unit_wan')""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + '万'""",
        """  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + t('dash.unit_wan')""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          return `${r.date}<br/>金额: ${fmtMoney(r.gmv)}<br/>单数: ${fmtInt(
            r.orders,
          )}<br/>件数: ${fmtInt(r.qty)}`""",
        """          return t('hist.tip_axis', {
            d: r.date,
            g: fmtMoney(r.gmv),
            o: fmtInt(r.orders),
            q: fmtInt(r.qty),
          })""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <span className="hist-title">历史归档</span>""",
        """          <span className="hist-title">{t('hist.title')}</span>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <span className="section-title hist-title-flat">区间汇总</span>""",
        """          <span className="section-title hist-title-flat">{t('hist.summary')}</span>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """            { k: '金额', v: fmtMoney(totals.gmv), s: '元' },
            { k: '单数', v: fmtInt(totals.orders), s: '单' },
            { k: '件数', v: fmtInt(totals.qty), s: '件' },
            { k: '日均', v: fmtMoney(totals.avg_gmv_per_day), s: '元/天' },""",
        """            { k: t('hist.kpi_amount'), v: fmtMoney(totals.gmv), s: t('hist.unit_yuan') },
            { k: t('hist.kpi_orders'), v: fmtInt(totals.orders), s: t('hist.unit_order') },
            { k: t('hist.kpi_qty'), v: fmtInt(totals.qty), s: t('hist.unit_piece') },
            {
              k: t('hist.kpi_avg'),
              v: fmtMoney(totals.avg_gmv_per_day),
              s: t('hist.unit_yuan_day'),
            },""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <div className="hist-empty">该区间没有数据</div>""",
        """          <div className="hist-empty">{t('hist.empty_range')}</div>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <span className="section-title hist-title-flat">Top 20</span>""",
        """          <span className="section-title hist-title-flat">{t('hist.top20')}</span>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """                {k === 'sku' ? 'SKU' : '店铺'}""",
        """                {k === 'sku' ? t('hist.dim_sku') : t('hist.dim_store')}""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <div className="hist-empty">无数据</div>""",
        """          <div className="hist-empty">{t('hist.empty_short')}</div>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """                {it.key || '(空)'}""",
        """                {it.key || t('hist.blank_key')}""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <span className="section-title hist-title-flat">同比</span>""",
        """          <span className="section-title hist-title-flat">{t('hist.yoy')}</span>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <div className="hist-empty">加载中…</div>""",
        """          <div className="hist-empty">{t('hist.loading')}</div>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """          <div className="hist-note">
            {yoy.prev?.start
              ? `去年同期 ${yoy.prev.start} ~ ${yoy.prev.end} 无数据`
              : '去年同期无数据'}
            ，暂不做同比
          </div>""",
        """          <div className="hist-note">
            {yoy.prev?.start
              ? t('hist.yoy_none_dated', { a: yoy.prev.start, b: yoy.prev.end })
              : t('hist.yoy_none')}
          </div>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """            <div className="hist-yoy-meta">
              去年同期 {yoy.prev.start} ~ {yoy.prev.end}
            </div>""",
        """            <div className="hist-yoy-meta">
              {t('hist.yoy_meta', { a: yoy.prev.start, b: yoy.prev.end })}
            </div>""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """                    {k === 'gmv' ? '金额' : k === 'orders' ? '单数' : '件数'}""",
        """                    {k === 'gmv'
                      ? t('hist.kpi_amount')
                      : k === 'orders'
                        ? t('hist.kpi_orders')
                        : t('hist.kpi_qty')}""",
    ),
    (
        "frontend/src/pages/HistoryPage.tsx",
        """                    去年 {k === 'gmv' ? fmtMoney(yoy.prev[k]) : fmtInt(yoy.prev[k])}""",
        """                    {t('hist.yoy_last', {
                      v: k === 'gmv' ? fmtMoney(yoy.prev[k]) : fmtInt(yoy.prev[k]),
                    })}""",
    ),
]
