LOCALE = {
    "nav.orders": ("订单明细", "Orders"),
    "oms.title": ("订单", "Orders"),
    "oms.search_tag": ('搜索 "{q}"', 'Search "{q}"'),
    "oms.no_data": ("没有数据", "No data"),
    "oms.to_history": ("查 1 年前 →", "Older than a year →"),
    "oms.go_import": ("去导入数据 →", "Go to import →"),
    "oms.import_hint": ("通过清洗导入订单数据", "Import order data from Data Import"),
    "oms.try_other_kw": ("换个关键词试试", "Try another keyword"),
    "oms.batch_purged": ("已永久删除 {n} 条", "Permanently deleted {n}"),
    "oms.batch_deleted": ("已删除 {n} 条", "Deleted {n}"),
    "oms.action_failed": ("操作失败: {msg}", "Action failed: {msg}"),
    "oms.confirm_del_title": ("删除订单", "Delete order"),
    "oms.confirm_del_desc": ("删除后不可恢复", "Deleted orders cannot be restored from here"),
    "oms.page_of": ("第 {a}/{b} 页", "Page {a} of {b}"),
    "oms.jump_to": ("跳至", "Go to"),
    "oms.page_suffix": ("页", "page"),
    "oms.loaded": (
        "已加载 {a}/{b} 条 · 显示 {c}/{d} 列",
        "Loaded {a}/{b} · {c}/{d} columns",
    ),
    "common.delete_btn": ("删除", "Delete"),
}

REPL = [
    # 列定义只存 id —— 标签走 colLabel(col.id)
    (
        "frontend/src/pages/OrdersPage.tsx",
        """const COLS = [
  { id: 'date', label: '下单日期' },
  { id: 'brand', label: '品牌' },
  { id: 'order_no', label: '订单号' },
  { id: 'barcode', label: '69码' },
  { id: 'store', label: '店铺' },
  { id: 'warehouse', label: '仓库' },
  { id: 'product', label: '商品' },
  { id: 'quantity', label: '数量' },
  { id: 'unit_price', label: '单价' },
  { id: 'amount', label: '金额' },
  { id: 'status', label: '状态' },
  { id: 'paid_at', label: '入库日期' },
]""",
        """// 列只存 id: 表头渲染走 colLabel(col.id), 列选择面板同源(configs.ts 的 ORDER_COLS)
const COLS = [
  { id: 'date' },
  { id: 'brand' },
  { id: 'order_no' },
  { id: 'barcode' },
  { id: 'store' },
  { id: 'warehouse' },
  { id: 'product' },
  { id: 'quantity' },
  { id: 'unit_price' },
  { id: 'amount' },
  { id: 'status' },
  { id: 'paid_at' },
]""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """                    return col ? <th key={col.id}>{col.label}</th> : null""",
        """                    return col ? <th key={col.id}>{colLabel(col.id)}</th> : null""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """        toast.success(
          action === 'permanent-delete' ? `已永久删除 ${ids.length} 条` : `已删除 ${ids.length} 条`,
        )
      } else {
        toast.error('操作失败: ' + (d.error || ''))
      }
    } catch (e) {
      toast.error('操作失败: ' + e.message)""",
        """        toast.success(
          action === 'permanent-delete'
            ? t('oms.batch_purged', { n: ids.length })
            : t('oms.batch_deleted', { n: ids.length }),
        )
      } else {
        toast.error(t('oms.action_failed', { msg: d.error || '' }))
      }
    } catch (e) {
      toast.error(t('oms.action_failed', { msg: e.message }))""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """          <span className="small muted">搜索 "{s}"</span>""",
        """          <span className="small muted">{t('oms.search_tag', { q: s })}</span>""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """          订单 {/* 2026-10-28 全站统一: 已加载 N/M 条 · 显示 x/y 列(同一行并排) */}
          <span className="small muted" style={{ fontWeight: 400, fontSize: 'var(--font-xs)' }}>
            已加载 {Math.min(orders.length, orderTotal || orders.length)}/
            {orderTotal || orders.length} 条 · 显示 {visCols.length}/{COLS.length} 列""",
        """          {t('nav.orders')} {/* 2026-10-28 全站统一: 已加载 N/M 条 · 显示 x/y 列(同一行并排) */}
          <span className="small muted" style={{ fontWeight: 400, fontSize: 'var(--font-xs)' }}>
            {t('oms.loaded', {
              a: Math.min(orders.length, orderTotal || orders.length),
              b: orderTotal || orders.length,
              c: visCols.length,
              d: COLS.length,
            })}""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """            查 1 年前 →""",
        """            {t('oms.to_history')}""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """              desc={s ? '换个关键词试试' : '通过清洗导入订单数据'}""",
        """              desc={s ? t('oms.try_other_kw') : t('oms.import_hint')}""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """                    去导入数据 →""",
        """                    {t('oms.go_import')}""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """        title="删除订单"
        desc="删除后不可恢复"
        confirmLabel="删除\"""",
        """        title={t('oms.confirm_del_title')}
        desc={t('oms.confirm_del_desc')}
        confirmLabel={t('common.delete_btn')}""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """            第 {orderPage}/{totalPages} 页""",
        """            {t('oms.page_of', { a: orderPage, b: totalPages })}""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """            <span className="small muted">跳至</span>""",
        """            <span className="small muted">{t('oms.jump_to')}</span>""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """            <span className="small muted">页</span>""",
        """            <span className="small muted">{t('oms.page_suffix')}</span>""",
    ),
    (
        "frontend/src/pages/OrdersPage.tsx",
        """import { t } from '../locale'""",
        """import { colLabel } from '../components/colLabels'
import { t } from '../locale'""",
    ),
]
