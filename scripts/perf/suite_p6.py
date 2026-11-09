#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P6 未达标项 · 稳态相位复验（只跑一轮，只跑未达标场景）。

背景（docs/PERF.md §12.6 / §15.190 五）：部署后那一轮是在「部署刚完成 +
持续写追赶态」下跑的，3 项 FAIL 全是**判据口径**问题，不是性能退化：
  P6-1 storm 写入次数 19/30/19 < expect_min 20/41/25（busy_share 0.923~1.0）
  P6-2 hot 单条 1并发×3轮 p95 抖（127/601/593ms），容差按 5轮8并发标定
  P6-3 cold repl 150s 轮询 timeout（跑后 gen=48 证明补建最终发生）

本轮按用户授权「再跑一轮未达标项，通过的不跑」：
  · 只跑 5 个未达标场景：hot_single / storm_S2 / storm_S4 / storm_S5 / cold
  · **不跑**上一轮已通过的 hot_multiconc(PASS) / first(PASS) / noise(INFO)
  · 判据真源仍是 run.py 的 TH 与 _judge_*，**一个字都不改**；不改判据转绿
  · 独立 30 分钟防重锁（.suite_p6.lock），保证只一轮
  · 额外只读取证：跑前/跑后 head gen·fp_match·heavy_slot·dv、逐接口空壳
    明细（补 §12.6 第 2 项「明细未落盘」）、cold 前后 gen 变化
  · 取证全部零写或只读；不重跑 seed、不导数据、不触发第二轮

用法：
    python3 scripts/perf/suite_p6.py run     # 一轮复验
    python3 scripts/perf/suite_p6.py report  # 汇总（读最新一次）
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "cloud-functions", "api"))

import kit as K                       # noqa: E402
import scenes as SC                   # noqa: E402
import run as R                       # noqa: E402
import endpoints as EP                # noqa: E402
import suite_once as SO               # noqa: E402  复用 _admin/_judge/_save/_dbq/SCENE_PLAN

OUT_DIR = os.path.join(HERE, "out")
LOCK = os.path.join(OUT_DIR, ".suite_p6.lock")

# 仅上一轮判 FAIL / 属 P6 未收敛尾巴的场景；其余一律不跑
P6_NAMES = ("hot_single", "storm_S2", "storm_S4", "storm_S5", "cold")
SKIPPED = ("hot_multiconc", "first", "noise")
SKIP_REASON = {
    "hot_multiconc": "上一轮 PASS（p50 43 / p95 678 / 0×503 / 0 不完整）",
    "first": "上一轮 PASS（单发 2266ms / 4并发 p95 110ms）",
    "noise": "INFO 场景（相位可信度，非达标判据）",
    # 2026-11-09 第 2 次复验（只跑仍未达标的 3 项）：这两项在上一轮已转 PASS
    "storm_S2": "上一轮 PASS（写 20 ≥ 20 / 0×503 / 0 不完整）",
    "cold": "上一轮 PASS（purchase 2.5s / disposal 4.7s ≤ 6s）",
}


def _save(obj, tag):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_p6_%s.json" % (time.strftime("%Y%m%d_%H%M%S"), tag))
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[out] %s" % p, flush=True)
    return p


def _latest(tag):
    fs = sorted(glob.glob(os.path.join(OUT_DIR, "*_p6_%s.json" % tag)))
    return json.load(open(fs[-1], encoding="utf-8")) if fs else {}


def _lock_check(force=False):
    if force or not os.path.isfile(LOCK):
        return
    try:
        d = json.load(open(LOCK, encoding="utf-8"))
        if time.time() - d.get("ts", 0) < 1800:
            raise RuntimeError("30 分钟内已跑过 P6 复验（%s），拒绝重跑；只一轮是红线。"
                               % d.get("at"))
    except (ValueError, KeyError):
        pass


