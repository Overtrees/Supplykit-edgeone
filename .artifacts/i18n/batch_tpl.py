"""批量: TemplateManageDialog 对齐词条 + DOC_TYPE_NAMES 词表化

DOC_TYPE_NAMES 原先是 code→code 的空映射(改名改到一半), 三处调用都拿不到中文名。
本批改为 code→词条键, 并新增 docTypeLabel() 统一解析; 界面上「预设模板-XX」「XX导入模板.csv」
「已存在同名模板XX」三处一起受益。
"""
import os, sys

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

LOCALE = [
    # 导入类型名词(用于标题/文件名, 区别于下拉的动词式 cl.opt_*)
    ('cl.dt_order', '订单', 'Order'),
    ('cl.dt_inventory', '自有仓库存', 'Own warehouse stock'),
    ('cl.dt_platform_inv', '平台仓库存', 'Platform stock'),
    ('cl.dt_inventory_b', 'B仓库存', 'B warehouse stock'),
    ('cl.dt_product', '商品', 'Product'),
    ('cl.dt_supplier', '供应商', 'Supplier'),
    ('cl.dt_inbound', '入库记录', 'Inbound records'),
    ('cl.dt_outbound', '出库记录', 'Outbound records'),
    ('cl.dt_gmv_flow', 'GMV 回款', 'GMV receipts'),
    # 模板管理弹窗
    ('tpl.title', '模板管理', 'Templates'),
    ('tpl.preset_title', '预设模板-{type}', 'Preset template · {type}'),
    ('tpl.dl_csv', '↓ 下载 CSV', '↓ Download CSV'),
    ('tpl.no_preset', '该类型暂无预设字段', 'No preset fields for this type'),
    ('tpl.csv_name', '{type}导入模板.csv', '{type} import template.csv'),
    ('tpl.downloaded', '模板已下载，按表头填充后导入', 'Template downloaded — fill by header then import'),
    ('tpl.mine', '我的模板', 'My templates'),
    ('tpl.pick_ph', '选择映射模板...', 'Select a mapping template...'),
    ('tpl.other_group', '── {type}模板（{n}个） ──', '── {type} templates ({n}) ──'),
    ('tpl.empty', '还没有模板：先完成字段映射 → 输入名称保存', 'No templates yet: finish field mapping → enter a name to save'),
    ('tpl.name_ph', '新模板名称', 'New template name'),
    ('tpl.need_name', '请输入模板名称', 'Please enter a template name'),
    ('tpl.need_mapping', '请先完成字段映射再保存模板', 'Finish field mapping before saving a template'),
    ('tpl.dup_confirm', '已存在同名模板「{name}」（{type}），是否覆盖更新？', 'A template named "{name}" ({type}) already exists. Overwrite?'),
    ('tpl.saved', '模板已保存', 'Template saved'),
    ('tpl.save_failed', '模板保存失败: {msg}', 'Failed to save template: {msg}'),
    ('tpl.applied', '模板已应用', 'Template applied'),
    ('tpl.del_confirm', '删除模板「{name}」？', 'Delete template "{name}"?'),
    ('tpl.save_btn', '保存模板', 'Save template'),
    ('tpl.save_hint_title', '保存前先在映射页完成字段映射，模板会记录当前映射关系',
     'Finish field mapping first; the template records the current mapping'),
    ('tpl.save_hint', 'ⓘ 保存当前映射为模板', 'ⓘ Save current mapping as a template'),
    ('tpl.cf', '自定义字段', 'Custom fields'),
    ('tpl.cf_n', '{n} 个', '{n}'),
    ('tpl.cf_none', '可自定义映射目标字段名', 'Custom target field names'),
    ('tpl.collapse', '收起 ▲', 'Collapse ▲'),
    ('tpl.expand', '展开 ▼', 'Expand ▼'),
    ('tpl.field_name_ph', '字段名', 'Field name'),
    ('tpl.tp_string', '文本', 'Text'),
    ('tpl.tp_number', '数字', 'Number'),
    ('tpl.tp_date', '日期', 'Date'),
    ('tpl.add_cf', '+ 添加自定义字段', '+ Add custom field'),
    ('tpl.close', '关闭', 'Close'),
    # 必选/可选标记
    ('tpl.req', '(必)', '(req.)'),
    ('tpl.opt', '(选)', '(opt.)'),
    ('common.deleted', '已删除', 'Deleted'),
    ('common.delete_failed', '删除失败', 'Delete failed'),
]

