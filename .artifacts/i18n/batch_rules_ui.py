LOCALE = {
    "rules.and_short": ("且", "and"),
    "rules.when": ("当", "When"),
    "rules.when_wh": ("当 {wh}", "When {wh}"),
    "rules.and": ("且 {cond}", "and {cond}"),
    "rules.or": ("或 ({cond})", "or ({cond})"),
    "rules.pct_of": ("{field}的{pct}%", "{pct}% of {field}"),
    "rules.active": ("启用", "Active"),
    "rules.inactive": ("停用", "Inactive"),
    "rules.no_title": ("无标题", "Untitled"),

    "rt.title": ("规则测试", "Test rule"),
    "rt.fill_real": ("用真实库存填充", "Fill from real inventory"),
    "rt.sample": ("样本: {sku}", "Sample: {sku}"),
    "rt.avail": ("可用量", "Available"),
    "rt.safety": ("安全线", "Safety line"),
    "rt.safety_hint": (
        "运行时动态注入=日销×补货周期（模拟测试用下方数值）",
        "Injected at runtime as daily sales × lead time (use the value below for simulation)",
    ),
    "rt.transit": ("在途", "In transit"),
    "rt.idle_days": ("滞销天数", "Days since last sale"),
    "rt.order_qty": ("订单数量", "Order qty"),
    "rt.warehouse": ("仓库主体", "Warehouse"),
    "rt.calc_vars": (
        "计算变量/规则参数（与看板判定同源，可调预览）",
        "Computed variables / rule params (same source as the dashboard; tune to preview)",
    ),
    "rt.adj_dos": ("可售天数 Adj-DOS", "Adj-DOS"),
    "rt.buffer": ("缓冲比 Buffer", "Buffer ratio"),
    "rt.health": ("库存健康分", "Health score"),
    "rt.downstream_ds": ("下游日销(件/天)", "Downstream daily sales (units/day)"),
    "rt.can_ship": ("自有可发(在仓+在途+采购在途)", "Own shippable (on hand + transit + PO)"),
    "rt.b2c_days": ("调拨周期(天)", "Transfer lead time (days)"),
    "rt.cover_days": (
        "自有可撑天数(留空=按可用/日销推)",
        "Own days of supply (empty = derive from available / daily sales)",
    ),
    "rt.days_to_expire": ("距过期天数(负=已过期)", "Days to expiry (negative = expired)"),
    "rt.eg": ("例 {v}", "e.g. {v}"),
    "rt.param": ("参数 {k}", "Param {k}"),
    "rt.running": ("测试中...", "Testing..."),
    "rt.run": ("运行测试", "Run test"),
    "rt.close": ("关闭", "Close"),
    "rt.triggered": ("触发告警", "Alert triggered"),
    "rt.not_triggered": ("未触发", "Not triggered"),
    "rt.cond": ("条件: 当 {wh}", "Condition: when {wh}"),
    "rt.calc_left": ("计算: 左侧值 = {v}", "Calc: left = {v}"),
    "rt.calc_right_pct": ("，右侧 = {v}", ", right = {v}"),
    "rt.calc_right": ("，右侧值 = {v}", ", right = {v}"),
    "rt.hit": ("命中", "hit"),
    "rt.miss": ("未命中", "miss"),
    "rt.or_any": ("或(任一)", "or (any)"),
    "rt.failed": ("测试失败: {msg}", "Test failed: {msg}"),

    "cl.title": ("数据清洗导入", "Data cleansing"),
    "cl.list_empty": ("暂无数据", "No data"),
}

