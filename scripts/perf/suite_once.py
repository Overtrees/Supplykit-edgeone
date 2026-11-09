#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§15.190 部署后一次性全套压测编排（**一轮**）。

    python3 scripts/perf/suite_once.py probe     # 零写只读取证（可反复，极低 RU）
    python3 scripts/perf/suite_once.py run       # 一轮全套（30 分钟防重，只跑一次）
    python3 scripts/perf/suite_once.py report    # 把 probe+run 汇总成 Markdown

背景：§15.189 的 P0（物化补建自愈）/ P1（seed 合并失效）/ P3（第四类判据）/
P5（hot 相位跳轮 + 空接口白名单）都是**业务/脚本代码改动**，推送部署后才生效。
本脚本用一次部署 + 一轮压测同时回答两件事：
  1) 单条 / 多并发读写的冷算态与热态，相对 §3 基线有没有性能退化；
  2) 上轮点名的尾巴，在线上真实数据上是否收敛达标。

红线（延续 §9 / §15.187-189）：
- **只一轮**：`run` 带 30 分钟防重锁，拒绝第二次；不重跑 seed、不导数据。
- **判据唯一真源**：直接 import `run.py` 的 `TH` / `_judge_*`，不另造阈值。
- **红线 17**：`storm` 只判「停摆/完整」，不判延迟；只有 `hot`（纯读 + 锁相位）
  可用于「是否退化」的结论。
- 耗时只认 `X-Srv-Ms`（kit），链路 RTT 单独记不参与判定。
- `probe` 纯只读；写只发生在压测场景自身（季节系数 1.5↔1.5001，对业务零影响）。
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "cloud-functions", "api"))

import kit as K                      # noqa: E402
import scenes as SC                  # noqa: E402
import endpoints as EP               # noqa: E402
import run as R                      # noqa: E402  判据唯一真源

OUT_DIR = os.path.join(HERE, "out")
LOCK = os.path.join(OUT_DIR, ".suite_once.lock")
BASELINE_JSON = os.path.join(OUT_DIR, "20261109_predeploy_baseline.json")

# §9.2 导入后稳态基线（25 接口 / 13 页面 / 5 轮中位），docs/PERF.md §3
BASELINE = {"srv_p50": 32, "srv_p95": 509,
            "source": "docs/PERF.md §9.2 导入后稳态 25接口/13页面/5轮中位"}

# 一轮场景顺序不可随意调整：hot（纯读 + 锁相位）必须在最前，
# 部署后 drain 补建完成、相位静默时取样，否则 wait_quiet 会跳轮（§15.189 P5）。
SCENE_PLAN = [
    ("hot_multiconc", "热态·多并发纯读 8并发×5轮", "hot", dict(readers=8, secs=15, rounds=5)),
    ("hot_single", "热态·单条纯读 1并发×3轮", "hot", dict(readers=1, secs=10, rounds=3)),
    ("storm_S2", "日常混合 6读2写 30s", "storm", dict(scene="S2")),
    ("storm_S4", "多用户读写单条 4读4写 25s", "storm", dict(scene="S4")),
    ("storm_S5", "极端混合 16读3写 20s", "storm", dict(scene="S5")),
    ("cold", "冷算本体 3 kind 重建", "cold", dict()),
    ("first", "真首建 单发 + 4并发", "first", dict(kind="repl")),
    ("noise", "相位可信度 2组×2轮", "noise", dict(rounds=2, secs=8, readers=8)),
]


def _save(obj, tag):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_suite_%s.json" % (time.strftime("%Y%m%d_%H%M%S"), tag))
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[out] %s" % p, flush=True)
    return p


def _latest(pattern):
    fs = sorted(glob.glob(os.path.join(OUT_DIR, pattern)))
    return json.load(open(fs[-1], encoding="utf-8")) if fs else {}


def _admin():
    """users 表为空时，用线上 JWT_SECRET 本地签 admin token（零 RU、不回显、不落盘）。"""
    import import_smoke as IS
    secret = IS._env_secret()
    os.environ["JWT_SECRET"] = secret
    from routes.common import create_token
    K.TOKEN["v"] = create_token("admin")
    K.TOKEN["user"] = "admin"
    K.init_db()
    return "admin(local-signed)"


