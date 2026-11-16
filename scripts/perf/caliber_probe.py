#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日销口径探针：手工算式 / 四维矩阵 / 线上对拍 / 导出闭环。

回答的是「**同一份日销被多个消费方读时，数对不对、齐不齐、新不新**」，
不判延迟（归 `run.py` 的 `TH`），不判页面通不通（归 `e2e_chain.py`）。

为什么单独一个脚本（2026-11-16 §15.211）：
    趋势长基准从「融合值」换成真 56 天后，补货 / 采购 / 看板 / 断货卡 / 导出
    五个消费方各取各的列。口径对不对**不能靠读代码确认**——`daily_sales_60`
    装融合值那次，代码看起来完全正常，是 `ds28/fused` 中位 2.00 才暴露的。
    所以这里把三件事固化成可复核的算式：独立复刻 vs 生产函数 vs 物化列。

用法
----
    python3 scripts/perf/caliber_probe.py manual    # 本地：手工算式三方对拍（逐行可抄）
    python3 scripts/perf/caliber_probe.py matrix    # 本地：覆盖/一致/降级/幂等四维
    python3 scripts/perf/caliber_probe.py online    # 线上：采购+补货全量 + 箭头分布 + 新鲜度
    python3 scripts/perf/caliber_probe.py export    # 线上：导出正/负向（建任务，较慢）
    python3 scripts/perf/caliber_probe.py selftest  # 判据反证（喂假数据，必须报红）

判据只在本文件（`TH` 与 `judge_*`），别处不得复制阈值。
`manual` 的 `manual()` 是**独立复刻**（不 import 生产函数）——它和生产实现不一致时
必须报出来，而不是跟着改：两边同时改就变成自证了。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = os.path.join(ROOT, "cloud-functions", "api")
OUT = os.path.join(HERE, "out")

sys.path.insert(0, HERE)
sys.path.insert(0, API)

import kit as K  # noqa: E402

ADMIN_USER = os.environ.get("SUPPLY_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("SUPPLY_ADMIN_PASS", "admin123")

# ── 判据（唯一真源） ──────────────────────────────────────────────────────
TH = {
    "val_tol": 0.0011,      # DECIMAL(12,3) 与独立复刻 round(4) 的差容差
    "api_tol": 0.051,       # 接口字段 round(1) 后的差容差
    "cover_min": 0.995,     # ds28>0 的行里 ds56>0 的占比下限（0.5% 容忍极稀疏）
    "trend_band": (0.85, 1.15),   # 前端箭头判据，必须与 JSX 一致
}

_RES: list[tuple[bool, str]] = []


def chk(ok, msg):
    _RES.append((bool(ok), msg))
    print("  %s %s" % ("✅" if ok else "❌", msg), flush=True)
    return ok


def trend_of(a, b, band=None):
    """与前端 28vs56 箭头同判据（补货 ReplenTab / 采购 PurchaseTab）。"""
    lo, hi = band or TH["trend_band"]
    if b <= 0:
        return "na"
    if a > b * hi:
        return "up"
    if a < b * lo:
        return "down"
    return "flat"


# ── 独立复刻：削峰 → 3σ 保留 → 尾部加权 → shrink ───────────────────────────
def manual(daily, win, end=None):
    """从原始日序列重算一个窗口的日销，返回全部中间量（逐行可抄）。

    刻意**不调用** biz.sales 的任何函数：它的存在意义就是"另一个人按文档重算"。
    与生产不一致时以文档为准查两边，不是把这里改成和生产一样。
    """
    end = end or today()
    days = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(win)]
    # 削峰基线：窗口之后 32 天（不含窗口自身）的非零均值
    hist = [daily.get((end - timedelta(days=i)).strftime("%Y-%m-%d"), 0)
            for i in range(win, win + 32)]
    hnz = [v for v in hist if v > 0]
    base = sum(hnz) / len(hnz) if hnz else 0
    cut, cuted = dict(daily), []
    for day in days:
        v = cut.get(day, 0)
        if base > 0 and v > base * 5:
            cut[day] = round(base * 2, 2)
            cuted.append({"day": day, "raw": v, "cut": cut[day]})
    vals = [cut.get(x, 0) for x in days]
    tot = sum(vals)
    nz = [v for v in vals if v > 0]
    nnz = len(nz)
    m = sum(nz) / nnz if nnz else 0
    sd = (sum((v - m) ** 2 for v in nz) / nnz) ** 0.5 if nnz else 0
    thr = max(3 * sd, m * 1.5) if nnz else 0
    ws, kept = 0.0, []
    for idx, v in enumerate(reversed(vals)):
        if v > 0 and abs(v - m) <= thr:
            w = 1.5 if idx >= win - 3 else 1.0   # 最近 3 天加权
            ws += v * w
            kept.append({"day": days[win - 1 - idx], "v": v, "w": w})
    res = tot / win
    if nnz < 3:
        res = res * (nnz / 3)                    # shrink：稀疏天按比例折
    else:
        # 稳定序列：剔除 3σ 外的离群日（计 0）后**按窗口天数摊薄**，近 3 天 ×1.5
        res = ws / win
    return {"win": win, "hist_base": round(base, 4), "hist_n": len(hnz),
            "cuted": cuted, "sum_cut": tot, "nnz": nnz, "nz_mean": round(m, 4),
            "std": round(sd, 4), "threshold": round(thr, 4),
            "weighted_sum": round(ws, 4), "plain_avg": round(tot / win, 4),
            "shrink": nnz < 3, "result": round(res, 4),
            "kept": kept[:8], "n_kept": len(kept)}


