LOCALE = {
    "ch.list_failed": ("渠道列表加载失败", "Failed to load channels"),
    "ch.err_code": (
        "code 需小写字母开头，1-20 位字母/数字/下划线",
        "code must start with a lowercase letter, 1-20 chars of a-z / 0-9 / _",
    ),
    "ch.created_copied": (
        "已创建「{name}」，复制规则 {rules} 条、参数 {cfg} 项",
        "Created \u201c{name}\u201d — copied {rules} rules and {cfg} params",
    ),
    "ch.created": ("已创建「{name}」", "Created \u201c{name}\u201d"),
    "ch.create_failed": ("创建失败", "Create failed"),
    "ch.saved": ("已保存", "Saved"),
    "ch.save_failed": ("保存失败", "Save failed"),
    "ch.disable_confirm": (
        "禁用渠道「{code}」？\n\n禁用后下拉不再出现，但已有数据保留（可再次启用）。",
        "Disable channel \u201c{code}\u201d?\n\nIt disappears from dropdowns, but existing data is kept (you can re-enable it).",
    ),
    "ch.disabled": ("已禁用", "Disabled"),
    "ch.disable_failed": ("禁用失败", "Failed to disable"),
    "ch.empty": ("暂无渠道", "No channels yet"),
    "ch.empty_sub": ("点下方「新增渠道」创建", "Tap \u201cAdd channel\u201d below to create one"),
    "ch.suffix_disabled": ("（已禁用）", " (disabled)"),
    "ch.mode_traditional": ("传统多仓", "Traditional multi-warehouse"),
    "ch.mode_traditional_full": ("传统多仓（逐仓补货）", "Traditional multi-warehouse (per-warehouse)"),
    "ch.mode_bbcc": ("BBCC（一盘货：B仓→C仓）", "BBCC (single pool: B → C warehouse)"),
    "ch.with_b": (" · 含B仓", " · incl. B warehouse"),
    "ch.collapse": ("收起", "Collapse"),
    "ch.add": ("新增渠道", "Add channel"),
    "ch.add_sub": (
        "登记后无需发版：下拉自动出现，可复制既有渠道的规则与参数",
        "Takes effect without a release: it shows up in dropdowns, and you can copy rules and params from an existing channel",
    ),
    "ch.code_label": ("渠道标识 code（小写，创建后不可改）", "Channel code (lowercase, immutable)"),
    "ch.code_ph": ("如 tmall / pdd / douyin", "e.g. tmall / pdd / douyin"),
    "ch.name_label": ("显示名", "Display name"),
    "ch.name_ph": ("如 天猫", "e.g. Tmall"),
    "ch.mode_label": ("默认补货模式", "Default replenishment mode"),
    "ch.supports_b": ("走 BBCC 体系（启用 B 仓调拨链）", "Use BBCC (enable B-warehouse transfer chain)"),
    "ch.copy_from": ("复制规则与参数自（可选）", "Copy rules and params from (optional)"),
    "ch.copy_none": ("不复制（空白渠道）", "Do not copy (empty channel)"),
    "ch.creating": ("创建中...", "Creating..."),
    "ch.create": ("创建渠道", "Create channel"),
    "ch.group_disable": ("停用渠道", "Disable channels"),
    "ch.disable_row": ("禁用 {name}", "Disable {name}"),
    "ch.enable_row": ("重新启用 {name}", "Re-enable {name}"),
}

