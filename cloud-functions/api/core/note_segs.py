"""结构化备注片段(note_segs)——中转层: 后端只产出「键+参数」, 文案由前端 i18n 渲染。

为什么存在(2026-11-04, 用户指令):
  补货备注/采购建议原先是后端直接拼的中文句子("7天走弱 · B建议补120.0件(…)"),
  数字和文案焊死在一个字符串里 —— 英文界面只能显示中文。改成结构化片段后:
    · 后端产出 segs = [{"k": "b_sug", "n": 120, ...}, ...] (键是稳定契约, 参数是纯数字)
    · 前端 `t('seg.' + k, params)` 出双语
    · 兼容: `note`(中文字符串)**继续产出且永不删除** —— Excel 导出/历史快照/告警文本
      仍吃 note, 老数据/老消费方零影响

一致性纪律: note 必须由 segs 经 zh_note() 派生(**单一真源**), 禁止再手写一份平行字符串
——否则两份文案迟早漂移(历史教训: FUNNEL_STAGES 之前两处各写一份, 漏了"申请退款")。
zh 模板要求与重构前的拼串逐字一致(已逐条对照, 见各 seg 注释)。
"""

# 中文模板(k → zh 文案)。%s/%d 参数顺序 = seg dict 里 params 的取值顺序(见 ZH_ARGS)。
_ZH = {
    # ---- 趋势(补货 bbcc/传统共用) ----
    # 原: "近7%s 近14%s" % (t7, t14) + 可选后缀
    "trend": "近7{t7} 近14{t14}",
    "trend_x_very_low": "销量极低，库存积压",
    "trend_x_down14": "持续下行（近14天无销量）",
    "trend_x_up": "持续上行",
    "trend_x_down": "持续下行",
    "trend_x_up7": "7天抬头",
    "trend_x_down7": "7天走弱",
    "trend_x_flat": "平稳",
    # ---- 补货 bbcc ----
    "c_sug": "C建议补{n}件",
    "b_sug": "B建议补{n}件(C缺口{c_gap}→B缺口{b_gap},调拨消耗{consume},箱规{box})",
    "b_empty": "B仓已空",
    "b_low": "B仓仅{n}件需从自有仓调",
    "unecon": "⚠️ 缺口不足1件，凑整箱不划算，暂不补",
    "b_fee_over": "🔴 超{n}天免费期有仓储费",
    "b_fee_near": "⚠️ 接近{n}天免费期",
    "no_sales_b": "近30天无销量，B仓库存积压",
    "no_sales_c": "近30天无销量，C仓库存积压",
    "no_sales": "⚪ 近30天无销量",
    "safety_bc_urgent": "现BC合计低于动态安全线：可撑{d}天<周期{p}天，需尽快补到B仓",
    "safety_bc_near": "⚠️ 接近动态安全线：约剩{d}天（周期{p}天）",
    # ---- 补货 传统逐仓 ----
    "need": "需补{n}件",
    "unecon_t": "⚠️ 缺口不足1件，凑整箱不经济，暂不补",
    "safety_urgent": "🔴 库存告急：只够卖{d}天（补货需{p}天），将断货，尽快补货",
    "safety_near": "⚠️ 约剩{d}天（补货周期{p}天），建议备货",
    "no_sales_stock": "🔴 近30天无销量，库存积压",
    # ---- 周转红线(bbcc/传统共用) ----
    "turn_over": "🔴 {w}{v}天超{t}天",
    "turn_near": "⚠️ {w}{v}天接近{t}天",
    # ---- 通用 ----
    "critical": "🔴 已濒临",
    "ok": "库存充足",
    # ---- 采购建议 ----
    "p_urgent": "🔴 库存告急：只够卖{d}天（采购需{p}天），将断货",
    "p_near": "⚠️ 约剩{d}天（采购周期{p}天），建议备货",
    "p_need": "需采购{n}件",
    "p_need_box": "需采购{n}件（箱规{box}件，实购{act}件）",
    "p_turn": "补后周转{v}天{cmp}目标{target}天",
    "p_moq": "供应商{sup}起订{moq}件, 该供应商合计{tot}件不足, 按占比{old}/{tot}提升至{new}件",
}

# 周转 seg 的 w 参数取值 → 中文(补后综转/当前综转)
_W_ZH = {"after": "补后综转", "cur": "当前综转"}
# 趋势方向 → emoji(所有语言通用, 不进词条)
_T_EMOJI = {"up": "📈", "down": "📉", "flat": "➡️"}
# trend 的 x 后缀键映射到 _ZH 里的 trend_x_*
_X_KEY = "trend_x_%s"


def _fmt(tpl, seg):
    """{name} 占位符填充(缺参数原样保留占位——显式暴露 bug 而不是静默出空串)"""
    out = tpl
    for k, v in (seg or {}).items():
        if k == "k":
            continue
        out = out.replace("{%s}" % k, str(v))
    return out


def zh_note(segs):
    """segs → 中文字符串(· 连接)。note 字段的唯一合法来源。"""
    parts = []
    for seg in (segs or []):
        k = seg.get("k") or ""
        if k == "trend":
            t = _ZH["trend"].format(t7=_T_EMOJI.get(seg.get("t7"), "➡️"),
                                    t14=_T_EMOJI.get(seg.get("t14"), "➡️"))
            x = seg.get("x")
            if x:
                t += " " + _ZH.get(_X_KEY % x, "")
            parts.append(t)
        elif k in ("turn_over", "turn_near"):
            parts.append(_ZH[k].format(w=_W_ZH.get(seg.get("w"), ""),
                                       v=seg.get("v"), t=seg.get("t")))
        elif k in _ZH:
            parts.append(_fmt(_ZH[k], seg))
        # 未知键: 静默跳过(不该发生; 前端同理回落键名, 后端 note 不受影响)
    return " · ".join(parts) if parts else _ZH["ok"]