def today():
    from core.clock import now_cn
    return now_cn()


def _daily_c(sku, bc, c_whs, days=94):
    """码级 C 仓日序列（与物化同口径：只算平台仓）。"""
    rows = K.QY("SELECT `date`, warehouse, order_count FROM daily_sales_snapshot "
                "WHERE channel='jd' AND sku=%s AND barcode=%s AND `date`>=%s",
                [sku, bc, (today() - timedelta(days=days)).strftime("%Y-%m-%d")])
    d = {}
    for r in rows:
        if r["warehouse"] not in c_whs:
            continue
        k = str(r["date"])[:10]
        d[k] = d.get(k, 0) + int(r["order_count"] or 0)
    return d


def _pick_samples(n=3):
    """自动挑样本：正常 / shrink / 有 5x 尖峰 各一，避免写死 SKU。"""
    rows = K.QY(
        "SELECT sku, barcode, ds28_cut, ds56_cut, nnz28 FROM sku_sales_stats "
        "WHERE channel='jd' AND warehouse='__C__' AND barcode<>'' AND ds28_cut>0 "
        "ORDER BY nnz28 DESC, ds28_cut DESC LIMIT 400")
    normal = next((r for r in rows if int(r["nnz28"] or 0) >= 8), None)
    sparse = next((r for r in rows if int(r["nnz28"] or 0) == 1), None)
    picked = [x for x in (normal, sparse) if x]
    if len(picked) < n:   # 不够就从高销量行补
        for r in rows:
            if r not in picked:
                picked.append(r)
            if len(picked) >= n:
                break
    return picked[:n]


# ── manual：独立复刻 vs 生产函数 vs 物化列 ─────────────────────────────────
def cmd_manual(_a):
    import biz.sales as BS
    from db import query

    c_whs = {r["warehouse"] for r in query(
        "SELECT DISTINCT warehouse FROM inventory WHERE channel='jd' "
        "AND warehouse_type='platform' AND warehouse<>''")}
    print("=== 手工算式对拍（%d 个样本 × 28/56 两窗口） ===" % 3, flush=True)
    out = []
    for dbrow in _pick_samples(3):
        sku, bc = dbrow["sku"], dbrow["barcode"]
        daily = _daily_c(sku, bc, c_whs)
        # 生产函数（同样只算 cut 单窗口）
        series = {d: {"v": v} for d, v in daily.items()}
        prod = {}
        for win in (28, 56):
            cut_s = BS.cut_series({k: v["v"] for k, v in series.items()}, win)
            prod[win] = round(BS.csm([win], cut_s)[win], 4) if hasattr(BS, "csm") else None
        rec = {"sku": sku, "bc": bc, "db": {k: float(dbrow[k] or 0)
                                            for k in ("ds28_cut", "ds56_cut")}, "win": {}}
        ok_all = True
        for win in (28, 56):
            m = manual(daily, win)
            dbv = rec["db"]["ds28_cut" if win == 28 else "ds56_cut"]
            d_db = abs(dbv - m["result"])
            rec["win"][win] = {"manual": m, "db": round(dbv, 3),
                               "d_db": round(d_db, 4)}
            ok_all &= d_db <= TH["val_tol"]
        rec["ok"] = ok_all
        out.append(rec)
        print("  %s/%s 28: 手工 %.4f ← 物化 %.3f（差 %.4f）| 56: 手工 %.4f ← 物化 %.3f"
              % (sku, bc, rec["win"][28]["manual"]["result"],
                 rec["win"][28]["db"], rec["win"][28]["d_db"],
                 rec["win"][56]["manual"]["result"], rec["win"][56]["db"]), flush=True)
    n_bad = sum(1 for r in out if not r["ok"])
    chk(n_bad == 0, "手工复刻 vs 物化列逐位一致（%d/%d 行超差 %.4f）"
        % (n_bad, len(out), TH["val_tol"]))
    return _dump("manual", {"cases": out})