REPL = [
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """      .catch((e: Error) => toast.error(e && e.message ? e.message : '渠道列表加载失败'))""",
        """      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.list_failed')))""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """      toast.warning('code 需小写字母开头，1-20 位字母/数字/下划线')""",
        """      toast.warning(t('ch.err_code'))""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """        toast.success(
          cp.rules != null
            ? `已创建「${form.name || code}」，复制规则 ${cp.rules} 条、参数 ${cp.config} 项`
            : `已创建「${form.name || code}」`,
        )""",
        """        toast.success(
          cp.rules != null
            ? t('ch.created_copied', {
                name: form.name || code,
                rules: cp.rules,
                cfg: cp.config,
              })
            : t('ch.created', { name: form.name || code }),
        )""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """      .catch((e: Error) => toast.error(e && e.message ? e.message : '创建失败'))""",
        """      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.create_failed')))""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """        toast.success('已保存')
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : '保存失败'))""",
        """        toast.success(t('ch.saved'))
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.save_failed')))""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """    if (
      !window.confirm(`禁用渠道「${code}」？\\n\\n禁用后下拉不再出现，但已有数据保留（可再次启用）。`)
    )
      return""",
        """    if (!window.confirm(t('ch.disable_confirm', { code }))) return""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """        toast.success('已禁用')
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : '禁用失败'))""",
        """        toast.success(t('ch.disabled'))
        load()
      })
      .catch((e: Error) => toast.error(e && e.message ? e.message : t('ch.disable_failed')))""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """      <Group title="渠道管理">""",
        """      <Group title={t('set.channels')}>""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """          <Row label="暂无渠道" sub="点下方「新增渠道」创建" />""",
        """          <Row label={t('ch.empty')} sub={t('ch.empty_sub')} />""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """              label={`${c.name}${c.enabled === 0 ? '（已禁用）' : ''}`}
              sub={`${c.code} · ${c.default_mode === 'bbcc' ? 'BBCC' : '传统多仓'}${
                c.supports_b ? ' · 含B仓' : ''
              }`}""",
        """              label={`${channelName(c)}${c.enabled === 0 ? t('ch.suffix_disabled') : ''}`}
              sub={`${c.code} · ${
                c.default_mode === 'bbcc' ? 'BBCC' : t('ch.mode_traditional')
              }${c.supports_b ? t('ch.with_b') : ''}`}""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """          label={showForm ? '收起' : '新增渠道'}
          sub="登记后无需发版：下拉自动出现，可复制既有渠道的规则与参数\"""",
        """          label={showForm ? t('ch.collapse') : t('ch.add')}
          sub={t('ch.add_sub')}""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """        <Group title="新增渠道">
          <div className="form-grid">
            <label className="form-field">
              渠道标识 code（小写，创建后不可改）
              <input
                value={form.code}
                onChange={e => setForm({ ...form, code: e.target.value })}
                placeholder="如 tmall / pdd / douyin"
                className="form-input"
              />
            </label>
            <label className="form-field">
              显示名
              <input
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="如 天猫"
                className="form-input"
              />
            </label>
            <label className="form-field">
              默认补货模式
              <select
                value={form.default_mode}
                onChange={e => setForm({ ...form, default_mode: e.target.value })}
                className="form-input"
              >
                <option value="traditional">传统多仓（逐仓补货）</option>
                <option value="bbcc">BBCC（一盘货：B仓→C仓）</option>
              </select>
            </label>""",
        """        <Group title={t('ch.add')}>
          <div className="form-grid">
            <label className="form-field">
              {t('ch.code_label')}
              <input
                value={form.code}
                onChange={e => setForm({ ...form, code: e.target.value })}
                placeholder={t('ch.code_ph')}
                className="form-input"
              />
            </label>
            <label className="form-field">
              {t('ch.name_label')}
              <input
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder={t('ch.name_ph')}
                className="form-input"
              />
            </label>
            <label className="form-field">
              {t('ch.mode_label')}
              <select
                value={form.default_mode}
                onChange={e => setForm({ ...form, default_mode: e.target.value })}
                className="form-input"
              >
                <option value="traditional">{t('ch.mode_traditional_full')}</option>
                <option value="bbcc">{t('ch.mode_bbcc')}</option>
              </select>
            </label>""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """              走 BBCC 体系（启用 B 仓调拨链）
            </label>
            <label className="form-field">
              复制规则与参数自（可选）
              <select
                value={form.copy_from}
                onChange={e => setForm({ ...form, copy_from: e.target.value })}
                className="form-input"
              >
                <option value="">不复制（空白渠道）</option>""",
        """              {t('ch.supports_b')}
            </label>
            <label className="form-field">
              {t('ch.copy_from')}
              <select
                value={form.copy_from}
                onChange={e => setForm({ ...form, copy_from: e.target.value })}
                className="form-input"
              >
                <option value="">{t('ch.copy_none')}</option>""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """              {saving ? '创建中...' : '创建渠道'}""",
        """              {saving ? t('ch.creating') : t('ch.create')}""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """            <Group title="停用渠道">""",
        """            <Group title={t('ch.group_disable')}>""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """                  label={`禁用 ${c.name}`}""",
        """                  label={t('ch.disable_row', { name: channelName(c) })}""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """                  label={`重新启用 ${c.name}`}""",
        """                  label={t('ch.enable_row', { name: channelName(c) })}""",
    ),
    (
        "frontend/src/pages/ChannelsPage.tsx",
        """import { useAppStore, type Channel } from '../store/useAppStore'""",
        """import { useAppStore, type Channel } from '../store/useAppStore'
import { channelName } from '../components/channels'""",
    ),
]
