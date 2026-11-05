"""账期(月结)核心: 期初结转 / 结账 / 批次月初余额切片

口径(2026-11-04 用户定案, 甲方案):
  · 本月期初 = 上月期末(月末 23:59:59 的结存) —— 会计等价, 不是反推
  · 可用 = 期初 + 本月入库 − 本月出库(自有仓按计算来; 平台仓/B仓仍以导出为准)
  · 某期没结(数据没导全) → 重算窗口自动从最近已结账月末起算, 结果是真值, 不是兜底
  · 批次月初余额必须**带日期**(batch_opening), 否则上月批次退化成无日期期初批次,
    指定批次出库全部命中不了, 只能降级 FIFO
  · 自动结账: 每月 1 号; 数据不齐 → 拒绝结账并留痕, 绝不结假账

只有自有仓(own)走账期; 平台仓/B仓由后台导出即权威, 不进本模块。
"""
from db import query, one, execute, executemany

from core.clock import now_cn, month_bounds_cn

# 走账期的仓型(与 cleanse_write.SELF_WAREHOUSE_TYPES 同一语义; 这里只用于默认值, 不复制真源)
OWN_WT = "own"
STATUS_BOOTSTRAP = "bootstrap"   # 尚未结过账: 起算日为空 → 重算走全期(首次建账)
STATUS_CLOSED = "closed"         # 该账期已结, begin_date = 该期 1 号
# 结账容差: 流水最后一天与账期末尾相差不超过 N 天视为"导全"(月末两天常无出入库)
CLOSE_TOLERANCE_DAYS = 2


# ── 账期字符串工具(纯函数, 便于单测) ──────────────────────────────────────

def period_of(dt=None):
    """北京时间日期 → 'YYYY-MM'"""
    return (dt or now_cn()).strftime("%Y-%m")


