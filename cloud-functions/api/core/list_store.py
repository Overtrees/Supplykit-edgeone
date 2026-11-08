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

_CHUNK = 2000         # 明细批量插入行数（每行 9 列, 含 MEDIUMTEXT payload）
# §15.174: 原 200。线上 list_head.timing(2026-11-08 14:05) 三个 kind 的 write 已超过 calc:
#   repl 2.4s / purchase 1.9s / disposal 2.7s, 而 1 万行按 200 分 = 50 批 = 50 次往返。
#   单批体积 = 行数 × payload 实测均值 853B(max 1128B) → 1000 行 ≈ 1.1MB,
#   远低于 max_allowed_packet 64MB(线上已核); TiDB 解析 1MB 语句的成本低于一次跨区往返。
#   批次数 50 → 10。**只改批量大小, 不改语义、不加闸门**(红线 4)。
# §15.176: 1000 → 2000。线上 list_head.timing(2026-11-08 14:58) 写仍占重建 1/3:
#   repl write=1.5s/10000 行、purchase write=1.4s/9625 行、disposal write=2.1s/15089 行。
#   2000 行 × 853B ≈ 1.7MB/批(payload 最大 1128B → 2.2MB), 仍远低于 64MB packet;
#   autocommit=1, 每批独立语句, 不受单事务大小限制。批次数再减半(10→5 / 8→4 / 16→8)。
_COOLDOWN = 30.0      # 同一 (kind,scope) 两次重建最小间隔 —— 一次导入会连调多次 invalidate
# §15.164: 原来 300s。线上 09:05 现场: 写新 gen 撞主键 1062 后 head 被标 stale=1,
# 每次读请求又刷新 built_at → 退避被无限续期, repl 连续 17min 每请求全量现算 12.8s/503。
# 失败不再置 stale(见 _mark_stale), 退避只作防御性兜底, 有界 20s 即可(红线 15 不靠盲重试)。
_STALE_BACKOFF = 20.0
_LOCK = threading.Lock()
_LAST = {}
_READ_ON = ("1", "true", "yes", "on")


def _safe_rebuild():
    """§15.164 总开关: 未设置=开; `LIST_SAFE_REBUILD=0` 一刀回退到修复前行为
    (幂等写 / 失败保旧 gen 同时关掉), 不必发版。红线 2: 不新增机制, 只是同一路径的开关。"""
    _v = os.environ.get("LIST_SAFE_REBUILD")
    if _v is None or str(_v).strip() == "":
        return True
    return str(_v).strip().lower() in _READ_ON

# ── §15.161 P1 冷算治理: 全局预算 + 同 key 单飞 + 读侧零等待 ────────────────
# 线上实测 2026-11-07 19:12~19:22(本报告改前基线, 全部真实链路):
#   纯现算单跑  repl 5.99~7.79s / purchase 3.01~3.88s / disposal 2.67~3.24s
#   同类并发 2 → 2.2~2.5×; 并发 4 → 4~6×, repl 4/4 HTTP 503(等 33s 零数据)
#   稳态物化并发 4 仅 1.35s → 退化根因是同实例内 Python 全量循环抢 GIL, 不是连接池/网关。
# 治理**不是**「把请求绑成一个全局单 worker」—— 那会把 3 个本可并行的 kind 串成
#   15.54s(实测 7.04+4.13+4.37), 再叠 29s 前端轮询, 比等重建 14s 更糟(已否决)。
# 正解(按 kind 分舱, bulkhead):
#   ① 全局名额复用 db.heavy(HEAVY_CONCURRENCY=3), **不另造第二套闸门**(红线 2 禁止叠加);
#   ② 同 (kind,scope,fp) 只算一次(单飞去重), 不同 kind 各自并行, 互不排队;
#   ③ 读侧**不等待**后台重建, 立即返回上一版完整物化 + 新鲜度播报(SWR, 见 serve)。
# 实例可被平台冻结: flight 带 t0 新鲜度, 超 _FLIGHT_TTL 判僵尸丢弃(红线 15)。
_FLIGHT_TTL = 60.0
_HEAVY_WAIT = 2.0     # 抢全局名额的最长等待; 抢不到 = 快速失败, 绝不排长队
_BUSY_COOL = 2.0      # §15.165 HeavyBusy 后同 key 的有界冷却, 防写风暴下每个读都重抢名额
_FIRST_WAIT = 20.0    # §15.165 首建单飞: 同组并发等 leader 落物化的上限(覆盖最慢 repl build 16.5s)
_FIRST_TTL = 60.0     # 首建 flight 僵尸判定(红线 15)
_FLIGHT_LOCK = threading.Lock()
_FLIGHTS = {}         # key -> {"t0": float, "kind": str}
_BUSY_UNTIL = {}      # key -> float, HeavyBusy 冷却截止(有界, 不排队)
_FIRSTS = {}          # key -> {"t0": float, "event": threading.Event}, 首建单飞(同实例共享一次计算)
_STATS = {"started": 0, "dedup": 0, "done": 0, "failed": 0, "busy": 0,
          "swr_served": 0, "first_legacy": 0, "first_shared": 0,
          "first_timeout": 0, "stale_prev_served": 0, "busy_cooled": 0}


