LOCALE = {
    "rules.select": ("选择", "Select"),
    "rules.no_match": ("没有匹配\"{q}\"的规则", "No rules matching \"{q}\""),
    "rules.empty_desc": ("创建规则后可自动监控库存异常", "Rules monitor inventory anomalies automatically once created"),
    "rules.new_plus": ("+ 新建规则", "+ New rule"),
}

REPL = [
    (
        "frontend/src/pages/rules/List.tsx",
        """<span style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)' }}>选择</span>""",
        """<span style={{ fontSize: 'var(--font-sm)', color: 'var(--muted2)' }}>
                  {t('rules.select')}
                </span>""",
    ),
    (
        "frontend/src/pages/rules/List.tsx",
        """            title={rules.length === 0 ? t('rules.empty') : '没有匹配"' + hammerSearch + '"的规则'}
            desc={rules.length === 0 ? '创建规则后可自动监控库存异常' : undefined}""",
        """            title={
              rules.length === 0
                ? t('rules.empty')
                : t('rules.no_match', { q: hammerSearch })
            }
            desc={rules.length === 0 ? t('rules.empty_desc') : undefined}""",
    ),
    (
        "frontend/src/pages/rules/List.tsx",
        """                  + 新建规则""",
        """                  {t('rules.new_plus')}""",
    ),
]