def _dbq():
    K.init_db()
    if not K.HAS_DB:
        raise RuntimeError("需要 .env 的 DB 凭据（只读）")
    return K.QY


def _q1(q, sql, args=()):
    r = q(sql, list(args))
    return r[0] if r else {}


# ── probe：零写只读取证（部署后尾巴收敛） ────────────────────────────────
def probe(wait_drain=True, max_polls=16, poll_gap=15.0):
    """只读：等 drain_pending 把落后的 repl 补建 + 四维/联动快照 + 第四类判据。

    每轮发一次带 admin token 的 `?state=1` 业务 GET 作**请求驱动源**：
      · 必须过鉴权中间件才会触发 index.py 的 60s 请求驱动线程（`/health` 在
        266-270 行被直接放行，**不经过**该钩子，只发 health 驱动不了 drain）；
      · `?state=1` 走 status() 分支，不触发 serve() 读侧重建，最省 RU。
    再只读 list_head 比对基线 gen。按状态推进，不做定死等待。
    """
    import requests
    who = _admin()          # 只读 GET / ?state=1 也需鉴权，否则 401 会让取证失真
    q = _dbq()
    base = _latest("20261109_predeploy_baseline.json") or {}
    base_gen = {str(x["kind"]): int(x["gen"] or 0)
                for x in (base.get("list_head") or [])}
    print("=== probe：部署后零写取证（%s） ===" % who, flush=True)
    print("  冻结基线 gen=%s dv=%s" % (base_gen, base.get("data_version")), flush=True)

    s = requests.Session()
    s.headers.update({"X-Auth-Token": K.TOKEN.get("v", ""),
                      "X-Accept-Encoding": "gzip, deflate, br",
                      "Accept-Encoding": "gzip, deflate, br"})
    drain = {"baseline_gen": base_gen, "polls": 0, "converged": False,
             "kind_progress": {}, "waited_s": 0}
    t0 = time.time()
    polls = max_polls if wait_drain else 1
    for i in range(1, polls + 1):
        try:
            # 过鉴权中间件 = 触发 60s 请求驱动线程；?state=1 纯诊断不重建
            s.get(K.BASE + "/api/insights/replenishment?channel=jd&state=1", timeout=30)
        except Exception as e:
            print("  驱动探测异常(非阻塞): %s" % str(e)[:60], flush=True)
        rows = q("SELECT kind,gen,fp,stale,built_at FROM list_head ORDER BY kind", [])
        cur = {str(x["kind"]): int(x["gen"] or 0) for x in rows}
        drain["polls"] = i
        drain["kind_progress"] = {k: {"base": base_gen.get(k), "now": v,
                                      "advanced": v > base_gen.get(k, -1)}
                                  for k, v in cur.items()}
        conv = all(v > base_gen.get(k, -1) for k, v in cur.items()) if base_gen else False
        print("  [%d/%d] gen=%s converged=%s" % (i, polls, cur, conv), flush=True)
        if conv or not wait_drain:
            drain["converged"] = conv
            break
        if i < polls:
            time.sleep(poll_gap)
    drain["waited_s"] = round(time.time() - t0, 1)

    # ── 四维 + 联动快照（全部只读）
    dv0 = _q1(q, "SELECT v FROM data_version WHERE id=1").get("v")
    gets = []
    for p in ("/api/dashboard/boot", "/api/dashboard/aux", "/api/alerts", "/api/tasks"):
        try:
            r = s.get(K.BASE + p, timeout=30)
            gets.append({"path": p, "http": r.status_code,
                         "degraded": r.headers.get("X-App-Degraded", "")})
        except Exception as e:
            gets.append({"path": p, "http": -1, "degraded": "", "err": str(e)[:60]})
    dv1 = _q1(q, "SELECT v FROM data_version WHERE id=1").get("v")

    heads = q("SELECT kind,scope,gen,fp,stale,built_at,note,row_cnt FROM list_head ORDER BY kind", [])
    slots = q("SELECT slot,holder,ts FROM heavy_slot", [])
    ev = q("SELECT task_id,task_type,status,updated_at FROM sync_tasks "
           "WHERE task_type IN ('cleansing','cleansing_eval') ORDER BY id DESC LIMIT 12", [])
    mx = _q1(q, "SELECT COALESCE(MAX(id),0) m FROM quality_logs").get("m") or 0
    hb = _q1(q, "SELECT COUNT(*) c FROM quality_logs WHERE id>=%s "
             "AND message LIKE %s", [int(mx) - 500, "%HeavyBusy%"]).get("c")
    alerts = _q1(q, "SELECT COUNT(*) c FROM alerts").get("c")
    running = _q1(q, "SELECT COUNT(*) c FROM sync_tasks WHERE status='running'").get("c")

    # ── 三个 ?state=1 诊断口（含 drain 计数 / fp_match / cold 统计）
    states = {}
    for kind, path in (("repl", "/api/insights/replenishment"),
                       ("purchase", "/api/insights/purchase"),
                       ("disposal", "/api/insights/disposal-suggestions")):
        try:
            r = s.get(K.BASE + path + "?channel=jd&state=1", timeout=30)
            d = (r.json() or {}).get("data") or {}
            states[kind] = {"http": r.status_code, "fp_match": d.get("fp_match"),
                            "gen": d.get("gen"), "stale": d.get("stale"),
                            "fp_head": str(d.get("fp_head") or "")[:12],
                            "fp_now": str(d.get("fp_now") or "")[:12],
                            "drain": d.get("drain"), "cold": d.get("cold")}
        except Exception as e:
            states[kind] = {"http": -1, "err": str(e)[:80]}

    verify_obj = {
        "data_version_converged": dv0 == dv1, "dv_before": dv0, "dv_after": dv1,
        "readonly_gets": gets,
        "degraded_any": any(g.get("degraded") for g in gets),
        "http_5xx": [g for g in gets if int(g.get("http") or 0) >= 500],
        "list_head": [{"kind": x["kind"], "gen": int(x["gen"] or 0)} for x in heads],
        "eval_tasks": ev, "heavybusy_logs_recent": hb, "alerts": alerts,
    }

    # ── 第四类判据：用 13:17 唯一一次历史 run 作基线 + 本次部署后取证
    import import_smoke as IS
    hist = _latest("*_import_smoke_run.json")
    judge = IS.judge_evidence(hist, verify_obj)
    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "probe",
           "drain": drain, "list_head": heads, "heavy_slot": slots,
           "data_version": {"before": dv0, "after": dv1, "converged": dv0 == dv1},
           "running_tasks": running, "alerts": alerts, "heavybusy_recent": hb,
           "eval_tasks": ev, "states": states, "verify": verify_obj,
           "judge": judge, "historical_run": (sorted(glob.glob(
               os.path.join(OUT_DIR, "*_import_smoke_run.json"))) or [""])[-1]}
    print("  dv %s→%s 稳定=%s；5xx=%d 降级=%s" % (
        dv0, dv1, dv0 == dv1, len(verify_obj["http_5xx"]),
        verify_obj["degraded_any"]), flush=True)
    print("  物化 gen=%s；HeavyBusy 近期=%s；alerts=%s；running=%s" % (
        [x["gen"] for x in heads], hb, alerts, running), flush=True)
    print("  drain 补建收敛=%s（轮询 %d 次 / %ss）" % (
        drain["converged"], drain["polls"], drain["waited_s"]), flush=True)
    print("  === 第四类判据：%s ===" % judge["verdict"], flush=True)
    for c in judge["checks"]:
        print("    %s %-22s %s" % ("✓" if c["ok"] else ("?" if c["ok"] is None else "✗"),
                                  c["key"], c["desc"]), flush=True)
    _save(out, "probe")
    return out


