"""批量: 外壳与零散组件对齐词条(第二批)。

- App.tsx 的操作态 label 直接进 OpPill 渲染 → 存词条键, 由 OpPill 解析
- ErrorRetry/Freshness/LogFileList/LoginPage/HistorySheet/SeasonPanel 常规文案
- InsightsPage 的 mutate({label,success,errorPrefix}) 会进 toast → 传词条键,
  mutate 内部已用 t() 解析(label 作 i18n 键)
"""
import os, sys, importlib.util

ROOT = '/workspace/supplykit'
ZH = os.path.join(ROOT, 'frontend/src/locale.zh.ts')
EN = os.path.join(ROOT, 'frontend/src/locale.en.ts')

spec = importlib.util.spec_from_file_location('repl', os.path.join(ROOT, '.artifacts/i18n/repl.py'))
repl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repl)

LOCALE = [
    # App / 操作态
    ('op.rules_running', '规则重算中', 'Recalculating rules'),
    ('op.n_processing', '{n} 项处理中', '{n} running'),
    ('op.seed', '种子数据填充', 'Seed data'),
    ('op.clean', '数据清洗', 'Data cleansing'),
    ('op.reset', '数据重置', 'Data reset'),
    ('op.export', '数据导出', 'Data export'),
    ('op.rules_fail', '规则重算失败，告警可能未更新（详情见质量页日志）',
     'Rule recalculation failed; alerts may be stale (see quality logs)'),
    ('op.sync_old', '同步中', 'Syncing'),
    ('op.seed_done', '种子数据填充完成', 'Seed data completed'),
    ('op.seed_fail', '种子数据填充失败', 'Seed data failed'),
    ('op.clean_done', '数据清洗完成', 'Cleansing completed'),
    ('op.clean_fail', '数据清洗失败', 'Cleansing failed'),
    ('op.reset_done', '数据重置完成', 'Reset completed'),
    ('op.export_done', '导出完成，可在质量日志查看下载', 'Export done — download from quality logs'),
    ('op.export_fail', '导出失败', 'Export failed'),
    ('op.title', '点击查看后台任务', 'View background tasks'),
    ('op.panel_title', '后台处理中', 'Running in background'),
    ('op.panel_hint', '这些操作在后端继续执行，可以离开当前页面；完成后看板与列表会自动更新。',
     'These keep running on the server; you can leave this page. Dashboards and lists refresh when done.'),
    ('op.hide', '隐藏', 'Hide'),
    ('op.hide_tip', '仅从列表移除(不影响后端任务)', 'Remove from list only (task keeps running)'),
    # ErrorRetry
    ('er.net_fluctuation', '网络波动', 'Network hiccup'),
    ('er.net_unavailable', '网络异常或服务不可用', 'Network error or service unavailable'),
    ('er.refresh_page', '刷新页面', 'Refresh page'),
    ('er.retry_hint_long', '连续重试未恢复，建议刷新页面（重新连接服务）',
     'Still failing after retries — try refreshing the page'),
    # 新鲜度
    ('fr.unknown', '未知', 'Unknown'),
    ('fr.today', '今天', 'Today'),
    ('fr.days_ago', '{n} 天前', '{n}d ago'),
    ('fr.data_stale', '{label}数据 {d} 天未更新', '{label} data not updated for {d} day(s)'),
    ('fr.data_stale_click', '数据 {d} 天未更新, 点击查看', 'Data not updated for {d} day(s), tap to view'),
    ('fr.title', '数据新鲜度', 'Data freshness'),
    ('fr.hint', '数据来自 ERP / 平台导出快照，超期建议重新导入对应类型',
     'Data comes from ERP / platform snapshots; re-import when overdue'),
    ('fr.section_dash', '看板指标', 'Dashboard metrics'),
    ('fr.section_src', '原始数据源', 'Raw data sources'),
    ('fr.empty', '暂无导入记录', 'No imports yet'),
    # 日志
    ('log.no_log', '# 无日志', '# No logs'),
    ('log.load_fail_retry', '加载失败，点此重试', 'Load failed — tap to retry'),
    ('log.loading_list', '加载文件列表...', 'Loading file list...'),
    ('log.empty', '暂无历史日志文件（每日维护自动归档，保留 90 天）',
     'No archived logs yet (daily maintenance archives, 90-day retention)'),
    ('log.n_items', '{n} 条', '{n}'),
    ('log.title', '质量日志 · {date}', 'Quality logs · {date}'),
    ('log.retry', '重试加载', 'Retry'),
    ('log.share', '分享文件', 'Share file'),
    ('log.sharing', '分享中...', 'Sharing...'),
    # 登录
    ('login.need_both', '请输入用户名和密码', 'Please enter username and password'),
    ('login.bad_cred', '用户名或密码错误', 'Incorrect username or password'),
    ('login.net_fail', '无法连接到服务器，请重试', 'Cannot reach the server, please retry'),
    ('login.subtitle', '供应链数据工作台', 'Supply chain data workbench'),
    ('login.title', '登录', 'Sign in'),
    ('login.user_ph', '用户名', 'Username'),
    ('login.pwd_ph', '密码', 'Password'),
    ('login.loading', '登录中...', 'Signing in...'),
    ('login.guest', '访客模式：', 'Guest mode: '),
    ('login.guest_hint', '（仅可查看，无需注册）', ' (view only, no sign-up)'),
    ('login.disclaimer', '声明：系统所使用的演示数据涉及品牌、商品、供应商为虚构，与任何真实企业或个人无关',
     'Note: demo brands, products and suppliers are fictional and unrelated to any real business or person'),
    # 历史
    ('hs.title', '配置变更历史', 'Configuration change history'),
    ('hs.empty', '暂无变更记录', 'No changes yet'),
    ('hs.mode_bbcc', 'BBCC', 'BBCC'),
    ('hs.mode_trad', '传统', 'Traditional'),
    ('hs.empty_value', '(空)', '(empty)'),
    ('hs.close', '关闭', 'Close'),
    # 活动系数
    ('sp.title', '活动系数', 'Campaign multipliers'),
    ('sp.name_ph', '618大促', 'e.g. Black Friday'),
    ('sp.unit_times', '倍', '×'),
    ('sp.enable', '启用', 'Enable'),
    ('sp.new_name', '新活动', 'New campaign'),
    ('sp.add', '+ 添加活动', '+ Add campaign'),
    ('sp.saved', '已保存', 'Saved'),
    ('sp.saving', '保存中...', 'Saving...'),
    ('sp.save', '保存', 'Save'),
    # 自有仓健康
    ('oh.title_n', '自有仓健康 · 共 {n} 项', 'Own warehouse health · {n} item(s)'),
    ('oh.tag_transfer_gap', '调拨缺口', 'Transfer gap'),
    ('oh.tag_expiry', '效期', 'Expiry'),
    ('oh.tag_overstock', '积压', 'Overstock'),
    ('oh.tag_warning', '预警', 'Warning'),
    ('oh.gap_n', '缺 {n}', 'Short {n}'),
    ('oh.expired_n', '过期 {n}天', 'Expired {n}d'),
    ('oh.left_n', '剩 {n}天', '{n}d left'),
    ('oh.cover_n', '撑 {n}天', 'Covers {n}d'),
    # 洞察页动作
    ('ip.cancel_order', '取消下单', 'Cancel order'),
    ('ip.cancel_ordered', '已取消下单', 'Order cancelled'),
    ('ip.cancel_failed', '取消失败', 'Cancel failed'),
    ('ip.place_order', '下单', 'Place order'),
    ('ip.placed', '已下单', 'Order placed'),
    ('ip.place_failed', '下单失败', 'Order failed'),
    ('ip.update_arrival', '更新到仓日期', 'Update arrival date'),
    ('ip.update_failed', '更新失败', 'Update failed'),
    # 库存锤子补充
    ('hi.wh_own', '自有仓', 'Own'),
    ('hi.wh_platform', '平台仓', 'Platform'),
    ('hi.wh_b', 'B仓', 'B warehouse'),
    ('hi.flow_detail', '出入库明细', 'Inbound / outbound details'),
    ('hi.wh_type', '仓库类型', 'Warehouse type'),
    ('hi.search_ph', '搜索SKU/商品名...', 'Search SKU / product name...'),
    ('hi.wh_label', '仓库 {w}', 'Warehouse {w}'),
]

