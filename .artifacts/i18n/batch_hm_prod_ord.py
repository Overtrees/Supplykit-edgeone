"""批量: HammerProducts + HammerOrders 对齐词条。

批量操作区的文案与 hr.* (规则锤子) 同构, 与其复制出第三套, 不如收敛为一套通用键 hx.*,
用 {obj} 参数区分「商品/订单/规则」。hr.* 保留(规则面板已引用), 新增 hx.* 供本批使用。
"""
import os, sys

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

LOCALE = [
    # ---- 通用批量操作区(商品/订单复用) ----
    ('hx.batch', '批量操作', 'Batch'),
    ('hx.enter_batch', '进入批量模式', 'Enter batch mode'),
    ('hx.exit_batch', '退出批量模式', 'Exit batch mode'),
    ('hx.select_all_toggle', '全选/取消', 'Select / unselect all'),
    ('hx.selected_pre', '已选', 'Selected'),
    ('hx.selected_suf', '项', 'item(s)'),
    ('hx.batch_delete', '批量删除', 'Batch delete'),
    ('hx.done_n', '{label}完成: {n} 项', '{label} done: {n} item(s)'),
    ('hx.fail', '{label}失败: {msg}', '{label} failed: {msg}'),
    ('hx.undo', '撤销', 'Undo'),
    ('hx.undo_failed', '撤销失败: {msg}', 'Undo failed: {msg}'),
    ('hx.undone', '已撤销删除', 'Delete undone'),
    ('hx.view_progress', '查看进度 →', 'View progress →'),
    ('hx.submit_failed', '提交失败', 'Submit failed'),
    ('hx.export_submitted', '导出任务已提交', 'Export task submitted'),
    ('hx.op_failed', '操作失败: {msg}', 'Operation failed: {msg}'),
    # ---- 商品专属 ----
    ('hx.pick_prod', '请先勾选商品', 'Please select products first'),
    ('hx.prod_batch_hint', '勾选商品后在此批量操作', 'Select products to batch operate here'),
    ('hx.prod_all_active', '所选商品已全部启用', 'All selected products are enabled'),
    ('hx.prod_all_inactive', '所选商品已全部停用', 'All selected products are disabled'),
    ('hx.prod_del_confirm', '删除 {n} 个商品？可在回收站恢复', 'Delete {n} product(s)? Restorable from recycle bin'),
    ('hx.act_enable', '启用', 'Enable'),
    ('hx.act_disable', '停用', 'Disable'),
    ('hx.act_delete', '删除', 'Delete'),
    ('hx.batch_enable', '批量启用', 'Batch enable'),
    ('hx.batch_disable', '批量停用', 'Batch disable'),
    ('hx.search_prod_ph', '搜索SKU/商品名...', 'Search SKU / product name...'),
    # ---- 订单专属 ----
    ('hx.order_batch_hint', '勾选订单后在此批量操作 · 删除为软删（回收站可恢复）',
     'Select orders to batch operate here · Delete is soft (restorable from recycle bin)'),
    ('hx.purge', '永久删除', 'Delete permanently'),
    ('hx.purge_title', '永久删除订单', 'Permanently delete orders'),
    ('hx.purge_desc', '永久删除 {n} 条订单？此操作不可撤销',
     'Permanently delete {n} order(s)? This cannot be undone'),
    ('hx.purged_n', '已永久删除 {n} 条', 'Permanently deleted {n} record(s)'),
    ('hx.deleted_n', '已删除 {n} 条', 'Deleted {n} record(s)'),
    ('hx.search_order_ph', '搜索单号/商品/SKU...', 'Search order no. / product / SKU...'),
]

P = 'frontend/src/components/hammer/HammerProducts.tsx'
O = 'frontend/src/components/hammer/HammerOrders.tsx'

