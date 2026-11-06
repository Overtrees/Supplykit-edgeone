"""cleansing 写入模块(2026-10-11 拆分——导入写入逻辑独立; cleansing.py 减负)
函数: _finalize_import/_adjust_inventory(库存联动+规则评估) / _write_rows/_write_batch/_sync_batches(批量插入/批次效期)
依赖: db/core.rules/cleansing 清洗枚举——本模块独立
"""
from datetime import datetime, timezone
import json
import re

from db import query, one, execute, executemany
from routes.common import try_err, TARGET_WH as _TARGET_WH
from routes.common import mark_agg_days


# ── 是否做效期管理(2026-11-01)─────────────────────────────────────────────
# 三态: 'yes' 做效期(精确扣减+FEFO) / 'no' 不做(到货序 FIFO, 批次不带日期) / '' 未标注
# 职责边界(DEVELOPMENT §15.94): 列必填拦截在前端(映射校验), 后端只做写入——
#   故此处仅做**值归一化**(是/否→yes/no)与非法值留痕, 不做整批拒绝。
_EXPIRY_YES = {"yes", "y", "是", "有", "true", "1", "做", "需要", "做效期管理"}
_EXPIRY_NO = {"no", "n", "否", "无", "false", "0", "不做", "不需要", "不做效期管理"}


def _norm_expiry(v):
    """是/否 → 'yes'/'no'; 空 → ''; 无法识别 → '?'(调用方留痕)"""
    s = str(v if v is not None else "").strip().lower()
    if not s:
        return ""
    if s in _EXPIRY_YES:
        return "yes"
    if s in _EXPIRY_NO:
        return "no"
    return "?"



def _finalize_import(target, channel, mp, cleaned_all, success, failed, elapsed):
    """[已废弃 2026-11-01 · 勿调用 · 勿在此加逻辑] 旧版"导入最后一步"整包实现

    2026-10-16 导入拆成两段后，实际链路是 cleansing.py 的:
      · cleansing_status(done 步只写计数) 
      · cleansing_eval_status(独立分片任务: 重算库存 / 重建批次 / 规则评估)
    本函数自那以后**没有任何调用点**。代价已经出现过: §15.96 的"效期商品入库缺到期日"
    提示写在这里面 —— 上线后从未显示过（用户零感知, 与"修了却没生效"等价）。
    保留仅为对照 _adjust_inventory(增量制)的旧实现；新增逻辑一律写 cleansing.py。
    """
    import time as _t
    adjusted = 0
    evaluated = 0
    # 库存联动(A3): inbound+ / outbound- / order(采购单+、销售-)
    try:
        if target in ("inbound", "outbound", "order"):
            # 2026-10-29 架构定案(用户业务模型): 所有数据源=ERP/平台导出的历史快照, 非实时事件流
            # · 订单: 纯数据源(日销/GMV/规则), **不碰任何库存**(平台库存导出已含销售影响)
            # · 入库/出库: 自有仓台账 —— 改为【重算制】(幂等), 不再增量加减
            #   available = beginning_stock(期初, 库存导入写入) + Σ入库 − Σ出库
            #   增量制缺陷: 重复导入翻倍 / 滞后补录顺序错 / 无法对账
            if target in ("inbound", "outbound", "inventory"):
                try:
                    _skus_touch = sorted({c.get("sku") for c in cleaned_all if c.get("sku")})
                    # 2026-10-29 用户定案: 仓库名以**导入表格**为准 —— 流水里出现的
                    # (sku, warehouse_type, warehouse) 若库存表没有, 自动建档(期初 0),
                    # 由下方 _recalc 算出 available。即"导入什么仓, 系统就认什么仓"。
                    if target in ("inbound", "outbound"):
                        try:
                            _ensure_inventory_rows(channel, cleaned_all)
                        except Exception as _ewh:
                            try_err('cleansing', '库存行补建降级', _ewh)
                    if _skus_touch:
                        # 库存: 期初 + Σ流水(重算制); 批次: FIFO 重建(Σ批次 ≡ available)
                        _recalc_own_inventory(channel, _skus_touch)
                        _rebuild_own_batches(channel, _skus_touch)
                except Exception as _er:
                    try_err('cleansing', '自有仓重算降级', _er)
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    # 2026-10-29 数据新鲜度记录(导入完成 → UPSERT; 供界面"数据截至"提示)
    try:
        from db import execute as _ef
        _bizd = ""
        for _c in cleaned_all[:200]:
            for _k in ("ordered_at", "inbound_date", "outbound_date", "date", "arrived_time"):
                _v = str(_c.get(_k) or "")[:10]
                if _v and _v > _bizd:
                    _bizd = _v
        _ef("INSERT INTO data_freshness(doc_type, channel, last_import_at, last_biz_date, rows_imported) "
            "VALUES(%s,%s,NOW(),%s,%s) ON DUPLICATE KEY UPDATE last_import_at=NOW(), "
            "last_biz_date=VALUES(last_biz_date), rows_imported=rows_imported+VALUES(rows_imported)",
            [target, channel, _bizd, len(cleaned_all)])
    except Exception as _efx:
        try_err('cleansing', '新鲜度记录降级', _efx)
    # 规则引擎评估(批量)
    try:
        from core.rules import evaluate_many, load_rules_for
        if target == "order":
            seen = set()
            skus = [c.get("sku") for c in cleaned_all
                    if c.get("sku") and not (c.get("sku") in seen or seen.add(c.get("sku")))]
            inv_map = {}
            if skus:
                for _i in range(0, len(skus), 500):  # 500/片(曾 200——25 次往返慢)
                    _batch = skus[_i:_i + 200]
                    _ph = ",".join(["%s"] * len(_batch))
                    # 2026-10-29 修正: 原 MAX(available_qty) 取"最大单仓库存" —— 订单不区分仓,
                    # 用单仓库存对比累计订单量会**低估超卖风险**(库存分散在多仓时看起来够)
                    # 改 SUM: 该 SKU 全部仓(含各 69码)合计可用, 与订单口径(全渠道累计量)匹配
                    for r in query("SELECT sku, SUM(available_qty) AS avail FROM inventory "
                                   "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph),
                                   [channel] + _batch):
                        inv_map[r.get("sku")] = int(r.get("avail") or 0)
            # 2026-10-29 超卖口径: 补 order.total_qty(累计未删订单量) —— 单笔口径会系统性漏报
            _cum2 = {}
            _sku_all = [str(c.get("sku")) for c in cleaned_all if c.get("sku")]
            if _sku_all:
                for _i2 in range(0, len(_sku_all), 500):
                    _b2 = _sku_all[_i2:_i2 + 500]
                    _ph3 = ",".join(["%s"] * len(_b2))
                    for _r2 in query("SELECT sku, SUM(quantity) AS q FROM orders WHERE channel=%s "
                                     "AND (deleted_at IS NULL OR deleted_at='') AND sku IN (%s) "
                                     "GROUP BY sku" % ("%s", _ph3), [channel] + _b2):
                        _cum2[_r2.get("sku")] = int(_r2.get("q") or 0)
            # 2026-10-29 SKU 级去重(数学等价: max 覆盖 ">", min 覆盖 "<", total_qty 用于累计超卖)
            _grp2 = {}
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                _q2 = int(c.get("quantity") or 0)
                _g2 = _grp2.setdefault(sku, {"max": _q2, "min": _q2, "n": 0})
                _g2["max"] = max(_g2["max"], _q2)
                _g2["min"] = min(_g2["min"], _q2)
                _g2["n"] += 1
            o_ctxs = []
            for sku, _g2 in _grp2.items():
                oq = _g2["max"]
                o_ctxs.append({"sku": sku, "channel": channel,
                               "order": {"quantity": oq, "max_quantity": _g2["max"],
                                         "min_quantity": _g2["min"], "line_count": _g2["n"],
                                         "total_qty": _cum2.get(sku, oq)},
                               "order_qty": _cum2.get(sku, oq),
                               "available_stock": inv_map.get(sku, 0),
                               "inv": {"available_qty": inv_map.get(sku, 0), "safety_qty": 0,
                                       "in_transit_qty": 0}})
            if o_ctxs:
                evaluate_many("order.created", o_ctxs, channel,
                              load_rules_for("order.created", channel))
                evaluated = len(o_ctxs)
        elif target in ("inventory", "platform_inv", "inventory_b"):
            i_ctxs = []
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                i_ctxs.append({"sku": sku, "channel": channel,
                               "inv": {"available_qty": int(c.get("available_qty") or 0),
                                       "safety_qty": int(c.get("safety_qty") or 0),
                                       "in_transit_qty": int(c.get("in_transit_qty") or 0),
                                       "warehouse_type": c.get("warehouse_type", ""),
                                       "warehouse": c.get("warehouse", ""),
                                       "product_name": c.get("product_name") or sku}})
            if i_ctxs:
                evaluate_many("inventory.changed", i_ctxs, channel,
                              load_rules_for("inventory.changed", channel))
                evaluated = len(i_ctxs)
    except Exception as _ee:
        try:
            import traceback as _tb6
            from db import execute as _e5
            _e5("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('cleansing_eval','error',%s,%s,'cleansing')",
                ("规则评估失败(%s): %s" % (target, str(_ee)[:200]),
                 _tb6.format_exc(limit=20)[-1800:]))
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    # 汇总消息(顺序状态口径 + 混渠道提示)
    _msg = "成功 %d 条, 跳过 %d 条" % (success, failed)
    try:
        if target == "order":
            from collections import Counter as _C
            _sc = _C((c.get("order_status") or "未知") for c in cleaned_all)
            _ignore = {k: v for k, v in _sc.items() if k != "已完成"}
            if _ignore:
                _msg += " · 注意: %s 不计入销量池(仅已完成), 已发货/待确认/退款类不参与补货采购计算" % \
                        ("; ".join("%s×%d" % (k, v) for k, v in _ignore.items()))
        _chs = {c.get("channel") for c in cleaned_all if c.get("channel")}
        if _chs and channel not in _chs:
            _msg += " · 文件含其他渠道数据(%s), 已按当前渠道 %s 写入" % ("/".join(_chs), channel)
        # 2026-11-01 补漏: 效期管理商品入库缺到期日 → 静默无批次(临期预警整段失效, 无任何提示)
        if target == "inbound":
            try:
                _emiss = _expiry_missing_count(channel, cleaned_all)
                if _emiss:
                    _msg += (" · 注意: %d 行是效期管理商品但无到期日期, 未生成批次"
                             "(临期预警/处置建议会缺这批货)" % _emiss)
            except Exception:
                pass
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    return adjusted, evaluated, _msg

def _allocate_to_batches(pool, exact_need, nobatch_need, mode="fefo"):
    """在批次行池上分配出库量(2026-11-01 期初/无批次扣减修复)

    背景: 旧实现只做 ① 精确匹配, 期初行(无日期)与未填批次出库都进不了匹配键
      → 期初永不扣减、无批次出库只告警不扣 → Σ批次 恒大于可用, 对账常年告警。
    现改为"精确优先 + 降级", 保证任何数据形态下 Σ批次 ≡ 可用:
      ① 精确(仅 fefo): 出库批次 (pd, ed) 与批次行一致 → 直接扣该行;
      ② 降级: ①扣不完的余量 + 未填批次的出库 → 从剩余量继续扣。
         FEFO 序 = 期初批次(无日期, 视为最早库存) → exp_date 升序(空排最后)
                   → prod_date 升序 → 原始顺序。
         降级扣减**同时告警**, 不静默: 扣减是为了账平, 告警是为了让人知道批次数据有缺。

    2026-11-01 新增 mode="fifo"(不做效期管理的商品): 忽略批次日期, 按池原始顺序
      (= 期初 → 入库 id 升序, 即真实先入先出)扣减。原 FEFO 把空效期排到最后,
      对不做效期的 SKU 等价于"永不先出", 会让无批次货长期滞留成僵尸批次。

    pool: [{quantity, prod_date, exp_date, _opening?}], 原地扣减
    返回: (超卖量, 精确命中量, 降级命中量, 精确未命中的批次键列表)
    """
    left = {k: int(v) for k, v in (exact_need or {}).items() if int(v or 0) > 0}
    hit_exact = 0
    _exact_total = sum(left.values())
    if mode == "fifo":
        # 不做效期管理: 批次无意义, 精确量并入"降级量"统一按到货顺序扣, 不产生告警
        left = {}
        nobatch_need = int(nobatch_need or 0) + _exact_total
    # ① 精确匹配: 同 (prod_date, exp_date)
    for r in pool:
        if not left:
            break
        _pd = str(r.get("prod_date") or "")[:10]
        _ed = str(r.get("exp_date") or "")[:10]
        if not (_pd or _ed):
            continue
        _need = left.get((_pd, _ed), 0)
        if _need <= 0:
            continue
        _q0 = int(r.get("quantity") or 0)
        if _q0 <= 0:
            continue
        _take = min(_q0, _need)
        r["quantity"] = _q0 - _take
        left[(_pd, _ed)] = _need - _take
        hit_exact += _take
    miss_keys = [k for k, v in left.items() if v > 0]
    rest = sum(v for v in left.values() if v > 0) + max(0, int(nobatch_need or 0))
    hit_fallback = 0
    # ② 降级: fifo=原序(期初→入库 id 升序), fefo=效期升序(空排最后)
    if rest > 0 and pool:
        if mode == "fifo":
            order = list(range(len(pool)))
        else:
            order = sorted(range(len(pool)), key=lambda i: (
                0 if pool[i].get("_opening") else 1,
                str(pool[i].get("exp_date") or "") or "9999-12-31",
                str(pool[i].get("prod_date") or "") or "9999-12-31",
                i))
        for i in order:
            if rest <= 0:
                break
            _q0 = int(pool[i].get("quantity") or 0)
            if _q0 <= 0:
                continue
            _take = min(_q0, rest)
            pool[i]["quantity"] = _q0 - _take
            rest -= _take
            hit_fallback += _take
    return max(0, rest), hit_exact, hit_fallback, miss_keys