REPL = [
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """      else toast.error('测试失败: ' + (d.error || ''))
    } catch (e) {
      toast.error('测试失败: ' + e.message)
    }""",
        """      else toast.error(t('rt.failed', { msg: d.error || '' }))
    } catch (e) {
      toast.error(t('rt.failed', { msg: e.message }))
    }""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """            规则测试
          </div>""",
        """            {t('rt.title')}
          </div>""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                <IconLightning size={12} /> 用真实库存填充""",
        """                <IconLightning size={12} /> {t('rt.fill_real')}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                  样本: {testSampleSku}""",
        """                  {t('rt.sample', { sku: testSampleSku })}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                可用量
                <input
                  type="number"
                  value={testInv.available_qty}""",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                {t('rt.avail')}
                <input
                  type="number"
                  value={testInv.available_qty}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                安全线
                <span className="small muted" style={{ marginLeft: 6, fontSize: 'var(--font-xs)' }}>
                  运行时动态注入=日销×补货周期（模拟测试用下方数值）
                </span>""",
        """                {t('rt.safety')}
                <span className="small muted" style={{ marginLeft: 6, fontSize: 'var(--font-xs)' }}>
                  {t('rt.safety_hint')}
                </span>""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                在途
                <input""",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                {t('rt.transit')}
                <input""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                滞销天数
                <input""",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                {t('rt.idle_days')}
                <input""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                订单数量
                <input
                  type="number"
                  value={testInv.order_quantity}""",
        """              <label style={{ fontSize: 'var(--font-sm)' }}>
                {t('rt.order_qty')}
                <input
                  type="number"
                  value={testInv.order_quantity}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                仓库主体
                <select
                  value={testInv.warehouse_type}
                  onChange={e => setTestInv(p => ({ ...p, warehouse_type: e.target.value }))}
                  style={IS}
                >
                  <option value="">全部</option>
                  <option value="platform">C仓</option>
                  {globalChannel === 'jd' && <option value="platform_b">B仓</option>}
                  <option value="own">自有仓</option>
                </select>""",
        """                {t('rt.warehouse')}
                <select
                  value={testInv.warehouse_type}
                  onChange={e => setTestInv(p => ({ ...p, warehouse_type: e.target.value }))}
                  style={IS}
                >
                  <option value="">{t('rf.wh.all')}</option>
                  <option value="platform">{t('rf.wh.platform')}</option>
                  {globalChannel === 'jd' && (
                    <option value="platform_b">{t('rf.wh.platform_b')}</option>
                  )}
                  <option value="own">{t('rf.wh.own')}</option>
                </select>""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                    计算变量/规则参数（与看板判定同源，可调预览）""",
        """                    {t('rt.calc_vars')}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                          参数 {k}""",
        """                          {t('rt.param', { k })}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                    <IconLoading size={14} /> 测试中...
                  </>
                ) : (
                  '运行测试'
                )}""",
        """                    <IconLoading size={14} /> {t('rt.running')}
                  </>
                ) : (
                  t('rt.run')
                )}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                关闭
              </button>""",
        """                {t('rt.close')}
              </button>""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                      <IconCheck size={15} style={{ verticalAlign: -3 }} /> 触发告警
                    </>
                  ) : (
                    <>
                      <IconClose size={15} style={{ verticalAlign: -3 }} /> 未触发""",
        """                      <IconCheck size={15} style={{ verticalAlign: -3 }} /> {t('rt.triggered')}
                    </>
                  ) : (
                    <>
                      <IconClose size={15} style={{ verticalAlign: -3 }} /> {t('rt.not_triggered')}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                    条件: 当{' '}
                    <b>
                      {testResult.detail.left === 'inv.warehouse_type'
                        ? // 字段级仓储条件(自有仓规则的固定前置)不再叠仓前缀
                          ''
                        : testResult.detail.warehouse
                          ? testResult.detail.warehouse === 'platform_b'
                            ? 'B仓'
                            : testResult.detail.warehouse === 'platform'
                              ? 'C仓'
                              : '自有仓'
                          : '全部'}
                    </b>{' '}""",
        """                    {t('rt.cond', {
                      wh:
                        testResult.detail.left === 'inv.warehouse_type'
                          ? // 字段级仓储条件(自有仓规则的固定前置)不再叠仓前缀
                            ''
                          : whLbl(testResult.detail.warehouse || ''),
                    })}{' '}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                    计算: 左侧值 = {String(testResult.detail.left_value)}
                    {String(testResult.detail.right_value).startsWith('max(') ? (
                      <>，右侧 = {testResult.detail.right_value}</>
                    ) : (
                      <>，右侧值 = {String(testResult.detail.right_value)}</>
                    )}""",
        """                    {t('rt.calc_left', { v: String(testResult.detail.left_value) })}
                    {String(testResult.detail.right_value).startsWith('max(')
                      ? t('rt.calc_right_pct', { v: testResult.detail.right_value })
                      : t('rt.calc_right', { v: String(testResult.detail.right_value) })}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                        <b style={{ color: 'var(--muted)' }}>且</b>{' '}""",
        """                        <b style={{ color: 'var(--muted)' }}>{t('rules.and_short')}</b>{' '}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                          {testResult.detail.and.hit ? '命中' : '未命中'}""",
        """                          {testResult.detail.and.hit ? t('rt.hit') : t('rt.miss')}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                        <b style={{ color: 'var(--muted)' }}>或(任一)</b>""",
        """                        <b style={{ color: 'var(--muted)' }}>{t('rt.or_any')}</b>""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """                              {o.hit ? '命中' : '未命中'}""",
        """                              {o.hit ? t('rt.hit') : t('rt.miss')}""",
    ),
    (
        "frontend/src/pages/rules/TestSheet.tsx",
        """import { fieldLbl, opLbl } from './labels'""",
        """import { fieldLbl, opLbl, whLbl } from './labels'""",
    ),
]
