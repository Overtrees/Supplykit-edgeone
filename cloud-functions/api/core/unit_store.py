"""core/unit_store.py —— 判定结果物化读模型（2026-11-07 P1）

## 一句话
把「每个单元判成什么」算一次、存下来；请求路径只剩 `GROUP BY` 与 `LIMIT`。

## 为什么需要（线上实测）

S3 把「判定」搬进请求路径后，summary 每请求要搬 15 万行进 Python，只为算 10 个整数。
`perf_debug` 逐层埋点：`conn=0.06s  stmt=6 条/5.22s  max=2.07s`，其中
`inventory` 90,100 行 2.08~2.76s、日销切片 63,186 行 1.87~2.53s，组装 1.2s，服务端 3.8s。
物化之后，卡片 = 读 `unit_summary` 8 行（≈4 RU），列表 = 读 `unit_alert` 的异常行。

## 这不是「把判定下推 SQL」（§15.151 拒绝过后者，理由我同意）

| | 下推 SQL（被拒） | 本模块 |
|---|---|---|
| 判定公式在哪 | SQL 条件表达式 + Python `classify()` | **只在 `metrics.judge()` 一处** |
| 真源数 | 2 份（改一处必漏另一处） | **1 份** |
| SQL 里有什么 | 分档逻辑、安全线、在途口径 | 只有 `GROUP BY` / `WHERE state=...` |

本模块存的不是算法，是**算完的答案**。任何分档判断都调 `metrics.judge()`；
`local_test` 有静态守门：本文件出现 `classify(` 字面量即红。

## 四维保障

| 维 | 机制 |
|---|---|
| 一致性 | `fp`（数据域计数器 md5 + 粒度 + 模式）驱动，**不是 TTL**；不等即重建 |
| 时效 | 写入 bump 域计数器 → 下一次请求即重建；重建完成前走原路径（慢但**正确**） |
| 完整性 | 发布前校验（总数 / 状态分布 / 独立 `tally` 复核 / 异常行数），不通过**不切换** |
| 可回滚 | `UNIT_STATE_READ=0` 即回旧路径；`head` 是唯一切换点，失败不影响旧版本 |

**读路径永不返回"可能过期的数"**：`fp` 不等就返回 `None`，调用方退回现算（今天的行为）。
宁可慢，不可错 —— 这比"TTL 内把旧值当新值"强，也是 §15.120 否掉"拖 TTL"的同一条理由。

## 发布语义

新 `gen` 的 summary / alert 行**全部写完**之后，才 `UPDATE head.gen` 一步切换。
中途失败 → head 仍指旧 gen → 读侧不受影响（旧版本继续正确，只是不是最新）。
保留 N 与 N-1 两个 gen：列表带 `?fp=` 钉版本时还能命中上一版。
"""
import hashlib
import os
import threading
import time

from db import query, one, execute
from core import metrics as M
from routes.common import try_err, INVENTORY_COLS

# 影响判定结果的数据域（与 analysis_cache._FAMILY_DEPS 同名）
#   orders 在列: 日销是 orders 的二阶物化(sku_sales_stats), "订单进了但日销还没重建"的窗口里
#   判定依据会变 —— 宁多重算, 不拿旧日销判。
#   schema 在列(2026-11-07): 表结构变化会让"算完的答案"缺列/语义变(如新增 code_out),
#   版本写成功时 bump 该域 → fp 变 → 自动重建, 不必依赖人工 force。
JUDGE_DOMAINS = ("orders", "inventory", "products", "rules", "config", "schema")

_READ_ON = ("1", "true", "yes", "on")
_COOLDOWN = 30.0        # 同一 (channel, mode) 两次重建的最小间隔(秒) —— 防一次请求风暴重建N次
_STALE_BACKOFF = 600.0  # 校验失败后的退避(秒): 别让每个读请求都再烧一次 19s 重建
_CHUNK = 500            # 明细批量插入行数
_LOCK = threading.Lock()
_LAST = {}              # (channel, mode) -> (ts, fp)


