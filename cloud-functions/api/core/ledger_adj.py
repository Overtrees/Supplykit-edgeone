"""期初实盘校准: 窗口内的库存导入覆盖「上月结转」并留痕

2026-11-05 用户定案(两个场景识别执行):

  场景 A 默认 —— 窗口内没有导入实盘 → 本月期初 = 上月期末(结转), 即 core/ledger 的既有口径。
  场景 B 实盘 —— 上月末最后一天 ~ 当月 3 号之间导入过库存(带期初/可用),
             → **以导入为准**(实盘是最终量), 覆盖本期期初, 对账/结转/周转全部以它为基线,
               并写用户维度日志(quality_logs → 用户可见) + ledger_begin_adj 明细, 供审计。

为什么要有窗口:
  · 再晚就不叫"期初"了 —— 4 号以后导入的量掺了本月流水, 拿去当期初等于把流水算两遍;
  · 上月末最后一天也要收: 结账在次月 1 号 03:05 才跑(平台调度), 月末盘库很常见,
    它描述的就是"下月期初"。

换算(校准"导入时点结存", 不是硬塞期初):
     期初 = 导入量 − Σ净流水[本月 1 日, 导入时刻)
  上月末导入时 Σ 区间为空 → 期初 = 导入量, 天然正确。
  这样导入量始终被当作"导入那一刻仓库里有多少", 不要求用户把实数倒推成月初数。

批次轨:
  期初一变, 库存轨与批次轨(Σbatch_opening)就差了 delta。按差额补一条调整批次
  (盘盈 → 无日期/到期日 9999-12-31 排在 FEFO 末位; 盘亏 → 按 FEFO 逐批扣减),
  维持「Σ批次 ≡ 可用」, 否则指定批次出库会当场退化。

边界:
  · 只处理自有仓(own) —— 平台仓/B 仓是快照制, 不进账期;
  · 只对**当前进行中**的账期生效: 已结账的期再改期初, 期末就得重算 → 返回 skipped,
    让用户走 /ledger/reclose(重结)而不是偷偷改历史;
  · 窗口内多次导入以**最后一次**为准(UPSERT), 每次覆盖都留痕(apply 时间/批次)。
"""
from db import query, one, execute, executemany

from core.clock import now_cn
from core.ledger import period_of, shift_period, period_bounds, current_period, state_get

WINDOW_END_DAY = 3          # 当月 1~3 号
OWN_WT = "own"
ADJ_EXP_DATE = "9999-12-31"  # 盘盈调整批次的到期日: FEFO 排最后, 不吃掉真实批次


# ── 窗口与账期判定(纯函数, 便于单测) ──────────────────────────────────────

def target_period(ts=None):
    """导入时刻 → 该次实盘应校准的账期; 窗口外返回 ''

    · 上月末最后一天 → 次月(月末盘库 = 下月期初)
    · 当月 1~3 号   → 当月
    其余 → ''(窗口外, 不走校准, 只落 snapshot_qty 做对账)
    """
    ts = ts or now_cn()
    cur = period_of(ts)
    last_day = period_bounds(cur)[1]
    if ts.strftime("%Y-%m-%d") == last_day:
        return shift_period(cur, 1)
    if 1 <= ts.day <= WINDOW_END_DAY:
        return cur
    return ""


def window_text(period):
    """给人看的窗口说明(日志/接口回显用): 上月末最后一天 00:00 ~ 当月 3 日 23:59:59"""
    prev = shift_period(period, -1)
    return "%s 00:00:00 ~ %s-%02d 23:59:59" % (period_bounds(prev)[1], period, WINDOW_END_DAY)


# ── 换算 ────────────────────────────────────────────────────────────────

def _net_since(channel, wt, start, ts):
    """Σ净流水[期初日 00:00, 导入时刻) → {(sku, 69码, 仓): 入−出}

    流水日期列是 VARCHAR('YYYY-MM-DD HH:MM:SS'), 字典序与时间序一致。
    只标了日期的行('YYYY-MM-DD')按"当日 00:00"处理 —— 前缀更短, 排在同日带时刻的行之前。
    """
    end = ts.strftime("%Y-%m-%d %H:%M:%S")
    out = {}
    for table, col, sign in (("inbound_records", "inbound_date", 1),
                             ("outbound_records", "outbound_date", -1)):
        rows = query("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, SUM(quantity) AS q "
                     "FROM `%s` WHERE channel=%%s AND `%s` >= %%s AND `%s` < %%s "
                     "AND (COALESCE(warehouse_type,'')='' OR warehouse_type=%%s) "
                     "GROUP BY sku, bc, warehouse" % (table, col, col),
                     [channel, start, end, wt]) or []
        for r in rows:
            k = (str(r.get("sku") or ""), str(r.get("bc") or ""), str(r.get("warehouse") or ""))
            out[k] = out.get(k, 0) + sign * int(r.get("q") or 0)
    return out