def shift_period(period, n):
    """账期前后平移 n 个月; 入参非法返回 ''"""
    try:
        y, m = int(period[:4]), int(period[5:7])
    except (ValueError, TypeError):
        return ""
    idx = (y * 12 + (m - 1)) + int(n)
    return "%04d-%02d" % (idx // 12, idx % 12 + 1)


def period_bounds(period):
    """'YYYY-MM' → (期初日, 期末日) 字符串; 非法返回 ('', '')"""
    import calendar
    try:
        y, m = int(period[:4]), int(period[5:7])
        last = calendar.monthrange(y, m)[1]
    except (ValueError, TypeError):
        return "", ""
    return "%s-01" % period, "%s-%02d" % (period, last)


def period_days(period):
    """该账期的自然月天数(28/29/30/31); 非法返回 0"""
    a, b = period_bounds(period)
    if not a:
        return 0
    return int(b[-2:])


def period_bounds_excl(period):
    """'YYYY-MM' → (期初日, **下月 1 日**) —— 右开区间

    流水表的时间列是 DATETIME。写 `inbound_date <= '2026-10-31'` 只会覆盖到
    10-31 00:00:00, **整天的流水被静默丢掉**: 实测 2026-10-31 当天就有
    19,405 入库 / 23,704 出库, 结账期末因此少 42,809。所以统一用
    `>= 期初日 AND < 下月 1 日`, 不依赖当日最后一秒的精度写法。
    """
    start, _ = period_bounds(period)
    nxt = shift_period(period, 1)
    if not start or not nxt:
        return "", ""
    return start, "%s-01" % nxt


def elapsed_days(period):
    """该账期**已过**的自然日数 —— 当月未满时算周转必须用它, 否则分母只有几天出库、
    分子却乘整月天数, 周转虚高数倍(11-04 用 4 天出库配 30 天, 实测虚高约 7 倍)。

    · 已过去的账期 → 整月天数
    · 当前账期 → 今天是几号
    · 未来账期 → 0
    """
    cur = period_of()
    if period < cur:
        return period_days(period)
    if period > cur:
        return 0
    return now_cn().day


def is_current(period):
    return period == period_of()


# ── 账期状态(每 渠道×仓型 一行) ────────────────────────────────────────────

def state_get(channel, warehouse_type=OWN_WT):
    """读账期状态; 无记录时返回内存默认值(不写库 —— 首次建账前不需要任何写入)

    表还没建(冷启动先于 ensure_schema)时同样返回默认值: 重算链路的入口不能因为
    一张新表没就绪就整体挂掉, 此时等价于"尚未建账", 走全期窗口, 是正确降级。
    """
    try:
        row = one("SELECT period, status, begin_date, reason, rows_count, closed_at "
                  "FROM ledger_state WHERE channel=%s AND warehouse_type=%s",
                  [channel, warehouse_type])
    except Exception:
        row = None
    if row:
        return {"period": str(row.get("period") or ""),
                "status": str(row.get("status") or STATUS_BOOTSTRAP),
                "begin_date": str(row.get("begin_date") or ""),
                "reason": str(row.get("reason") or ""),
                "rows": int(row.get("rows_count") or 0),
                "closed_at": str(row.get("closed_at") or "")}
    # period 必须是空串: 它的语义是「**已结账**的账期」, 不是「当前账期」。
    #   这里曾返回 period_of(), 后果是首次结账时链条判断看到 period=当月,
    #   误认为"已经结到当月了", 于是把上一期当成跳期拒绝 —— 建账第一步就卡死
    #   (2026-11-05 线上实证: 结 2026-10 被拒「请先结上月 2026-09」)。
    #   需要"当前账期"的调用方走 current_period(), 那里以 status==CLOSED 为准。
    return {"period": "", "status": STATUS_BOOTSTRAP, "begin_date": "",
            "reason": "", "rows": 0, "closed_at": ""}


def begin_date(channel, warehouse_type=OWN_WT):
    """重算窗口下界(含)。'' = 尚未建账 → 不限日期(首次建账走全期)

    这是"可用 = 期初 + Σ流水"里 Σ 的起点: 结过账就是本月 1 号,
    没结过就是空(全期), 所以某期漏结不会让数据变错, 只会让窗口变长。
    """
    return state_get(channel, warehouse_type).get("begin_date") or ""


def current_period(channel, warehouse_type=OWN_WT):
    """当前生效账期 = 最近已结账期的下一期(未结过则是本月)"""
    st = state_get(channel, warehouse_type)
    if st.get("status") == STATUS_CLOSED and st.get("period"):
        return shift_period(st["period"], 1) or period_of()
    return period_of()


def _set_state(channel, warehouse_type, period, status, begin, reason="", rows=0):
    execute("INSERT INTO ledger_state(channel, warehouse_type, period, status, begin_date, "
            "reason, rows_count, closed_at) VALUES(%s,%s,%s,%s,%s,%s,%s,NOW()) "
            "ON DUPLICATE KEY UPDATE period=VALUES(period), status=VALUES(status), "
            "begin_date=VALUES(begin_date), reason=VALUES(reason), rows_count=VALUES(rows_count), "
            "closed_at=NOW()",
            [channel, warehouse_type, period, status, begin, str(reason or "")[:500], int(rows)])


# ── 结账前体检: 上期流水到底导全了没有 ────────────────────────────────────

def close_check(period, channel, warehouse_type=OWN_WT):
    """返回 (ok, reason)。只读, 不写库 —— 给"自动结账"和"手动结账"共用

    判据: 入库/出库流水在该账期内的**最后一天**距期末不超过容差天数。
    宁可拒绝结账, 也不结一个缺两天流水的假账(期末会偏大, 且会结转进下月期初)。
    """
    start, end_x = period_bounds_excl(period)
    end = period_bounds(period)[1]
    if not start:
        return False, "账期格式非法: %s" % period
    if period >= period_of():
        return False, "账期 %s 尚未结束, 不能结账" % period
    for table, col, label in (("inbound_records", "inbound_date", "入库"),
                              ("outbound_records", "outbound_date", "出库")):
        try:
            row = one("SELECT MAX(%s) AS mx FROM %s WHERE channel=%%s AND %s >= %%s AND %s < %%s"
                      % (col, table, col, col), [channel, start, end_x]) or {}
        except Exception as e:
            return False, "体检失败(%s): %s" % (label, e)
        mx = str(row.get("mx") or "")[:10]
        if not mx:
            return False, "%s流水在 %s 一整月都没有数据" % (label, period)
        gap = _days_between(mx, end)
        if gap > CLOSE_TOLERANCE_DAYS:
            return False, ("%s流水只到 %s, 距期末 %s 还差 %d 天 —— 请先补导再结账"
                           % (label, mx, end, gap))
    return True, ""


def _days_between(a, b):
    """两个 YYYY-MM-DD 之间的天数(b - a); 解析失败返回 9999(视为不齐)"""
    from datetime import datetime
    try:
        return (datetime.strptime(b[:10], "%Y-%m-%d") - datetime.strptime(a[:10], "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return 9999


# ── 结账主体 ──────────────────────────────────────────────────────────────

def _flow_agg(table, date_col, period, channel, wh_types):
    """按 五维(sku+69码+仓+仓型) 归集某账期流水; 返回 {(sku,bc,wh,wt): qty} + 无仓型兜底键

    日期用右开区间 `< 下月 1 日`: 见 period_bounds_excl 的说明(末日整天会被 `<=` 吞掉)。
    """
    start, end_x = period_bounds_excl(period)
    ph = ",".join(["%s"] * len(wh_types))
    rows = query("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, "
                 "COALESCE(warehouse_type,'') AS wt, SUM(quantity) AS q "
                 "FROM `%s` WHERE channel=%%s "
                 "AND (COALESCE(warehouse_type,'')='' OR warehouse_type IN (%s)) "
                 "AND `%s` >= %%s AND `%s` < %%s "
                 "GROUP BY sku, bc, warehouse, wt" % (table, ph, date_col, date_col),
                 [channel] + list(wh_types) + [start, end_x]) or []
    out = {}
    for r in rows:
        out[(str(r.get("sku") or ""), str(r.get("bc") or ""),
             str(r.get("warehouse") or ""), str(r.get("wt") or ""))] = int(r.get("q") or 0)
    return out


def _net_before(channel, warehouse_type, start):
    """账期起点**之前**的累计净流水(入 − 出), key = (sku, 69码, 仓)

    只给首次建账(bootstrap)用。原因: `beginning_stock` 不是"上月期末", 而是
    **流水起点之前的基线**(可用 = 基线 + 全期流水 是系统恒等式, 实测吻合)。
    直接拿它当某个月期初, 会把"基线之后、该月之前"整段流水漏掉 ——
    实测 2026-10 会因此少 79,315(8 月 +10,565 / 9 月 +68,750)。
    """
    out = {}
    for table, col, sign in (("inbound_records", "inbound_date", 1),
                             ("outbound_records", "outbound_date", -1)):
        rows = query("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, SUM(quantity) AS q "
                     "FROM `%s` WHERE channel=%%s AND `%s` < %%s "
                     "AND (COALESCE(warehouse_type,'')='' OR warehouse_type=%%s) "
                     "GROUP BY sku, bc, warehouse" % (table, col),
                     [channel, start, warehouse_type]) or []
        for r in rows:
            k = (str(r.get("sku") or ""), str(r.get("bc") or ""), str(r.get("warehouse") or ""))
            out[k] = out.get(k, 0) + sign * int(r.get("q") or 0)
    return out


def _pick(agg, sku, bc, wh, wt):
    """取某库存行的流水合计: 先四维精确, 再降级"流水未标仓型"(空串)那一档"""
    v = agg.get((sku, bc, wh, wt))
    if v is not None:
        return v
    return agg.get((sku, bc, wh, ""), 0)


def build_rows(period, channel, warehouse_type=OWN_WT):
    """算出该账期每一行的 期初/入/出/期末(不写库), 供 dry-run 与结账共用

    期初来源, 按优先级:
      ① 上月已结账 → 直接取 inventory_month 的 end_qty(结转, 会计等价)
      ② 首次建账(没结过) → beginning_stock(流水起点之前的基线) **+ 基线之后到
         账期起点之前的净流水**。少了后半段, 期初会低估一整段历史流水。
    """
    start, _end = period_bounds(period)
    prev = shift_period(period, -1)
    ins = _flow_agg("inbound_records", "inbound_date", period, channel, [warehouse_type])
    outs = _flow_agg("outbound_records", "outbound_date", period, channel, [warehouse_type])
    prev_end = {}
    if prev:
        for r in query("SELECT sku, barcode, warehouse, end_qty FROM inventory_month "
                       "WHERE period=%s AND channel=%s AND warehouse_type=%s",
                       [prev, channel, warehouse_type]) or []:
            prev_end[(str(r.get("sku") or ""), str(r.get("barcode") or ""),
                      str(r.get("warehouse") or ""))] = int(r.get("end_qty") or 0)
    # 首次建账(没有任何上期账期行): 期初 = 基线 + 基线之后、账期之前的净流水
    boot_net = _net_before(channel, warehouse_type, start) if not prev_end else {}
    rows = []
    for r in query("SELECT id, sku, COALESCE(barcode,'') AS bc, warehouse, "
                   "COALESCE(beginning_stock,0) AS beg FROM inventory "
                   "WHERE channel=%s AND warehouse_type=%s "
                   "AND (deleted_at IS NULL OR deleted_at='')",
                   [channel, warehouse_type]) or []:
        sku = str(r.get("sku") or "")
        bc = str(r.get("bc") or "")
        wh = str(r.get("warehouse") or "")
        k3 = (sku, bc, wh)
        beg = prev_end[k3] if k3 in prev_end else int(r.get("beg") or 0) + boot_net.get(k3, 0)
        mi = _pick(ins, sku, bc, wh, warehouse_type)
        mo = _pick(outs, sku, bc, wh, warehouse_type)
        rows.append({"id": r.get("id"), "sku": sku, "barcode": bc, "warehouse": wh,
                     "begin": beg, "in": mi, "out": mo, "end": beg + mi - mo})
    # 流水有、库存无(空码/仓名不一致)的行: 不静默吞掉, 单独统计给对账用
    orphan = _orphan_count(ins, outs, rows)
    return {"period": period, "start": start, "rows": rows, "orphan": orphan,
            "beg": sum(x["begin"] for x in rows), "in": sum(x["in"] for x in rows),
            "out": sum(x["out"] for x in rows), "end": sum(x["end"] for x in rows)}


def _orphan_count(ins, outs, rows):
    """流水侧存在、库存侧没有的 五维键数量(说明有流水挂在不存在的库存行上)"""
    have = {(x["sku"], x["barcode"], x["warehouse"]) for x in rows}
    keys = {k[:3] for k in list(ins.keys()) + list(outs.keys()) if k[0]}
    return len([k for k in keys if k not in have])


def _write_month(period, channel, warehouse_type, rows):
    """UPSERT 账期行(同账期重复结账 = 覆盖, 永不累加)"""
    if not rows:
        return 0
    args = [(period, channel, warehouse_type, x["sku"], x["barcode"], x["warehouse"],
             int(x["begin"]), int(x["in"]), int(x["out"]), int(x["end"]),
             period_bounds(period)[1]) for x in rows]
    executemany("INSERT INTO inventory_month(period, channel, warehouse_type, sku, barcode, "
                "warehouse, begin_qty, in_qty, out_qty, end_qty, src, closed, as_of) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'derived',1,%s) "
                "ON DUPLICATE KEY UPDATE begin_qty=VALUES(begin_qty), in_qty=VALUES(in_qty), "
                "out_qty=VALUES(out_qty), end_qty=VALUES(end_qty), src=VALUES(src), "
                "closed=VALUES(closed), as_of=VALUES(as_of), updated_at=NOW()", args)
    return len(args)


def _apply_to_inventory(rows):
    """结账结果回写 inventory: 期末成为下期期初(beginning_stock), 月入/月出同步

    **不写 available_qty** —— 可用永远由重算得出(用户定案"可用按计算来"),
    这里落期初, 重算用 `beginning_stock + Σ流水(>= begin_date)` 得到可用,
    避免出现"结账写的可用"和"重算的可用"两套数。
    """
    args = [(int(x["end"]), int(x["in"]), int(x["out"]), x["id"]) for x in rows]
    if not args:
        return 0
    executemany("UPDATE inventory SET beginning_stock=%s, month_inbound=%s, "
                "month_outbound=%s WHERE id=%s", args)
    return len(args)


def freeze_batches(period_next, channel, warehouse_type=OWN_WT):
    """把当前 batches 余额切片存进下期 batch_opening(带日期), 供下月批次池起算"""
    execute("DELETE FROM batch_opening WHERE period=%s AND channel=%s AND warehouse_type=%s",
            [period_next, channel, warehouse_type])
    execute("INSERT INTO batch_opening(period, channel, warehouse_type, sku, barcode, warehouse, "
            "prod_date, exp_date, qty) "
            "SELECT %s, channel, warehouse_type, sku, COALESCE(barcode,''), warehouse, "
            "COALESCE(prod_date,''), COALESCE(exp_date,''), qty FROM batches "
            "WHERE channel=%s AND warehouse_type=%s AND qty > 0",
            [period_next, channel, warehouse_type])
    row = one("SELECT COUNT(*) AS c FROM batch_opening WHERE period=%s AND channel=%s "
              "AND warehouse_type=%s", [period_next, channel, warehouse_type]) or {}
    return int(row.get("c") or 0)


def close_period(period, channel, warehouse_type=OWN_WT, force=False):
    """结一个账期: 体检 → 算 → 写账期 → 回写库存 → 切片批次 → 记账期状态

    force=True 跳过体检(补结历史用), 但 reason 里仍写明"强制"。
    返回 dict: ok / reason / period / rows / beg / in / out / end / batches
    """
    if not force:
        ok, why = close_check(period, channel, warehouse_type)
        if not ok:
            _set_state(channel, warehouse_type, period, "blocked", "", why)
            return {"ok": False, "period": period, "reason": why}
    # 链条完整性: 除首期(bootstrap)外, 只能结"当前生效账期的下一期", 或重结当期本身。
    #   跳过中间月份去结后面那期, 期初会静默取到更早的基线 —— 数错得毫无征兆,
    #   所以宁可拒绝, 并告诉用户先补结哪一期。
    #   (status 不参与判断: blocked 也记了 period, 否则"上月被拒后照样能结下月"就漏了)
    st = state_get(channel, warehouse_type)
    prev = shift_period(period, -1)
    _p0 = st.get("period") or ""
    if _p0 and _p0 != prev and _p0 != period:
        why = "上月(%s)尚未结账, 请先结上月再结 %s" % (prev, period)
        _set_state(channel, warehouse_type, period, "blocked", "", why)
        return {"ok": False, "period": period, "reason": why}
    data = build_rows(period, channel, warehouse_type)
    rows = data["rows"]
    n = _write_month(period, channel, warehouse_type, rows)
    _apply_to_inventory(rows)
    nb = freeze_batches(shift_period(period, 1), channel, warehouse_type)
    nxt = shift_period(period, 1)
    _set_state(channel, warehouse_type, period, STATUS_CLOSED,
               "%s-01" % nxt if nxt else "", "强制结账" if force else "", n)
    return {"ok": True, "period": period, "rows": n, "batches": nb,
            "beg": data["beg"], "in": data["in"], "out": data["out"],
            "end": data["end"], "orphan": data["orphan"],
            "begin_date": "%s-01" % nxt if nxt else ""}


def reclose(channel, warehouse_type=OWN_WT, from_period=None):
    """补导后重结: 从 from_period 起逐月重结到**上个月**(当月不结), 期初自动级联

    这是"自动结账失败后还有救"的唯一通道 —— 没有它, 补导完流水也永远结不回来。

    边界: from_period 必须是"当前生效账期的下一期"(即正常补结刚过去的那一期);
    要重结更早的历史月份, 期初基线已被后续结账覆盖, 需要整链重建 —— 本函数不做,
    照实报错让人知道, 而不是半截级联出一串错数。
    """
    cur = period_of()
    p = from_period or (shift_period(cur, -1) or "")
    out = []
    guard = 0
    while p and p < cur and guard < 24:
        r = close_period(p, channel, warehouse_type)
        out.append(r)
        if not r.get("ok"):
            break          # 体检/链条没过就停: 跳过继续结后面的, 期初就是错的
        p = shift_period(p, 1)
        guard += 1
    return out
