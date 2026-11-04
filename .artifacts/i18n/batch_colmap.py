LOCALE = {
    "cm.title": ("表格列状态映射", "Column value mapping"),
    "cm.desc": (
        "按列配置 值→档位，导入时自动归一化 · 渠道：{ch}（全局主体隔离） 计入销量池=日销/GMV 都算；仅订单记录=写订单可见，GMV 按状态（已发货类算），不计日销补货",
        "Map each column's values to a tier; normalized on import · Channel: {ch} (isolated per entity) In sales pool = counted in daily sales and GMV; Record only = visible in orders, GMV by status (shipped-like counts), excluded from daily sales and replenishment",
    ),
    "cm.ch_jd": ("京东", "JD"),
    "cm.ch_other": ("其他渠道", "Other channels"),
    "cm.map_col": ("映射列", "Column"),
    "cm.opt_order_status": ("订单状态（销量池判定）", "Order status (sales pool rule)"),
    "cm.empty": (
        "还没有配置：点击「补全内置默认」快速生成，或「+ 添加值」手动添加",
        "Nothing configured yet — tap \u201cFill built-in defaults\u201d or \u201c+ Add value\u201d",
    ),
    "cm.val_ph": ("值(如 交易成功)", "Value (e.g. 交易成功)"),
    "cm.val_dup": ("值重复：导入归一化时后者覆盖前者", "Duplicate value — the later one wins during normalization"),
    "cm.grp_sale": ("销量池", "Sales pool"),
    "cm.grp_sale_tip": ("计入销量池（日销+GMV）", "Count in sales pool (daily sales + GMV)"),
    "cm.grp_blocked": ("仅记录", "Record only"),
    "cm.grp_blocked_tip": (
        "仅订单记录（GMV 按状态，不计日销）",
        "Order record only (GMV by status, excluded from daily sales)",
    ),
    "cm.add_val": ("+ 添加值", "+ Add value"),
    "cm.fill_defaults": ("补全内置默认", "Fill built-in defaults"),
    "cm.all_sale": ("全部销量池", "All to sales pool"),
    "cm.all_blocked": ("全部仅记录", "All to record only"),
    "cm.saving": ("保存中...", "Saving..."),
}

REPL = [
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """            表格列状态映射
          </div>""",
        """            {t('cm.title')}
          </div>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """            按列配置 值→档位，导入时自动归一化 · 渠道：{channel === 'jd' ? '京东' : '其他渠道'}
            （全局主体隔离） 计入销量池=日销/GMV 都算；仅订单记录=写订单可见，GMV
            按状态（已发货类算），不计日销补货""",
        """            {t('cm.desc', {
              ch: channel === 'jd' ? t('cm.ch_jd') : t('cm.ch_other'),
            })}""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              映射列
            </span>""",
        """              {t('cm.map_col')}
            </span>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              <option value="order_status">订单状态（销量池判定）</option>""",
        """              <option value="order_status">{t('cm.opt_order_status')}</option>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              还没有配置：点击「补全内置默认」快速生成，或「+ 添加值」手动添加""",
        """              {t('cm.empty')}""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                placeholder="值(如 交易成功)\"""",
        """                placeholder={t('cm.val_ph')}""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                    ? '值重复：导入归一化时后者覆盖前者'""",
        """                    ? t('cm.val_dup')""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                      label: '销量池',
                      tip: '计入销量池（日销+GMV）',""",
        """                      label: 'cm.grp_sale',
                      tip: 'cm.grp_sale_tip',""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                      label: '仅记录',
                      tip: '仅订单记录（GMV 按状态，不计日销）',""",
        """                      label: 'cm.grp_blocked',
                      tip: 'cm.grp_blocked_tip',""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                      title={tip}""",
        """                      title={t(tip)}""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              + 添加值
            </button>""",
        """              {t('cm.add_val')}
            </button>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              补全内置默认
            </button>""",
        """              {t('cm.fill_defaults')}
            </button>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              全部销量池
            </button>""",
        """              {t('cm.all_sale')}
            </button>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              全部仅记录
            </button>""",
        """              {t('cm.all_blocked')}
            </button>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """              <span style={{ fontSize: 'var(--font-15)', fontWeight: 600 }}>取消</span>""",
        """              <span style={{ fontSize: 'var(--font-15)', fontWeight: 600 }}>
                {t('common.cancel')}
              </span>""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                {colMapSaving ? '保存中...' : '保存'}""",
        """                {colMapSaving ? t('cm.saving') : t('common.save')}""",
    ),
    (
        "frontend/src/pages/cleansing/ColMapSheet.tsx",
        """                  {label}""",
        """                  {t(label)}""",
    ),
]