def begin_from_import(imported_qty, offset_qty):
    """导入量 → 期初: 导入量是"导入时点结存", 减掉本月到那一刻为止的净流水

    offset_qty 可正可负(本月净出库为负 → 期初反而大于导入量), 一律直接相减;
    不夹 0 —— 实盘与流水打架时要暴露出来, 而不是悄悄归零(与"负库存要暴露"同一纪律)。
    """
    return int(imported_qty or 0) - int(offset_qty or 0)


def _carried(channel, wt, period):
    """上月已结账的期末(= 本文口径下"本该用的期初") → {(sku,69码,仓): qty}"""
    prev = shift_period(period, -1)
    out = {}
    if not prev:
        return out
    for r in query("SELECT sku, barcode, warehouse, end_qty FROM inventory_month "
                   "WHERE period=%s AND channel=%s AND warehouse_type=%s",
                   [prev, channel, wt]) or []:
        out[(str(r.get("sku") or ""), str(r.get("barcode") or ""),
             str(r.get("warehouse") or ""))] = int(r.get("end_qty") or 0)
    return out


# ── 场景识别(2026-11-05 补: 让 §15.152 的两个场景真正自动识别执行) ──────
#
# 文档 §15.152 定的是**两个场景**, 但原实现只有一条路径: `target_period()` 把窗口外的
# 导入一律 skip。这带来一个结构性问题 —— **窗口外发生的导入没有任何出口**:
#   · 真实客户月初导了一次库存报表(4 号以后), 盘库结果无处落地, 只能手改库;
#   · seed 在建账时跑在月中, 期初拿不到来源, 只能退成 0。
# 之前为了救 seed 加过一个 `baseline=True` 开关, 那是**在文档之外新造第三条路** ——
# 口径分叉的代价是后续每一次调账都会有人问"这两条路以谁为准"。现在撤掉它, 改成:
#
#   场景由「导入时刻 + 该渠道账期状态 + 流入的量」共同判定, 只有这四种:
#
#   | 场景 | 判定 | 本期期初 | 依据 |
#   |---|---|---|---|
#   | A 结转 | 账期已建、窗口内无导入 | 上月期末 | 0002 §47 |
#   | B 实盘 | 窗口内导入(上月末最后一天 ~ 当月 3 号) | 导入量 − Σ净流水[本月1日, 导入时刻) | §15.152 |
#   | C 首建 | 该渠道从未建过账 | 导入值(本期即首期, 无上月可结转) | 0002 §47「首期才用导入值当基线」 |
#   | D 窗口外 | 已建账 + 窗口外导入 | **不改期初**, 只存 snapshot_qty 做对账 | §15.152「4 号以后掺了流水, 当期初双算」|
#
# C 是本轮补上的: 原来"首期"靠 `_ledger_started()` 在写入侧分支, 与 `apply_import`
#   各自判断, 两边口径能分叉(实测 other 渠道走 C 拿到 211 万, jd 走 A 拿到 0)。
#   现在统一到这里判定, 写入侧只看判定结果, 不再自己猜。
#
# D 是**正常且正确**的行为, 不是缺口: 月中导入的可用量本来就含了本月流水, 拿来当期初
#   必然双算。用户的期望("我盘完要能改期初")由 B 满足; 月中普通导入不属于盘库。
SCENE_CARRY = "A_carry"      # 窗口内无导入 → 本月期初 = 上月期末
SCENE_STOCKTAKE = "B_stocktake"  # 窗口内实盘 → 导入量反推期初
SCENE_FIRST = "C_first"      # 从未建账 → 首期, 导入值即基线
SCENE_OUTSIDE = "D_outside"  # 已建账 + 窗口外 → 只对账不改期初


