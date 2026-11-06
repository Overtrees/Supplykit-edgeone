"""轻量规则引擎(方案 B, 从 PA backend/app/core/rules.py 移植, TiDB 适配)

定义 → 评估 → 动作:
- condition_json: {left, op, right, warehouse?, and?: {...}}
  left/right 支持字段引用(inv.available_qty)与四则运算/括号/max()
- evaluate(event, context): 匹配 active 规则(event+channel+mode) → 条件满足 → _action_create_alert(去重)
"""
import json

from db import query, one, execute, executemany
from routes.common import try_err
from core.rule_eval import evaluate_stock_skus, close_oversell_for_skus, close_orphan_for_skus  # 拆分 re-export(2026-10-15)
from core.rule_cond import _resolve_single, _resolve_muldiv, _resolve_value, _resolve_any, _check_single, _check_condition

# 告警写入的唯一语句(2026-11-06 S2-4): 依赖 alerts 的 uq_alerts_dim
#   (channel, alert_type, related_sku, barcode, warehouse, source)。
#   语义 = "这个维度当前是否在告警": 已 active 只刷新展示字段; 已关闭则重开并计数。
#
# ⚠ 赋值顺序不能改: `reopen_count` / `created_at` 必须排在 `status='active'` **之前**。
#   MySQL/TiDB 的 ON DUPLICATE KEY UPDATE 是**从左到右**执行赋值的, 后面的表达式
#   读到的是**已被前面赋过值**的列。若先写 `status='active'`, 后面的
#   `IF(status<>'active',1,0)` 永远为 0 —— 重开次数再也不增长, 且 created_at 不刷新,
#   "抖了多久"就彻底查不出来了。
_ALERT_UPSERT_SQL = (
    "INSERT INTO alerts(alert_type, title, description, severity, status, source, "
    "related_sku, related_rule_id, warehouse_type, warehouse, channel, barcode) "
    "VALUES(%s,%s,%s,%s,'active','rules_engine',%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "reopen_count=COALESCE(reopen_count,0)+IF(status<>'active',1,0), "
    "created_at=IF(status<>'active', NOW(), created_at), "
    "title=VALUES(title), description=VALUES(description), severity=VALUES(severity), "
    "related_rule_id=VALUES(related_rule_id), warehouse_type=VALUES(warehouse_type), "
    "closed_reason='', closed_at=NULL, status='active'"
)














def _action_create_alert(ctx):
    """生成告警(去重: alert_type+related_sku+warehouse+status+channel+source=rules_engine)

    逐仓粒度: 规则按库存行(SKU×仓)评估, 去重 key 含 warehouse —— 传统多仓下每个仓的
    低库存/补货风险独立成条, 弹窗/预览按数据 warehouse 列显示实际仓名
    """
    try:
        rule = ctx["rule"]
        sku = ctx.get("sku", "")
        channel = ctx.get("channel", "jd")
        wh = (ctx.get("inv") or {}).get("warehouse", "")
        title_tpl = rule.get("alert_title", "") or ""
        desc_tpl = rule.get("alert_desc", "") or ""
        try:
            title = title_tpl.format(**ctx) if "{" in title_tpl else title_tpl
            desc = desc_tpl.format(**ctx) if "{" in desc_tpl else desc_tpl
        except Exception:
            title, desc = title_tpl, desc_tpl
        # 2026-11-03 D8: 去重 + 重开 收敛到生命周期入口(同维度已关 → 重开, 不新增行)
        from core.alert_lifecycle import open_or_reopen as _oor
        _oor({"alert_type": rule.get("alert_type", ""), "related_sku": sku, "warehouse": wh,
              "channel": channel, "barcode": str(ctx.get("barcode") or "")},
             {"title": title, "description": desc, "severity": rule.get("severity", "warning"),
              "source": "rules_engine", "related_rule_id": int(rule.get("id") or 0),
              "warehouse_type": (ctx.get("inv") or {}).get("warehouse_type", "")})
    except Exception as _e:
            try_err('core_rules', '静默降级', _e)


def evaluate(event, context):
    """单条评估(兼容): 内部转批量"""
    return evaluate_many(event, [context], context.get("channel"))


