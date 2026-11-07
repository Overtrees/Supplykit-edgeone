"""core/list_store.py —— 补货/采购建议行「域指纹物化」读模型（2026-11-07 P0）

## 一句话
把 builder() 的全量建议行算一次、按最终排序落 seq 存下来；请求路径只剩按 seq 读一页。

## 为什么需要（线上 2026-11-07 16:41 基线, /tmp/sk_baseline_before.json）
    repl      热 min 0.666s, 但第一次 8.815s
    purchase  热 min 0.764s, 但第一次 5.015s
    disposal  2.503s（一直慢, 属第 2 轮）
根因不是算法慢, 是「读时全量现算 + TTL 300s 表缓存」:
  · TTL 到点（**没有任何写操作**）→ 下一个请求在请求路径里同步跑 builder();
  · invalidate() 给整族打墓碑 → 任何相关域写操作也让下一个请求重算。
即 §15.152 明令否决的「时间参与数据新不新的判断」（红线: 不加缓存 TTL）。

## 设计（与 unit_store / own_store 同一套骨架, 不另造轮子）
- **指纹驱动, 不是 TTL**: fp = data_version_dom 各域计数（对齐 _FAMILY_DEPS 对应族）,
  写操作 bump → 下一次请求 fp 不等 → 不读物化、退回现算, 并起后台重建。
  读路径**永不返回可能过期的行**: fp 不等就 None, 调用方走今天的老路（慢但正确）。
- **排序落序号**: 行顺序由 builder() 的 sort 唯一决定, 重建时写成 seq;
  读侧 ORDER BY seq 切页 —— SQL 不参与排序（collation 与 Python 码位序不同, 两套排序必错位）。
- **一步切换**: 新 gen 全部写完并核对「总行数 / need 数」才 UPDATE head.gen;
  中途失败 → head 仍指旧 gen → 读侧继续用旧版本（正确, 只是不是最新）。
- **可回滚**: `LIST_READ=0` 一刀回现算, 不必重新部署。
- **算法不复制**: build() 只调 builder()（红线 9）, 不抄公式。

## 与 list_page.shape_cached（旧路径）的关系
旧路径（页级缓存 → 全量分块 → builder）**已整体删除**, 不是并存兜底（红线 2）。
现算降级复用 `list_page.shape()`（同一份筛选/切页纯函数）, 保证降级与读回同源同序。
search 用 SQL `INSTR` 而不是 `LIKE`: LIKE 的 `%`/`_` 会被当通配符, Python `in` 不会;
`INSTR(LOWER(col), sq)>0` 与 `sq in str(row.get(k,'')).lower()` 完全同构。
"""
import hashlib
import json
import os
import threading
import time

from db import query, one, execute, executemany
from routes.common import try_err

# 依赖域 = analysis_cache._FAMILY_DEPS 里对应族那一行（一处真源在那里）。
#   这里显式写出是为了 fp 的可读性; local_test 断言两者逐项相等, 防两处漂移。
DEPS = {
    "repl": ("orders", "inventory", "products", "config"),
    "purchase": ("orders", "inventory", "products", "config", "rules", "purchases"),
    # disposal: 还读 alerts(pseudo 线索, rules 域) / purchase_orders + disposal_records
    #   (b_arrival / disposed, purchases 域); 日销物化(orders) / 库存 / 商品 / 配置同源。
    "disposal": ("orders", "inventory", "products", "config", "rules", "purchases"),
}
NEED_KEYS = {
    "repl": ("suggested_qty", "b_suggested"),
    "purchase": ("actual_purchase",),
    "disposal": (),          # 滞销无 need_only 概念; need 列恒 0, 读侧不按它筛
}
OUT_KEY = {"repl": "items", "purchase": "suggestions", "disposal": "items"}
# 无分页裸调的最大返回行数: repl 旧契约截断 2000; purchase/disposal 旧契约返回全量
UNPAGED_CAP = {"repl": 2000, "purchase": None, "disposal": None}

# 业务日期是**确定性输入域**(不是缓存 TTL): 滞销的 days_zero / B仓在库天数 / 临期天数 /
#   30天已处置 / 14天补货线索全部读 now_cn()。跨天不改它们 = 用户看到昨天的"未销售天数"
#   和过时的 level → 数据四维-准确性受损。把日期纳入 fp, 跨天首请求 fp 不等 → 退回现算
#   + 后台重建(与 §15.152「时间不参与数据新不新」不矛盾: 那里指"数据没变不该因时间流逝
#   重算", 这里指"日期变了计算结果本身就变", 属输入变化)。repl/purchase 公式不读日期, 不加。
DATE_AWARE = ("disposal",)