def detect_scene(channel, ts=None, wt=OWN_WT, has_import=True):
    """判定本次库存导入该走哪个场景; 返回 (scene, period, 说明)

    · 只有 own 进账期(0002 §17: 平台仓/B 仓是快照制);
    · period 是「这次导入要落的账期」, 窗口外时为当期(仅用于展示/留痕, 不改期初);
    · has_import=False 表示"查询当期应结转多少"(场景 A 的读侧), 不涉及导入值。
    """
    ts = ts or now_cn()
    if wt != OWN_WT:
        return "", "", "仅自有仓走账期"
    started = bool(state_get(channel, wt).get("begin_date"))
    if not started:
        return SCENE_FIRST, period_of(ts), "该渠道从未建账 → 首期, 导入值即基线"
    if has_import:
        p = target_period(ts)
        if p:
            return SCENE_STOCKTAKE, p, "窗口内实盘导入(%s)" % window_text(p)
        return SCENE_OUTSIDE, period_of(ts), "窗口外导入(仅 %d 号前算实盘)" % WINDOW_END_DAY
    return SCENE_CARRY, current_period(channel, wt), "上月期末结转"


# ── 应用 ────────────────────────────────────────────────────────────────

def apply_import(channel, items, ts=None, batch_id="", wt=OWN_WT):
    """把一次库存导入按场景落成期初; 返回 {ok, applied, skipped, delta, period, scene}

    items: 清洗后的库存行(含 sku / barcode / warehouse; 数量取 snapshot_qty →
           beginning_stock → available_qty 第一个非空)
    调用点: routes/cleanse_write.py 的 inventory 写入成功后(失败只留痕, 绝不阻断导入)。

    场景(见 detect_scene):
      B 实盘 / C 首建 → 写期初(换算见 §15.152: 期初 = 导入量 − Σ净流水)
      D 窗口外       → 返回 skipped, 调用方已把导入值写进 snapshot_qty 做对账
      A 结转         → 不经过本函数(没有导入就没有校准)

    为什么 seed 不需要单独开关: seed 在空库首跑时 `ledger_state` 为空 → 自动落 C;
      若上次残留了 `status=closed`(上一轮 seed 建过账), `reset` 会清 `ledger_state`,
      所以每轮 seed 都从 C 开始, 与"真实客户首次导入"走同一条路。
    """
    ts = ts or now_cn()
    res = {"ok": True, "applied": 0, "skipped": "", "period": "", "delta": 0,
           "carried": 0, "begin": 0, "window": "", "scene": ""}
    if wt != OWN_WT:
        res["skipped"] = "仅自有仓走账期"
        return res
    scene, period, why = detect_scene(channel, ts, wt, has_import=True)
    res["scene"] = scene
    res["period"] = period
    res["window"] = why
    if scene == SCENE_OUTSIDE:
        # 已建账 + 窗口外: 期中导入的可用量含本月流水, 当期初必双算(§15.152)。
        # 不改期初 —— 导入值已在 snapshot_qty 里, 由对账/巡检暴露差异。
        res["skipped"] = "窗口外(仅上月末最后一天 ~ 当月 %d 号), 只对账不改期初" % WINDOW_END_DAY
        return res
    if scene == SCENE_FIRST:
        # 首期: 导入值即基线, 没有上月可结转 → carried 全 0, 不做"减去本月净流水"的换算。
        # 这不是特例, 是 §15.152 换算式在"本期第一天之前没有流水"时的自然结果。
        pass
    else:
        # B 实盘: 只对**当前进行中的账期**生效 —— 已结的期改期初, 期末就与月报不符
        if period != current_period(channel, wt):
            res["skipped"] = "账期 %s 不是当前进行中的账期(已结/未到), 如需修正请重结" % period
            return res

    # Σ 起点必须与**可用重算/批次重建**同源(core.ledger.begin_date), 否则两条轨算的不是
    #   同一个量 —— 线上"available 与 beginning_stock 差 1~651 不等、10,100 行只对得上 323 行"
    #   就是这么来的(排查两轮才定位, 之前一直误以为是换算式本身写错了)。
    #
    #   语义对齐(不是取巧): §15.152 换算式 `期初 = 导入量 − Σ净流水[本月1日, 导入时刻)` 里的
    #   "本月 1 日", 本质是**本期起算日**。已建账时它就是 begin_date; 从未建账时(bootstrap)
    #   系统把"本期"定义为全期, 起算日就是"有流水之前"。两处取同一个值, 期初与可用才自洽。
    #   写死本月 1 号正是分裂的来源, 不能再写回去。
    _bd = state_get(channel, wt).get("begin_date") or ""
    _sum_start = _bd if _bd else "1970-01-01"
    res["sum_start"] = _sum_start
    # 首期(fresh 账)没有上月可结转: carried 全 0, 免得把上轮残留的 inventory_month 当结转。
    carried = {} if scene == SCENE_FIRST else _carried(channel, wt, period)
    off = _net_since(channel, wt, _sum_start, ts)
    rows, args = [], []
    for it in items or []:
        sku = str(it.get("sku") or "").strip()
        if not sku:
            continue
        q = it.get("snapshot_qty")
        if q in (None, ""):
            q = it.get("beginning_stock")
        if q in (None, ""):
            q = it.get("available_qty")
        try:
            imp = int(q or 0)
        except (TypeError, ValueError):
            continue
        k3 = (sku, str(it.get("barcode") or ""), str(it.get("warehouse") or ""))
        o = int(off.get(k3, 0))
        beg = begin_from_import(imp, o)
        old = int(carried.get(k3, 0))
        rows.append({"sku": sku, "barcode": k3[1], "warehouse": k3[2],
                     "qty": beg, "imported": imp, "offset": o, "carried": old,
                     "delta": beg - old})
        args.append((period, channel, wt, sku, k3[1], k3[2], beg, imp, o, old,
                     beg - old, "import", str(batch_id or "")[:64]))
    if not rows:
        res["skipped"] = "本次导入没有可用的期初值"
        return res

    executemany("INSERT INTO ledger_begin_adj(period, channel, warehouse_type, sku, barcode, "
                "warehouse, qty, imported_qty, offset_qty, carried_qty, delta, src, batch_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE qty=VALUES(qty), imported_qty=VALUES(imported_qty), "
                "offset_qty=VALUES(offset_qty), carried_qty=VALUES(carried_qty), "
                "delta=VALUES(delta), src=VALUES(src), batch_id=VALUES(batch_id), "
                "applied_at=NOW()", args)

    _apply_to_inventory(rows, channel, wt)
    _adjust_batches(period, channel, wt, rows)
    _refresh(channel, [r["sku"] for r in rows], begin_date=_sum_start)

    res["applied"] = len(rows)
    res["delta"] = sum(r["delta"] for r in rows)
    res["carried"] = sum(r["carried"] for r in rows)
    res["begin"] = sum(r["qty"] for r in rows)
    _log(channel, period, rows, batch_id)
    return res


