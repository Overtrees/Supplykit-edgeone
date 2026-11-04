LOCALE = {
    "dev.checking": ("检查中...", "Checking..."),
    "dev.ok": ("正常", "OK"),
    "dev.abnormal": ("异常", "Error"),
    "dev.unreachable": ("无法连接", "Unreachable"),
    "dev.has_data": ("已有数据，请先重置", "Data already exists — reset first"),
    "dev.seed_submitted": ("填充任务已提交", "Seeding task submitted"),
    "dev.view_progress": ("查看进度 →", "View progress →"),
    "dev.seed_failed": ("填充失败: {msg}", "Seeding failed: {msg}"),
    "dev.seed_failed_plain": ("填充失败", "Seeding failed"),
    "dev.reset_submitted": ("重置任务已提交，后台清理中...", "Reset submitted — cleaning up in the background..."),
    "dev.reset_done": ("数据已重置", "Data reset"),
    "dev.reset_failed": ("重置失败: {msg}", "Reset failed: {msg}"),
    "dev.reset_failed_plain": ("重置失败", "Reset failed"),
    "dev.conn": ("连接状态", "Connection"),
    "dev.backend": ("后端服务", "Backend service"),
    "dev.realtime": ("实时连接", "Realtime link"),
    "dev.connected": ("已连接", "Connected"),
    "dev.polling": ("轮询中", "Polling"),
    "dev.disconnected": ("已断开", "Disconnected"),
    "dev.env": ("环境信息", "Environment"),
    "dev.build_date": ("构建日期", "Build date"),
    "dev.same_origin": ("同源", "Same origin"),
    "dev.overview": ("运行概况", "Runtime overview"),
    "dev.total_errors": ("异常总数", "Total errors"),
    "dev.slow_req": ("慢请求", "Slow requests"),
    "dev.today_errors": ("今日异常", "Errors today"),
    "dev.today_slow": ("今日慢请求", "Slow today"),
    "dev.overview_note": (
        "异常/慢请求留痕于 quality_logs(开发者维度), 控制台「日志分析」为平台请求级(24h 保留)",
        "Errors and slow requests are logged to quality_logs (developer scope); the console log analysis is request-level with 24h retention",
    ),
    "dev.log_analysis": ("日志分析", "Log analysis"),
    "dev.log_analysis_sub": ("按日日志文件 · 点击预览当日明细", "Daily log files · tap to preview today's detail"),
    "dev.seed": ("种子数据", "Seed data"),
    "dev.seed_fill": ("一键填充", "Fill demo data"),
    "dev.seed_fill_sub": ("生成 2,000 SKU × 60 天 × 10 万条模拟数据", "Generate 2,000 SKUs × 60 days × 100k simulated rows"),
    "dev.seed_reset": ("一键重置", "Reset all"),
    "dev.seed_reset_sub": ("清空所有数据恢复初始状态", "Clear all data and restore the initial state"),
    "dev.confirm_fill_title": ("生成种子数据？", "Generate seed data?"),
    "dev.confirm_fill_desc": (
        "将生成 160 个商品、60 天订单、9 个仓库库存等模拟数据，覆盖现有数据。",
        "This creates 160 products, 60 days of orders and 9 warehouses of inventory, overwriting existing data.",
    ),
    "dev.confirm_fill_ok": ("生成", "Generate"),
    "dev.confirm_reset_title": ("重置所有数据？", "Reset all data?"),
    "dev.confirm_reset_desc": (
        "此操作不可恢复。将清空订单、库存、商品、规则等全部数据。",
        "This cannot be undone. Orders, inventory, products and rules will all be cleared.",
    ),
    "dev.confirm_reset_ok": ("重置", "Reset"),
    "dev.lv_warning": ("警告", "Warning"),
    "dev.lv_error": ("异常", "Error"),
    "dev.lv_info": ("提示", "Info"),
    "dev.k_api_error": ("接口异常", "API error"),
    "dev.k_slow": ("慢请求", "Slow request"),
    "dev.k_cache": ("缓存降级", "Cache fallback"),
    "dev.k_quiet": ("静默降级", "Silent fallback"),
    "dev.k_frontend": ("前端上报", "Frontend report"),
    "dev.k_js": ("JS 错误", "JS error"),
    "dev.k_rejection": ("Promise 拒绝", "Unhandled rejection"),
    "dev.k_render": ("组件渲染", "Render error"),
    "dev.k_http": ("接口 HTTP", "API HTTP"),
}