def read_enabled():
    """读路径**总开关**（环境变量）。语义:
      · 未设置 → 允许尝试（真正是否命中由 `unit_state_head.enabled` 决定）
      · "0"    → 一刀关闭, 连 head 都不读 —— 紧急回滚用, 优先于库里的 enabled 列
    正常切换用库里的 enabled 列(UPDATE 一行即生效, 不必重新部署)。
    """
    _v = os.environ.get("UNIT_STATE_READ")
    if _v is None or str(_v).strip() == "":
        return True
    return str(_v).strip().lower() in _READ_ON


# ── 指纹 ────────────────────────────────────────────────────────
def fp_now(channel, mode=None):
    """当前数据版本指纹。空串 = 不可用（表没建 / 查询失败）→ 调用方一律走旧路径

    取的是 `data_version_dom` 的域计数器, 不是时间: 时间驱动会误判(§15.66 踩过
    "now-3天 判陈旧 → 数据停更后每次直算"), 而计数器只随**写入**变。
    粒度(grain)与模式(mode)会改变判定对象, 必须进指纹 —— 只改配置不 bump 计数时也能感知。
    """
    _m = M.mode_of(channel, mode)
    try:
        rows = query("SELECT dom, v FROM data_version_dom WHERE dom IN ("
                     + ",".join(["%s"] * len(JUDGE_DOMAINS)) + ")", list(JUDGE_DOMAINS))
    except Exception:
        return ""
    vals = {str(r.get("dom")): int(r.get("v") or 0) for r in rows}
    try:
        _g = M.grain_of(channel, _m)
    except Exception:
        _g = ""
    raw = "|".join("%s=%d" % (d, vals.get(d, 0)) for d in JUDGE_DOMAINS)
    raw += "|g=%s|m=%s" % (_g, _m)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:32]


# ── 读 ──────────────────────────────────────────────────────────
def head(channel, mode=None):
    _m = M.mode_of(channel, mode)
    try:
        # SELECT * 而不是点名列: 本表是**纯读的控制面**, 只有十来列;
        #   点名列会在"新列已进 DDL、存量实例还没补列"的窗口里整条查询报错 →
        #   tally_of/risk_of 全返回 None → 所有读路径退回现算(比不加列还慢)。
        r = one("SELECT * FROM unit_state_head WHERE channel=%s AND mode=%s", [channel, _m])
    except Exception as _e:
        try_err("unit_store", "head 读取降级", _e)
        return None
    return r or None


def tally_of(channel, mode=None, gen=None):
    """读回 `metrics.tally()` 的结构；`None` = 不可用。

    None 的三种含义（调用方都退回现算, 语义一致）：
      ① 从未构建过；② fp 已变（源数据变了）；③ 上次重建校验未通过（stale=1）。
    """
    _m = M.mode_of(channel, mode)
    if not read_enabled():
        return None
    h = head(channel, _m)
    if not h or not int(h.get("gen") or 0) or int(h.get("stale") or 0):
        return None
    if not int(h.get("enabled") or 0):
        return None          # 双写观察期: 有数据但不读
    _fp = fp_now(channel, _m)
    if not _fp or str(h.get("fp") or "") != _fp:
        return None
    try:
        rows = query("SELECT kind, wtype, total, ok, low, `out`, idle, paused, code_out "
                     "FROM unit_summary WHERE channel=%s AND mode=%s AND gen=%s",
                     [channel, _m, int(gen or h.get("gen"))])
    except Exception as _e:
        try_err("unit_store", "summary 读取降级", _e)
        return None
    if not rows:
        return None
    return {(str(r.get("kind")), str(r.get("wtype") or "")): {
        "total": int(r.get("total") or 0), "ok": int(r.get("ok") or 0),
        "low": int(r.get("low") or 0), "out": int(r.get("out") or 0),
        "idle": int(r.get("idle") or 0), "paused": int(r.get("paused") or 0),
        "code_out": int(r.get("code_out") or 0)} for r in rows}


def low_stock_counts(channel, mode=None):
    """低库存卡片的三个数 —— 与 `dashboard` 原实现**同口径**, 只是从物化读

    原实现: `counts(judge_units(units(merge_bc=(mode=="bbcc"))), (DIM_BC, DIM_WH))`
      · bbcc       → units 已把 platform/platform_b 并进 bc, 逐仓行里这两类被剔除
      · traditional → 不建 bc, 逐仓行含全部仓型
    物化表存的是 `with_bc=True, merge_bc=False` 的四种桶, 所以这里用 `roll` 还原同一集合。
    """
    t = tally_of(channel, mode)
    if not t:
        return None
    _m = M.mode_of(channel, mode)
    if _m == "bbcc":
        c = M.roll(t, kinds=(M.DIM_BC, M.DIM_WH), wt_not_in=("platform", "platform_b"))
    else:
        c = M.roll(t, kinds=(M.DIM_WH,))
    return {"c": int(c.get("low") or 0), "out": int(c.get("out") or 0),
            "total": int(c.get("total") or 0)}