_CHUNK = 200          # 明细批量插入行数（每行 9 列, 含 MEDIUMTEXT payload）
_COOLDOWN = 30.0      # 同一 (kind,scope) 两次重建最小间隔 —— 一次导入会连调多次 invalidate
_STALE_BACKOFF = 300.0  # 校验失败后的退避, 防每个读请求都再烧一次 8s 重建
_LOCK = threading.Lock()
_LAST = {}
_READ_ON = ("1", "true", "yes", "on")


def read_enabled():
    """读路径总开关（环境变量）。未设置 = 开; "0" = 一律现算（紧急回滚, 不必发版）"""
    _v = os.environ.get("LIST_READ")
    if _v is None or str(_v).strip() == "":
        return True
    return str(_v).strip().lower() in _READ_ON


# ── 指纹 ────────────────────────────────────────────────────────
def fp_now(kind, channel, mode, extra=""):
    """当前数据版本指纹（空串 = 不可用 → 一律走现算）。

    复用 unit_store.fp_of 的同一套域计数器（不另造指纹语义）; 再把 scope 纳入,
    使 days/source 这类不进域计数器、却会改变计算入参的维度也能感知。
    """
    try:
        from core import unit_store as _US
        base = _US.fp_of(DEPS[kind], channel, mode)
    except Exception as _e:
        try_err("list_store", "指纹计算降级", _e)
        return ""
    if not base:
        return ""
    raw = "%s|%s" % (base, extra)
    if kind in DATE_AWARE:
        # 日期是输入, 不是保鲜期: 跨天必须重算, 否则滞销天数/B仓在库/临期 level 全是昨天的。
        try:
            from core.clock import today_cn
            raw += "|d=" + today_cn()
        except Exception as _e:
            try_err("list_store", "业务日期计算降级(退回现算)", _e)
            return ""
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:32]


# ── head ────────────────────────────────────────────────────────
def head(kind, scope):
    try:
        return one("SELECT * FROM list_head WHERE kind=%s AND scope=%s", [kind, scope]) or None
    except Exception as _e:
        try_err("list_store", "head 读取降级", _e)
        return None


def register_scope(kind, scope, channel, mode, days, source):
    """登记 scope 的 builder 参数 —— 让 invalidate() 能在**写侧**主动后台重建。

    INSERT IGNORE: 已登记（含真实物化结果）不覆盖, 不会把 gen/fp 打回 0。
    只在新 scope 首次访问时调一次（serve 里判过 head 为空才调）, 常态零写入。
    """
    try:
        execute("INSERT IGNORE INTO list_head(kind,scope,channel,mode,days,source,"
                "gen,fp,row_cnt,need_cnt,stale,note) "
                "VALUES(%s,%s,%s,%s,%s,%s,0,'',-1,0,0,'registered')",
                [kind, scope, str(channel)[:16], str(mode)[:16],
                 int(days or 0), str(source or "")[:32]])
    except Exception as _e:
        try_err("list_store", "scope 登记降级", _e)


def _mark_stale(kind, scope, note):
    try:
        execute("INSERT INTO list_head(kind,scope,stale,note,built_at) "
                "VALUES(%s,%s,1,%s,NOW(6)) "
                "ON DUPLICATE KEY UPDATE stale=1, note=VALUES(note), built_at=VALUES(built_at)",
                [kind, scope, str(note)[:250]])
    except Exception as _e:
        try_err("list_store", "标脏失败(%s/%s)" % (kind, scope), _e)


def _stale_recent(kind, scope):
    """stale 且最近 _STALE_BACKOFF 秒内失败过 → 退避。
    INTERVAL 一律 str() 显式拼接（红线 14: 不许留 %d 让 _guard_fmt 转 str）。"""
    try:
        _sql = ("SELECT COUNT(*) AS n FROM list_head WHERE kind=%s AND scope=%s AND stale=1 "
                "AND built_at > DATE_SUB(NOW(6), INTERVAL " + str(int(_STALE_BACKOFF)) + " SECOND)")
        r = one(_sql, [kind, scope])
        return int((r or {}).get("n") or 0) > 0
    except Exception:
        return False


