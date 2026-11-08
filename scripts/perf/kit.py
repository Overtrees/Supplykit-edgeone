# -*- coding: utf-8 -*-
"""压测公共件：登录态会话、服务端耗时读取、百分位、直连状态查询。

设计约束
--------
1. **耗时只认 `X-Srv-Ms`**（服务端自报，红线 7）。链路 RTT 单独记 `e2e`，不用于判定。
2. 直连（`QY`）只用于**读状态**（相位/重建分段/日志），不产出任何耗时结论。
3. 没有 `.env`（无 DB 凭据）时 `HAS_DB=False`，相位相关功能降级为"未知"，脚本照常跑。
4. 账号不写死在仓库：`SUPPLY_USER/SUPPLY_PASS` 环境变量；缺省用公开访客 `demo`（只读，
   写场景会 403 并明确报错，不会静默变成"没测到写"）。
"""
import os
import statistics
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("SUPPLY_BASE", "https://supplykit.top").rstrip("/")
USER = os.environ.get("SUPPLY_USER", "demo")
PASS = os.environ.get("SUPPLY_PASS", "demo123")

TOKEN = {}
HAS_DB = False
QY = None


def _load_env():
    """从仓库根 .env 读 DB 凭据（只在本地直连时需要；线上压测不依赖）。"""
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def init_db():
    """可选：初始化直连（失败即降级，不抛）。"""
    global HAS_DB, QY
    if HAS_DB:
        return True
    try:
        _load_env()
        import sys
        sys.path.insert(0, os.path.join(ROOT, "cloud-functions", "api"))
        from db import query as _q          # noqa: E402
        QY = _q
        HAS_DB = True
    except Exception:
        HAS_DB = False
    return HAS_DB


def login(user=None, pwd=None):
    s = requests.Session()
    r = s.post(BASE + "/api/auth/login",
               json={"username": user or USER, "password": pwd or PASS}, timeout=30)
    j = r.json()
    tok = (j.get("data") or {}).get("token") or j.get("token")
    if not tok:
        raise RuntimeError("登录失败(%s): %s" % (r.status_code, str(j)[:160]))
    TOKEN["v"] = tok
    TOKEN["user"] = user or USER
    return tok


def sess():
    s = requests.Session()
    s.headers.update({
        "X-Accept-Encoding": "gzip, deflate, br",   # 红线 13：网关会剥 Accept-Encoding
        "Accept-Encoding": "gzip, deflate, br",
        "X-Auth-Token": TOKEN["v"],
    })
    return s


def srv_ms(r):
    """服务端自报耗时（ms）；缺失返回 -1，**不要**用 e2e 顶替。"""
    try:
        v = r.headers.get("X-Srv-Ms")
        return int(v) if v not in (None, "") else -1
    except Exception:
        return -1


def pct(v, q):
    v = sorted(x for x in v if x >= 0)
    return v[min(len(v) - 1, int(len(v) * q))] if v else -1


def stat(v):
    v = [x for x in v if x >= 0]
    if not v:
        return {"n": 0}
    return {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": max(v),
            "mean": round(sum(v) / len(v), 1)}


