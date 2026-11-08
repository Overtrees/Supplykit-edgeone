"""看板三接口「最终聚合结果」的域指纹物化 + 读侧零等待 SWR (2026-11-08 §15.163 / P3)

## 为什么需要它(线上分层实测, 不是推测)

§15.162 只把**判定读模型**(unit_store 的 tally/overview/risk)做了 SWR, 但看板三接口的
**最终聚合结果**仍走 `analysis_cache.cache_get(TTL) + 请求路径同步 builder + heavy(2s)
+ 单飞锁(2s)`。2026-11-08 改前基线(p3_probe.py, 线上真实链路)把这条链钉死:

| 分组 | 条件 | 结果 |
|---|---|---|
| A 稳态 | 全命中 | 并发4 墙钟 1.70s / 0 非200 |
| B 纯 TTL 失效 | 只清缓存 | 单发 0.89~1.95s, **无播报** |
| C 纯 fp 失配 | 只 bump 域计数 | 单发 0.45~1.51s, **无播报**(TTL 把 fp 失配屏蔽了 → 静默返旧值) |
| D 真实冷态 | 墓碑+fp 失配 | **并发4 墙钟 8.10s**; 日志 `HeavyBusy 已等2s`(aux/risk 各2) + `单飞等待超时2s` |

即: 判定读模型 SWR 已让「单发」不慢了, 但**并发**下 4 个 boot 各拆出 aux/risk 去抢
global heavy 名额(3)+ 同 key 单飞锁(2s), 等待链叠加成 8.1s。而且 TTL 与 fp 两套判据
并存(C 组)会静默返回旧值 —— 违反数据四维的「准确」。

建议页(insights/list_store)早就没这套东西了: `serve()` 稳态读物化、冷态**读侧零等待**
立即返回上一版完整物化 + 顶层 __swr + 后台按 kind 分舱重建, 且**无 TTL**(fp 驱动)。
本模块把看板三接口收敛到同一套骨架, 一次去掉三处等待链 + 两套判据:

  · 载体: 复用 `analysis_cache` 表(已跨实例共享/令牌守卫), 独立 key 前缀 `dmat|`,
    **不新建表/队列/worker/租约**(红线 2/4)。value 内嵌 fp/gen/data 自描述。
  · 指纹: `unit_store.fp_of(族依赖域, channel, mode)` —— 与判定读模型**同一套域计数器**
    (红线 16 fp 同源), 不另造指纹语义。写操作 invalidate() 同源 bump 域计数 → fp 变。
  · 后台重建: `list_store.submit_background(kind, scope, fp, fn)` —— 复用 db.heavy
    全局预算 + 同 key 单飞 + 60s 僵尸判定(红线 4/5), 不叠加第二套闸门。
  · 读侧: 任何分支**从不 wait** 后台结果(红线 6)。稳态零额外字段; 冷态返回上一版
    **完整**物化 + 顶层 __swr 显式播报(freshness_s/fp_changed/built_at); 首次/上一版
    不可用 → 直接现算保证完整数据, 绝不返回空结构/0 条。
  · 回滚: `DASH_SWR=0` 一刀退回纯现算(旧路径由路由层保留), 互斥不并存(红线 2)。

## 与 §15.162 / list_store 的关系(红线 1: 先读懂再动手)

· 本模块物化的是**路由函数的最终返回 dict**(summary/aux/stock_risk), 与 unit_store
  物化的「判定单元 tally」是**不同层次**: 前者是组装好的卡片数字, 后者是底层计数。
  两者不冲突、不重复计算 —— serve() 命中时连 builder 都不调, 判定读模型也不碰。
· __swr / pop_swr / 顶层状态 / boot 冒泡 / 前端 __swrProbe: 全部复用 §15.161~162
  已上线的现成机制, 本模块只产出同构的 meta, 前端零改动。
· 算法/_build_*/_assemble/排序: 一行不动(红线 9), 只换读路径的包装层。
"""
import os
import json
import time
import hashlib

from db import query, one, execute
from routes.common import try_err

# ── 族依赖域(与 analysis_cache._FAMILY_DEPS 同源, 不另造语义) ──────────────
def _deps(family):
    from routes.analysis_cache import _FAMILY_DEPS
    return tuple(sorted(_FAMILY_DEPS.get(family) or ()))


# 回滚总开关: 未设置 = 开; "0" = 一律纯现算(紧急回滚, 不必发版)。与 list_store 同风格。
_READ_ON = ("1", "on", "true", "yes")


def read_enabled():
    _v = os.environ.get("DASH_SWR")
    if _v is None or str(_v).strip() == "":
        return True
    return str(_v).strip().lower() in _READ_ON


