"""§15.200~§15.202 线上验收（可复跑）

用法:
    python3 scripts/perf/relay_verify.py            # 全部
    python3 scripts/perf/relay_verify.py --only eval
    python3 scripts/perf/relay_verify.py --only export,delmap,sweep

四组判据（都不判"快了多少" —— 那是 run.py TH 的事）:

    eval    每日评估真的接力跑完了吗   —— 任务 done / 分片数 / pipeline_runs 是 ok 不是 timeout
    export  补货导出真的给全量了吗     —— 下载行数 == total, 且 > 2000
    delmap  8 个端点还"报成功没做事"吗  —— 不存在的 id 必须 ok:false；BC 计数两处一致
    sweep   agg-sweep 还在窗口外放行吗  —— 必须 skipped

eval 组会**清当天 daily_rules 占位**（`maintenance_log` 一行）再触发 —— 不清的话
"今天已按当前库存评估"会让兜底直接返回, 看不到接力。这是可接受的写: 占位行本来就每天重写,
且评估本身幂等（同依据重复评只是多花 RU）。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import kit as K                      # noqa: E402

ROWS = []


def rec(group, name, ok, detail=""):
    ROWS.append({"g": group, "n": name, "ok": bool(ok), "d": detail})
    print("  %s [%s] %s%s" % ("✓" if ok else "✗", group, name, ("  " + str(detail)) if detail else ""))
    return ok


def _json(r):
    try:
        return r.json()
    except Exception:
        return {}


# ── eval: 每日评估接力 ───────────────────────────────────────────────

def run_eval(admin):
    from datetime import datetime, timedelta, timezone
    import db                       # kit.init_db() 已把 api 目录放进 sys.path
    _today = (datetime.now(timezone(timedelta(hours=8)))).strftime("%Y-%m-%d")

    print("\n== eval: 清当天占位 → 触发兜底 → 观察接力")
    try:
        db.execute("DELETE FROM maintenance_log WHERE `date`=%s AND task='daily_rules'", [_today])
    except Exception as e:
        rec("eval", "清当天占位", False, str(e)[:120])
        return
    rec("eval", "清当天占位(让它能重新 claim)", True)

    t0 = time.time()
    r = admin.get(K.BASE + "/api/dashboard/summary?channel=jd&part=main&refresh=1", timeout=120)
    rec("eval", "summary 触发兜底", r.status_code == 200, "http=%s %ss" % (r.status_code, round(time.time() - t0, 1)))

    tid, st, params = "", "", {}
    for _ in range(8):
        time.sleep(2.0)
        row = K.QY("SELECT task_id, status, params FROM sync_tasks "
                   "WHERE task_type='rule_eval' ORDER BY id DESC LIMIT 1")
        if not row:
            continue
        tid = row[0]["task_id"]
        st = row[0]["status"]
        try:
            params = json.loads(row[0].get("params") or "{}")
        except Exception:
            params = {}
        if tid:
            break
    rec("eval", "已建接力任务(eval_tasks 之外才有 sync_tasks 行)", bool(tid), "task=%s status=%s" % (tid, st))
    if not tid:
        return

    # 推进: continue-tasks 有 30s 进程内限频, 所以每次间隔 35s; 每次预算 40s 能推 8~10 片
    deadline = time.time() + 20 * 60
    rounds = 0
    while time.time() < deadline and st not in ("done", "error"):
        rounds += 1
        admin.get(K.BASE + "/api/cron/continue-tasks", timeout=180)
        time.sleep(1.0)
        row = K.QY("SELECT status, params, result FROM sync_tasks WHERE task_id=%s", [tid])
        if not row:
            break
        st = row[0]["status"]
        try:
            params = json.loads(row[0].get("params") or "{}")
        except Exception:
            params = {}
        print("     round %d: status=%s stage=%s chunks=%s skus=%s"
              % (rounds, st, params.get("stage"), params.get("chunks"), params.get("rows")))
        if st in ("done", "error"):
            break
        time.sleep(34.0)

    elapsed = round(time.time() - t0, 1)
    rec("eval", "任务最终 done", st == "done", "status=%s 用时=%ss rounds=%d" % (st, elapsed, rounds))
    rec("eval", "真的分片了(不是一次跑完)", int(params.get("chunks") or 0) >= 2,
        "chunks=%s skus=%s hits=%s" % (params.get("chunks"), params.get("rows"), params.get("hits")))

    # 运行流水: 必须是 ok(不是 timeout), 且不再有 running
    pr = K.QY("SELECT status, scope, duration_ms, started_at, finished_at FROM pipeline_runs "
              "WHERE pipeline='rule_eval' ORDER BY id DESC LIMIT 4")
    oks = [x for x in pr if str(x.get("status")) == "ok"]
    running = [x for x in pr if str(x.get("status")) == "running"]
    rec("eval", "pipeline_runs 有 ok 记录(不再是 timeout)", bool(oks),
        "最近 4 条: %s" % [str(x.get("status")) for x in pr])
    rec("eval", "没有残留 running", not running, str(len(running)))

    et = K.QY("SELECT status, rows_out FROM eval_tasks ORDER BY id DESC LIMIT 1")
    rec("eval", "eval_tasks 已收尾(横幅会退出)", bool(et) and str(et[0].get("status")) == "done",
        str(et[0]) if et else "-")

    ml = K.QY("SELECT IFNULL(details,'') AS d FROM maintenance_log WHERE `date`=%s AND task='daily_rules'",
              [_today])
    d = str((ml[0].get("d") if ml else "") or "")
    rec("eval", "占位已落指纹(不再是 #run#)", bool(d) and not d.startswith("#run#"), d[:40])


# ── export: 补货导出全量 ─────────────────────────────────────────────

def run_export(admin):
    print("\n== export: 补货导出是否给全量")
    r = admin.post(K.BASE + "/api/exports?type=replen&channel=jd&mode=bbcc", timeout=60)
    j = _json(r)
    tid = j.get("task_id") or (j.get("data") or {}).get("task_id") or ""
    rec("export", "建任务成功(且请求很快返回)", r.status_code == 200 and bool(tid),
        "http=%s task=%s" % (r.status_code, tid))
    if not tid:
        return
    st = ""
    deadline = time.time() + 10 * 60
    rounds = 0
    while time.time() < deadline:
        rounds += 1
        q = admin.get(K.BASE + "/api/exports/status?task_id=%s&page_size=2000" % tid, timeout=180)
        jq = _json(q)
        st = jq.get("status") or ""
        if st in ("done", "error"):
            break
        if rounds > 60:
            break
    rec("export", "任务 done", st == "done", "status=%s rounds=%d" % (st, rounds))
    if st != "done":
        return
    # 判据取自 **DB 真源**(sync_tasks.params/result), 不依赖 status 响应字段 ——
    #   响应只是给前端的视图, 而"到底导了多少行"必须以落库的自报值为准(§15.201 修的正是它)。
    _row = K.QY("SELECT params, result FROM sync_tasks WHERE task_id=%s", [tid]) or [{}]
    try:
        _pj = json.loads(_row[0].get("params") or "{}")
    except Exception:
        _pj = {}
    try:
        _rj = (json.loads(_row[0].get("result") or "{}") or {}).get("result") or {}
    except Exception:
        _rj = {}
    parts = _rj.get("parts") or _pj.get("parts") or []
    rows = int(_rj.get("rows") or _rj.get("success") or 0)
    total = int(_pj.get("total") or 0)
    rec("export", "分片数 > 1(真的接力了)", len(parts) >= 2, "parts=%d rows=%s total=%s"
        % (len(parts), rows, total))

    # 下载并数行数(CSV, 第一片带表头)
    dl = admin.get(K.BASE + "/api/exports/download/%s" % tid, timeout=300)
    body = dl.content
    rec("export", "下载 200", dl.status_code == 200, "bytes=%d ct=%s" % (len(body),
        dl.headers.get("content-type", "")[:40]))
    if dl.status_code != 200:
        return
    txt = body.decode("utf-8-sig", errors="replace")
    lines = [x for x in txt.splitlines() if x.strip()]
    got = max(0, len(lines) - 1)          # 减表头
    rec("export", "行数 > 2000(不再被 UNPAGED_CAP 截断)", got > 2000, "got=%d" % got)
    rec("export", "下载行数 == 落库 rows == total(三处一致)",
        got == rows == total and total > 0, "got=%d rows=%s total=%s" % (got, rows, total))
    rec("export", "中段无 BOM(合并只留文件开头一个)", txt.count("\ufeff") <= 1,
        "BOM 次数=%d" % txt.count("\ufeff"))
    rec("export", "分片各只有第一片带表头", lines[0].startswith("SKU") if lines else False,
        lines[0][:40] if lines else "-")


# ── delmap: 空转成功 + BC 计数 ──────────────────────────────────────

def run_delmap(admin):
    print("\n== delmap: 删除空转 + BC 计数")
    cases = [
        ("DELETE", "/api/orders/999999907"),
        ("DELETE", "/api/rules/999999907"),
        ("DELETE", "/api/suppliers/999999907"),
        ("DELETE", "/api/cleansing/templates/999999907"),
        ("POST", "/api/orders/999999907/restore"),
        ("POST", "/api/rules/999999907/restore"),
    ]
    bad = []
    for m, p in cases:
        r = admin.request(m, K.BASE + p, timeout=60)
        j = _json(r)
        ok = (j.get("ok") is False)
        if not ok:
            bad.append("%s %s -> %s" % (m, p, json.dumps(j, ensure_ascii=False)[:80]))
    rec("delmap", "不存在的 id 一律 ok:false(不再报成功)", not bad, "; ".join(bad)[:240])

    aux = _json(admin.get(K.BASE + "/api/dashboard/aux?channel=jd", timeout=120))
    cnt = _json(admin.get(K.BASE + "/api/alerts/counts?channel=jd", timeout=120))
    a_bc = ((aux.get("data") or {}).get("alertCounts") or {}).get("ls_warehouse") or {}
    c_bc = (cnt.get("data") or {}).get("ls_warehouse") or {}
    ab, cb = a_bc.get("bc", 0), c_bc.get("bc", 0)
    rec("delmap", "aux.ls_warehouse.bc 与 alerts/counts 同值(不再是恒 0)",
        ab == cb, "aux=%s alerts=%s（aux 是物化: 映射类改动要有一次失效才生效, "
        "所以本组应在 eval 之后跑）" % (ab, cb))
    rec("delmap", "BC 计数非零(bbcc 模式确有 bc 行)", ab > 0, "aux.bc=%s" % ab)


# ── sweep: 窗口护栏 ────────────────────────────────────────────────

def run_sweep(admin):
    print("\n== sweep: agg-sweep 窗口")
    r = admin.get(K.BASE + "/api/cron/agg-sweep", timeout=120)
    j = _json(r)
    d = j.get("data") or {}
    skipped = isinstance(d, dict) and bool(d.get("skipped"))
    rec("sweep", "窗口外被挡(不再匿名随时触发)", skipped,
        json.dumps(d, ensure_ascii=False)[:160])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    # 默认 admin: 这组判据要打写端点(删除/导出), demo 是只读访客会 403 ——
    #   kit 的默认账号是 demo(压测只读用), 这里必须分开, 否则"403"会被误读成"端点坏了"
    ap.add_argument("--user", default=os.environ.get("SUPPLY_USER", "admin"))
    ap.add_argument("--pwd", default=os.environ.get("SUPPLY_PASS", "admin123"))
    a = ap.parse_args()
    only = {x.strip() for x in (a.only or "").split(",") if x.strip()}

    tok = K.login(a.user, a.pwd)
    admin = K.sess()
    print("base=%s 登录=%s" % (K.BASE, "ok" if tok else "fail"))
    K.init_db()

    if not only or "eval" in only:
        run_eval(admin)
    if not only or "export" in only:
        run_export(admin)
    if not only or "delmap" in only:
        run_delmap(admin)
    if not only or "sweep" in only:
        run_sweep(admin)

    bad = [r for r in ROWS if not r["ok"]]
    print("\n%d/%d 通过" % (len(ROWS) - len(bad), len(ROWS)))
    for r in bad:
        print("  ✗ [%s] %s  %s" % (r["g"], r["n"], r["d"]))
    out = os.path.join(HERE, "out", "%s_relay_verify.json" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": ROWS}, f,
                  ensure_ascii=False, indent=1)
    print("[out] %s" % out)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