# ── 零写/只读快照（跑前、跑后各一次） ─────────────────────────────────────
def snapshot(tag):
    import requests
    q = SO._dbq()
    out = {"tag": tag, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    out["data_version"] = SO._q1(q, "SELECT v FROM data_version WHERE id=1").get("v")
    out["list_head"] = q("SELECT kind,gen,fp,stale,built_at,note FROM list_head ORDER BY kind", [])
    out["heavy_slot"] = q("SELECT slot,holder,ts FROM heavy_slot", [])
    out["running_tasks"] = SO._q1(q, "SELECT COUNT(*) c FROM sync_tasks WHERE status='running'").get("c")
    out["alerts"] = SO._q1(q, "SELECT COUNT(*) c FROM alerts").get("c")
    s = requests.Session()
    s.headers.update({"X-Auth-Token": K.TOKEN.get("v", ""), "Accept-Encoding": "identity"})
    st = {}
    for k, path in (("repl", "/api/insights/replenishment"),
                    ("purchase", "/api/insights/purchase"),
                    ("disposal", "/api/insights/disposal-suggestions")):
        try:
            d = (s.get(K.BASE + path + "?channel=jd&state=1", timeout=30).json() or {}).get("data") or {}
            cold = d.get("cold") or {}
            st[k] = {"gen": d.get("gen"), "fp_match": d.get("fp_match"), "stale": d.get("stale"),
                     "drain": d.get("drain"),
                     "cold": {x: cold.get(x) for x in ("busy", "drain_submitted",
                                                       "drain_pending", "drain_cooled")}}
        except Exception as e:
            st[k] = {"error": str(e)[:100]}
    out["state1"] = st
    return out


def _incomplete_detail(keys):
    """§12.6 第 2 项：把 hot 聚合的 incomplete 落到逐接口明细（附加取证，不进判据）。

    只发 1 轮全量 GET，逐条记 status / 是否空壳，零写、极低 RU。
    """
    rows = []
    for k in keys:
        try:
            h = K.hit(k)                 # hit 内部已用 endpoints.complete() 判空壳
            rows.append({"key": k, "status": h.get("status"), "srv_ms": h.get("srv"),
                         "rows": h.get("rows"), "complete": h.get("complete")})
        except Exception as e:
            rows.append({"key": k, "error": str(e)[:80]})
    return rows


def run(force=False, only=None, gap=20.0, quiet_wait=90):
    _lock_check(force)
    who = SO._admin()
    keys = list(EP.ENDPOINTS)
    names = tuple(only) if only else P6_NAMES
    plan = [x for x in SO.SCENE_PLAN if x[0] in names]
    assert len(plan) == len(names), "场景清单漂移，拒绝开跑（要跑 %s，实际匹配 %d）" % (
        list(names), len(plan))
    skipped = tuple(n for n in SKIPPED if n not in names) + tuple(
        n for n in P6_NAMES if n not in names)

    print("=== P6 未达标项复验（只跑一轮 / 判据不改） ===", flush=True)
    print("  %s @ %s · %d 接口 / %d 页面" % (who, K.BASE, len(EP.ENDPOINTS), len(EP.PAGE_ORDER)), flush=True)
    print("  跑：%s" % ", ".join(x[0] for x in plan), flush=True)
    for n in skipped:
        print("  不跑：%-16s %s" % (n, SKIP_REASON.get(n, "未纳入本轮")), flush=True)
    print("  节奏：每场景前等相位静默(≤%ds)，场景间 %ds 间隔" % (quiet_wait, gap), flush=True)
    print("  判据真源 = run.py TH / _judge_*（一个字未改；红线：不改判据转绿）\n", flush=True)

    out = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "p6_run",
           "ran": [x[0] for x in plan],
           "skipped": {n: SKIP_REASON.get(n, "未纳入本轮") for n in skipped},
           "pace": {"quiet_wait_s": quiet_wait, "scene_gap_s": gap},
           "judge_source": "run.py TH / _judge_*（未改）",
           "th": R.TH, "scenes": []}
    out["before"] = snapshot("before")
    print("  跑前 gen=%s dv=%s fp_match=%s" % (
        [(x["kind"], x["gen"]) for x in out["before"]["list_head"]],
        out["before"]["data_version"],
        {k: v.get("fp_match") for k, v in out["before"]["state1"].items()}), flush=True)

    t_all = time.time()
    for _i, (name, desc, kind, kw) in enumerate(plan):
        if _i:
            print("  … 间隔 %ds，让上一场景的写触发重建收敛" % gap, flush=True)
            time.sleep(gap)
        ph = K.wait_quiet(quiet_wait)
        print("\n──── [%s] %s ────" % (name, desc), flush=True)
        print("  相位：%s" % json.dumps(ph, ensure_ascii=False)[:160], flush=True)
        t0 = time.time()
        try:
            if kind == "hot":
                r = SC.hot(keys, readers=kw["readers"], secs=kw["secs"], rounds=kw["rounds"])
            elif kind == "storm":
                r = SC.storm(kw["scene"], keys)
            elif kind == "cold":
                r = SC.cold()
            else:
                raise RuntimeError("意外场景类型 %s" % kind)
        except Exception as e:
            print("  场景异常：%s" % str(e)[:200], flush=True)
            out["scenes"].append({"name": name, "error": str(e)[:300],
                                  "wall_s": round(time.time() - t0, 1)})
            continue
        wall = round(time.time() - t0, 1)
        res = SO._judge(kind, r)                 # 判据不动
        ok = all(v for _, v in res) if res else None
        for jn, jv in res:
            print("  %s %s" % ("✓" if jv else "✗", jn), flush=True)
        if res:
            print("  → %s" % ("PASS" if ok else "FAIL"), flush=True)
        r["_judge"] = [{"name": n, "ok": bool(v)} for n, v in res]
        r["_verdict"] = ("PASS" if ok else "FAIL") if res else "INFO"
        rec = {"name": name, "desc": desc, "kind": kind, "wall_s": wall,
               "phase": ph, "verdict": r["_verdict"], "judge": r["_judge"], "result": r}
        # 附加取证（不进判据）
        if name == "hot_single":
            bad = sum(x.get("incomplete", 0) for x in r.get("rounds") or [])
            rec["incomplete_detail"] = _incomplete_detail(keys) if bad else []
            rec["incomplete_total"] = bad
            print("  附加取证：逐接口明细 %d 条（incomplete 合计 %d，仅记录不判据）"
                  % (len(rec["incomplete_detail"]), bad), flush=True)
        if name == "cold":
            q = SO._dbq()
            rec["head_after_cold"] = q(
                "SELECT kind,gen,stale,timing FROM list_head ORDER BY kind", [])
            print("  附加取证：cold 后 gen=%s" % [
                (x["kind"], x["gen"]) for x in rec["head_after_cold"]], flush=True)
        out["scenes"].append(rec)
        _save(r, "scene_" + name)

    out["wall_s"] = round(time.time() - t_all, 1)
    out["after"] = snapshot("after")
    print("\n  跑后 gen=%s dv=%s fp_match=%s" % (
        [(x["kind"], x["gen"]) for x in out["after"]["list_head"]],
        out["after"]["data_version"],
        {k: v.get("fp_match") for k, v in out["after"]["state1"].items()}), flush=True)

    # RU 代理量
    writes = sum(int(((s.get("result") or {}).get("writes") or {}).get("n") or 0)
                 for s in out["scenes"] if s["kind"] == "storm")
    out["ru_proxy"] = {
        "observed_writes": writes,
        "cold_rebuild_triggers": len(((next((s["result"] for s in out["scenes"]
                                             if s["name"] == "cold"), {}) or {}).get("items") or [])),
        "note": "写=季节系数 1.5↔1.5001 对业务零影响；未重跑 seed / 未导数据 / 未跑已通过场景。",
    }

    # P6 三项逐条归纳（如实，不转绿）
    def _v(n):
        if any(s["name"] == n for s in out["scenes"]):
            return next((s.get("verdict") for s in out["scenes"] if s["name"] == n), "N/A")
        return "未跑(上轮 PASS)"

    st = {s["name"]: s.get("result") or {} for s in out["scenes"]}
    # 503 归因（本轮的核心问题）：读还是写、应用层还是边缘、哪个接口
    out["s503_attr"] = {
        "read_503": sum(int((st.get(s["name"]) or {}).get("status503") or 0)
                        for s in out["scenes"] if s["kind"] == "storm"),
        "write_fail": sum(int(((st.get(s["name"]) or {}).get("writes") or {}).get("fail") or 0)
                          for s in out["scenes"] if s["kind"] == "storm"),
        "bad": [{"scene": s["name"], "items": (st.get(s["name"]) or {}).get("bad") or []}
                for s in out["scenes"] if s["kind"] == "storm"],
        "note": "status503 只统计读（hits 仅由 _reader 写入）；写失败单列 writes.fail。"
                "bad[].app_headers=False 表示响应无 X-Srv-Ms → 边缘/网关白页，非应用返回。",
    }
    out["p6"] = {
        "P6-1_storm_writes": {
            "verdict": [_v(n) for n in ("storm_S2", "storm_S4", "storm_S5")],
            "detail": [{"scene": n, "writes": (st.get(n) or {}).get("writes"),
                        "busy_share": (st.get(n) or {}).get("busy_share")}
                       for n in ("storm_S2", "storm_S4", "storm_S5")],
            "note": "expect_min=int(writers*secs/write_gap*0.5) 理想节奏估算，判据未改",
        },
        "P6-2_hot_small_sample": {
            "verdict": _v("hot_single"),
            "rounds": (st.get("hot_single") or {}).get("rounds"),
            "dispersion": (st.get("hot_single") or {}).get("dispersion"),
            "incomplete_detail": next((s.get("incomplete_detail") for s in out["scenes"]
                                       if s["name"] == "hot_single"), []),
            "note": "容差按 5轮8并发标定；本轮附加逐接口明细，判据未改",
        },
        "P6-3_cold_repl": {
            "verdict": _v("cold"),
            "items": (st.get("cold") or {}).get("items"),
            "head_after": next((s.get("head_after_cold") for s in out["scenes"]
                                if s["name"] == "cold"), []),
            "note": "timeout=150s 未改；无 timing 项仍被 _judge_cold 静默跳过（如实保留）",
        },
    }

    print("\n=== 一轮汇总（只跑未达标项） ===", flush=True)
    for s in out["scenes"]:
        print("  %-12s %-5s %6ss  %s" % (s["name"], s.get("verdict", "ERR"),
                                          s.get("wall_s"), s.get("error", "")[:50]), flush=True)
    print("  P6-1 storm writes : %s" % json.dumps(out["p6"]["P6-1_storm_writes"]["detail"],
                                                   ensure_ascii=False), flush=True)
    print("  503 归因          : 读 %d / 写失败 %d / %s"
          % (out["s503_attr"]["read_503"], out["s503_attr"]["write_fail"],
             json.dumps(out["s503_attr"]["bad"], ensure_ascii=False)[:220]), flush=True)
    print("  P6-2 hot_single   : %s" % out["p6"]["P6-2_hot_small_sample"]["verdict"], flush=True)
    print("  P6-3 cold repl    : %s" % out["p6"]["P6-3_cold_repl"]["verdict"], flush=True)
    print("  RU 代理量：%s" % json.dumps(out["ru_proxy"], ensure_ascii=False), flush=True)
    print("  总墙钟 %ss" % out["wall_s"], flush=True)

    _save(out, "once")
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump({"at": out["at"], "ts": time.time()}, open(LOCK, "w", encoding="utf-8"))
    return out


