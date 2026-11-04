"""批量: insights 三 Tab(Purchase/Replen/Slow) 对齐词条

注意两处「中文值兼作判据」:
- PurchaseTab 的 timing: '建议'/'充足' 参与 className 分支 → 改 code 'suggest'/'enough'
- SlowTab 的等级同理 → 改 code
「天数」这类单位拼接统一走 ins.days_n, 避免 '天' 硬编码。
"""
import os, sys

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

LOCALE = [
    # ---- 通用(insights 内) ----
    ('ins.days_n', '{n}天', '{n}d'),
    ('ins.loading', '加载中...', 'Loading...'),
    ('ins.load_more', '加载更多', 'Load more'),
    ('ins.load_failed', '加载失败', 'Load failed'),
    ('ins.total_row', '合计', 'Total'),
    ('ins.showing_cols', '显示 {v}/{all} 列', 'Showing {v}/{all} columns'),
    ('ins.loaded_n_of_m', '已加载 {n}/{m} 条', 'Loaded {n}/{m}'),
    ('ins.avg_turnover', '平均周转 {v} 天', 'Avg turnover {v} days'),
    # ---- 采购 ----
    ('ins_p.banner_1', '采购闭环', 'Purchase loop'),
    ('ins_p.banner_2', '清洗导入库存（含在途列）', 'Import stock via cleansing (incl. in-transit)'),
    ('ins_p.banner_pre', '导出采购建议 → 内部评估执行下单 → ', 'Export suggestions → place orders → '),
    ('ins_p.banner_mid', '更新在途 → 建议自动抵扣，完成闭环',
     'updates in-transit → suggestions auto-offset, closing the loop'),
    ('ins_p.mode_bbcc', 'BBCC 口径(含B仓)', 'BBCC (incl. B warehouse)'),
    ('ins_p.mode_trad', '传统口径(不含B仓)', 'Traditional (excl. B warehouse)'),
    ('ins_p.timing_suggest', '建议', 'Suggested'),
    ('ins_p.timing_enough', '充足', 'Sufficient'),
    ('ins_p.own_avail', '(自有{a}', '(own {a}'),
    ('ins_p.own_transit', '(自有在途{a}', '(own in-transit {a}'),
    ('ins_p.b_in_transit', '+B在途{b}', '+B in-transit {b}'),
    ('ins_p.c_in_transit', '+C在途{c}', '+C in-transit {c}'),
    # ---- 补货 ----
    ('ins_r.title', '补货建议', 'Replenishment'),
    ('ins_r.ordered_n', '已下单 {n} 项', '{n} ordered'),
    ('ins_r.wh_b', 'B仓', 'B warehouse'),
    ('ins_r.over_90_confirm', '补后综合周转{n}天，已超90天考核红线，仍标记操作？',
     'Post-replenishment turnover is {n} days, beyond the 90-day red line. Mark anyway?'),
    ('ins_r.ordered_detail', '已下单 {n} 项', '{n} ordered'),
    ('ins_r.ordered_detail_hint', '点击查看入库日期与仓储天数', 'Tap to see arrival date and storage days'),
    ('ins_r.pending_arrival', '待入仓', 'Awaiting arrival'),
    # ---- 滞销 ----
    ('ins_s.subtitle', '按 SKU×仓库 · 品类滞销线 + 临期 + B仓仓储费',
     'By SKU × warehouse · category slow line + near-expiry + B warehouse storage fee'),
    ('ins_s.hide_disposed', '隐藏已处理', 'Hide processed'),
    ('ins_s.show_disposed', '查看已处理', 'Show processed'),
    ('ins_s.empty_disposed', '暂无处置记录', 'No disposal records'),
    ('ins_s.empty_slow', '暂无滞销', 'No slow-moving items'),
    ('ins_s.disposed', '已处理', 'Processed'),
    ('ins_s.lv_urgent', '紧急', 'Urgent'),
    ('ins_s.lv_dispose', '处置', 'Dispose'),
    ('ins_s.lv_slow', '滞销', 'Slow'),
    ('ins_s.lv_watch', '观察', 'Watch'),
]

P = 'frontend/src/pages/insights/PurchaseTab.tsx'
R = 'frontend/src/pages/insights/ReplenTab.tsx'
S = 'frontend/src/pages/insights/SlowTab.tsx'

