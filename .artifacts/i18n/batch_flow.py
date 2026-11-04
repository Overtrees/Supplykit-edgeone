"""批量: FlowRecordsSheet 对齐词条

表头列名复用现有 fld.*(字段字典同源: SKU/仓库/生产日期/数量/备注), 日期用新增 flw.date。
仓库类型 WT_LABEL 改存词条键(own/platform/platform_b)。
doDelete 的 reason 是**送给后端的审计文本**, 不属界面文案, 保持中文。
"""
import os, sys

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

LOCALE = [
    ('flw.title', '出入库明细', 'Inbound / outbound details'),
    ('flw.tab_in', '入库', 'Inbound'),
    ('flw.tab_out', '出库', 'Outbound'),
    ('flw.total_n', '共 {n} 条', '{n} record(s)'),
    ('flw.date', '日期', 'Date'),
    ('flw.search_sku', '搜索 SKU', 'Search SKU'),
    ('flw.empty_filtered', '当前筛选无匹配记录，可调整日期或清空搜索',
     'No records match the filter — adjust the date range or clear the search'),
    ('flw.empty', '暂无记录', 'No records'),
    ('flw.all_loaded', '已全部加载（{n} 条）', 'All loaded ({n})'),
    ('flw.default_supplier', '供应商', 'Supplier'),
    ('flw.default_target_wh', '目标仓', 'Target warehouse'),
    ('flw.wh_own', '自有仓', 'Own warehouse'),
    ('flw.wh_platform', '平台仓', 'Platform warehouse'),
    ('flw.wh_b', 'B仓', 'B warehouse'),
]

F = 'frontend/src/components/FlowRecordsSheet.tsx'

EDITS = [
    (F, "import { IconTrash } from './Icons'",
        "import { IconTrash } from './Icons'\nimport { t } from '../locale'"),
    (F, "  const [extraLabel, setExtraLabel] = useState('供应商')",
        "  // extraLabel 是后端给的列名文案, 后端未给时按 tab 取词条兜底\n  const [extraLabel, setExtraLabel] = useState(t('flw.default_supplier'))"),
    (F, "  const WT_LABEL: Record<string, string> = { own: '自有仓', platform: '平台仓', platform_b: 'B仓' }",
        "  // 存词条键, 渲染时解析(与 rules/labels 的 WHS 同一手法)\n  const WT_LABEL: Record<string, string> = {\n    own: 'flw.wh_own',\n    platform: 'flw.wh_platform',\n    platform_b: 'flw.wh_b',\n  }"),
    (F, "      setExtraLabel(d.extra_label || (t === 'inbound' ? '供应商' : '目标仓'))",
        "      setExtraLabel(\n        d.extra_label || (t === 'inbound' ? t('flw.default_supplier') : t('flw.default_target_wh')),\n      )"),
    (F, "      if (r?.data?.ok === false) throw new Error(r?.data?.error || '删除失败')",
        "      if (r?.data?.ok === false) throw new Error(r?.data?.error || t('common.delete_failed'))"),
    (F, '            <div className="flow-head-title">出入库明细</div>',
        '            <div className="flow-head-title">{t(\'flw.title\')}</div>'),
    (F, "                  ['inbound', '入库'],\n                  ['outbound', '出库'],",
        "                  ['inbound', t('flw.tab_in')],\n                  ['outbound', t('flw.tab_out')],"),
    (F, '            <span className="flow-head-count">共 {total} 条</span>',
        '            <span className="flow-head-count">{t(\'flw.total_n\', { n: total })}</span>'),
    (F, '              placeholder="搜索 SKU"',
        "              placeholder={t('flw.search_sku')}"),
    (F, """                  {[
                    '日期',
                    'SKU',
                    '仓库',
                    '生产日期',
                    '截止日期',
                    '数量',
                    extraLabel,
                    '备注',
                    '',
                  ].map((h, i) => (""",
        """                  {[
                    t('flw.date'),
                    t('fld.sku'),
                    t('fld.warehouse'),
                    t('fld.prod_date'),
                    t('fld.exp_date'),
                    t('fld.quantity'),
                    extraLabel,
                    t('fld.remark'),
                    '',
                  ].map((h, i) => ("""),
    (F, "                    <td>{it.warehouse || WT_LABEL[it.warehouse_type] || '—'}</td>",
        "                    <td>\n                      {it.warehouse ||\n                        (WT_LABEL[it.warehouse_type] ? t(WT_LABEL[it.warehouse_type]) : '—')}\n                    </td>"),
    (F, '                        title="删除"',
        "                        title={t('common.delete')}"),
    (F, """                      {dateFrom || dateTo || skuQ
                        ? '当前筛选无匹配记录，可调整日期或清空搜索'
                        : '暂无记录'}""",
        """                      {dateFrom || dateTo || skuQ ? t('flw.empty_filtered') : t('flw.empty')}"""),
    (F, '          {loading && <div className="flow-foot">加载中…</div>}',
        '          {loading && <div className="flow-foot">{t(\'common.loading\')}</div>}'),
    (F, '            <div className="flow-foot">已全部加载（{total} 条）</div>',
        '            <div className="flow-foot">{t(\'flw.all_loaded\', { n: total })}</div>'),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'flw.title'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 出入库明细弹窗 ----\n' + block + cur[pos:]
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