REPL = [
    (
        "frontend/src/pages/DevModePage.tsx",
        """const DEV_LABEL: Record<string, string> = {
  api_error: '接口异常',
  slow_request: '慢请求',
  cache_error: '缓存降级',
  quiet_error: '静默降级',
  frontend_error: '前端上报',
  window_error: 'JS 错误',
  unhandled_rejection: 'Promise 拒绝',
  component_error: '组件渲染',
  api_http_error: '接口 HTTP',
}
const LEVEL_LABEL: Record<string, string> = { warning: '警告', error: '异常', info: '提示' }""",
        """// 词表化: 存键不存文案, 渲染时 t() 解析(切语言立即生效)
const DEV_LABEL: Record<string, string> = {
  api_error: 'dev.k_api_error',
  slow_request: 'dev.k_slow',
  cache_error: 'dev.k_cache',
  quiet_error: 'dev.k_quiet',
  frontend_error: 'dev.k_frontend',
  window_error: 'dev.k_js',
  unhandled_rejection: 'dev.k_rejection',
  component_error: 'dev.k_render',
  api_http_error: 'dev.k_http',
}
const LEVEL_LABEL: Record<string, string> = {
  warning: 'dev.lv_warning',
  error: 'dev.lv_error',
  info: 'dev.lv_info',
}""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """  const [status, setStatus] = useState('检查中...')""",
        """  const [status, setStatus] = useState(() => t('dev.checking'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      setStatus(d.ok ? '正常' : '异常')""",
        """      setStatus(d.ok ? t('dev.ok') : t('dev.abnormal'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      setStatus('无法连接')""",
        """      setStatus(t('dev.unreachable'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """          toast.error('已有数据，请先重置')""",
        """          toast.error(t('dev.has_data'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """          title: '填充任务已提交',
          duration: 6000,
          action: {
            label: '查看进度 →',""",
        """          title: t('dev.seed_submitted'),
          duration: 6000,
          action: {
            label: t('dev.view_progress'),""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """        toast.error('填充失败: ' + (d.error || ''))
        setSeeding(false)
      }
    } catch {
      toast.error('填充失败')""",
        """        toast.error(t('dev.seed_failed', { msg: d.error || '' }))
        setSeeding(false)
      }
    } catch {
      toast.error(t('dev.seed_failed_plain'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """        toast.success('重置任务已提交，后台清理中...')""",
        """        toast.success(t('dev.reset_submitted'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """              toast.success('数据已重置')""",
        """              toast.success(t('dev.reset_done'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """        toast.error('重置失败: ' + (d.error || ''))
        setResetting(false)
      }
    } catch {
      toast.error('重置失败')""",
        """        toast.error(t('dev.reset_failed', { msg: d.error || '' }))
        setResetting(false)
      }
    } catch {
      toast.error(t('dev.reset_failed_plain'))""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      <Group title="连接状态">
        <Row
          label="后端服务"
          value={status}
          sub={`${ping}ms · ${lastCheck}`}
          onClick={checkConnection}
          loading={refreshing}
        />
        <LastRow
          label="实时连接"
          value={wsStatus === 'connected' ? '已连接' : wsStatus === 'polling' ? '轮询中' : '已断开'}
        />
      </Group>""",
        """      <Group title={t('dev.conn')}>
        <Row
          label={t('dev.backend')}
          value={status}
          sub={`${ping}ms · ${lastCheck}`}
          onClick={checkConnection}
          loading={refreshing}
        />
        <LastRow
          label={t('dev.realtime')}
          value={
            wsStatus === 'connected'
              ? t('dev.connected')
              : wsStatus === 'polling'
                ? t('dev.polling')
                : t('dev.disconnected')
          }
        />
      </Group>""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      <Group title="环境信息">
        <Row label="构建日期" value={BUILD || '-'} />
        <Row label="API" value={API || '同源'} />""",
        """      <Group title={t('dev.env')}>
        <Row label={t('dev.build_date')} value={BUILD || '-'} />
        <Row label="API" value={API || t('dev.same_origin')} />""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      <Group title="运行概况">
        <StatsRow
          items={[
            { n: mon ? mon.totals?.error || 0 : '-', c: '异常总数', color: 'var(--danger)' },
            { n: mon ? mon.totals?.slow || 0 : '-', c: '慢请求', color: 'var(--warning)' },
            { n: mon ? mon.today?.error || 0 : '-', c: '今日异常', color: 'var(--text)' },
            { n: mon ? mon.today?.slow || 0 : '-', c: '今日慢请求', color: 'var(--text)' },
          ]}
        />""",
        """      <Group title={t('dev.overview')}>
        <StatsRow
          items={[
            {
              n: mon ? mon.totals?.error || 0 : '-',
              c: t('dev.total_errors'),
              color: 'var(--danger)',
            },
            { n: mon ? mon.totals?.slow || 0 : '-', c: t('dev.slow_req'), color: 'var(--warning)' },
            { n: mon ? mon.today?.error || 0 : '-', c: t('dev.today_errors'), color: 'var(--text)' },
            { n: mon ? mon.today?.slow || 0 : '-', c: t('dev.today_slow'), color: 'var(--text)' },
          ]}
        />""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """          异常/慢请求留痕于 quality_logs(开发者维度), 控制台「日志分析」为平台请求级(24h 保留)""",
        """          {t('dev.overview_note')}""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      <Group title="日志分析">
        <LastRow
          label="日志分析"
          sub="按日日志文件 · 点击预览当日明细\"""",
        """      <Group title={t('dev.log_analysis')}>
        <LastRow
          label={t('dev.log_analysis')}
          sub={t('dev.log_analysis_sub')}""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """      <Group title="种子数据">
        <Row
          label="一键填充"
          sub="生成 2,000 SKU × 60 天 × 10 万条模拟数据\"""",
        """      <Group title={t('dev.seed')}>
        <Row
          label={t('dev.seed_fill')}
          sub={t('dev.seed_fill_sub')}""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """          label="一键重置"
          sub="清空所有数据恢复初始状态\"""",
        """          label={t('dev.seed_reset')}
          sub={t('dev.seed_reset_sub')}""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """                日志分析
              </div>""",
        """                {t('dev.log_analysis')}
              </div>""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """          title="生成种子数据？"
          desc="将生成 160 个商品、60 天订单、9 个仓库库存等模拟数据，覆盖现有数据。"
          confirmLabel="生成\"""",
        """          title={t('dev.confirm_fill_title')}
          desc={t('dev.confirm_fill_desc')}
          confirmLabel={t('dev.confirm_fill_ok')}""",
    ),
    (
        "frontend/src/pages/DevModePage.tsx",
        """          title="重置所有数据？"
          desc="此操作不可恢复。将清空订单、库存、商品、规则等全部数据。"
          confirmLabel="重置\"""",
        """          title={t('dev.confirm_reset_title')}
          desc={t('dev.confirm_reset_desc')}
          confirmLabel={t('dev.confirm_reset_ok')}""",
    ),
]
