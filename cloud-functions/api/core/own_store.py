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

_CHUNK = 1000         # §15.168 明细批量插入行数(1000 × 17 列 = 17000 占位符, 远低于上限;
                      #   线上首建 write 3.1s 里 36 次往返, 合并成 15 次)
_COOLDOWN = 60.0      # 同一渠道两次重建最小间隔(秒) —— 一次导入会连调多次 invalidate
# §15.164: 原 600s。写新 gen 撞主键后标脏 + 每次刷新 built_at 会把退避无限续期,
# 失败不再置 stale(见 _mark_stale), 这里只作有界防御(红线 15 不靠盲重试)。
_STALE_BACKOFF = 20.0
_LOCK = threading.Lock()
_LAST = {}
_TYPES = ("own_transfer_gap", "own_overstock", "own_expiry_risk")

_READ_ON = ("1", "true", "yes", "on")


def _safe_rebuild():
    """§15.164 总开关: 未设置=开; `OWN_SAFE_REBUILD=0` 一刀回退到修复前行为。"""
    _v = os.environ.get("OWN_SAFE_REBUILD")
    if _v is None or str(_v).strip() == "":
        return True
    return str(_v).strip().lower() in _READ_ON


def _flag(name, default=True):
    """§15.165 开关族: 未设置=开, 置 "0" 一刀回退(不必发版), 红线 2 互斥不叠加。"""
    _v = os.environ.get(name)
    if _v is None or str(_v).strip() == "":
        return default
    return str(_v).strip().lower() in _READ_ON


def _swr_on():
    """own-health 读侧 SWR 总开关: `OWN_SWR=0` 回到「失配即同步 _compute 全量」旧路径。"""
    return _flag("OWN_SWR")


def _stale_prev_on():
    return _flag("OWN_STALE_PREV")


def _budget_on():
    """后台重建是否收编进 list_store.submit_background(db.heavy 全局预算 + 单飞)。
    `OWN_BUDGET=0` 回退到裸 daemon 线程(旧行为)。红线 4: 只复用既有闸门, 不新建。"""
    return _flag("OWN_BUDGET")


_STAT = {"hit": 0, "swr_served": 0, "first_legacy": 0, "rebuild_done": 0,
         "rebuild_failed": 0}


def swr_meta(channel, h, state):
    """顶层 __swr 播报; freshness_s 用 **DB 时间**(实例与 DB 时区可能不同), 不静默伪装新值。"""
    age = -1
    try:
        r = one("SELECT TIMESTAMPDIFF(SECOND, built_at, NOW(6)) AS age_s "
                "FROM own_health_head WHERE channel=%s", [channel])
        v = (r or {}).get("age_s")
        age = int(v) if v is not None and int(v) >= 0 else -1
    except Exception:
        age = -1
    return {"state": "serving_previous", "freshness_s": age,
            "built_at": str(h.get("built_at") or ""), "fp_changed": True,
            "prev_stale": bool(int(h.get("stale") or 0)), "rebuild": state,
            "source": "own_mat", "retry_after_ms": 900}


def submit_rebuild(channel):
    """后台补建 —— 复用 list_store 的分舱单飞 + db.heavy 全局预算(红线 4/5), 不另造闸门。"""
    try:
        from core import list_store as _LS
        return _LS.submit_background("own:" + str(channel), str(channel),
                                     fp_now(channel) or "?",
                                     lambda: build(channel, force=False))
    except Exception as _e:
        try_err("own_store", "后台重建调度降级(%s)" % channel, _e)
        return "error"