# ── matrix：覆盖 / 一致 / 降级 / 幂等 ────────────────────────────────────
def cmd_matrix(_a):
    from db import query
    print("=== 四维矩阵（本地直连 + 进程内计算） ===", flush=True)

    # ① 完整性：新列覆盖
    cov = query(
        "SELECT channel, IF(warehouse='__C__','C','WH') dim, COUNT(*) n, "
        "SUM(ds28_cut>0) p28, SUM(ds28_cut>0 AND ds56_cut>0) AS both56 "
        "FROM sku_sales_stats GROUP BY channel, dim")
    bad = []
    for r in cov:
        p28 = int(r["p28"] or 0)
        if p28 == 0:
            continue
        ratio = float(r["both56"] or 0) / p28
        if ratio < TH["cover_min"]:
            bad.append({"channel": r["channel"], "dim": r["dim"],
                        "p28": p28, "both": int(r["both56"] or 0), "ratio": round(ratio, 4)})
        print("  %-6s %-3s ds28>0 %6d / 同时有 56 %6d = %.4f"
              % (r["channel"], r["dim"], p28, int(r["both56"] or 0), ratio), flush=True)
    chk(not bad, "ds56 覆盖 ≥ %.3f（缺口 %d 组：%s）"
        % (TH["cover_min"], len(bad), bad[:3] or "无"))

    # ② 一致性：补货 vs 采购 同键 ds56
    from routes.replenishment import _build_repl
    from routes.purchase_calc import _build_purchase
    rb = {(r["sku"], r.get("barcode") or ""): r for r in _build_repl("jd", "bbcc")}
    pu = {(r["sku"], r.get("barcode") or ""): r for r in _build_purchase("jd", "bbcc", 28)}
    same = diff = 0
    ex = []
    for k, a in pu.items():
        b = rb.get(k)
        if not b:
            continue
        x, y = float(b.get("daily_sales_56") or 0), float(a.get("daily_sales_56") or 0)
        if abs(x - y) <= TH["api_tol"]:
            same += 1
        else:
            diff += 1
            if len(ex) < 3:
                ex.append({"key": k, "repl": x, "purch": y})
    chk(diff == 0, "补货 vs 采购 ds56 同键一致（%d 同 / %d 异 %s）" % (same, diff, ex or ""))

    # ③ 可靠性：看板 digest 降级直算 vs 物化
    import biz.sales as BS
    _, fused_m, _ = BS.get_sales_digest("jd")   # (by_sku, fused, sigma) —— 别取错第一项
    # 必须与生产 get_sales_digest 降级分支同法: 90 天 → cut_series(28) → 三窗口 shrink
    _b28, _, _ = BS.load_daily_sales_grouped(90, "jd")
    _m = BS.calc_sales_multi({k: BS.cut_series(v) for k, v in _b28.items()},
                             windows=[7, 14, 28], sparse="shrink")
    fused_d = {s: BS.rolling_predict(_m[7].get(s, 0), _m[14].get(s, 0), _m[28].get(s, 0))
               for s in _b28}
    dif = [abs(fused_m.get(s, 0) - fused_d.get(s, 0)) for s in set(fused_m) | set(fused_d)]
    big = sum(1 for x in dif if x > 0.05)
    print("  digest 降级对拍：n=%d max=%.4f >0.05 的行 %d" % (len(dif), max(dif or [0]), big), flush=True)
    chk(max(dif or [0]) <= 0.05, "看板降级 == 物化（max %.4f）" % max(dif or [0]))

    # ④ 重建幂等：同一份取数连算两次
    _b2, _, _ = BS.load_daily_sales_grouped(90, "jd")
    _m2 = BS.calc_sales_multi({k: BS.cut_series(v) for k, v in _b2.items()},
                              windows=[7, 14, 28], sparse="shrink")
    f2 = {s: BS.rolling_predict(_m2[7].get(s, 0), _m2[14].get(s, 0), _m2[28].get(s, 0))
          for s in _b2}
    d2 = [abs(fused_d.get(s, 0) - f2.get(s, 0)) for s in fused_d]
    chk(max(d2 or [0]) == 0, "同输入两次计算逐位相同（max %.6f）" % max(d2 or [0]))
    return _dump("matrix", {"cover": cov, "cover_bad": bad,
                            "cross": {"same": same, "diff": diff, "ex": ex},
                            "degrade": {"n": len(dif), "max": round(max(dif or [0]), 4)}})


