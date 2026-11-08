#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""性能基准统一入口。判据只在这里定义（红线 17 的可执行版）。

    python3 scripts/perf/run.py --list-md                 # 打印 docs/PERF.md 的页面表（文档由它生成）
    python3 scripts/perf/run.py hot --rounds 5            # 纯读 8 并发 ×5 轮（唯一可判"快了多少"）
    python3 scripts/perf/run.py hot --pages dash          # 只测看板模块
    python3 scripts/perf/run.py storm --scene S2          # 日常：6 读 2 写 30s
    python3 scripts/perf/run.py storm --scene S5          # 极端：16 读 3 写 20s
    python3 scripts/perf/run.py cold                      # 后台重建本体（读 list_head.timing）
    python3 scripts/perf/run.py first --kind repl         # 真首建（自带清理）
    python3 scripts/perf/run.py noise --rounds 3          # 验证"本轮测量是否可信"
    python3 scripts/perf/run.py all                       # hot+storm(S2/S5)+cold

写场景需管理员账号：`SUPPLY_USER=... SUPPLY_PASS=... python3 scripts/perf/run.py storm`
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kit as K                       # noqa: E402
import scenes as SC                   # noqa: E402
import endpoints as EP                # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

# ── 判据（阈值集中在此，改判据只改这里） ──────────────────────────
TH = {
    "hot_p95": 1500,        # 热态并发 p95 ≤1.5s（稳态判据）
    "hot_p50": 800,         # 热态并发 p50 ≤0.8s（超过说明读侧没走物化/SWR）
    "hot_cv": 0.20,         # 5 轮 p50 的 CV ≤20%，否则这轮测量不可信
    # 低延迟时 CV 会被绝对值放大（30ms vs 48ms 的 CV 就是 0.2，但差 18ms 无意义），
    # 所以再加一条绝对容差：极差 ≤ 该值也算稳定（两者取"或"）。
    "hot_jitter_ms": {"srv_p50": 100, "srv_p95": 150},
    "cold_total": 6000,     # 单 kind 重建 total ≤6s
    "first_single": 8000,   # 真首建单发 ≤8s
    "first_conc_p95": 8000, # 真首建 4 并发 p95 ≤8s（单飞 + 排队，不等于 4×）
    "storm_max": 10000,     # 单请求 ≤10s（含写风暴）
    "storm_503": 0,         # 0 个 503
    "storm_incomplete": 0,  # 0 条空结构/零数据
}