def serve(channel, full=0, limit=10, offset=0, per_type=0, only="",
          compute=None, legacy=0):
    """own-health 读路径唯一入口(§15.165) → (data, swr_meta_or_None)。与 list_store /
    unit_store / dash_store 同构: 稳态读物化 / 冷态**读侧零等待**返回上一版完整物化 +
    顶层 __swr + 后台分舱单飞重建 / 首次单飞现算一次。

    改前(线上 stress_165 P1~P3): 失配即 `_compute()` 同步全量(3 次取数 ~2s, 并发下
    7.9→25.5s) + 起裸 daemon 线程 build 绕过 heavy 预算 → 连接池耗尽(上限 10, 等 20s),
    违反红线 4/6。`OWN_SWR=0` 一刀回退。
    """
    if not read_enabled() or legacy or not _swr_on():
        return compute(), None
    p = page_of(channel, full=full, limit=limit, offset=offset,
                per_type=per_type, only=only)
    if p is not None:
        _STAT["hit"] += 1
        return p, None
    h = head(channel)
    prev = page_of(channel, full=full, limit=limit, offset=offset,
                   per_type=per_type, only=only, allow_stale=True)
    if prev is not None:
        state = submit_rebuild(channel)
        _STAT["swr_served"] += 1
        return prev, swr_meta(channel, h or {}, state)
    # 真·从未构建: 复用 list_store.first_shared 首建单飞 —— leader 一次 build
    # (算+落物化), follower 有界等后读回同一份; 绝不 0 条、绝不 N 并发各算全量。
    try:
        from core import list_store as _LS

        def _leader():
            _STAT["first_legacy"] += 1
            try:
                return _LS.first_guard("own", lambda: build(channel, force=True))
            except _LS.FirstBusy:
                # §15.169: 名额忙 → 后台补建, 前台立刻 building(不排队等)
                ensure_async(channel)
                raise

        def _readback(shared):
            # §15.169: 只读一次。读不到 = 名额忙/失败 → None, 由路由转 building。
            #   绝不在这里再算一遍全量(并发下会 30 份同时算, 拖垮连接池)。
            p = page_of(channel, full=full, limit=limit, offset=offset,
                        per_type=per_type, only=only, allow_stale=True)
            if isinstance(p, dict) and p:
                return p
            # leader 算完但读不到(极端写失败): 单份现算兜底; follower 到不了这里
            return compute()

        if _LS._first_sf_on():
            try:
                return _LS.first_shared("own_first|" + str(channel), _LS._FIRST_TTL,
                                        _LS._FIRST_WAIT, _leader, _readback), None
            except _LS.FirstBusy:
                # §15.169: 首建中 → 明确 building(形状与正常一致), 前端按 retry_after 重试
                return ({"items": [], "total": -1, "building": True}, None)
    except Exception as _e:
        # FirstBusy 是「首次构建未完成」的明确失败, 必须穿透到路由(ok:false + 可重试);
        # 若在这里被吞掉再 compute() 兜底, 30 个并发又会各自全量算一遍(§15.168 雪崩)。
        try:
            from core import list_store as _LS3
            if isinstance(_e, _LS3.FirstBusy):
                raise
        except ImportError:
            pass
        try_err("own_store", "首建单飞降级(%s)" % channel, _e)
    d = compute()
    return d, None


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


def page_of(channel, full=0, limit=10, offset=0, per_type=0, only="",
            allow_stale=False):
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
    if not h or not int(h.get("gen") or 0):
        return None
    # §15.165: 冷态(allow_stale)放行「历史 stale=1 但 gen>0 且 stat 可解析」的完整旧
    # 快照; 稳态仍严格硬拒(红线 8②)。`OWN_STALE_PREV=0` 回退。与 list_store 同构。
    if int(h.get("stale") or 0) and not (allow_stale and _stale_prev_on()):
        return None
    if not allow_stale:
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
    # 2026-11-07 §15.158: LIMIT/OFFSET 一律 str() 显式拼接, SQL 里不许残留 `%d`。
    #   db._guard_fmt 会把所有参数转成字符串再模拟 `sql % args`, 残留的 `%d` 必抛
    #   "a real number is required, not str" → 线上 per_type 明细一直静默降级回现算。
    #   (另两条 `% (_lim,_off)` 因 % 优先级高于 + 只格式化了尾段, 结果虽对但太脆, 一并收紧)
    try:
        if per_type:
            for _t in _TYPES:
                if only and _t != only:
                    continue
                rows.extend(query(
                    "SELECT " + _COLS + " FROM own_health_snapshot WHERE " + _where +
                    " AND alert_type=%s ORDER BY seq LIMIT " + str(int(per_type)),
                    _p + [_t]) or [])
        elif full:
            rows = query("SELECT " + _COLS + " FROM own_health_snapshot WHERE " + _where +
                         " ORDER BY seq LIMIT " + str(_lim) + " OFFSET " + str(_off), _p) or []
        else:
            rows = query("SELECT " + _COLS + " FROM own_health_snapshot WHERE " + _where +
                         " ORDER BY seq LIMIT " + str(_lim), _p) or []
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
    """§15.164: 记失败原因, 但**不把完整旧 gen 标成不可用**(与 list_store 同源修订)。

    线上 09:03:01 自有仓重建撞 `own_health_snapshot.PRIMARY` 1062, 旧 gen(14309 行,
    发布前计数自校验通过过) 完整却被置 stale=1 → 读侧退回现算 3.5~4.4s。
    默认 stale=0 让读侧继续用上一版完整物化 + 新鲜度播报; `OWN_SAFE_REBUILD=0` 回退。
    """
    _v = "0" if _safe_rebuild() else "1"
    try:
        execute("INSERT INTO own_health_head(channel,stale,note,built_at) "
                "VALUES(%s,%s,%s,NOW(6)) ON DUPLICATE KEY UPDATE stale=VALUES(stale), "
                "note=VALUES(note), built_at=VALUES(built_at)",
                [channel, _v, str(note)[:250]])
    except Exception as _e:
        try_err("own_store", "标脏失败(%s)" % channel, _e)