def _rebuild_own_batches(channel, skus, begin_date=None):
    """自有仓批次重建(2026-10-29 四维): 绑定键 = 仓库类型 + SKU + **69码** + 批次信息

    批次信息 = (prod_date, exp_date) —— 入库记录里的生产/到期日期即批次标识
    69码为空时降级三键(sku + warehouse), 与项目既有 "69码三键优先, 空落二键" 约定一致。

    算法:
      · 期初(库存导入给的 beginning_stock)按 (sku, barcode, warehouse) 生成"期初批次"
        (效期留空=未知, FEFO 排最前, **参与扣减**)
      · 每行入库记录 = 一个批次(带 prod_date/exp_date/barcode)
      · 出库扣减(2026-11-01 起) = "精确优先 + FEFO 降级", 见 _allocate_to_batches:
          ① 批次对得上 → 精确扣;
          ② 对不上(批次不存在/未填批次) → 按 FEFO 继续扣 + 告警, 保证 Σ批次 ≡ 可用
    只处理 own 维度; 平台/B 仓批次由库存导入提供。
    """
    from db import query as _q, execute as _e, executemany as _em
    from core.ledger import (begin_date as _ledger_begin, current_period as _cur_period)
    _ph_wt = ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES))
    # 2026-11-04 账期改造: 批次池 = **月初余额(带日期)** + 本账期入库, 出库也只扣本账期
    #   不这么切, 上月批次到本月就退化成"无日期期初批次" → 指定批次出库全部命中不了、
    #   只能降级 FIFO, 用户"以导入指定批次为准"的口径当场失效。
    # 起点优先级: 调用方显式传入 > ledger_state。显式传入的场景是"期初刚被校准",
    #   此刻 ledger_state 可能与校准时的口径不一致(结账/cron 会改它), 必须用同一个值,
    #   否则批次池区间与库存期初区间错开 —— 见 core/ledger_adj._refresh 的说明。
    if begin_date is None:
        _bd = _ledger_begin(channel)
    else:
        _bd = str(begin_date or "")
    # 哨兵/非法值一律归空。注意 "1970-01-01" **符合**日期格式, 单靠正则挡不住:
    #   它曾是"全期"哨兵, 若留着, `bool(_bd)` 会被判成"已结过账" → 改从 batch_opening
    #   取月初余额, 首建时该表为空 → 期初批次池缺 beginning_stock → Σ批次 ≠ 可用。
    if _bd == "1970-01-01" or (_bd and not re.match(r"^\d{4}-\d{2}-\d{2}$", _bd)):
        _bd = ""
    _is_ledger = bool(_bd)                       # True = 已结过账, 期初来自 batch_opening
    _cur_p = _cur_period(channel) if _is_ledger else ""
    _d_in = " AND inbound_date >= '%s' " % _bd if _bd else " "
    _d_out = " AND outbound_date >= '%s' " % _bd if _bd else " "
    done = 0
    _miss = []          # 未填批次的出库量 [(sku, 仓, qty)] —— 已按 FEFO 扣, 仅告警
    _miss_exact = []    # 填了批次但该批次无对应入库 [(sku, 仓, [(pd,ed)], qty)] —— 已按 FEFO 扣, 仅告警
    _oversold = []      # 出库 > 期初+入库 [(sku, 仓, 超出量)] —— 批次扣到 0, 需补流水
    _unlabeled = set()  # 未标注"是否做效期管理"的 SKU(历史数据; 暂按做效期处理并告警)
    # 2026-11-05 批大小 100 → 5000(用户报 seed 落账段"十来分钟/一直不结束")
    #   实测(线上 TiDB, 1 万 SKU/渠道):
    #     批大小   主查询合计   批数   总耗时(含 Python 分配 + 写入)
    #     100      1.26s        200    ≈ 280s   ← 撞死 90s 上限
    #     1000     1.19~1.78s    20    ≈ 40s
    #     5000     1.78s          4    ≈ 12s   ← 查询耗时不再涨, 批数减半
    #   耗时只跟**查询次数**走(与 _align_order_keys 的 chunk 结论同源)。5000 是单条
    #   IN 列表还在优化器舒适区的上限; 再大就要考虑改成 JOIN 临时表。
    for i in range(0, len(skus), 5000):
        batch_skus = [x for x in skus[i:i + 5000] if x]
        if not batch_skus:
            continue
        ph = ",".join(["%s"] * len(batch_skus))
        # 2026-10-29 仓型维度: 入库记录必须属于自有类仓型(为多仓/多类型准备)
        _wh_cond = (" AND (COALESCE(warehouse_type,'')='' OR warehouse_type IN (%s)) "
                    " AND warehouse IN (SELECT DISTINCT warehouse FROM inventory "
                    "WHERE channel=%%s AND warehouse_type IN (%s))"
                    % (_ph_wt, "%s"))
        _sql_ins = ("SELECT id, sku, barcode, warehouse, prod_date, exp_date, quantity "
                    "FROM inbound_records WHERE channel=%s AND sku IN (%s)" + _wh_cond + _d_in +
                    " ORDER BY CASE WHEN prod_date='' THEN 1 ELSE 0 END, prod_date, id")
        # _sql_ins 共 4 个 %s: channel + sku IN(...) + _wh_cond 的 warehouse_type IN(...) + 子查询 channel
        # 曾只给 2 个 → "not enough arguments for format string"(给 _wh_cond 加过滤后引入)
        ins = _q(_sql_ins % ("%s", ph, "%s", "%s", "%s"),
                 [channel] + batch_skus + list(SELF_WAREHOUSE_TYPES) + [channel] + list(SELF_WAREHOUSE_TYPES))
        # 出库: 按 (sku, 69码, 仓) 归集 —— 2026-11-01 扣减键补 warehouse
        #   旧键 (sku, 69码, 生产日, 截止日) 跨仓累加 → 多仓同批号时第一个仓吃满出库量, 仓错账平
        outs_exact = {}   # (sku, bc, wh) -> {(pd, ed): qty}
        outs_noexp = {}   # (sku, bc, wh) -> qty
        _sql_out = ("SELECT sku, barcode, warehouse, prod_date, exp_date, SUM(quantity) AS q "
                    "FROM outbound_records WHERE channel=%s AND sku IN (%s) "
                    # 2026-10-29 仓型维度: 只取自有类流水的出库
                    "AND (COALESCE(warehouse_type,'')='' OR warehouse_type IN (%s)) "
                    + _d_out + "GROUP BY sku, barcode, warehouse, prod_date, exp_date")
        for r in _q(_sql_out % ("%s", ph, _ph_wt),
                    [channel] + batch_skus + list(SELF_WAREHOUSE_TYPES)):
            _sk = str(r.get("sku") or "")
            _bc = str(r.get("barcode") or "").strip()
            _wh_o = str(r.get("warehouse") or "").strip()   # 仓名来自出库记录(数据驱动, 非硬编码)
            _pd = str(r.get("prod_date") or "")[:10]
            _ed = str(r.get("exp_date") or "")[:10]
            _qq = int(r.get("q") or 0)
            _gk = (_sk, _bc, _wh_o)
            if _pd or _ed:
                _d = outs_exact.setdefault(_gk, {})
                _d[(_pd, _ed)] = _d.get((_pd, _ed), 0) + _qq
            else:
                outs_noexp[_gk] = outs_noexp.get(_gk, 0) + _qq
        # 期初(按 sku+barcode+warehouse)
        _beg_map = {}
        for r in _q("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, "
                    "COALESCE(beginning_stock,0) AS b FROM inventory "
                    "WHERE channel=%s AND sku IN (%s) AND warehouse_type IN (%s)"
                    % ("%s", ph, _ph_wt),
                    [channel] + batch_skus + list(SELF_WAREHOUSE_TYPES)):
            _beg_map[(str(r.get("sku") or ""), str(r.get("bc") or ""),
                      str(r.get("warehouse") or ""))] = int(r.get("b") or 0)
        # 2026-11-04 账期版期初: 已结过账 → 月初余额来自 batch_opening(**带生产/到期日**),
        #   这才是"上月入库没卖完、本月继续按指定批次出"能成立的唯一前提。
        #   未结过账(首次建账) → 仍用库存导入的 beginning_stock(无日期, FEFO 排最前)。
        _open_map = {}
        if _is_ledger:
            for r in _q("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, "
                        "COALESCE(prod_date,'') AS pd, COALESCE(exp_date,'') AS ed, qty "
                        "FROM batch_opening WHERE period=%s AND channel=%s AND sku IN (%s) "
                        "AND warehouse_type IN (%s)" % ("%s", "%s", ph, _ph_wt),
                        [_cur_p, channel] + batch_skus + list(SELF_WAREHOUSE_TYPES)):
                _open_map.setdefault((str(r.get("sku") or ""), str(r.get("bc") or ""),
                                      str(r.get("warehouse") or "")), []).append(
                    {"quantity": int(r.get("qty") or 0), "prod_date": str(r.get("pd") or ""),
                     "exp_date": str(r.get("ed") or ""), "_opening": True})
        # 库存行的仓型映射(插入批次时用真实仓型, 而非写死 'own')
        _wt_map = {}
        for _r1 in _q("SELECT sku, warehouse, warehouse_type FROM inventory "
                      "WHERE channel=%s AND sku IN (%s) AND warehouse_type IN (%s)"
                      % ("%s", ph, _ph_wt),
                      [channel] + batch_skus + list(SELF_WAREHOUSE_TYPES)):
            _wt_map[(str(_r1.get("sku") or ""), str(_r1.get("warehouse") or ""))] = str(
                _r1.get("warehouse_type") or SELF_WAREHOUSE_TYPES[0])
        # 2026-11-01 是否做效期管理(按 SKU 取, products.sku 全局唯一)
        #   'no' → 批次不参与效期排序, 出库按到货顺序 FIFO 扣; 'yes'/'空' → 精确+FEFO
        _em_map = {}
        for _r2 in _q("SELECT sku, COALESCE(expiry_managed,'') AS em FROM products "
                      "WHERE sku IN (%s)" % ph, batch_skus):
            _em_map[str(_r2.get("sku") or "")] = str(_r2.get("em") or "")
        # 按 (sku, barcode, warehouse) 分组入库明细
        # 69码规则: 有值则精准归组; 无值则保持无码(不反推/不猜测归属)
        by_key = {}
        for r in ins:
            _sk = str(r.get("sku") or "")
            _wh = str(r.get("warehouse") or "")
            _bc = str(r.get("barcode") or "").strip()
            # 2026-10-31 修复: 本行原缩进在 for 外, 导致整个 ins 只有最后一条进入分组,
            # 批次重建只保留最后一笔入库 → Σ批次 远小于可用, 触发"批次对账偏差"告警。
            by_key.setdefault((_sk, _bc, _wh), []).append(r)
        for k, _b0 in _beg_map.items():
            if _b0 > 0:
                by_key.setdefault(k, [])
        for k in _open_map:
            by_key.setdefault(k, [])   # 只有月初余额、本账期无入库的 SKU 也要建池
        _del_keys, _insert_rows = [], []
        for (sk, bc, wh), rows in by_key.items():
            _b0 = _beg_map.get((sk, bc, wh), 0)
            _pool = []
            if _is_ledger:
                _pool.extend(_open_map.get((sk, bc, wh), []))
            elif _b0 > 0:
                # 期初批次: 无日期 = 效期未知, FEFO 排最前(视为最早库存), 参与扣减
                _pool.append({"quantity": _b0, "prod_date": "", "exp_date": "", "_opening": True})
            _pool.extend(rows)
            _exact = outs_exact.get((sk, bc, wh)) or {}
            _nb = outs_noexp.get((sk, bc, wh), 0)
            _expm = _em_map.get(sk, "")   # 注意: 本函数内 _em 是 executemany 别名
            if _expm == "":
                _unlabeled.add(sk)
            if _expm == "no":
                # 不做效期管理: 出库量(无论是否填批次)全部按到货顺序 FIFO 扣;
                #   不产生"未填批次/批次无对应入库"告警(该品类本就没有批次概念)
                _all_out = sum(int(v or 0) for v in _exact.values()) + _nb
                _over, _hit_e, _hit_f, _miss_keys = _allocate_to_batches(
                    _pool, {}, _all_out, mode="fifo")
            else:
                _over, _hit_e, _hit_f, _miss_keys = _allocate_to_batches(_pool, _exact, _nb)
                if _miss_keys:
                    _miss_exact.append((sk, wh, _miss_keys,
                                        sum(int(_exact.get(k) or 0) for k in _miss_keys)))
                if _nb > 0:
                    _miss.append((sk, wh, _nb))
            if _over > 0:
                _oversold.append((sk, wh, _over))
            # ③ 生成本次剩余批次(不做效期的 SKU 批次不带日期 —— 下游效期预警/临期自动跳过)
            for r in _pool:
                qty = int(r.get("quantity") or 0)
                if qty > 0:
                    _insert_rows.append({"sku": sk, "barcode": bc, "warehouse": wh,
                                         "warehouse_type": _wt_map.get((sk, wh), SELF_WAREHOUSE_TYPES[0]),
                                         "channel": channel,
                                         "prod_date": "" if _expm == "no" else str(r.get("prod_date") or "")[:10],
                                         "exp_date": "" if _expm == "no" else str(r.get("exp_date") or "")[:10],
                                         "qty": qty})
            _del_keys.append((sk, bc, wh))
        if not _del_keys:
            continue
        # 删旧(按 sku 全删该渠道 own 批次, 再整体重建, 保证幂等)
        # 2026-11-05: 分块 200/300 → 2000 → 插入侧 5000 —— 与读取侧同一个理由(耗时随**次数**走)。
        #   实测 1 万 SKU/渠道 ≈ 4.3 万批发行, 原 300/块 = 143 次 INSERT/渠道;
        #   2000/块 = 22 次, 5000/块 = 9 次。单条 INSERT 5 万行实测 0.645s / 808KB,
        #   max_allowed_packet=64MB, 5000 行远在安全区(仅删除侧 IN 列表仍留 2000,
        #   那个是优化器 IN 列表舒适区, 不是写请求数, 不一起抬)。
        _sk_all = sorted({k[0] for k in _del_keys})
        for j in range(0, len(_sk_all), 2000):
            d = _sk_all[j:j + 2000]
            try:
                _e("DELETE FROM batches WHERE channel=%s AND warehouse_type IN (%s) AND sku IN (%s)"
                   % ("%s", ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES)), ",".join(["%s"] * len(d))),
                   [channel] + list(SELF_WAREHOUSE_TYPES) + d)
            except Exception as _ed:
                try_err('cleansing', '自有仓批次删除降级', _ed)
        for j in range(0, len(_insert_rows), 5000):
            chunk = _insert_rows[j:j + 5000]
            cols = ["sku", "barcode", "warehouse", "warehouse_type", "channel",
                    "prod_date", "exp_date", "qty"]
            try:
                _em("INSERT INTO batches(%s) VALUES(%s)"
                    % (", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols))),
                    [tuple(r[c] for c in cols) for r in chunk])
            except Exception as _ee:
                try_err('cleansing', '自有仓批次插入降级', _ee)
        done += len(_insert_rows)
    if _miss:
        try:
            _tot = sum(x[2] for x in _miss)
            _s0 = _miss[0]
            try_err('cleansing', '出库未填批次',
                    Exception("渠道 %s 有 %d 个 SKU×69码×仓 的出库记录未填批次(生产/截止日期), 合计 %d 件 "
                              "(例 sku=%s 仓=%s 未填=%d 件) "
                              "—— 已按 FEFO 从该仓现有批次扣减(账要平), 建议补全批次信息以便精准追溯"
                              % (channel, len(_miss), _tot, _s0[0], _s0[1] or "-", _s0[2])))
        except Exception:
            pass
    if _miss_exact:
        try:
            _tot = sum(x[3] for x in _miss_exact)
            _s0 = _miss_exact[0]
            _k0 = _s0[2][0] if _s0[2] else ("", "")
            try_err('cleansing', '出库批次无对应入库',
                    Exception("渠道 %s 有 %d 个 SKU×69码×仓 的出库批次在入库中不存在或数量不足, 合计 %d 件 "
                              "(例 sku=%s 仓=%s 批次=%s~%s) "
                              "—— 已按 FEFO 从该仓现有批次(含期初)降级扣减; 请核对批次号或补录入库"
                              % (channel, len(_miss_exact), _tot, _s0[0], _s0[1] or "-",
                                 _k0[0] or "-", _k0[1] or "-")))
        except Exception:
            pass
    if _oversold:
        try:
            _tot = sum(x[2] for x in _oversold)
            _s0 = _oversold[0]
            try_err('cleansing', '出库超卖',
                    Exception("渠道 %s 有 %d 个 SKU×69码×仓 的出库量超过 期初+入库, 合计超出 %d 件 "
                              "(例 sku=%s 仓=%s 超出=%d 件) "
                              "—— 批次已扣至 0, 可用同步截断为 0; 请补录入库或核对出库数据"
                              % (channel, len(_oversold), _tot, _s0[0], _s0[1] or "-", _s0[2])))
        except Exception:
            pass
    if _unlabeled:
        try:
            _ul = sorted(_unlabeled)
            try_err('cleansing', '商品未标注是否做效期管理',
                    Exception("渠道 %s 有 %d 个 SKU 未填「是否做效期管理」(例 sku=%s) "
                              "—— 本次暂按'做效期'处理(精确扣减+FEFO); "
                              "请在商品导入补齐该列(是/否)后重新导入, 否则不做效期的货会被按效期排序误扣"
                              % (channel, len(_ul), _ul[0])))
        except Exception:
            pass
    return done


def _ensure_inventory_rows(channel, rows):
    """按导入数据补齐 inventory 行(2026-10-29 用户定案: 仓库名以导入表格为准)

    业务逻辑: 出入库流水里出现的 (sku, warehouse_type, warehouse) = 该仓确实有业务
      · 库存表已有该行 → 不动
      · 没有 → 新建(available_qty=0, beginning_stock=0), 随后由 _recalc_own_inventory 算出可用
    即"导入里出现什么仓, 系统就认什么仓", 无需事先在库存表配置仓名。

    返回补建行数。
    """
    from db import query as _q, executemany as _em
    combos = {}
    for c in rows:
        _sk = str(c.get("sku") or "").strip()
        if not _sk:
            continue
        _wh = str(c.get("warehouse") or "").strip()
        _wt = str(c.get("warehouse_type") or "").strip() or SELF_WAREHOUSE_TYPES[0]
        _bc = str(c.get("barcode") or "").strip()   # 2026-10-29 四维: 69码参与
        combos[(_sk, _bc, _wh, _wt)] = True
    if not combos:
        return 0
    keys = list(combos.keys())
    existing = set()
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        ph = " OR ".join(["(sku=%s AND COALESCE(barcode,'')=%s AND warehouse=%s AND warehouse_type=%s)"] * len(chunk))
        pp = []
        for (_sk, _bc, _wh, _wt) in chunk:
            pp += [_sk, _bc, _wh, _wt]
        try:
            for r in _q("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, warehouse_type "
                        "FROM inventory WHERE channel=%s AND (%s)" % ("%s", ph), [channel] + pp):
                existing.add((str(r.get("sku")), str(r.get("bc") or ""),
                              str(r.get("warehouse") or ""), str(r.get("warehouse_type") or "")))
        except Exception as _eq:
            try_err('cleansing', '库存行查重降级', _eq)
            return 0
    params = []
    for (_sk, _bc, _wh, _wt) in keys:
        if (_sk, _bc, _wh, _wt) in existing:
            continue
        params.append((_sk, "", _bc, _wh, _wt, 0, 0, 0, channel))
    if not params:
        return 0
    try:
        _em("INSERT INTO inventory(sku, product_name, barcode, warehouse, warehouse_type, "
            "available_qty, beginning_stock, safety_qty, channel) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", params)
        return len(params)
    except Exception as _ee:
        try_err('cleansing', '库存行补建降级', _ee)
        return 0


def _recalc_own_inventory(channel, skus, begin_date=None):
    """自有仓库存重算(账期版 2026-11-04): available = 期初 + Σ入库 − Σ出库(**限本账期**)

    绑定键 = 仓库类型 + SKU + **69码** + 仓库名
      · 有 69码 → 四维匹配
      · 69码为空 → 降级三维(sku + warehouse), 与项目 "69码三键优先, 空落二键" 约定一致
    幂等: 流水与期初确定 → 结果唯一; 严格匹配(不做单仓降级等隐式修正)

    2026-11-04 账期改造(用户定案甲方案): Σ 的起点不再是"开天辟地", 而是 `ledger_state.begin_date`
      —— 结过账就是本月 1 号(期初 = 上月期末), 没结过是空(首次建账走全期)。
      原实现无日期下界 + 期初被每次导入的快照覆盖 → 每月累加一次双算, 这是根因。

    2026-11-05 RU 改造(用户定案, 目标: 一次 2 万 SKU 重灌落在免费额度内):
      原实现 = 每 200 SKU 一条 SELECT, 里面为**每一行**跑两个相关子查询
      (inbound/outbound 各一次) → 2 万 SKU 实测 12.7 RU/SKU × 2 万 ≈ 25 万次索引探测 ≈ 25M RU。
      一次 seed 重灌光是"重算可用"就要吃掉整月额度的一大半, 这不是优化题, 是可行性题。

      线上 EXPLAIN ANALYZE 定价(2026-11-05 实测, 同一份数据):
        · 旧路径 12 SKU 一批 ........................ 29.6 RU
        · 新路径 UPDATE ... JOIN 12 SKU 一批 ........ 23.9 RU  ← 只省两成, 不解决问题
        · **全渠道一次聚合(不管多少 SKU) ........... 66.9 RU**
        · **一次 SELECT 出全部 id+beg+in+out ........ 119.4 RU / 39.6ms**
      结论: 成本**按"批次数"计, 不按 SKU 数计**。0.5 万批 × 24 RU = 12 万 RU 起步,
      2 万 SKU 就是 1000 批; 而"一次拿全量"只要 119 RU —— 差三个数量级。

      所以改成 **一条 SELECT 取全量 → Python 算 → executemany 回写**:
        · 流水侧 UNION ALL 后按 (sku,69码,仓,仓型) 聚合一次, LEFT JOIN 库存行;
        · 只拉 own 维度的行(与旧实现 WHERE 一致), 行数 = 库存行数, 不随订单/流水增长;
        · 回写按 2000 行一批 executemany(与 _write_batch 同参数), 写侧 RU 与旧路径同量级。
      **不再按 SKU 分批** —— `skus` 参数只用于"只重算这些 SKU"的语义, 不用于切批。
    """
    from db import query as _q, executemany as _em
    from core.ledger import begin_date as _ledger_begin
    _ph_wt = ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES))
    # 账期下界: 严格校验成 YYYY-MM-DD 后内联(值来自 ledger_state, 非用户输入)
    if begin_date is None:
        _bd = _ledger_begin(channel)
    else:
        _bd = str(begin_date or "")   # 显式传入优先, 理由同 _rebuild_own_batches
    # 与 _rebuild_own_batches 同一套哨兵归空规则(见该函数注释): "1970-01-01" 是合法
    #   日期格式, 正则挡不住, 必须显式判。留着会让 Σ 起点与批次池区间错开。
    if _bd == "1970-01-01" or (_bd and not re.match(r"^\d{4}-\d{2}-\d{2}$", _bd)):
        _bd = ""
    _d_in = " AND r.inbound_date >= '%s' " % _bd if _bd else " "
    _d_out = " AND r.outbound_date >= '%s' " % _bd if _bd else " "
    _want = {str(x) for x in (skus or []) if x}
    # 流水分组合并: 一条语句同时产出 in_q / out_q(UNION ALL 后再 SUM, 比两段子查询少一次扫描)
    #   **分组键必须带仓型**: 旧实现的匹配条件是「流水仓型为空 或 等于库存行仓型」——
    #   也就是同 (sku,码,仓) 下, 空仓型的流水会同时算给该仓的**每一个**仓型行。
    #   若这里按 (sku,码,仓) 聚合、再 join 三个仓型行, 那份流水会被三次 JOIN 累加三次。
    #   所以仓型进分组键, join 时用"空或相等"还原旧口径。
    sql = (
        "SELECT i.id, COALESCE(i.beginning_stock,0) AS beg, "
        "       COALESCE(f._in,0) AS in_q, COALESCE(f._out,0) AS out_q, i.sku "
        "FROM inventory i "
        "LEFT JOIN ("
        "  SELECT r.sku AS _sku, COALESCE(r.barcode,'') AS _bc, r.warehouse AS _wh, "
        "         COALESCE(r.warehouse_type,'') AS _wt, "
        "         SUM(CASE WHEN r._io=1 THEN r.quantity ELSE 0 END) AS _in, "
        "         SUM(CASE WHEN r._io=2 THEN r.quantity ELSE 0 END) AS _out "
        "  FROM ("
        "    SELECT sku, barcode, warehouse, warehouse_type, quantity, 1 AS _io FROM inbound_records "
        "      WHERE channel=%%s%s"
        "    UNION ALL "
        "    SELECT sku, barcode, warehouse, warehouse_type, quantity, 2 AS _io FROM outbound_records "
        "      WHERE channel=%%s%s"
        "  ) r "
        "  GROUP BY r.sku, COALESCE(r.barcode,''), r.warehouse, COALESCE(r.warehouse_type,'')"
        ") f ON f._sku = i.sku "
        "   AND f._wh = i.warehouse "
        # 69码严格相等(2026-11-01 收口): 库存行无码而流水有码 = 数据不一致, 应暴露而非借码
        "   AND f._bc = COALESCE(i.barcode,'') "
        # 仓型兼容匹配(与旧子查询同一条件): 流水仓型为空 = 未标注, 视为适用于该仓所有仓型
        "   AND (f._wt = '' OR f._wt = i.warehouse_type) "
        "WHERE i.channel=%%s AND i.warehouse_type IN (%s) "
        "  AND (i.deleted_at IS NULL OR i.deleted_at='')"
        # 2026-11-04 去掉 `max(0, val)` 兜底: 负可用是"出库 > 期初+入库"的真实异常,
        #   归 0 会把它伪装成"恰好卖完", 看板与对账永远发现不了。负值一路带到前端标「负库存异常」。
    ) % (_d_in, _d_out, _ph_wt)
    try:
        rows = _q(sql, [channel, channel, channel] + list(SELF_WAREHOUSE_TYPES))
    except Exception as _er:
        # 执行计划异常(如优化器选了不合适的 join 顺序) → 退回旧逐 SKU 路径,
        #   不让一次计划变化就把重算全丢掉(旧路径慢但语义相同)。正常情况零成本。
        try_err('cleanse_write', '批量重算降级(退回逐 SKU)', _er)
        return _recalc_own_inventory_slow(channel, skus, _bd)
    upd = []
    for r in rows:
        if _want and str(r.get("sku") or "") not in _want:
            continue
        try:
            val = int(r.get("beg") or 0) + int(r.get("in_q") or 0) - int(r.get("out_q") or 0)
        except (TypeError, ValueError):
            continue
        upd.append((val, r.get("id")))
    out = 0
    for i in range(0, len(upd), 2000):
        chunk = upd[i:i + 2000]
        try:
            _em("UPDATE inventory SET available_qty=%s WHERE id=%s", chunk)
            out += len(chunk)
        except Exception as _ew:
            try_err('cleanse_write', '重算回写批失败(%d 行)' % len(chunk), _ew)
    return out