def stock_overview(channel, mode=None, bc_limit=100):
    """`/dashboard/aux` 的 stockOverview 三个数 —— 与 `_block_stock_low` **同口径**，从物化读

    原来 aux 每次冷算都要 `SELECT 7 列 × 9 万行` + 日销切片 + `units` + `judge_units`,
    只为得到 out / low / total / ok / idle / code_out 六个整数 —— 与 summary 在 S3 之后
    踩的是同一个坑：**同一批判定，summary 已读物化, aux 还在现算一遍**。
    线上实测 aux 冷算 4.1~5.5s（slow_request 自报），切到本函数后只剩两次小查询。

    返回 `None` = 不可用（从未构建 / fp 已变 / 校验未过）→ 调用方退回现算。
    """
    t = tally_of(channel, mode)
    if not t:
        return None
    _m = M.mode_of(channel, mode)
    if _m == "bbcc":
        c = M.roll(t, kinds=(M.DIM_BC, M.DIM_WH), wt_not_in=("platform", "platform_b"))
    else:
        c = M.roll(t, kinds=(M.DIM_WH,))
    # BC 合计缺货清单(卡片只展示够用的前 N 条) —— 明细与计数同一个 gen, 不会"卡片 N 条、点开 N±1"
    bc_out = []
    try:
        h = head(channel, _m)
        _g = int((h or {}).get("gen") or 0)
        if _g:
            bc_out = query(
                "SELECT a.sku, a.warehouse, COALESCE(p.product_name, a.sku) AS product_name "
                "FROM unit_alert a LEFT JOIN products p "
                "  ON p.sku=a.sku AND p.channel=a.channel "
                "WHERE a.channel=%s AND a.mode=%s AND a.gen=%s AND a.state=%s AND a.kind=%s "
                "ORDER BY a.sku LIMIT %d" % (int(bc_limit),),
                [channel, _m, _g, M.ST_OUT, M.DIM_BC]) or []
    except Exception as _e:
        try_err("unit_store", "bc_out 明细读取降级", _e)
    return {"out": int(c.get("out") or 0), "low": int(c.get("low") or 0),
            "total": int(c.get("total") or 0), "ok": int(c.get("ok") or 0),
            "idle": int(c.get("idle") or 0), "code_out": int(c.get("code_out") or 0),
            "bc_out": [{"sku": r.get("sku"), "product_name": r.get("product_name"),
                        "warehouse_type": "bc"} for r in bc_out]}


_RISK_DIMS = (("bc", "bcItems", "BC"), ("c", "cItems", "C"), ("own", "ownItems", "OWN"))


