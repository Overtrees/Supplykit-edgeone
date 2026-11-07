"""core/own_store.py —— 自有仓健康明细物化读模型（2026-11-07 P2）

## 一句话
把「自有仓三类告警的每一条」算一次、排好序存下来；请求路径只剩按 seq 读一页。

## 为什么需要（线上实测）
`/dashboard/own-health` 每页 1.92s，而翻第 2 页还是 1.92s —— 因为它**每页都把 1.4 万条
从头算一遍再切片**。`debug=1` 的分段计时拆得很清楚:

    products 0.07s / ds 0.59s / batches 0.59s / 组装 0.11s / 收尾 0.02s
    + own_rows 那条 GROUP BY(未埋点) ≈ 0.9s

结论: 慢在**三次取数**(库存聚合 / 下游日销 2 万行 / 批次效期 1.5 万行)，不在 Python 组装。
这正是红线的边界（见 docs/DEVELOPMENT.md 红线 11）:
    分页治"搬得多"，物化治"算得慢"。在"每次现算全量"之上加分页是负优化 —— 翻 10 页等于算 10 遍。

## 设计（与 unit_store 同一套骨架，不另造轮子）
- **指纹驱动，不是 TTL**: `fp` 取 `data_version_dom` 的域计数器（与判定模型同一组域），
  写操作 bump → 下一次请求 fp 不等 → 不读物化、退回现算，并起后台重建。
  读路径**永不返回可能过期的行**: fp 不等就 `None`，调用方走今天的老路（慢但正确）。
- **排序落序号**: 条目顺序由 `_compute()` 的 `_key` 唯一决定，重建时写成 `seq`。
  读侧 `ORDER BY seq` 切页 —— SQL 不参与排序（collation 与 Python 码位序不同，两套排序必错位）。
- **一步切换**: 新 gen 的 1.4 万行**全部写完并核对过计数**，才 `UPDATE head.gen`。
  中途失败 → head 仍指旧 gen → 读侧继续用旧版本（正确，只是不是最新）。
- **回滚**: `OWN_HEALTH_READ=0` 即回现算路径，不需重新部署。

## 与 alerts 表的关系
`own_overstock` 等三类在 `alerts` 里也有一份（规则引擎写的）。**不用它当读模型**:
它是"评估那一刻"的快照，刷新节奏与本卡独立，且缺 gap_qty/cover_days/days_to_expiry 等
卡面字段。拿它顶替会让"卡面数字"和"弹窗明细"来自两次不同的计算 —— 那才是真正的不一致。
"""
import hashlib
import os
import threading
import time

from db import query, one, execute, executemany
from routes.common import try_err

# 依赖域 = 判定模型那一组（orders 二阶日销 / inventory / products / rules / config / schema）。
#   为什么直接引用 unit_store 的常量而不是抄一份: 抄一份就会各自漂移 —— 判定引入了新域,
#   这里忘了加, 表现是"数据变了但自有仓卡还在用旧值", 而这正是 fp 机制要防的事。
#   差异只在 suppliers: 断货算 OTIF 要看评分, 自有仓三类不看(它只看自有可用/在途/效期)。
# 依赖域（与判定模型同一组）—— 见下方 fp_now 的说明
DOMAINS = ("orders", "inventory", "products", "rules", "config", "schema")

_CHUNK = 400          # 明细批量插入行数(400 × 17 列 = 6800 占位符, 远低于单语句上限)
_COOLDOWN = 60.0      # 同一渠道两次重建最小间隔(秒) —— 一次导入会连调多次 invalidate
_STALE_BACKOFF = 600.0  # 校验失败后的退避, 防每个读请求都再烧一次 7s 重建
_LOCK = threading.Lock()
_LAST = {}
_TYPES = ("own_transfer_gap", "own_overstock", "own_expiry_risk")

_READ_ON = ("1", "true", "yes", "on")
_COLS = ("seq, alert_type, level, sku, barcode, warehouse, product_name, avail, need_qty, "
         "gap_qty, cover_days, days_to_expiry, batch_no, exp_date, title, description")


def read_enabled():
    """读路径总开关（环境变量）。未设置 = 开；"0" = 一律现算（紧急回滚，不必发版）"""
    _v = os.environ.get("OWN_HEALTH_READ")
    if _v is None or str(_v).strip() == "":
        return True
    return str(_v).strip().lower() in _READ_ON