def _recalc_own_inventory_slow(channel, skus, bd=""):
    """旧版逐行子查询重算 —— **仅作批量版异常时的降级路径**, 勿在新代码里调用

    语义与 _recalc_own_inventory 逐字一致(同一套匹配条件), 见上方改造说明。
    """
    from db import query as _q, executemany as _em
    _ph_wt = ",".join(["%s"] * len(SELF_WAREHOUSE_TYPES))
    _d_in = " AND r.inbound_date >= '%s' " % bd if bd else " "
    _d_out = " AND r.outbound_date >= '%s' " % bd if bd else " "
    out = 0
    for i in range(0, len(skus), 200):
        batch = [x for x in skus[i:i + 200] if x]
        if not batch:
            continue
        ph = ",".join(["%s"] * len(batch))
        rows = _q(
            "SELECT i.id, COALESCE(i.beginning_stock, 0) AS beg, "
            "COALESCE((SELECT SUM(r.quantity) FROM inbound_records r "
            "  WHERE r.channel=i.channel AND r.sku=i.sku AND r.warehouse=i.warehouse "
            "  AND (COALESCE(r.warehouse_type,'')='' OR r.warehouse_type=i.warehouse_type) "
            "  AND COALESCE(r.barcode,'')=COALESCE(i.barcode,'')" + _d_in + "), 0) AS in_q, "
            "COALESCE((SELECT SUM(r.quantity) FROM outbound_records r "
            "  WHERE r.channel=i.channel AND r.sku=i.sku AND r.warehouse=i.warehouse "
            "  AND (COALESCE(r.warehouse_type,'')='' OR r.warehouse_type=i.warehouse_type) "
            "  AND COALESCE(r.barcode,'')=COALESCE(i.barcode,'')" + _d_out + "), 0) AS out_q "
            "FROM inventory i WHERE i.channel=%s AND i.warehouse_type IN (%s) AND i.sku IN (%s)"
            " AND (i.deleted_at IS NULL OR i.deleted_at='')"
            % ("%s", _ph_wt, ph),
            [channel] + list(SELF_WAREHOUSE_TYPES) + batch)
        upd = []
        for r in rows:
            try:
                val = int(r.get("beg") or 0) + int(r.get("in_q") or 0) - int(r.get("out_q") or 0)
            except Exception:
                continue
            upd.append((val, r.get("id")))
        if upd:
            _em("UPDATE inventory SET available_qty=%s WHERE id=%s", upd)
            out += len(upd)
    return out