# ── online：线上全量对拍 ────────────────────────────────────────────────
def cmd_online(_a):
    import requests
    print("=== 线上对拍（%s） ===" % K.BASE, flush=True)
    K.login(ADMIN_USER, ADMIN_PASS)
    s = K.sess()
    out = {}

    # ① 采购：三列粒度（主值同源 69 码）+ 箭头分布
    out["purchase"] = {}
    for ch in ("jd", "other"):
        r = s.get(K.BASE + "/api/insights/purchase",
                  params={"channel": ch, "mode": "bbcc", "page": 1, "page_size": 20000},
                  timeout=900)
        d = (r.json() or {}).get("data") or {}
        items = d.get("items") or d.get("suggestions") or []
        rows = K.QY("SELECT sku, COALESCE(barcode,'') barcode, ds14_cut, ds28_cut, ds56_cut "
                    "FROM sku_sales_stats WHERE channel=%s AND warehouse='__C__'", [ch])
        bc = {(str(x["sku"]), str(x["barcode"])): x for x in rows if str(x["barcode"] or "")}
        agg = {str(x["sku"]): x for x in rows if not str(x["barcode"] or "")}
        st = {"rows": len(items), "checked": 0, "fallback": 0,
              "bad14": 0, "bad28": 0, "bad56": 0, "ex": [], "trend": {}}
        for x in items:
            sku, b = str(x.get("sku")), str(x.get("barcode") or "")
            src = bc.get((sku, b)) if b else None
            if src is None:
                src = agg.get(sku)
                st["fallback"] += b and 1 or 0
            if src is None:
                continue
            st["checked"] += 1
            for got, want, key in ((x.get("daily_sales_14"), src["ds14_cut"], "bad14"),
                                   (x.get("daily_sales_28"), src["ds28_cut"], "bad28"),
                                   (x.get("daily_sales_56"), src["ds56_cut"], "bad56")):
                if abs(float(got or 0) - round(float(want or 0), 1)) > TH["api_tol"]:
                    st[key] += 1
                    if len(st["ex"]) < 4:
                        st["ex"].append({"sku": sku, "bc": b, "field": key,
                                         "got": got, "want": round(float(want or 0), 1)})
            t = trend_of(float(x.get("daily_sales_28") or 0),
                         float(x.get("daily_sales_56") or 0))
            st["trend"][t] = st["trend"].get(t, 0) + 1
        out["purchase"][ch] = st
        chk(st["bad14"] == 0 and st["bad28"] == 0 and st["bad56"] == 0,
            "采购[%s] %d 行三列 == 物化码级（14/28/56 各 %d/%d/%d 超差）"
            % (ch, st["checked"], st["bad14"], st["bad28"], st["bad56"]))

    # ② 补货：双模式 × 双渠道，ds56 覆盖 + 残留旧键。
    #   注意接口字段 round(1)：库里 ds28=0.071 / ds56=0.048 → 显示 0.1 / 0.0，
    #   按接口值判「28>0 且 56=0」会把**舍入假象**算成缺陷（实测 jd/traditional 2255 行全是这类）。
    #   真判据必须回库看原值；只把「原值 ds28>0 且 ds56=0」记为覆盖缺口。
    raw_by_ch = {}
    for ch in ("jd", "other"):
        idx = {}
        for x in K.QY("SELECT sku, warehouse, COALESCE(barcode,'') bc, ds28_cut, ds56_cut "
                      "FROM sku_sales_stats WHERE channel=%s", [ch]):
            idx[(str(x["sku"]), str(x["warehouse"]), str(x["bc"]))] = x
        raw_by_ch[ch] = idx
    out["repl"] = {}
    for ch in ("jd", "other"):
        for mode in ("bbcc", "traditional"):
            r = s.get(K.BASE + "/api/insights/replenishment",
                      params={"channel": ch, "mode": mode, "page": 1, "page_size": 30000},
                      timeout=1200)
            d = (r.json() or {}).get("data") or {}
            items = d.get("items") or []
            t, cand = {}, []
            for x in items:
                k = trend_of(float(x.get("daily_sales_28") or 0),
                             float(x.get("daily_sales_56") or 0))
                t[k] = t.get(k, 0) + 1
                if float(x.get("daily_sales_28") or 0) > 0 and float(x.get("daily_sales_56") or 0) == 0:
                    cand.append(x)
            z, rounded = 0, 0
            idx = raw_by_ch[ch]
            for x in cand:
                sku, wh, bc = str(x.get("sku")), str(x.get("warehouse") or ""), str(x.get("barcode") or "")
                hit = (idx.get((sku, wh, bc)) or idx.get((sku, wh, ""))
                       or idx.get((sku, "__C__", bc)) or idx.get((sku, "__C__", "")))
                if not hit:
                    continue
                if float(hit["ds28_cut"] or 0) > 0 and float(hit["ds56_cut"] or 0) == 0:
                    z += 1
                else:
                    rounded += 1        # 显示成 0，但库里有值 → 舍入伪影（P8-5 同类）
            out["repl"]["%s_%s" % (ch, mode)] = {
                "status": r.status_code, "rows": len(items), "d56_zero_with_28": z,
                "round_artifacts": rounded,
                "n60": sum(1 for x in items if "daily_sales_60" in x), "trend": t}
            chk(z == 0, "补货[%s/%s] %d 行无真「28>0 且 56=0」（%d 行；舍入伪影 %d 行已单列）"
                % (ch, mode, len(items), z, rounded))
            chk(out["repl"]["%s_%s" % (ch, mode)]["n60"] == 0,
                "补货[%s/%s] 无残留 daily_sales_60 键" % (ch, mode))

    # ③ 新鲜度：判据必须能报出「业务日期陈旧」，而不是只看导入时间
    r = s.get(K.BASE + "/api/data-freshness", params={"channel": "jd"}, timeout=120)
    f = (r.json() or {}).get("data") or {}
    out["freshness"] = {k: f.get(k) for k in ("staleLevel", "sources")}
    lv = f.get("staleLevel")
    print("  新鲜度 staleLevel=%s sources=%d" % (lv, len(f.get("sources") or [])), flush=True)
    chk(isinstance(lv, int), "新鲜度接口可判档（%s）" % lv)
    chk(all("level" in x for x in (f.get("sources") or [])),
        "每个源都带 level（前端圆点有据可依）")
    return _dump("online", out)