def _judge_hot(r):
    res = []
    for f, lim in (("srv_p95", TH["hot_p95"]), ("srv_p50", TH["hot_p50"])):
        v = [x[f] for x in r["rounds"]]
        med = sorted(v)[len(v) // 2]
        res.append(("%s 中位 %d ≤ %d" % (f, med, lim), med <= lim))
    for f in ("srv_p50", "srv_p95"):
        d = (r.get("dispersion") or {}).get(f) or {}
        c = d.get("cv")
        if c is None:
            continue
        jit = d["vals"] and (max(d["vals"]) - min(d["vals"])) or 0
        lim = TH["hot_jitter_ms"][f]
        res.append(("离散度 %s：CV %.3f ≤ %.2f 或极差 %dms ≤ %dms"
                    % (f, c, TH["hot_cv"], jit, lim), c <= TH["hot_cv"] or jit <= lim))
    if len(r["rounds"]) < 2:
        res.append(("轮数 ≥2（单轮只能看数、不能判离散度）", False))
    bad = sum(x["status503"] + x["incomplete"] for x in r["rounds"])
    res.append(("0 个 503 / 0 条不完整（实测 %d）" % bad, bad == 0))
    return res


def _judge_storm(r):
    res = [("0 个 503（实测 %d）" % r["status503"], r["status503"] <= TH["storm_503"]),
           ("0 条不完整（实测 %d）" % r["incomplete"], r["incomplete"] <= TH["storm_incomplete"]),
           ("单请求 max %d < %d" % (r["srv_max"], TH["storm_max"]), r["srv_max"] < TH["storm_max"])]
    # 写必须真的发生：账号只读时写会 403，若只看前三条会得到"假 PASS"（本轮踩过）
    w = r.get("writes") or {}
    need = w.get("expect_min") or 1
    res.append(("写入 %d 次 ≥ %d（写没发生=没测到，不是通过）" % (w.get("n", 0), need),
                w.get("n", 0) >= need))
    res.append(("写入失败 0 次（实测 %d）%s" % (w.get("fail", 0), (w.get("err") or "")[:60]),
                w.get("fail", 0) == 0))
    return res


def _judge_cold(r):
    res = []
    for it in r.get("items") or []:
        t = it.get("timing") or ""
        tot = None
        for seg in t.split(","):
            if seg.startswith("total="):
                try:
                    tot = int(float(seg.split("=")[1]) * 1000)
                except Exception:
                    pass
        if tot is not None:
            res.append(("%s total %dms ≤ %d" % (it["kind"], tot, TH["cold_total"]),
                        tot <= TH["cold_total"]))
    return res


def _judge_first(r):
    res = []
    if r.get("single"):
        res.append(("首建单发 %dms ≤ %d" % (r["single"]["srv"], TH["first_single"]),
                    r["single"]["srv"] <= TH["first_single"]))
    if r.get("conc"):
        res.append(("首建 4 并发 p95 %dms ≤ %d" % (r["conc"]["p95"], TH["first_conc_p95"]),
                    r["conc"]["p95"] <= TH["first_conc_p95"]))
    return res


def _print_judge(res):
    ok = all(v for _, v in res) if res else False
    for name, v in res:
        print("  %s %s" % ("✓" if v else "✗", name))
    print("  → %s" % ("PASS" if ok else "FAIL"))
    return ok


def _save(obj, mode):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_%s.json" % (time.strftime("%Y%m%d_%H%M%S"), mode))
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[out] %s" % p)
    return p


def _keys(pages):
    """all=全部已登记接口（覆盖新页面）；core=12 个历史可比接口（与 11-07/08 数据同口径）。"""
    if pages == "core":
        return list(EP.CORE)
    if not pages or pages == "all":
        return list(EP.ENDPOINTS)
    want = set(p.strip() for p in pages.split(","))
    ks = [k for k, v in EP.ENDPOINTS.items() if v["page"] in want]
    if not ks:
        raise SystemExit("没有匹配的页面：%s（可选：%s）"
                         % (pages, ",".join(p[0] for p in EP.PAGE_ORDER)))
    return ks


def main():
    ap = argparse.ArgumentParser(description="SupplyKit 性能基准")
    ap.add_argument("mode", nargs="?", default="hot",
                    choices=["hot", "storm", "cold", "first", "noise", "all"])
    ap.add_argument("--pages", default="all", help="all 或逗号分隔的页面 id（见 --list-md）")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--readers", type=int, default=8)
    ap.add_argument("--writers", type=int, default=None, help="覆盖场景预设的写线程数")
    ap.add_argument("--secs", type=int, default=15)
    ap.add_argument("--scene", default="S2", choices=list(SC.STORM_PRESETS))
    ap.add_argument("--kind", default="repl", choices=list(SC.KINDS))
    ap.add_argument("--list-md", action="store_true", help="打印文档用的页面表")
    a = ap.parse_args()

    if a.list_md:
        print(EP.markdown_table())
        return 0

    K.login()
    print("[login] %s @ %s" % (K.TOKEN.get("user"), K.BASE), flush=True)
    print("[phase] %s" % K.phase_now(), flush=True)

    if a.mode in ("hot", "all"):
        r = SC.hot(_keys(a.pages), readers=a.readers, secs=a.secs, rounds=a.rounds)
        print("\n[hot] 判据")
        ok = _print_judge(_judge_hot(r))
        _save(r, "hot")
        if a.mode == "hot":
            return 0 if ok else 1

    if a.mode in ("storm", "all"):
        for sc in (["S2", "S5"] if a.mode == "all" else [a.scene]):
            r = SC.storm(sc, _keys(a.pages), writers=a.writers)
            print("\n[storm %s] 判据（只判不停摆，不判延迟 —— 红线 17）" % sc)
            ok = _print_judge(_judge_storm(r))
            _save(r, "storm_" + sc)
        if a.mode == "storm":
            return 0 if ok else 1

    if a.mode in ("cold", "all"):
        r = SC.cold()
        print("\n[cold] 判据")
        ok = _print_judge(_judge_cold(r))
        _save(r, "cold")

    if a.mode == "first":
        r = SC.first(a.kind)
        print("\n[first] 判据")
        ok = _print_judge(_judge_first(r))
        _save(r, "first_" + a.kind)

    if a.mode == "noise":
        r = SC.noise(rounds=a.rounds, secs=a.secs, readers=a.readers)
        print("\n[noise] 离散度（read_only 的 CV 应远小于 with_write）")
        for g, d in (r.get("dispersion") or {}).items():
            print("  %-11s vals=%s CV=%s 极差比=%s" % (g, d["vals"], d["cv"], d["range_ratio"]))
        _save(r, "noise")
    return 0


if __name__ == "__main__":
    sys.exit(main())
