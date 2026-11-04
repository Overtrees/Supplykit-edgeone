LOCALE = {
    "dash.brand_gmv_title": ("品牌GMV", "GMV by brand"),
    "dash.dim_store": ("店铺", "Store"),
    "dash.dim_brand": ("品牌", "Brand"),
    "dash.sev_warning": ("警告", "Warning"),
    "dash.dup_rows_hint": ("重复的明细", "Duplicate rows"),
    "dash.more_btn": ("更多 {n}", "More {n}"),
    "dash.more_aria_n": ("还有 {n} 条", "{n} more"),
    "dash.proc_card_title": ("采购&补货告警", "Purchase & replenishment"),
    "dash.loading_ellipsis": ("加载中...", "Loading..."),
    "dash.no_proc_demand": ("暂无采购/补货需求", "Nothing to purchase or replenish"),
    "dash.stock_sufficient": ("库存充足", "Sufficient"),
    "dash.cover_for_days": ("可撑 {n} 天", "Cover {n} d"),
    "dash.tag_purchase": ("采购", "Purchase"),
    "dash.tag_replenish": ("补货", "Replenish"),
}

REPL = [
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """          {_storeDim === 'brand' ? '品牌GMV' : t('dash.store_gmv')}""",
        """          {_storeDim === 'brand' ? t('dash.brand_gmv_title') : t('dash.store_gmv')}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """              店铺
            </span>""",
        """              {t('dash.dim_store')}
            </span>""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """              品牌
            </span>""",
        """              {t('dash.dim_brand')}
            </span>""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """                  {x.severity === 'warning' ? '警告' : t('dash.alert_overstock')}""",
        """                  {x.severity === 'warning' ? t('dash.sev_warning') : t('dash.alert_overstock')}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """            aria-label={`还有 ${lowStockTotal - lowStockShown.length} 条`}""",
        """            aria-label={t('dash.more_aria_n', { n: lowStockTotal - lowStockShown.length })}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """            更多 {fmtInt(lowStockTotal - lowStockShown.length)}""",
        """            {t('dash.more_btn', { n: fmtInt(lowStockTotal - lowStockShown.length) })}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """        <div className="section-title">采购&补货告警{procTotal > 0 ? ` (${procTotal})` : ''}</div>""",
        """        <div className="section-title">
          {t('dash.proc_card_title')}
          {procTotal > 0 ? ` (${procTotal})` : ''}
        </div>""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            加载中...
          </div>""",
        """          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            {t('dash.loading_ellipsis')}
          </div>""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            暂无采购/补货需求
          </div>""",
        """          <div className="small muted" style={{ padding: 12, textAlign: 'center' }}>
            {t('dash.no_proc_demand')}
          </div>""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """                    {x.tag}""",
        """                    {x.tag === '采购' ? t('dash.tag_purchase') : t('dash.tag_replenish')}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """                  : x.days_to_empty > 999
                    ? '库存充足'
                    : '可撑' + x.days_to_empty + '天'}""",
        """                  : x.days_to_empty > 999
                    ? t('dash.stock_sufficient')
                    : t('dash.cover_for_days', { n: x.days_to_empty })}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """            aria-label={`还有 ${procTotal - 5} 条`}""",
        """            aria-label={t('dash.more_aria_n', { n: procTotal - 5 })}""",
    ),
    (
        "frontend/src/pages/dashboard/ChartRows.tsx",
        """            更多 {procTotal - 5}""",
        """            {t('dash.more_btn', { n: procTotal - 5 })}""",
    ),
]