def risk_of(channel, mode=None, full=0):
    """濒临断货三级（stock-risk）读物化 —— 返回与 `_stock_risk` **逐字段同构**的 payload

    `None` = 不可用（从未构建 / fp 已变 / stale / 开关未开 / 查询失败）→ 调用方退回现算。
    ⚠️「真的没有断货」必须返回**合法空 payload**（全 0），不能返回 None ——
       否则每次请求都会退回那个 4.4s 的现算，物化等于白做。
    """
    _m = M.mode_of(channel, mode)
    if not read_enabled():
        return None
    h = head(channel, _m)
    if not h or not int(h.get("gen") or 0) or int(h.get("stale") or 0):
        return None
    if not int(h.get("risk_enabled") or 0):
        return None
    # risk_rows < 0 = 本轮没算成(异常/未计算) → 必须退回现算。
    #   这一条是"完整性"的兜底: 没有它, 计算失败会让卡片显示 0 条断货 —— 用户会以为
    #   "今天没有断货风险", 比慢 4 秒严重得多。0 是合法值(真的没有断货)。
    if int(h.get("risk_rows", -1)) < 0:
        return None
    _fp = fp_now(channel, _m)
    if not _fp or str(h.get("fp") or "") != _fp:
        return None
    _g = int(h.get("gen"))
    try:
        rows = query(
            "SELECT dim, sku, barcode, warehouse, product_name, avail, ds, adj_dos, level, accel "
            "FROM risk_snapshot WHERE channel=%s AND mode=%s AND gen=%s",
            [channel, _m, _g]) or []
    except Exception as _e:
        try_err("unit_store", "risk 读取降级", _e)
        return None
    # 排序交给 `risk_sort_key`（现算/物化唯一真源），不交给 SQL：
    #   MySQL collation 与 Python 码位序不一致，同一批 SKU 格式不统一就会错位。
    try:
        from routes.dashboard_stock_risk import risk_sort_key as _rsk
    except Exception:
        _rsk = None
    buckets = {"bc": [], "c": [], "own": []}
    for r in rows:
        _d = str(r.get("dim") or "")
        if _d not in buckets:
            continue
        _w = str(r.get("warehouse") or "")
        buckets[_d].append({
            "sku": str(r.get("sku") or ""), "barcode": str(r.get("barcode") or ""),
            "product_name": str(r.get("product_name") or r.get("sku") or ""),
            "warehouse": _w, "type": {"bc": "BC", "c": "C", "own": "OWN"}[_d],
            "available_qty": int(r.get("avail") or 0),
            "daily_sales": round(float(r.get("ds") or 0), 1),
            "days_to_empty": round(float(r.get("adj_dos") or 0), 1),
            "level": str(r.get("level") or ""),
            "accel": True if int(r.get("accel") or 0) else None,
        })
    out = {"items": [], "total": 0, "critical": 0, "warning": 0}
    for _d, _k, _t in _RISK_DIMS:
        _it = buckets[_d]
        if _rsk is not None:
            _it.sort(key=_rsk)
        out[_k] = _it if full else _it[:10]
        out[_d + "Total"] = len(_it)
        out[_d + "Critical"] = sum(1 for x in _it if x["level"] == "red")
        out[_d + "Warning"] = sum(1 for x in _it if x["level"] == "orange")
    return out


def alerts_of(channel, mode=None, state=None, limit=200, offset=0, gen=None, fp=None):
    """异常单元明细（列表用）。`fp` 传入时钉住版本 —— 卡片与列表必须同一版本

    返回 `None` = 不可用（调用方退回现算）。
    """
    _m = M.mode_of(channel, mode)
    h = head(channel, _m)
    if not h or not int(h.get("gen") or 0):
        return None
    _g = int(gen or h.get("gen"))
    if fp and str(fp) != str(h.get("fp") or ""):
        # 钉住的版本已被新一轮重建取代: 仍读旧 gen(保留 N-1), 读不到再说
        _g = int(gen or 0) or _g
    _sql = ("SELECT kind, sku, barcode, warehouse, state, avail, transit, ds, line, cover, gap, adj_dos "
            "FROM unit_alert WHERE channel=%s AND mode=%s AND gen=%s")
    _p = [channel, _m, _g]
    if state:
        _sql += " AND state=%s"
        _p.append(state)
    _sql += " ORDER BY state, sku, barcode, warehouse LIMIT %d OFFSET %d" % (int(limit), int(offset))
    try:
        return query(_sql, _p)
    except Exception as _e:
        try_err("unit_store", "alert 明细读取降级", _e)
        return None


# ── 构建 ────────────────────────────────────────────────────────
def _inv_rows(channel):
    return query("SELECT " + INVENTORY_COLS + " FROM inventory "
                 "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!=''", [channel])


