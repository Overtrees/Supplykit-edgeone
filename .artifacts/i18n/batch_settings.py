LOCALE = {
    "set.webhook_saved_on": ("已保存，新告警将推送到该地址", "Saved — new alerts will be sent to this address"),
    "set.webhook_saved_off": ("已保存，告警推送已关闭", "Saved — alert push is now off"),
    "set.save_failed": ("保存失败: {msg}", "Save failed: {msg}"),
    "set.recycle_sub": (
        "查看已删除的规则和订单，可恢复或永久删除",
        "View deleted rules and orders — restore or purge permanently",
    ),
    "set.channels": ("渠道管理", "Channels"),
    "set.channels_sub": (
        "新增/禁用渠道（无需发版），可复制既有渠道的规则与参数",
        "Add or disable channels without a release; copy rules and params from an existing one",
    ),
    "set.devmode": ("开发者模式", "Developer mode"),
    "set.devmode_sub": ("版本/构建信息 · 异常日志分析", "Version & build info · error log analysis"),
    "set.version": ("版本号", "Version"),
    "set.devmode_on": ("开发者模式已开启", "Developer mode enabled"),
    "set.devmode_left": ("再点 {n} 次开启开发者模式", "{n} more taps to enable developer mode"),
    "set.interface": ("界面", "Interface"),
    "set.reset_welcome_sub": ("重新显示首次使用引导", "Show the first-run guide again"),
    "set.welcome_reset": ("欢迎页已重置", "Welcome guide reset"),
    "set.alert_push": ("告警推送", "Alert push"),
    "set.webhook": ("Webhook 地址", "Webhook URL"),
    "set.webhook_hint": (
        "钉钉/企业微信机器人地址，新告警每 30 分钟推送到该地址（留空不推送）",
        "DingTalk / WeCom bot URL. New alerts are pushed there every 30 minutes (leave empty to disable)",
    ),
    "set.saving": ("保存中...", "Saving..."),
    "set.footer": ("SupplyKit · 供应链数据工作台", "SupplyKit · Supply chain data workbench"),

    "welcome.tagline1": ("电商供应链数据清洗", "E-commerce supply chain data cleansing"),
    "welcome.tagline2": ("与补货决策看板", "and replenishment decision dashboard"),
    "welcome.seed_exists": (
        "已有数据，请在设置页先「一键重置」",
        "Data already exists — run \u201cReset all\u201d in Settings first",
    ),
    "welcome.seed_running": (
        "种子数据填充中，可前往任务管理查看进度",
        "Seeding in progress — check Tasks for status",
    ),
    "welcome.seed_failed": ("填充失败: {msg}", "Seeding failed: {msg}"),
}

REPL = [
    (
        "frontend/src/pages/SettingsPage.tsx",
        """        toast.success(webhookUrl.trim() ? '已保存，新告警将推送到该地址' : '已保存，告警推送已关闭')
      else toast.error('保存失败: ' + (d.error || ''))
    } catch (e) {
      toast.error('保存失败: ' + e.message)
    }""",
        """        toast.success(
          webhookUrl.trim() ? t('set.webhook_saved_on') : t('set.webhook_saved_off'),
        )
      else toast.error(t('set.save_failed', { msg: d.error || '' }))
    } catch (e) {
      toast.error(t('set.save_failed', { msg: e.message }))
    }""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """        <Group title="操作">
          <LastRow
            label="回收站"
            sub="查看已删除的规则和订单，可恢复或永久删除\"""",
        """        <Group title={t('settings.actions')}>
          <LastRow
            label={t('settings.recycle_bin')}
            sub={t('set.recycle_sub')}""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """          <LastRow
            label="渠道管理"
            sub="新增/禁用渠道（无需发版），可复制既有渠道的规则与参数\"""",
        """          <LastRow
            label={t('set.channels')}
            sub={t('set.channels_sub')}""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """            <LastRow
              label="开发者模式"
              sub="版本/构建信息 · 异常日志分析\"""",
        """            <LastRow
              label={t('set.devmode')}
              sub={t('set.devmode_sub')}""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """        <Group title="系统信息">
          <LastRow
            label="版本号\"""",
        """        <Group title={t('settings.system')}>
          <LastRow
            label={t('set.version')}""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """                toast.success('开发者模式已开启')
              } else if (devTap.current >= 4) {
                toast.info(`再点 ${6 - devTap.current} 次开启开发者模式`)""",
        """                toast.success(t('set.devmode_on'))
              } else if (devTap.current >= 4) {
                toast.info(t('set.devmode_left', { n: 6 - devTap.current }))""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """        <Group title="界面">
          <LastRow
            label="重置欢迎页"
            sub="重新显示首次使用引导\"""",
        """        <Group title={t('set.interface')}>
          <LastRow
            label={t('settings.reset_welcome')}
            sub={t('set.reset_welcome_sub')}""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """              toast.success('欢迎页已重置')""",
        """              toast.success(t('set.welcome_reset'))""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """        <Group title="告警推送">
          <div style={{ padding: 14 }}>
            <div style={{ fontSize: 'var(--font-13)', fontWeight: 600, marginBottom: 4 }}>
              Webhook 地址
            </div>
            <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
              钉钉/企业微信机器人地址，新告警每 30 分钟推送到该地址（留空不推送）
            </div>""",
        """        <Group title={t('set.alert_push')}>
          <div style={{ padding: 14 }}>
            <div style={{ fontSize: 'var(--font-13)', fontWeight: 600, marginBottom: 4 }}>
              {t('set.webhook')}
            </div>
            <div className="small muted" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
              {t('set.webhook_hint')}
            </div>""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """                {webhookSaving ? '保存中...' : '保存'}""",
        """                {webhookSaving ? t('set.saving') : t('common.save')}""",
    ),
    (
        "frontend/src/pages/SettingsPage.tsx",
        """          SupplyKit · 供应链数据工作台""",
        """          {t('set.footer')}""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """            电商供应链数据清洗
            <br />
            与补货决策看板""",
        """            {t('welcome.tagline1')}
            <br />
            {t('welcome.tagline2')}""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """              title: '看数据',
              desc: '多维看板总览',""",
        """              title: t('welcome.dash'),
              desc: t('welcome.dash_desc'),""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """              title: '看补货',
              desc: '补货/采购建议',""",
        """              title: t('welcome.insights'),
              desc: t('welcome.insights_desc'),""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """              title: '导数据',
              desc: '数据清洗导入',""",
        """              title: t('welcome.cleansing'),
              desc: t('welcome.cleansing_desc'),""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """              title: '设规则',
              desc: '规则引擎配置',""",
        """              title: t('welcome.rules'),
              desc: t('welcome.rules_desc'),""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """                  notify.error('已有数据，请在设置页先「一键重置」')""",
        """                  notify.error(t('welcome.seed_exists'))""",
    ),
    (
        "frontend/src/components/Welcome.tsx",
        """                  notify.success('种子数据填充中，可前往任务管理查看进度')
                }
              } else notify.error('填充失败: ' + (d.error || ''))""",
        """                  notify.success(t('welcome.seed_running'))
                }
              } else notify.error(t('welcome.seed_failed', { msg: d.error || '' }))""",
    ),
]