def hit(key, path=None, timeout=90):
    """打一个接口，返回服务端耗时 + 完整性 + SWR 状态。"""
    from endpoints import ENDPOINTS, complete, _rows
    s = sess()
    p = path or ENDPOINTS[key]["path"]
    t0 = time.time()
    try:
        r = s.get(BASE + p, timeout=timeout)
        try:
            jj = r.json()
        except Exception:
            jj = {}
        d = jj.get("data")
        swr = jj.get("__swr") if isinstance(jj.get("__swr"), dict) else None
        return {"ep": key, "srv": srv_ms(r), "e2e": round(time.time() - t0, 3),
                "status": r.status_code, "rows": _rows(d),
                "swr": bool(swr), "fresh": (swr or {}).get("freshness_s", -1),
                "complete": complete(key, d), "kb": len(r.content) // 1024}
    except Exception as e:
        return {"ep": key, "srv": -1, "e2e": round(time.time() - t0, 3), "status": -1,
                "rows": -1, "swr": False, "fresh": -1, "complete": False, "err": str(e)[:80]}


# ── 写操作（模拟真实用户写入，触发 invalidate） ──────────────────
_SEASON = {}


def real_write():
    """改季节系数首条（enabled=false，不参与计算）的 factor：1.5 ↔ 1.5001。

    每次值都变 → 一定触发 `invalidate('config')`，偶数次写完回到原值，
    **对业务结果零影响**（§15.174 起后端对"值未变"做幂等短路，写原值测不到失效）。
    """
    try:
        s = sess()
        if "base" not in _SEASON:
            g = s.get(BASE + "/api/replenishment-config/seasons?channel=jd&mode=bbcc", timeout=30)
            _SEASON["base"] = g.json().get("data") or []
            _SEASON["n"] = 0
        _SEASON["n"] += 1
        import json as _j
        items = _j.loads(_j.dumps(_SEASON["base"]))
        if items:
            items[0]["factor"] = 1.5 if _SEASON["n"] % 2 else 1.5001
        body = {"channel": "jd", "mode": "bbcc", "items": items}
        t0 = time.time()
        r = s.put(BASE + "/api/replenishment-config/seasons", json=body, timeout=30)
        if r.status_code == 403:
            raise RuntimeError("写场景需要管理员账号（当前 %s 只读）：设 SUPPLY_USER/SUPPLY_PASS"
                               % TOKEN.get("user"))
        return {"e2e": round(time.time() - t0, 3), "status": r.status_code}
    except RuntimeError:
        raise
    except Exception as e:
        return {"e2e": -1, "status": -1, "err": str(e)[:80]}


# ── 直连状态（相位 / 分段 / 日志） ──────────────────────────────
def phase_now():
    """相位：有几个重建在跑 + 最近一次重建距今几秒。无 DB 时返回 unknown。"""
    if not init_db():
        return {"busy": -1, "age": -1, "unknown": True}
    try:
        r = QY("SELECT COUNT(*) n FROM heavy_slot WHERE holder<>'' "
               "AND ts > NOW(6) - INTERVAL 300 SECOND", [])
        busy = int((r[0] or {}).get("n") or 0)
        r2 = QY("SELECT MIN(TIMESTAMPDIFF(SECOND, built_at, NOW())) a FROM ("
                " SELECT built_at FROM list_head"
                " UNION ALL SELECT built_at FROM unit_state_head) t", [])
        return {"busy": busy, "age": round(float((r2[0] or {}).get("a") or 9999), 1)}
    except Exception as e:
        return {"busy": -1, "age": -1, "err": str(e)[:60]}


def wait_quiet(max_wait=90.0, need=3, min_age=10.0):
    """等到「连续 need 次无重建占用，且最近重建 ≥min_age 秒」。带写场景常等不到（如实返回）。"""
    t0 = time.time()
    quiet, last = 0, {}
    while time.time() - t0 < max_wait:
        last = phase_now()
        if last.get("unknown"):
            return {"ok": False, "waited": 0, **last}
        if last["busy"] == 0 and last["age"] >= min_age:
            quiet += 1
            if quiet >= need:
                return {"ok": True, "waited": round(time.time() - t0, 1), **last}
        else:
            quiet = 0
        time.sleep(1.0)
    return {"ok": False, "waited": round(time.time() - t0, 1), **last}


def heads():
    """各 kind 的重建分段（冷算本体归因的唯一来源）。"""
    if not init_db():
        return []
    try:
        return QY("SELECT kind, scope, gen, row_cnt, timing, stale, built_at "
                  "FROM list_head ORDER BY kind, scope", [])
    except Exception:
        return []


def cv(vals):
    v = [x for x in vals if x and x > 0]
    if len(v) < 2:
        return None
    m = statistics.median(v)
    return round(statistics.pstdev(v) / m, 3) if m else None
