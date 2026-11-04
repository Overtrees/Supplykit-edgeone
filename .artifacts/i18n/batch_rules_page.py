LOCALE = {
    "rp.guest_readonly": ("访客模式仅可查看，不可修改数据", "Guest mode is read-only"),
    "rp.save_failed": ("保存失败: {msg}", "Save failed: {msg}"),
    "rp.load_failed": ("加载失败", "Load failed"),
    "rp.bad_payload": ("返回数据格式异常", "Unexpected response format"),
    "rp.new_rule": ("新建规则", "New rule"),
    "rp.save_rule": ("保存规则", "Save rule"),
    "rp.saved_recalc": ("已保存，正在重算告警", "Saved — recalculating alerts"),
    "rp.saved": ("已保存", "Saved"),
    "rp.recalc_op": ("规则重算", "Rule recalculation"),
    "rp.warn_order_event": (
        "条件引用 order.* 但事件非「订单创建」，该规则可能永不触发",
        "The condition references order.* but the event is not \u201cOrder created\u201d — this rule may never fire",
    ),
    "rp.warn_param_missing": (
        "条件引用参数 {keys} 但未填写，评估时将按 0 处理",
        "The condition references parameter {keys} without a value — it will be treated as 0 when evaluated",
    ),
    "rp.restore_rule": ("恢复规则", "Restore rule"),
    "rp.restored": ("规则已恢复，正在重算告警", "Rule restored — recalculating alerts"),
    "rp.param_b_to_c": ("B→C调拨(天)", "B→C transfer (days)"),
    "rp.param_b_to_c_h": ("京东B仓→C仓调拨时效", "JD B-warehouse → C-warehouse lead time"),
    "rp.param_c_safety": ("C仓缓冲(天)", "C-warehouse buffer (days)"),
    "rp.param_c_safety_h": ("C仓安全储备", "C-warehouse safety reserve"),
    "rp.param_ship_to_b": ("自有仓→B仓时效(天)", "Own WH → B warehouse (days)"),
    "rp.param_safety_mult": ("安全库存天数", "Safety stock days"),
    "rp.param_turnover_15": ("仓储费阈值(天)", "Storage fee threshold (days)"),
    "rp.param_turnover_90": ("周转考核红线(天)", "Turnover limit (days)"),
    "rp.param_lead_time": ("前置期(天)", "Lead time (days)"),
    "rp.param_purchase_lead": ("采购前置(天)", "Purchase lead time (days)"),
    "rp.param_purchase_safety": ("采购安全库存(天)", "Purchase safety stock (days)"),
    "rp.param_moq": ("MOQ最小起订(件)", "MOQ (units)"),
    "rp.param_max_turnover": ("目标周转(天)", "Target turnover (days)"),
    "rp.debug_title": (
        "🔍 规则页调试追踪（关闭: localStorage 设 c_debug_rules=0）",
        "🔍 Rules page debug trace (disable: localStorage c_debug_rules=0)",
    ),
    "rp.debug_state": ("当前 rules state: {n} 条", "rules state: {n}"),
    "rp.debug_filtered": ("filteredRules: {n} 条", "filteredRules: {n}"),
    "rp.debug_channel": ("渠道: {v}", "Channel: {v}"),
    "rp.debug_tab": ("tab: {v}", "tab: {v}"),
    "rp.debug_clear": ("清空日志", "Clear log"),
    "rp.debug_empty": ("暂无操作日志", "No operation log yet"),

    "slow.hint": (
        "按品类分组自定义滞销判定：每个品类设「滞销线(天)」和「临期线(月)」，品类名单匹配商品的分类字段",
        "Define slow-moving rules per category: set a slow-moving line (days) and a near-expiry line (months) for each; the category list matches the product's category field",
    ),
    "slow.name_ph": ("品类名(如 食品)", "Category name (e.g. Food)"),
    "slow.slow_days": ("滞销线(天)", "Slow-moving line (days)"),
    "slow.observe_days": ("观察线(天)", "Watch line (days)"),
    "slow.auto": ("留空自动", "empty = auto"),
    "slow.shelf_months": ("临期线(月)", "Near-expiry line (months)"),
    "slow.cats_label": (
        "品类名单（逗号分隔，匹配商品\u201c分类\u201d字段）",
        "Category list (comma separated, matches the product \u201ccategory\u201d field)",
    ),
    "slow.cats_ph": ("酱油,薯片,糖果...", "Soy sauce, chips, candy..."),
    "slow.transit": ("物流在途(天)", "Transit time (days)"),
    "slow.transit_hint": (
        "库存出库到客户/入仓的运输天数，默认 3（用于效期预警：已消耗 + 在途 &gt; 1/3 标临近）",
        "Shipping days from warehouse outbound to customer or inbound. Default 3 (used by expiry alerts: consumed + in transit &gt; 1/3 marks near-expiry)",
    ),
    "slow.fund": ("资金占用线(¥)", "Capital tie-up line (¥)"),
    "slow.fund_hint": (
        "滞销品资金占用(库存×单价)超此线 → 升级「处置」等级(默认 10000)",
        "Slow-mover capital tie-up (stock × unit price) above this line escalates to \u201cdispose\u201d (default 10000)",
    ),
    "slow.new_cat": ("新品类", "New category"),
    "slow.add_cat": ("+ 添加品类", "+ Add category"),
}