# ── run：一轮全套压测（严格防重） ────────────────────────────────────────
def _lock_check(force=False):
    if force or not os.path.isfile(LOCK):
        return
    try:
        d = json.load(open(LOCK, encoding="utf-8"))
        if time.time() - d.get("ts", 0) < 1800:
            raise RuntimeError("30 分钟内已跑过一轮（%s），拒绝重跑；只跑一轮是红线。"
                               % d.get("at"))
    except (ValueError, KeyError):
        pass


def _judge(kind, r):
    if kind == "hot":
        return R._judge_hot(r)
    if kind == "storm":
        return R._judge_storm(r)
    if kind == "cold":
        return R._judge_cold(r)
    if kind == "first":
        return R._judge_first(r)
    return []


def run(force=False):
    _lock_check(force)
    who = _admin()
    keys = list(EP.ENDPOINTS)
    print("=== 一轮全套压测（只此一次）===", flush=True)
    print("  %s @ %s · %d 接口 / %d 页面 · phase=%s" % (
        who, K.BASE, len(EP.ENDPOINTS), len(EP.PAGE_ORDER), K.phase_now()), flush=True)
    print("  判据真源 = scripts/perf/run.py TH（红线 17：storm 不判延迟）\n", flush=True)

    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "run",
           "interfaces": len(EP.ENDPOINTS), "pages": len(EP.PAGE_ORDER),
           "baseline": BASELINE, "th": R.TH, "scenes": []}
    t_all = time.time()
    for name, desc, kind, kw in SCENE_PLAN:
        print("\n──── [%s] %s ────" % (name, desc), flush=True)
        t0 = time.time()
        try:
            if kind == "hot":
                r = SC.hot(keys, readers=kw["readers"], secs=kw["secs"], rounds=kw["rounds"])
            elif kind == "storm":
                r = SC.storm(kw["scene"], keys)
            elif kind == "cold":
                r = SC.cold()
            elif kind == "first":
                r = SC.first(kw["kind"])
            else:
                r = SC.noise(rounds=kw["rounds"], secs=kw["secs"], readers=kw["readers"])
        except Exception as e:
            print("  场景异常：%s" % str(e)[:200], flush=True)
            out["scenes"].append({"name": name, "desc": desc, "kind": kind,
                                  "error": str(e)[:300], "wall_s": round(time.time() - t0, 1)})
            continue
        wall = round(time.time() - t0, 1)
        res = _judge(kind, r)
        ok = all(v for _, v in res) if res else None
        for jn, jv in res:
            print("  %s %s" % ("✓" if jv else "✗", jn), flush=True)
        if res:
            print("  → %s" % ("PASS" if ok else "FAIL"), flush=True)
        else:
            for g, d in (r.get("dispersion") or {}).items():
                print("  %-11s vals=%s CV=%s 极差比=%s" % (
                    g, d.get("vals"), d.get("cv"), d.get("range_ratio")), flush=True)
        if kind == "hot" and r.get("skipped_rounds"):
            print("  相位未静默跳过 %d 轮（已留痕，判据计入）"
                  % len(r["skipped_rounds"]), flush=True)
        r["_judge"] = [{"name": n, "ok": bool(v)} for n, v in res]
        r["_verdict"] = ("PASS" if ok else "FAIL") if res else "INFO"
        out["scenes"].append({"name": name, "desc": desc, "kind": kind,
                              "wall_s": wall, "verdict": r["_verdict"],
                              "judge": r["_judge"], "result": r})
        # 每步单独落盘，避免中途异常丢全部证据
        _save(r, "scene_" + name)

    out["wall_s"] = round(time.time() - t_all, 1)

    # ── 基线对比 + RU 成本统计（红线 17：只有 hot 可判退化）
    hot = next((s["result"] for s in out["scenes"]
                if s["name"] == "hot_multiconc"), None)
    cmp_ = {"available": False}
    if hot and hot.get("rounds"):
        d = hot.get("dispersion") or {}
        p50 = (d.get("srv_p50") or {}).get("median")
        p95 = (d.get("srv_p95") or {}).get("median")
        cmp_ = {"available": True, "source": BASELINE["source"],
                "hot_p50_median": p50, "hot_p95_median": p95,
                "baseline_p50": BASELINE["srv_p50"], "baseline_p95": BASELINE["srv_p95"],
                "p50_ratio": round(p50 / BASELINE["srv_p50"], 2) if p50 else None,
                "p95_ratio": round(p95 / BASELINE["srv_p95"], 2) if p95 else None,
                "th_p50": R.TH["hot_p50"], "th_p95": R.TH["hot_p95"],
                "verdict": hot.get("_verdict")}
    out["baseline_cmp"] = cmp_
    writes = 0
    for s in out["scenes"]:
        r = s.get("result") or {}
        if s["kind"] == "storm":
            writes += int(((r.get("writes") or {}).get("n")) or 0)
        if s["kind"] == "noise":
            for arr in (r.get("groups") or {}).values():
                for g in arr:
                    writes += int(((g.get("writes") or {}).get("n")) or 0)
    out["ru_proxy"] = {
        "observed_writes": writes,
        "cold_rebuild_triggers": len(((next((s["result"] for s in out["scenes"]
                                             if s["name"] == "cold"), {}) or {}).get("items") or [])),
        "first_builds": 1 if any(s["name"] == "first" and not s.get("error")
                                 for s in out["scenes"]) else 0,
        "note": "写=季节系数 1.5↔1.5001 对业务零影响；cold 触发 3 kind 重建；"
                "first 建 1 个 91 天 scope 后自带清理。未重跑 seed / 未导数据。",
    }
    print("\n=== 一轮汇总 ===", flush=True)
    for s in out["scenes"]:
        print("  %-16s %-5s %6ss  %s" % (
            s["name"], s.get("verdict", "ERR"), s.get("wall_s"),
            s.get("error", "")[:60]), flush=True)
    print("  基线对比：%s" % json.dumps(cmp_, ensure_ascii=False), flush=True)
    print("  RU 代理量：%s" % json.dumps(out["ru_proxy"], ensure_ascii=False), flush=True)
    print("  总墙钟 %ss" % out["wall_s"], flush=True)

    _save(out, "once")
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"at": out["at"], "ts": time.time()}, open(LOCK, "w", encoding="utf-8"))
    return out