def fp_now(family, channel, mode=None, scope=""):
    """域指纹 —— 复用 unit_store.fp_of(同一套 data_version_dom 计数器, 红线 16)。

    scope 纳入指纹: 同一族不同参数(start_date/end_date/full/level/part 等)必须各有
    自己的物化, 否则第 1 页的响应会被第 3 页命中。返回空串 = 不可用 → 一律走现算。
    """
    try:
        from core import unit_store as _US
        base = _US.fp_of(_deps(family), channel, mode)
    except Exception as _e:
        try_err("dash_store", "指纹计算降级", _e)
        return ""
    if not base:
        return ""
    return hashlib.md5(("%s|%s|%s" % (base, family, scope)).encode("utf-8")).hexdigest()[:32]


_PREFIX = "dmat|"
_STAT = {"hit": 0, "swr_served": 0, "first_legacy": 0,
         "rebuild_done": 0, "rebuild_failed": 0}


def _key(family, scope):
    return _PREFIX + family + "|" + scope


# ── 读物化 ──────────────────────────────────────────────────────────────
def _read(family, scope, fp=None, allow_stale=False):
    """读回上一版完整物化; None = 不可用(从未建 / 坏行 / 完整性哨兵不过)。

    allow_stale=True: 只跳过 fp 比对, **完整性校验一字不减**(gen>0 / value 可解析 /
    data 是 dict 且非空 / fp 字段存在) —— 与 list_store.page_of 同构, 绝不把半截数据
    或空结构当成"上一版完整物化"返回(数据四维: 完整)。
    """
    try:
        row = one("SELECT value FROM analysis_cache WHERE `key`=%s", [_key(family, scope)])
        if not row or not row.get("value"):
            return None
        box = json.loads(row["value"])
        if not isinstance(box, dict) or box.get("v") != 1:
            return None
        if not int(box.get("gen") or 0) or not str(box.get("fp") or ""):
            return None
        data = box.get("data")
        if not isinstance(data, dict) or not data:
            return None
        if not allow_stale and (not fp or str(box.get("fp")) != fp):
            return None
        return box
    except Exception as _e:
        try_err("dash_store", "物化读取降级(%s)" % family, _e)
        return None


def _swr_meta(box, state):
    """顶层 __swr 播报(DB 时间算 freshness, 不用 Python 本地时钟)。"""
    age = -1
    try:
        r = one("SELECT TIMESTAMPDIFF(SECOND, FROM_UNIXTIME(%s), NOW(6)) AS age_s",
                [int(box.get("ts") or 0)])
        v = (r or {}).get("age_s")
        age = int(v) if v is not None and int(v) >= 0 else -1
    except Exception:
        age = -1
    return {"state": "serving_previous", "freshness_s": age,
            "fp_changed": True, "built_at": str(box.get("built_at") or ""),
            "rebuild": state, "source": "dash_mat", "retry_after_ms": 900}


# ── 写物化 ──────────────────────────────────────────────────────────────
def _write(family, scope, fp, data):
    """发布前完整性自校验(§15.152 物化四维): data 必须是非空 dict; 用令牌守卫
    的 cache_put 同源 UPSERT 落表(跨实例一致), 超大值不落表(= 下次重建, 不撑爆单行)。
    """
    if not isinstance(data, dict) or not data or not fp:
        return False
    # 底层判定读模型(unit_store)可能往结果里塞 `__swr`(§15.162)——它是**响应顶层状态**,
    # 绝不能被写进最终物化, 否则稳态命中会一直误播报"数据更新中"。浅拷贝后剥离,
    # 不影响首次现算路径里 builder() 原对象(那条由路由层 pop 到底层播报)。
    data = dict(data)
    data.pop("__swr", None)
    if not data:
        return False
    # built_at / ts 用 **DB 时间**: 实例与 DB 时区可能不同, 用 Python 本地时钟会让
    # freshness_s 算错(与 list_store._swr_meta 同源口径, 红线: 新鲜度只用 DB 时间)。
    try:
        _r = one("SELECT NOW(6) AS n, UNIX_TIMESTAMP(NOW(6)) AS ts") or {}
        built_at, ts = str(_r.get("n") or ""), int(_r.get("ts") or 0)
    except Exception:
        built_at, ts = time.strftime("%Y-%m-%d %H:%M:%S"), int(time.time())
    box = {"v": 1, "fp": fp, "gen": int(time.time() * 1000),
           "ts": ts, "data": data, "built_at": built_at}
    s = json.dumps(box, ensure_ascii=False, default=str)
    if len(s.encode("utf-8")) > 6 * 1024 * 1024:   # 6MB 单行上限(同 stock_risk 口径)
        try_err("dash_store", "物化超 6MB 不落表(%s/%s)" % (family, scope), None)
        return False
    try:
        from routes.analysis_cache import cache_put
        cache_put(_key(family, scope), s, raw=True)
        return True
    except Exception as _e:
        try_err("dash_store", "物化发布降级(%s)" % family, _e)
        return False