def _adjust_key(k):
    """deltas 键归一为四维 (sku, 69码, 仓, 仓型)

    2026-11-02 补码: inventory 的业务唯一键是**五维** `uq_inventory_biz(sku, barcode, warehouse,
    channel, warehouse_type)`, 原键只有 (sku, 仓, 仓型) —— 多码 SKU 的 A 码/B 码会命中同一行
    (`_cur_map` 后者覆盖前者), 扣减加到别的码上。
    兼容旧三维调用方: 缺码按空串处理(与"空落二键"的项目约定一致, 不猜码)。
    """
    if len(k) >= 4:
        sku, bc, wh, wt = k[0], k[1], k[2], k[3]
    else:
        sku, wh, wt = k[0], k[1], k[2]
        bc = ""
    return (str(sku or ""), str(bc or ""),
            str(wh or ""), str(wt or "") or SELF_WAREHOUSE_TYPES[0])


def _adjust_inventory(channel, deltas, evaluate_skus=None):
    """库存联动: {(sku, 69码, 仓, 仓型): delta} → 批量更新库存(下限 0) + 规则批量评估

    完整性: 不限量 —— 大文件联动 SKU 全覆盖(原 300 上限曾致 >300 SKU 导入后规则评估缺失, 告警漏报);
    性能: evaluate_many 一次加载规则 + executemany 批量(原单条 evaluate 循环 N×查询);
         2026-11-02 连评估上下文也复用同一次查询结果(原逐 SKU 一次 `one(... LIMIT 1)` = N 次往返)。
    """
    if not deltas:
        return 0, []
    # 2026-10-15 批量联动(逐键 SELECT+UPDATE 曾 5000 键 ~10000 往返——210s 大头): 批量 SELECT 一次 + executemany 更新
    n = 0
    touched = []
    # 键归一 + 同键合并(四维/三维混传时可能落到同一行, 先加起来再更新, 避免后写覆盖)
    _K = {}
    for _k, _d in deltas.items():
        try:
            _n = _adjust_key(_k)
            _K[_n] = _K.get(_n, 0) + int(_d or 0)
        except Exception as _e:
            try_err('cleansing', '库存联动键非法(已跳过): %r' % (_k,), _e)
    _keys = list(_K.keys())
    _cur_map = {}
    for _i in range(0, len(_keys), 200):
        _batch = _keys[_i:_i + 200]
        _or = " OR ".join("(sku=%s AND warehouse=%s AND channel=%s)" for _ in _batch)
        _params = []
        for (sk, bc, wh, wt) in _batch:
            _params += [sk, wh, channel]
        if _or:
            # 软删行不参与: 增量加到看不见的行上 = 用户看不到变化, 且下次导入还会被覆盖
            for _c in query(
                    "SELECT id, sku, COALESCE(barcode,'') AS bc, COALESCE(warehouse,'') AS wh, "
                    "COALESCE(warehouse_type,'') AS wt, available_qty, safety_qty, product_name, "
                    "in_transit_qty FROM inventory WHERE (%s) "
                    "AND (deleted_at IS NULL OR deleted_at='')" % _or, _params):
                _cur_map[(str(_c.get("sku") or ""), str(_c.get("bc") or ""),
                          str(_c.get("wh") or ""), str(_c.get("wt") or ""))] = _c
    _upd = []
    _ins = []
    _ctxs = []
    # 2026-11-06 同上: 这处也在循环里, 新建行时逐行查 ledger_state。本函数只处理自有仓,
    #   channel 固定, 一次导入内账期状态不变 → 循环外取一次。
    _ledger_on = _ledger_started(channel)
    for key, delta in _K.items():
        sku, bc, wh, wht = key
        cur = _cur_map.get(key)
        if cur:
            new_qty = max(0, int(cur.get("available_qty") or 0) + delta)
            _upd.append((new_qty, cur.get("id")))
            # 评估上下文复用本次查到的行(更新后量), 不再逐 SKU 回查
            _ctxs.append({"sku": sku, "channel": channel, "barcode": bc,
                          "inv": {"sku": sku, "warehouse": wh, "warehouse_type": wht,
                                  "product_name": cur.get("product_name") or "",
                                  "available_qty": new_qty,
                                  "safety_qty": cur.get("safety_qty") or 0,
                                  "in_transit_qty": cur.get("in_transit_qty") or 0}})
        else:
            # 新行基线: 未建账时 beginning 与 available 同置(否则重算会把增量抹成 0)。
            # 2026-11-04 已建账后改为 0 —— 这时那条入库流水本来就在本月窗口里会被 Σ 统计,
            #   再把增量写成期初就是双算(同一个 +N 进两次)。
            _q = max(0, delta)
            if wht == SELF_WAREHOUSE_TYPES[0] and _ledger_on:
                _q = 0
            _ins.append((sku, bc, wh, wht, _q, _q, channel))
            _ctxs.append({"sku": sku, "channel": channel, "barcode": bc,
                          "inv": {"sku": sku, "warehouse": wh, "warehouse_type": wht,
                                  "product_name": "", "available_qty": _q,
                                  "safety_qty": 10, "in_transit_qty": 0}})
        n += 1
        if sku not in touched:
            touched.append(sku)
    if _upd:
        executemany("UPDATE inventory SET available_qty=%s WHERE id=%s", _upd)
    if _ins:
        executemany("INSERT INTO inventory(sku, barcode, warehouse, warehouse_type, available_qty, "
                    "beginning_stock, safety_qty, channel) VALUES(%s,%s,%s,%s,%s,%s,10,%s)", _ins)
    skus = touched
    try:
        from core.rules import evaluate_many, load_rules_for
        if _ctxs:
            # 逐行(sku×码×仓)一条上下文 —— 与告警去重键的粒度一致, 多码/多仓各判各的
            evaluate_many("inventory.changed", _ctxs, channel, load_rules_for("inventory.changed", channel))
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    return n, skus