def evaluate_many(event, contexts, channel=None, rule_cache=None, return_hits=False):
    """批量评估同事件上下文(性能版): 规则一次加载 + 告警去重预载 + executemany 批量插入

    避免逐条 evaluate 的 N×查询——2000 SKU 全量评估从分钟级降到秒级
    return_hits=True 时返回 (triggered, hits); hits={(alert_type, sku, warehouse, channel)}
    供每日全量评估后反向关闭已恢复告警(完整性: 库存补足后旧告警不残留)
    """
    if not contexts:
        return []
    rules = rule_cache if rule_cache is not None else load_rules_for(event, channel)
    # 规则 mode(bbcc/traditional)不再按 ctx.mode 过滤排除 —— 原逻辑在 evaluate_stock_skus
    # (ctx 无 mode 键)下 mode 指定规则永不评估(静默失效); mode 现仅用于 params.lit fallback
    # 参数选择(bbcc→b_to_c+c_safety, traditional→lead_time, 跟随补货模式)
    for _rl in rules:
        _rl["_params"] = _rule_params_loaded(_rl)
        # 2026-10-29 性能: 原在 ctx×rule 双层循环里 json.loads 每次(9000 ctx × 3 规则 ≈ 2.7 万次)
        try:
            _rl["_cond"] = json.loads(_rl.get("condition_json") or "{}")
        except Exception:
            _rl["_cond"] = None
    # 参数载体规则(alert_enabled=0)不产告警 → 提前过滤, 循环内不再判断
    rules = [r for r in rules if str((r.get("_params") or {}).get("alert_enabled")) != "0"]
    if not rules:
        return ([], set()) if return_hits else []
    # 2026-11-06 S2-3/S2-4: 删掉两次全表预载(active 全量 + 近 7 天 inactive)。
    #   原来这两条 SELECT 在 5 万告警库下每次评估要读 5 万行, 而且**根本挡不住并发**:
    #   两个 evaluate_many 同时读到"不存在"就各插一条(线上实证 purchase_need 同 SKU 13 条)。
    #   现在"同一维度只有一行"由 alerts.uq_alerts_dim 的库约束保证, 写入走单语句 upsert。
    #   batch_seen 只做**本批内**去重(同 SKU 多仓/多码时同维度可能出现两次), 不查库。
    batch_seen = set()
    upserts = []
    triggered = []
    hits = set()
    for ctx in contexts:
        sku = ctx.get("sku", "")
        channel_x = ctx.get("channel") or channel or "jd"
        wh = (ctx.get("inv") or {}).get("warehouse", "")
        bc_x = str(ctx.get("barcode") or "")
        _inv_x = ctx.get("inv") or {}
        _base_ctx = {**ctx, "avail": int(_inv_x.get("available_qty") or 0),
                     "safety": float(_inv_x.get("safety_qty") or 0),
                     "product_name": _inv_x.get("product_name", "")}
        # sellable/transit 仅在"库存上下文"事件里注入(order/flow 事件无 inv → 不提供键,
        #   条件判定遇缺失即跳过, 避免用 0 当成"库存为0"造成误报)
        if "inv" in ctx:
            _base_ctx["transit"] = int(_inv_x.get("in_transit_qty") or 0)
            _base_ctx["sellable"] = (int(_inv_x.get("available_qty") or 0)
                                     + int(_inv_x.get("in_transit_qty") or 0))
        for rule in rules:
            cond = rule.get("_cond")
            if cond is None:
                continue
            # params.lit 模式 fallback: 规则未设 lit 时按规则 mode 跟随补货周期
            # (bbcc→b_to_c+c_safety, traditional→lead_time; 与断货卡模式双线一致)
            _rp2 = dict(rule.get("_params") or {})
            if "lit" not in _rp2 and ctx.get("lit_trad") is not None:
                _m = str(rule.get("mode") or "")
                if not _m:
                    _m = "bbcc" if ctx.get("channel") == "jd" else "traditional"  # 未指定=跟随渠道默认模式
                _rp2["lit"] = ctx.get("lit_bbcc" if _m == "bbcc" else "lit_trad")
            ctx2 = {**_base_ctx, "rule": rule, "params": _rp2}
            # 2026-11-01 模板扁平化: alert_title/alert_desc 用 str.format(**ctx2) 渲染,
            #   只能取**顶层**键({product_name}/{sellable} 这类), 取不到嵌套的 own.gap / params.xxx。
            #   → 把 own.* 与 params.* 摊到顶层, 模板可写成 {gap} / {own_overstock_days}。
            #   setdefault 让原 ctx 顶层键优先(不被覆盖)。
            #   线上实证踩过两次坑: own_transfer_gap 描述里 {need}/{gap} 原样落库(先漏 own),
            #   修完 own 后 own_overstock 仍原样落库(再漏 params)。
            _own_x = ctx.get("own")
            if isinstance(_own_x, dict) and _own_x:
                for _ok, _ov in _own_x.items():
                    ctx2.setdefault(_ok, _ov)
            if isinstance(_rp2, dict) and _rp2:
                for _pk, _pv in _rp2.items():
                    ctx2.setdefault(_pk, _pv)
            # batch.*: 效期规则模板要写 {days_to_expire}(如"批次剩余 {days_to_expire} 天到期")
            _bt_x = ctx.get("batch")
            if isinstance(_bt_x, dict) and _bt_x:
                for _bk, _bv in _bt_x.items():
                    ctx2.setdefault(_bk, _bv)
            if not _check_condition(cond, ctx2):
                continue
            at = rule.get("alert_type", "")
            hits.add((at, sku, wh, channel_x, bc_x))  # 条件触发即命中(含已存在告警, 反向关闭勿误关)
            key = (at, sku, channel_x, wh, bc_x)
            if key in batch_seen:
                continue
            batch_seen.add(key)
            title_tpl = rule.get("alert_title", "") or ""
            desc_tpl = rule.get("alert_desc", "") or ""
            try:
                title = title_tpl.format(**ctx2) if "{" in title_tpl else title_tpl
                desc = desc_tpl.format(**ctx2) if "{" in desc_tpl else desc_tpl
            except Exception as _etpl:
                # 2026-11-01: 原 except 静默吞 → 模板变量写错时, 告警描述会**原样存进库**
                #   (线上实证: '下游 {downstream_ds}/日×{b2c_days}天需 {need} 件' 直接落表)。
                #   改为留痕, 便于发现规则模板与 ctx 键不匹配。
                title, desc = title_tpl, desc_tpl
                try_err("rules", "告警模板渲染失败(变量未匹配 ctx): %s" % (at,), _etpl)
            # 2026-11-06 S2-4: 不再区分"重开"与"新插入" —— 一条 upsert 覆盖两种语义,
            #   由 uq_alerts_dim 决定命中哪一行(不存在则插, 存在则重开/刷新)。
            upserts.append((at, title, desc, rule.get("severity", "warning"),
                            sku, int(rule.get("id") or 0),
                            (ctx.get("inv") or {}).get("warehouse_type", ""), wh, channel_x, bc_x))
            triggered.append(rule.get("name") or str(rule.get("id")))
            hits.add((at, sku, wh, channel_x, bc_x))
    if upserts:
        for i in range(0, len(upserts), 100):
            try:
                executemany(_ALERT_UPSERT_SQL, upserts[i:i + 100])
            except Exception as _e:
                try_err("rules", "告警批量写入失败(%d条)" % len(upserts[i:i + 100]), _e)
    if return_hits:
        return list(dict.fromkeys(triggered)), hits
    return list(dict.fromkeys(triggered))