# ── 指纹 ────────────────────────────────────────────────────────
def fp_now(channel):
    """当前数据版本指纹（空串 = 不可用 → 一律走现算）"""
    try:
        from core import unit_store as _US
        return _US.fp_of(DOMAINS, channel)
    except Exception as _e:
        try_err("own_store", "指纹计算降级", _e)
        return ""


def _stat_str(d):
    """计数 + 生效阈值 → 一行定长文本（读侧解析失败即视为不可用，退回现算）"""
    _l = d.get("limits") or {}
    return "total:%d;tg:%d;os:%d;er:%d;crit:%d;warn:%d;od:%g;eb:%g;b2c:%g" % (
        int(d.get("total") or 0), int(d.get("transferGap") or 0),
        int(d.get("overstock") or 0), int(d.get("expiryRisk") or 0),
        int(d.get("critical") or 0), int(d.get("warning") or 0),
        float(_l.get("overstock_days") or 0), float(_l.get("expiry_buffer_days") or 0),
        float(_l.get("b2c_days") or 0))


def _parse_stat(s):
    try:
        _m = {}
        for _seg in str(s or "").split(";"):
            if not _seg:
                continue
            _k, _v = _seg.split(":", 1)
            _m[_k] = float(_v) if _k in ("od", "eb", "b2c") else int(float(_v))
        if "total" not in _m:
            return None
        return _m
    except Exception:
        return None


# ── 读 ──────────────────────────────────────────────────────────
def head(channel):
    try:
        return one("SELECT * FROM own_health_head WHERE channel=%s", [channel]) or None
    except Exception as _e:
        try_err("own_store", "head 读取降级", _e)
        return None


def _row_to_item(r):
    _cd = r.get("cover_days")
    _de = r.get("days_to_expiry")
    return {
        "sku": str(r.get("sku") or ""), "barcode": str(r.get("barcode") or ""),
        "product_name": str(r.get("product_name") or ""), "warehouse": str(r.get("warehouse") or ""),
        "alert_type": str(r.get("alert_type") or ""), "level": str(r.get("level") or ""),
        "title": str(r.get("title") or ""), "description": str(r.get("description") or ""),
        "available_qty": int(r.get("avail") or 0), "need_qty": int(r.get("need_qty") or 0),
        "gap_qty": int(r.get("gap_qty") or 0),
        "cover_days": round(float(_cd), 1) if _cd is not None else None,
        "days_to_expiry": int(_de) if _de is not None else None,
        "batch_no": str(r.get("batch_no") or ""), "exp_date": str(r.get("exp_date") or ""),
    }


def page_of(channel, full=0, limit=10, offset=0, per_type=0, only=""):
    """读一页；`None` = 不可用（调用方退回现算）。

    分页语义必须与 `dashboard_own_health._page` **逐字一致**（否则"卡面 3 条"和
    "弹窗第 1 页"会对不上）:
      · per_type=n → 每类各取前 n 条（卡片要三类都露头，不能整段截断）
      · full=1     → seq 区间 [offset, offset+limit)
      · 其余       → 前 limit 条
      · only=类型  → 先按类型过滤，再套上面三条
    """
    if not read_enabled():
        return None
    h = head(channel)
    if not h or not int(h.get("gen") or 0) or int(h.get("stale") or 0):
        return None
    _fp = fp_now(channel)
    if not _fp or str(h.get("fp") or "") != _fp:
        return None
    st = _parse_stat(h.get("stat"))
    if st is None:
        return None
    _g = int(h.get("gen"))
    _lim = max(1, int(limit or 10))
    _off = max(0, int(offset or 0))
    _where = "channel=%s AND gen=%s"
    _p = [channel, _g]
    if only:
        _where += " AND alert_type=%s"
        _p.append(only)
    rows = []
    try:
        if per_type:
            for _t in _TYPES:
                if only and _t != only:
                    continue
                rows.extend(query(
                    "SELECT " + _COLS + " FROM own_health_snapshot WHERE " + _where +
                    " AND alert_type=%s ORDER BY seq LIMIT %d", _p + [_t, int(per_type)]) or [])
        elif full:
            rows = query("SELECT " + _COLS + " FROM own_health_snapshot WHERE " + _where +
                         " ORDER BY seq LIMIT %d OFFSET %d" % (_lim, _off), _p) or []
        else:
            rows = query("SELECT " + _COLS + " FROM own_health_snapshot WHERE " + _where +
                         " ORDER BY seq LIMIT %d" % _lim, _p) or []
    except Exception as _e:
        try_err("own_store", "明细读取降级", _e)
        return None
    if per_type:
        # 三类拼起来后再按全局 seq 排 —— 与现算 `_page(_per_type())` 的产出顺序一致
        rows.sort(key=lambda r: int(r.get("seq") or 0))
    return {
        "items": [_row_to_item(r) for r in rows],
        "total": int(st.get("total") or 0), "transferGap": int(st.get("tg") or 0),
        "overstock": int(st.get("os") or 0), "expiryRisk": int(st.get("er") or 0),
        "critical": int(st.get("crit") or 0), "warning": int(st.get("warn") or 0),
        "limits": {"overstock_days": st.get("od") or 0, "expiry_buffer_days": st.get("eb") or 0,
                   "b2c_days": st.get("b2c") or 0},
    }


