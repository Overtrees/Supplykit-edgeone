LOCALE = {
    "dash.gmv_card_title": ("{p} GMV", "{p} GMV"),
    "dash.view_total": ("总", "Gross"),
    "dash.view_net": ("净", "Net"),
    "dash.view_payout": ("回款", "Settled"),
    "dash.view_payout_tip": (
        "回款按到账时间归属——仅月度/完整月可用",
        "Settlements are dated by arrival — monthly / full months only",
    ),
    "dash.payout_pending_short": ("回款待接入资金流水", "Settlement: awaiting cash-flow data"),
    "dash.orders_n_plain": ("{n} 单", "{n} orders"),
    "dash.no_basis": ("无可比数据", "No comparison base"),
    "dash.vs_prev_day": ("较昨日", "vs yesterday"),
    "dash.vs_prev_week": ("较上上周", "vs week before last"),
    "dash.vs_prev_month": ("较上上月", "vs month before last"),
    "dash.daily_avg_prefix": (" · 日均 ¥", " · avg/day ¥"),
    "dash.line_purchase": ("● 采购 {n}", "● Purchase {n}"),
    "dash.line_replenish": ("● 补货 {n}", "● Replenish {n}"),
    "dash.line_low_stock": ("● 低库存 {n}", "● Low stock {n}"),
    "dash.line_other": ("● 其他 {n}", "● Other {n}"),
    "dash.other_zero_plain": ("其他 0", "Other 0"),
    "dash.line_slow": ("● 滞销 {n}", "● Slow {n}"),
    "dash.health_card_title": ("库存健康度", "Stock health"),
    "dash.tab_own": ("自有", "Own"),
    "dash.tab_platform": ("平台", "Platform"),
    "dash.tab_c": ("C仓", "WH C"),
    "dash.tab_bc": ("BC", "BC"),
    "dash.score_value": ("{n}分", "{n} pts"),
    "dash.healthy_row": ("● {n}健康", "● {n} healthy"),
    "dash.warning_row": ("● {n}预警", "● {n} warning"),
    "dash.score_tip": ("{d} {v}分", "{d} {v} pts"),
    "dash.chart_gmv_by_brand": ("品牌GMV", "GMV by brand"),
    "dash.store_label": ("店铺", "Store"),
    "dash.brand_label": ("品牌", "Brand"),
    "dash.more_aria_n": ("还有 {n} 条", "{n} more"),
    "dash.proc_replenish_title": ("采购&补货告警", "Purchase & replenishment"),
    "dash.loading_short": ("加载中...", "Loading..."),
    "dash.no_proc_demand": ("暂无采购/补货需求", "No purchase or replenishment needed"),
    "dash.stock_sufficient": ("库存充足", "Stock sufficient"),
    "dash.cover_prefix": ("可撑", "Cover"),
    "dash.warn_tag": ("警告", "Warning"),
    "dash.dup_rows": ("重复的明细", "Duplicate rows"),
    "dash.purchase_tag": ("采购", "Purchase"),
    "dash.replenish_tag": ("补货", "Replenish"),
}

REPL = [
    # ── StatCards: GMV 卡 ──
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """          {periodTab === 'custom' ? '自定义' : periodLabel[periodTab]} GMV""",
        """          {t('dash.gmv_card_title', {
            p: periodTab === 'custom' ? t('dash.custom') : periodLabel[periodTab],
          })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """            ['total', '总'],
            ['net', '净'],
            ['payout', '回款'],""",
        """            ['total', t('dash.view_total')],
            ['net', t('dash.view_net')],
            ['payout', t('dash.view_payout')],""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """              title={
                v === 'payout' && !gmvPayoutOk ? '回款按到账时间归属——仅月度/完整月可用' : undefined
              }""",
        """              title={v === 'payout' && !gmvPayoutOk ? t('dash.view_payout_tip') : undefined}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """          回款待接入资金流水
        </span>""",
        """          {t('dash.payout_pending_short')}
        </span>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """          <span>{periodMeta.orders} 单</span>""",
        """          <span>{t('dash.orders_n_plain', { n: periodMeta.orders })}</span>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                  无可比数据
                </span>""",
        """                  {t('dash.no_basis')}
                </span>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """            const _cmpLabel =
              periodTab === 'today' ? '较昨日' : periodTab === 'week' ? '较上上周' : '较上上月'""",
        """            const _cmpLabel =
              periodTab === 'today'
                ? t('dash.vs_prev_day')
                : periodTab === 'week'
                  ? t('dash.vs_prev_week')
                  : t('dash.vs_prev_month')""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """            · 日均 ¥
            {(() => {""",
        """            {t('dash.daily_avg_prefix')}
            {(() => {""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                    回款待接入资金流水
                  </span>""",
        """                    {t('dash.payout_pending_short')}
                  </span>""",
    ),
    # ── StatCards: 采购/补货 明细行 ──
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                ● 采购 {fmtInt(procBuyN)}""",
        """                {t('dash.line_purchase', { n: fmtInt(procBuyN) })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                ● 补货 {fmtInt(procRepN)}""",
        """                {t('dash.line_replenish', { n: fmtInt(procRepN) })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """              ● 低库存 {fmtInt(lowStockTotal)}""",
        """              {t('dash.line_low_stock', { n: fmtInt(lowStockTotal) })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                ● 其他 {fmtInt(otherTotal)}""",
        """                {t('dash.line_other', { n: fmtInt(otherTotal) })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """              <span style={{ color: 'var(--muted)' }}>其他 0</span>""",
        """              <span style={{ color: 'var(--muted)' }}>{t('dash.other_zero_plain')}</span>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """              <span style={{ color: 'var(--muted2)' }}>● 滞销 {fmtInt(slowMovingTotal)}</span>""",
        """              <span style={{ color: 'var(--muted2)' }}>
                {t('dash.line_slow', { n: fmtInt(slowMovingTotal) })}
              </span>""",
    ),
    # ── StatCards: 健康卡 ──
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """        const bcLabel = healthTab === 'platform' ? 'C仓' : 'BC'""",
        """        const bcLabel = healthTab === 'platform' ? t('dash.tab_c') : 'BC'""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                库存{t('dash.healthy')}度""",
        """                {t('dash.health_card_title')}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                  自有
                </span>""",
        """                  {t('dash.tab_own')}
                </span>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                    平台
                  </span>""",
        """                    {t('dash.tab_platform')}
                  </span>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                        C仓
                      </div>""",
        """                        {t('dash.tab_c')}
                      </div>""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                {healthData.score != null ? healthData.score + '分' : '—'}""",
        """                {healthData.score != null ? t('dash.score_value', { n: healthData.score }) : '—'}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                    ● {healthData.healthy || 0}健康""",
        """                    {t('dash.healthy_row', { n: healthData.healthy || 0 })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                    ● {healthData.warning || 0}
                    {t('dash.low')}""",
        """                    {t('dash.warning_row', { n: healthData.warning || 0 })}""",
    ),
    (
        "frontend/src/pages/dashboard/StatCards.tsx",
        """                            title={`${String(x.date).slice(5)} ${v}分`}""",
        """                            title={t('dash.score_tip', { d: String(x.date).slice(5), v })}""",
    ),
]