def _flag(name, default=True):
    """§15.165 总开关族: 未设置=开; 置 "0" 一刀回退到本轮修复前行为(不必发版)。
    红线 2: 不新增机制, 只是同一路径上的开关; 互斥不并存。"""
    _v = os.environ.get(name)
    if _v is None or str(_v).strip() == "":
        return default
    return str(_v).strip().lower() in _READ_ON


def _coalesce_on():
    return _flag("FLIGHT_COALESCE")


def _stale_prev_on():
    return _flag("LIST_STALE_PREV")


def _first_sf_on():
    return _flag("FIRST_SINGLEFLIGHT")


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
    """§15.164: 记录重建失败原因, 但**不再把完整旧 gen 标成不可用**。

    线上现场(09:05:23, 90min 日志 3 条 1062): 新 gen 写一半撞主键失败, 旧 gen
    (gen=5 / row_cnt=10000 / 发布前 COUNT 自校验通过过) 其实完整可读, 却被这里置
    stale=1 → page_of(含 allow_stale=True) 第 209 行直接 return None → serve() 判
    `prev is None` 走 first_legacy 同步全量现算(12.8s / HTTP 503), 违反红线 6。

    因此默认路径改为 stale=0 + note 记因: 读侧继续读上一版完整物化, 并由 __swr
    显式播报 freshness_s / fp_changed(红线 8② 的 gen/stale/row_cnt 哨兵一字未删,
    只是不再被误置; 绝不把旧值静默伪装成新值)。`LIST_SAFE_REBUILD=0` 回退旧行为。
    """
    _safe = _safe_rebuild()
    _v = "0" if _safe else "1"
    try:
        execute("INSERT INTO list_head(kind,scope,stale,note,built_at) "
                "VALUES(%s,%s,%s,%s,NOW(6)) "
                "ON DUPLICATE KEY UPDATE stale=VALUES(stale), note=VALUES(note), "
                "built_at=VALUES(built_at)",
                [kind, scope, _v, str(note)[:250]])
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
            extra="", h=None, allow_stale=False):
    """读一页; `None` = 不可用（调用方退回现算 + 起后台重建）。

    total 与 items 同源: 无筛选直接读 head.row_cnt（物化计数, 红线 11）;
    带 need_only/search 时用**同一条 WHERE** 的 COUNT, 不会出现"标题 N 条、翻到底 N-2"。

    `allow_stale=True`(§15.161 SWR): 只跳过 fp 比对, **其余完整性校验一字不减** ——
    gen>0 / stale=0 / row_cnt 哨兵 / 坏行整页退回。当前 gen 是上一次**发布前 COUNT
    自校验通过**的完整快照, 不是半截数据; 调用方必须带上 freshness_s 与 fp_changed
    明确播报, 绝不静默伪装成新值(§15.8 准确/完整/可靠)。
    """
    if not read_enabled():
        return None
    h = h if h is not None else head(kind, scope)
    if not h or not int(h.get("gen") or 0):
        return None
    # §15.165: 历史/并发遗留的 stale=1, 只要 gen>0 且完整旧快照可读, 冷态(allow_stale)
    # 下仍可作为「上一版」返回 + __swr 显式播报 stale_prev。§15.164 只修了「不再误置
    # stale」, 没修「已 stale 的完整旧 gen 仍被 page_of 硬拒 → serve 退回首建双算 →
    # P4 压测 4 并发 16.9s / 2×503 / 4/4 零数据(停摆)」。稳态(allow_stale=False)
    # 仍严格硬拒 stale=1, 红线 8② 哨兵一字不松。`LIST_STALE_PREV=0` 回退。
    if int(h.get("stale") or 0) and not (allow_stale and _stale_prev_on()):
        return None
    if int(h.get("row_cnt") or -1) < 0:      # 哨兵: 本轮没算成 ≠ 真的是 0 条（红线 8①）
        return None
    if not allow_stale:
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
    # §15.172 只读分段埋点(红线 7): calc 到底贵在 SQL 往返还是 Python 循环 —— 决定
    #   下一步是加索引/减往返, 还是向量化/下推。只写 list_head.timing, 不改执行路径。
    #   同实例并发 build 时 STATS 会互串, 因此**只用于判断量级**(谁是大头), 不当精确值;
    #   精确值只能靠线上 e2e 与 perf_debug(红线 7 禁止用本地计时下结论)。
    try:
        from db import stats_reset as _sr
        _sr()
    except Exception:
        _sr = None
    try:
        rows = _builder_of(kind, h)()
    except Exception as _e:
        _mark_stale(kind, scope, "计算异常: %s" % _e)
        try_err("list_store", "重建计算失败(%s/%s)" % (kind, scope), _e)
        return {"ok": False, "error": "compute"}
    try:
        from db import stats_snapshot as _ss
        _q = _ss()
        _SQL_S = round(float(_q.get("stmt_s") or 0), 1)
        _SQL_N = int(_q.get("stmt_n") or 0)
    except Exception:
        _SQL_S, _SQL_N = -1.0, -1
    if not isinstance(rows, list):
        rows = []
    _t_calc = time.time() - _t0

    gen = int(h.get("gen") or 0) + 1
    need_keys = NEED_KEYS[kind]
    _t_w0 = time.time()
    need_cnt = 0
    _safe = _safe_rebuild()
    try:
        payload = []
        for i, row in enumerate(rows):
            need, sku, pn, bc, js = _row_vals(row, need_keys)
            need_cnt += need
            payload.append([kind, scope, gen, i, need, sku, pn, bc, js])
        if _safe:
            # §15.164 根治 1062: 跨实例并发领到同一 gen(或上次失败留下半批)时,
            # 先清掉目标 gen 中**超出本轮行数**的尾部残留(线上 repl gen=6 残留 5600 行),
            # 再用 ON DUPLICATE KEY UPDATE 幂等覆盖同名行。同 fp 结果同源, 覆盖不损数据。
            execute("DELETE FROM list_snapshot WHERE kind=%s AND scope=%s AND gen=%s AND seq>=%s",
                    [kind, scope, gen, len(rows)])
        _upsert = (" ON DUPLICATE KEY UPDATE need=VALUES(need), sku=VALUES(sku), "
                   "product_name=VALUES(product_name), barcode=VALUES(barcode), "
                   "payload=VALUES(payload)") if _safe else ""
        # §15.176 分批**并行**写: 线上实测 write 与行数严格线性(156µs/行), 且批量
        #   200→1000→2000 都没有变化 —— 说明成本在 TiDB 逐行解析/索引/落盘, 不在往返。
        #   批与批之间的 seq 段**互不重叠**(切片互斥), 并发 INSERT 不会写同一行;
        #   跨实例同 gen 的重叠由上面的 DELETE 尾部残留 + ON DUPLICATE KEY UPDATE 兜住。
        #   任一失败 → 整体视为写失败(不切 head, 旧 gen 继续服务), 与串行语义一致。
        _sql = ("INSERT INTO list_snapshot(kind,scope,gen,seq,need,sku,product_name,barcode,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)" + _upsert)
        _batches = [payload[i:i + _CHUNK] for i in range(0, len(payload), _CHUNK)]
        if len(_batches) > 1:
            from concurrent.futures import ThreadPoolExecutor as _TPE
            with _TPE(max_workers=min(3, len(_batches))) as _pex:
                list(_pex.map(lambda b: executemany(_sql, b), _batches))
        else:
            for b in _batches:
                executemany(_sql, b)
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

    timing = "calc=%.1f(sql=%.1f/%d),write=%.1f,total=%.1f,rows=%d" % (
        _t_calc, _SQL_S, _SQL_N, _t_write, time.time() - _t0, len(rows))
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