# ── 写 ──────────────────────────────────────────────────────────
def _mark_stale(channel, note):
    try:
        execute("INSERT INTO own_health_head(channel,stale,note,built_at) "
                "VALUES(%s,1,%s,NOW(6)) ON DUPLICATE KEY UPDATE stale=1, note=VALUES(note)",
                [channel, str(note)[:250]])
    except Exception as _e:
        try_err("own_store", "标脏失败(%s)" % channel, _e)


def _stale_recent(channel):
    """stale 且最近 10 分钟内失败过 → 退避（时间比较放在 SQL 里，避免时区解释差异）"""
    try:
        r = one("SELECT COUNT(*) AS n FROM own_health_head WHERE channel=%s AND stale=1 "
                "AND built_at > DATE_SUB(NOW(6), INTERVAL %d SECOND)",
                [channel, int(_STALE_BACKOFF)])
        return int((r or {}).get("n") or 0) > 0
    except Exception:
        return False


def build(channel, force=False):
    """算一次 → 写入新 gen → 核对计数 → 切 head。幂等：同一 fp 重复执行结果相同"""
    fp = fp_now(channel)
    if not fp:
        return {"ok": False, "error": "fp_unavailable"}
    h = head(channel)
    if (not force and h and str(h.get("fp") or "") == fp
            and not int(h.get("stale") or 0)):
        return {"ok": True, "skipped": "fresh", "gen": int(h.get("gen") or 0)}
    if not force and h and int(h.get("stale") or 0) and _stale_recent(channel):
        return {"ok": False, "skipped": "backoff"}
    _now = time.time()
    with _LOCK:
        _last = _LAST.get(channel)
        if not force and _last and _last[1] == fp and _now - _last[0] < _COOLDOWN:
            return {"ok": True, "skipped": "cooldown"}
        _LAST[channel] = (_now, fp)

    _t0 = time.time()
    try:
        from routes.dashboard_own_health import _compute
        d = _compute(channel)
    except Exception as _e:
        _mark_stale(channel, "计算异常: %s" % _e)
        try_err("own_store", "自有仓重建计算失败(%s)" % channel, _e)
        return {"ok": False, "error": "compute"}
    _t_calc = time.time() - _t0

    items = d.get("items") or []
    gen = (int(h.get("gen") or 0) + 1) if h else 1
    _t_w0 = time.time()
    try:
        if items:
            _payload = []
            for _i, it in enumerate(items):
                _cd = it.get("cover_days")
                _de = it.get("days_to_expiry")
                _payload.append([
                    channel, gen, _i, str(it.get("alert_type") or "")[:32],
                    str(it.get("level") or "")[:12], str(it.get("sku") or "")[:80],
                    str(it.get("barcode") or "")[:40], str(it.get("warehouse") or "")[:80],
                    str(it.get("product_name") or "")[:255], int(it.get("available_qty") or 0),
                    int(it.get("need_qty") or 0), int(it.get("gap_qty") or 0),
                    None if _cd is None else round(float(_cd), 1),
                    None if _de is None else int(_de),
                    str(it.get("batch_no") or "")[:40], str(it.get("exp_date") or "")[:20],
                    str(it.get("title") or "")[:300], str(it.get("description") or "")[:900]])
            for _i in range(0, len(_payload), _CHUNK):
                executemany(
                    "INSERT INTO own_health_snapshot(channel,gen,seq,alert_type,level,sku,"
                    "barcode,warehouse,product_name,avail,need_qty,gap_qty,cover_days,"
                    "days_to_expiry,batch_no,exp_date,title,description) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    _payload[_i:_i + _CHUNK])
    except Exception as _e:
        _mark_stale(channel, "写入异常: %s" % _e)
        try_err("own_store", "自有仓重建写入失败(%s)" % channel, _e)
        return {"ok": False, "error": "write"}
    _t_write = time.time() - _t_w0

    # 校验: 库里的三类计数必须与计算时一致 —— 写一半/写重复都在这里暴露, 不切 head。
    errs = []
    try:
        _c = query("SELECT alert_type, COUNT(*) AS n FROM own_health_snapshot "
                   "WHERE channel=%s AND gen=%s GROUP BY alert_type", [channel, gen]) or []
        _got = {str(r.get("alert_type")): int(r.get("n") or 0) for r in _c}
        _sum = sum(_got.values())
        if _sum != len(items):
            errs.append("总行数 库=%d 算=%d" % (_sum, len(items)))
        for _t, _k in (("own_transfer_gap", "transferGap"), ("own_overstock", "overstock"),
                       ("own_expiry_risk", "expiryRisk")):
            if _got.get(_t, 0) != int(d.get(_k) or 0):
                errs.append("%s 库=%d 算=%d" % (_t, _got.get(_t, 0), int(d.get(_k) or 0)))
    except Exception as _e:
        errs.append("核对失败: %s" % _e)
    if errs:
        _mark_stale(channel, "校验未通过: " + "; ".join(errs))
        try_err("own_store", "自有仓重建校验未通过(%s), 已保留旧版本" % channel,
                Exception("; ".join(errs)[:300]))
        return {"ok": False, "error": "validate", "detail": errs[:5]}

    timing = "calc=%.1f,write=%.1f,total=%.1f,rows=%d" % (
        _t_calc, _t_write, time.time() - _t0, len(items))
    try:
        execute("INSERT INTO own_health_head(channel,gen,fp,row_cnt,stat,timing,stale,note,built_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,0,'ok',NOW(6)) "
                "ON DUPLICATE KEY UPDATE gen=VALUES(gen), fp=VALUES(fp), row_cnt=VALUES(row_cnt), "
                "stat=VALUES(stat), timing=VALUES(timing), stale=0, note='ok', "
                "built_at=VALUES(built_at)",
                [channel, gen, fp, len(items), _stat_str(d), timing])
        execute("DELETE FROM own_health_snapshot WHERE channel=%s AND gen<%s", [channel, gen - 1])
    except Exception as _e:
        _mark_stale(channel, "发布异常: %s" % _e)
        try_err("own_store", "自有仓重建发布失败(%s)" % channel, _e)
        return {"ok": False, "error": "publish"}
    return {"ok": True, "gen": gen, "rows": len(items), "timing": timing,
            "elapsed": round(time.time() - _t0, 2)}


# ── 触发 ────────────────────────────────────────────────────────
def _channels():
    try:
        return [str(r.get("code")) for r in query("SELECT code FROM channels WHERE enabled=1")
                if r.get("code")]
    except Exception:
        return []


def ensure_async(channel=None, force=False):
    """读未命中时后台重建 —— 不在响应路径上等它

    重复执行无害（幂等 + cooldown）；实例被冻结也无害（head 未切换 = 旧版本继续生效，
    读侧走现算兜底）。不传 channel 时遍历已开启渠道（冷启动/维护用）。
    """
    def _run(_chs):
        for _c in _chs:
            try:
                build(_c, force=force)
            except Exception as _e2:
                try_err("own_store", "重建失败(%s)" % _c, _e2)

    try:
        _chs = [channel] if channel else _channels()
        _t = threading.Thread(target=_run, args=(_chs,), daemon=True)
        _t.start()
        return True
    except Exception as _e:
        try_err("own_store", "重建线程启动降级", _e)
        return False


def status(channel):
    """诊断口(/dashboard/own-health/state): 四维逐项可查, 不靠猜"""
    h = head(channel) or {}
    _fp = fp_now(channel)
    _n = 0
    if int(h.get("gen") or 0):
        try:
            _n = int((one("SELECT COUNT(*) AS n FROM own_health_snapshot WHERE channel=%s AND gen=%s",
                          [channel, int(h.get("gen"))]) or {}).get("n") or 0)
        except Exception:
            _n = -1
    return {
        "read_enabled": read_enabled(), "gen": int(h.get("gen") or 0),
        "fp_head": str(h.get("fp") or ""), "fp_now": _fp,
        "fp_match": bool(_fp) and str(h.get("fp") or "") == _fp,
        "row_cnt": int(h.get("row_cnt") or 0), "rows_actual": _n,
        "stat": str(h.get("stat") or ""), "timing": str(h.get("timing") or ""),
        "stale": int(h.get("stale") or 0), "note": str(h.get("note") or ""),
        "built_at": str(h.get("built_at") or ""),
    }