# ── 读 ──────────────────────────────────────────────────────────
def _where(kind, scope, gen, need_only, search):
    """读侧 WHERE —— 与 list_page.filter_rows 逐字等价（同一份筛选语义）。

    物化时 sku/product_name/barcode 三列用 `str(row.get(k,''))` 落库（见 _row_vals）,
    与 filter_rows 的 `str(r.get(k,''))` 同源; LOWER() 对齐 `.lower()`。
    """
    w = "kind=%s AND scope=%s AND gen=%s"
    p = [kind, scope, gen]
    if need_only:
        w += " AND need=1"
    if search:
        sq = str(search).lower()
        w += (" AND (INSTR(LOWER(sku),%s)>0 OR INSTR(LOWER(product_name),%s)>0 "
              "OR INSTR(LOWER(barcode),%s)>0)")
        p += [sq, sq, sq]
    return w, p


def page_of(kind, scope, channel, mode, need_only=0, search="", page=0, page_size=0,
            extra="", h=None):
    """读一页; `None` = 不可用（调用方退回现算 + 起后台重建）。

    total 与 items 同源: 无筛选直接读 head.row_cnt（物化计数, 红线 11）;
    带 need_only/search 时用**同一条 WHERE** 的 COUNT, 不会出现"标题 N 条、翻到底 N-2"。
    """
    if not read_enabled():
        return None
    h = h if h is not None else head(kind, scope)
    if not h or not int(h.get("gen") or 0) or int(h.get("stale") or 0):
        return None
    if int(h.get("row_cnt") or -1) < 0:      # 哨兵: 本轮没算成 ≠ 真的是 0 条（红线 8①）
        return None
    _fp = fp_now(kind, channel, mode, extra)
    if not _fp or str(h.get("fp") or "") != _fp:
        return None
    gen = int(h["gen"])
    w, p = _where(kind, scope, gen, need_only, search)
    try:
        if not need_only and not search:
            total = int(h.get("row_cnt") or 0)
        else:
            total = int((one("SELECT COUNT(*) AS n FROM list_snapshot WHERE " + w, p)
                         or {}).get("n") or 0)
        if page and page_size and page > 0 and page_size > 0:
            _lim = " LIMIT " + str(int(page_size)) + " OFFSET " + str(int((page - 1) * page_size))
        else:
            _cap = UNPAGED_CAP.get(kind)
            _lim = " LIMIT " + str(_cap) if _cap else ""
        rows = query("SELECT payload FROM list_snapshot WHERE " + w + " ORDER BY seq" + _lim,
                     p) or []
        items = []
        for r in rows:
            try:
                items.append(json.loads(r.get("payload") or "{}"))
            except Exception:
                return None       # 坏行 → 整页退回现算, 不返回半份数据
        return {OUT_KEY[kind]: items, "total": total, "page": page, "page_size": page_size}
    except Exception as _e:
        try_err("list_store", "明细读取降级", _e)
        return None


# ── 写 ──────────────────────────────────────────────────────────
def _row_vals(row, need_keys):
    """一行 → (need, sku, product_name, barcode, payload)。

    need 复用 list_page._num（红线 9: 不复制筛选算法）; 三个检索列用与
    filter_rows **完全相同的表达式** `str(row.get(k,''))`, 避免大小写/None 漂移。
    """
    from core.list_page import _num
    return (
        1 if any(_num(row, k) > 0 for k in need_keys) else 0,
        str(row.get("sku", ""))[:80],
        str(row.get("product_name", ""))[:255],
        str(row.get("barcode", ""))[:40],
        json.dumps(row, ensure_ascii=False, default=str),
    )


def _builder_of(kind, h):
    """按 kind 取现算入口（唯一公式所在）。函数内延迟导入, 沿用 own_store 的
    core→routes 先例（避免启动期循环）; build() 只调它, 绝不复制算法。"""
    channel = str(h.get("channel") or "jd")
    mode = str(h.get("mode") or "bbcc")
    days = int(h.get("days") or 28)
    if kind == "repl":
        from routes.replenishment import _build_repl
        return lambda: _build_repl(channel, mode)
    if kind == "disposal":
        from routes.purchase_calc import _disposal_compute
        return lambda: _disposal_compute(channel, 0)[0]
    from routes.purchase import _build_purchase
    return lambda: _build_purchase(channel, mode, days)


