LOCALE = {
    "task.page_title": ("任务管理", "Tasks"),
    "task.title": ("任务", "Task"),
    "task.export": ("导出", "Export"),
    "task.import": ("导入", "Import"),
    "task.eval": ("评估联动", "Evaluation run"),
    "task.subtitle": ("{ch} · 异步任务", "{ch} · async tasks"),
    "task.ch_jd": ("京东", "JD"),
    "task.ch_other": ("其他渠道", "Other channels"),
    "task.load_failed": ("加载失败", "Load failed"),
    "task.load_failed_msg": ("加载失败: {msg}", "Load failed: {msg}"),
    "task.empty": ("暂无任务", "No tasks yet"),
    "task.empty_desc": ("导入数据或导出文件后，处理进度会在这里显示", "Import or export something and its progress shows up here"),
    "task.go_import": ("去导入数据 →", "Go to import →"),
    "task.show_more": ("显示更多（还有 {n} 条已完成）↓", "Show more ({n} more finished) ↓"),
    "task.done_toast": ("任务完成: {what} ✓", "Task finished: {what} ✓"),
    "task.failed_toast": ("任务失败: {msg}", "Task failed: {msg}"),
    "task.session_expired": ("登录已失效，请重新登录", "Session expired — please sign in again"),
    "task.db_busy": ("数据正在处理中...", "Data is being processed..."),
    "task.net_retry": ("网络异常，自动重试中...", "Network problem — retrying automatically..."),
    "task.chunk_failed": ("分片下载失败", "Chunk download failed"),
    "task.dl_failed": ("导出下载失败({code})", "Export download failed ({code})"),
    "task.type_eval": ("评估联动", "Evaluation run"),
    "task.type_seed": ("种子数据填充", "Seed data fill"),
    "task.type_cleansing": ("数据清洗导入", "Data cleansing import"),
    "task.type_export": ("导出任务", "Export task"),
    "task.type_reset": ("数据重置", "Data reset"),
    "task.type_unknown": ("任务", "Task"),
    "task.st_pending": ("等待中", "Pending"),
    "task.st_running": ("进行中", "Running"),
    "task.st_done": ("已完成", "Done"),
    "task.st_error": ("失败", "Failed"),
    "task.what_seed": ("种子填充", "seed fill"),
    "task.what_cleansing": ("清洗导入", "cleansing import"),
    "task.what_eval": ("评估联动", "evaluation run"),
    "task.what_export": ("导出", "export"),
    "task.exp_orders": ("订单明细", "Order detail"),
    "task.exp_inventory": ("库存明细", "Inventory detail"),
    "task.exp_slow": ("滞销明细", "Slow-mover detail"),
    "task.exp_purchase": ("补货建议", "Replenishment suggestions"),
    "task.exp_purchase_suggestions": ("采购建议", "Purchase suggestions"),
    "task.imp_order": ("订单表", "Order table"),
    "task.imp_inventory": ("库存表(自有)", "Inventory table (own)"),
    "task.imp_platform_inv": ("库存表(平台)", "Inventory table (platform)"),
    "task.imp_inventory_b": ("库存表(B仓)", "Inventory table (B warehouse)"),
    "task.imp_inbound": ("入库记录表", "Inbound records table"),
    "task.imp_outbound": ("出库记录表", "Outbound records table"),
    "task.imp_product": ("商品表", "Product table"),

    "tc.just_now": ("刚刚", "just now"),
    "tc.mins_ago": ("{n} 分钟前", "{n} min ago"),
    "tc.hours_ago": ("{n} 小时前", "{n} h ago"),
    "tc.backend_run": ("后端推进", "backend"),
    "tc.frontend_poll": ("前端轮询", "frontend poll"),
    "tc.done": ("完成", "Done"),
    "tc.failed": ("失败", "Failed"),
    "tc.running": ("进行中", "Running"),
    "tc.waiting": ("等待", "Waiting"),
    "tc.step": ("步骤 {n}", "Step {n}"),
    "tc.processing": ("处理中", "Processing"),
    "tc.rows": ("共 {n} 行", "{n} rows"),
    "tc.success": ("成功 {n}", "OK {n}"),
    "tc.failed_n": (" / 失败 {n}", " / failed {n}"),
    "tc.elapsed": ("耗时 {n}s", "{n}s"),
    "tc.window": (
        "窗口 {seg} · 已评估 {cur}/{total} SKU",
        "Window {seg} · evaluated {cur}/{total} SKU",
    ),
    "tc.eval_rules": ("评估 {n} SKU", "Evaluated {n} SKU"),
    "tc.eval_inv": ("· 联动 {n} 库存", "· {n} inventory rows updated"),
    "tc.task_failed": ("任务失败", "Task failed"),
    "tc.downloading": ("下载中…", "Downloading…"),
    "tc.download": ("下载", "Download"),
}

