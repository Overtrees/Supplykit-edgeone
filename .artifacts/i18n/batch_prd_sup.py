LOCALE = {
    "prd.title": ("商品管理", "Products"),
    "prd.loaded": ("已加载 {a}/{b} 条 · 显示 {c}/{d} 列", "Loaded {a}/{b} · {c}/{d} columns"),
    "prd.try_other_kw": ("换个关键词试试", "Try another keyword"),
    "prd.import_hint": ("通过清洗页导入商品数据", "Import products from Data Import"),
    "prd.status_active": ("在售", "On sale"),
    "prd.status_inactive": ("停用", "Disabled"),
    "prd.em_yes": ("是", "Yes"),
    "prd.em_no": ("否", "No"),
    "prd.em_unset": ("未标注", "Not set"),
    "prd.em_yes_tip": (
        "出库按批次精确匹配, 缺失走 FEFO 降级",
        "Outbound matches batches exactly; falls back to FEFO when missing",
    ),
    "prd.em_no_tip": (
        "出库按到货顺序 FIFO, 批次不记日期",
        "Outbound follows arrival order (FIFO); batches carry no date",
    ),
    "prd.em_unset_tip": (
        '未标注 → 出入库按"做效期"处理并告警; 请在清洗页导入商品表补标',
        'Not set → in/outbound is treated as expiry-managed and raises alerts; set it in the product import',
    ),
    "prd.unit_default": ("瓶", "btl"),
    "sup.title": ("供应商管理", "Suppliers"),
    "sup.loaded": ("已加载 {a}/{b} 条 · 显示 {c}/{d} 列", "Loaded {a}/{b} · {c}/{d} columns"),
    "sup.try_other_kw": ("换个关键词试试", "Try another keyword"),
    "sup.import_hint": ("通过清洗页导入供应商数据", "Import suppliers from Data Import"),
    "common.all_loaded": ("已加载全部 {n} 条", "All {n} rows loaded"),
    "common.go_import": ("去导入数据 →", "Go to import →"),
    "inv.channel_other_short": ("其他", "Other"),
    "inv.channel_jd_short": ("京东", "JD"),
}

REPL = [
    # ── ProductPage ──
    (
        "frontend/src/pages/ProductPage.tsx",
        """          商品管理{' '}
          <span className="small muted" style={{ fontSize: 'var(--font-xs)', fontWeight: 400 }}>
            已加载 {Math.min(list.length, pgTotal || list.length)}/{pgTotal || list.length} 条 ·
            显示 {visCols.length}/{COLS.length} 列{s ? ` · "${s}"` : ''}""",
        """          {t('prd.title')}{' '}
          <span className="small muted" style={{ fontSize: 'var(--font-xs)', fontWeight: 400 }}>
            {t('prd.loaded', {
              a: Math.min(list.length, pgTotal || list.length),
              b: pgTotal || list.length,
              c: visCols.length,
              d: COLS.length,
            })}
            {s ? ` · "${s}"` : ''}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """            desc={s ? '换个关键词试试' : '通过清洗页导入商品数据'}""",
        """            desc={s ? t('prd.try_other_kw') : t('prd.import_hint')}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                  去导入数据 →""",
        """                  {t('common.go_import')}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                          {useAppStore.getState().channel === 'other' ? '其他' : '京东'}""",
        """                          {useAppStore.getState().channel === 'other'
                            ? t('inv.channel_other_short')
                            : t('inv.channel_jd_short')}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                          {x.unit || '瓶'}""",
        """                          {x.unit || t('prd.unit_default')}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                            {x.status === 'active' ? '在售' : '停用'}""",
        """                            {x.status === 'active' ? t('prd.status_active') : t('prd.status_inactive')}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                      const _map =
                        _em === 'yes'
                          ? { txt: '是', cls: 'success' }
                          : _em === 'no'
                            ? { txt: '否', cls: 'neutral' }
                            : { txt: '未标注', cls: 'warning' }""",
        """                      const _map =
                        _em === 'yes'
                          ? { txt: t('prd.em_yes'), cls: 'success', tip: t('prd.em_yes_tip') }
                          : _em === 'no'
                            ? { txt: t('prd.em_no'), cls: 'neutral', tip: t('prd.em_no_tip') }
                            : { txt: t('prd.em_unset'), cls: 'warning', tip: t('prd.em_unset_tip') }""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                            title={
                              _em === 'yes'
                                ? '出库按批次精确匹配, 缺失走 FEFO 降级'
                                : _em === 'no'
                                  ? '出库按到货顺序 FIFO, 批次不记日期'
                                  : '未标注 → 出入库按"做效期"处理并告警; 请在清洗页导入商品表补标'
                            }""",
        """                            title={_map.tip}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """                {loadingMore ? '加载中... ' : ''}({Math.min(list.length, pgTotal)}/{pgTotal})""",
        """                {loadingMore ? t('common.loading') : ''}({Math.min(list.length, pgTotal)}/
                {pgTotal})""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """              已加载全部 {pgTotal} 条""",
        """              {t('common.all_loaded', { n: pgTotal })}""",
    ),
    (
        "frontend/src/pages/ProductPage.tsx",
        """        setLoadErr('加载失败')""",
        """        setLoadErr(t('common.load_failed'))""",
    ),
    # ── SupplierPage ──
    (
        "frontend/src/pages/SupplierPage.tsx",
        """          供应商管理{' '}
          <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            已加载 {list.length}/{list.length} 条 · 显示 {visCols.length}/{COLS.length} 列""",
        """          {t('sup.title')}{' '}
          <span className="small muted" style={{ fontSize: 'var(--font-xs)' }}>
            {t('sup.loaded', {
              a: list.length,
              b: list.length,
              c: visCols.length,
              d: COLS.length,
            })}""",
    ),
    (
        "frontend/src/pages/SupplierPage.tsx",
        """            desc={s ? '换个关键词试试' : '通过清洗页导入供应商数据'}""",
        """            desc={s ? t('sup.try_other_kw') : t('sup.import_hint')}""",
    ),
    (
        "frontend/src/pages/SupplierPage.tsx",
        """                  去导入数据 →""",
        """                  {t('common.go_import')}""",
    ),
    (
        "frontend/src/pages/SupplierPage.tsx",
        """      setLoadErr('加载失败')""",
        """      setLoadErr(t('common.load_failed'))""",
    ),
]