def _apply_to_inventory(rows, channel, wt):
    """把校准后的期初落回 inventory.beginning_stock

    可用 = beginning_stock + Σ流水(>= begin_date), 所以改期初必须改这一列 ——
    否则页面/补货/健康度仍按旧期初算, 只有下次结账才"突然对上一次"(最难查的一类)。
    """
    args = [(int(r["qty"]), channel, wt, r["sku"], r["barcode"], r["warehouse"]) for r in rows]
    executemany("UPDATE inventory SET beginning_stock=%s WHERE channel=%s AND warehouse_type=%s "
                "AND sku=%s AND barcode=%s AND warehouse=%s", args)


def _adjust_batches(period, channel, wt, rows):
    """按差额调整本期 batch_opening: 盘盈补一条末位批次, 盘亏按 FEFO 逐批扣

    批次键 = (sku, 69码, 仓, 生产日, 到期日)。盘盈用到期日 9999-12-31 排在 FEFO 末位,
    不会被当成"最早到期"优先消耗掉; 盘亏从最早到期的批次开始扣(与出库扣减同序)。
    """
    for r in rows:
        d = int(r["delta"])
        if not d:
            continue
        k = (period, channel, wt, r["sku"], r["barcode"], r["warehouse"])
        if d > 0:
            execute("INSERT INTO batch_opening(period, channel, warehouse_type, sku, barcode, "
                    "warehouse, prod_date, exp_date, qty) VALUES(%s,%s,%s,%s,%s,%s,'',%s,%s) "
                    "ON DUPLICATE KEY UPDATE qty=qty+VALUES(qty)",
                    list(k) + [ADJ_EXP_DATE, d])
            continue
        need = -d
        avail = query("SELECT prod_date, exp_date, qty FROM batch_opening "
                      "WHERE period=%s AND channel=%s AND warehouse_type=%s AND sku=%s "
                      "AND barcode=%s AND warehouse=%s ORDER BY exp_date ASC",
                      list(k)) or []
        for b in avail:
            if need <= 0:
                break
            have = int(b.get("qty") or 0)
            if have <= 0:
                continue
            take = min(have, need)
            execute("UPDATE batch_opening SET qty=qty-%s WHERE period=%s AND channel=%s "
                    "AND warehouse_type=%s AND sku=%s AND barcode=%s AND warehouse=%s "
                    "AND prod_date=%s AND exp_date=%s",
                    [take] + list(k) + [str(b.get("prod_date") or ""), str(b.get("exp_date") or "")])
            need -= take
        if need > 0:      # 批次不够扣(期初被调小到低于批次余额): 留负数暴露, 不静默抹平
            execute("INSERT INTO batch_opening(period, channel, warehouse_type, sku, barcode, "
                    "warehouse, prod_date, exp_date, qty) VALUES(%s,%s,%s,%s,%s,%s,'',%s,%s) "
                    "ON DUPLICATE KEY UPDATE qty=qty+VALUES(qty)",
                    list(k) + [ADJ_EXP_DATE, -need])


