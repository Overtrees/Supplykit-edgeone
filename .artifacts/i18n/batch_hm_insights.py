LOCALE = {
    "common.suggestions": ("建议", "Suggestions"),
    "hm.export_submitted": ("导出任务已提交", "Export task submitted"),
    "hm.view_progress": ("查看进度 →", "View progress →"),
    "hm.submit_failed": ("提交失败", "Submit failed"),
    "hm.export_failed": ("导出失败: {msg}", "Export failed: {msg}"),
    "hm.dp_mark": ("标记已处理", "Mark as handled"),
    "hm.dp_mark_desc": ("确认库存已清理或不再处理", "Confirm the stock is cleared or no longer needed"),
    "hm.dp_return": ("退货供应商", "Return to supplier"),
    "hm.dp_return_desc": ("向供应商退货, 回收占用资金", "Return to the supplier and recover tied-up cash"),
    "hm.dp_clearance": ("清仓甩卖", "Clearance sale"),
    "hm.dp_clearance_desc": ("大力度清库, 尽快释放资金", "Deep clearance to free up cash quickly"),
    "hm.dp_promo": ("降价促销", "Markdown promotion"),
    "hm.dp_promo_desc": ("降价促销拉动动销", "Markdown to lift sell-through"),
    "hm.pick_first": ("请先勾选要处置的项", "Select items to dispose of first"),
    "hm.dispose_confirm": (
        "确定对 {n} 项执行「{action}」？该操作将记录处置状态。",
        "Apply \u201c{action}\u201d to {n} items? The disposal state will be recorded.",
    ),
    "hm.disposed": ("已处置 {n} 项({action})", "Disposed {n} items ({action})"),
    "hm.dispose_failed": ("处置失败: {msg}", "Disposal failed: {msg}"),
    "hm.jd": ("京东", "JD"),
    "hm.other": ("其他", "Others"),
    "hm.tab_replen": ("补货建议", "Replenishment"),
    "hm.tab_purchase": ("采购建议", "Purchasing"),
    "hm.tab_slow": ("滞销预警", "Slow movers"),
    "hm.mode_traditional": ("传统多仓", "Traditional multi-WH"),
    "hm.search": ("搜索", "Search"),
    "hm.columns": ("列选择 ({a}/{b})", "Columns ({a}/{b})"),
    "hm.exporting": ("导出中...", "Exporting..."),
    "hm.export": ("导出", "Export"),
    "hm.batch_dispose": ("批量处置", "Batch dispose"),
    "hm.selected_n": ("已选 {n} 项", "{n} selected"),
    "hm.exit_batch": ("退出批量模式", "Exit batch mode"),
    "hm.enter_batch": ("进入批量模式", "Enter batch mode"),
    "hm.select_all_toggle": ("全选/取消", "Select all / none"),
    "hm.slow_hint": ("滞销预警按 SKU×仓库 · 处置后该行标记「已处理」", "Slow movers are per SKU × warehouse · handled rows are marked \u201chandled\u201d"),
    "hm.actions_title": ("处置动作(按滞销业务特性)", "Disposal actions (slow-mover specifics)"),
    "hm.note_ph": ("备注(可选)", "Note (optional)"),
    "hm.busy": ("处理中...", "Working..."),
    "hm.run_dispose": ("执行处置", "Dispose"),
    "hm.suffix_bbcc": (" (BBCC)", " (BBCC)"),
    "hm.suffix_trad": (" (传统)", " (traditional)"),
    "hm.suffix_search": (" · 搜索", " · search"),
    "hm.search_ph": ("搜索SKU/商品名...", "Search SKU / product name..."),
    "hm.shown": ("已显示", "Shown"),
    "hm.hidden": ("已隐藏", "Hidden"),
    "hm.seq": ("(序号)", "(row #)"),
}