def scan(channel, mode=None, inv_rows=None, cfg=None, series=None):
    """跑一遍判定 → (tally, alerts)。**只调 metrics**, 不含任何新口径。"""
    _m = M.mode_of(channel, mode)
    _cfg = M.channel_cfg(channel) if cfg is None else (cfg or {})
    rows = _inv_rows(channel) if inv_rows is None else inv_rows
    us = M.units(rows, merge_bc=False, with_bc=True,
                 grain=M.grain_of(channel, _m, _cfg),
                 drop_platform_b=(_m != "bbcc"))
    if series is None:
        series = M.load(channel, dims=M.dims_needed(rows, channel, _m), fields=("ds28",))
    p = M.periods(_cfg)
    inc = M.include_transit_default()
    summ, alerts = {}, []
    for u in us:
        dim = M.dim_of(u.get("kind"), u.get("warehouse"), u.get("warehouse_type"))
        ds = series.value("ds28", u["sku"], u.get("barcode"), dim) if series is not None else 0.0
        v = M.judge(u, ds, p, channel, _m, include_transit=inc)
        _k = (str(u.get("kind")), str(u.get("warehouse_type") or ""))
        c = summ.get(_k)
        if c is None:
            c = summ[_k] = {"total": 0, "ok": 0, "low": 0, "out": 0, "idle": 0, "paused": 0,
                            "code_out": 0}
        c["total"] += 1
        c[v["state"]] = c.get(v["state"], 0) + 1
        # 码级复核(2026-11-07 P0): 与 dashboard._block_stock_low 的 `_code_out` 逐字同口径 ——
        #   只在"状态 ok/low"(合计看起来能卖)且多码单元里有码 <=0 时计 1。
        #   out 单元整盘都断, 码级不额外计数(否则同一件事数两遍)。
        if v["state"] in (M.ST_OK, M.ST_LOW) and M.code_risk(u)["code_out"]:
            c["code_out"] += 1
        if v["state"] in (M.ST_LOW, M.ST_OUT):
            alerts.append({"kind": _k[0], "wtype": _k[1],
                           "sku": str(u.get("sku") or ""), "barcode": str(u.get("barcode") or ""),
                           "warehouse": str(u.get("warehouse") or ""), "state": v["state"],
                           "avail": int(u.get("avail") or 0), "transit": int(u.get("transit") or 0),
                           "ds": float(v.get("ds") or 0), "line": float(v.get("line") or 0),
                           "cover": int(v.get("cover") or 0), "gap": float(v.get("gap") or 0),
                           "adj_dos": float(v.get("adj_dos") or 0)})
    return {"units": us, "tally": summ, "alerts": alerts, "series": series, "p": p, "inc": inc}


def _validate(channel, mode, sc):
    """发布前自校验 —— **不通过就不切换**, 而不是发布后再对账。

    对账在事后发生时, 用户已经看过那个错的数了; 而且"卡片 3823 / 点开 3822"这种
    一致地错最难发现(两边都少同一个单元, 对不上才怪)。
    """
    errs = []
    summ, alerts, us = sc["tally"], sc["alerts"], sc["units"]
    _n = sum(c["total"] for c in summ.values())
    if _n != len(us):
        errs.append("单元总数不符 %d != %d" % (_n, len(us)))
    for _k, c in summ.items():
        if c["ok"] + c["low"] + c["out"] + c["idle"] + c["paused"] != c["total"]:
            errs.append("状态分布不自洽 %s" % (_k,))
    # 独立路径复核: 同一批 units 再走一遍 metrics.tally()
    #   本模块的循环与 tally 是两段代码, 对上才说明"记账没错", 而不是同一个函数自己证明自己。
    try:
        ref = M.tally(us, sc["series"], channel, mode, metric="ds28",
                      include_transit=sc["inc"], p=sc["p"])
        for _k, rc in ref.items():
            # code_out 是本模块在 tally 之外多记的"码级复核"计数, 不属于分档口径,
            #   复核时只比六个分档键(其余逐项相等才算过)。
            _mine = {kk: vv for kk, vv in (summ.get(_k) or {}).items() if kk != "code_out"}
            if rc != _mine:
                errs.append("tally 复核不一致 %s: %s != %s" % (_k, rc, _mine))
        if set(ref) != set(summ):
            errs.append("桶集合不一致 %s" % (set(ref) ^ set(summ),))
    except Exception as _e:
        errs.append("tally 复核异常 %s" % _e)
    # 异常行数: 明细表里每个桶的行数必须等于 low+out
    _cnt = {}
    for a in alerts:
        _k = (a["kind"], a["wtype"])
        _cnt[_k] = _cnt.get(_k, 0) + 1
    for _k, c in summ.items():
        if _cnt.get(_k, 0) != c["low"] + c["out"]:
            errs.append("异常行数不符 %s: %d != %d" % (_k, _cnt.get(_k, 0), c["low"] + c["out"]))
    return errs