def load_rules_for(event, channel=None, mode=None):
    """一次性加载某事件的全部 active 规则(性能: evaluate_many 复用)

    2026-10-28 mode 生效(原只存不读): rules.mode='' 或缺失 = 全部模式通用;
    指定 mode(bbcc/traditional) 的规则仅在当前补货模式匹配时参与评估。
    mode=None → 不过滤(向后兼容, 老调用方行为不变)
    """
    params = [event]
    sql = ("SELECT * FROM rules WHERE is_active=1 AND event=%s "
           "AND (deleted_at IS NULL OR deleted_at='')")
    if channel:
        sql += " AND channel=%s"
        params.append(channel)
    if mode:
        # 空串/NULL 表示"全部"→ 始终生效; 否则需与当前模式一致
        sql += " AND (mode IS NULL OR mode='' OR mode=%s)"
        params.append(mode)
    return query(sql, params)


def _rule_params_loaded(rule):
    """规则 params(JSON 列)解析为 dict, 供条件引用 params.key"""
    if not rule:
        return {}
    try:
        p = rule.get("params")
        if isinstance(p, dict):
            return p
        return json.loads(p or "{}")
    except Exception:
        return {}


# ── 条件字段: 唯一真源在 core/rule_fields.py(2026-11-05 根治"两份手工列表") ──
#   白名单与"是否走计算注入"必须由同一张表派生。原实现是两份手写列表, 漏登记即
#   静默吞规则(supplier.score / inv.turnover_days 可选可存却永不触发)。
from core.rule_fields import (  # noqa: E402  (常量随迁, 放此处保持模块尾部集中)
    CALC, FIELD_SPEC, calc_tokens as _calc_tokens, needs_calc, refs_in,
    unknown_refs, validate_refs,
)

_VALID_FIELDS = frozenset(FIELD_SPEC)
# 兼容层: 仅供既有断言("own." / "batch." in _CALC_VARS)使用, 判定逻辑一律走 needs_calc()
_CALC_VARS = _calc_tokens()


def validate_condition(cond, event=None):
    """校验规则条件引用的字段有效性(创建/更新规则时调用)

    两件事:
      ① 字段必须登记过(拼错即拦, 含**无点**顶层字段 —— 原正则只查 a.b, 顶层拼错全放行)
      ② 字段必须在该事件上下文中存在(如 flow.quantity 只能用在 inbound/outbound.created)
    返回错误列表, 空 = 通过; params.* 为动态键通配。
    """
    return validate_refs(cond, event)