def build(kind, scope, force=False):
    """算一次 → 写新 gen → 核对计数 → 切 head。幂等: 同一 fp 重复执行结果相同。"""
    h = head(kind, scope)
    if not h:
        return {"ok": False, "error": "scope_unregistered"}
    channel = str(h.get("channel") or "jd")
    # mode 必须与 page_of / register 登记的**原始值同源**: disposal 传空串, 若这里兜底成
    # "bbcc", 写侧 fp(mode_of("bbcc")="bbcc") 与读侧 fp(mode_of("")=渠道默认) 不等 →
    # page_of 永远判 fp 不命中 → 物化白做、每请求仍 2.5s 现算。空串交给 mode_of 自己查
    # 渠道默认, 读写两侧查同一份配置, 必然一致。(§15.160 抓到的读写 fp 不对称)
    mode = str(h.get("mode") or "")
    fp = fp_now(kind, channel, mode, scope)
    if not fp:
        return {"ok": False, "error": "fp_unavailable"}
    if (not force and str(h.get("fp") or "") == fp
            and not int(h.get("stale") or 0) and int(h.get("row_cnt") or -1) >= 0):
        return {"ok": True, "skipped": "fresh", "gen": int(h.get("gen") or 0)}
    if not force and int(h.get("stale") or 0) and _stale_recent(kind, scope):
        return {"ok": False, "skipped": "backoff"}
    _now = time.time()
    _key = (kind, scope)
    with _LOCK:
        _last = _LAST.get(_key)
        if not force and _last and _last[1] == fp and _now - _last[0] < _COOLDOWN:
            return {"ok": True, "skipped": "cooldown"}
        _LAST[_key] = (_now, fp)

    _t0 = time.time()
    try:
        rows = _builder_of(kind, h)()
    except Exception as _e:
        _mark_stale(kind, scope, "计算异常: %s" % _e)
        try_err("list_store", "重建计算失败(%s/%s)" % (kind, scope), _e)
        return {"ok": False, "error": "compute"}
    if not isinstance(rows, list):
        rows = []
    _t_calc = time.time() - _t0

    gen = int(h.get("gen") or 0) + 1
    need_keys = NEED_KEYS[kind]
    _t_w0 = time.time()
    need_cnt = 0
    try:
        payload = []
        for i, row in enumerate(rows):
            need, sku, pn, bc, js = _row_vals(row, need_keys)
            need_cnt += need
            payload.append([kind, scope, gen, i, need, sku, pn, bc, js])
        for i in range(0, len(payload), _CHUNK):
            executemany(
                "INSERT INTO list_snapshot(kind,scope,gen,seq,need,sku,product_name,barcode,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", payload[i:i + _CHUNK])
    except Exception as _e:
        _mark_stale(kind, scope, "写入异常: %s" % _e)
        try_err("list_store", "重建写入失败(%s/%s)" % (kind, scope), _e)
        return {"ok": False, "error": "write"}
    _t_write = time.time() - _t_w0

    # 发布前自校验（红线 8② / §15.152 完整性）: 行数与 need 数必须与计算产出一致。
    # 写一半 / 写重复 / 序列化截断都在这里暴露, 不过就**不切 head**, 旧 gen 继续服务。
    errs = []
    try:
        c = one("SELECT COUNT(*) AS n, SUM(need) AS nd FROM list_snapshot "
                "WHERE kind=%s AND scope=%s AND gen=%s", [kind, scope, gen]) or {}
        if int(c.get("n") or 0) != len(rows):
            errs.append("总行数 库=%s 算=%d" % (c.get("n"), len(rows)))
        if int(c.get("nd") or 0) != need_cnt:
            errs.append("need 数 库=%s 算=%d" % (c.get("nd"), need_cnt))
    except Exception as _e:
        errs.append("核对失败: %s" % _e)
    if errs:
        _mark_stale(kind, scope, "校验未通过: " + "; ".join(errs))
        try_err("list_store", "重建校验未通过(%s/%s), 已保留旧版本" % (kind, scope),
                Exception("; ".join(errs)[:300]))
        return {"ok": False, "error": "validate", "detail": errs[:5]}

    timing = "calc=%.1f,write=%.1f,total=%.1f,rows=%d" % (
        _t_calc, _t_write, time.time() - _t0, len(rows))
    try:
        # 只更新 gen/fp/计数/timing/stale/note —— 不覆盖登记时的 channel/mode/days/source。
        execute("INSERT INTO list_head(kind,scope,channel,mode,days,source,gen,fp,row_cnt,"
                "need_cnt,timing,stale,note,built_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,'ok',NOW(6)) "
                "ON DUPLICATE KEY UPDATE gen=VALUES(gen), fp=VALUES(fp), "
                "row_cnt=VALUES(row_cnt), need_cnt=VALUES(need_cnt), timing=VALUES(timing), "
                "stale=0, note='ok', built_at=VALUES(built_at)",
                [kind, scope, channel, mode, int(h.get("days") or 0),
                 str(h.get("source") or ""), gen, fp, len(rows), need_cnt, timing])
        execute("DELETE FROM list_snapshot WHERE kind=%s AND scope=%s AND gen<%s",
                [kind, scope, gen - 1])
    except Exception as _e:
        _mark_stale(kind, scope, "发布异常: %s" % _e)
        try_err("list_store", "重建发布失败(%s/%s)" % (kind, scope), _e)
        return {"ok": False, "error": "publish"}
    return {"ok": True, "gen": gen, "rows": len(rows), "timing": timing,
            "elapsed": round(time.time() - _t0, 2)}


