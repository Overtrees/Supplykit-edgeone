"""批量: 清洗向导(HammerCleansing / MappingStep / PreviewStep / ResultStep) 对齐词条

渠道名统一走 channel.jd / channel.other(与 HammerProducts 等同一套)。
"""
import os, re, sys

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

LOCALE = [
    # 锤子: 清洗导入
    ('cw.header', '清洗导入 · 数据归入：', 'Import · destination: '),
    ('cw.subject_warn_pre', '当前全局主体是「', 'Current global entity is "'),
    ('cw.subject_warn_suf', '」，导入后请切换主体查看该数据', '" — switch entity after import to view the data'),
    ('cw.conflict_title', '重复数据冲突处理', 'Duplicate conflict handling'),
    ('cw.conflict_sum', '累加求和', 'Sum'),
    ('cw.conflict_overwrite', '覆盖', 'Overwrite'),
    ('cw.template_manage', '模版管理', 'Templates'),
    ('cw.colmap', '表格列状态映射', 'Column status mapping'),
    # 映射步骤
    ('cw.mapped', '已映射', 'Mapped'),
    ('cw.unmapped_req', '未映射 · 必选', 'Unmapped · required'),
    ('cw.unmapped_drop', '未映射 · 导入时丢弃', 'Unmapped · dropped on import'),
    ('cw.back', '← 返回', '← Back'),
    ('cw.next_preview', '下一步 · 预览', 'Next · Preview'),
    ('cw.one_click', '一键执行', 'Run all'),
    # 预览步骤
    ('cw.preview_title', '清洗预览', 'Cleaning preview'),
    ('cw.pv_total', '共 {n} 行', '{n} row(s)'),
    ('cw.pv_shown_50', '已展示前 50 行', 'Showing first 50 rows'),
    ('cw.pv_cols', '{n} 列', '{n} column(s)'),
    ('cw.pv_issue_pre', '预览发现 ', 'Preview found '),
    ('cw.pv_issue_sku', ' 行缺SKU、', ' row(s) missing SKU, '),
    ('cw.pv_issue_dup', ' 个重复SKU（已标红）——', ' duplicated SKU(s) (highlighted) — '),
    ('cw.pv_issue_suf', '建议返回修正后导入', 'please go back, fix and re-import'),
    ('cw.pv_no_mapping', '没有已映射的字段，请返回并设置字段映射', 'No mapped fields — go back and set field mapping'),
    ('cw.pv_swipe_hint', '← 左右滑动查看 · 仅显示已映射的 {n} 列 →', '← Swipe to browse · {n} mapped column(s) →'),
    ('cw.issue_no_sku', '缺SKU', 'Missing SKU'),
    ('cw.issue_dup_sku', '重复SKU', 'Duplicate SKU'),
    ('cw.confirm_write', '确认写入 {n} 条', 'Write {n} record(s)'),
    # 结果步骤
    ('cw.res_async', '已提交后台处理', 'Submitted for background processing'),
    ('cw.res_ok', '导入完成', 'Import complete'),
    ('cw.res_partial', '导入未完成', 'Import incomplete'),
    ('cw.res_detail_pre', '（侧边栏 ', '(see sidebar '),
    ('cw.res_detail_suf', ' 查看详情）', ' for details)'),
    ('cw.res_st_submitted', '已提交', 'Submitted'),
    ('cw.res_st_processing', '处理中', 'Processing'),
    ('cw.res_st_success', '成功', 'Succeeded'),
    ('cw.res_st_skipped', '跳过', 'Skipped'),
    ('cw.go_tasks', '前往任务管理页查看进度 →', 'Go to Tasks to view progress →'),
    ('cw.fail_detail', '失败明细（可对照修正后重新导入）', 'Failure details (fix and re-import)'),
    ('cw.restart', '重新开始', 'Start over'),
    ('cw.same_format', '导入相同格式', 'Import same format'),
]

HC = 'frontend/src/components/hammer/HammerCleansing.tsx'
MS = 'frontend/src/pages/cleansing/MappingStep.tsx'
PV = 'frontend/src/pages/cleansing/PreviewStep.tsx'
RS = 'frontend/src/pages/cleansing/ResultStep.tsx'