# ── 触发 / 冷算治理(§15.161 / §15.165) ──────────────────────────
def _flight_key(kind, scope, fp):
    """§15.165: fp **不进单飞 key**。

    线上压测(stress_165 P2/P3)坐实: 任务页多任务每 0.2~0.3s bump 一次域计数 → fp
    每次都变 → 旧 key(kind|scope|fp)使同 (kind,scope) 每次都是「新 flight」, 单飞
    与 cooldown(要求 fp 相同)同时失效 → 6+ 类重建反复抢 3 个 heavy 名额, 30 条
    HeavyBusy, repl gen 3 分钟不涨, 读侧永远吃 18~20min 旧值(卡数据)。

    去 fp 后同一 (kind,scope) 在飞期间只算一次; build() 内部会**重新 fp_now()** 发布
    当时的最新 fp(红线 16 fp 同源), 所以一次在飞即可收敛到最新, 不需要每次写各算一遍。
    `FLIGHT_COALESCE=0` 一刀回退到含 fp 的旧 key。
    """
    if not _coalesce_on():
        return "%s|%s|%s" % (kind, scope, fp)
    return "%s|%s" % (kind, scope)


def _cold_worker(kind, scope, fp, fn, key):
    """后台单飞: 全局名额由 db.heavy 分配, 不同 kind 可并行, 读侧不等它。

    与「全局单 worker」的区别是**按 kind 分舱**: 3 个 kind 各占 1 个名额并行跑,
    不是一条队列串行 15.54s。抢不到全局名额 = 快速失败, 不排队(红线 4/10)。
    §15.165: HeavyBusy 后给同 key 一个有界冷却(_BUSY_COOL), 防写风暴下每个读请求
    都立刻重抢名额、把 3 个名额反复打满却不收敛(红线 15: 有界, 不排队)。
    """
    try:
        from db import heavy, HeavyBusy
        try:
            # §15.178: global_=True → 除实例内名额, 再抢一张跨实例槽位(heavy_slot 表)。
            #   实例内名额是进程内 dict, 多实例下管不到全局; 实测 7 kind 同时重建时
            #   全站 p95 962ms(单 kind 只 117ms), 读侧被"同时"拖慢, 不是"重建"拖慢。
            with heavy("list:" + kind, timeout=_HEAVY_WAIT, global_=True):
                try:
                    fn()
                    _STATS["done"] += 1
                except Exception as _e:
                    _STATS["failed"] += 1
                    try_err("list_store", "后台重建失败(%s/%s)" % (kind, scope), _e)
        except HeavyBusy as _hb:
            # 全局 3 个名额已满: 快速失败并留痕, 不排队、不拖住读侧。
            # 读侧仍返回上一版完整物化; 冷却结束后下一次请求会再触发(flight 已释放)。
            _STATS["busy"] += 1
            if _coalesce_on():
                with _FLIGHT_LOCK:
                    _BUSY_UNTIL[key] = time.time() + _BUSY_COOL
                _STATS["busy_cooled"] += 1
            try_err("list_store", "冷算全局名额已满, 本轮跳过(%s/%s)" % (kind, scope), _hb)
    except Exception as _e2:
        _STATS["failed"] += 1
        try_err("list_store", "冷算调度异常(%s/%s)" % (kind, scope), _e2)
    finally:
        with _FLIGHT_LOCK:
            _FLIGHTS.pop(key, None)