def _refresh(channel, skus, begin_date=""):
    """校准后立刻重算库存/批次 —— 页面与补货读到的是新基线, 不等下一次导入

    放在这里而不是让调用方补: 调用方只做"写入 → 重算", 期初校准夹在两者之间,
    谁忘了调重算, 期初就只改了列、可用还是旧值(静默错账)。

    begin_date: 本次换算用的 Σ 起点, **显式传下去**, 让重算/重建与期初用同一个区间。
      不传的话它们会各自再查一次 `ledger_state`, 两次读之间状态若被结账/cron 改动,
      就出现"期初按 A 区间算、可用按 B 区间算"—— 这正是本轮那个差 1~651 的来源。
      (两次查询之间隔几十秒, 期初校准又是长事务, 这个窗口是真实存在的。)
    """
    skus = sorted({s for s in skus if s})
    if not skus:
        return
    try:
        from routes.cleanse_write import _recalc_own_inventory, _rebuild_own_batches
        _recalc_own_inventory(channel, skus, begin_date=begin_date)
        _rebuild_own_batches(channel, skus, begin_date=begin_date)
    except Exception as e:
        from routes.common import try_err
        try_err("ledger_adj", "期初校准后重算/重建失败", e)


def _log(channel, period, rows, batch_id):
    """用户维度留痕: 多少人/哪些行/原结转 vs 校准后/差额 —— 审计要能只看这一条

    log_type 必须登记在 routes/misc._LOG_SCOPE_TYPES['user'], 否则日志页按类型过滤时
    "写了等于没写"(CONTEXT 铁律)。
    """
    try:
        d = sum(int(r["delta"]) for r in rows)
        msg = ("期初按实盘校准: 账期 %s · 渠道 %s · %d 行 · 原结转 %d → 校准后 %d (差 %+d)%s"
               % (period, channel, len(rows), sum(int(r["carried"]) for r in rows),
                  sum(int(r["qty"]) for r in rows), d,
                  (" · 批次 %s" % batch_id) if batch_id else ""))
        execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('ledger_override','info',%s,%s,'ledger_adj')",
                (msg[:200], ("窗口 %s; 差额为 0 的行也留有明细(实盘与结转一致也是事实)"
                             % window_text(period))[:300]))
    except Exception as e:
        from routes.common import try_err
        try_err("ledger_adj", "期初校准留痕失败", e)


# ── 读侧: 供结账/状态接口用 ─────────────────────────────────────────────

def begin_map(period, channel, wt=OWN_WT):
    """该账期被校准过的行 → {(sku,69码,仓): qty}; 结账 build_rows 的期初优先级 ⓪"""
    try:
        rows = query("SELECT sku, barcode, warehouse, qty FROM ledger_begin_adj "
                     "WHERE period=%s AND channel=%s AND warehouse_type=%s",
                     [period, channel, wt]) or []
    except Exception:
        return {}
    return {(str(r.get("sku") or ""), str(r.get("barcode") or ""),
             str(r.get("warehouse") or "")): int(r.get("qty") or 0) for r in rows}


def summary(period, channel, wt=OWN_WT):
    """账期页展示用: 本期期初来源 + 校准行数/差额"""
    try:
        r = one("SELECT COUNT(*) AS c, COALESCE(SUM(delta),0) AS d FROM ledger_begin_adj "
                "WHERE period=%s AND channel=%s AND warehouse_type=%s",
                [period, channel, wt]) or {}
    except Exception:
        r = {}
    c = int(r.get("c") or 0)
    return {"begin_source": "import" if c else "carryover",
            "adj_rows": c, "adj_delta": int(r.get("d") or 0),
            "window": window_text(period) if period else ""}
