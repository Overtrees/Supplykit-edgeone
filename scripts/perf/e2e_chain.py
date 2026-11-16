#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路消费方验收（页面 → 接口 → 物化/缓存 → 跨消费方一致性）。

回答的不是"快不快"（那是 `run.py` 的事），而是：

  1) **页面链路**：16 个页面各自依赖的接口，在线上是否 200/有数据/结构完整；
  2) **模块链路**：124 个端点（含 cron / 导出 / 导入 / 任务）是否可达、鉴权是否正确；
  3) **跨消费方联动**：同一份数据被多个消费方读时，数值是否同源一致
     （看板 summary / aux / boot、告警 counts、断货 stock-risk / unit-state、补货 vs 导出）。

红线（与 §15.187-198 一脉）：
- **判据只在这里，且只判"通不通、一不一致"**，不判延迟；延迟归 `run.py` 的 `TH`。
- 只读为主；写只用**可回滚/只读型**端点（季节系数 1.5↔1.5001、日志上报、导出、规则试算），
  破坏性写（seed/月结/批量改数）一律 `skip` 并如实标注，不假装测过。
- 直连 DB 只用于**取基准真值**（`COUNT`），不产出任何耗时结论。
- 不硬编码账号：`SUPPLY_USER/SUPPLY_PASS`（默认 demo），admin 走 `SUPPLY_ADMIN_USER/PASS`。

用法
----
    python3 scripts/perf/e2e_chain.py inventory   # 零网络：路由/页面/消费方矩阵 + 缺口
    python3 scripts/perf/e2e_chain.py run         # 线上实测（只读 + 安全写闭环）
    python3 scripts/perf/e2e_chain.py report      # 最近一次 run 的 Markdown 汇总
    python3 scripts/perf/e2e_chain.py selftest    # 判据反证（喂假数据，不改线上）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = os.path.join(ROOT, "cloud-functions", "api")
OUT = os.path.join(HERE, "out")

sys.path.insert(0, HERE)
sys.path.insert(0, API)

import kit as K  # noqa: E402  登录/会话/服务端耗时