EDITS = [
    # ================= HammerProducts =================
    (P, "      toast.error('请先勾选商品')",
        "      toast.error(t('hx.pick_prod'))"),
    (P, "    if (action === 'delete' && !window.confirm('删除 ' + ids.length + ' 个商品？可在回收站恢复'))\n      return",
        "    if (action === 'delete' && !window.confirm(t('hx.prod_del_confirm', { n: ids.length })))\n      return"),
    (P, "          title: label + '完成: ' + ids.length + ' 项',",
        "          title: t('hx.done_n', { label, n: ids.length }),"),
    (P, "            label: '撤销',",
        "            label: t('hx.undo'),"),
    (P, "                toast.success('已撤销删除')",
        "                toast.success(t('hx.undone'))"),
    (P, "                toast.error('撤销失败: ' + (e.message || ''))",
        "                toast.error(t('hx.undo_failed', { msg: e.message || '' }))"),
    (P, "        toast.success(label + '完成: ' + ids.length + ' 项')",
        "        toast.success(t('hx.done_n', { label, n: ids.length }))"),
    (P, "      toast.error(label + '失败: ' + (e.message || ''))",
        "      toast.error(t('hx.fail', { label, msg: e.message || '' }))"),
    (P, """          批量操作
        </button>""",
        """          {t('hx.batch')}
        </button>"""),
    (P, """              已选{' '}
              <b style={{ color: 'var(--text)' }}>
                {(useAppStore.getState().prodSelIds || []).length}
              </b>{' '}
              项""",
        """              {t('hx.selected_pre')}{' '}
              <b style={{ color: 'var(--text)' }}>
                {(useAppStore.getState().prodSelIds || []).length}
              </b>{' '}
              {t('hx.selected_suf')}"""),
    (P, "                退出批量模式\n              </button>",
        "                {t('hx.exit_batch')}\n              </button>"),
    (P, "                进入批量模式\n              </button>",
        "                {t('hx.enter_batch')}\n              </button>"),
    (P, "              全选/取消\n            </button>",
        "              {t('hx.select_all_toggle')}\n            </button>"),
    (P, "onClick={() => runBatch('active', '启用')}\n            >\n              批量启用",
        "onClick={() => runBatch('active', t('hx.act_enable'))}\n            >\n              {t('hx.batch_enable')}"),
    (P, "onClick={() => runBatch('inactive', '停用')}\n            >\n              批量停用",
        "onClick={() => runBatch('inactive', t('hx.act_disable'))}\n            >\n              {t('hx.batch_disable')}"),
    (P, "onClick={() => runBatch('delete', '删除')}\n            >\n              批量删除",
        "onClick={() => runBatch('delete', t('hx.act_delete'))}\n            >\n              {t('hx.batch_delete')}"),
    (P, """            勾选商品后在此批量操作
            {allActive ? ' · 所选商品已全部启用' : allInactive ? ' · 所选商品已全部停用' : ''}""",
        """            {t('hx.prod_batch_hint')}
            {allActive
              ? ' · ' + t('hx.prod_all_active')
              : allInactive
                ? ' · ' + t('hx.prod_all_inactive')
                : ''}"""),
    (P, '            placeholder="搜索SKU/商品名..."',
        "            placeholder={t('hx.search_prod_ph')}"),
    # ================= HammerOrders =================
    (O, "        toast.success(\n          action === 'permanent-delete' ? `已永久删除 ${ids.length} 条` : `已删除 ${ids.length} 条`,\n        )",
        "        toast.success(\n          action === 'permanent-delete'\n            ? t('hx.purged_n', { n: ids.length })\n            : t('hx.deleted_n', { n: ids.length }),\n        )"),
    (O, "        toast.error('操作失败: ' + (d.error || ''))",
        "        toast.error(t('hx.op_failed', { msg: d.error || '' }))"),
    (O, "      toast.error('操作失败: ' + e.message)",
        "      toast.error(t('hx.op_failed', { msg: e.message }))"),
    (O, "          title: '导出任务已提交',",
        "          title: t('hx.export_submitted'),"),
    (O, "            label: '查看进度 →',",
        "            label: t('hx.view_progress'),"),
    (O, "      else throw new Error(d.error || '提交失败')",
        "      else throw new Error(d.error || t('hx.submit_failed'))"),
    (O, "              批量操作\n            </button>",
        "              {t('hx.batch')}\n            </button>"),
    (O, """                已选{' '}
                <b style={{ color: 'var(--text)' }}>
                  {(useAppStore.getState().orderSelIds || []).length}
                </b>{' '}
                项""",
        """                {t('hx.selected_pre')}{' '}
                <b style={{ color: 'var(--text)' }}>
                  {(useAppStore.getState().orderSelIds || []).length}
                </b>{' '}
                {t('hx.selected_suf')}"""),
    (O, "                  退出批量模式\n                </button>",
        "                  {t('hx.exit_batch')}\n                </button>"),
    (O, "                  进入批量模式\n                </button>",
        "                  {t('hx.enter_batch')}\n                </button>"),
    (O, "                全选/取消\n              </button>",
        "                {t('hx.select_all_toggle')}\n              </button>"),
    (O, "onClick={() => runOrderBatch('delete')}\n              >\n                批量删除",
        "onClick={() => runOrderBatch('delete')}\n              >\n                {t('hx.batch_delete')}"),
    (O, "onClick={() => setConfirmPurge(true)}\n              >\n                永久删除",
        "onClick={() => setConfirmPurge(true)}\n              >\n                {t('hx.purge')}"),
    (O, "              勾选订单后在此批量操作 · 删除为软删（回收站可恢复）",
        "              {t('hx.order_batch_hint')}"),
    (O, """          title="永久删除订单"
          desc={
            '永久删除 ' +
            (useAppStore.getState().orderSelIds || []).length +
            ' 条订单？此操作不可撤销'
          }
          confirmLabel="永久删除\"""".replace('\\"', ''),
        """          title={t('hx.purge_title')}
          desc={t('hx.purge_desc', { n: (useAppStore.getState().orderSelIds || []).length })}
          confirmLabel={t('hx.purge')}"""),
    (O, "              取消全选\n            </button>",
        "              {t('common.deselect')}\n            </button>"),
    (O, "              <span>已显示</span>",
        "              <span>{t('common.visible')}</span>"),
    (O, "                  <span>已隐藏</span>",
        "                  <span>{t('common.hidden')}</span>"),
    (O, '            placeholder="搜索单号/商品/SKU..."',
        "            placeholder={t('hx.search_order_ph')}"),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'hx.batch'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 通用批量操作区(商品/订单/规则同构) ----\n' + block + cur[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cur)
        print(f'  [locale {tag}] +{len(LOCALE)} 键')


def main():
    add_locale()
    misses = []
    for rel, old, new in EDITS:
        p = os.path.join(ROOT, rel)
        with open(p, encoding='utf-8') as f:
            src = f.read()
        if old not in src:
            misses.append((rel, old.splitlines()[0][:70]))
            continue
        if src.count(old) > 1:
            print(f'  [警告] {rel} 多处命中: {old.splitlines()[0][:50]}')
        src = src.replace(old, new, 1)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(src)
    if misses:
        print(f'\n未命中 {len(misses)} 条:')
        for rel, s in misses:
            print(f'  {rel} :: {s}')
        sys.exit(1)
    print(f'\n全部 {len(EDITS)} 条替换完成')


if __name__ == '__main__':
    main()