def submit_background(kind, scope, fp, fn):
    """同 (kind,scope) 只算一次(§15.165 去 fp); 不同 kind 并行; 读侧**永不等待**。

    :return: state 字符串(started / dedup / cooling / error), 只用于 __swr 状态播报。
    """
    key = _flight_key(kind, scope, fp)
    _now = time.time()
    with _FLIGHT_LOCK:
        cur = _FLIGHTS.get(key)
        if cur and _now - cur["t0"] < _FLIGHT_TTL:
            _STATS["dedup"] += 1
            return "dedup"
        if _coalesce_on() and _BUSY_UNTIL.get(key, 0) > _now:
            # 上次抢名额失败后的有界冷却, 不新起线程、不排队(红线 4/15)
            _STATS["busy_cooled"] += 1
            return "cooling"
        _BUSY_UNTIL.pop(key, None)
        _FLIGHTS[key] = {"t0": _now, "kind": kind}
    try:
        threading.Thread(target=_cold_worker, args=(kind, scope, fp, fn, key),
                         daemon=True, name="list-cold-" + str(kind)).start()
        _STATS["started"] += 1
        return "started"
    except Exception as _e:
        with _FLIGHT_LOCK:
            _FLIGHTS.pop(key, None)
        try_err("list_store", "冷算线程启动降级(%s/%s)" % (kind, scope), _e)
        return "error"