REPL = [
    (
        "frontend/src/pages/RulesPage.tsx",
        """    else if (e?.response?.status === 403) toast.error('访客模式仅可查看，不可修改数据')
    else toast.error('保存失败: ' + (e.message || ''))""",
        """    else if (e?.response?.status === 403) toast.error(t('rp.guest_readonly'))
    else toast.error(t('rp.save_failed', { msg: e.message || '' }))""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """      setRulesErr(Array.isArray(rawData) ? '' : r.status !== 200 ? '加载失败' : '返回数据格式异常')""",
        """      setRulesErr(
        Array.isArray(rawData)
          ? ''
          : r.status !== 200
            ? t('rp.load_failed')
            : t('rp.bad_payload'),
      )""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """      setRulesErr('加载失败')""",
        """      setRulesErr(t('rp.load_failed'))""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """        label: isNew ? '新建规则' : '保存规则',""",
        """        label: isNew ? t('rp.new_rule') : t('rp.save_rule'),""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """        success: r => (r.tid ? '已保存，正在重算告警' : '已保存'),""",
        """        success: r => (r.tid ? t('rp.saved_recalc') : t('rp.saved')),""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """                label: '规则重算',""",
        """                label: t('rp.recalc_op'),""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """        toast.warning('条件引用 order.* 但事件非「订单创建」，该规则可能永不触发')""",
        """        toast.warning(t('rp.warn_order_event'))""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """          toast.warning('条件引用参数 ' + _miss.join('/') + ' 但未填写，评估时将按 0 处理')""",
        """          toast.warning(t('rp.warn_param_missing', { keys: _miss.join('/') }))""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """      toast.error('保存失败: ' + e.message)""",
        """      toast.error(t('rp.save_failed', { msg: e.message }))""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """            label: '恢复规则',""",
        """            label: t('rp.restore_rule'),""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """            success: '规则已恢复，正在重算告警',""",
        """            success: t('rp.restored'),""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """        { k: 'b_to_c_days', l: 'B→C调拨(天)', h: '京东B仓→C仓调拨时效' },
        { k: 'c_safety_days', l: 'C仓缓冲(天)', h: 'C仓安全储备' },""",
        """        { k: 'b_to_c_days', l: 'rp.param_b_to_c', h: 'rp.param_b_to_c_h' },
        { k: 'c_safety_days', l: 'rp.param_c_safety', h: 'rp.param_c_safety_h' },""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """        { k: 'ship_to_b_days', l: '自有仓→B仓时效(天)' },
        { k: 'safety_multiplier', l: '安全库存天数' },
        { k: 'turnover_warning_15', l: '仓储费阈值(天)' },
        { k: 'turnover_warning_90', l: '周转考核红线(天)' },""",
        """        { k: 'ship_to_b_days', l: 'rp.param_ship_to_b' },
        { k: 'safety_multiplier', l: 'rp.param_safety_mult' },
        { k: 'turnover_warning_15', l: 'rp.param_turnover_15' },
        { k: 'turnover_warning_90', l: 'rp.param_turnover_90' },""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """        { k: 'lead_time_days', l: '前置期(天)' },
        { k: 'safety_multiplier', l: '安全库存天数' },
        { k: 'turnover_warning_90', l: '周转考核红线(天)' },""",
        """        { k: 'lead_time_days', l: 'rp.param_lead_time' },
        { k: 'safety_multiplier', l: 'rp.param_safety_mult' },
        { k: 'turnover_warning_90', l: 'rp.param_turnover_90' },""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """    { k: 'purchase_lead_days', l: '采购前置(天)' },
    { k: 'purchase_safety_days', l: '采购安全库存(天)' },
    { k: 'moq', l: 'MOQ最小起订(件)' },
    { k: 'max_turnover_days', l: '目标周转(天)' },""",
        """    { k: 'purchase_lead_days', l: 'rp.param_purchase_lead' },
    { k: 'purchase_safety_days', l: 'rp.param_purchase_safety' },
    { k: 'moq', l: 'rp.param_moq' },
    { k: 'max_turnover_days', l: 'rp.param_max_turnover' },""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """            🔍 规则页调试追踪（关闭: localStorage 设 c_debug_rules=0）
          </div>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>
            当前 rules state: <b>{rules.length}</b> 条 | filteredRules:{' '}
            <b>{filteredRules.length}</b> 条 | 渠道: <b>{globalChannel}</b> | tab: <b>{tab}</b>
          </div>""",
        """            {t('rp.debug_title')}
          </div>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>
            {t('rp.debug_state', { n: rules.length })} |{' '}
            {t('rp.debug_filtered', { n: filteredRules.length })} |{' '}
            {t('rp.debug_channel', { v: globalChannel })} | {t('rp.debug_tab', { v: tab })}
          </div>""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """            清空日志
          </button>""",
        """            {t('rp.debug_clear')}
          </button>""",
    ),
    (
        "frontend/src/pages/RulesPage.tsx",
        """            <div style={{ color: 'var(--muted2)' }}>暂无操作日志</div>""",
        """            <div style={{ color: 'var(--muted2)' }}>{t('rp.debug_empty')}</div>""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """        按品类分组自定义滞销判定：每个品类设「滞销线(天)」和「临期线(月)」，品类名单匹配商品的分类字段""",
        """        {t('slow.hint')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              placeholder="品类名(如 食品)\"""",
        """              placeholder={t('slow.name_ph')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              滞销线(天)""",
        """              {t('slow.slow_days')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              观察线(天) <span style={{ color: 'var(--muted2)' }}>留空自动</span>""",
        """              {t('slow.observe_days')}{' '}
              <span style={{ color: 'var(--muted2)' }}>{t('slow.auto')}</span>""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              临期线(月)""",
        """              {t('slow.shelf_months')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """            品类名单（逗号分隔，匹配商品"分类"字段）""",
        """            {t('slow.cats_label')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              placeholder="酱油,薯片,糖果...\"""",
        """              placeholder={t('slow.cats_ph')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """          物流在途(天)""",
        """          {t('slow.transit')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """            库存出库到客户/入仓的运输天数，默认 3（用于效期预警：已消耗 + 在途 &gt; 1/3 标临近）""",
        """            {t('slow.transit_hint')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """          资金占用线(¥)""",
        """          {t('slow.fund')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """            滞销品资金占用(库存×单价)超此线 → 升级「处置」等级(默认 10000)""",
        """            {t('slow.fund_hint')}""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """                name: '新品类',""",
        """                name: t('slow.new_cat'),""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """          + 添加品类
        </button>""",
        """          {t('slow.add_cat')}
        </button>""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              toast.success('已保存')""",
        """              toast.success(t('rp.saved'))""",
    ),
    (
        "frontend/src/pages/rules/SlowPanel.tsx",
        """              <IconLoading size={14} /> 保存中...
            </>
          ) : (
            <>
              <IconSave size={14} /> 保存""",
        """              <IconLoading size={14} /> {t('rfm.saving')}
            </>
          ) : (
            <>
              <IconSave size={14} /> {t('common.save')}""",
    ),
]
