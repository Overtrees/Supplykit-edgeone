LOCALE = {
    "hr.eval_done": ("评估完成 · jd {jd} · other {ot}", "Evaluation done · jd {jd} · other {ot}"),
    "hr.eval_failed": ("评估失败: {msg}", "Evaluation failed: {msg}"),
    "hr.pick_rules": ("请先勾选规则", "Select rules first"),
    "hr.confirm_delete": (
        "删除 {n} 条规则？可在回收站恢复",
        "Delete {n} rules? They can be restored from the recycle bin",
    ),
    "hr.batch_done": ("{label}完成: {n} 项", "{label}: {n} items done"),
    "hr.batch_failed": ("{label}失败: {msg}", "{label} failed: {msg}"),
    "hr.undo": ("撤销", "Undo"),
    "hr.undone": ("已撤销删除", "Deletion undone"),
    "hr.undo_failed": ("撤销失败: {msg}", "Undo failed: {msg}"),
    "hr.header": ("{ch} · 规则参数", "{ch} · rule params"),
    "hr.ch_jd": ("京东", "JD"),
    "hr.ch_other": ("其他", "Others"),
    "hr.tab_rules": ("规则", "Rules"),
    "hr.tab_params": ("补货", "Replenish"),
    "hr.tab_purchase": ("采购", "Purchase"),
    "hr.tab_slow": ("滞销", "Slow"),
    "hr.new": ("+ 新建", "+ New"),
    "hr.search": ("搜索", "Search"),
    "hr.history": ("变更历史", "Change history"),
    "hr.batch": ("批量操作", "Batch actions"),
    "hr.evaluating": ("评估中(全量 SKU×仓)...", "Evaluating (all SKU × warehouses)..."),
    "hr.run_all": ("立即运行全部规则", "Run all rules now"),
    "hr.selected_n": ("已选 {n} 项", "{n} selected"),
    "hr.exit_batch": ("退出批量模式", "Exit batch mode"),
    "hr.enter_batch": ("进入批量模式", "Enter batch mode"),
    "hr.select_all_toggle": ("全选/取消", "Select all / none"),
    "hr.act_active": ("启用", "Enable"),
    "hr.act_inactive": ("停用", "Disable"),
    "hr.act_delete": ("删除", "Delete"),
    "hr.batch_enable": ("批量启用", "Enable selected"),
    "hr.batch_disable": ("批量停用", "Disable selected"),
    "hr.batch_delete": ("批量删除", "Delete selected"),
    "hr.batch_hint": ("勾选规则后在此批量操作（删除可回收站恢复）", "Select rules to act on them here (deleted ones go to the recycle bin)"),
    "hr.all_active": (" · 所选规则已全部启用", " · all selected rules are enabled"),
    "hr.all_inactive": (" · 所选规则已全部停用", " · all selected rules are disabled"),
    "hr.search_ph": ("搜索规则名称...", "Search rule name..."),
    "hr.clear": ("清除", "Clear"),
    "hr.mode_bbcc": ("BBCC 送仓", "BBCC delivery"),
    "hr.mode_trad": ("传统多仓", "Traditional multi-WH"),
}