def ensure_async(kind, scope=None, force=False):
    """写侧 invalidate / 读侧冷态的后台重建入口 —— 不在响应路径上等它。

    §15.161: 改走 submit_background(全局预算 + 同 key 单飞)。旧版每次起裸 daemon
    线程、3 个 kind 各起一个、无名额管理, 是并发互抢的真源(红线 4/10)。
    """
    try:
        if scope:
            scopes = [scope]
        else:
            scopes = [str(r.get("scope")) for r in
                      (query("SELECT scope FROM list_head WHERE kind=%s", [kind]) or [])
                      if r.get("scope")]
        if not scopes:
            return False
        for sc in scopes:
            _h = head(kind, sc) or {}
            # mode 必须与 build/page_of 同源: 空串交给 mode_of 自己查渠道默认(红线 16)。
            _ch = str(_h.get("channel") or "jd")
            _md = str(_h.get("mode") or "")
            _fp = fp_now(kind, _ch, _md, sc) or "?"
            submit_background(kind, sc, _fp,
                              lambda _s=sc, _f=force: build(kind, _s, force=_f))
        return True
    except Exception as _e:
        try_err("list_store", "重建调度降级(%s)" % kind, _e)
        return False


def _swr_meta(kind, scope, h, state):
    """冷态 SWR 的新鲜度播报(放响应**顶层** __swr, 不进 data → 形状契约一字不变)。

    freshness_s 用 DB 时间算, 不用 Python 本地时钟(DB 与实例时区可能不同)。
    """
    age = -1
    try:
        _r = one("SELECT TIMESTAMPDIFF(SECOND, built_at, NOW(6)) AS age_s "
                 "FROM list_head WHERE kind=%s AND scope=%s", [kind, scope])
        _v = (_r or {}).get("age_s")
        age = int(_v) if _v is not None else -1
    except Exception:
        age = -1
    return {
        "state": "serving_previous",
        "freshness_s": age,
        "built_at": str(h.get("built_at") or ""),
        "fp_changed": True,
        # §15.165 准确维: 放行「历史 stale=1 完整旧 gen」时显式播报, 绝不静默伪装成新值
        "prev_stale": bool(int(h.get("stale") or 0)),
        "rebuild": state,
        "retry_after_ms": 900,
    }


def pop_swr(d):
    """从 serve 结果里摘出 __swr 供路由放到响应顶层; data 形状保持不变。

    放顶层而不是塞进 data: 无分页裸调的 data 是**数组**(契约), 没有地方放状态字段;
    顶层字段对数组/对象两种形状都成立, 前端拦截器一处收口。
    """
    if isinstance(d, dict) and "__swr" in d:
        return {"__swr": d.pop("__swr")}
    return {}