A = 'frontend/src/App.tsx'
FR = 'frontend/src/components/Freshness.tsx'
LG = 'frontend/src/components/LogFileList.tsx'
LP = 'frontend/src/pages/LoginPage.tsx'
OP = 'frontend/src/components/OpPill.tsx'
ER = 'frontend/src/components/ErrorRetry.tsx'
HS = 'frontend/src/components/hammer/HistorySheet.tsx'
IP = 'frontend/src/pages/InsightsPage.tsx'
SP = 'frontend/src/pages/rules/SeasonPanel.tsx'
OH = 'frontend/src/pages/dashboard/sheets/OwnHealthSheet.tsx'
HI = 'frontend/src/components/hammer/HammerInventory.tsx'

EDITS = [
    # ===== App.tsx =====
    (A, "notify.error('规则重算失败，告警可能未更新（详情见质量页日志）')",
        "notify.error(t('op.rules_fail'))"),
    (A, "{syncOld ? '同步中' : '加载中'}", "{syncOld ? t('op.sync_old') : t('common.loading')}"),
    (A, "label: '种子数据填充',", "label: 'op.seed',"),
    (A, "notify.success('种子数据填充完成')", "notify.success(t('op.seed_done'))"),
    (A, "notify.error('种子数据填充失败')", "notify.error(t('op.seed_fail'))"),
    (A, "label: '数据清洗',", "label: 'op.clean',"),
    (A, "notify.success('数据清洗完成')", "notify.success(t('op.clean_done'))"),
    (A, "notify.error('数据清洗失败')", "notify.error(t('op.clean_fail'))"),
    (A, "label: '数据重置',", "label: 'op.reset',"),
    (A, "notify.success('数据重置完成')", "notify.success(t('op.reset_done'))"),
    (A, "label: '数据导出',", "label: 'op.export',"),
    (A, "notify.success('导出完成，可在质量日志查看下载')", "notify.success(t('op.export_done'))"),
    (A, "notify.error('导出失败')", "notify.error(t('op.export_fail'))"),

    # ===== OpPill =====
    (OP, """  const label = rules.length
    ? '规则重算中'
    : ops.length > 1
      ? ops.length + ' 项处理中'
      : ops[0].label""",
        """  const label = rules.length
    ? t('op.rules_running')
    : ops.length > 1
      ? t('op.n_processing', { n: ops.length })
      : t(ops[0].label)"""),
    (OP, 'title="点击查看后台任务"', "title={t('op.title')}"),
    (OP, "<span style={{ fontWeight: 700 }}>后台处理中</span>",
         "<span style={{ fontWeight: 700 }}>{t('op.panel_title')}</span>"),
    (OP, """<span className="small muted" style={{ cursor: 'pointer' }}>
                      关闭
                    </span>""",
         """<span className="small muted" style={{ cursor: 'pointer' }}>
                      {t('common.close')}
                    </span>"""),
    (OP, "                  这些操作在后端继续执行，可以离开当前页面；完成后看板与列表会自动更新。",
         "                  {t('op.panel_hint')}"),
    (OP, 'title="仅从列表移除(不影响后端任务)"', "title={t('op.hide_tip')}"),
    (OP, "                      隐藏", "                      {t('op.hide')}"),
    (OP, "                        {o.label}", "                        {t(o.label)}"),

    # ===== ErrorRetry =====
    (ER, """    ? '加载失败'
    : '网络波动'""", """    ? t('common.load_failed')
    : t('er.net_fluctuation')"""),
    (ER, "error = '加载失败',", "error = '',"),
    (ER, "const finalDesc = desc ?? (err ? errText(err) : '网络异常或服务不可用')",
         "const finalDesc = desc ?? (err ? errText(err) : t('er.net_unavailable'))"),
    (ER, "            {t('common.retry') || '重试'}", "            {t('common.retry')}"),
    (ER, "              刷新页面", "              {t('er.refresh_page')}"),
    (ER, "              连续重试未恢复，建议刷新页面（重新连接服务）", "              {t('er.retry_hint_long')}"),

    # ===== Freshness =====
    (FR, "  if (d === null || d === undefined) return '未知'\n  if (d <= 0) return '今天'\n  return d + ' 天前'",
         "  if (d === null || d === undefined) return t('fr.unknown')\n  if (d <= 0) return t('fr.today')\n  return t('fr.days_ago', { n: d })"),
    (FR, "title={label ? `${label}数据 ${days ?? '?'} 天未更新` : undefined}",
         "title={label ? t('fr.data_stale', { label, d: days ?? '?' }) : undefined}"),
    (FR, "title={`数据 ${days ?? '?'} 天未更新, 点击查看`}",
         "title={t('fr.data_stale_click', { d: days ?? '?' })}"),
    (FR, "<span>数据新鲜度</span>", "<span>{t('fr.title')}</span>"),
    (FR, "              关闭", "              {t('common.close')}"),
    (FR, '<div className="fresh-hint">数据来自 ERP / 平台导出快照，超期建议重新导入对应类型</div>',
         '<div className="fresh-hint">{t(\'fr.hint\')}</div>'),
    (FR, '<div className="fresh-section">看板指标</div>',
         '<div className="fresh-section">{t(\'fr.section_dash\')}</div>'),
    (FR, '<div className="fresh-section">原始数据源</div>',
         '<div className="fresh-section">{t(\'fr.section_src\')}</div>'),
    (FR, '<div className="fresh-empty">暂无导入记录</div>',
         '<div className="fresh-empty">{t(\'fr.empty\')}</div>'),

    # ===== LogFileList =====
    (LG, "markdown: r.data?.markdown || '# 无日志\\n',", "markdown: r.data?.markdown || t('log.no_log') + '\\n',"),
    (LG, "markdown: '加载失败，点此重试',", "markdown: t('log.load_fail_retry'),"),
    (LG, "              加载文件列表...", "              {t('log.loading_list')}"),
    (LG, "              暂无历史日志文件（每日维护自动归档，保留 90 天）", "              {t('log.empty')}"),
    (LG, "              {f.count || 0} 条", "              {t('log.n_items', { n: f.count || 0 })}"),
    (LG, "              质量日志 · {preview.date}{' '}", "              {t('log.title', { date: preview.date })}{' '}"),
    (LG, "              {preview.count} 条", "              {t('log.n_items', { n: preview.count })}"),
    (LG, "                加载中...", "                {t('common.loading')}"),
    (LG, "              重试加载", "              {t('log.retry')}"),
    (LG, "              {sharing ? '分享中...' : '分享文件'}", "              {sharing ? t('log.sharing') : t('log.share')}"),

    # ===== LoginPage =====
    (LP, "setError('请输入用户名和密码')", "setError(t('login.need_both'))"),
    (LP, "setError(d.detail || '用户名或密码错误')", "setError(d.detail || t('login.bad_cred'))"),
    (LP, "setError('无法连接到服务器，请重试')", "setError(t('login.net_fail'))"),
    (LP, "供应链数据工作台", "{t('login.subtitle')}"),
    (LP, ">登录</div>", ">{t('login.title')}</div>"),
    (LP, 'placeholder="用户名"', "placeholder={t('login.user_ph')}"),
    (LP, 'placeholder="密码"', "placeholder={t('login.pwd_ph')}"),
    (LP, "{loading ? '登录中...' : '登录'}", "{loading ? t('login.loading') : t('login.title')}"),
    (LP, "              访客模式：<b>demo</b> / <b>demo123</b>（仅可查看，无需注册）",
         "              {t('login.guest')}<b>demo</b> / <b>demo123</b>\n              {t('login.guest_hint')}"),
    (LP, "声明：系统所使用的演示数据涉及品牌、商品、供应商为虚构，与任何真实企业或个人无关",
         "{t('login.disclaimer')}"),

    # ===== HistorySheet =====
    (HS, "              配置变更历史", "              {t('hs.title')}"),
    (HS, "              暂无变更记录", "              {t('hs.empty')}"),
    (HS, "const modeInfo = h.mode ? (h.mode === 'bbcc' ? 'BBCC' : '传统') : ''",
         "const modeInfo = h.mode ? (h.mode === 'bbcc' ? t('hs.mode_bbcc') : t('hs.mode_trad')) : ''"),
    (HS, "{h.channel === 'jd' ? '京东' : '其他'}", "{h.channel === 'jd' ? t('channel.jd') : t('hm.other')}"),
    (HS, "{h.old_value || '(空)'}", "{h.old_value || t('hs.empty_value')}"),
    (HS, "{h.new_value || '(空)'}", "{h.new_value || t('hs.empty_value')}"),
    (HS, "              关闭", "              {t('hs.close')}"),

    # ===== InsightsPage =====
    (IP, "        label: '取消下单',", "        label: 'ip.cancel_order',"),
    (IP, "        success: '已取消下单',", "        success: 'ip.cancel_ordered',"),
    (IP, "        errorPrefix: '取消失败',", "        errorPrefix: 'ip.cancel_failed',"),
    (IP, "        label: '下单',", "        label: 'ip.place_order',"),
    (IP, "        success: '已下单',", "        success: 'ip.placed',"),
    (IP, "        errorPrefix: '下单失败',", "        errorPrefix: 'ip.place_failed',"),
    (IP, "      label: '更新到仓日期',", "      label: 'ip.update_arrival',"),
    (IP, "      errorPrefix: '更新失败',", "      errorPrefix: 'ip.update_failed',"),

    # ===== SeasonPanel =====
    (SP, "<IconTag size={14} /> 活动系数", "<IconTag size={14} /> {t('sp.title')}"),
    (SP, 'placeholder="618大促"', "placeholder={t('sp.name_ph')}"),
    (SP, '<span className="small muted">倍</span>', '<span className="small muted">{t(\'sp.unit_times\')}</span>'),
    (SP, "              启用", "              {t('sp.enable')}"),
    (SP, "                    删除", "                    {t('common.delete')}"),
    (SP, "{ key: 'new', name: '新活动', factor: 1.2, enabled: true }",
         "{ key: 'new', name: t('sp.new_name'), factor: 1.2, enabled: true }"),
    (SP, "              + 添加活动", "              {t('sp.add')}"),
    (SP, "toast.success('已保存')", "toast.success(t('sp.saved'))"),
    (SP, "<IconLoading size={14} /> 保存中...", "<IconLoading size={14} /> {t('sp.saving')}"),
    (SP, "<IconSave size={14} /> 保存", "<IconSave size={14} /> {t('sp.save')}"),

    # ===== OwnHealthSheet =====
    (OH, "          自有仓健康 · 共 {fmtInt((ownHealth as OwnHealthData | null)?.total || 0)} 项",
         "          {t('oh.title_n', {\n            n: fmtInt((ownHealth as OwnHealthData | null)?.total || 0),\n          })}"),
    (OH, "              own_transfer_gap: { tag: '调拨缺口', cls: 'danger', c: 'var(--danger)' },",
         "              own_transfer_gap: { tag: 'oh.tag_transfer_gap', cls: 'danger', c: 'var(--danger)' },"),
    (OH, "              own_expiry_risk: { tag: '效期', cls: 'danger', c: 'var(--danger)' },",
         "              own_expiry_risk: { tag: 'oh.tag_expiry', cls: 'danger', c: 'var(--danger)' },"),
    (OH, "              own_overstock: { tag: '积压', cls: 'warning', c: 'var(--warning)' },",
         "              own_overstock: { tag: 'oh.tag_overstock', cls: 'warning', c: 'var(--warning)' },"),
    (OH, "                    tag: '预警',", "                    tag: 'oh.tag_warning',"),
    (OH, "                      ? '缺 ' + fmtInt(x.gap_qty)", "                      ? t('oh.gap_n', { n: fmtInt(x.gap_qty) })"),
    (OH, "                      ? '过期 ' + Math.abs(x.days_to_expiry) + '天'",
         "                      ? t('oh.expired_n', { n: Math.abs(x.days_to_expiry) })"),
    (OH, "                      : '剩 ' + fmtInt(x.days_to_expiry ?? undefined) + '天'",
         "                      : t('oh.left_n', { n: fmtInt(x.days_to_expiry ?? undefined) })"),
    (OH, "                      : '撑 ' + Math.round(Number(x.cover_days || 0)) + '天'",
         "                      : t('oh.cover_n', { n: Math.round(Number(x.cover_days || 0)) })"),
    (OH, ">关闭</span>", ">{t('common.close')}</span>"),

    # ===== HammerInventory =====
    (HI, "          title: '导出任务已提交',", "          title: t('hx.export_submitted'),"),
    (HI, "            label: '查看进度 →',", "            label: t('hx.view_progress'),"),
    (HI, "<IconClipboard size={13} /> 出入库明细", "<IconClipboard size={13} /> {t('hi.flow_detail')}"),
    (HI, "              取消全选", "              {t('common.deselect')}"),
    (HI, "<span>已显示</span>", "<span>{t('common.visible')}</span>"),
    (HI, "<span>已隐藏</span>", "<span>{t('common.hidden')}</span>"),
    (HI, 'placeholder="搜索SKU/商品名..."', "placeholder={t('hi.search_ph')}"),
    (HI, '<div className="muted2 text-10 mb-4">仓库类型</div>',
         '<div className="muted2 text-10 mb-4">{t(\'hi.wh_type\')}</div>'),
]