F = 'frontend/src/components/TemplateManageDialog.tsx'
FLD = 'frontend/src/pages/cleansing/fields.ts'

EDITS = [
    # ---------- fields.ts: DOC_TYPE_NAMES 词表化 ----------
    (FLD, """// 导入类型中文名(下载文件名/预设标题)
export const DOC_TYPE_NAMES: Record<string, string> = {
  order: 'order',
  inventory: 'inventory',
  platform_inv: 'platform_inv',
  inventory_b: 'inventory_b',
  product: 'product',
  supplier: 'supplier',
  inbound: 'inbound',
  outbound: 'outbound',
  gmv_flow: 'gmv_flow',
}""",
     """// 导入类型的中文名词条(下载文件名/预设标题)。存的是**词条键**, 渲染走 docTypeLabel()
export const DOC_TYPE_NAMES: Record<string, string> = {
  order: 'cl.dt_order',
  inventory: 'cl.dt_inventory',
  platform_inv: 'cl.dt_platform_inv',
  inventory_b: 'cl.dt_inventory_b',
  product: 'cl.dt_product',
  supplier: 'cl.dt_supplier',
  inbound: 'cl.dt_inbound',
  outbound: 'cl.dt_outbound',
  gmv_flow: 'cl.dt_gmv_flow',
}
/** 导入类型的中文名词(未知类型原样返回 code) */
export const docTypeLabel = (tt: string) =>
  DOC_TYPE_NAMES[tt] ? t(DOC_TYPE_NAMES[tt]) : tt"""),

    # ---------- TemplateManageDialog ----------
    (F, "import {\n  presetMapping,\n  presetExample,\n  presetRequired,\n  DOC_TYPE_NAMES,\n} from '../pages/cleansing/fields'",
        "import {\n  presetMapping,\n  presetExample,\n  presetRequired,\n  docTypeLabel,\n} from '../pages/cleansing/fields'\nimport { t } from '../locale'"),
    (F, "      toast.success('模板已应用')",
        "      toast.success(t('tpl.applied'))"),
    (F, "    if (!window.confirm(`删除模板「${name}」？`)) return",
        "    if (!window.confirm(t('tpl.del_confirm', { name }))) return"),
    (F, "      toast.success('已删除')",
        "      toast.success(t('common.deleted'))"),
    (F, "      toast.error('删除失败')",
        "      toast.error(t('common.delete_failed'))"),
    (F, "    changeCf([...cf, { t: 'field_' + Date.now(), l: '自定义字段', tp: 'string' }])",
        "    changeCf([\n      ...cf,\n      { t: 'field_' + Date.now(), l: t('tpl.cf'), tp: 'string' },\n    ])"),
    (F, "            <span>模板管理</span>",
        "            <span>{t('tpl.title')}</span>"),
    (F, "            <span>预设模板-{DOC_TYPE_NAMES[tt] || tt}</span>",
        "            <span>{t('tpl.preset_title', { type: docTypeLabel(tt) })}</span>"),
    (F, "                if (!_cols.length) return toast.error('该类型暂无预设字段')",
        "                if (!_cols.length) return toast.error(t('tpl.no_preset'))"),
    (F, "                  ([label, target]) => label + (_req.includes(String(target)) ? '(必)' : '(选)'),",
        "                  ([label, target]) =>\n                    label +\n                    (_req.includes(String(target)) ? t('tpl.req') : t('tpl.opt')),"),
    (F, "                a.download = (DOC_TYPE_NAMES[tt] || tt) + '导入模板.csv'",
        "                a.download = t('tpl.csv_name', { type: docTypeLabel(tt) })"),
    (F, "                toast.success('模板已下载，按表头填充后导入')",
        "                toast.success(t('tpl.downloaded'))"),
    (F, "              ↓ 下载 CSV\n            </button>",
        "              {t('tpl.dl_csv')}\n            </button>"),
    (F, '          <div className="tpl-section-title">我的模板</div>',
        '          <div className="tpl-section-title">{t(\'tpl.mine\')}</div>'),
    (F, '                <option value="">选择映射模板...</option>',
        '                <option value="">{t(\'tpl.pick_ph\')}</option>'),
    (F, """                    ── {tt === 'order' ? '库存' : '订单'}模板（
                    {templates.filter(t => t.doc_type !== tt).length}个） ──""",
        """                    {t('tpl.other_group', {
                      type: docTypeLabel(tt === 'order' ? 'inventory' : 'order'),
                      n: templates.filter(x => x.doc_type !== tt).length,
                    })}"""),
    (F, '              <div className="tpl-empty">还没有模板：先完成字段映射 → 输入名称保存</div>',
        '              <div className="tpl-empty">{t(\'tpl.empty\')}</div>'),
    (F, '              placeholder="新模板名称"',
        "              placeholder={t('tpl.name_ph')}"),
    (F, "                if (!n) return toast.error('请输入模板名称')",
        "                if (!n) return toast.error(t('tpl.need_name'))"),
    (F, "                  return toast.error('请先完成字段映射再保存模板')",
        "                  return toast.error(t('tpl.need_mapping'))"),
    (F, "                  const okOverwrite = window.confirm(\n                    `已存在同名模板「${n}」（${DOC_TYPE_NAMES[tt] || tt}），是否覆盖更新？`,\n                  )",
        "                  const okOverwrite = window.confirm(\n                    t('tpl.dup_confirm', { name: n, type: docTypeLabel(tt) }),\n                  )"),
    (F, "                  const msg = r?.data?.message || '模板已保存'",
        "                  const msg = r?.data?.message || t('tpl.saved')"),
    (F, "                  toast.error('模板保存失败: ' + (e.response?.data?.detail || e.message))",
        "                  toast.error(\n                    t('tpl.save_failed', { msg: e.response?.data?.detail || e.message }),\n                  )"),
    (F, "              保存模板\n            </button>",
        "              {t('tpl.save_btn')}\n            </button>"),
    (F, '            <span title="保存前先在映射页完成字段映射，模板会记录当前映射关系">\n              ⓘ 保存当前映射为模板\n            </span>',
        "            <span title={t('tpl.save_hint_title')}>{t('tpl.save_hint')}</span>"),
    (F, "              自定义字段\n              <span",
        "              {t('tpl.cf')}\n              <span"),
    (F, "                {cf.length ? `${cf.length} 个` : '可自定义映射目标字段名'}",
        "                {cf.length ? t('tpl.cf_n', { n: cf.length }) : t('tpl.cf_none')}"),
    (F, "              {cfOpen ? '收起 ▲' : '展开 ▼'}",
        "              {cfOpen ? t('tpl.collapse') : t('tpl.expand')}"),
    (F, '                  placeholder="字段名"',
        "                  placeholder={t('tpl.field_name_ph')}"),
    (F, '                  <option value="string">文本</option>\n                  <option value="number">数字</option>\n                  <option value="date">日期</option>',
        '                  <option value="string">{t(\'tpl.tp_string\')}</option>\n                  <option value="number">{t(\'tpl.tp_number\')}</option>\n                  <option value="date">{t(\'tpl.tp_date\')}</option>'),
    (F, "                  删除\n                </button>",
        "                  {t('common.delete')}\n                </button>"),
    (F, "              + 添加自定义字段\n            </button>",
        "              {t('tpl.add_cf')}\n            </button>"),
    (F, "            关闭\n          </div>",
        "            {t('tpl.close')}\n          </div>"),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'tpl.title'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 映射模板管理 ----\n' + block + cur[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cur)
        print(f'  [locale {tag}] +{len(LOCALE)} 键')


def main():
    # fields.ts 需要 t
    fp = os.path.join(ROOT, FLD)
    src = open(fp, encoding='utf-8').read()
    if "from '../../locale'" not in src:
        lines = src.split('\n')
        idx = 0
        if lines and lines[0].startswith('//'):
            idx = next((i + 1 for i, l in enumerate(lines) if not l.startswith('//')), 0)
        lines.insert(idx, "import { t } from '../../locale'")
        open(fp, 'w', encoding='utf-8').write('\n'.join(lines))
        print('  [fields.ts] +import t')

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
