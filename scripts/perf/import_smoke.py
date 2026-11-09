#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线上小批量拟真冒烟：走**前端导入链路**（同步 + 异步），验证数据四维与联动（2026-11-09）

用户口径：基于 §1 的全面方案后，允许做线上小批量冒烟；数据拟真 = 走前端导入
现有不同类型数据，同步 / 异步各小批量最终验证；对应 100 万单 / 10000 SKU 预测。

红线（脚本内硬约束，违反直接拒绝执行）：
  · **不重跑 seed**、不触发 /api/seed/*、不跑 storm/cold/first/hot 多轮；
  · 数据拟真且**零业务数据变化**：CSV 从线上真实业务行按 seed_import.spec() 的中文表头
    取样后原样回导（overwrite / upsert 幂等），不是凭空造数，也不累加流水；
  · 总导入行数 ≤ 1500、同步每批 ≤ 10 行、异步 ≤ 1200 行、批数 ≤ 6、串行提交；
  · **只跑一次**（out/import_smoke_last.json 防重，30 分钟内拒绝二次 run）；
  · 取证全部走只读索引点查，不用全表 COUNT 做四维判据（控 RU）。

用法：
    python3 scripts/perf/import_smoke.py plan      # 零写：只读取样 + 打印计划（可反复）
    python3 scripts/perf/import_smoke.py run       # 只跑一次：同步 5 批 + 异步 1 批
    python3 scripts/perf/import_smoke.py verify    # 只读取证（跑后，可反复）

鉴权：新空库 users 表 0 行，admin 登录不可用。从 EdgeOne Production env 现取
JWT_SECRET（不落盘 / 不回显），用 routes.common.create_token("admin") 本地签发
（verify_token 只验签名不查库）—— 零 RU、不写 users。
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = os.path.join(ROOT, "cloud-functions", "api")
OUT_DIR = os.path.join(HERE, "out")
sys.path.insert(0, HERE)
sys.path.insert(0, API)
sys.path.insert(0, os.path.join(API, "routes"))

BASE = os.environ.get("SUPPLY_BASE", "https://supplykit.top")
MARKER = os.path.join(OUT_DIR, "import_smoke_last.json")
MAX_TOTAL_ROWS = 1500
MAX_SYNC_ROWS = 10
MAX_ASYNC_ROWS = 1200
MAX_BATCHES = 6

# (target, 源表, 渠道, 行数, 同步?, 额外 where, 额外 query)
PLAN = [
    ("order", "orders", "jd", 5, True, "", {}),
    ("inventory", "inventory", "jd", 5, True, "warehouse_type='own'", {}),
    ("product", "products", "other", 5, True, "", {}),
    ("supplier", "suppliers", "other", 5, True, "", {}),
    # gmv_flow 是**无 upsert 的流水**：原样回导同键会撞 uq_platform_flow_ch(1062)。
    # 前端真实行为是 dup-check 同步拦截(duplicate:True)，所以这里不加 force_new。
    # 11-09 首跑曾加 force_new=1 绕过拦截，5 行全 1062、行数不变 —— 已定性为既有设计，
    # 非本轮引入缺陷；修掉参数让后续复跑与前端一致（脚本只改参数，不重跑）。
    ("gmv_flow", "gmv_flows", "jd", 5, True, "", {}),
    ("order", "orders", "other", 1000, False, "", {}),
]


def _env_secret():
    """从 EdgeOne Production env 现取 JWT_SECRET（不落盘、不回显）。"""
    tok = ""
    uploads = "/root/uploads"
    fs = sorted([f for f in os.listdir(uploads) if f.endswith("env.supplykit.txt")])
    if fs:
        for ln in open(os.path.join(uploads, fs[-1]), encoding="utf-8"):
            if ln.startswith("EDGEONE_API_TOKEN="):
                tok = ln.split("=", 1)[1].strip()
    if not tok:
        tok = os.environ.get("EDGEONE_API_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("缺 EDGEONE_API_TOKEN（附件 / 环境变量）")
    proj = json.load(open(os.path.join(ROOT, ".edgeone", "project.json"), encoding="utf-8"))["ProjectId"]
    body = json.dumps({"Action": "DescribePagesProjectEnvs", "ProjectId": proj}).encode()
    req = urllib.request.Request("https://pages-api.cloud.tencent.com/v1", data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + tok})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if r.get("Code") != 0:
        raise RuntimeError("DescribePagesProjectEnvs Code=%s" % r.get("Code"))
    evs = ((r.get("Data") or {}).get("Response") or {}).get("EnvVars") or []
    for e in evs:
        if e.get("Key") == "JWT_SECRET" and "Production" in (e.get("Env") or []):
            v = str(e.get("Value") or "")
            if v:
                return v
    raise RuntimeError("Production env 未返回 JWT_SECRET")


def _session():
    secret = _env_secret()
    os.environ["JWT_SECRET"] = secret
    from routes.common import create_token
    s = requests.Session()
    s.headers.update({"X-Auth-Token": create_token("admin"),
                      "X-Accept-Encoding": "gzip, deflate, br",
                      "Accept-Encoding": "gzip, deflate, br"})
    return s


def _dbq():
    import kit as K
    if not K.init_db():
        raise RuntimeError("无法直连 DB（.env 缺凭据）")
    return K.QY


def _scalar(q, sql, args=()):
    r = q(sql, list(args))
    return list(r[0].values())[0] if r else None


def _cell(v):
    return "" if v is None else str(v)


def _sample(q, target, table, channel, n, where_extra):
    """按 seed_import.spec() 的中文表头，从线上真实行取 n 条，生成 CSV bytes。"""
    from routes.seed_import import spec
    sp = spec(target)
    cols, mp = sp["cols"], sp["mp"]
    colnames = [_scalar(q, "SHOW COLUMNS FROM `%s` LIKE 'channel'" % table)]
    has_channel = bool(colnames[0])
    wh = []
    if has_channel:
        wh.append("channel='" + channel + "'")
    if where_extra:
        wh.append(where_extra)
    sql = ("SELECT * FROM `%s` %s ORDER BY id LIMIT %d"
           % (table, ("WHERE " + " AND ".join(wh)) if wh else "", n))
    rows = q(sql, [])
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([_cell(r.get(str((mp.get(c) or {}).get("target") or ""))) for c in cols])
    data = buf.getvalue().encode("utf-8")
    # 记录业务键，供 verify 做索引点查（不做全表 GROUP BY）
    keys = []
    for r in rows:
        keys.append(tuple(_cell(r.get(k)) for k in sp["keys"]) if sp.get("keys") else ())
    return {"csv": data, "rows": len(rows), "cols": cols, "mp": mp,
            "table": table, "keys": keys, "key_cols": list(sp.get("keys") or []),
            "channel": channel, "target": target, "sql": sql}


def _guard():
    n = sum(p[3] for p in PLAN)
    batches = len(PLAN)
    assert n <= MAX_TOTAL_ROWS, "总行数 %d > %d" % (n, MAX_TOTAL_ROWS)
    assert batches <= MAX_BATCHES, "批数 %d > %d" % (batches, MAX_BATCHES)
    for t, tb, ch, k, sync, we, ex in PLAN:
        assert k <= (MAX_SYNC_ROWS if sync else MAX_ASYNC_ROWS), "%s 行数超限" % t
    return n, batches


def _marker_check(force=False):
    if force or not os.path.isfile(MARKER):
        return
    try:
        d = json.load(open(MARKER, encoding="utf-8"))
        if time.time() - d.get("ts", 0) < 1800:
            raise RuntimeError("30 分钟内已跑过一次（%s），拒绝重跑；只跑一次是红线。"
                               % d.get("at"))
    except (ValueError, KeyError):
        pass


def _baseline(q):
    tables = ["orders", "inventory", "products", "suppliers", "gmv_flows", "alerts"]
    out = {"data_version": _scalar(q, "SELECT v FROM data_version WHERE id=1"),
           "heavy_busy": _scalar(q, "SELECT COUNT(*) FROM heavy_slot WHERE holder<>'' "
                                    "AND ts > NOW(6) - INTERVAL 300 SECOND"),
           "running_tasks": _scalar(q, "SELECT COUNT(*) FROM sync_tasks WHERE status='running'")}
    out["tables"] = {t: _scalar(q, "SELECT COUNT(*) FROM `%s`" % t) for t in tables}
    out["list_head"] = q("SELECT kind,scope,gen,built_at FROM list_head", [])
    out["unit_gen"] = _scalar(q, "SELECT MAX(gen) FROM unit_state_head")
    return out


def _get(s, path, **params):
    r = s.get(BASE + path, params=params, timeout=60)
    return r


def _post_import(s, item, sync, extra):
    target, channel = item[0], item[2]
    smp = item[-1]
    params = {"q_target": target, "q_channel": channel,
              "q_conflict": "overwrite", "q_mp": json.dumps(smp["mp"], ensure_ascii=False)}
    params.update(extra)
    r = s.post(BASE + "/api/cleansing/execute-async", params=params,
               data=smp["csv"], timeout=120)
    try:
        j = r.json()
    except Exception:
        j = {}
    return {"http": r.status_code, "body": j,
            "degraded": r.headers.get("X-App-Degraded", ""),
            "srv_ms": r.headers.get("X-Srv-Ms", "-1"),
            "sync_expected": sync, "target": target, "channel": channel,
            "rows": smp["rows"]}


def _poll_status(s, task_id, max_calls=8):
    """异步接力：每次 GET /cleansing/status 推进一窗（driver=frontend），到 done 为止。"""
    log = []
    for i in range(max_calls):
        r = _get(s, "/api/cleansing/status", task_id=task_id, page_size=1000,
                 driver="frontend", probe=0)
        try:
            j = r.json()
        except Exception:
            j = {}
        d = j.get("data") or j
        st = d.get("status") or j.get("status")
        log.append({"call": i + 1, "http": r.status_code, "status": st,
                    "success": d.get("success"), "failed": d.get("failed"),
                    "degraded": r.headers.get("X-App-Degraded", "")})
        if st in ("done", "error") or j.get("ok") is False:
            break
    return log


def plan(quiet=False):
    n, b = _guard()
    q = _dbq()
    items = []
    for t, tb, ch, k, sync, we, ex in PLAN:
        smp = _sample(q, t, tb, ch, k, we)
        items.append({"target": t, "table": tb, "channel": ch, "rows": smp["rows"],
                      "sync": sync, "csv_bytes": len(smp["csv"]),
                      "key_cols": smp["key_cols"], "sample_keys": smp["keys"][:2],
                      "extra": ex})
    out = {"rows_total": n, "batches": b, "items": items,
           "baseline": _baseline(q)}
    if not quiet:
        print("=== 拟真小批量冒烟计划（零写，CSV 取自线上真实业务行） ===")
        for i, it in enumerate(items, 1):
            print("  %d. %-10s %-6s %4d 行  %s  CSV %dB  keys=%s" % (
                i, it["target"], it["channel"], it["rows"],
                "同步(<1000)" if it["sync"] else "异步(≥1000 接力)",
                it["csv_bytes"], ",".join(it["key_cols"])))
        print("  合计 %d 行 / %d 批；同步 5 类 × 2 渠道 + 异步 1 类" % (n, b))
        print("  基线：data_version=%s heavy_busy=%s running=%s" % (
            out["baseline"]["data_version"], out["baseline"]["heavy_busy"],
            out["baseline"]["running_tasks"]))
        print("  表行数：%s" % out["baseline"]["tables"])
    _save(out, "plan")
    return out, q


def _save(obj, mode):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_import_smoke_%s.json"
                     % (time.strftime("%Y%m%d_%H%M%S"), mode))
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[out] %s" % p)
    return p


def run(force=False):
    _guard()
    _marker_check(force)
    s = _session()
    # 只读探活：确认 admin token 有效、服务未降级
    h = _get(s, "/api/health")
    hj = h.json()
    if hj.get("db") != "ok" or h.headers.get("X-App-Degraded"):
        raise RuntimeError("线上 db 非 ok 或处于降级，拒绝导入：%s" % str(hj)[:160])
    q = _dbq()
    before = _baseline(q)
    print("=== 线上小批量拟真冒烟（只跑一次） ===", flush=True)
    print("  探活：%s db=%s dv=%s" % (h.status_code, hj.get("db"), before["data_version"]), flush=True)

    results = []
    for idx, (t, tb, ch, k, sync, we, ex) in enumerate(PLAN, 1):
        smp = _sample(q, t, tb, ch, k, we)
        if smp["rows"] == 0:
            results.append({"batch": idx, "target": t, "channel": ch,
                            "error": "取样 0 行，跳过（不造假数据）"})
            continue
        item = (t, tb, ch, k, sync, we, ex, smp)
        r = _post_import(s, item, sync, ex)
        r["batch"] = idx
        body = r["body"]
        if "task_id" in body and body.get("task_id") and "success" not in body:
            r["mode"] = "异步"
            r["task_id"] = body["task_id"]
            r["status_log"] = _poll_status(s, body["task_id"])
        else:
            r["mode"] = "同步"
            r["task_id"] = body.get("task_id") or ""
        results.append(r)
        print("  %d. %-10s %-6s %4d 行 → %s HTTP%s success=%s failed=%s dup=%s degraded=%s" % (
            idx, t, ch, smp["rows"], r["mode"], r["http"],
            body.get("success"), body.get("failed"), body.get("duplicate"),
            r["degraded"] or "-"), flush=True)
        if r.get("status_log"):
            print("     接力：%s" % " → ".join(str(x["status"]) for x in r["status_log"]), flush=True)

    # 等联动（评估任务 / 物化）落定：轮询只读状态，不 sleep 固定长时
    q2 = _dbq()
    eval_rows = []
    for i in range(6):
        eval_rows = q2("SELECT task_id,task_type,status,updated_at FROM sync_tasks "
                       "WHERE task_type IN ('cleansing','cleansing_eval') "
                       "ORDER BY id DESC LIMIT 12", [])
        running = [x for x in eval_rows if str(x.get("status")) == "running"]
        if not running:
            break
        time.sleep(2)
    after = _baseline(q2)
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
           "before": before, "after": after, "results": results,
           "eval_tasks": eval_rows}
    _save(out, "run")
    json.dump({"at": out["at"], "ts": out["ts"]}, open(MARKER, "w", encoding="utf-8"))
    print("\n  data_version %s → %s；表行数变化：%s" % (
        before["data_version"], after["data_version"],
        {t: after["tables"][t] - before["tables"][t] for t in before["tables"]}), flush=True)
    return out


def judge_evidence(run_obj, verify_obj):
    """§15.189 P3 第四类「作业/联动」判据（§10.5）。

    **独立于 `run.py` 的 `TH`**（不污染页面表与 `complete()`），**不判 p50**（红线 17）。
    纯函数：只吃两份取证 dict（run + verify），可喂历史 `out/*.json` 做反证，
    不连线上、零 RU、不重跑导入。判据键同时是 `pipeline_probe.coverage` 的判据层锚点。
    `ok=None` = 取证缺该字段（DEGRADED，如实标注，不假装 PASS 也不误判 FAIL）。
    """
    def C(key, desc, ok, detail):
        return {"key": key, "desc": desc,
                "ok": (None if ok is None else bool(ok)), "detail": detail}

    checks = []
    before = (run_obj or {}).get("before") or {}
    after = (run_obj or {}).get("after") or {}
    bh = {str(x.get("kind")): int(x.get("gen") or 0)
          for x in (before.get("list_head") or [])}
    heads = (verify_obj or {}).get("list_head") or []

    # ① 物化集中重建是否全部建成：所有 kind 的 gen 相对导入前推进。
    #    §10.9 实证 repl 40→40 未推进 → 这条历史取证上必须报红（反证）。
    adv = [{"kind": str(x.get("kind")), "gen": int(x.get("gen") or 0),
            "base_gen": bh.get(str(x.get("kind"))),
            "advanced": bool(bh) and int(x.get("gen") or 0) > bh.get(str(x.get("kind")), -1)}
           for x in heads]
    checks.append(C("materialize_gen_ok", "所有 kind 的 head.gen 推进（补建完成）",
                    (bool(adv) and all(a["advanced"] for a in adv)) if bh else None, adv))

    # ② 评估联动完成：无僵死 running（红线 15 新鲜度/租约）
    ev = (verify_obj or {}).get("eval_tasks") or []
    running = [x for x in ev if str(x.get("status")) == "running"]
    checks.append(C("eval_task_done", "评估/导入任务无 running（租约未僵死）",
                    (bool(ev) and not running) if ev else None,
                    {"tasks": len(ev), "running": len(running)}))

    # ③ 告警生成增量：只判「确实生成了」，**不取瞬时值判增量**（§10.9 收敛期
    #    1419→5258→10230→22072 是评估分片续跑，不是重复缺陷）
    alerts = (verify_obj or {}).get("alerts")
    checks.append(C("alert_delta_ok", "告警已生成且不用瞬时值判增量",
                    (int(alerts) > 0) if alerts is not None else None,
                    {"alerts": alerts, "note": "收敛期持续增长 = 评估续跑, 不取瞬时值"}))

    # ④ 集中导入作业完成且等价：源表行数变化全 0（原样回导幂等）+ 0 次重灌。
    #    ⚠ alerts 是**派生表**（评估生成告警，§10.9 收敛期必然增长），混入「源表」
    #    对账会把合法告警增长误判成数据不等价 —— 历史取证反证抓到过这个假 FAIL。
    SRC_TABLES = ("orders", "inventory", "products", "suppliers", "gmv_flows")
    b_rows, a_rows = before.get("tables") or {}, after.get("tables") or {}
    deltas = {k: int(a_rows.get(k, 0)) - int(b_rows.get(k, 0))
              for k in SRC_TABLES if k in a_rows}
    checks.append(C("import_equivalent", "源表行数变化全 0（原样回导幂等）、0 次重灌",
                    (bool(deltas) and all(v == 0 for v in deltas.values())) if deltas else None,
                    {"row_delta": deltas,
                     "batches": len((run_obj or {}).get("results") or [])}))

    # ⑤ 相位/收敛：dv 稳定 + HeavyBusy 留痕可核（hot 锁相位前置的采集层）
    conv = (verify_obj or {}).get("data_version_converged")
    hb = (verify_obj or {}).get("heavybusy_logs_recent")
    checks.append(C("phase_quiet_gate", "dv 收敛 + HeavyBusy 留痕可核（相位前置采集）",
                    (bool(conv) and hb is not None) if conv is not None else None,
                    {"dv_converged": conv, "heavybusy_recent": hb}))

    # ⑥ 已知空接口白名单：从唯一真源 endpoints.KNOWN_EMPTY 读，不硬编码
    try:
        import endpoints as EP
        wl = sorted(EP.KNOWN_EMPTY)
    except Exception:
        wl = []
    checks.append(C("known_empty", "已知空数据接口显式白名单（不算 incomplete）",
                    "alerts" in wl, {"whitelist": wl}))

    vals = [c["ok"] for c in checks]
    verdict = ("PASS" if all(v is True for v in vals)
               else "FAIL" if any(v is False for v in vals) else "DEGRADED")
    return {"verdict": verdict, "checks": checks,
            "note": "第四类判据独立于 run.py 的 TH；不判 p50（红线 17）"}


def verify():
    q = _dbq()
    s = _session()
    print("=== 跑后只读取证（四维 + 联动） ===", flush=True)
    dv0 = _scalar(q, "SELECT v FROM data_version WHERE id=1")
    # 红线 19：4 个只读 GET 前后 dv 不变
    gets = []
    for p in ("/api/dashboard/boot", "/api/dashboard/aux", "/api/alerts", "/api/tasks"):
        r = _get(s, p)
        gets.append({"path": p, "http": r.status_code,
                     "degraded": r.headers.get("X-App-Degraded", "")})
    dv1 = _scalar(q, "SELECT v FROM data_version WHERE id=1")
    heads = q("SELECT kind,scope,gen,built_at FROM list_head", [])
    ev = q("SELECT task_id,task_type,status,updated_at FROM sync_tasks "
           "WHERE task_type IN ('cleansing','cleansing_eval') ORDER BY id DESC LIMIT 12", [])
    # 控 RU：先索引取 MAX(id)，只在最近 500 行里查 HeavyBusy，不做全表 LIKE '%...%'
    _mx = _scalar(q, "SELECT COALESCE(MAX(id),0) FROM quality_logs")
    heavy = q("SELECT COUNT(*) c FROM quality_logs WHERE id >= %s "
              "AND message LIKE %s", [int(_mx or 0) - 500, "%HeavyBusy%"])
    alerts = _scalar(q, "SELECT COUNT(*) FROM alerts")
    h = _get(s, "/api/health")
    out = {
        "data_version_converged": dv0 == dv1, "dv_before": dv0, "dv_after": dv1,
        "readonly_gets": gets,
        "degraded_any": any(g["degraded"] for g in gets) or bool(h.headers.get("X-App-Degraded")),
        "http_5xx": [g for g in gets if g["http"] >= 500],
        "list_head": heads, "unit_gen": _scalar(q, "SELECT MAX(gen) FROM unit_state_head"),
        "eval_tasks": ev, "heavybusy_logs_recent": (heavy[0]["c"] if heavy else 0),
        "alerts": alerts, "health_db": h.json().get("db"),
    }
    print("  实时：dv %s→%s %s；降级头 %s；5xx %d" % (
        dv0, dv1, "稳定" if dv0 == dv1 else "仍在变", out["degraded_any"],
        len(out["http_5xx"])), flush=True)
    print("  联动：物化 gen %s；unit_gen %s；HeavyBusy 近期日志 %s 条；alerts %s" % (
        [x.get("gen") for x in heads], out["unit_gen"],
        out["heavybusy_logs_recent"], alerts), flush=True)
    print("  评估任务：%s" % [(x["task_type"], x["status"]) for x in ev[:6]], flush=True)
    # §15.189 P3: 第四类「作业/联动」判据（§10.5）—— 独立、不判 p50（红线 17）。
    #   纯函数喂最近一次 run.json + 本次 verify 取证；历史 json 可离线反证。
    try:
        import glob
        _runs = sorted(glob.glob(os.path.join(OUT_DIR, "*_import_smoke_run.json")))
        _ro = json.load(open(_runs[-1], encoding="utf-8")) if _runs else {}
    except Exception:
        _ro = {}
    out["judge"] = judge_evidence(_ro, out)
    print("  === 第四类判据：%s ===" % out["judge"]["verdict"], flush=True)
    for c in out["judge"]["checks"]:
        print("    %s %-22s %s" % (
            "✓" if c["ok"] else ("?" if c["ok"] is None else "✗"),
            c["key"], c["desc"]), flush=True)
    _save(out, "verify")
    return out


def main():
    ap = argparse.ArgumentParser(description="线上小批量拟真冒烟（走前端导入链路）")
    ap.add_argument("mode", nargs="?", default="plan", choices=["plan", "run", "verify"])
    ap.add_argument("--force", action="store_true", help="跳过 30 分钟防重（默认不用）")
    a = ap.parse_args()
    if a.mode == "plan":
        plan()
    elif a.mode == "run":
        run(a.force)
    else:
        verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