def _fill_barcode_from_products(channel, cleaned, cache=None):
    """69码入口对齐(2026-11-01 二次收紧): products.barcode 是档案, 流水/订单里的码是一手实况

    业务前提: 客户首月导入的订单表/出入库表往往**只有 SKU 和仓**, 没有 69码 列。
    若在入口拒绝, 首月导入全废; 若在聚合层猜码, 违反"不猜"。所以对齐放在入口。

      | 档案码 | 本行码 | 效期管理 | 多码证据 | 动作 |
      | 有     | 空     | no       | 无       | 用档案码补齐(**唯一合法补码**) |
      | 有     | 空     | yes/未标注/多码 | — | **留空 + 告警** |
      | 有     | 有且不同 | 任意   | —        | 保留流水码 + 告警(不改写) |
      | 空     | 有值   | 任意     | 无       | 回写档案(流水反哺档案) |
      | 空     | 有值   | 任意     | 有       | 不回写 + 告警 |
      | 空     | 空     | 任意     | —        | 无码商品, 原样 |

    **为什么补码要加这两道闸(2026-11-01 用户质询后修正)**:
      档案 barcode 是单值列, 它只能证明"有这个码", **不能证明"只有这个码"**。
      效期管理商品不同批次/不同包装本就可能是不同 69码 —— 此时"用档案码补齐"
      等于把 B 码的销量记到 A 码头上。留空只是丢一个码的分辨率(下游按合计降级),
      补错是错账, 且事后无法分辨哪几笔是错的。**两害相权, 留空。**

    **为什么"不符"不剔除**:
      多码 SKU 恰是"同一 SKU 换了条码"——档案只能存一个码, 新码流水必然"不符"。
      按不符即剔除, 多码客户的第二码流水会被整批丢掉且不可挽回; 误放一个错码只是
      多出一个码分组, 可事后修正。**两害取轻 → 保留 + 告警。**

    返回 (补码行数, 回写档案数, 不符行数); cleaned 原地修改。

    cache(2026-11-06 「快速通道」): 传入一个 dict 时, 档案码/效期/多码证据按 SKU 缓存,
      由调用方**跨批复用**。seed 把一份大文件切成几百个 500 行批次时, 原来每批都要
      重新查一次 products 与 inventory/orders/batches 的多码证据(约 4 次往返/批);
      缓存后同一个 SKU 只查一次, 判码规则、判定结果与原实现**逐行一致**
      (仍按本渠道优先、先到先得; 新出现的 SKU 仍会实查, 不是"拿旧结果糊弄")。
    """
    if not cleaned:
        return 0, 0, 0
    _skus = list({str(r.get("sku") or "").strip() for r in cleaned if r.get("sku")})
    if not _skus:
        return 0, 0, 0

    # 档案码 + 效期标记(补码是否合法的两个前提之一)
    #   products 按 (sku, channel) 建档: 同 SKU 跨渠道可能各自一码, 必须优先取本渠道档案,
    #   否则 B 渠道导入会拿到 A 渠道的码 —— 这是比"缺码"更隐蔽的串码。ORDER BY 让本渠道排前,
    #   下面用 setdefault(先到先得) 取值。
    if cache is not None:
        _amap = cache.setdefault("amap", {})
        _expmap = cache.setdefault("expmap", {})
        _resolved = cache.setdefault("resolved", set())   # 查过 products 的 SKU(含查无档案)
        _seen = set()                                      # 本次查询内的去重(多行取第一条)
    else:
        _amap, _expmap, _seen, _resolved = {}, {}, set(), None
    _todo = [s for s in _skus if _resolved is None or s not in _resolved]
    for _i in range(0, len(_todo), 500):
        _part = _todo[_i:_i + 500]
        for _b in query("SELECT sku, barcode, expiry_managed, channel FROM products WHERE sku IN (%s) "
                        "ORDER BY (channel=%%s) DESC, id DESC"
                        % ",".join(["%s"] * len(_part)), list(_part) + [channel]):
            _k = str(_b.get("sku"))
            if _k in _seen:
                continue
            _bc_ch = str(_b.get("channel") or "")
            if _bc_ch and channel and _bc_ch != channel:
                continue          # 只有别的渠道建档 → 本渠道视为无档案, 不借别渠道的码
            _seen.add(_k)
            _bc = str(_b.get("barcode") or "").strip()
            if _bc:
                _amap[_k] = _bc
            _expmap[_k] = _norm_expiry(_b.get("expiry_managed"))
        if _resolved is not None:
            # 查无档案的 SKU 也要标记"查过", 否则每批都会为它再发一次 IN 查询
            _resolved.update(_part)

    # 多码证据(两级): ①本批同一 SKU 出现 ≥2 个码 ②历史库存/订单/批次里出现过 ≥2 个码
    #   档案 barcode 是单值列, 它只能证明"有这个码", 不能证明"只有这个码" —— 所以要另找证据
    #   注意: ①是**批内**事实, 不跨批缓存(否则后续批次会"继承"别批的多码判定, 补码结果改变);
    #        ②是历史事实, 同一 SKU 只查一次即可 —— 这也是快速通道省下的那 3 次往返/批。
    _codes = {}
    for r in cleaned:
        _k = str(r.get("sku") or "").strip()
        if not _k:
            continue
        _codes.setdefault(_k, set())
        _bc = str(r.get("barcode") or "").strip()
        if _bc:
            _codes[_k].add(_bc)
    _multi = {k for k, s in _codes.items() if len(s) > 1}
    _hist = cache.setdefault("multi", set()) if cache is not None else set()
    _multi |= _hist
    # 2026-11-06 性能根治: **只查会被读到的证据**
    #
    # _multi 的读点只有两处, 且都在"缺码"分支里(见下面逐行判定):
    #   · `if not _arc:` → `if _k in _multi: 留空`      档案缺码
    #   · `if not _own:` → `if _k not in _multi and ...` 本行缺码
    # 档案有码 **且** 本批这一 SKU 每行都有码 → 两个分支都进不去 → 证据算了也没人读。
    #
    # 而这条证据查询是 `WHERE sku IN (...)` 过滤, orders 上能用的索引只有
    # (channel, order_no, sku, barcode) 的**最左**前缀, 用不到 sku →
    # 实测 EXPLAIN 是 IndexFullScan 44 万条索引项; batches 更是 TableFullScan 18 万行。
    # 每 500 个 SKU 发 3 条, 一份大文件几十个批次就是上百次全表扫 —— 这正是
    # seed 订单段单窗口 20 次探针 × 6 窗 = 120 次全表扫的来源。
    # 收紧到 _need 后: 判定结果逐行不变(不读的就不算), 常规导入(档案有码)探针数=0。
    _no_code = {str(r.get("sku") or "").strip() for r in cleaned
                if r.get("sku") and not str(r.get("barcode") or "").strip()}
    _need = [s for s in _skus if (not _amap.get(s)) or (s in _no_code)]
    if cache is not None:
        _probed = cache.setdefault("probed", set())
        _probe = [s for s in _need if s not in _probed]
    else:
        _probe = _need
    for _i in range(0, len(_probe), 500):
        _part = _probe[_i:_i + 500]
        _ph = ",".join(["%s"] * len(_part))
        for _t in ("inventory", "orders", "batches"):
            try:
                for _b in query("SELECT sku, COUNT(DISTINCT barcode) c FROM `%s` WHERE sku IN (%s) "
                                "AND barcode IS NOT NULL AND barcode<>'' GROUP BY sku" % (_t, _ph), _part):
                    if int(_b.get("c") or 0) > 1:
                        _multi.add(str(_b.get("sku")))
                        _hist.add(str(_b.get("sku")))
            except Exception:
                pass   # 证据查不到 → 按"无多码"处理(不因基础设施异常阻断导入), 但只影响 no 商品
    if cache is not None:
        _probed.update(_probe)   # 查过的 SKU(无论是否多码)不再重复查

    _filled, _mismatch, _writeback, _held = 0, [], {}, []
    for r in cleaned:
        _k = str(r.get("sku") or "").strip()
        _own = str(r.get("barcode") or "").strip()
        _arc = _amap.get(_k, "")
        if not _arc:
            if not _own:
                continue
            if _k in _multi:
                _why = "档案缺码且存在多个69码"
                _held.append((_k, _why))   # 回写哪个? 不猜
                r["_bc_held"] = _why
            else:
                _writeback[_k] = _own      # 档案缺码 + 流水有码 + 唯一码 → 回写档案
            continue
        if not _own:
            # 补码**唯一**合法场景: ①明确不做效期管理(expiry_managed='no') ②无任何多码证据
            #   效期管理=yes 的商品, 不同批次/不同包装本就可能是不同 69码,
            #   补档案码 = 把 B 码的销量记到 A 码头上, 比留空更糟(留空只丢一个码的分辨率, 补错是错账)
            #   未标注('')同样不补: "没填"不能当成"不会多码"
            if _k not in _multi and _expmap.get(_k) == "no":
                r["barcode"] = _arc
                _filled += 1
            else:
                _why = "效期管理=%s/多码=%s" % (_expmap.get(_k) or "未标注",
                                               "是" if _k in _multi else "否")
                _held.append((_k, _why))
                # 行级标记: 快照类(inventory)必须据此**拒写** —— 见 inventory 分支
                r["_bc_held"] = _why
        elif _own != _arc:
            # 一手数据优先: 订单/出入库是实际发生的交易, 码以流水为准; 只告警不改写
            _mismatch.append((_k, _arc, _own))
    if _mismatch:
        try:
            try_err('cleansing', '69码与档案不符(保留流水码 %d 行)' % len(_mismatch),
                    ValueError('sku=%s 档案=%s 流水=%s' % _mismatch[0]))
        except Exception:
            pass
    if _held:
        try:
            _rc = {}
            for _k, _why in _held:
                _rc[_why] = _rc.get(_why, 0) + 1
            try_err('cleansing', '69码留空未补 %d 行(缺码+效期管理/多码, 不猜测)'
                    % len(_held),
                    ValueError('; '.join('%s×%d' % (w, c) for w, c in list(_rc.items())[:3])))
        except Exception:
            pass
    if _writeback:
        try:
            executemany("UPDATE products SET barcode=%s WHERE sku=%s "
                        "AND (barcode IS NULL OR barcode='')",
                        [(v, k) for k, v in _writeback.items()])
            try_err('cleansing', '档案回写69码 %d 个 SKU(流水反哺档案)' % len(_writeback),
                    ValueError('sku=%s 码=%s' % next(iter(_writeback.items()))))
        except Exception as _e:
            try_err('cleansing', '档案回写69码降级', _e)
    return _filled, len(_writeback), len(_mismatch)


def _expiry_missing_count(channel, rows):
    """效期管理商品(expiry_managed='yes')的入库行缺到期日期 → 返回行数

    为什么单独统计: batches 只在入库导入带 prod_date/exp_date 时同步(_sync_batches),
    缺了就**静默**无批次, 而临期预警/处置建议/批次对账完全依赖 batches ——
    用户看不到任何提示, 只会觉得"预警不准"。这是"是否效期管理"标记唯一的落地校验点。
    """
    if not rows:
        return 0
    _skus = list({str(r.get("sku") or "").strip() for r in rows if r.get("sku")})
    if not _skus:
        return 0
    _yes = set()
    for _i in range(0, len(_skus), 500):
        _part = _skus[_i:_i + 500]
        for _b in query("SELECT sku FROM products WHERE sku IN (%s) "
                        "AND COALESCE(expiry_managed,'')='yes' "
                        "AND (channel=%%s OR COALESCE(channel,'')='')"
                        % ",".join(["%s"] * len(_part)), list(_part) + [channel]):
            _yes.add(str(_b.get("sku")))
    if not _yes:
        return 0
    return sum(1 for r in rows if str(r.get("sku") or "").strip() in _yes
               and not str(r.get("exp_date") or "").strip())


def _expiry_batch_gap(channel):
    """自有仓: 做效期管理(expiry_managed='yes')但**没有一条带到期日批次**的库存行数

    业务前提(2026-11-01 用户定案): 库存导入只提供【期初 + 在途】, 不提供批次;
      批次只能由入库记录(含生产/到期日)产生。所以效期商品若只导了期初,
      系统会生成"期初批次(有效期未知)", 临期预警/处置建议**天然覆盖不到这批货**。
    为什么是提示而不是拦截: 期初本来就是历史库存, 有效期不可考; 拒写等于不让初始化。
      但必须让人看见缺口 —— 否则"预警不准"永远查不到原因。
    只看自有仓: 平台仓/B仓是平台导出快照, 不参与批次管理(用户定案)。
    """
    try:
        r = one("SELECT COUNT(*) AS n FROM inventory i "
                "JOIN products p ON p.sku=i.sku "
                "WHERE i.channel=%s AND i.warehouse_type IN ('own') AND i.available_qty>0 "
                "AND COALESCE(i.deleted_at,'')='' AND COALESCE(p.expiry_managed,'')='yes' "
                "AND NOT EXISTS (SELECT 1 FROM batches b WHERE b.channel=i.channel AND b.sku=i.sku "
                "  AND COALESCE(b.barcode,'')=COALESCE(i.barcode,'') AND b.warehouse=i.warehouse "
                "  AND b.warehouse_type=i.warehouse_type AND COALESCE(b.exp_date,'')<>'')", [channel])
        return int((r or {}).get("n") or 0)
    except Exception as _e:
        try_err('cleansing', '效期批次缺口统计降级', _e)
        return 0


def _align_order_keys(channel, cleaned, chunk=3000):
    """订单四维业务键对齐(channel, order_no, sku, barcode)—— 2026-11-02 S2-5

    唯一键是四维, 而"码"在入口会被 `_fill_barcode_from_products` 补齐或保留, 于是**同一行在不同
    时间导入可能算出不同的码**: 首次导入时档案没码(存 ''), 之后档案补了码, 再导同批 →
    四维键变了 → 不是"更新那一行", 而是"再造一行" → 同一笔订单两行, 金额翻倍。
    反向同理: 本批缺码而库里已有码。

    所以写入前按 (channel, order_no) 前缀把已存在行读一次, 做两件事:
      ① 本批空码 + 库里同 (order_no,sku) 只有一个非空码 → 沿用库里的码。
         这是**不猜**: 只用来阻止同一行分叉, 不产生新信息(库里那行本来就带这个码)。
         多码 SKU(库里有 ≥2 个码, 或空码与有码并存)不参与对齐 —— 那是真的多行, 不该合并。
      ② 本批有码 + 库里有同 (order_no,sku) 的空码行 → 先把那行原地升级成新码(保住 id),
         之后 INSERT..ON DUPLICATE 正好命中它, 不会和它并成两行。

    顺带返回每个已存在键的 ordered_at 集合: upsert 可能把一行挪到新的业务日期, 旧日期也得
    重建 agg, 否则旧日期物化里留着这份数据 → 双计(调用方用它补登记)。

    返回 ({[order_no, sku]: {"codes": set, "dates": set}}, 补码行数, 升级行数)。

    **chunk=3000 是实测出来的, 不是拍的**(线上 179,061 行, 按 (channel,order_no) 索引前缀):
    IN 300 个 0.25s / 1000 个 0.72s / 3000 个 0.78s / 5000 个 1.26s —— 耗时几乎只跟**查询次数**
    有关, 跟键数关系很小(每次固定 ~0.7s 起步)。所以宁可用大 IN, 不要切成 300 一段:
    5000 行的文件按 300 切要 17 次查询 ≈ 12s, 按 3000 切只 2 次 ≈ 1.6s。
    """
    if not cleaned:
        return {}, 0, 0
    _nos = list(dict.fromkeys(str(it.get("order_no") or "").strip() for it in cleaned
                              if str(it.get("order_no") or "").strip()))
    if not _nos:
        return {}, 0, 0
    prev = {}
    for _i in range(0, len(_nos), chunk):
        _part = _nos[_i:_i + chunk]
        for _r in query("SELECT order_no, sku, barcode, ordered_at FROM orders "
                        "WHERE channel=%%s AND order_no IN (%s)"
                        % ",".join(["%s"] * len(_part)), [channel] + _part):
            _k = (str(_r.get("order_no") or ""), str(_r.get("sku") or ""))
            _p = prev.setdefault(_k, {"codes": set(), "dates": set()})
            _p["codes"].add(str(_r.get("barcode") or "").strip())
            _d = str(_r.get("ordered_at") or "")[:10]
            if _d:
                _p["dates"].add(_d)
    fixed = 0
    _promote = {}    # (order_no, sku) → 本批的新码; 同键多行只留一个(码相同)
    _conflict = set()
    for it in cleaned:
        _k = (str(it.get("order_no") or "").strip(), str(it.get("sku") or "").strip())
        _p = prev.get(_k)
        if not _p:
            continue
        _b = str(it.get("barcode") or "").strip()
        _codes = _p["codes"]
        if not _b:
            _nonempty = {c for c in _codes if c}
            if len(_nonempty) == 1 and "" not in _codes:
                it["barcode"] = next(iter(_nonempty))
                fixed += 1
        elif _b not in _codes and "" in _codes and _k not in _conflict:
            # 同键多行给了不同码 → 无法判断升级成哪个, 不猜(交给唯一键按原样处理)
            if _k in _promote and _promote[_k] != _b:
                _promote.pop(_k, None)
                _conflict.add(_k)
                continue
            _promote[_k] = _b
            _codes.discard("")
            _codes.add(_b)
    promoted = _promote_empty_barcode(channel, _promote)
    return prev, fixed, promoted