def _mark_stale(channel, mode, note):
    """重建失败留痕 —— 只置 stale, **不改 fp**: 改了 fp 会让旧 gen 被当成新版本读出去。

    `built_at` 记的是"上次尝试时间", 与 `build()` 的 10 分钟退避配合使用:
    否则校验一直不过时, 每个读请求都会再起一次 19s 的重建线程(烧 RU 且无收益)。
    """
    try:
        execute("INSERT INTO unit_state_head(channel,mode,gen,fp,units,alerts,stale,note,built_at) "
                "VALUES(%s,%s,0,'',0,0,1,%s,NOW(6)) "
                "ON DUPLICATE KEY UPDATE stale=1, note=VALUES(note), built_at=VALUES(built_at)",
                [channel, mode, str(note or "")[:240]])
    except Exception as _e:
        try_err("unit_store", "重建失败留痕降级", _e)


def _stale_recent(channel, mode):
    """上次重建失败是否还在退避窗口内（比较由 DB 做, 避开 Python/DB 时区差）"""
    _sql = ("SELECT 1 AS x FROM unit_state_head WHERE channel=%s AND mode=%s "
            "AND stale=1 AND built_at > NOW(6) - INTERVAL %d SECOND" % int(_STALE_BACKOFF))
    try:
        return bool(one(_sql, [channel, mode]))
    except Exception:
        return False


def _risk_rows(channel, mode):
    """按 stock-risk 现有口径算一遍 → 扁平行(落 risk_snapshot)

    **不复制算法**: 直接调 `_stock_risk(legacy=True)` —— 它是唯一的计算入口。
    复制一份公式到本模块, 就是 §15.153 红线 D 说的"叠加成垃圾堆":
    以后改一处必漏另一处, 两张卡片的数会各自漂。
    重建在后台线程, 多花 4~5s 无感; 换来请求路径从"搬 21 万行"变成"读几百行"。
    """
    try:
        from routes.dashboard_stock_risk import _stock_risk
        p = _stock_risk(channel, full=1, legacy=True)
    except Exception as _e:
        try_err("unit_store", "risk 计算失败(%s/%s)" % (channel, mode), _e)
        return None
    rows = []
    for _d, _k, _t in _RISK_DIMS:
        for it in (p.get(_k) or []):
            rows.append({
                "dim": _d, "sku": str(it.get("sku") or "")[:80],
                "barcode": str(it.get("barcode") or "")[:40],
                "warehouse": str(it.get("warehouse") or "")[:80],
                "product_name": str(it.get("product_name") or "")[:255],
                "avail": int(it.get("available_qty") or 0),
                "ds": float(it.get("daily_sales") or 0),
                "adj_dos": float(it.get("days_to_empty") or 0),
                "level": str(it.get("level") or "")[:12],
                "accel": 1 if it.get("accel") else 0,
            })
    return rows