def esc(v):
    return v.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')


def add_locale():
    for path, idx, tag in ((ZH, 1, 'zh'), (EN, 2, 'en')):
        with open(path, encoding='utf-8') as f:
            cur = f.read()
        if "'op.rules_running'" in cur:
            print(f'  [locale {tag}] 已存在, 跳过')
            continue
        block = ''.join(f"  '{r[0]}': '{esc(r[idx])}',\n" for r in LOCALE)
        pos = cur.index('= {') + 3
        cur = cur[:pos] + '\n  // ---- 外壳/零散组件 ----\n' + block + cur[pos:]
        with open(path, 'w', encoding='utf-8') as f:
            f.write(cur)
        print(f'  [locale {tag}] +{len(LOCALE)} 键')


def ensure_import(rel):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding='utf-8').read()
    if __import__('re').search(r"from '(?:\.\./)+locale'", s):
        return
    lines = s.split('\n')
    idx = max((i for i, l in enumerate(lines) if l.startswith('import ')), default=-1)
    if idx < 0:
        print(f'  [{rel}] 无 import, 跳过')
        return
    depth = rel.count('/') - 1
    up = '../' * (depth - 1)
    lines.insert(idx + 1, f"import {{ t }} from '{up}locale'")
    open(p, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'  [{rel}] +import t')


def main():
    for rel in (A, FR, LG, LP, OP, ER, HS, IP, SP, OH, HI):
        ensure_import(rel)
    add_locale()
    ok, _ = repl.apply_all(EDITS, ROOT)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