def _promote_empty_barcode(channel, pairs, chunk=500):
    """把库里"空码行"的 barcode 原地升级成本批的码(保住 id, 让 upsert 命中它而不是另起一行)

    为什么必须**批量**做: 实测(线上 TiDB, 200 条一条一语句 UPDATE 命中空集)222ms/条 ——
    5000 行整批换码要 18 分钟。改成一条 CASE 语句后 200 对 0.53s。
    WHERE 里的 CONCAT 用不上索引, 但 `barcode=''` 的行本来少, 每个 chunk 扫一遍可接受;
    参数化传键值, 不做字符串拼接(69 码可能含引号)。
    """
    if not pairs:
        return 0
    _items = list(pairs.items())
    done = 0
    for _i in range(0, len(_items), chunk):
        _part = _items[_i:_i + chunk]
        try:
            _sql = ("UPDATE orders SET barcode = CASE CONCAT(order_no,'|',sku) %s ELSE barcode END "
                    "WHERE channel=%%s AND (barcode IS NULL OR barcode='') "
                    "AND CONCAT(order_no,'|',sku) IN (%s)"
                    % (" ".join(["WHEN %s THEN %s"] * len(_part)), ",".join(["%s"] * len(_part))))
            _args = []
            for (_o, _s), _b in _part:
                _args += [_o + "|" + _s, _b]
            _args.append(channel)
            _args += [_o + "|" + _s for (_o, _s), _b in _part]
            execute(_sql, _args)
            done += len(_part)
        except Exception as _e:
            # 升级失败不退化为"再插一行"——那正是要避免的翻倍, 交给唯一键挡住
            try_err('cleanse_write', '空码行批量升级降级(%d 对)' % len(_part), _e)
    return done


def _write_rows(target, channel, conflict_mode, cleaned, conflict_sql="", ctx=None):
    """按 target 分派写入; 返回 (success, failed)
    渠道归属: 导入数据归属导入渠道(除非 mapping 显式映射了 channel 列)——否则未映射时落默认 jd 污染跨渠道

    conflict_sql: 透传给库存分支的 _write_batch, 覆盖默认的"同键全列覆盖"。
      seed 重灌演示库时用「不覆盖已有行」——那样才能看出"重复导入被幂等挡下"这件事,
      而不是把旧行改一遍还报成功。传空 = 现有导入行为完全不变。

    ctx(2026-11-06 「快速通道」): 文件级上下文, 由调用方在同一份文件的多个批次间复用。
      · 只合并**文件级动作**(归码档案索引、脏日标记), 行级规则(69码判定、四维键对齐、
        期初校准、可用重算、批次 FIFO)一条不少、逐行照跑 —— 所以结果与逐批导入一致;
      · 默认 None = 完全走原路径(真实上传导入不受影响, 不存在"两条口径");
      · 批次跑完必须调 flush_import_ctx() 把延后的脏日标记登记掉, 否则物化不会重建。
    """
    _ksql = conflict_sql
    _ctx = ctx if isinstance(ctx, dict) else None
    for it in cleaned:
        if "channel" not in it:
            it["channel"] = channel
    if target == "order":
        # 69 码入口对齐(2026-11-01 统一): 与 inbound/outbound/inventory 同一套四象限规则,
        #   见 _fill_barcode_from_products —— 原实现只补"空码"、不回写档案、不告警码不符
        try:
            _fill_barcode_from_products(channel, cleaned,
                                        cache=(_ctx.setdefault("bc", {}) if _ctx is not None else None))
        except Exception as _e:
            try_err('cleansing', '69码入口对齐降级(order)', _e)
        # 2026-11-02 S2-5 四维业务键对齐(必须放在补码之后 —— 补码会改变键)
        _prev, _fixed, _promoted = {}, 0, 0
        try:
            _prev, _fixed, _promoted = _align_order_keys(channel, cleaned)
        except Exception as _e:
            try_err('cleansing', '订单四维键预读降级(退化为按本批码直写)', _e)
        # 2026-11-02 S2-2: 先记下"这批数据落在哪些业务日期", 写成功后再登记重建。
        #   顺序不能反 —— 先标记后写入的话, 后台重建线程可能在写入完成前就把这几天
        #   重建完并清掉标记, 这批数据就永远进不了物化(且不会有任何报错)。
        _days = {str(_it.get("ordered_at") or "")[:10] for _it in cleaned}
        # upsert 会把已存在的行挪到新的 ordered_at —— 旧日期也要重建, 否则旧日期 agg 里
        #   还留着这一行, 新日期又加一遍 → 同一笔订单在两天各算一次
        _old_days = {_d for _p in _prev.values() for _d in (_p.get("dates") or set())}
        _res = _write_batch("orders",
                            ["order_no", "store", "warehouse", "sku", "product_name", "barcode",
                             "quantity", "unit_price", "total_amount", "order_status",
                             "ordered_at", "paid_at", "platform", "channel", "data_source",
                             "freight_amount", "subsidy_amount", "tax_amount", "discount_amount",
                             "actual_amount", "supplier", "remark"],
                            cleaned, conflict_mode, "order_no", "sku", channel=channel,
                            # 2026-10-17 69码三键优先(order_no|sku|barcode, 空barcode落二键): 同单同SKU同69码多行求和(quantity/金额)
                            agg_key3="barcode",
                            agg_sum=["quantity", "total_amount", "actual_amount", "discount_amount",
                                     "freight_amount", "subsidy_amount", "tax_amount"])
        # 只在真有行写成功时登记(全失败还去重建, 等于拿没变的数据白跑一遍物化)
        try:
            if int(_res[0] or 0) > 0:
                _nd = {_d for _d in (_days | _old_days) if _d}
                if _ctx is not None:
                    # 快速通道: 同一份文件几十个批次只登记一次(merge 进 ctx, 文件末 flush)。
                    #   mark_agg_days 本身是幂等 upsert, 合并只是省往返, 不改变最终标记集合。
                    _ctx.setdefault("days", set()).update(_nd)
                else:
                    mark_agg_days(channel, _nd, reason="order_import")
        except Exception as _em:
            # 标记失败不影响导入结果(数据已落库), 由每日 sweep 用 synced_at 反查补标记
            try_err('cleanse_write', '订单导入后登记重建降级', _em)
        return _res
    if target == "gmv_flow":
        # 2026-10-16 GMV 回款(资金流水——表B): platform+flow_no 去重——月度回款/在途数据源
        try:
            execute("CREATE TABLE IF NOT EXISTS gmv_flows (id BIGINT AUTO_INCREMENT PRIMARY KEY, platform VARCHAR(32) NOT NULL, shop VARCHAR(64) DEFAULT '', flow_no VARCHAR(64) NOT NULL, related_no VARCHAR(64) DEFAULT '', arrived_time DATETIME, amount DECIMAL(14,2) DEFAULT 0, flow_type VARCHAR(32) DEFAULT '', settled_amount DECIMAL(14,2) DEFAULT 0, channel VARCHAR(20) DEFAULT 'jd', data_source VARCHAR(32) DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE KEY uq_platform_flow_ch (channel, platform, flow_no))")
        except Exception as _etb:
            try_err('cleanse_write', 'gmv_flows 建表降级', _etb)
        # 2026-11-06 老表唯一键迁移移出逐行循环: 原来每处理一行就 SHOW INDEX 一次
        #   (900 行 = 900 次往返, ALTER 还可能重复执行), 是导入链路的纯浪费。
        #   这是**一次性 DDL**, 整批只需查一次; 新表由 schema_create.py 的版本门禁建好,
        #   这里只兼容历史库里仍是 uq_platform_flow(platform,flow_no) 的情况 ——
        #   跨渠道 platform+flow_no 撞键会让第二渠道整批 1062。
        try:
            from db import query as _qix
            _ix = _qix("SHOW INDEX FROM gmv_flows")
            _uq_cols = [str(r.get("Column_name") or "") for r in (_ix or [])
                        if r.get("Key_name") == "uq_platform_flow" and r.get("Column_name")]
            if _uq_cols and "channel" not in _uq_cols:
                execute("ALTER TABLE gmv_flows DROP INDEX uq_platform_flow")
                execute("ALTER TABLE gmv_flows ADD UNIQUE KEY uq_platform_flow_ch (channel, platform, flow_no)")
                try_err('cleanse_write', 'gmv_flows 唯一键迁移完成(channel 补入)', '')
        except Exception as _eix:
            try_err('cleanse_write', 'gmv_flows 唯一键迁移降级', _eix)
        for it in cleaned:
            it["channel"] = channel
            # 2026-10-16 钉死 1366 根因: amount/settled_amount 为 DECIMAL, 映射类型 string 时
            # 原样直写(如 ¥1,200.50/文本/空态) → TiDB 1366 整批失败。写入前强制 number 清洗
            # (与 _v 同规: 去 ¥,元/逗号/空格 → float; 无法解析归 0, 不炸批)
            for _nc in ("amount", "settled_amount"):
                if it.get(_nc) in (None, ""):
                    continue  # 空值跳过列(DECIMAL DEFAULT 0)
                try:
                    _s = str(it[_nc]).replace("¥", "").replace("元", "").replace(",", "").replace(" ", "").strip()
                    it[_nc] = float(_s) if _s not in ("", "-", "--") else 0.0
                except Exception:
                    it[_nc] = 0.0  # 无法解析归 0(计数成功——不做静默失败)
            # arrived_time 宽松归一(样例可能是 "2026-09-15 10:00:00" / 带 T / 仅日期):
            # 解析失败置 None(空值走缺列跳过——不 1366)
            if it.get("arrived_time") not in (None, ""):
                _at = str(it["arrived_time"]).strip()
                _norm = ""
                try:
                    from datetime import datetime as _dtn
                    for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                                 "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y年%m月%d日"):
                        try:
                            _norm = _dtn.strptime(_at, _fmt).strftime("%Y-%m-%d %H:%M:%S")
                            break
                        except Exception:
                            continue
                except Exception:
                    _norm = ""
                it["arrived_time"] = _norm
        return _write_batch("gmv_flows",
                            ["platform", "shop", "flow_no", "related_no", "arrived_time",
                             "amount", "flow_type", "settled_amount", "channel", "data_source"],
                            cleaned, conflict_mode, "platform", "flow_no",
                            agg_sum=["amount", "settled_amount"])
    if target in ("inventory", "platform_inv", "inventory_b"):
        wt = _TARGET_WH[target]
        # 69码入口对齐: inventory 本就按 (sku,barcode,warehouse,warehouse_type) 拆行,
        #   缺码会凭空多出一行空码库存, 与档案有码的流水对不上
        try:
            _fill_barcode_from_products(channel, cleaned)
        except Exception as _e:
            try_err('cleansing', '69码入口对齐降级(inventory)', _e)
        # 2026-11-01 快照类必须**拒写**"未归码"行:
        #   inventory 的唯一行是 (sku,码,仓,渠道,仓型)。档案有码、本行缺码、又不满足补码条件时,
        #   若照写会新增一行空码库存, 与既有有码行**并存** → 同一批货被算两次(可用量翻倍)。
        #   留空是错账, 补码是猜测, 拒写至少是"看得见的失败"。订单/出入库是流水, 空码不翻倍, 不在此列。
        _held_rows, _keep = [], []
        for it in cleaned:
            _why = it.pop("_bc_held", None)
            if _why:
                _held_rows.append((it, _why))
            else:
                _keep.append(it)
        cleaned = _keep
        # 2026-11-06 性能根治: `_ledger_started` → `core.ledger.begin_date` → `state_get`,
        #   **每次调用都发一条 SELECT** `ledger_state`(无进程内缓存)。它原先写在下面
        #   `for it in cleaned` 循环里 → **每行一次往返**。
        #   线上实测: 库存·JD·自有仓 2600 行 → 2600 次往返 → 单段 121s 撞穿网关硬限(504),
        #   任务反复被切成僵尸租约; 同期平台仓 8 万行只要 24s(它不走 own 分支)。
        #   账期状态在一次导入内不可能变, 循环外取一次即可, 与 §15.157「耗时只跟查询次数有关」
        #   是同一条账: 这里砍掉的正是「查询次数」, 判定口径一个字节都没动。
        _ledger_on = _ledger_started(channel) if wt == "own" else False
        for it in cleaned:
            # 2026-11-05 修: 原实现无条件 it["warehouse_type"] = wt, 把**文件里自己的仓型**抹掉。
            #   后果(线上实测): seed 的"平台仓"文件走 inventory_b 入口 → 北京/上海/成都等 7 个
            #   platform 仓全部被改写成 platform_b, 库存表里 platform_b 凭空 8 万行、platform 归零。
            #   真实客户同样会中招: 一份含"京东B仓 + 平台仓"的库存导出, 传上去仓型全变。
            #   正确语义: target 只决定**默认仓型**(文件没这一列/这一列为空时才用), 文件自己
            #   写明的仓型要尊重 —— 否则"导入什么仓, 系统就认什么仓"这条定案就是假的。
            _wt0 = str(it.get("warehouse_type") or "").strip()
            it["warehouse_type"] = _wt0 or wt
            # 2026-11-04 账期改造(用户定案): 已建账(结过账)后, 导入的"当时可用"**不再写成期初** ——
            #   写成期初就等于每次导入都把基线抬高一次, 而流水又是全期/滚动窗口, 必然双算,
            #   这是"每月累加一次、偏差单调增长"的根因。改为存 snapshot_qty 只做对账。
            #   首次建账(未结过账)仍以导入值作为基线 —— 那时它本来就是基线。
            if wt == "own":
                _bs = it.get("beginning_stock")
                if _bs in (None, "", 0):
                    _bs = it.get("available_qty") or 0
                if _ledger_on:
                    it["snapshot_qty"] = _bs
                    it.pop("beginning_stock", None)
                else:
                    it["beginning_stock"] = _bs
        # 2026-11-05 month_inbound/month_outbound 仍照写: 它们是**导入文件里的原始字段**,
        #   留档 + 供巡检脚本做"报表值 vs 流水值"对账(test_inv_recalc / task_seed)。
        #   但已不参与任何计算 —— 自有仓走流水聚合, 平台仓/B 仓不再读这两列(见 insights.py)。
        _s, _f, _ed = _write_batch("inventory",
                                   ["sku", "product_name", "store", "warehouse", "warehouse_type",
                                    "available_qty", "in_transit_qty", "safety_qty", "channel", "barcode",
                                    "beginning_stock", "month_inbound", "month_outbound", "locked_qty",
                                    "c_transit", "weight", "volume", "snapshot_qty"],
                                   cleaned, conflict_mode, "sku", "warehouse", channel=channel,
                                   agg_key3="barcode",  # 69码三键(sku|warehouse|barcode, 空落二键)
                                   agg_sum=["available_qty", "in_transit_qty", "c_transit", "locked_qty",
                                            "beginning_stock", "month_inbound", "month_outbound"],
                                   conflict_sql=_ksql)
        if _held_rows:
            _f += len(_held_rows)
            for _it, _why in _held_rows[:40]:
                _ed.append({
                    "sku": str(_it.get("sku") or "")[:40],
                    "reason": "未归码已拒写(%s): 该商品做效期管理或存在多个69码, 缺69码会与"
                              "有码行并存导致可用量翻倍。请补69码列后重导" % _why,
                    "row": str({k: _it.get(k) for k in ("sku", "warehouse", "barcode")})[:160]})
            try:
                try_err('cleansing', '库存行拒写 %d 行(未归码, 防可用量翻倍)' % len(_held_rows),
                        ValueError('首行原因: %s' % _held_rows[0][1]))
            except Exception:
                pass
        # 2026-11-05 期初实盘校准(用户定案): 上月末最后一天 ~ 当月 3 号之间的库存导入
        #   = 实盘最终量 → 覆盖本期期初(snapshot_qty 只是对账用, 不足以让"以导入为准"成立);
        #   窗口外只保留 snapshot_qty, 走正常结转。失败只留痕, 绝不阻断导入。
        #
        # 2026-11-05: 场景判定统一收到 core.ledger_adj.detect_scene —— 这里不再传"要不要建账"
        #   之类的开关(曾加过 baseline=True, 等于在文档 §15.152 之外开第三条口径, 后来回退)。
        #   本层只负责"导入成功后接线", 走 A/B/C/D 哪个场景由账期状态与导入时刻自己决定。
        if wt == "own":
            try:
                from core.clock import now_cn as _nowc
                from core.ledger_adj import apply_import as _adj_apply
                _adj_apply(channel, cleaned,
                           batch_id="inv-%s" % _nowc().strftime("%Y%m%d%H%M%S"))
            except Exception as _ea:
                try_err('cleansing', '期初实盘校准降级(导入本身已成功)', _ea)
        return _s, _f, _ed
    if target == "product":
        # 2026-11-01 效期管理标记归一(是/否 → yes/no); 非法值留痕并留空,
        #   不阻断写入(列必填拦在前端; 值非法由 cleansing.py done 步回显到结果 message, 见 §15.94)
        _bad_exp = []
        for it in cleaned:
            _v = _norm_expiry(it.get("expiry_managed"))
            if _v == "?":
                _bad_exp.append(str(it.get("sku") or ""))
                it["expiry_managed"] = ""
            else:
                it["expiry_managed"] = _v
        if _bad_exp:
            try:
                try_err('cleansing', '商品效期标记取值非法',
                        Exception("渠道 %s 有 %d 行「是否做效期管理」取值无法识别(例 sku=%s), 已留空 "
                                  "—— 仅接受 是/否; 留空期间出入库将按'做效期'处理并告警"
                                  % (channel, len(_bad_exp), _bad_exp[0])))
            except Exception:
                pass
        # 2026-11-05 补 supplier_code: 清洗页 PROD_FIELDS 里可选可映射(fields.ts:132)、
        #   products 表也有该列(schema_create:92), 但这份写入白名单漏了它 →
        #   用户映射"供应商编码"后导入成功、`products.supplier_code` 却恒空,
        #   供应商评分(supplier.score)/供应商维度统计全部断链, 且不报任何错。
        return _write_batch("products",
                            ["sku", "product_name", "barcode", "store", "category", "price",
                             "box_qty", "unit", "status", "channel", "brand", "weight", "volume",
                             "expiry_managed", "supplier_code"],
                            cleaned, conflict_mode, "sku", channel=channel,
                            agg_key3="barcode", agg_overwrite=True)  # 同 SKU+69码 覆盖末行档案
    if target == "supplier":
        return _write_batch("suppliers",
                            ["supplier_code", "supplier_name", "contact_person", "contact_phone",
                             "score", "status", "channel", "brand"],
                            cleaned, conflict_mode, "supplier_code", channel=channel,
                            agg_overwrite=True)  # 同 supplier_code 覆盖末行档案
    if target == "inbound":
        cleaned = _fill_warehouse_type(channel, cleaned, "inbound")
        # 69码入口对齐: 档案有码而流水缺码 → 补齐(避免下游按码聚合时落到空码组)
        try:
            _fill_barcode_from_products(channel, cleaned)
        except Exception as _e:
            try_err('cleansing', '69码入口对齐降级(inbound)', _e)
        s, f, ed = _write_batch("inbound_records",
                            ["sku", "product_name", "quantity", "supplier", "inbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status", "remark",
                             "warehouse_type"],
                            cleaned, conflict_mode, "sku", "inbound_date",
                            agg_key3="barcode", agg_sum=["quantity", "price"])
        # 批次效期同步(batches): 入库记录含 prod_date/exp_date(按映射列抓取) → 按 SKU×仓 维护
        # 更新方式联动导入冲突模式(conflict_mode): overwrite=删旧插新(文件即权威) / sum=同效期 qty 累加
        # (效期管控数据源: 进销存批次效期预警/临期处置建议)
        try:
            _br = [c for c in cleaned if (c.get("prod_date") or "") or (c.get("exp_date") or "")]
            if _br:
                _sync_batches(channel, _br, conflict_mode)
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
        return s, f, ed
    if target == "outbound":
        cleaned = _fill_warehouse_type(channel, cleaned, "outbound")
        # 69码入口对齐: 出库码缺失会让批次按 FEFO 降级扣减(精确匹配失效)
        try:
            _fill_barcode_from_products(channel, cleaned)
        except Exception as _e:
            try_err('cleansing', '69码入口对齐降级(outbound)', _e)
        return _write_batch("outbound_records",
                            ["sku", "product_name", "quantity", "target_warehouse", "outbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status", "remark",
                            "warehouse_type"],
                            cleaned, conflict_mode, "sku", "outbound_date", channel=channel,
                            agg_key3="barcode", agg_sum=["quantity", "price"])
    return 0, len(cleaned), []