REPL = [
    (
        "frontend/src/pages/TaskPage.tsx",
        """const EXPORT_TYPE_NAME = {
  orders: '订单明细',
  inventory: '库存明细',
  slow: '滞销明细',
  purchase: '补货建议',
  purchase_suggestions: '采购建议',
}
const CLEAN_TARGET_NAME = {
  order: '订单表',
  inventory: '库存表(自有)',
  platform_inv: '库存表(平台)',
  inventory_b: '库存表(B仓)',
  inbound: '入库记录表',
  outbound: '出库记录表',
  product: '商品表',
}
const TYPE_LABEL = {
  cleansing_eval: { label: '评估联动', Icon: IconRefresh },
  seed: { label: '种子数据填充', Icon: IconRefresh },
  cleansing: { label: '数据清洗导入', Icon: IconBroom },
  export: { label: '导出任务', Icon: IconExport },
  reset: { label: '数据重置', Icon: IconUndo },
}
const STATUS_LABEL = { pending: '等待中', running: '进行中', done: '已完成', error: '失败' }""",
        """// 词表化: 存键不存文案(渲染时 t() 解析, 切语言立即生效)
const EXPORT_TYPE_NAME = {
  orders: 'task.exp_orders',
  inventory: 'task.exp_inventory',
  slow: 'task.exp_slow',
  purchase: 'task.exp_purchase',
  purchase_suggestions: 'task.exp_purchase_suggestions',
}
const CLEAN_TARGET_NAME = {
  order: 'task.imp_order',
  inventory: 'task.imp_inventory',
  platform_inv: 'task.imp_platform_inv',
  inventory_b: 'task.imp_inventory_b',
  inbound: 'task.imp_inbound',
  outbound: 'task.imp_outbound',
  product: 'task.imp_product',
}
const TYPE_LABEL = {
  cleansing_eval: { label: 'task.type_eval', Icon: IconRefresh },
  seed: { label: 'task.type_seed', Icon: IconRefresh },
  cleansing: { label: 'task.type_cleansing', Icon: IconBroom },
  export: { label: 'task.type_export', Icon: IconExport },
  reset: { label: 'task.type_reset', Icon: IconUndo },
}
const STATUS_LABEL = {
  pending: 'task.st_pending',
  running: 'task.st_running',
  done: 'task.st_done',
  error: 'task.st_error',
}""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """              toast.success(
                '任务完成: ' +
                  (t.task_type === 'seed'
                    ? '种子填充'
                    : t.task_type === 'clean'
                      ? '清洗导入'
                      : t.task_type === 'cleansing_eval'
                        ? '评估联动'
                        : t.task_type === 'export'
                          ? '导出'
                          : t.task_type) +
                  ' ✓',
              )""",
        """              toast.success(
                t('task.done_toast', {
                  what:
                    t.task_type === 'seed'
                      ? t('task.what_seed')
                      : t.task_type === 'clean'
                        ? t('task.what_cleansing')
                        : t.task_type === 'cleansing_eval'
                          ? t('task.what_eval')
                          : t.task_type === 'export'
                            ? t('task.what_export')
                            : t.task_type,
                }),
              )""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """              toast.error('任务失败: ' + String(t.result || '').slice(0, 60))""",
        """              toast.error(t('task.failed_toast', { msg: String(t.result || '').slice(0, 60) }))""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """      } else if (r.status === 401) setLoadErr('登录已失效，请重新登录')
      else if (d.error === 'database_busy') {
        if (tasks.length === 0) setLoadErr('数据正在处理中...')
      } else setLoadErr('加载失败: ' + (d.error || 'HTTP ' + r.status))
    } catch (e) {
      if (tasks.length === 0) setLoadErr('网络异常，自动重试中...')
    }""",
        """      } else if (r.status === 401) setLoadErr(t('task.session_expired'))
      else if (d.error === 'database_busy') {
        if (tasks.length === 0) setLoadErr(t('task.db_busy'))
      } else setLoadErr(t('task.load_failed_msg', { msg: d.error || 'HTTP ' + r.status }))
    } catch (e) {
      if (tasks.length === 0) setLoadErr(t('task.net_retry'))
    }""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """          if (!r.ok) throw new Error('分片下载失败')""",
        """          if (!r.ok) throw new Error(t('task.chunk_failed'))""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """        if (!dl.ok) throw new Error('导出下载失败(' + dl.status + ')')""",
        """        if (!dl.ok) throw new Error(t('task.dl_failed', { code: dl.status }))""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """      <div className="section-title">任务管理</div>
      <div className="small muted" style={{ padding: '0 0 8px 0', fontSize: 12 }}>
        {channel === 'jd' ? '京东' : '其他渠道'} · 异步任务
      </div>""",
        """      <div className="section-title">{t('task.page_title')}</div>
      <div className="small muted" style={{ padding: '0 0 8px 0', fontSize: 12 }}>
        {t('task.subtitle', { ch: channel === 'jd' ? t('task.ch_jd') : t('task.ch_other') })}
      </div>""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """              <ErrorRetry error={'加载失败'} onRetry={loadTasks} />""",
        """              <ErrorRetry error={t('task.load_failed')} onRetry={loadTasks} />""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """                title="暂无任务"
                desc="导入数据或导出文件后，处理进度会在这里显示\"""",
        """                title={t('task.empty')}
                desc={t('task.empty_desc')}""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """                    去导入数据 →
                  </button>""",
        """                    {t('task.go_import')}
                  </button>""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """                  const meta = TYPE_LABEL[type] || { label: '任务', Icon: IconClipboard }""",
        """                  const meta = TYPE_LABEL[type] || { label: 'task.type_unknown', Icon: IconClipboard }""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """                      ? EXPORT_TYPE_NAME[result?.result?.type || result?.type] || '导出'
                      : task.task_type === 'cleansing'
                        ? CLEAN_TARGET_NAME[result?.result?.target || result?.target] || '导入'
                        : task.task_type === 'cleansing_eval'
                          ? '评估联动'
                          : task.task_id.slice(0, 22)""",
        """                      ? t(EXPORT_TYPE_NAME[result?.result?.type || result?.type] || 'task.export')
                      : task.task_type === 'cleansing'
                        ? t(
                            CLEAN_TARGET_NAME[result?.result?.target || result?.target] ||
                              'task.import',
                          )
                        : task.task_type === 'cleansing_eval'
                          ? t('task.eval')
                          : task.task_id.slice(0, 22)""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """                      label={meta.label}""",
        """                      label={t(meta.label)}""",
    ),
    (
        "frontend/src/pages/TaskPage.tsx",
        """                    显示更多（还有 {_finished.length - _shown.length} 条已完成）↓""",
        """                    {t('task.show_more', { n: _finished.length - _shown.length })}""",
    ),
]