REPL = [
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """      toast.success('评估完成 · jd ' + (ev.jd ?? '-') + ' · other ' + (ev.other ?? '-'))""",
        """      toast.success(t('hr.eval_done', { jd: ev.jd ?? '-', ot: ev.other ?? '-' }))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """      toast.error('评估失败: ' + (e.message || ''))""",
        """      toast.error(t('hr.eval_failed', { msg: e.message || '' }))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """      toast.error('请先勾选规则')""",
        """      toast.error(t('hr.pick_rules'))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """    if (action === 'delete' && !window.confirm('删除 ' + ids.length + ' 条规则？可在回收站恢复'))
      return""",
        """    if (action === 'delete' && !window.confirm(t('hr.confirm_delete', { n: ids.length })))
      return""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """          title: label + '完成: ' + ids.length + ' 项',
          duration: 5000,
          action: {
            label: '撤销',""",
        """          title: t('hr.batch_done', { label, n: ids.length }),
          duration: 5000,
          action: {
            label: t('hr.undo'),""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                toast.success('已撤销删除')""",
        """                toast.success(t('hr.undone'))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                toast.error('撤销失败: ' + (e.message || ''))""",
        """                toast.error(t('hr.undo_failed', { msg: e.message || '' }))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """        toast.success(label + '完成: ' + ids.length + ' 项')""",
        """        toast.success(t('hr.batch_done', { label, n: ids.length }))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """      toast.error(label + '失败: ' + (e.message || ''))""",
        """      toast.error(t('hr.batch_failed', { label, msg: e.message || '' }))""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """      <div className="hammer-header">{channel === 'jd' ? '京东' : '其他'} · 规则参数</div>""",
        """      <div className="hammer-header">
        {t('hr.header', { ch: channel === 'jd' ? t('hr.ch_jd') : t('hr.ch_other') })}
      </div>""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """        {[
          ['rules', '规则'],
          ['params', '补货'],
          ['purchase', '采购'],
          ['slow', '滞销'],
        ].map(([id, label]) => (""",
        """        {[
          ['rules', 'hr.tab_rules'],
          ['params', 'hr.tab_params'],
          ['purchase', 'hr.tab_purchase'],
          ['slow', 'hr.tab_slow'],
        ].map(([id, label]) => (""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """            className={'hammer-segment' + (hammerRulesTab === id ? ' active' : '')}
          >
            {label}""",
        """            className={'hammer-segment' + (hammerRulesTab === id ? ' active' : '')}
          >
            {t(label)}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """              + 新建
            </button>""",
        """              {t('hr.new')}
            </button>""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """              搜索{hammerSearch ? <IconCheck size={12} style={{ verticalAlign: -2 }} /> : ''}""",
        """              {t('hr.search')}
              {hammerSearch ? <IconCheck size={12} style={{ verticalAlign: -2 }} /> : ''}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """              变更历史
            </button>""",
        """              {t('hr.history')}
            </button>""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """              批量操作
            </button>""",
        """              {t('hr.batch')}
            </button>""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """            {evaluating ? '评估中(全量 SKU×仓)...' : '立即运行全部规则'}""",
        """            {evaluating ? t('hr.evaluating') : t('hr.run_all')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                  已选{' '}
                  <b style={{ color: 'var(--text)' }}>
                    {(useAppStore.getState().prodSelIds || []).length}
                  </b>{' '}
                  项""",
        """                  {t('hr.selected_n', {
                    n: (useAppStore.getState().prodSelIds || []).length,
                  })}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                    退出批量模式""",
        """                    {t('hr.exit_batch')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                    进入批量模式""",
        """                    {t('hr.enter_batch')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                  全选/取消""",
        """                  {t('hr.select_all_toggle')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                  onClick={() => runBatch('active', '启用')}
                >
                  批量启用""",
        """                  onClick={() => runBatch('active', t('hr.act_active'))}
                >
                  {t('hr.batch_enable')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                  onClick={() => runBatch('inactive', '停用')}
                >
                  批量停用""",
        """                  onClick={() => runBatch('inactive', t('hr.act_inactive'))}
                >
                  {t('hr.batch_disable')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                  onClick={() => runBatch('delete', '删除')}
                >
                  批量删除""",
        """                  onClick={() => runBatch('delete', t('hr.act_delete'))}
                >
                  {t('hr.batch_delete')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                勾选规则后在此批量操作（删除可回收站恢复）
                {allActive ? ' · 所选规则已全部启用' : allInactive ? ' · 所选规则已全部停用' : ''}""",
        """                {t('hr.batch_hint')}
                {allActive
                  ? t('hr.all_active')
                  : allInactive
                    ? t('hr.all_inactive')
                    : ''}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                placeholder="搜索规则名称...\"""",
        """                placeholder={t('hr.search_ph')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """                    清除
                  </button>""",
        """                    {t('hr.clear')}
                  </button>""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """            BBCC 送仓""",
        """            {t('hr.mode_bbcc')}""",
    ),
    (
        "frontend/src/components/hammer/HammerRules.tsx",
        """            传统多仓""",
        """            {t('hr.mode_trad')}""",
    ),
]