# ── 身份 ──────────────────────────────────────────────────────────────────
ADMIN_USER = os.environ.get("SUPPLY_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("SUPPLY_ADMIN_PASS", "admin123")
# demo 走 kit 的默认（SUPPLY_USER/SUPPLY_PASS），保持与压测同账号

# ── 写端点处置：read=只读、safe=可回滚写、guarded=无效入参验错误语义、skip=不跑 ──
# 默认：GET=read，非 GET=guarded；下面登记例外。
WRITE_SPEC = {
    # 只读型 POST（名字叫 POST，但不改业务数据）
    "/db/diag": ("read", "库诊断（只读 EXPLAIN/计数）"),
    "/logs/frontend": ("safe", "前端日志上报，写一条带 e2e 标记的日志"),
    "/cleansing/dup-check": ("read", "导入查重（只读 CSV 解析）"),
    "/cleansing/detect": ("read", "导入检测（只读列识别）"),
    "/cleansing/preview": ("read", "导入预览（只读解析）"),
    "/exports": ("safe", "创建导出任务（只读业务数据，写任务文件）"),
    "/rules/{rid}/test": ("read", "单规则试算（不落库）"),
    # 可回滚写
    "/replenishment-config/seasons": ("safe", "季节系数 1.5↔1.5001，读回后还原"),
    # 明确不跑（破坏面/成本/需要业务上下文），如实标注原因
    "/auth/setup": ("skip", "已配置 SETUP_TOKEN，建号走 scripts/bootstrap_admin.py"),
    "/seed/fill": ("skip", "重灌/造数，会改业务数据"),
    "/seed/reset": ("skip", "清空演示数据，破坏性"),
    "/seed/rollback": ("skip", "回滚 seed，破坏性"),
    "/seed/step": ("skip", "重灌分步，会改业务数据"),
    "/seed/watermark": ("skip", "种子水位报告，需 seed 任务上下文"),
    "/cleansing/execute-async": ("skip", "真实导入，改业务数据；导入链路由 import_smoke.py 验"),
    "/cleansing/driver": ("skip", "接力驱动，需导入任务 id"),
    "/rules/evaluate": ("skip", "全量评估 100s+，会改 alerts；由 cron/daily-rules 链路验"),
    "/rules/batch": ("skip", "批量启停规则，改配置"),
    "/orders/batch": ("skip", "批量改单/软删，改业务数据"),
    "/products/batch": ("skip", "批量改商品，改业务数据"),
    "/inventory/batch": ("skip", "批量改库存，改业务数据"),
    "/disposals/batch": ("skip", "批量处置，改业务数据"),
    "/flow-records/batch": ("skip", "批量写回款流水，改业务数据"),
    "/ops/snapshot-barcode": ("skip", "69 码快照写入 ops 表，非读链路"),
    "/maintenance/heal-own": ("skip", "自有仓自愈会重算库存，写面较大"),
    "/tasks/maintain": ("skip", "维护任务会触发物化重建，归 suite_once/cron"),
    "/ledger/close": ("skip", "账期月结，不可逆"),
    "/ledger/reclose": ("skip", "反月结，不可逆"),
    "/channels": ("skip", "新建渠道会改配置（渠道是路由键，误建影响面大）"),
    "/suppliers": ("skip", "新建供应商会改档案与计数"),
    "/products": ("skip", "商品新建需完整档案，越界"),
    "/purchase-orders": ("skip", "新建/删除采购单会改在途与告警联动"),
    "/rules": ("skip", "新建规则会参与评估"),
    "/cleansing/templates": ("skip", "保存模板会改配置"),
}

# 这些端点即使 200 也可能是「空结构且合法」，不计空壳
KNOWN_EMPTY = {
    "/cleansing/status", "/cleansing/eval-status", "/exports/status",
    "/seed/fill/status", "/rules/eval-status", "/quality-logs/file",
    "/quality-logs/export", "/ledger/status", "/ledger/preview",
}

# 路径参数用明显不存在的 id：验「错误语义」，不碰真行
FAKE = {"oid": 999999901, "iid": 999999902, "rid": 999999903,
        "sid": 999999904, "pid": 999999905, "tid": 999999906,
        "code": "zz-e2e-none", "filename": "zz-e2e-none.csv", "task_id": 999999907,
        "sku": "SKU-E2E-NONE", "warehouse": "ZZ"}

# demo/anon 身份矩阵只跑代表接口：鉴权在中间件统一处理，与具体接口无关；
# 重接口（全量分页/多表拼接）重复跑只会把"身份验证"变成"再压一遍"。
IDENTITY_SKIP = {"/insights/with-sales", "/insights/disposal-suggestions", "/insights/purchase",
                 "/ledger/preview", "/history/summary", "/history/top", "/history/yoy",
                 "/dashboard/boot", "/dashboard/own-health", "/inventory/archive",
                 "/quality-logs/export", "/flow-records/list", "/orders", "/alerts"}
# 按**前端真实参数**探测（默认 page=0/page_size=0 是全量导出型口径，会把握手当页面耗时）
PARAM_OVERRIDE = {
    "/insights/with-sales": {"page": 1, "page_size": 100, "wh_type": "own"},
    "/insights/replenishment": {"page": 1, "page_size": 100},
    "/insights/purchase": {"page": 1, "page_size": 100},
    "/insights/disposal-suggestions": {"page": 1, "page_size": 100},
    "/insights/slow-moving": {"page": 1, "page_size": 100},
    "/orders": {"page": 1, "page_size": 30},
    "/alerts": {"limit": 200},
    "/dashboard/own-health": {"limit": 200},
    "/quality-logs": {"limit": 50},
}

ANON_SAMPLE = {"/health", "/insights/ping", "/auth/check", "/dashboard/summary",
               "/alerts/counts", "/insights/replenishment", "/products", "/rules",
               "/cron/ping", "/cleansing/templates"}

# 页面 → 代表接口（用于 run 的页面级结论；完整映射由 inventory 现算）
PAGES = [
    ("dash", "看板"), ("products", "商品"), ("suppliers", "供应商"),
    ("orders", "订单"), ("history", "历史归档"), ("inv", "进销存"),
    ("insights", "洞察/补货/采购/滞销"), ("cleansing", "数据清洗"),
    ("rules", "规则引擎"), ("quality", "异常记录"), ("settings", "设置"),
    ("tasks", "任务"), ("devmode", "开发模式"), ("channels", "渠道"),
    ("recycle", "回收站"), ("login", "登录"),
]


def _load_env():
    try:
        K._load_env()
    except Exception:
        pass


def _app():
    _load_env()
    from index import app  # noqa: E402
    return app


def routes():
    """真实路由清单（含函数式注册与嵌套 include），排除 docs。"""
    out = []
    for r in _app().routes:
        p = getattr(r, "path", "")
        if not p or p.startswith(("/docs", "/openapi", "/redoc")):
            continue
        for m in sorted(getattr(r, "methods", []) or []):
            out.append({"m": m, "p": p, "n": getattr(r, "name", "")})
    out.sort(key=lambda x: (x["p"], x["m"]))
    return out


def _openapi_params():
    spec = _app().openapi()
    idx = {}
    for p, ops in spec["paths"].items():
        for m, op in ops.items():
            ps = []
            for pr in op.get("parameters", []):
                ps.append({"n": pr["name"], "in": pr["in"], "req": pr.get("required", False),
                           "d": (pr.get("schema") or {}).get("default")})
            idx[(m.upper(), p)] = ps
    return idx


# ── 前端调用点（静态） ─────────────────────────────────────────────────────
_FE_SKIP = {"/api/client", "/api/logger", "/api/types", "/api/poller", "/api/mutate",
            "/api/notify", "/api/taskWatch", "/api/x", "/api/nonexistent",
            "/api/core/note_segs"}


def fe_calls():
    pat = re.compile(r"/api/[a-zA-Z0-9_\-/{}]+")
    out = {}
    for f in glob.glob(os.path.join(ROOT, "frontend", "src", "**", "*.ts*"), recursive=True):
        rel = os.path.relpath(f, os.path.join(ROOT, "frontend", "src"))
        if rel.startswith("tests") or "locale" in rel:
            continue
        for line in open(f, encoding="utf-8"):
            for m in pat.findall(line):
                p = m.rstrip("/")
                if p in _FE_SKIP:
                    continue
                out.setdefault(rel, set()).add(p)
    return {k: sorted(v) for k, v in out.items()}


def norm(p):
    """把前端的具体段归一成后端路径模板：/api/orders/123 → /orders/{oid}。"""
    p = p[4:] if p.startswith("/api") else p
    p = re.sub(r"/\d+(?=/|$)", "/{id}", p)
    return p


def route_match(fe_path, routeset):
    """前端路径 → 后端模板（前缀匹配最长）。"""
    p = norm(fe_path)
    if p in routeset:
        return p
    best = ""
    for r in routeset:
        if "{" not in r:
            continue
        head = r.split("{")[0].rstrip("/")
        if head and (p == head or p.startswith(head + "/")):
            if len(head) > len(best.split("{")[0].rstrip("/")):
                best = r
    return best or ""


def inventory():
    rs = routes()
    rset = {r["p"] for r in rs if r["m"] == "GET"} | {r["p"] for r in rs if r["m"] != "GET"}
    fe = fe_calls()
    fe_all = sorted({p for v in fe.values() for p in v})
    matched, unmatched = {}, []
    for p in fe_all:
        t = route_match(p, rset)
        if t:
            matched.setdefault(t, []).append(p)
        else:
            unmatched.append(p)

    # 页面 → 接口（页面目录前缀归并）
    page_api = {}
    for f, ps in fe.items():
        key = f.split(os.sep)[0]
        if key == "pages":
            key = f.split(os.sep)[1].replace(".tsx", "")
        elif key == "components":
            key = "components/" + (f.split(os.sep)[2].replace(".tsx", "") if len(f.split(os.sep)) > 2 else "")
        page_api.setdefault(key, set()).update(ps)

    spec = []
    for r in rs:
        kind = "read" if r["m"] == "GET" else "guarded"
        note = ""
        if r["p"] in WRITE_SPEC:
            kind, note = WRITE_SPEC[r["p"]]
        if r["p"].startswith("/cron/"):
            kind, note = "skip", "cron 维护任务：跑会真执行（窗口外才安全），归 suite_once"
        spec.append({"m": r["m"], "p": r["p"], "n": r["n"], "kind": kind, "note": note,
                     "fe": matched.get(r["p"], [])})

    uncovered = [x for x in spec if not x["fe"] and x["kind"] != "skip"]
    out = {
        "routes": len(rs),
        "get": sum(1 for x in rs if x["m"] == "GET"),
        "write": sum(1 for x in rs if x["m"] != "GET"),
        "fe_paths": len(fe_all),
        "fe_unmatched": unmatched,
        "page_api": {k: sorted(v) for k, v in sorted(page_api.items())},
        "spec": spec,
        "uncovered": [{"m": x["m"], "p": x["p"], "kind": x["kind"]} for x in uncovered],
    }
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "%s_inventory.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("路由 %d（GET %d / 写 %d） 前端独立路径 %d" % (out["routes"], out["get"], out["write"], out["fe_paths"]))
    print("前端调了但后端无路由：%d" % len(unmatched))
    for x in unmatched:
        print("   ! " + x)
    print("后端有、前端未直接调用、且非 skip：%d" % len(uncovered))
    for x in uncovered:
        print("   - %-6s %-46s %s" % (x["m"], x["p"], x["kind"]))
    print("[out] %s" % p)
    return out


# ── 线上探测 ──────────────────────────────────────────────────────────────
def _sess(token=None):
    import requests
    s = requests.Session()
    s.headers.update({"X-Accept-Encoding": "gzip, deflate, br",
                      "Accept-Encoding": "gzip, deflate, br"})
    if token:
        s.headers["X-Auth-Token"] = token
    return s


def _login(user, pwd):
    s = _sess()
    r = s.post(K.BASE + "/api/auth/login", json={"username": user, "password": pwd}, timeout=30)
    j = r.json()
    tok = (j.get("data") or {}).get("token") or j.get("token")
    if not tok:
        raise RuntimeError("登录失败 %s: %s %s" % (user, r.status_code, str(j)[:160]))
    return tok, (j.get("role") or "")


def _url(p, params):
    if not params:
        return K.BASE + "/api" + p
    import urllib.parse
    q = []
    for pr in params:
        v = pr["d"]
        if v is None or v == "":
            continue
        q.append((pr["n"], str(v)))
    return K.BASE + "/api" + p + ("?" + urllib.parse.urlencode(q) if q else "")


def _classify(status, srv, body):
    if status == 0:
        return "net"
    if srv >= 0:
        return "app" if 200 <= status < 300 else "app_err"
    if isinstance(body, str) and body.lstrip().startswith("<"):
        return "edge"
    return "app_uncaught" if status >= 500 else "unknown"


def _empty(body):
    """空结构判定：ok:false 不算空、有非零业务字段不算空。"""
    if not isinstance(body, dict):
        return True
    if body.get("ok") is False:
        return False
    d = body.get("data", body)
    if isinstance(d, list):
        return len(d) == 0
    if isinstance(d, dict):
        biz = [k for k in d if k not in ("channel", "mode", "built_at", "timing", "note",
                                          "ok", "fp_head", "fp_now", "fp_match", "version")]
        if not biz:
            return True
        for k in biz:
            v = d[k]
            if isinstance(v, (list, dict)) and len(v):
                return False
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                return False
            if isinstance(v, str) and v and v not in ("ok", "0"):
                return False
        return True
    return True


def probe(sess, method, path, params=None, body=None, timeout=120):
    t0 = time.time()
    try:
        if method == "GET":
            r = sess.get(_url(path, params), timeout=timeout)
        elif method == "POST":
            url = _url(path, params) if params else (K.BASE + "/api" + path)
            r = sess.post(url, json=body, timeout=timeout)
        elif method == "PUT":
            r = sess.put(K.BASE + "/api" + path, json=body, timeout=timeout)
        else:
            r = sess.delete(K.BASE + "/api" + path, timeout=timeout)
    except Exception as e:
        return {"status": 0, "srv": -1, "rtt": int((time.time() - t0) * 1000),
                "body": None, "err": str(e)[:200], "src": "net", "headers": {}}
    try:
        j = r.json()
    except Exception:
        j = r.text[:400]
    srv = K.srv_ms(r)
    return {"status": r.status_code, "srv": srv, "rtt": int((time.time() - t0) * 1000),
            "body": j, "err": "", "src": _classify(r.status_code, srv, r.text[:200]),
            "headers": {k: v for k, v in r.headers.items() if k.startswith("X-")}}


def _params_for(path, base):
    ov = PARAM_OVERRIDE.get(path)
    if not ov:
        return base
    m = {x["n"]: dict(x) for x in base}
    for k, v in ov.items():
        m.setdefault(k, {"n": k, "req": False})["d"] = v
    return list(m.values())


def run(full=True):
    admin_tok, admin_role = _login(ADMIN_USER, ADMIN_PASS)
    demo_tok, demo_role = _login(K.USER, K.PASS)
    A, D, N = _sess(admin_tok), _sess(demo_tok), _sess()
    params = _openapi_params()
    rs = routes()
    res = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": K.BASE,
           "roles": {"admin": admin_role, "demo": demo_role},
           "reads": [], "writes": [], "checks": [], "notes": []}

    # 先 warm：首访会触发冷算/重建，把冷态算进"链路通不通"时会被误判成慢/空
    A.get(K.BASE + "/api/dashboard/boot?channel=jd", timeout=180)

    for r in rs:
        if r["m"] != "GET":
            continue
        ps = _params_for(r["p"], params.get(("GET", r["p"]), []))
        if any(pr["req"] for pr in ps):
            res["reads"].append({"p": r["p"], "skip": "required path param"})
            continue
        base = {"p": r["p"], "admin": probe(A, "GET", r["p"], ps)}
        if full and r["p"] not in IDENTITY_SKIP:
            d = probe(D, "GET", r["p"], ps)
            if d["status"] == 503:          # 边缘保护偶发：隔 1s 复测一次，仍 503 才记边缘
                time.sleep(1.0)
                d2 = probe(D, "GET", r["p"], ps)
                if d2["status"] != 503:
                    d = d2
            base["demo"] = d
        if full and r["p"] in ANON_SAMPLE:
            base["anon"] = probe(N, "GET", r["p"], ps)
        res["reads"].append(base)

    # 写端点：只跑 safe / guarded
    rule_id = _first_rule_id(A)
    for r in rs:
        if r["m"] == "GET":
            continue
        kind = WRITE_SPEC.get(r["p"], ("guarded", ""))[0]
        if r["p"].startswith("/cron/"):
            continue
        if kind == "skip":
            res["writes"].append({"p": r["p"], "m": r["m"], "skip": WRITE_SPEC[r["p"]][1]})
            continue
        path = r["p"]
        for k, v in FAKE.items():
            path = path.replace("{%s}" % k, str(v))
        if kind == "guarded":
            for k, v in FAKE.items():
                path = path.replace("{%s}" % k, str(v))
            res["writes"].append({"p": r["p"], "m": r["m"], "kind": "guarded",
                                  "r": probe(A, r["m"], path, body={})})
        else:
            res["writes"].append({"p": r["p"], "m": r["m"], "kind": kind, "todo": True})

    # ── 安全写闭环 ──
    res["flows"] = []
    res["flows"].append(_flow_log(A, D))
    res["flows"].append(_flow_export(A))
    res["flows"].append(_flow_rule_test(A, rule_id))
    res["flows"].append(_flow_dupcheck(A))
    res["flows"].append(_flow_seasons(A))

    res["checks"] = consistency(A, res)
    p = os.path.join(OUT, "%s_e2e.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    _print(res)
    print("[out] %s" % p)
    return res


def _first_rule_id(s):
    r = s.get(K.BASE + "/api/rules?channel=jd", timeout=60)
    try:
        d = r.json().get("data") or []
        if d:
            return d[0].get("id")
    except Exception:
        pass
    return None


def _flow_log(A, D):
    """前端上报 → quality_logs 可查（跨消费方：前端 ↔ 日志面）。"""
    body = {"level": "info", "message": "e2e-chain smoke %s" % time.strftime("%H%M%S"),
            "url": "e2e://chain", "stack": ""}
    w = probe(D, "POST", "/logs/frontend", body=body)
    q = probe(A, "GET", "/quality-logs", [{"n": "limit", "d": 50, "req": False},
                                          {"n": "scope", "d": "frontend", "req": False}])
    hit = False
    try:
        d = q["body"].get("data") or []
        rows = d if isinstance(d, list) else (d.get("items") or d.get("rows") or [])
        hit = any("e2e-chain smoke" in json.dumps(x, ensure_ascii=False) for x in rows)
    except Exception:
        pass
    return {"name": "前端上报→日志查询", "ok": w["status"] == 200 and q["status"] == 200 and hit,
            "detail": {"write": w["status"], "read": q["status"], "found": hit}}


def _flow_export(A):
    """补货建议 → 导出任务 → 下载 CSV，行数与接口 total 对比（跨消费方：API ↔ 导出）。"""
    r = probe(A, "POST", "/exports", params=[{"n": "type", "d": "replen", "req": False},
                                             {"n": "channel", "d": "jd", "req": False},
                                             {"n": "mode", "d": "bbcc", "req": False}])
    tid = ""
    try:
        d = r["body"] if isinstance(r["body"], dict) else {}
        tid = (d.get("data") or {}).get("task_id") or d.get("task_id") or ""
    except Exception:
        pass
    st, rows, fname, parts = None, None, "", []
    if tid:
        for _ in range(40):
            q = probe(A, "GET", "/exports/status", [{"n": "task_id", "d": tid, "req": True},
                                                    {"n": "page_size", "d": 10000, "req": False}])
            try:
                d = q["body"] if isinstance(q["body"], dict) else {}
                st = d.get("status") or st
                rows = d.get("total", rows)
                fname = d.get("filename") or fname
                parts = d.get("parts") or parts
            except Exception:
                pass
            if st in ("done", "completed", "ok", "error", "failed"):
                break
            time.sleep(1.0)
        # done 与 parts 不是同一时刻落库(实测: 状态先到、parts 后到几百毫秒),
        #   命中终态却拿到空 parts 会掉进旧 xlsx 分支 → 假红。补读一次终态。
        if not parts and st in ("done", "completed", "ok"):
            try:
                _q2 = probe(A, "GET", "/exports/status", [{"n": "task_id", "d": tid, "req": True}])
                _d2 = _q2["body"] if isinstance(_q2["body"], dict) else {}
                parts = _d2.get("parts") or parts
                fname = _d2.get("filename") or fname
            except Exception:
                pass
    dl = probe(A, "GET", "/exports/download/%s" % (fname or FAKE["filename"]))
    got = 0
    if parts:
        # 2026-11-16 §15.201 起导出改为分片 CSV（每片 2000 行，仅首片带表头）:
        #   status 不再回 total, 必须逐片下载数行。裸名整体下载仍可用, 但数行会
        #   撞 2.2MB 一次性读 —— 逐片更稳且能定位是第几片断的。
        tot = 0
        for _i, _p in enumerate(parts):
            try:
                b = A.get(K.BASE + "/api/exports/download/%s" % _p, timeout=120).content
                tot += b.count(b"\n") - (1 if _i == 0 else 0)
            except Exception:
                tot = -1
                break
        got = tot
        rows = got if rows is None else rows
    elif dl["status"] == 200:
        # 老格式: 单文件 xlsx（read_only 下 max_row 是估算值，逐行数才准）
        try:
            import io as _io
            from openpyxl import load_workbook
            b = A.get(K.BASE + "/api/exports/download/%s" % (fname or FAKE["filename"]), timeout=90).content
            wb = load_workbook(_io.BytesIO(b), read_only=True)
            n = sum(1 for _ in wb.active.iter_rows(values_only=True))
            got = max(0, n - 1)
            wb.close()
        except Exception:
            got = -1
    rep = probe(A, "GET", "/insights/replenishment",
                [{"n": "channel", "d": "jd", "req": False}, {"n": "mode", "d": "bbcc", "req": False},
                 {"n": "page", "d": 1, "req": False}, {"n": "page_size", "d": 1, "req": False}])
    total = None
    try:
        total = int((rep["body"].get("data") or {}).get("total") or 0)
    except Exception:
        pass
    ok = bool(tid) and st in ("done", "completed", "ok") and dl["status"] == 200 and got > 0
    return {"name": "补货接口→导出→下载", "ok": ok,
            "detail": {"task": tid, "status": st, "rows": rows, "csv_lines": got,
                       "api_total": total, "filename": fname, "download": dl["status"]}}


def _flow_rule_test(A, rid):
    if not rid:
        return {"name": "规则试算", "ok": None, "detail": {"reason": "no rule"}}
    r = probe(A, "POST", "/rules/%s/test" % rid, body={"sku": "SKU-00001-J", "channel": "jd"})
    return {"name": "规则试算（不落库）", "ok": r["status"] == 200 and (r["body"] or {}).get("ok") is not False,
            "detail": {"rule": rid, "status": r["status"], "body": str(r["body"])[:200]}}


def _flow_dupcheck(A):
    """导入查重：小 CSV（中文表头），只解析不落库。"""
    csv = "订单号,SKU,69码,数量,金额\nE2E-0001,SKU-00001-J,6900000000001,1,9.9\n"
    import requests
    try:
        r = requests.post(K.BASE + "/api/cleansing/dup-check", files={"file": ("e2e.csv", csv.encode("utf-8"), "text/csv")},
                          data={"target": "orders", "channel": "jd"}, timeout=90,
                          headers={"X-Auth-Token": A.headers["X-Auth-Token"]})
        j = r.json()
        return {"name": "导入查重（只读）", "ok": r.status_code == 200 and j.get("ok") is not False,
                "detail": {"status": r.status_code, "body": str(j)[:300]}}
    except Exception as e:
        return {"name": "导入查重（只读）", "ok": False, "detail": {"err": str(e)[:200]}}


def _flow_seasons(A):
    """季节系数写 → 读回 → 读消费方 → 还原（跨消费方：配置 ↔ 看板/补货）。"""
    g0 = probe(A, "GET", "/replenishment-config/seasons", [{"n": "channel", "d": "jd", "req": False},
                                                           {"n": "mode", "d": "bbcc", "req": False}])
    try:
        cur = (g0["body"].get("data") or {})
    except Exception:
        cur = {}
    items = cur if isinstance(cur, list) else (cur.get("items") if isinstance(cur, dict) else [])
    if not items:
        return {"name": "季节系数写读回滚", "ok": None, "detail": {"reason": "无 items"}}
    orig = json.loads(json.dumps(items))
    probe_items = json.loads(json.dumps(items))
    try:
        probe_items[0]["factor"] = round(float(probe_items[0].get("factor") or 1.5) + 0.0001, 4)
    except Exception:
        return {"name": "季节系数写读回滚", "ok": None, "detail": {"reason": "factor 非数值"}}
    def _gen():
        probe(A, "GET", "/insights/replenishment",
              [{"n": "channel", "d": "jd"}, {"n": "mode", "d": "bbcc"},
               {"n": "page", "d": 1}, {"n": "page_size", "d": 1}])
        for h in (K.heads() or []):
            if str(h.get("kind")) == "repl":
                try:
                    return int(h.get("gen") or 0)
                except Exception:
                    return None
        return None

    gen_before = _gen()
    w1 = probe(A, "PUT", "/replenishment-config/seasons",
               body={"channel": "jd", "mode": "bbcc", "items": probe_items})
    g1 = probe(A, "GET", "/replenishment-config/seasons",
               [{"n": "channel", "d": "jd", "req": False}, {"n": "mode", "d": "bbcc", "req": False}])
    got1 = None
    try:
        gd = g1["body"].get("data") or []
        got1 = float((gd if isinstance(gd, list) else gd.get("items"))[0]["factor"])
    except Exception:
        pass
    # 还原（原样）
    w2 = probe(A, "PUT", "/replenishment-config/seasons",
               body={"channel": "jd", "mode": "bbcc", "items": orig})
    g2 = probe(A, "GET", "/replenishment-config/seasons",
               [{"n": "channel", "d": "jd", "req": False}, {"n": "mode", "d": "bbcc", "req": False}])
    got2 = None
    try:
        gd = g2["body"].get("data") or []
        got2 = float((gd if isinstance(gd, list) else gd.get("items"))[0]["factor"])
    except Exception:
        pass
    want1 = float(probe_items[0]["factor"])
    gen_after = None
    for _ in range(20):
        gen_after = _gen()
        if gen_before is not None and gen_after is not None and gen_after > gen_before:
            break
        time.sleep(1.5)
    ok = (w1["status"] == 200 and got1 == want1 and w2["status"] == 200
          and got2 == float(orig[0].get("factor") or 0))
    return {"name": "季节系数写→读回→还原", "ok": ok,
            "detail": {"write": w1["status"], "read": got1, "want": want1,
                       "rollback": w2["status"], "read_back": got2,
                       "orig": float(orig[0].get("factor") or 0),
                       "repl_gen": [gen_before, gen_after],
                       "gen_advanced": (gen_before is not None and gen_after is not None
                                        and gen_after > gen_before)}}


# ── 跨消费方一致性 ────────────────────────────────────────────────────────
def _num(body, *path):
    x = body
    for k in path:
        if isinstance(x, dict):
            x = x.get(k)
        else:
            return None
    try:
        return int(x) if isinstance(x, (int, float)) else None
    except Exception:
        return None


def consistency(A, res):
    """同源数值必须相等；口径差异单独列（不混判）。"""
    out = []

    def one(key, desc, a, b, tol=0, op="eq"):
        if a is None or b is None:
            out.append({"key": key, "desc": desc, "ok": None, "a": a, "b": b})
            return
        ok = (a >= b) if op == "ge" else (abs(a - b) <= tol)
        out.append({"key": key, "desc": desc, "ok": ok, "a": a, "b": b})

    # 1) 同一物化的两个入口：boot vs 各自路由
    boot = probe(A, "GET", "/dashboard/boot", [{"n": "channel", "d": "jd"}])
    summ = probe(A, "GET", "/dashboard/summary", [{"n": "channel", "d": "jd"}, {"n": "part", "d": "main"}])
    aux = probe(A, "GET", "/dashboard/aux", [{"n": "channel", "d": "jd"}])
    sr = probe(A, "GET", "/dashboard/stock-risk", [{"n": "channel", "d": "jd"}])
    cnt = probe(A, "GET", "/alerts/counts", [{"n": "channel", "d": "jd"}])
    us = probe(A, "GET", "/dashboard/unit-state", [{"n": "channel", "d": "jd"}])
    rep = probe(A, "GET", "/insights/replenishment",
                [{"n": "channel", "d": "jd"}, {"n": "mode", "d": "bbcc"},
                 {"n": "page", "d": 1}, {"n": "page_size", "d": 1}])

    bd = boot["body"].get("data") or {}
    one("boot.summary == summary", "首屏聚合与独立接口同一物化",
        _num(bd, "summary", "summary", "active_alerts"), _num(summ["body"], "data", "summary", "active_alerts"))
    one("boot.aux == aux", "首屏 aux 与独立 aux 同一物化",
        _num(bd, "aux", "alertCounts", "total"), _num(aux["body"], "data", "alertCounts", "total"))
    one("boot.risk == stock-risk", "首屏断货与独立断货同一物化",
        _num(bd, "stock_risk", "bcTotal"), _num(sr["body"], "data", "bcTotal"))

    # 2) 同义但异源：物化 vs 实时。评估分片续跑时 alerts 本就在变（CONTEXT §10.9），
    #    判"3 次采样内差值收敛且 <=0.5%"，而不是拿某一瞬间判死。
    a1 = _num(summ["body"], "data", "summary", "active_alerts")
    samples, fail_samples = [], []
    for _ in range(3):
        samples.append(_num(cnt["body"], "data", "total"))
        if cnt["status"] != 200:
            fail_samples.append({"status": cnt["status"], "src": cnt["src"]})
        time.sleep(2.0)
        cnt = probe(A, "GET", "/alerts/counts", [{"n": "channel", "d": "jd"}])
    samples.append(_num(cnt["body"], "data", "total"))
    # 评估正在写 alerts 时，两边本就不可比（CONTEXT §10.9 收敛期 1419→22072）。
    # 这种情况如实标 DEGRADED（ok=None），既不假装 PASS，也不误判成缺陷。
    h = probe(A, "GET", "/health", [{"n": "detail", "d": 1}])
    running = []
    # 2026-11-16 §15.205: 规则评估已改接力(sync_tasks + eval_tasks), **不在 pipeline_runs**,
    #   所以 /health.last_runs 里 daily_rules 恒为 skipped/ok —— 只看它会漏判"评估正在写
    #   alerts", 于是把收敛期的计数增长误报成缺陷。真源是 /rules/eval-status(running 计数,
    #   内部就是 eval_live_cond)。两路都看, 任一为真即 DEGRADED。
    try:
        for x in (h["body"] or {}).get("last_runs") or []:
            if x.get("status") == "running" and str(x.get("pipeline")) in ("rule_eval", "daily_rules"):
                running.append(x.get("scope"))
    except Exception:
        pass
    try:
        _es = probe(A, "GET", "/rules/eval-status")
        if int((_es["body"] or {}).get("data", {}).get("running") or 0) > 0:
            running.append("eval_tasks")
    except Exception:
        pass
    diffs = [abs(x - a1) for x in samples if x is not None and a1 is not None]
    worst = max(diffs) if diffs else None
    if running:
        ok, desc = None, "评估运行中(%s)，计数不可比 → DEGRADED" % ",".join(map(str, running))
    elif fail_samples or any(x is None for x in samples):
        ok, desc = None, "采样失败(%s)，本轮不判" % fail_samples
    else:
        ok, desc = (worst == 0), "物化计数 vs 实时 COUNT（静默期要求严格相等）"
    out.append({"key": "summary.active_alerts vs alerts/counts", "desc": desc,
                "ok": ok, "a": a1, "b": samples,
                "detail": {"worst": worst, "running": running, "fail_samples": fail_samples}})

    # 3) 断货：stock-risk 的 bc+c 合计 与 unit_state.risk_rows（同 gen 发布，行数应相等）
    _rs = str((us["body"].get("data") or {}).get("risk_stat") or "")
    _sum = 0
    for seg in _rs.split(";"):
        if ":" in seg:
            try:
                _sum += int(seg.split(":")[1].split("/")[0])
            except Exception:
                pass
    one("stock-risk(bc+c) == unit_state.risk_rows", "断货快照行数与判定物化（同 gen）",
        _sum or None, _num(us["body"], "data", "risk_rows"))

    # 4) 低库存/断货：summary vs aux.stockOverview（同批判定单元）
    one("summary.low == aux.stockOverview.low", "低库存计数同一批单元",
        _num(summ["body"], "data", "summary", "low_stock_count"),
        _num(aux["body"], "data", "stockOverview", "low_stock_count"))
    one("summary.out == aux.stockOverview.out", "断货计数同一批单元",
        _num(summ["body"], "data", "summary", "out_of_stock_count"),
        _num(aux["body"], "data", "stockOverview", "out_of_stock_count"))

    # 5) 补货接口 total 与列表长度（分页一致性）
    pd = rep["body"].get("data") or {}
    one("repl.total >= len(items)", "补货 total 不小于本页行数",
        _num(rep["body"], "data", "total"), len(pd.get("items") or []), op="ge")

    # 6) 渠道隔离：jd 与 other 不能是同一份数
    o = probe(A, "GET", "/dashboard/summary", [{"n": "channel", "d": "other"}, {"n": "part", "d": "main"}])
    jg = _num(summ["body"], "data", "summary", "gmv")
    og = _num(o["body"], "data", "summary", "gmv")
    out.append({"key": "jd/other 渠道隔离", "desc": "两渠道 GMV 不应相等（同源串仓）",
                "ok": (jg is not None and og is not None and jg != og), "a": jg, "b": og})

    # 7) 身份：demo 写必须 403
    dw = probe(_sess(_login(K.USER, K.PASS)[0]), "PUT", "/replenishment-config",
               body={"key": "x", "value": "y"})
    out.append({"key": "demo 写 = 403", "desc": "只读账号不得写",
                "ok": dw["status"] == 403, "a": dw["status"], "b": 403})
    # 8) 匿名读受保护接口 = 401
    an = probe(_sess(), "GET", "/dashboard/summary", [{"n": "channel", "d": "jd"}])
    out.append({"key": "anon 读 = 401", "desc": "未登录不得读业务接口",
                "ok": an["status"] == 401, "a": an["status"], "b": 401})
    # body 可能是 str(非 JSON / 解压失败) —— checks 模式复用旧结果时会撞到, 不能假设 dict
    _d = lambda b: b if isinstance(b, dict) else {}
    res["snapshot"] = {"boot": bd, "summary": _d(summ["body"]).get("data"),
                       "aux": _d(aux["body"]).get("data"), "counts": _d(cnt["body"]).get("data"),
                       "stock_risk": _d(sr["body"]).get("data"), "unit_state": _d(us["body"]).get("data"),
                       "repl": {k: pd.get(k) for k in ("total", "gen", "built_at", "__swr")}}
    return out


def _print(res):
    print("\n== 读端点（admin）==")
    bad = []
    for x in res["reads"]:
        if "skip" in x:
            print("  SKIP %-46s %s" % (x["p"], x["skip"]))
            continue
        a = x["admin"]
        flag = "OK " if a["status"] == 200 else "BAD"
        if a["status"] != 200:
            bad.append(x)
        tag = ""
        try:
            dd = (a["body"] or {}).get("data") or {}
            if isinstance(dd, dict) and dd.get("skipped"):
                tag = "skip:" + str(dd.get("reason"))[:22]
            elif x["p"].startswith("/cron/"):
                tag = "ran"
        except Exception:
            pass
        print("  %s %-46s %3d srv=%-5s src=%-11s %s" % (
            flag, x["p"], a["status"], a["srv"], a["src"], tag or ("" if a["status"] == 200 else str(a["body"])[:110])))
    print("\n== 一致性 ==")
    for c in res["checks"]:
        m = "?" if c["ok"] is None else ("✓" if c["ok"] else "✗")
        print("  %s %-42s a=%s b=%s  %s" % (m, c["key"], c["a"], c["b"], c["desc"]))
    print("\n== 写链路 ==")
    for f in res.get("flows", []):
        m = "?" if f["ok"] is None else ("✓" if f["ok"] else "✗")
        print("  %s %-24s %s" % (m, f["name"], json.dumps(f["detail"], ensure_ascii=False)[:180]))


def selftest():
    """判据反证：喂假数据，锁住"不会把错误判成通过"。"""
    cases = []

    def ck(name, got, want):
        cases.append({"name": name, "ok": got == want, "got": got, "want": want})

    ck("空 data 判空", _empty({"ok": True, "data": []}), True)
    ck("非空 data 不判空", _empty({"ok": True, "data": [{"a": 1}]}), False)
    ck("ok:false 不判空", _empty({"ok": False, "data": []}), False)
    ck("零计数判空", _empty({"ok": True, "data": {"total": 0, "items": []}}), True)
    ck("非零计数不判空", _empty({"ok": True, "data": {"total": 3, "items": []}}), False)
    ck("HTML 判 edge", _classify(502, -1, "<html>Tencent</html>"), "edge")
    ck("应用 500 无 srv 判 app_uncaught", _classify(500, -1, '{"detail":1}'), "app_uncaught")
    ck("应用 200 带 srv 判 app", _classify(200, 33, "{}"), "app")
    ck("网络失败判 net", _classify(0, -1, None), "net")
    ck("前端动态段归一", norm("/api/orders/123"), "/orders/{id}")
    ck("路由模板匹配", route_match("/api/inventory/archive", {"/inventory/archive"}), "/inventory/archive")
    ok = all(c["ok"] for c in cases)
    for c in cases:
        print("  %s %-34s got=%r want=%r" % ("✓" if c["ok"] else "✗", c["name"], c["got"], c["want"]))
    print("selftest: %d/%d" % (sum(1 for c in cases if c["ok"]), len(cases)))
    return 0 if ok else 1


def report():
    files = sorted(glob.glob(os.path.join(OUT, "*_e2e.json")))
    if not files:
        print("没有 run 结果")
        return 1
    d = json.load(open(files[-1], encoding="utf-8"))
    lines = ["# 全链路验收 %s" % d["at"], "",
             "- 目标: %s" % d["base"], "- 身份: %s" % d["roles"], ""]
    lines.append("## 读端点")
    lines.append("| 端点 | admin | demo | anon | srv(ms) | 来源 |")
    lines.append("|---|---|---|---|---|---|")
    for x in d["reads"]:
        if "skip" in x:
            continue
        a = x["admin"]
        de = (x.get("demo") or {}).get("status", "-")
        an = (x.get("anon") or {}).get("status", "-")
        lines.append("| `%s` | %s | %s | %s | %s | %s |" % (x["p"], a["status"], de, an, a["srv"], a["src"]))
    lines += ["", "## 一致性", "| 判据 | a | b | 结果 |", "|---|---|---|---|"]
    for c in d["checks"]:
        lines.append("| %s | %s | %s | %s |" % (c["key"], c["a"], c["b"],
                                                "?" if c["ok"] is None else ("PASS" if c["ok"] else "FAIL")))
    lines += ["", "## 写链路", "| 链路 | 结果 | 明细 |", "|---|---|---|"]
    for f in d.get("flows", []):
        lines.append("| %s | %s | `%s` |" % (f["name"],
                                             "?" if f["ok"] is None else ("PASS" if f["ok"] else "FAIL"),
                                             json.dumps(f["detail"], ensure_ascii=False)[:220]))
    print("\n".join(lines))
    return 0


def main():
    ap = argparse.ArgumentParser(description="全链路消费方验收")
    ap.add_argument("mode", nargs="?", default="inventory",
                    choices=["inventory", "run", "checks", "report", "selftest"])
    ap.add_argument("--no-identities", action="store_true", help="run 时只跑 admin（省时间）")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.mode == "inventory":
        inventory(); return 0
    if a.mode == "selftest":
        return selftest()
    if a.mode == "report":
        return report()
    if a.mode == "checks":
        tok, _ = _login(ADMIN_USER, ADMIN_PASS)
        A = _sess(tok)
        res = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": K.BASE, "reads": [], "writes": [], "flows": []}
        res["checks"] = consistency(A, res)
        for c in res["checks"]:
            m = "?" if c["ok"] is None else ("✓" if c["ok"] else "✗")
            print("  %s %-42s a=%s b=%s  %s" % (m, c["key"], c["a"], c["b"], c["desc"]))
        p = os.path.join(OUT, "%s_e2e_checks.json" % time.strftime("%Y%m%d_%H%M%S"))
        json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print("[out] %s" % p)
        return 0
    run(full=not a.no_identities)
    return 0


if __name__ == "__main__":
    sys.exit(main())