def _publish(channel, mode, fp, summ, alerts, n_units, risk=None):
    """gen+1 写入 → 校验已过 → head 一步切换 → 清 N-2 及更早"""
    h = head(channel, mode)
    gen = int((h or {}).get("gen") or 0) + 1
    execute("DELETE FROM unit_summary WHERE channel=%s AND mode=%s AND gen=%s", [channel, mode, gen])
    execute("DELETE FROM unit_alert WHERE channel=%s AND mode=%s AND gen=%s", [channel, mode, gen])
    execute("DELETE FROM risk_snapshot WHERE channel=%s AND mode=%s AND gen=%s", [channel, mode, gen])
    for (_kind, _wt), c in summ.items():
        execute("INSERT INTO unit_summary(channel,mode,gen,kind,wtype,total,ok,low,`out`,idle,paused,code_out) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [channel, mode, gen, _kind, _wt, c["total"], c["ok"], c["low"],
                 c["out"], c["idle"], c["paused"], c.get("code_out", 0)])
    for i in range(0, len(alerts), _CHUNK):
        part = alerts[i:i + _CHUNK]
        vals = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(part))
        _p = []
        for a in part:
            # 2026-11-07 P0 修 bug: 原来列清单漏了 adj_dos(表里有、INSERT 没写),
            #   导致该列恒为默认 0 —— stock-risk 若改读这张表会拿到"可撑 0 天"。
            _p += [channel, mode, gen, a["kind"], a["sku"], a["barcode"], a["warehouse"],
                   a["state"], a["avail"], a["transit"], a["ds"], a["line"], a["cover"],
                   a["gap"], a.get("adj_dos", 0)]
        execute("INSERT INTO unit_alert(channel,mode,gen,kind,sku,barcode,warehouse,state,"
                "avail,transit,ds,line,cover,gap,adj_dos) VALUES" + vals, _p)
    # ── 断货三级(risk_snapshot): 与 unit 同一个 gen 一起发布 ──
    #   失败**只影响断货卡**(读侧 fp/gen 校验不过 → 退回现算, 正确但慢), 不阻断判定模型发布。
    #   risk=None 表示本轮没算成: 本 gen 无行 → 读侧读不到 → 现算。绝不给错数。
    try:
        if risk:
            for i in range(0, len(risk), _CHUNK):
                part = risk[i:i + _CHUNK]
                vals = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(part))
                _p = []
                for a in part:
                    _p += [channel, mode, gen, a["dim"], a["sku"], a["barcode"], a["warehouse"],
                           a["product_name"], a["avail"], a["ds"], a["adj_dos"],
                           a["level"], a["accel"]]
                execute("INSERT INTO risk_snapshot(channel,mode,gen,dim,sku,barcode,warehouse,"
                        "product_name,avail,ds,adj_dos,level,accel) VALUES" + vals, _p)
    except Exception as _e:
        try_err("unit_store", "risk 发布失败(断货卡退回现算)", _e)
    # ── 唯一切换点 ──
    #   risk_rows: -1 = 本轮断货快照没算成 → 读侧退回现算(宁慢不错)。
    execute("INSERT INTO unit_state_head(channel,mode,gen,fp,units,alerts,risk_rows,stale,note,built_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,0,'ok',NOW(6)) "
            "ON DUPLICATE KEY UPDATE gen=VALUES(gen), fp=VALUES(fp), units=VALUES(units), "
            "alerts=VALUES(alerts), risk_rows=VALUES(risk_rows), stale=0, note='ok', "
            "built_at=VALUES(built_at)",
            [channel, mode, gen, fp, n_units, len(alerts),
             -1 if risk is None else len(risk)])
    execute("DELETE FROM unit_summary WHERE channel=%s AND mode=%s AND gen<%s",
            [channel, mode, gen - 1])
    execute("DELETE FROM risk_snapshot WHERE channel=%s AND mode=%s AND gen<%s",
            [channel, mode, gen - 1])
    execute("DELETE FROM unit_alert WHERE channel=%s AND mode=%s AND gen<%s",
            [channel, mode, gen - 1])
    return gen


def build(channel, mode=None, inv_rows=None, force=False):
    """构建并发布一个 (channel, mode) 的判定快照。幂等: 同一 fp 重复执行结果相同"""
    _m = M.mode_of(channel, mode)
    fp = fp_now(channel, _m)
    if not fp:
        return {"ok": False, "error": "fp_unavailable"}
    h = head(channel, _m)
    if not force and h and str(h.get("fp") or "") == fp and not int(h.get("stale") or 0):
        return {"ok": True, "skipped": "fresh", "gen": int(h.get("gen") or 0)}
    # 上次失败后退避 10 分钟 —— 否则校验一直不过时, 每个读请求都会再起一次重建线程
    #   比较放在 SQL 里: `built_at` 是 DB 会话时区(NOW(6)), Python 侧 datetime 是 naive,
    #   拿 `.timestamp()` 比会按本地时区解释 → 差 8 小时 → 退避永远成立(重建永久停摆)。
    if not force and h and int(h.get("stale") or 0) and _stale_recent(channel, _m):
        return {"ok": False, "skipped": "backoff"}
    _k = (str(channel), _m)
    _now = time.time()
    with _LOCK:
        _last = _LAST.get(_k)
        if not force and _last and _last[1] == fp and _now - _last[0] < _COOLDOWN:
            return {"ok": True, "skipped": "cooldown"}
        _LAST[_k] = (_now, fp)
    _t0 = time.time()
    try:
        sc = scan(channel, _m, inv_rows=inv_rows)
    except Exception as _e:
        _mark_stale(channel, _m, "scan 异常: %s" % _e)
        try_err("unit_store", "重建扫描失败(%s/%s)" % (channel, _m), _e)
        return {"ok": False, "error": "scan"}
    errs = _validate(channel, _m, sc)
    if errs:
        _mark_stale(channel, _m, "校验未通过: " + "; ".join(errs))
        try_err("unit_store", "重建校验未通过(%s/%s), 已保留旧版本" % (channel, _m),
                Exception("; ".join(errs)[:300]))
        return {"ok": False, "error": "validate", "detail": errs[:5]}
    # 断货三级快照: 放在校验通过之后 —— 校验不过要退回旧版本, 没必要为它白烧 4~5s。
    #   它是**同一次重建的副产品**, 不是第二个模型: 数据域/粒度/配置与判定同源, 天然同版本。
    _risk = _risk_rows(channel, _m)
    try:
        gen = _publish(channel, _m, fp, sc["tally"], sc["alerts"], len(sc["units"]), risk=_risk)
    except Exception as _e:
        _mark_stale(channel, _m, "发布异常: %s" % _e)
        try_err("unit_store", "重建发布失败(%s/%s)" % (channel, _m), _e)
        return {"ok": False, "error": "publish"}
    return {"ok": True, "gen": gen, "units": len(sc["units"]), "alerts": len(sc["alerts"]),
            "risk": len(_risk or []), "elapsed": round(time.time() - _t0, 2)}