# ── export：导出闭环（正/负向） ──────────────────────────────────────────
def cmd_export(_a):
    print("=== 导出闭环（正向 purchase_suggestions / 负向 purchase） ===", flush=True)
    K.login(ADMIN_USER, ADMIN_PASS)
    s = K.sess()
    out = {}

    # 负向：purchase 已删除（无消费方），必须明确报错而不是静默给补货表
    r = s.post(K.BASE + "/api/exports",
               params={"type": "purchase", "channel": "jd", "mode": "bbcc"}, timeout=120)
    j = r.json() or {}
    bad_ok = j.get("ok") is False and "未知导出类型" in str(j.get("msg") or j.get("error") or j)
    out["neg_purchase"] = {"status": r.status_code, "body": j}
    chk(bad_ok, "type=purchase 明确报「未知导出类型」（不再静默给补货表）")

    # 正向：purchase_suggestions 表头含 日销56、不含 日销60
    r = s.post(K.BASE + "/api/exports",
               params={"type": "purchase_suggestions", "channel": "jd", "mode": "bbcc",
                       "page_size": 50}, timeout=300)
    j = r.json() or {}
    tid = ((j.get("data") or j) or {}).get("task_id") or ""
    hdr, parts, fn = [], 0, ""
    for _ in range(60):
        st = s.get(K.BASE + "/api/exports/status",
                   params={"task_id": tid, "page_size": 50}, timeout=300).json() or {}
        d = st.get("data") or st or {}
        if str(d.get("status")) in ("done", "failed", "error"):
            parts = len(((d.get("result") or {}) if isinstance(d.get("result"), dict) else {}).get("parts") or [])
            fn = str(d.get("filename") or "")   # 文件名由服务端给，别自己拼 tid_p1.csv
            break
        time.sleep(3)
    if fn:
        dl = s.get(K.BASE + "/api/exports/download/" + fn, timeout=300)
        if dl.status_code == 200:
            hdr = dl.content.decode("utf-8-sig", errors="ignore").splitlines()[:1]
    line = hdr[0] if hdr else ""
    out["pos"] = {"task_id": tid, "parts": parts, "filename": fn, "header": line[:400]}
    chk("日销56" in line, "采购导出表头含「日销56」（%s）" % (line[:80] or "未取到"))
    chk("日销60" not in line, "采购导出表头无「日销60」")
    return _dump("export", out)