def report():
    d = _latest("once")
    if not d:
        print("（无 p6 复验记录）")
        return
    print("# P6 未达标项 · 稳态相位复验\n")
    print("> %s · 只跑 %s · 不跑 %s\n" % (d["at"], ", ".join(d["ran"]), ", ".join(d["skipped"])))
    print("| 场景 | 结论 | 墙钟 |")
    print("|---|---|---|")
    for s in d["scenes"]:
        print("| %s | %s | %ss |" % (s["name"], s.get("verdict"), s.get("wall_s")))
    print("\n跑前/跑后：`dv %s→%s` / gen %s→%s" % (
        d["before"]["data_version"], d["after"]["data_version"],
        [(x["kind"], x["gen"]) for x in d["before"]["list_head"]],
        [(x["kind"], x["gen"]) for x in d["after"]["list_head"]]))


def main():
    ap = argparse.ArgumentParser(description="P6 未达标项稳态复验（只跑一轮）")
    ap.add_argument("mode", nargs="?", default="report", choices=("run", "report"))
    ap.add_argument("--force", action="store_true", help="跳过 30 分钟防重（默认不用）")
    ap.add_argument("--only", default="",
                    help="只跑这些场景（逗号分隔）；默认 = 5 个 P6 项。"
                         "例：--only hot_single,storm_S4,storm_S5")
    ap.add_argument("--gap", type=float, default=20.0, help="场景间间隔秒（默认 20）")
    ap.add_argument("--quiet-wait", type=float, default=90.0, help="场景前等相位静默上限秒")
    a = ap.parse_args()
    if a.mode == "run":
        only = tuple(x.strip() for x in a.only.split(",") if x.strip()) or None
        run(a.force, only=only, gap=a.gap, quiet_wait=a.quiet_wait)
    else:
        report()


if __name__ == "__main__":
    main()
