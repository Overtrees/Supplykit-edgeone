"""历史数据查询出口(2026-11-02 S2-4 方案 A): daily_stats 的第一个读取方

背景: /cron/archive 每天把 365 天前的订单搬进 daily_stats 并删除源行, 但此前全仓库
**零读取方**(`grep "FROM daily_stats"` 零命中, 前端也没有接口) —— 用户查一年前的订单
只会看到"没有数据", "归档保留年同比"是句空话。本模块就是那个出口。

两张表拼成**一个查询面**, 调用方不需要知道数据在哪:
  · orders       → 近 365 天(archive 只搬更早的)
  · daily_stats  → 365 天以前
两表数据互斥(archive 先写成功再删源行, 失败即中止), 所以按 365 天切线切开后
UNION ALL 不会重复计 —— 这条不变量是本模块正确性的全部前提, 改 archive 时必须同步。

口径(与 daily_stats 写入侧逐字一致, 否则跨切线会出现台阶):
  gmv = SUM(total_amount)  全部状态, 不扣优惠/不加运费税
  cnt = COUNT(*)           订单行数
  qty = SUM(quantity)      件数
与看板 GMV(已支付口径 total-discount+freight+tax)不同 —— 前端必须标注, 否则两个页面
数字对不上会被当成 bug。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from db import query, one
from routes.common import ok, traced, try_err
from routes.analysis_cache import cache_get as _cache_get
from core.clock import now_cn

router = APIRouter(tags=["history"])

# 与 cron.cron_archive 的 cutoff 必须一致(见模块注释的不变量)
_CUT_DAYS = 365
# 单次查询最大跨度: 订单侧是逐行聚合, 一年约百万行, 再长就是全表扫描
_MAX_SPAN = 400


def _today():
    return now_cn().strftime("%Y-%m-%d")


def _cutoff():
    return (now_cn() - timedelta(days=_CUT_DAYS)).strftime("%Y-%m-%d")


def _archive_day():
    """一笔**今天下的订单**要等到哪天才会被归档

    cron_archive 的判定是 `DATE(ordered_at) < today-365`(注意是严格小于), 所以 t 日下的
    订单在 (t+366) 日第一次满足 —— 报"满 365 天后"是常见误读(实际第 366 天才搬)。
    前端提示按这个日期算, 与 cron 的判定式逐字对齐, 不另立口径。
    """
    return (now_cn() + timedelta(days=1)).strftime("%Y-%m-%d")


def _shift(day, days):
    _d = datetime.strptime(str(day)[:10], "%Y-%m-%d") + timedelta(days=days)
    return _d.strftime("%Y-%m-%d")


def _range(start, end):
    """规整 [start, end]: 缺省补值、防反序、限跨度(400 天)

    限跨度的理由很具体: 订单侧是逐行聚合, 一年 ≈ 100 万行(实测 2 个月 18 万行 0.3s),
    放到 3 年就是一次全表扫描 —— 直接把实例打慢, 连带看板一起超时。
    """
    _e = str(end or "").strip()[:10] or _today()
    _s = str(start or "").strip()[:10] or _shift(_e, -29)
    try:
        datetime.strptime(_s, "%Y-%m-%d")
        datetime.strptime(_e, "%Y-%m-%d")
    except Exception:
        _e, _s = _today(), _shift(_today(), -29)
    if _s > _e:
        _s, _e = _e, _s
    _clamped = False
    if _shift(_s, _MAX_SPAN) < _e:
        _s = _shift(_e, -(_MAX_SPAN - 1))
        _clamped = True
    return _s, _e, _clamped


def _union(channel, start, end, key=""):
    """按 365 天切线把 [start,end] 拆成 归档段 + 订单段, 返回 (子查询 SQL, 参数)

    输出列固定 (d, gmv, cnt, qty[, k]), 外层直接 GROUP BY。
    key 传 "sku"/"store" 时附带该维度(两端字段同名, 不用适配)。
    两段都为空(整段既不在订单表也不在归档表) → (None, [])。
    """
    _cut = _cutoff()
    _a_end = _shift(_cut, -1)          # 归档: date <= cut-1
    _o_start = _cut                    # 订单: ordered_at >= cut
    _k = (", %s AS k" % key) if key else ""
    parts, params = [], []
    if start <= _a_end:
        _e = min(end, _a_end)
        parts.append(
            "SELECT `date` AS d, gmv, order_count AS cnt, quantity AS qty" + _k +
            ", 'a' AS src FROM daily_stats WHERE channel=%s AND `date`>=%s AND `date`<=%s")
        params += [channel, start, _e]
    if end >= _o_start:
        _s = max(start, _o_start)
        parts.append(
            "SELECT DATE(ordered_at) AS d, total_amount AS gmv, 1 AS cnt, quantity AS qty" + _k +
            ", 'o' AS src FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') "
            "AND ordered_at>=%s AND ordered_at<DATE_ADD(%s, INTERVAL 1 DAY)")
        params += [channel, _s, end]
    if not parts:
        return None, []
    return "(" + " UNION ALL ".join(parts) + ") t", params


def _num(v):
    try:
        return round(float(v or 0), 2)
    except Exception:
        return 0.0


def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


@router.get("/history/coverage")
@traced
def history_coverage(channel: str = ""):
    """归档覆盖范围 —— 先回答"能查到什么", 再谈查到了什么

    历史页最糟的体验是"选了 2023 年, 表格空白, 不知道为什么"。这个端点给出
    订单表/归档表各自的日期边界与行数, 前端据此提示"归档从哪天开始有数据"。
    """
    def _build():
        out = {"cutoff": _cutoff(), "cut_days": _CUT_DAYS, "archive": None, "orders": None}
        # 2026-11-02 S2-9: "新订单何时开始进归档" —— 前端把归档状态收进锤子菜单后,
        #   顶部只剩一行覆盖摘要, 这句"哪天起自动归档"需要一份可信口径, 由后端给
        #   (前端自己拿 cut_days 算时区/边界日容易差一天)。
        out["archive_day"] = _archive_day()
        try:
            _w = " WHERE channel=%s" if channel else ""
            _p = [channel] if channel else []
            _a = one("SELECT COUNT(*) AS c, MIN(`date`) AS mn, MAX(`date`) AS mx, "
                     "COUNT(DISTINCT sku) AS skus FROM daily_stats" + _w, _p) or {}
            out["archive"] = {"rows": _int(_a.get("c")), "min": str(_a.get("mn") or "")[:10],
                              "max": str(_a.get("mx") or "")[:10], "skus": _int(_a.get("skus"))}
            _o = one("SELECT COUNT(*) AS c, MIN(ordered_at) AS mn, MAX(ordered_at) AS mx "
                     "FROM orders WHERE (deleted_at IS NULL OR deleted_at='')" +
                     (" AND channel=%s" if channel else ""), _p) or {}
            out["orders"] = {"rows": _int(_o.get("c")), "min": str(_o.get("mn") or "")[:10],
                             "max": str(_o.get("mx") or "")[:10]}
            # 早于归档线但仍在 orders 里的行(2026-11-02 S2-2): 只可能是"补录了 365 天前的数据",
            # archive 还没搬走。这段区间**本页查不到**(归档段读 daily_stats, 订单段从切线起),
            # 与其让用户对着空白表格猜, 不如直接告诉他"下次归档后可见"。
            _st = one("SELECT COUNT(*) AS c FROM orders "
                      "WHERE (deleted_at IS NULL OR deleted_at='') AND ordered_at < %s" +
                      (" AND channel=%s" if channel else ""),
                      [_cutoff()] + ([channel] if channel else [])) or {}
            out["stale_orders"] = _int(_st.get("c"))
        except Exception as _e:
            try_err('history', '覆盖率查询降级', _e)
        # 人话结论: 归档为空时直接说清"为什么空", 别让用户以为是查询坏了
        _a = out.get("archive") or {}
        if not _a.get("rows"):
            _mn = (out.get("orders") or {}).get("min") or ""
            # 一句人话即可: 长句("保留期 N 天, 归档只搬 N 天前的订单")在卡片顶部会折三行,
            # 而用户只需要知道"为什么空 / 什么时候会有"
            out["note"] = ("暂无归档：订单自 %s 起，满 %d 天后自动归档"
                           % (_mn or "-", _CUT_DAYS))
        else:
            out["note"] = "归档覆盖 %s ~ %s" % (_a.get("min"), _a.get("max"))
        if out.get("stale_orders"):
            out["note"] += " · 另有 %d 行早于归档线待搬移" % out["stale_orders"]
        return out
    _key = "history|coverage|%s" % (channel or "all")
    return ok(_cache_get(_key, 300, _build))


@router.get("/history/summary")
@traced
def history_summary(channel: str = "jd", start: str = "", end: str = "", by: str = "day"):
    """区间汇总 + 趋势序列(按日/按月)

    跨 365 天切线时两段来自不同表 —— 因为口径逐字对齐, 曲线是连续的;
    若哪天 archive 的写入口径改了, 这里会立刻出现台阶, 属于有意保留的"报警器"。
    """
    s, e, _clamped = _range(start, end)
    _by = by if by in ("day", "month") else "day"

    def _build():
        sql, params = _union(channel, s, e)
        _days = max(1, (datetime.strptime(e, "%Y-%m-%d") -
                        datetime.strptime(s, "%Y-%m-%d")).days + 1)
        if not sql:
            return {"start": s, "end": e, "by": _by, "clamped": _clamped,
                    "totals": {"gmv": 0.0, "orders": 0, "qty": 0, "span_days": _days,
                               "active_days": 0, "avg_gmv_per_day": 0.0},
                    "series": [], "sources": {"orders": None, "archive": None}}
        # %% 是给 pymysql 的: 带 params 时它会对整条 SQL 做一次 % 格式化,
        # 写成 %Y 会被当成格式符 → ValueError('unsupported format character')。
        _k = "d" if _by == "day" else "DATE_FORMAT(d,'%%Y-%%m')"
        rows = query("SELECT " + _k + " AS k, src, SUM(gmv) AS gmv, SUM(cnt) AS cnt, "
                     "SUM(qty) AS qty FROM " + sql + " GROUP BY k, src ORDER BY k", params) or []
        # 合并同一天的两个来源(切线两侧); 顺便记下每个来源自己的日期区间 —— 这样
        # "来源"字段反映的是**真的有数据**的那段, 而不是"本来打算查的那段"。
        _map, _src = {}, {}
        for r in rows:
            _key = str(r.get("k") or "")
            _m = _map.setdefault(_key, {"date": _key, "gmv": 0.0, "orders": 0, "qty": 0})
            _m["gmv"] = round(_m["gmv"] + _num(r.get("gmv")), 2)
            _m["orders"] += _int(r.get("cnt"))
            _m["qty"] += _int(r.get("qty"))
            _code = r.get("src") or "o"
            _b = _src.setdefault(_code, {"from": _key, "to": _key, "days": 0})
            _b["from"], _b["to"] = min(_b["from"], _key), max(_b["to"], _key)
            _b["days"] += 1
        series = [_map[k] for k in sorted(_map)]
        _gmv = sum(x["gmv"] for x in series)
        _cnt = sum(x["orders"] for x in series)
        _qty = sum(x["qty"] for x in series)
        return {"start": s, "end": e, "by": _by, "clamped": _clamped,
                "totals": {"gmv": round(_gmv, 2), "orders": _cnt, "qty": _qty,
                           "span_days": _days, "active_days": len(series),
                           "avg_gmv_per_day": round(_gmv / _days, 2)},
                "series": series,
                "sources": {"orders": _src.get("o"), "archive": _src.get("a")}}

    _key = "history|summary|%s|%s|%s|%s" % (channel, s, e, _by)
    return ok(_cache_get(_key, 120, _build))


@router.get("/history/top")
@traced
def history_top(channel: str = "jd", start: str = "", end: str = "",
                by: str = "sku", limit: int = 20):
    """区间 Top 榜: 按 SKU 或店铺 —— 归档里同样有这两个维度, 所以历史数据也能排"""
    s, e, _clamped = _range(start, end)
    _by = by if by in ("sku", "store") else "sku"
    _n = max(1, min(int(limit or 20), 200))

    def _build():
        sql, params = _union(channel, s, e, key=_by)
        if not sql:
            return {"start": s, "end": e, "by": _by, "clamped": _clamped,
                    "total_gmv": 0.0, "items": []}
        # 占比用窗口函数一次算完: 再发一条 SUM 查询等于把整段聚合跑两遍(365 天约 2s)
        rows = query(
            "SELECT k, SUM(gmv) AS gmv, SUM(cnt) AS cnt, SUM(qty) AS qty, "
            "SUM(SUM(gmv)) OVER () AS total FROM %s GROUP BY k ORDER BY gmv DESC LIMIT %d"
            % (sql, _n), params) or []
        _tot = _num(rows[0].get("total")) if rows else 0.0
        items = [{"key": str(r.get("k") or ""), "gmv": _num(r.get("gmv")),
                  "orders": _int(r.get("cnt")), "qty": _int(r.get("qty")),
                  "share": round(_num(r.get("gmv")) / _tot * 100, 1) if _tot else 0.0}
                 for r in rows]
        return {"start": s, "end": e, "by": _by, "clamped": _clamped,
                "total_gmv": _tot, "items": items}

    _key = "history|top|%s|%s|%s|%s|%d" % (channel, s, e, _by, _n)
    return ok(_cache_get(_key, 120, _build))


def _delta(cur, prev):
    """同比增幅(%), 分母为 0 时给 None 而不是 0/无穷 —— 前端据此显示"—"而非"0%" """
    out = {}
    for _k in ("gmv", "orders", "qty"):
        _p = prev.get(_k) or 0
        out[_k] = round((cur.get(_k, 0) - _p) / _p * 100, 1) if _p else None
    return out


@router.get("/history/yoy")
@traced
def history_yoy(channel: str = "jd", end: str = "", days: int = 30,
                start: str = ""):
    """同比: 本报区间 vs 去年同期(去年那份来自归档表)

    关键取舍: **没有去年数据就返回 has_prev=false, 不算同比**。
    直接算 (今年-0)/0 会显示 -100%, 用户会以为生意掉了 100% —— 这比"暂无同比"有害得多。

    2026-11-02 S2-9(用户定案"同比跟随区间"): 新增 start。
    此前只认 days(默认近 30 天), 而历史页上方汇总跟着菜单区间走 —— 选「近 90 天」时
    页面里同时存在 90 天汇总与 30 天同比两个时间口径, 且界面上没有任何字说明。
    现在传 [start,end] 就按这段算, 去年同期 = 各自减 _CUT_DAYS; 不传 start 仍走
    原 days 语义(向后兼容, 其它调用方零改动)。
    """
    _e = str(end or "").strip()[:10] or _today()
    _s = str(start or "").strip()[:10]
    if _s:
        try:
            datetime.strptime(_s, "%Y-%m-%d")
            datetime.strptime(_e, "%Y-%m-%d")
        except Exception:
            _s = ""
    if _s and _s <= _e:
        # 区间语义: 天数由两端日期决定(含首尾), 同样限 400 天与 _range 一致
        _n = (datetime.strptime(_e, "%Y-%m-%d") -
              datetime.strptime(_s, "%Y-%m-%d")).days + 1
        if _n > _MAX_SPAN:
            _s = _shift(_e, -(_MAX_SPAN - 1))
            _n = _MAX_SPAN
    else:
        _n = max(1, min(int(days or 30), 366))
        _s = _shift(_e, -(_n - 1))
    _ps, _pe = _shift(_s, -_CUT_DAYS), _shift(_e, -_CUT_DAYS)

    def _window(start, end):
        sql, params = _union(channel, start, end)
        if not sql:
            return {"start": start, "end": end, "gmv": 0.0, "orders": 0, "qty": 0}
        r = one("SELECT SUM(gmv) AS gmv, SUM(cnt) AS cnt, SUM(qty) AS qty FROM %s" % sql,
                params) or {}
        return {"start": start, "end": end, "gmv": _num(r.get("gmv")),
                "orders": _int(r.get("cnt")), "qty": _int(r.get("qty"))}

    def _build():
        cur = _window(_s, _e)
        prev = _window(_ps, _pe)
        _has = prev["orders"] > 0
        out = {"channel": channel, "days": _n, "cur": cur, "prev": prev, "has_prev": _has,
               "delta": None, "note": ""}
        if not _has:
            # 给 API 直接消费方的一句结论; 前端自己用 prev.start/end 拼完整句子
            out["note"] = "去年同期(%s ~ %s)无数据, 不做同比" % (_ps, _pe)
        else:
            out["delta"] = _delta(cur, prev)
        return out

    # key 必须含 _s: 加了 start 参数后, 同一 end 配不同起点是两份不同结果
    _key = "history|yoy|%s|%s|%s|%d" % (channel, _s, _e, _n)
    return ok(_cache_get(_key, 120, _build))