EDITS = [
    # ============ HammerCleansing ============
    (HC, "  const target = hammerCleansingChannel === 'jd' ? '京东' : '其他渠道'",
         "  const target = hammerCleansingChannel === 'jd' ? t('channel.jd') : t('channel.other')"),
    (HC, "        清洗导入 · 数据归入：<b>{target}</b>",
         "        {t('cw.header')}\n        <b>{target}</b>"),
    (HC, """          ['jd', '京东'],
          ['other', '其他渠道'],""",
         """          ['jd', t('channel.jd')],
          ['other', t('channel.other')],"""),
    (HC, """              <IconWarning size={13} style={{ verticalAlign: -2 }} /> 当前全局主体是「
              {channel === 'jd' ? '京东' : '其他渠道'}」，导入后请切换主体查看该数据""",
         """              <IconWarning size={13} style={{ verticalAlign: -2 }} />{' '}
              {t('cw.subject_warn_pre')}
              {channel === 'jd' ? t('channel.jd') : t('channel.other')}
              {t('cw.subject_warn_suf')}"""),
    (HC, "                重复数据冲突处理\n              </div>",
         "                {t('cw.conflict_title')}\n              </div>"),
    (HC, "                  累加求和\n                </span>",
         "                  {t('cw.conflict_sum')}\n                </span>"),
    (HC, "                  覆盖\n                </span>",
         "                  {t('cw.conflict_overwrite')}\n                </span>"),
    (HC, "                模版管理\n              </button>",
         "                {t('cw.template_manage')}\n              </button>"),
    (HC, "                  <IconTag size={13} /> 表格列状态映射",
         "                  <IconTag size={13} /> {t('cw.colmap')}"),

    # ============ MappingStep ============
    (MS, "                          <IconCheck size={11} style={{ verticalAlign: -1 }} /> 已映射",
         "                          <IconCheck size={11} style={{ verticalAlign: -1 }} />{' '}\n                          {t('cw.mapped')}"),
    (MS, """                        ? '未映射 · 必选'
                        : '未映射 · 导入时丢弃'}""",
         """                        ? t('cw.unmapped_req')
                        : t('cw.unmapped_drop')}"""),
    (MS, "                      ← 返回",
         "                      {t('cw.back')}"),
    (MS, "                      下一步 · 预览",
         "                      {t('cw.next_preview')}"),
    (MS, "                  <IconLightning size={14} /> 一键执行",
         "                  <IconLightning size={14} /> {t('cw.one_click')}"),

    # ============ PreviewStep ============
    (PV, "            <span style={{ fontSize: 'var(--font-md)', fontWeight: 700 }}>清洗预览</span>",
         "            <span style={{ fontSize: 'var(--font-md)', fontWeight: 700 }}>\n              {t('cw.preview_title')}\n            </span>"),
    (PV, """            const parts: string[] = [`共 ${pv.total} 行`]
            if (pv.total > 50) parts.push('已展示前 50 行')
            if (n > 0) parts.push(`${n} 列`)""",
         """            const parts: string[] = [t('cw.pv_total', { n: pv.total })]
            if (pv.total > 50) parts.push(t('cw.pv_shown_50'))
            if (n > 0) parts.push(t('cw.pv_cols', { n }))"""),
    (PV, """                      预览发现 <b>{missing}</b> 行缺SKU、<b>{dupSkus.size}</b> 个重复SKU（已标红）——
                      建议返回修正后导入""",
         """                      {t('cw.pv_issue_pre')}
                      <b>{missing}</b>
                      {t('cw.pv_issue_sku')}
                      <b>{dupSkus.size}</b>
                      {t('cw.pv_issue_dup')}
                      {t('cw.pv_issue_suf')}"""),
    (PV, "                  没有已映射的字段，请返回并设置字段映射",
         "                  {t('cw.pv_no_mapping')}"),
    (PV, "              ← 左右滑动查看 · 仅显示已映射的 {cols.length} 列 →",
         "              {t('cw.pv_swipe_hint', { n: cols.length })}"),
    (PV, "              const issue = skuVal === '' ? '缺SKU' : dupSkus.has(skuVal) ? '重复SKU' : ''",
         "              const issue =\n                skuVal === ''\n                  ? t('cw.issue_no_sku')\n                  : dupSkus.has(skuVal)\n                    ? t('cw.issue_dup_sku')\n                    : ''"),
    (PV, "                    ← 返回",
         "                    {t('cw.back')}"),
    (PV, "                    确认写入 {pv.total} 条",
         "                    {t('cw.confirm_write', { n: pv.total })}"),

    # ============ ResultStep ============
    (RS, "            {res.async ? '已提交后台处理' : res.success > 0 ? '导入完成' : '导入未完成'}",
         "            {res.async\n              ? t('cw.res_async')\n              : res.success > 0\n                ? t('cw.res_ok')\n                : t('cw.res_partial')}"),
    (RS, """            （侧边栏 <IconAlert size={12} style={{ display: 'inline', verticalAlign: 'middle' }} />{' '}
            查看详情）""",
         """            {t('cw.res_detail_pre')}
            <IconAlert size={12} style={{ display: 'inline', verticalAlign: 'middle' }} />{' '}
            {t('cw.res_detail_suf')}"""),
    (RS, '              <div className="small muted">已提交</div>',
         '              <div className="small muted">{t(\'cw.res_st_submitted\')}</div>'),
    (RS, '              <div className="small muted">处理中</div>',
         '              <div className="small muted">{t(\'cw.res_st_processing\')}</div>'),
    (RS, '              <div className="small muted">成功</div>',
         '              <div className="small muted">{t(\'cw.res_st_success\')}</div>'),
    (RS, '              <div className="small muted">跳过</div>',
         '              <div className="small muted">{t(\'cw.res_st_skipped\')}</div>'),
    (RS, "                  前往任务管理页查看进度 →",
         "                  {t('cw.go_tasks')}"),
    (RS, "                  失败明细（可对照修正后重新导入）",
         "                  {t('cw.fail_detail')}"),
    (RS, "                  重新开始",
         "                  {t('cw.restart')}"),
    (RS, "                  <IconFolder size={14} /> 导入相同格式",
         "                  <IconFolder size={14} /> {t('cw.same_format')}"),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'cw.preview_title'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 清洗向导 ----\n' + block + cur[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cur)
        print(f'  [locale {tag}] +{len(LOCALE)} 键')


def ensure_import(rel):
    """按文件相对 src 的深度补 import { t } from '<up>locale'。"""
    p = os.path.join(ROOT, rel)
    s = open(p, encoding='utf-8').read()
    if re.search(r"from '(?:\.\./)+locale'", s):
        return
    lines = s.split('\n')
    idx = max((i for i, l in enumerate(lines) if l.startswith('import ')), default=-1)
    if idx < 0:
        print(f'  [{rel}] 无 import 语句, 跳过')
        return
    # src/components/hammer/x.tsx -> src/components/hammer -> 到 src 需 ../../../
    depth = rel.count('/') - 1          # rel 以 frontend/src 之后为界, 目录层数
    up = '../' * (depth - 1)
    lines.insert(idx + 1, f"import {{ t }} from '{up}locale'")
    open(p, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'  [{rel}] +import t ({up}locale)')


def main():
    for rel in (HC, MS, PV, RS):
        ensure_import(rel)
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