REPL = [
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """          title: '导出任务已提交',
          duration: 6000,
          action: {
            label: '查看进度 →',""",
        """          title: t('hm.export_submitted'),
          duration: 6000,
          action: {
            label: t('hm.view_progress'),""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """        throw new Error(d.error || '提交失败')
      }
    } catch (e) {
      toast.error('导出失败: ' + e.message)
    }""",
        """        throw new Error(d.error || t('hm.submit_failed'))
      }
    } catch (e) {
      toast.error(t('hm.export_failed', { msg: e.message }))
    }""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      label: '标记已处理',
      desc: '确认库存已清理或不再处理',""",
        """      label: 'hm.dp_mark',
      desc: 'hm.dp_mark_desc',""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      label: '退货供应商',
      desc: '向供应商退货, 回收占用资金',""",
        """      label: 'hm.dp_return',
      desc: 'hm.dp_return_desc',""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      label: '清仓甩卖',
      desc: '大力度清库, 尽快释放资金',""",
        """      label: 'hm.dp_clearance',
      desc: 'hm.dp_clearance_desc',""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      label: '降价促销',
      desc: '降价促销拉动动销',""",
        """      label: 'hm.dp_promo',
      desc: 'hm.dp_promo_desc',""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      toast.error('请先勾选要处置的项')""",
        """      toast.error(t('hm.pick_first'))""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      !window.confirm(
        '确定对 ' + ids.length + ' 项执行「' + _a.label + '」？该操作将记录处置状态。',
      )""",
        """      !window.confirm(
        t('hm.dispose_confirm', { n: ids.length, action: t(_a.label) }),
      )""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      toast.success('已处置 ' + items.length + ' 项(' + _a.label + ')')""",
        """      toast.success(t('hm.disposed', { n: items.length, action: t(_a.label) }))""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      toast.error('处置失败: ' + (e.message || ''))""",
        """      toast.error(t('hm.dispose_failed', { msg: e.message || '' }))""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """      <div className="hammer-header">{channel === 'jd' ? '京东' : '其他'} · 建议</div>""",
        """      <div className="hammer-header">
        {channel === 'jd' ? t('hm.jd') : t('hm.other')} · {t('common.suggestions')}
      </div>""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """        {[
          ['replen', '补货建议'],
          ['purchase', '采购建议'],
          ['slow', '滞销预警'],
        ].map(([id, label]) => (""",
        """        {[
          ['replen', 'hm.tab_replen'],
          ['purchase', 'hm.tab_purchase'],
          ['slow', 'hm.tab_slow'],
        ].map(([id, label]) => (""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            className={'hammer-segment' + (hammerInsightsTab === id ? ' active' : '')}
          >
            {label}""",
        """            className={'hammer-segment' + (hammerInsightsTab === id ? ' active' : '')}
          >
            {t(label)}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """          <span
            onClick={() => setHammerReplenMode('traditional')}
            className={'hammer-segment' + (mode === 'traditional' ? ' active' : '')}
          >
            传统多仓
          </span>""",
        """          <span
            onClick={() => setHammerReplenMode('traditional')}
            className={'hammer-segment' + (mode === 'traditional' ? ' active' : '')}
          >
            {t('hm.mode_traditional')}
          </span>""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            搜索
          </button>""",
        """            {t('hm.search')}
          </button>""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            列选择 ({visCols.length}/{cols.length})""",
        """            {t('hm.columns', { a: visCols.length, b: cols.length })}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """          {exporting ? '导出中...' : '导出'}""",
        """          {exporting ? t('hm.exporting') : t('hm.export')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            批量处置
          </button>""",
        """            {t('hm.batch_dispose')}
          </button>""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """              已选{' '}
              <b style={{ color: 'var(--text)' }}>
                {useAppStore.getState().prodSelIds?.length || 0}
              </b>{' '}
              项""",
        """              {t('hm.selected_n', { n: useAppStore.getState().prodSelIds?.length || 0 })}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """                退出批量模式""",
        """                {t('hm.exit_batch')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """                进入批量模式""",
        """                {t('hm.enter_batch')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """              全选/取消""",
        """              {t('hm.select_all_toggle')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            滞销预警按 SKU×仓库 · 处置后该行标记「已处理」""",
        """            {t('hm.slow_hint')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            <span className="text-11 muted2">处置动作(按滞销业务特性)</span>""",
        """            <span className="text-11 muted2">{t('hm.actions_title')}</span>""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """                title={a.desc}""",
        """                title={t(a.desc)}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """                {a.icon}
                {a.label}""",
        """                {a.icon}
                {t(a.label)}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            {DISPOSE_ACTIONS[bpAction]?.desc || ''}""",
        """            {DISPOSE_ACTIONS[bpAction]?.desc ? t(DISPOSE_ACTIONS[bpAction].desc) : ''}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """              placeholder="备注(可选)\"""",
        """              placeholder={t('hm.note_ph')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """              {bpBusy ? '处理中...' : '执行处置'}""",
        """              {bpBusy ? t('hm.busy') : t('hm.run_dispose')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            {hammerInsightsTab === 'replen'
              ? '补货建议'
              : hammerInsightsTab === 'purchase'
                ? '采购建议'
                : '滞销预警'}
            {isPurchase ? '' : mode === 'bbcc' ? ' (BBCC)' : ' (传统)'} · 搜索""",
        """            {hammerInsightsTab === 'replen'
              ? t('hm.tab_replen')
              : hammerInsightsTab === 'purchase'
                ? t('hm.tab_purchase')
                : t('hm.tab_slow')}
            {isPurchase ? '' : mode === 'bbcc' ? t('hm.suffix_bbcc') : t('hm.suffix_trad')}
            {t('hm.suffix_search')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """            placeholder="搜索SKU/商品名...\"""",
        """            placeholder={t('hm.search_ph')}""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """              <span>已显示</span>""",
        """              <span>{t('hm.shown')}</span>""",
    ),
    (
        "frontend/src/components/hammer/HammerInsights.tsx",
        """                  <span>已隐藏</span>""",
        """                  <span>{t('hm.hidden')}</span>""",
    ),
]
