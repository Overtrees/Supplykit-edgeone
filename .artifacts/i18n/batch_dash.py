"""批量: DashboardPage 对齐词条 + 采购/补货 tag 改判据码值

原先 procList 用中文 '采购'/'补货' 同时充当「判据」和「显示」——i18n 无法翻,
且 ChartRows/ProcSheet 靠 x.tag === '采购' 做分支。改为 kind: 'purchase' | 'replen',
显示走 dash.tag_purchase / dash.tag_replenish(已有词条)。
"""
import os, sys

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

LOCALE = [
    ('dash.funnel_tip_fmt', '数量: {n}单', 'Qty: {n} orders'),
    ('dash.funnel_rate', '占比: {p}%', 'Share: {p}%'),
    ('dash.funnel_conv', '转化率: {c}%', 'Conversion: {c}%'),
    ('dash.unit_orders', '{n}单', '{n} orders'),
    ('dash.unit_wan_orders', '{n}万单', '{n}0k orders'),
    ('dash.recalc_running', '规则重算中，告警数据稍后自动更新',
     'Rules are recalculating — alerts will refresh automatically'),
]

D = 'frontend/src/pages/DashboardPage.tsx'
CR = 'frontend/src/pages/dashboard/ChartRows.tsx'
PS = 'frontend/src/pages/dashboard/sheets/ProcSheet.tsx'

EDITS = [
    # ---------- DashboardPage: 告警分级表存词条键 ----------
    (D, """  const riskTabs: Array<['all' | 'red' | 'orange' | 'yellow', string, string]> = [
    ['all', '全部', 'var(--muted)'],
    ['red', '紧急', 'var(--danger)'],
    ['orange', '预警', 'var(--warning)'],
    ['yellow', '观察', 'var(--muted2)'],
  ]""",
     """  // 存词条键, 渲染时 t() 解析(级别值本身是判据, 用 code 不用文案)
  const riskTabs: Array<['all' | 'red' | 'orange' | 'yellow', string, string]> = [
    ['all', 'dash.all', 'var(--muted)'],
    ['red', 'dash.urgent', 'var(--danger)'],
    ['orange', 'dash.warning', 'var(--warning)'],
    ['yellow', 'dash.watch', 'var(--muted2)'],
  ]"""),
    # ---------- tag -> kind ----------
    (D, "          tag: '补货',", "          kind: 'replen',"),
    (D, "          tag: '采购',", "          kind: 'purchase',"),
    (D, """      const sorted = [...purNeed, ...repNeed].sort((a, b) => {
        if (a.tag !== b.tag) return a.tag === '采购' ? -1 : 1""",
     """      const sorted = [...purNeed, ...repNeed].sort((a, b) => {
        if (a.kind !== b.kind) return a.kind === 'purchase' ? -1 : 1"""),
    (D, "  const procBuyN = (Array.isArray(procList) ? procList : []).filter(x => x.tag === '采购').length\n  const procRepN = (Array.isArray(procList) ? procList : []).filter(x => x.tag === '补货').length",
     "  const procBuyN = (Array.isArray(procList) ? procList : []).filter(x => x.kind === 'purchase').length\n  const procRepN = (Array.isArray(procList) ? procList : []).filter(x => x.kind === 'replen').length"),
    # ---------- 其余文案 ----------
    (D, "      setDashErr(s.status === 'rejected' || !dashOk ? '加载失败，数据可能正在处理中' : '')",
     "      setDashErr(\n        s.status === 'rejected' || !dashOk ? t('dash.load_fail_processing') : '',\n      )"),
    (D, "  const gmvName = gmvView === 'net' ? '净GMV' : gmvView === 'payout' ? '回款' : 'GMV'",
     "  const gmvName =\n    gmvView === 'net' ? t('dash.net_gmv') : gmvView === 'payout' ? t('dash.payout') : 'GMV'"),
    (D, "          data: periodTrend.map(i => i['订单数']) || [],\n          color: '#0f766e',\n          yAxisIndex: 1,\n          name: '订单数',",
     "          data: periodTrend.map(i => i['订单数']) || [],\n          color: '#0f766e',\n          yAxisIndex: 1,\n          name: t('dash.order_count'),"),
    (D, "        data: [gmvName, '订单数'],",
     "        data: [gmvName, t('dash.order_count')],"),
    (D, "          return `${item.name}<br/>数量: ${item.value}单<br/>占比: ${item.percentage}%<br/>转化率: ${item.conversion}%`",
     "          return [\n            item.name,\n            t('dash.funnel_tip_fmt', { n: item.value }),\n            t('dash.funnel_rate', { p: item.percentage }),\n            t('dash.funnel_conv', { c: item.conversion }),\n          ].join('<br/>')"),
    (D, "              p.value >= 10000 ? (p.value / 10000).toFixed(1) + '万单' : `${p.value}单`,",
     "              p.value >= 10000\n                ? t('dash.unit_wan_orders', { n: (p.value / 10000).toFixed(1) })\n                : t('dash.unit_orders', { n: p.value }),"),
    (D, "          规则重算中，告警数据稍后自动更新",
     "          {t('dash.recalc_running')}"),
    # ---------- ChartRows ----------
    (CR, "                onGoInsights && onGoInsights(x.tag === '采购' ? 'purchase' : 'replen', x.sku)",
     "                onGoInsights && onGoInsights(x.kind === 'purchase' ? 'purchase' : 'replen', x.sku)"),
    (CR, "              key={x.tag + x.sku + i}",
     "              key={x.kind + x.sku + i}"),
    (CR, "                    className={'pill ' + (x.tag === '采购' ? 'warning' : 'danger')}",
     "                    className={'pill ' + (x.kind === 'purchase' ? 'warning' : 'danger')}"),
    (CR, "                    {x.tag === '采购' ? t('dash.tag_purchase') : t('dash.tag_replenish')}",
     "                    {x.kind === 'purchase' ? t('dash.tag_purchase') : t('dash.tag_replenish')}"),
    (CR, "                      color: x.tag === '采购' ? 'var(--warning)' : 'var(--danger)',",
     "                      color: x.kind === 'purchase' ? 'var(--warning)' : 'var(--danger)',"),
    # ---------- ProcSheet ----------
    (PS, "                onGoInsights && onGoInsights(x.tag === '采购' ? 'purchase' : 'replen', x.sku)",
     "                onGoInsights && onGoInsights(x.kind === 'purchase' ? 'purchase' : 'replen', x.sku)"),
    (PS, "                    className={'pill ' + (x.tag === '采购' ? 'warning' : 'danger')}",
     "                    className={'pill ' + (x.kind === 'purchase' ? 'warning' : 'danger')}"),
    (PS, "                    {x.tag}",
     "                    {x.kind === 'purchase' ? t('dash.tag_purchase') : t('dash.tag_replenish')}"),
    (PS, "                      color: x.tag === '采购' ? 'var(--warning)' : 'var(--danger)',",
     "                      color: x.kind === 'purchase' ? 'var(--warning)' : 'var(--danger)',"),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'dash.recalc_running'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 看板补充 ----\n' + block + cur[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cur)
        print(f'  [locale {tag}] +{len(LOCALE)} 键')


def main():
    # ProcSheet 需要 t
    pp = os.path.join(ROOT, PS)
    src = open(pp, encoding='utf-8').read()
    if "from '../../locale'" not in src:
        src = src.replace(
            "import { fmtInt } from '../format'",
            "import { fmtInt } from '../format'\nimport { t } from '../../locale'",
            1,
        )
        open(pp, 'w', encoding='utf-8').write(src)
        print('  [ProcSheet] +import t')

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