def flush_import_ctx(channel, ctx):
    """快速通道收尾: 把延后的**文件级动作**一次性落地(当前只有脏日标记)。

    必须在同一份文件的最后一个批次之后调用。不调不会写错数据, 但物化不会重建 ——
    表现为"订单已落库, 看板还是旧值", 比报错更难查, 所以调用方要像 close() 一样必调。
    返回本次登记的日期数(便于 seed 在窗口摘要里显示)。
    """
    if not isinstance(ctx, dict):
        return 0
    _days = ctx.pop("days", None)
    if not _days:
        return 0
    try:
        return mark_agg_days(channel, _days, reason="order_import")
    except Exception as _e:
        try_err('cleanse_write', '快速通道脏日登记降级(改逐批兜底)', _e)
        return 0


# 2026-10-29 自有类仓型白名单(可扩展): 出入库只允许这些仓型
# 未来新增自有仓类型(如区域仓/前置仓)只需在此追加, 其余逻辑自动跟随
SELF_WAREHOUSE_TYPES = ("own",)


def _ledger_started(channel):
    """该渠道是否已建账(结过账)。

    是 → 库存导入不再把"当时可用"写成期初(只存 snapshot_qty 做对账);
    否 → 首次建账, 导入值就是基线。表没建/查询失败一律按"未建账"处理(等价旧行为, 不会写错)。
    """
    try:
        from core.ledger import begin_date as _bd
        return bool(_bd(channel))
    except Exception:
        return False


def _is_self_wt(wt):
    return str(wt or "").strip() in SELF_WAREHOUSE_TYPES


def _fill_warehouse_type(channel, cleaned, target):
    """仓型归属(2026-10-29 业务定案): **出入库只属于自有仓维度**

    业务事实: 平台仓/京东B仓的数据只能从平台后台导出, 没有"出入库单据"概念;
    它们的在途(供应商→B仓 / B→C调拨)由【库存】类型导入的 in_transit_qty 承载。
    → 出入库导入一律归 own(未来多自有仓仍为 own, 用 warehouse 区分);
      文件里填了其它仓型会被纠正并留痕(避免误把平台数据当自有仓台账)
    """
    if target in ("inbound", "outbound"):
        fixed = 0
        for c in cleaned:
            wt0 = str(c.get("warehouse_type") or "").strip()
            if wt0 and not _is_self_wt(wt0):
                fixed += 1
                c["warehouse_type"] = SELF_WAREHOUSE_TYPES[0]  # 非自有类 → 纠正为首个自有仓型
            elif not wt0:
                c["warehouse_type"] = SELF_WAREHOUSE_TYPES[0]  # 未填 → 默认自有仓
            # 已在白名单内(如未来 own_a) → 原样保留
        if fixed:
            try_err('cleansing', '出入库仓型纠正(非自有类 %d 行 → %s)' % (fixed, SELF_WAREHOUSE_TYPES[0]),
                    ValueError('warehouse_type corrected'))
        return cleaned
    return cleaned


