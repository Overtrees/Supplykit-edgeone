LOCALE = {
    "rfm.suffix_rule": ("规则", " rule"),
    "rfm.severity": ("级别", "Severity"),
    "rfm.sev_warning": ("警告", "Warning"),
    "rfm.sev_info": ("提示", "Info"),
    "rfm.mode": ("补货模式", "Replenishment mode"),
    "rfm.type": ("规则类型", "Rule type"),
    "rfm.type_low_stock": ("低库存(动态安全线)", "Low stock (dynamic safety line)"),
    "rfm.type_stockout": ("濒临断货(看板同源)", "Near stockout (dashboard source)"),
    "rfm.type_health": ("健康监控(看板同源)", "Health monitor (dashboard source)"),
    "rfm.type_oversell": ("超卖保护", "Oversell guard"),
    "rfm.type_slow_moving": ("滞销识别", "Slow-moving detection"),
    "rfm.type_own_gap": ("自有仓·调拨覆盖不足", "Own WH · transfer coverage gap"),
    "rfm.type_own_overstock": ("自有仓·积压", "Own WH · overstock"),
    "rfm.type_own_expiry": ("自有仓·效期风险", "Own WH · expiry risk"),
    "rfm.type_custom": ("自定义", "Custom"),
    "rfm.event": ("触发事件", "Trigger event"),
    "rfm.name_ph": ("例：低库存预警", "e.g. Low stock alert"),
    "rfm.cond_title": ("触发条件", "Trigger condition"),
    "rfm.when": ("当", "When"),
    "rfm.unit_day": ("天", "days"),
    "rfm.unit_piece": ("件", "units"),
    "rfm.unit_yuan": ("元", "¥"),
    "rfm.unit_pct": ("%", "%"),
    "rfm.unit_pct_safety": ("%（安全库存百分比）", "% (of safety stock)"),
    "rfm.pct_of_safety": ("（安全库存的 {pct}%）", "({pct}% of safety stock)"),
    "rfm.then": ("时", ""),
    "rfm.or": ("或", "or"),
    "rfm.add_or": ("＋ 或条件（任一满足触发）", "+ OR condition (any match triggers)"),
    "rfm.alert_title_section": ("告警内容", "Alert content"),
    "rfm.gen_alert": ("生成告警", "Raise alert"),
    "rfm.on": ("开", "On"),
    "rfm.off": ("关", "Off"),
    "rfm.alert_title": ("告警标题", "Alert title"),
    "rfm.alert_desc": ("告警描述", "Alert description"),
    "rfm.var_hint": ("输入文字或点击下方按钮插入变量", "Type text, or tap a variable below to insert"),
    "rfm.var_hint_ph": ("输入文字，点击下方按钮插入变量", "Type text and tap a variable below"),
    "rfm.param_stockout": ("断货计算参数(看板断货卡同源)", "Stockout params (same source as the OOS card)"),
    "rfm.param_health": ("健康分档参数(看板健康卡同源)", "Health params (same source as the health card)"),
    "rfm.param_stockout_hint": (
        "配置后看板断货判定(Adj-DOS/OTIF/动态SS/分级阈值)即时按此计算，停用/删除本规则则回默认值",
        "The dashboard stockout logic (Adj-DOS / OTIF / dynamic SS / tier thresholds) follows these values immediately; disabling or deleting the rule restores defaults",
    ),
    "rfm.param_health_hint": (
        "配置后看板库存健康度分档(good/warning)即时按此计算，停用/删除本规则则回默认值",
        "Dashboard health tiers (good / warning) follow these values immediately; disabling or deleting the rule restores defaults",
    ),
    "rfm.param_own": ("自有仓口径参数(看板同源)", "Own-warehouse params (dashboard source)"),
    "rfm.param_own_gap_hint": (
        "缺口 = 下游日销 × 调拨周期 − 自有可发(在仓+在途+采购在途)；小于阈值不报，避免零头噪音",
        "Gap = downstream daily sales × transfer lead time − own shippable (on hand + transit + PO). Below the floor no alert is raised, avoiding rounding noise",
    ),
    "rfm.param_own_overstock_hint": (
        "可撑天数 = 自有可用 ÷ 下游日销；超过阈值判积压(占用资金与库位)",
        "Days of supply = own available ÷ downstream daily sales. Above the threshold it counts as overstock (tied-up cash and space)",
    ),
    "rfm.param_own_expiry_hint": (
        "距过期天数低于缓冲即报；另含\"按当前消化速度赶不上效期\"的加速预警",
        "Alerts when days to expiry falls under the buffer, plus an acceleration warning when the current burn rate cannot beat the expiry date",
    ),
    "rfm.param_default_ph": ("默认值", "default"),
    "rfm.test": ("测试", "Test"),
    "rfm.saving": ("保存中...", "Saving..."),

    "p.lit": ("补货周期 L/T(天, 留空=按模式自动)", "Lead time L/T (days, empty = by mode)"),
    "p.otif_min": ("OTIF 置信下限(0.6~1)", "OTIF lower bound (0.6–1)"),
    "p.ss_z": ("动态SS Z值(1.28/1.65/2.33)", "Dynamic SS Z (1.28 / 1.65 / 2.33)"),
    "p.sigma_days": ("波动率σ样本天数(默认60, 与断货卡同源)", "Volatility σ sample days (default 60, same as OOS card)"),
    "p.accel_ratio": ("加速倍率阈值(默认1.3)", "Acceleration ratio threshold (default 1.3)"),
    "p.accel_min_qty": ("加速样本量(默认10)", "Acceleration sample size (default 10)"),
    "p.buffer_orange": ("橙灯缓冲上限(默认1.2)", "Orange buffer ceiling (default 1.2)"),
    "p.buffer_yellow": ("黄灯缓冲上限(默认1.0)", "Yellow buffer ceiling (default 1.0)"),
    "p.orange_slack_days": ("橙灯天数余量(默认1)", "Orange days margin (default 1)"),
    "p.include_avail_zero": ("已断纳入红灯 1/0", "Count out-of-stock as red 1/0"),
    "p.log": ("审计日志 1/0(触发写 quality_logs)", "Audit log 1/0 (writes to quality_logs on trigger)"),
    "p.health_good": ("健康档位线(默认85)", "Healthy tier line (default 85)"),
    "p.health_warning": ("预警档位线(默认60)", "Warning tier line (default 60)"),
    "p.transfer_gap_min": ("缺口下限(件, 低于不报, 默认1)", "Gap floor (units, below which no alert; default 1)"),
    "p.b2c_days": ("调拨周期(天, 留空=按补货模式取 B→C+缓冲)", "Transfer lead time (days, empty = B→C + buffer by mode)"),
    "p.own_overstock_days": ("积压天数阈值(默认90)", "Overstock days threshold (default 90)"),
    "p.own_expiry_buffer_days": ("效期缓冲(天, 默认15)", "Expiry buffer (days, default 15)"),
}