# ── 触发 ────────────────────────────────────────────────────────
def _channels():
    try:
        return [str(r.get("code")) for r in query("SELECT code FROM channels WHERE enabled=1")
                if r.get("code")]
    except Exception:
        return []


def _ensure_all(force=False):
    """只为"已开启读"的渠道重建 —— 关闭态不必付这份算力(要建就显式 build(force=True))"""
    for _ch in _channels():
        try:
            _m = M.mode_of(_ch)
            _h = head(_ch, _m)
            if not _h or not int(_h.get("enabled") or 0):
                continue
            build(_ch, _m, force=force)
        except Exception as _e:
            try_err("unit_store", "重建失败(%s)" % _ch, _e)


def ensure_async(force=False):
    """写操作后/读未命中时后台重建 —— 不在响应路径上等它

    重复执行无害(幂等 + `_COOLDOWN`), 实例被冻结也无害(head 未切换 = 旧版本继续生效)。
    """
    try:
        _t = threading.Thread(target=_ensure_all, args=(force,), daemon=True)
        _t.start()
        return True
    except Exception as _e:
        try_err("unit_store", "重建线程启动降级", _e)
        return False


def status(channel, mode=None):
    """诊断口（/dashboard/unit-state）—— 四维逐项可查, 不靠猜"""
    _m = M.mode_of(channel, mode)
    h = head(channel, _m) or {}
    _fp = fp_now(channel, _m)
    _t = tally_of(channel, _m)
    _risk_rows = 0
    try:
        if int(h.get("gen") or 0):
            _r = one("SELECT COUNT(*) AS n FROM risk_snapshot WHERE channel=%s AND mode=%s AND gen=%s",
                     [channel, _m, int(h.get("gen") or 0)])
            _risk_rows = int((_r or {}).get("n") or 0)
    except Exception:
        _risk_rows = -1
    return {"channel": channel, "mode": _m, "read_enabled": read_enabled(),
            "enabled": int(h.get("enabled") or 0),
            "gen": int(h.get("gen") or 0), "units": int(h.get("units") or 0),
            "alerts": int(h.get("alerts") or 0), "stale": int(h.get("stale") or 0),
            "note": h.get("note") or "", "built_at": str(h.get("built_at") or ""),
            "fp_head": h.get("fp") or "", "fp_now": _fp,
            "fp_match": bool(_fp) and str(h.get("fp") or "") == _fp,
            "available": _t is not None, "buckets": len(_t or {}),
            # 断货三级快照(独立开关/独立表): risk_rows 是 head 记录的本轮产出数(-1=没算成),
            #   risk_rows_actual 是表里实际行数 —— 两者不等说明发布没写完(该报警)。
            "risk_enabled": int(h.get("risk_enabled") or 0),
            "risk_rows": int(h.get("risk_rows", -1)),
            "risk_rows_actual": _risk_rows}