# ── report：汇总 Markdown ────────────────────────────────────────────────
def report():
    pr = _latest("*_suite_probe.json")
    rn = _latest("*_suite_once.json")
    print("# 部署后一轮压测 · 尾巴收敛与基线退化核验\n")
    print("> probe=%s / run=%s\n" % (pr.get("at"), rn.get("at")))
    if pr:
        d = pr.get("drain") or {}
        print("## 尾巴收敛（零写取证）\n")
        print("| 项 | 结果 |")
        print("|---|---|")
        print("| drain 补建自愈 | %s（轮询 %s 次 / %ss） |" % (
            "收敛" if d.get("converged") else "未收敛", d.get("polls"), d.get("waited_s")))
        print("| 物化 gen | %s |" % [(x["kind"], x["gen"]) for x in pr.get("list_head") or []])
        print("| dv 稳定 | %s |" % (pr.get("data_version") or {}).get("converged"))
        print("| 第四类判据 | %s |" % (pr.get("judge") or {}).get("verdict"))
        print("")
    if rn:
        print("## 一轮压测（判据真源 run.py TH）\n")
        print("| 场景 | 结论 | 墙钟 |")
        print("|---|---|---|")
        for s in rn.get("scenes") or []:
            print("| %s | %s | %ss |" % (s["desc"], s.get("verdict", "ERR"), s.get("wall_s")))
        c = rn.get("baseline_cmp") or {}
        print("\n## 基线退化\n")
        if c.get("available"):
            print("- hot p50 中位 **%s ms** vs §3 基线 %s ms（%s×），阈值 %s ms" % (
                c["hot_p50_median"], c["baseline_p50"], c["p50_ratio"], c["th_p50"]))
            print("- hot p95 中位 **%s ms** vs §3 基线 %s ms（%s×），阈值 %s ms" % (
                c["hot_p95_median"], c["baseline_p95"], c["p95_ratio"], c["th_p95"]))
            print("- hot 判据：**%s**" % c["verdict"])
        else:
            print("- hot 无有效轮（相位未静默跳轮），判据如实 FAIL，不下退化结论")
        print("\nRU 代理量：%s" % json.dumps(rn.get("ru_proxy"), ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="部署后一次性全套压测编排（一轮）")
    ap.add_argument("mode", nargs="?", default="probe",
                    choices=["probe", "run", "report"])
    ap.add_argument("--force", action="store_true", help="跳过 30 分钟防重（默认不用）")
    ap.add_argument("--no-wait", action="store_true", help="probe 不等 drain 收敛，只取一次")
    a = ap.parse_args()
    if a.mode == "probe":
        probe(wait_drain=not a.no_wait)
    elif a.mode == "run":
        run(force=a.force)
    else:
        report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