def build(family, scope, channel, mode, builder):
    """后台重建入口: 调**原计算体** builder() → 自校验 → 发布。读侧从不调用它同步。"""
    fp = fp_now(family, channel, mode, scope)
    if not fp:
        return {"ok": False, "why": "fp_unavailable"}
    try:
        data = builder()
    except Exception as _e:
        _STAT["rebuild_failed"] += 1
        try_err("dash_store", "后台重算失败(%s/%s)" % (family, scope), _e)
        return {"ok": False, "why": "builder_error"}
    ok = _write(family, scope, fp, data)
    if ok:
        _STAT["rebuild_done"] += 1
    return {"ok": ok, "fp": fp}


# ── 服务(路由唯一入口) ───────────────────────────────────────────────────
def serve(family, scope, channel, mode, builder):
    """看板三接口统一读路径 → (data, swr_meta_or_None)。与 list_store.serve 同构:

      ① 稳态(fp 命中): 零额外开销、零额外字段, 直接读物化。
      ② 冷态(有上一版但 fp 不等): 后台按 family 分舱重建(**读侧不等待**),
         立即返回上一版**完整**物化 + 顶层 __swr; 全量计算只在后台(红线 6)。
      ③ 首次 / 上一版不可用: 直接现算保证完整数据(绝不返回空结构/0 条),
         同时后台建立物化; 不等待, 任何请求 ≤ 单跑 p95。
      ④ DASH_SWR=0 / legacy: 由路由层互斥走纯现算(此处不接预算/单飞)。
    """
    if not read_enabled():
        return builder(), None
    fp = fp_now(family, channel, mode, scope)
    # fp 不可用(域计数表没建/查询失败) → 一律纯现算, 不读物化/不写物化/不后台:
    #   宁可慢也绝不用无法判定新鲜度的数据(红线 16 最安全降级)。
    if not fp:
        return builder(), None
    # ① 稳态: fp 命中 → 直接返回, 零额外开销、零额外字段
    box = _read(family, scope, fp=fp)
    if box is not None:
        _STAT["hit"] += 1
        return box["data"], None
    # ② 冷态: 取上一版完整物化(只跳 fp 比对, 完整性哨兵一字不减)
    prev = _read(family, scope, allow_stale=True)
    if prev is None:
        # ③ 首次/上一版不可用 → 直接现算保证完整数据(绝不返回空结构/0 条), **同步**落物化
        #    (一次轻量 UPSERT), 不后台双算。部署后 `dmat|` 新前缀首波全是首次路径,
        #    这一刀让第二发立即命中, 且不产生 N 个各自现算的后台线程(红线 4/9/10)。
        _STAT["first_legacy"] += 1
        data = builder()
        _write(family, scope, fp, data)
        return data, None
    # ④ 有上一版 → 后台按 family 分舱重建(读侧不 wait) + 立即返回完整数据 + 显式播报
    from core import list_store as _LS
    state = _LS.submit_background("dmat:" + family, scope, fp,
                                  lambda: build(family, scope, channel, mode, builder))
    _STAT["swr_served"] += 1
    return prev["data"], _swr_meta(prev, state)


def pop_swr(d):
    """从 serve 结果里摘出 __swr 供路由放响应顶层; data 形状保持不变。
    (与 list_store.pop_swr 同款; 本模块 serve 返回的是二元组, 路由直接用第二个返回值,
     此函数保留给「__swr 可能被塞进 data」的兼容路径, 保证形状契约一字不变。)
    """
    if isinstance(d, dict) and "__swr" in d:
        return {"__swr": d.pop("__swr")}
    return {}


def overview():
    """P2 观测(只读): 各族物化行数 / 最旧新年龄 / 冷态计数, 供诊断口与线上验收核对。
    不参与任何读路径决策, 只回答「物化建了没 / 命中没 / 有没有降级」。"""
    try:
        rows = query("SELECT `key`, value FROM analysis_cache WHERE `key` LIKE %s",
                     [_PREFIX + "%"]) or []
        n = len(rows)
        gen = 0
        for r in rows:
            try:
                gen = max(gen, int((json.loads(r.get("value") or "{}") or {}).get("gen") or 0))
            except Exception:
                pass
    except Exception:
        n, gen = -1, 0
    return {"mat_rows": n, "mat_gen": gen, "read_enabled": read_enabled(),
            "stat": dict(_STAT)}


def status(family, scope, channel, mode=None):
    """诊断口(只读): 四维逐项可查, 不靠猜。"""
    fp = fp_now(family, channel, mode, scope)
    hit = _read(family, scope, fp=fp) if fp else None
    stale = _read(family, scope, allow_stale=True)
    try:
        from core import list_store as _LS
        cold = dict(_LS._STATS, inflight=len(_LS._FLIGHTS))
    except Exception:
        cold = {}
    return {"read_enabled": read_enabled(), "family": family, "scope": scope,
            "fp_now": fp, "fp_head": str((stale or {}).get("fp") or ""),
            "fp_match": hit is not None, "has_previous": stale is not None,
            "gen": int((stale or {}).get("gen") or 0),
            "built_at": str((stale or {}).get("built_at") or ""),
            "deps": list(_deps(family)), "stat": dict(_STAT), "cold": cold}