# ── 触发 ────────────────────────────────────────────────────────
def ensure_async(kind, scope=None, force=False):
    """写侧/读侧后台重建 —— 不在响应路径上等它。

    · 传 scope: 重建这一个（读侧 fp 不命中时）。
    · 不传: 遍历 list_head 里该 kind 已登记的 scope（写侧 invalidate 时）。
    重复执行无害（幂等 + cooldown）; 实例被冻结也无害（head 未切换 = 旧版本继续生效,
    读侧走现算兜底）。
    """
    def _run(scopes):
        for sc in scopes:
            try:
                build(kind, sc, force=force)
            except Exception as _e2:
                try_err("list_store", "重建失败(%s/%s)" % (kind, sc), _e2)

    try:
        if scope:
            scopes = [scope]
        else:
            scopes = [str(r.get("scope")) for r in
                      (query("SELECT scope FROM list_head WHERE kind=%s", [kind]) or [])
                      if r.get("scope")]
        if not scopes:
            return False
        threading.Thread(target=_run, args=(scopes,), daemon=True).start()
        return True
    except Exception as _e:
        try_err("list_store", "重建线程启动降级", _e)
        return False


def serve(kind, scope, channel, mode, days, source, builder,
          need_only=0, search="", page=0, page_size=0, legacy=0):
    """路由唯一入口: 读物化 or 现算降级（两者共用 list_page.shape, 同源同序）。

    `legacy=1` 强制现算 —— 线上与物化逐字段对账用（对齐 §15.154 的 legacy 先例）。
    """
    h = head(kind, scope)
    if h is None:
        register_scope(kind, scope, channel, mode, days, source)
    d = None if legacy else page_of(kind, scope, channel, mode, need_only, search,
                                    page, page_size, extra=scope, h=h)
    if d is None:
        if not legacy:
            ensure_async(kind, scope)
        from core.list_page import shape as _shape
        items, total = _shape(builder(), need_only=need_only, search=search,
                              page=page, page_size=page_size, need_keys=NEED_KEYS[kind])
        return {OUT_KEY[kind]: items, "total": total, "page": page, "page_size": page_size}
    return d


def status(kind, scope):
    """诊断口: 四维逐项可查, 不靠猜。"""
    h = head(kind, scope) or {}
    ch, md = str(h.get("channel") or "jd"), str(h.get("mode") or "bbcc")
    _fp = fp_now(kind, ch, md, scope)
    _n = 0
    if int(h.get("gen") or 0):
        try:
            _n = int((one("SELECT COUNT(*) AS n FROM list_snapshot WHERE kind=%s AND scope=%s "
                          "AND gen=%s", [kind, scope, int(h.get("gen"))]) or {}).get("n") or 0)
        except Exception:
            _n = -1
    return {
        "read_enabled": read_enabled(), "kind": kind, "scope": scope,
        "gen": int(h.get("gen") or 0),
        "fp_head": str(h.get("fp") or ""), "fp_now": _fp,
        "fp_match": bool(_fp) and str(h.get("fp") or "") == _fp,
        "row_cnt": int(h.get("row_cnt") or -1), "rows_actual": _n,
        "need_cnt": int(h.get("need_cnt") or 0), "timing": str(h.get("timing") or ""),
        "stale": int(h.get("stale") or 0), "note": str(h.get("note") or ""),
        "built_at": str(h.get("built_at") or ""),
    }