def first_shared(key, ttl, wait, leader_fn, readback_fn):
    """§15.165 通用首建单飞骨架(供 list_store / dash_store / own_store 复用, 红线 2 不叠加)。

    同 key 并发只让 leader 跑一次 `leader_fn`(算+落物化), 其返回值作为**共享结果**
    直接交给所有人的 `readback_fn(shared)`; follower 有界等 `wait` 后读同一份,
    绝不各算一遍全量。等待是「等 leader 算完这一份」, 不是「读请求绑后台重建 30s」。
    `shared` 为 None(leader 没产出/异常)时由 readback_fn 自行读物化或单份现算兜底。
    进程内单飞跨实例仍各算一次(既有边界), 但幂等写 + 去 fp 单飞保证不损数据。
    """
    with _FLIGHT_LOCK:
        cur = _FIRSTS.get(key)
        if cur and time.time() - cur["t0"] < ttl:
            entry, leader = cur, False
        else:
            entry = {"t0": time.time(), "event": threading.Event(), "data": None}
            _FIRSTS[key] = entry
            leader = True
    if leader:
        try:
            entry["data"] = leader_fn()
        finally:
            entry["event"].set()
            with _FLIGHT_LOCK:
                # 结果留在 entry 里给所有 follower 读, 不随 pop 丢; 由 TTL 下次覆盖
                _FIRSTS.pop(key, None)
    else:
        _STATS["first_shared"] += 1
        if not entry["event"].wait(wait):
            _STATS["first_timeout"] += 1
    return readback_fn(entry["data"])


def _first_serve(kind, scope, fp, builder, channel, mode,
                 need_only, search, page, page_size):
    """§15.165 首建单飞: 仅「从未构建 / 物化全坏行」的一次性初始化入口。

    改前(线上 stress_165 P4 实测): `submit_background(build)` 后台算一遍 +
    `return _live()` 前台同步全量再算一遍 = **双算**; 4 并发 = 8 份 13s 全量 Python
    循环抢 GIL + 抢 10 个连接 → 16.9s / 2×503 / 4/4 零数据(停摆), 违反红线 6/10。

    改后: 同 (kind,scope) 单飞, **leader 只跑一次 `build(force=True)`**(算+落物化+
    发布前 COUNT 自校验+切 head, 复用红线 8②/9 的既有写入链), follower 有界等
    `_FIRST_WAIT` 后**读回同一份物化**(不重复全量); 全部返回完整数据, 绝不 0 条。
    等待是「等 leader 算完这一份」, 不是「等后台重建 30s」; 且只在真·从未构建时发生
    (§15.165 第 2 刀后, 有完整旧 gen 的 stale=1 已改走 SWR, 根本不进这里)。
    `FIRST_SINGLEFLIGHT=0` 一刀回退到旧双算路径。
    """
    key = _flight_key(kind, scope, fp)

    def _leader():
        _STATS["first_legacy"] += 1
        try:
            build(kind, scope, force=True)
        except Exception as _e:
            try_err("list_store", "首建单飞失败(%s/%s)" % (kind, scope), _e)

    def _readback(shared):
        d = page_of(kind, scope, channel, mode, need_only, search, page,
                    page_size, extra=scope, allow_stale=True)
        if d is not None:
            return d
        # 极端降级(build 失败/实例冻结): 单份现算兜底, 绝不返回 0 条(数据四维-完整)
        from core.list_page import shape as _shape
        items, total = _shape(builder(), need_only=need_only, search=search,
                              page=page, page_size=page_size, need_keys=NEED_KEYS[kind])
        return {OUT_KEY[kind]: items, "total": total, "page": page, "page_size": page_size}

    return first_shared(key, _FIRST_TTL, _FIRST_WAIT, _leader, _readback)