REPL = [
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """        {editing.id ? t('rules.edit') : t('rules.new_btn') + '规则'}""",
        """        {editing.id ? t('rules.edit') : t('rules.new_btn') + t('rfm.suffix_rule')}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            placeholder="例：低库存预警"
          />
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          级别
          <div className="hammer-segmented" style={{ marginTop: 4, flexShrink: 0 }}>
            {[
              { v: 'warning', t: '警告', c: 'var(--warning)' },
              { v: 'error', t: t('rules.severity_error'), c: 'var(--danger)' },
              { v: 'info', t: '提示', c: 'var(--primary)' },
            ].map(({ v, t, c }) => (""",
        """            placeholder={t('rfm.name_ph')}
          />
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          {t('rfm.severity')}
          <div className="hammer-segmented" style={{ marginTop: 4, flexShrink: 0 }}>
            {[
              { v: 'warning', lb: t('rfm.sev_warning'), c: 'var(--warning)' },
              { v: 'error', lb: t('rules.severity_error'), c: 'var(--danger)' },
              { v: 'info', lb: t('rfm.sev_info'), c: 'var(--primary)' },
            ].map(({ v, lb, c }) => (""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                {t}
              </span>
            ))}
          </div>
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          补货模式""",
        """                {lb}
              </span>
            ))}
          </div>
        </label>
        <label style={{ fontSize: 'var(--font-sm)' }}>
          {t('rfm.mode')}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """        <label style={{ fontSize: 'var(--font-sm)' }}>
          规则类型""",
        """        <label style={{ fontSize: 'var(--font-sm)' }}>
          {t('rfm.type')}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            <option value="low_stock">低库存(动态安全线)</option>
            <option value="stockout">濒临断货(看板同源)</option>
            <option value="health">健康监控(看板同源)</option>
            <option value="oversell">超卖保护</option>
            <option value="slow_moving">滞销识别</option>""",
        """            <option value="low_stock">{t('rfm.type_low_stock')}</option>
            <option value="stockout">{t('rfm.type_stockout')}</option>
            <option value="health">{t('rfm.type_health')}</option>
            <option value="oversell">{t('rfm.type_oversell')}</option>
            <option value="slow_moving">{t('rfm.type_slow_moving')}</option>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            <option value="own_transfer_gap">自有仓·调拨覆盖不足</option>
            <option value="own_overstock">自有仓·积压</option>
            <option value="own_expiry_risk">自有仓·效期风险</option>
            <option value="custom">自定义</option>""",
        """            <option value="own_transfer_gap">{t('rfm.type_own_gap')}</option>
            <option value="own_overstock">{t('rfm.type_own_overstock')}</option>
            <option value="own_expiry_risk">{t('rfm.type_own_expiry')}</option>
            <option value="custom">{t('rfm.type_custom')}</option>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """        <label style={{ fontSize: 'var(--font-sm)' }}>
          触发事件""",
        """        <label style={{ fontSize: 'var(--font-sm)' }}>
          {t('rfm.event')}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                {ev.label}
              </option>""",
        """                {t(ev.label)}
              </option>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """          <IconScale size={14} /> 触发条件
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="text-14 font-500">当</span>""",
        """          <IconScale size={14} /> {t('rfm.cond_title')}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="text-14 font-500">{t('rfm.when')}</span>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """              {cond.left === 'days_since_last'
                ? '天'
                : cond.left === 'inv.available_qty'
                  ? '%（安全库存百分比）'
                  : cond.left === 'order.quantity'
                    ? '件'
                    : cond.left === 'order.total_amount'
                      ? '元'
                      : '%'}
            </span>""",
        """              {cond.left === 'days_since_last'
                ? t('rfm.unit_day')
                : cond.left === 'inv.available_qty'
                  ? t('rfm.unit_pct_safety')
                  : cond.left === 'order.quantity'
                    ? t('rfm.unit_piece')
                    : cond.left === 'order.total_amount'
                      ? t('rfm.unit_yuan')
                      : t('rfm.unit_pct')}
            </span>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """          当 <b>{whLbl(cond.warehouse)}</b>{' '}
          <b>{fieldLbl(cond.left)}</b> {opLbl(cond.op)}{' '}
          <b>
            {cond.pctValue || 0}
            {cond.left === 'days_since_last'
              ? '天'
              : cond.left === 'inv.available_qty'
                ? '%'
                : '件'}
          </b>
          {cond.left === 'inv.available_qty' ? (
            <span style={{ color: 'var(--muted2)', fontSize: 'var(--font-xs)' }}>
              （安全库存的 {cond.pctValue || 0}%）
            </span>
          ) : (
            ''
          )}
          时
          {(cond.or || []).map((o, i) => (
            <span key={i} style={{ color: 'var(--primary)' }}>
              {' '}
              <b>或</b> {whLbl(o.warehouse)} <b>{fieldLbl(o.left)}</b>{' '}
              {opLbl(o.op)} <b>{o.right}</b>
            </span>
          ))}""",
        """          {t('rfm.when')} <b>{whLbl(cond.warehouse)}</b> <b>{fieldLbl(cond.left)}</b>{' '}
          {opLbl(cond.op)}{' '}
          <b>
            {cond.pctValue || 0}
            {cond.left === 'days_since_last'
              ? t('rfm.unit_day')
              : cond.left === 'inv.available_qty'
                ? t('rfm.unit_pct')
                : t('rfm.unit_piece')}
          </b>
          {cond.left === 'inv.available_qty' ? (
            <span style={{ color: 'var(--muted2)', fontSize: 'var(--font-xs)' }}>
              {t('rfm.pct_of_safety', { pct: cond.pctValue || 0 })}
            </span>
          ) : (
            ''
          )}
          {t('rfm.then')}
          {(cond.or || []).map((o, i) => (
            <span key={i} style={{ color: 'var(--primary)' }}>
              {' '}
              <b>{t('rfm.or')}</b> {whLbl(o.warehouse)} <b>{fieldLbl(o.left)}</b> {opLbl(o.op)}{' '}
              <b>{o.right}</b>
            </span>
          ))}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            <span className="text-13 font-600" style={{ color: 'var(--primary)' }}>
              或
            </span>""",
        """            <span className="text-13 font-600" style={{ color: 'var(--primary)' }}>
              {t('rfm.or')}
            </span>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """              placeholder={o.right === 'inv.safety_qty' ? '动态安全线' : ''}""",
        """              placeholder={o.right === 'inv.safety_qty' ? t('rf.vars.safety') : ''}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """          ＋ 或条件（任一满足触发）
        </button>""",
        """          {t('rfm.add_or')}
        </button>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """          <IconAlert size={14} /> 告警内容
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span className="muted2" style={{ fontSize: 'var(--font-10)' }}>
              生成告警
            </span>""",
        """          <IconAlert size={14} /> {t('rfm.alert_title_section')}
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span className="muted2" style={{ fontSize: 'var(--font-10)' }}>
              {t('rfm.gen_alert')}
            </span>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                开
              </span>""",
        """                {t('rfm.on')}
              </span>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                关
              </span>""",
        """                {t('rfm.off')}
              </span>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            告警标题
            <div""",
        """            {t('rfm.alert_title')}
            <div""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                <span className="muted" style={{ fontSize: 'var(--font-sm)' }}>
                  输入文字或点击下方按钮插入变量
                </span>
              )}
            </div>
            <input
              value={f.alert_title}""",
        """                <span className="muted" style={{ fontSize: 'var(--font-sm)' }}>
                  {t('rfm.var_hint')}
                </span>
              )}
            </div>
            <input
              value={f.alert_title}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            告警描述""",
        """            {t('rfm.alert_desc')}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                <span className="muted" style={{ fontSize: 'var(--font-sm)' }}>
                  输入文字或点击下方按钮插入变量
                </span>
              )}
            </div>
            <input
              value={f.alert_desc}""",
        """                <span className="muted" style={{ fontSize: 'var(--font-sm)' }}>
                  {t('rfm.var_hint')}
                </span>
              )}
            </div>
            <input
              value={f.alert_desc}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            {f.alert_type === 'stockout'
              ? '断货计算参数(看板断货卡同源)'
              : '健康分档参数(看板健康卡同源)'}
          </div>
          <div className="muted2" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
            {f.alert_type === 'stockout'
              ? '配置后看板断货判定(Adj-DOS/OTIF/动态SS/分级阈值)即时按此计算，停用/删除本规则则回默认值'
              : '配置后看板库存健康度分档(good/warning)即时按此计算，停用/删除本规则则回默认值'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {(f.alert_type === 'stockout'
              ? [
                  ['lit', '补货周期 L/T(天, 留空=按模式自动)'],
                  ['otif_min', 'OTIF 置信下限(0.6~1)'],
                  ['ss_z', '动态SS Z值(1.28/1.65/2.33)'],
                  ['sigma_days', '波动率σ样本天数(默认60, 与断货卡同源)'],
                  ['accel_ratio', '加速倍率阈值(默认1.3)'],
                  ['accel_min_qty', '加速样本量(默认10)'],
                  ['buffer_orange', '橙灯缓冲上限(默认1.2)'],
                  ['buffer_yellow', '黄灯缓冲上限(默认1.0)'],
                  ['orange_slack_days', '橙灯天数余量(默认1)'],
                  ['include_avail_zero', '已断纳入红灯 1/0'],
                  ['log', '审计日志 1/0(触发写 quality_logs)'],
                ]
              : [
                  ['health_good', '健康档位线(默认85)'],
                  ['health_warning', '预警档位线(默认60)'],
                  ['log', '审计日志 1/0(触发写 quality_logs)'],
                ]
            ).map(([k, l]) => (
              <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                {l}""",
        """            {f.alert_type === 'stockout' ? t('rfm.param_stockout') : t('rfm.param_health')}
          </div>
          <div className="muted2" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
            {f.alert_type === 'stockout'
              ? t('rfm.param_stockout_hint')
              : t('rfm.param_health_hint')}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {(f.alert_type === 'stockout'
              ? [
                  ['lit', 'p.lit'],
                  ['otif_min', 'p.otif_min'],
                  ['ss_z', 'p.ss_z'],
                  ['sigma_days', 'p.sigma_days'],
                  ['accel_ratio', 'p.accel_ratio'],
                  ['accel_min_qty', 'p.accel_min_qty'],
                  ['buffer_orange', 'p.buffer_orange'],
                  ['buffer_yellow', 'p.buffer_yellow'],
                  ['orange_slack_days', 'p.orange_slack_days'],
                  ['include_avail_zero', 'p.include_avail_zero'],
                  ['log', 'p.log'],
                ]
              : [
                  ['health_good', 'p.health_good'],
                  ['health_warning', 'p.health_warning'],
                  ['log', 'p.log'],
                ]
            ).map(([k, l]) => (
              <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                {t(l)}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                  placeholder="默认值"
                />
              </label>
            ))}
          </div>
        </div>
      )}""",
        """                  placeholder={t('rfm.param_default_ph')}
                />
              </label>
            ))}
          </div>
        </div>
      )}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """            <IconGear size={13} style={{ verticalAlign: -2 }} /> 自有仓口径参数(看板同源)
          </div>
          <div className="muted2" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
            {f.alert_type === 'own_transfer_gap'
              ? '缺口 = 下游日销 × 调拨周期 − 自有可发(在仓+在途+采购在途)；小于阈值不报，避免零头噪音'
              : f.alert_type === 'own_overstock'
                ? '可撑天数 = 自有可用 ÷ 下游日销；超过阈值判积压(占用资金与库位)'
                : '距过期天数低于缓冲即报；另含"按当前消化速度赶不上效期"的加速预警'}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {(f.alert_type === 'own_transfer_gap'
              ? [
                  ['transfer_gap_min', '缺口下限(件, 低于不报, 默认1)'],
                  ['b2c_days', '调拨周期(天, 留空=按补货模式取 B→C+缓冲)'],
                ]
              : f.alert_type === 'own_overstock'
                ? [['own_overstock_days', '积压天数阈值(默认90)']]
                : [['own_expiry_buffer_days', '效期缓冲(天, 默认15)']]
            ).map(([k, l]) => (
              <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                {l}""",
        """            <IconGear size={13} style={{ verticalAlign: -2 }} /> {t('rfm.param_own')}
          </div>
          <div className="muted2" style={{ fontSize: 'var(--font-xs)', marginBottom: 8 }}>
            {f.alert_type === 'own_transfer_gap'
              ? t('rfm.param_own_gap_hint')
              : f.alert_type === 'own_overstock'
                ? t('rfm.param_own_overstock_hint')
                : t('rfm.param_own_expiry_hint')}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {(f.alert_type === 'own_transfer_gap'
              ? [
                  ['transfer_gap_min', 'p.transfer_gap_min'],
                  ['b2c_days', 'p.b2c_days'],
                ]
              : f.alert_type === 'own_overstock'
                ? [['own_overstock_days', 'p.own_overstock_days']]
                : [['own_expiry_buffer_days', 'p.own_expiry_buffer_days']]
            ).map(([k, l]) => (
              <label key={k} style={{ fontSize: 'var(--font-sm)' }}>
                {t(l)}""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """                  placeholder="默认值"
                />
              </label>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>""",
        """                  placeholder={t('rfm.param_default_ph')}
                />
              </label>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """          <IconLightning size={14} /> 测试
        </button>""",
        """          <IconLightning size={14} /> {t('rfm.test')}
        </button>""",
    ),
    (
        "frontend/src/pages/rules/EditForm.tsx",
        """              <IconLoading size={14} /> 保存中...""",
        """              <IconLoading size={14} /> {t('rfm.saving')}""",
    ),
]