def _stale_recent(channel):
    """stale 且最近 _STALE_BACKOFF 秒内失败过 → 退避（时间比较放在 SQL 里，避免时区解释差异）

    红线 14: INTERVAL 一律 str() 显式拼接。原实现把整数留在参数位、SQL 里写格式化
    占位符, db._guard_fmt 会把参数转 str 再模拟格式化 → 类型错误被这里的 except 吞掉
    → **退避永远返回 False**(失败后每个请求都重算)。§15.168 修。
    """
    try:
        _sql = ("SELECT COUNT(*) AS n FROM own_health_head WHERE channel=%s AND stale=1 "
                "AND built_at > DATE_SUB(NOW(6), INTERVAL " + str(int(_STALE_BACKOFF)) + " SECOND)")
        r = one(_sql, [channel])
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
    _safe = _safe_rebuild()
    try:
        if _safe:
            # §15.164: 清掉目标 gen 中超出本轮行数的半批残留, 避免下次写同 gen 1062,
            # 也避免残留行把发布前计数自校验算多(红线 8② 完整性)。
            execute("DELETE FROM own_health_snapshot WHERE channel=%s AND gen=%s AND seq>=%s",
                    [channel, gen, len(items)])
        _upsert = (" ON DUPLICATE KEY UPDATE alert_type=VALUES(alert_type), level=VALUES(level), "
                   "sku=VALUES(sku), barcode=VALUES(barcode), warehouse=VALUES(warehouse), "
                   "product_name=VALUES(product_name), avail=VALUES(avail), "
                   "need_qty=VALUES(need_qty), gap_qty=VALUES(gap_qty), "
                   "cover_days=VALUES(cover_days), days_to_expiry=VALUES(days_to_expiry), "
                   "batch_no=VALUES(batch_no), exp_date=VALUES(exp_date), "
                   "title=VALUES(title), description=VALUES(description)") if _safe else ""
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
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)" + _upsert,
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
    """读未命中/写后失效的后台重建 —— 不在响应路径上等它。

    §15.165: 默认收编进 `list_store.submit_background`(db.heavy 全局预算 + 去 fp 单飞),
    根治线上 stress_165 P1~P3 里「每次 invalidate 起一个裸 daemon 线程直接 build,
    绕过 3 个 heavy 名额 → 任务风暴下 6+ 类重建同时抢 10 个连接 → 连接池耗尽
    (上限 10, 等待 20s 无归还)」。不新建队列/worker/租约(红线 4/5), 只复用既有闸门。
    `OWN_BUDGET=0` 一刀回退到旧裸线程行为(逐字保留, 便于对照验证)。
    """
    _chs = [channel] if channel else _channels()
    if _budget_on() and not force:
        try:
            for _c in _chs:
                submit_rebuild(_c)
            return True
        except Exception as _e:
            try_err("own_store", "分舱重建调度降级(%s)" % channel, _e)
            return False

    def _run(_chs):
        for _c in _chs:
            try:
                build(_c, force=force)
            except Exception as _e2:
                try_err("own_store", "重建失败(%s)" % _c, _e2)

    try:
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