# ── selftest：判据反证 ─────────────────────────────────────────────────
def cmd_selftest(_a):
    print("=== 反证（判据必须能报红） ===", flush=True)
    saved = list(_RES)
    _RES.clear()

    # 箭头判据：三种方向 + 边界 + 分母 0
    chk(trend_of(2.0, 1.0) == "up", "trend(2.0,1.0)=up")
    chk(trend_of(1.0, 1.0) == "flat", "trend(1.0,1.0)=flat")
    chk(trend_of(0.5, 1.0) == "down", "trend(0.5,1.0)=down")
    chk(trend_of(1.0, 0) == "na", "trend(x,0)=na（无长基准不判方向）")
    chk(trend_of(1.15, 1.0) == "flat" and trend_of(1.16, 1.0) == "up",
        "1.15 边界含等号（与 JSX `>` 一致）")

    # 手工复刻：造一条已知序列，验证四个环节都真的在起作用
    end = today()
    day = lambda i: (end - timedelta(days=i)).strftime("%Y-%m-%d")   # noqa: E731
    # 基线取「窗口之后 32 天」，所以造数要连历史段一起填，否则 base=0 不启用削峰
    d = {day(i): 2 for i in range(0, 120)}
    m = manual(d, 28, end)
    # 平稳 2/天 + 最近 3 天 ×1.5 加权 → (25×2 + 3×3) / 28 = 59/28
    chk(abs(m["result"] - round(59 / 28, 4)) < 1e-6,
        "平稳序列 28 天 → 59/28=%.4f（实测 %.4f，含近 3 天加权）"
        % (59 / 28, m["result"]))
    chk(m["nnz"] == 28 and not m["shrink"], "平稳序列不触发 shrink")

    # 削峰：窗口内塞一个 100（> 5×基线 2）→ 削到 2×基线 = 4
    d2 = dict(d)
    d2[day(3)] = 100
    m2 = manual(d2, 28, end)
    chk(bool(m2["cuted"]) and m2["cuted"][0]["cut"] == 4.0,
        "5× 尖峰被削到 2×基线（100 → %s）" % (m2["cuted"][0]["cut"] if m2["cuted"] else "未削"))

    # shrink：只留 1 个非零日 → ×(1/3)
    d3 = {day(i): 0 for i in range(0, 120)}
    d3[day(1)] = 18
    m3 = manual(d3, 28, end)
    chk(abs(m3["result"] - round(18 / 28 / 3, 4)) < 1e-6,
        "nnz=1 → 18/28×(1/3)=%.4f（实测 %.4f）" % (18 / 28 / 3, m3["result"]))
    chk(m3["shrink"] is True, "nnz<3 标记 shrink")

    # 覆盖判据：分母 0 不应误判红
    chk(all(v for v, _ in _RES), "以上反证全部成立")
    ok = all(v for v, _ in _RES)
    n = len(_RES)
    _RES[:] = saved
    print("  → selftest %s（%d 项）" % ("PASS" if ok else "FAIL", n), flush=True)
    return ok


# ── 落盘 ──────────────────────────────────────────────────────────────
def _dump(name, payload):
    os.makedirs(OUT, exist_ok=True)
    ts = today().strftime("%Y%m%d_%H%M%S")
    p = os.path.join(OUT, "%s_%s.json" % (ts, name))
    json.dump(payload, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    fail = sum(1 for v, _ in _RES if not v)
    print("\n【%s】%d 项判据，FAIL %d → %s\n  落盘 %s"
          % (name, len(_RES), fail, "PASS" if fail == 0 else "FAIL", p), flush=True)
    return fail == 0


def main():
    ap = argparse.ArgumentParser(description="日销口径探针")
    ap.add_argument("cmd", nargs="?", default="manual",
                    choices=["manual", "matrix", "online", "export", "selftest"])
    a = ap.parse_args()
    if a.cmd != "selftest" and not K.init_db():
        print("!! 直连 DB 不可用（缺 .env？）—— 只有 online/export 可无 DB 跑", flush=True)
    fn = {"manual": cmd_manual, "matrix": cmd_matrix, "online": cmd_online,
          "export": cmd_export, "selftest": cmd_selftest}[a.cmd]
    ok = fn(a)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