EDITS = [
    # ================= PurchaseTab =================
    (P, """        <IconLightbulb size={13} style={{ verticalAlign: -2 }} /> <b>采购闭环</b>：导出采购建议 →
        内部评估执行下单 → <b>清洗导入库存（含在途列）</b>更新在途 → 建议自动抵扣，完成闭环""",
        """        <IconLightbulb size={13} style={{ verticalAlign: -2 }} />{' '}
        <b>{t('ins_p.banner_1')}</b>：{t('ins_p.banner_pre')}
        <b>{t('ins_p.banner_2')}</b>
        {t('ins_p.banner_mid')}"""),
    (P, "        <span>采购建议</span>",
        "        <span>{t('insights.purchase')}</span>"),
    (P, "            {replenMode === 'bbcc' ? 'BBCC 口径(含B仓)' : '传统口径(不含B仓)'}",
        "            {replenMode === 'bbcc' ? t('ins_p.mode_bbcc') : t('ins_p.mode_trad')}"),
    (P, """          显示 {purchaseVisCols.length}/{PURCHASE_COLS.length} 列 · 已加载{' '}
          {Math.min(purchaseLimit, filteredPurchase.length)}/{filteredPurchase.length} 条""",
        """          {t('ins.showing_cols', {
            v: purchaseVisCols.length,
            all: PURCHASE_COLS.length,
          })}{' '}
          ·{' '}
          {t('ins.loaded_n_of_m', {
            n: Math.min(purchaseLimit, filteredPurchase.length),
            m: filteredPurchase.length,
          })}"""),
    (P, """                const timing =
                  !x.purchase_qty || x.purchase_qty <= 0
                    ? '充足'
                    : x.after_turnover &&
                        (x.target_turnover || 15) > 0 &&
                        x.after_turnover <= (x.target_turnover || 15)
                      ? '建议'
                      : '充足'""",
        """                // 判据用 code, 显示层再映射(中文值不能兼作判据, 否则翻译即失效)
                const timing =
                  !x.purchase_qty || x.purchase_qty <= 0
                    ? 'enough'
                    : x.after_turnover &&
                        (x.target_turnover || 15) > 0 &&
                        x.after_turnover <= (x.target_turnover || 15)
                      ? 'suggest'
                      : 'enough'"""),
    (P, """                              (自有{x.own_available || 0}""",
        """                              {t('ins_p.own_avail', { a: x.own_available || 0 })}"""),
    (P, """                              (自有在途{x.own_transit || 0}
                              {replenMode === 'bbcc' ? `+B在途${x.b_transit || 0}` : ''}+C在途
                              {x.plat_transit || 0})""",
        """                              {t('ins_p.own_transit', { a: x.own_transit || 0 })}
                              {replenMode === 'bbcc' ? t('ins_p.b_in_transit', { b: x.b_transit || 0 }) : ''}
                              {t('ins_p.c_in_transit', { c: x.plat_transit || 0 })})"""),
    (P, "                            {x.actual_purchase > 0 ? x.after_turnover + '天' : '-'}",
        "                            {x.actual_purchase > 0 ? t('ins.days_n', { n: x.after_turnover }) : '-'}"),
    (P, """                            <span className={`pill ${timing === '建议' ? 'warning' : 'info'}`}>
                              {timing}
                            </span>""",
        """                            <span
                              className={`pill ${timing === 'suggest' ? 'warning' : 'info'}`}
                            >
                              {timing === 'suggest'
                                ? t('ins_p.timing_suggest')
                                : t('ins_p.timing_enough')}
                            </span>"""),
    (P, "                      合计\n                    </td>",
        "                      {t('ins.total_row')}\n                    </td>"),
    (P, "                            return '平均周转 ' + (avgTurnover || '—') + ' 天'",
        "                            return t('ins.avg_turnover', { v: avgTurnover || '—' })"),
    (P, "                加载中...\n              </span>",
        "                {t('ins.loading')}\n              </span>"),
    # ================= ReplenTab =================
    (R, '        <span>补货建议</span>',
        '        <span>{t(\'ins_r.title\')}</span>'),
    (R, """            已下单 {orderedKeys.length} 项""",
        """            {t('ins_r.ordered_n', { n: orderedKeys.length })}"""),
    (R, "            <ErrorRetry error={'加载失败'} onRetry={() => loadReplen(replenMode, globalChannel)} />",
        "            <ErrorRetry\n              error={t('ins.load_failed')}\n              onRetry={() => loadReplen(replenMode, globalChannel)}\n            />"),
    (R, "                              {replenMode === 'bbcc' ? 'B仓' : x.warehouse || x.store || '-'}",
        "                              {replenMode === 'bbcc'\n                                ? t('ins_r.wh_b')\n                                : x.warehouse || x.store || '-'}"),
    (R, "                                  ? (x.b_stock / x.daily_sales).toFixed(1) + '天'",
        "                                  ? t('ins.days_n', {\n                                      n: (x.b_stock / x.daily_sales).toFixed(1),\n                                    })"),
    (R, "                              {x.c_turnover != null ? x.c_turnover + '天' : '∞'}",
        "                              {x.c_turnover != null ? t('ins.days_n', { n: x.c_turnover }) : '∞'}"),
    (R, "                              {x.transit_turnover != null ? x.transit_turnover + '天' : '∞'}",
        "                              {x.transit_turnover != null\n                                ? t('ins.days_n', { n: x.transit_turnover })\n                                : '∞'}"),
    (R, "                                        `补后综合周转${x.combined_turnover}天，已超90天考核红线，仍标记操作？`,",
        "                                        t('ins_r.over_90_confirm', { n: x.combined_turnover }),"),
    (R, "                  加载更多\n                </span>",
        "                  {t('ins.load_more')}\n                </span>"),
    (R, """            <IconPackage size={14} style={{ verticalAlign: -3 }} /> 已下单 {orderedItems.length} 项
            · 点击查看入库日期与仓储天数""",
        """            <IconPackage size={14} style={{ verticalAlign: -3 }} />{' '}
            {t('ins_r.ordered_detail', { n: orderedItems.length })} ·{' '}
            {t('ins_r.ordered_detail_hint')}"""),
    (R, "                      {daysSinceArrival != null ? daysSinceArrival + '天' : '待入仓'}",
        "                      {daysSinceArrival != null\n                        ? t('ins.days_n', { n: daysSinceArrival })\n                        : t('ins_r.pending_arrival')}"),
    # ================= SlowTab =================
    (S, '        <span>滞销预警</span>',
        '        <span>{t(\'insights.slow\')}</span>'),
    (S, """          已加载 {Math.min(filteredDisp.length, slowTotal || filteredDisp.length)}/
          {slowTotal || filteredDisp.length} 条 · 显示 {slowVisCols.length}/{SLOW_COLS.length} 列""",
        """          {t('ins.loaded_n_of_m', {
            n: Math.min(filteredDisp.length, slowTotal || filteredDisp.length),
            m: slowTotal || filteredDisp.length,
          })}{' '}
          ·{' '}
          {t('ins.showing_cols', { v: slowVisCols.length, all: SLOW_COLS.length })}"""),
    (S, "          按 SKU×仓库 · 品类滞销线 + 临期 + B仓仓储费",
        "          {t('ins_s.subtitle')}"),
    (S, "            {showDisposed ? '隐藏已处理' : '查看已处理'}",
        "            {showDisposed ? t('ins_s.hide_disposed') : t('ins_s.show_disposed')}"),
    (S, "          <EmptyState icon=\"tag\" title={showDisposed ? '暂无处置记录' : '暂无滞销'} />",
        "          <EmptyState\n            icon=\"tag\"\n            title={showDisposed ? t('ins_s.empty_disposed') : t('ins_s.empty_slow')}\n          />"),
    (S, "            <IconCheck size={12} style={{ verticalAlign: -2 }} /> 已处理",
        "            <IconCheck size={12} style={{ verticalAlign: -2 }} /> {t('ins_s.disposed')}"),
    (S, "                          {x.days_zero == 999 ? '∞' : x.days_zero}天",
        "                          {x.days_zero == 999 ? '∞' : t('ins.days_n', { n: x.days_zero })}"),
    (S, """                      ? '紧急'""", """                      ? t('ins_s.lv_urgent')"""),
    (S, """                      ? '处置'""", """                      ? t('ins_s.lv_dispose')"""),
    (S, """                      ? '滞销'
                      : '观察'}""",
        """                      ? t('ins_s.lv_slow')
                      : t('ins_s.lv_watch')}"""),
    (S, "                加载中...\n              </span>",
        "                {t('ins.loading')}\n              </span>"),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'ins_p.banner_1'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 洞察三面板 ----\n' + block + cur[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cur)
        print(f'  [locale {tag}] +{len(LOCALE)} 键')


def main():
    add_locale()
    misses = []
    for rel, old, new in EDITS:
        p = os.path.join(ROOT, rel)
        with open(p, encoding='utf-8') as f:
            s = f.read()
        if old not in s:
            misses.append((rel, old.splitlines()[0][:70]))
            continue
        s = s.replace(old, new, 1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(s)
    if misses:
        print(f'\n未命中 {len(misses)} 条:')
        for rel, x in misses:
            print(f'  {rel} :: {x}')
        sys.exit(1)
    print(f'\n全部 {len(EDITS)} 条替换完成')


if __name__ == '__main__':
    main()