def _write_batch(table, allowed_cols, cleaned, conflict_mode, *key_cols, channel="jd", agg_sum=None, agg_overwrite=False, agg_key3=None, conflict_sql=""):
    """批量写入: 仅保留存在的列; 冲突策略——
    inbound/outbound(sum/overwrite 前端适配面): sum=数量累加, overwrite=全列覆盖
    inventory: 同键全列覆盖(快照, 重导=刷新此刻数量)
    orders: 同键全列覆盖 + deleted_at 恢复(四维键 upsert, 重导幂等)  ← 2026-11-02 S2-5
    product/supplier: 统一全列覆盖(对齐 PA ON CONFLICT DO UPDATE)
    2026-10-17 文件内同键聚合(新导入数据内部: 同组合键多行 → 合并, 不撞唯一键 1062):
    agg_sum=求和列集(数量/金额类——order/inventory/gmv_flow/inbound/outbound 同键多行求和一条);
    agg_overwrite=True(同键覆盖末行——product/supplier 档案类 upsert 语义); None=不聚合
    agg_key3=69码三键(订单/库存/商品/出入库: 组合键 + 69码 优先, 69码空落二键——用户定)

    conflict_sql (2026-11-05 seed 走导入链路): 调用方显式给出 ON DUPLICATE KEY UPDATE 子句,
      覆盖上面按表名推断的默认冲突策略。seed 用它把"流水/订单已存在就不动"这条语义
      写全 —— 键里带 deleted_at(与业务唯一键逐列一致)时, 缺失的 updated 子句会让
      ON DUPLICATE 静默退化成"什么都不做"; 写清楚是唯一能让语义与导入对齐的办法。
      传了它就完全绕过 sum/inv/ord 三套默认推断, 避免两套规则打架。
    """
    if not cleaned:
        return 0, 0, []
    keys = list(dict.fromkeys(key_cols))  # 保序 list(曾 set 无序——查重 OR keys[0]/keys[1] 下标崩)
    # 2026-10-17 分派式文件内聚合(按 target 组合键/字段特殊适配——不能一刀切)
    if agg_sum is not None and len(keys) >= 1 and len(cleaned) > 1:
        _agg_sum_cols = [c for c in agg_sum if c in allowed_cols]
        _agg_map = {}
        _agg_order = []
        for it in cleaned:
            _base = tuple(str(it.get(k) or "") for k in keys)
            if not all(_base):
                continue  # 缺键行留 _write_batch 原判失败逻辑
            # 69码三键优先: barcode 非空 → 键+69码; 空 → 落二键(与 dup-check/force_new 同规则)
            _b3 = str(it.get(agg_key3) or "").strip() if agg_key3 else ""
            _k = _base + ((_b3,) if _b3 else ())
            if _k not in _agg_map:
                _agg_map[_k] = dict(it)
                _agg_order.append(_k)
            else:
                _cur = _agg_map[_k]
                for _c in _agg_sum_cols:
                    try:
                        _cur[_c] = float(_cur.get(_c) or 0) + float(it.get(_c) or 0)
                    except Exception:
                        pass  # 非数字保持原值
        if _agg_order:
            cleaned = [_agg_map[k] for k in _agg_order]
    elif agg_overwrite and len(keys) >= 1 and len(cleaned) > 1:
        # 档案类(products/suppliers): 同键覆盖末行(upsert 语义——同 SKU 多行取最后一行档案)
        _agg_map = {}
        _agg_order = []
        for it in cleaned:
            _base = tuple(str(it.get(k) or "") for k in keys)
            if not all(_base):
                continue
            _b3 = str(it.get(agg_key3) or "").strip() if agg_key3 else ""
            _k = _base + ((_b3,) if _b3 else ())
            if _k not in _agg_map:
                _agg_map[_k] = dict(it)
                _agg_order.append(_k)
            else:
                _agg_map[_k] = dict(it)  # 覆盖末行
        if _agg_order:
            cleaned = [_agg_map[k] for k in _agg_order]
    success = failed = 0
    err_details = []
    qty_cols = [c for c in ("quantity", "available_qty") if c in allowed_cols]
    # 2026-10-16: 数字列兜底(缺失列补 0 而非 KeyError——曾空值跳键 → tuple(r[c]) KeyError 整批降级)
    _num_fill = {c for c in ("quantity", "available_qty", "amount", "settled_amount", "price",
                             "total_amount", "unit_price", "freight_amount", "subsidy_amount",
                             "tax_amount", "discount_amount", "actual_amount", "safety_qty",
                             "in_transit_qty", "c_transit", "locked_qty", "weight", "volume",
                             "box_qty", "score", "beginning_stock", "month_inbound",
                             "month_outbound", "turnover_days") if c in allowed_cols}
    # sum 累加仅限出入库记录(HammerCleansing 只在 inbound/outbound 暴露 sum/overwrite 选择)
    sum_mode = table in ("inbound_records", "outbound_records") and conflict_mode == "sum"
    # 2026-11-01 库存 upsert = **覆盖**: inventory 是快照(此刻有多少), 不是流水(累加量)。
    #   2026-11-01 才给 inventory 加业务唯一键(sku,barcode,warehouse,channel,warehouse_type);
    #   在此之前没有唯一键, 重复导入是"另插一行"(行数翻倍, 肉眼可见); 加键之后若不声明 upsert
    #   语义, 会直接 1062 整行失败。两种都不对 —— 正确的是覆盖(同一份文件重导应幂等)。
    inv_upsert = table == "inventory"
    # 2026-11-02 S2-5 订单 = 四维业务键(channel, order_no, sku, barcode) 上的 upsert:
    #   重导同一份文件结果不变(不再整批 1062)。语义是**覆盖**不是累加 —— 订单行是"这笔订单的
    #   这一行", 累加会让重导翻倍; 同键多行在批内已由 agg_sum 合并过了。
    #   deleted_at 显式清空: 源文件里有这条就以源文件为准恢复。不清的话, upsert 会照常更新
    #   各列但让行留在回收站 —— 用户看到"成功 N 条", 数据却不出现, 且无从察觉。
    #   实测(TiDB, 2000 行/1000 一批): 纯 INSERT 2.14s vs upsert 1.03s —— 没有注释里说的
    #   "ON DUP 慢 10 倍", 那条结论来自旧表结构, 不适用于有四维唯一键的现在。
    ord_upsert = table == "orders"
    # 写批 500 → 2000(2026-11-05 TiDB 实测: 单条 INSERT 40 万行 2.948s / 4.9MB,
    #   64MB max_allowed_packet 的 7.6%, 写入行数不是瓶颈; RU 按**写请求次数**计)。
    #   500/片时内层 1000/批永远只跑一次, 实际写批 = 500 行 —— 两个值必须一起抬。
    #   列并集按片算, 片变大后补的默认值(数值 0 / 其余 '')与相关表列默认值一致,
    #   已逐表核对 information_schema, 不会把缺列行写成错值。
    for i in range(0, len(cleaned), 2000):
        chunk = cleaned[i:i + 2000]
        rows = []
        for it in chunk:
            row = {}
            ext = {}
            for c, v in it.items():
                if c == "_meta" or v is None or str(v) == "":
                    continue
                if c in allowed_cols:
                    row[c] = v
                else:
                    # 自定义列(用户动态新增, 非标准字段) → ext_json 合并存储
                    ext[c] = v
            if ext:
                row["ext_json"] = json.dumps(ext, ensure_ascii=False)
            # 严谨性: key 列必须存在且有值(原 `k in row` 跳过缺失列 → 无有效映射的导入
            # 也会写入空记录污染数据); 缺 key 的行判失败 + 留痕原因(2026-10-16: 曾只 failed++
            # 无明细——gmv_flow 5 次导入 failed=15 全无因可查——用户无法定位缺哪个必填列)
            if not row or not all(k in row and row.get(k) for k in keys):
                failed += 1
                if len(err_details) < 50:
                    _miss = [str(k) for k in keys if not (k in row and row.get(k))]
                    err_details.append({
                        "sku": str(it.get("sku") or it.get("flow_no") or it.get("platform") or "")[:40],
                        "reason": "缺少必填列: %s" % "、".join(_miss),
                        "row": str({k: it.get(k) for k in keys})[:200]})
                continue
            rows.append(row)
        if not rows:
            continue
        # 2026-10-16 写入层不再逐行查重(查重在入口/唯一键)——此处只拼一条 INSERT
        # 2026-11-02: orders/inventory 走 ON DUPLICATE 覆盖, 其余(sum_mode 之外)靠唯一键兜底
        # 2026-10-16 列并集: 曾 cols=rows[0] 键——首行缺列(空值跳键)时后续行该列静默丢弃——
        # 并集 + .get 兜底(缺失补默认)保列完整(不丢数据)
        cols = list(dict.fromkeys(c for r in rows for c in (r.keys() if isinstance(r, dict) else [])))
        sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (
            table, ", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols)))
        if conflict_sql:
            # 调用方显式声明的冲突策略(见函数 docstring) —— 优先于下面三套按表名推断
            sql += " ON DUPLICATE KEY UPDATE %s" % conflict_sql
        elif sum_mode:
            # inbound/outbound + sum: 数量列累加, 其余列覆盖
            upd = []
            for c in cols:
                if c in qty_cols:
                    upd.append("`%s` = `%s` + VALUES(`%s`)" % (c, c, c))
                elif c not in keys and c not in ("id", "deleted_at"):
                    upd.append("`%s` = VALUES(`%s`)" % (c, c))
            if upd:
                sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(upd)
        elif inv_upsert:
            # 同键全列覆盖(排除代理键与软删标记): 重导同一份库存文件结果不变
            _iupd = ["`%s` = VALUES(`%s`)" % (c, c) for c in cols
                     if c not in ("id", "deleted_at")]
            if _iupd:
                sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(_iupd)
        elif ord_upsert:
            # 同键全列覆盖 + 软删恢复(见上方 ord_upsert 注释)
            _oupd = ["`%s` = VALUES(`%s`)" % (c, c) for c in cols
                     if c not in ("id", "deleted_at")]
            _oupd.append("`deleted_at` = ''")
            sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(_oupd)
        else:
            # 2026-10-15 性能: 非 sum 模式已做已存在跳过(rows 全新键)——纯 INSERT 无 ON DUPLICATE(executemany+ON DUP 对 TiDB 慢 10 倍——seed 5000/批 36s vs 此处 5.5 分钟)
            pass
        # 批量写入(2000/批 executemany 单语句——TiDB 单语句原子无需事务; 事务(5000行大=持锁慢/小批往返多)均慢)。
        #   与上层清洗切片同为 2000, 一次 executemany 就是一条 2000 行多值 INSERT,
        #   写请求数 = ceil(行数/2000); 事务仍不启用(单语句本身原子)。
        for _bi in range(0, len(rows), 2000):
            _b = rows[_bi:_bi + 2000]
            try:
                executemany(sql, [tuple(r.get(c, 0 if c in _num_fill else "") for c in cols) for r in _b])
                success += len(_b)
            except Exception as _be:
                # 2026-10-16 留痕(曾无痕——批失败被吞——窗口5 计数 4000/1000 无法定位)
                try_err('cleanse_write', '写入批失败降级: %s (表 %s, 批 %d 行)' % (str(_be)[:120], table, len(_b)), _be)
                # 批失败(单语句原子——无部分写入)→ 单行降级(精确计数)
                for r in _b:
                    try:
                        execute(sql, [r[c] for c in cols])
                        success += 1
                    except Exception as e1:
                        failed += 1
                        try_err('cleanse_write', '单行写失败 %s: %s' % (r.get("sku") or "?", str(e1)[:90]), e1)
                        if len(err_details) < 50:
                            err_details.append({"sku": str(r.get("sku") or r.get("supplier_code") or ""),
                                                "reason": str(e1)[:80], "row": str(r)[:200]})
    return success, failed, err_details

def _sync_batches(channel, rows, conflict_mode="overwrite"):
    """批次效期同步(入库记录导入含 prod_date/exp_date 列时): 按 SKU×仓 维护 batches
    (效期管控数据源: 进销存批次效期预警/临期处置建议)
    更新方式联动导入冲突模式:
    - overwrite(默认): 文件即权威 —— 删该 SKU×仓 旧批次, 插入文件批次
    - sum: 累加 —— 同 SKU×仓×效期(prod_date+exp_date)批次 qty 累加(多次入库同批次累加), 不同效期独立行"""
    from db import executemany as _em
    if not rows:
        return 0
    touched = set()
    ins = []
    for c in rows:
        sku = c.get("sku")
        if not sku:
            continue
        wh = str(c.get("warehouse") or "")
        wt = str(c.get("warehouse_type") or "")
        pd = str(c.get("prod_date") or "")[:10]
        ed = str(c.get("exp_date") or "")[:10]
        touched.add((sku, wh, wt))
        ins.append({"sku": sku, "warehouse": wh, "warehouse_type": wt, "channel": channel,
                    "prod_date": pd, "exp_date": ed,
                    "qty": int(c.get("available_qty") or c.get("quantity") or c.get("qty") or 0)})
    if conflict_mode == "sum":
        # 2026-11-05 批量化(线上事故根因): 原实现**逐行**发 2 条 SQL(SELECT + UPDATE/INSERT),
        #   一份 2 万行的入库单 = 4 万次往返。RU/耗时按「查询次数」计而非行数 —— 单请求
        #   实测 123s 被网关 120s 硬切, step14 永远跑不完。改成「内存聚合 → 分批预读 →
        #   批量写」后降到几十次往返, 语义完全不变(同 SKU×仓×渠道×效期 qty 累加, 无则插入)。
        # ① 本批按业务键聚合(与旧 SELECT 的 WHERE 逐列一致: 不含 warehouse_type)
        _agg, _wt = {}, {}
        for it in ins:
            _k = (it["sku"], it["warehouse"], it["channel"], it["prod_date"], it["exp_date"])
            _agg[_k] = int(_agg.get(_k, 0)) + int(it["qty"] or 0)
            _wt.setdefault(_k, it["warehouse_type"])
        # ② 按 sku IN 分批读已存在批次(每片 500 个 SKU, 1 万 SKU ≈ 20 次查询)
        _exist = {}
        _skus = sorted({k[0] for k in _agg})
        for _i in range(0, len(_skus), 500):
            _part = _skus[_i:_i + 500]
            try:
                for _b in query("SELECT id, sku, warehouse, prod_date, exp_date, qty FROM batches "
                                "WHERE channel=%s AND sku IN (%s)"
                                % ",".join(["%s"] * len(_part)), [channel] + _part):
                    _bk = (str(_b.get("sku") or ""), str(_b.get("warehouse") or ""),
                           channel, str(_b.get("prod_date") or "")[:10],
                           str(_b.get("exp_date") or "")[:10])
                    _exist[_bk] = (int(_b.get("id") or 0), int(_b.get("qty") or 0))
            except Exception as _e:
                try_err('cleansing', '批次累加预读降级', _e)
        # ③ 内存分派 → executemany 批量 UPDATE / INSERT
        _upd, _new = [], []
        for _k, _qty in _agg.items():
            _e = _exist.get(_k)
            if _e:
                _upd.append((_e[1] + _qty, _e[0]))
            else:
                _new.append((_k[0], _k[1], _wt.get(_k, ""), _k[2], _k[3], _k[4], _qty))
        if _upd:
            try:
                _em("UPDATE batches SET qty=%s WHERE id=%s", _upd)
            except Exception as _e:
                try_err('cleansing', '批次累加批量更新降级', _e)
        for _j in range(0, len(_new), 2000):
            try:
                _em("INSERT INTO batches(sku, warehouse, warehouse_type, channel, "
                    "prod_date, exp_date, qty) VALUES(%s,%s,%s,%s,%s,%s,%s)", _new[_j:_j + 2000])
            except Exception as _e:
                try_err('cleansing', '批次累加批量插入降级', _e)
        return len(ins)
    # overwrite: 文件即权威 —— 删本批涉及的 (sku, warehouse) 旧批次。原实现逐组合 DELETE
    #   同样是 O(行数) 往返, 改成行构造器 IN 一次删一片(语义逐列一致: 不含 warehouse_type)。
    _tlist = sorted(touched)
    for _i in range(0, len(_tlist), 500):
        _part = _tlist[_i:_i + 500]
        _args = [channel]
        for _s, _w, _wt in _part:
            _args += [_s, _w]
        try:
            execute("DELETE FROM batches WHERE channel=%s AND (sku, warehouse) IN (%s)"
                    % ",".join(["(%s,%s)"] * len(_part)), _args)
        except Exception as _e:
            try_err('cleansing', '批次覆盖批量清理降级', _e)
    for i in range(0, len(ins), 2000):
        chunk = ins[i:i + 2000]
        cols = list(chunk[0].keys())
        try:
            _em("INSERT INTO batches(%s) VALUES(%s)" % (
                ", ".join("`%s`" % x for x in cols), ", ".join(["%s"] * len(cols))),
                [tuple(r[c] for c in cols) for r in chunk])
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    return len(ins)