def serve(kind, scope, channel, mode, days, source, builder,
          need_only=0, search="", page=0, page_size=0, legacy=0):
    """路由唯一入口(§15.161): 稳态读物化 / 冷态 SWR 立即返回上一版 / 首次直接现算。

    · 稳态(fp 命中): 零额外开销、零额外字段, 形状一字不变。
    · 冷态(有上一版但 fp 不等): 后台按 kind 分舱重建(**读侧不等待**), 立即返回上一版
      **完整**物化 + __swr 新鲜度播报; 前端不写 30s 缓存、有界重拉(≤3s / 2 次)。
    · 首次 / 上一版不可用(gen=0 / stale / 哨兵 / 坏行): 直接现算保证业务正确,
      并后台建立物化; 不等待、不返回 0 条。任何请求 ≤ 单跑 p95, 绝不 15~30s。
    · 回滚: `LIST_READ=0` 一刀退回纯现算(旧路径), 互斥不并存(红线 2)。
    · `legacy=1`: 诊断口强制现算, 保持原样 —— 它是红线 16 三重验证的基准,
      必须真实反映「不走物化」的耗时, 不接预算/单飞。

    删掉的旧行为: 「每请求前台 builder() 全量现算 + 再起裸线程 build() 算第二遍」
    (双算) —— 并发 4 实测 33.26s / repl 4/4 HTTP 503, 是冷算雪崩的真源(红线 2/4/10)。
    """
    h = head(kind, scope)
    if h is None:
        register_scope(kind, scope, channel, mode, days, source)
        h = head(kind, scope)

    from core.list_page import shape as _shape

    def _live():
        items, total = _shape(builder(), need_only=need_only, search=search,
                              page=page, page_size=page_size, need_keys=NEED_KEYS[kind])
        return {OUT_KEY[kind]: items, "total": total, "page": page, "page_size": page_size}

    # 回滚开关 / 诊断口: 互斥分支, 不与新路径并存(红线 2)
    if not read_enabled() or legacy:
        return _live()

    # ① 稳态: fp 命中 → 直接返回, 零额外开销、零额外字段
    d = page_of(kind, scope, channel, mode, need_only, search,
                page, page_size, extra=scope, h=h)
    if d is not None:
        return d

    # ② 冷态: 先取「上一版完整物化」(只跳过 fp 比对, 完整性校验一字不减)
    prev = page_of(kind, scope, channel, mode, need_only, search,
                   page, page_size, extra=scope, h=h, allow_stale=True)

    fp = fp_now(kind, channel, mode, scope) or "?"

    # ③ 从未物化 / 上一版不可用(gen=0 / stale=1 / 哨兵 / 坏行): 直接现算保证业务正确,
    #    同时后台建立物化(一次性)。与「有上一版但 fp 不等」是**互斥状态**, 不是两套并存
    #    兜底(红线 2)。这里**不等待**后台结果: 任何请求 ≤ 单跑 p95(repl 6s / 其余 3~4s),
    #    首帧是当前实时算出的完整数据, 绝不返回 0 条、绝不等 15~30s。
    if prev is None:
        if _first_sf_on():
            return _first_serve(kind, scope, fp, builder, channel, mode,
                                need_only, search, page, page_size)
        _STATS["first_legacy"] += 1
        submit_background(kind, scope, fp, lambda: build(kind, scope, force=False))
        return _live()

    # ④ 有上一版 → 后台按 kind 分舱重建 + 立即返回完整数据 + 明确新鲜度播报。
    #    用户不等重建; 读侧**从不 wait** 后台结果(红线 6: 全量只在后台)。
    state = submit_background(kind, scope, fp, lambda: build(kind, scope, force=False))
    _STATS["swr_served"] += 1
    prev["__swr"] = _swr_meta(kind, scope, h, state)
    return prev


def status(kind, scope):
    """诊断口: 四维逐项可查, 不靠猜; §15.161 追加冷算治理计数。"""
    h = head(kind, scope) or {}
    # mode 必须与 build/page_of 同源: 空串交给 mode_of 查渠道默认, 不兜底 "bbcc"
    # (红线 16: 诊断口自己造一个 mode 会让 fp_now 与读写两侧不一致, 显示假的 fp 不命中)。
    ch, md = str(h.get("channel") or "jd"), str(h.get("mode") or "")
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
        # §15.161 冷算治理实时播报: 有没有真的撞车/降级, 可查可告警, 不靠猜。
        "cold": dict(_STATS, inflight=len(_FLIGHTS)),
    }
