# -*- coding: utf-8 -*-
"""四类场景的执行体。每个函数只做"打请求 + 记录"，判定在 run.py（判据集中一处）。

| 场景 | 回答什么问题 | 是否带写 | 是否需 DB |
|---|---|---|---|
| `hot`    | 快了多少（可重复，CV≈4%） | 否 | 否 |
| `storm`  | 会不会停摆 / 数据是否完整 | 是 | 否 |
| `cold`   | 重建本体各段耗时（归因用） | 否（改 stale 触发） | 是 |
| `first`  | 真首建（无旧版本可读）多快、4 并发是否排队 | 否 | 是 |
| `noise`  | 验证"这轮测量是否可信"（相位对照） | 是/否 | 是 |

红线 17：`hot` 的 p50/p95 可作优劣判据；`storm` 只判"不停摆+完整"，**不判延迟**。
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import kit as K
from endpoints import CORE, ENDPOINTS


def _by_ep(hits):
    """逐接口汇总。除延迟外**必须带状态归因** —— 否则 503 只能看到总数、点不出接口。

    `n` = 该接口被打了多少次（含失败的）；`n_srv` = 拿到 X-Srv-Ms 的次数。
    `n > n_srv` 说明响应里没有应用层头（边缘白页 / 连接层失败），这是判
    「503 是应用返回还是网关返回」的关键证据（§13.x 203 复验）。
    """
    by = {}
    for h in hits:
        by.setdefault(h["ep"], []).append(h)
    out = {}
    for k, hs in by.items():
        d = K.stat([h["srv"] for h in hs])
        d.update({"n": len(hs), "n_srv": sum(1 for h in hs if h.get("srv", -1) >= 0),
                  "s503": sum(1 for h in hs if h.get("status") == 503),
                  "non200": sum(1 for h in hs if h.get("status") != 200),
                  "inc": sum(1 for h in hs if not h.get("complete"))})
        out[k] = d
    return out


def _summary(hits, wall=None):
    srv = [h["srv"] for h in hits]
    return {
        "n": len(hits), "wall_s": wall,
        "srv_p50": K.pct(srv, 0.5), "srv_p95": K.pct(srv, 0.95),
        "srv_max": max(srv or [-1]),
        "status503": sum(1 for h in hits if h["status"] == 503),
        "non200": sum(1 for h in hits if h["status"] != 200),
        "incomplete": sum(1 for h in hits if not h["complete"]),
        "gt1500": sum(1 for h in srv if h > 1500),
        "gt10000": sum(1 for h in srv if h > 10000),
        "swr": sum(1 for h in hits if h["swr"]),
        # 非 200 逐条留证（接口 + 是否带应用层头 + 响应片段）——
        #   503 只报个总数时无法判断"读还是写、应用还是网关、哪个接口"（§13 的坑）。
        "bad": [{"ep": h["ep"], "status": h["status"], "srv": h["srv"],
                 "app_headers": h.get("app_headers"), "err": h.get("err"),
                 "body": (h.get("body") or "")[:140]}
                for h in hits if h["status"] != 200][:10],
        "eps": _by_ep(hits),
        "worst": sorted([{"ep": h["ep"], "srv": h["srv"], "status": h["status"], "rows": h["rows"]}
                         for h in hits], key=lambda x: -x["srv"])[:8],
    }


# ── hot：纯读 + 锁相位 ─────────────────────────────────────────
def hot(keys=None, readers=8, secs=15, rounds=5, gap=15.0, quiet=True):
    """纯读并发多轮。**这是唯一可用于判定"快了多少"的场景**（实测 CV 0.043）。

    每轮开始前锁相位（等无重建 ≥10s），轮内不发写 → 相位不被打断。
    """
    keys = keys or CORE
    out = {"mode": "hot", "readers": readers, "secs": secs, "rounds": [],
           "skipped_rounds": [], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    for i in range(1, rounds + 1):
        ph = K.wait_quiet(60) if quiet else K.phase_now()
        # §15.189 P5: 相位未静默(仍有重建在跑) → **跳轮**, 不把导入后冷算相位
        #   混进纯读判据。§9.7 第 3 轮 p95 1703ms 正是冷算相位被误判成性能退化;
        #   红线 17 要求「快了多少」只能用纯读 + 静默相位。跳轮显式留痕, 不静默丢弃。
        if quiet and not ph.get("ok"):
            out["skipped_rounds"].append(
                {"round": i, "reason": "phase_not_quiet", "phase": ph})
            print("[hot %d/%d] 跳过: 相位未静默(busy=%s age=%s), 本轮不计入判据"
                  % (i, rounds, ph.get("busy"), ph.get("age")), flush=True)
            if i < rounds:
                time.sleep(gap)
            continue
        stop = threading.Event()
        hits = []
        lock = threading.Lock()

        def _reader(seq):
            while not stop.is_set():
                for k in seq:
                    if stop.is_set():
                        break
                    h = K.hit(k)
                    with lock:
                        hits.append(h)
                    time.sleep(0.05)

        ths = [threading.Thread(target=_reader, args=(keys[j::readers] or keys,), daemon=True)
               for j in range(readers)]
        t0 = time.time()
        for t in ths:
            t.start()
        time.sleep(secs)
        stop.set()
        for t in ths:
            t.join(timeout=60)
        r = {"round": i, "phase": ph, "wall_s": round(time.time() - t0, 1)}
        r.update(_summary(hits))
        out["rounds"].append(r)
        print("[hot %d/%d] phase=%s p50=%s p95=%s 503=%s inc=%s"
              % (i, rounds, ph.get("ok"), r["srv_p50"], r["srv_p95"],
                 r["status503"], r["incomplete"]), flush=True)
        if i < rounds:
            time.sleep(gap)

    for f in ("srv_p50", "srv_p95"):
        vals = [r[f] for r in out["rounds"]]
        # §15.189 P5: 全部跳轮时 vals 为空 —— 不能 IndexError, 也不能伪造 dispersion
        if not vals:
            out.setdefault("dispersion", {})[f] = {
                "vals": [], "median": None, "cv": None, "range_ratio": None}
            continue
        out.setdefault("dispersion", {})[f] = {
            "vals": vals, "median": sorted(vals)[len(vals) // 2],
            "cv": K.cv(vals), "range_ratio": round(max(vals) / min(vals), 2) if min(vals) else None}
    return out


# ── storm：带写（日常 / 极端） ─────────────────────────────────
STORM_PRESETS = {
    # 日常最坏：6 读 2 写，写间隔 1.5s（重建能追上）
    "S2": dict(readers=6, writers=2, secs=30, write_gap=1.5),
    # 极端：16 读 3 写，写间隔 1.2s（重建追不上 → 长期追赶态）
    "S5": dict(readers=16, writers=3, secs=20, write_gap=1.2),
    # 多用户读写单条：4 读 4 写
    "S4": dict(readers=4, writers=4, secs=25, write_gap=1.2),
}


def storm(scene="S2", keys=None, readers=None, writers=None, secs=None, write_gap=None, tag=None):
    """带写压测。**判据只有三条**：0 个 503 / 0 条不完整 / 单请求 <10s（红线 17）。"""
    p = dict(STORM_PRESETS.get(scene, STORM_PRESETS["S2"]))
    readers = readers or p["readers"]
    writers = writers if writers is not None else p["writers"]
    secs = secs or p["secs"]
    write_gap = write_gap or p["write_gap"]
    keys = keys or CORE

    stop = threading.Event()
    hits, wlog = [], []
    lock = threading.Lock()
    busy = [0, 0]

    def _reader(seq):
        while not stop.is_set():
            for k in seq:
                if stop.is_set():
                    break
                h = K.hit(k)
                with lock:
                    hits.append(h)
                time.sleep(0.05)

    def _writer():
        while not stop.is_set():
            try:
                w = K.real_write()
            except Exception as e:
                # 写失败必须留痕：账号只读/403 时若静默，压测会退化成"纯读"却报 PASS
                w = {"status": -1, "err": str(e)[:140]}
            with lock:
                wlog.append(w)
            time.sleep(write_gap)

    def _probe():
        while not stop.is_set():
            p0 = K.phase_now()
            busy[1] += 1
            if (p0.get("busy") or 0) > 0:
                busy[0] += 1
            time.sleep(1.5)

    ths = [threading.Thread(target=_reader, args=(keys[j::readers] or keys,), daemon=True)
           for j in range(readers)]
    ths += [threading.Thread(target=_writer, daemon=True) for _ in range(writers)]
    ths.append(threading.Thread(target=_probe, daemon=True))
    t0 = time.time()
    for t in ths:
        t.start()
    time.sleep(secs)
    stop.set()
    for t in ths:
        t.join(timeout=90)

    out = {"mode": "storm", "scene": scene, "readers": readers, "writers": writers,
           "secs": secs, "write_gap": write_gap,
           "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "busy_share": round(busy[0] / max(busy[1], 1), 3),
           "writes": {"n": len(wlog),
                      "fail": sum(1 for w in wlog if w.get("status") != 200),
                      "expect_min": int(writers * secs / write_gap * 0.5),
                      # 状态码直方图：写失败到底是 503(边缘) / 403(只读账号) / -1(网络)
                      #   —— 只报 fail 次数时无从判断，§13.3 就卡在这里。
                      "codes": {str(c): sum(1 for w in wlog if w.get("status") == c)
                                for c in sorted({w.get("status") for w in wlog})},
                      "err": next((w.get("err") for w in wlog if w.get("err")), "")}}
    out.update(_summary(hits, wall=round(time.time() - t0, 1)))
    print("[storm %s] p50=%s p95=%s max=%s 503=%s inc=%s writes=%s busy=%s"
          % (scene, out["srv_p50"], out["srv_p95"], out["srv_max"], out["status503"],
             out["incomplete"], out["writes"]["n"], out["busy_share"]), flush=True)
    return out


# ── cold：后台重建本体 ─────────────────────────────────────────
KINDS = {"repl": "/api/insights/replenishment",
         "purchase": "/api/insights/purchase",
         "disposal": "/api/insights/disposal-suggestions"}


def cold(kinds=None, timeout=150):
    """触发一次后台重建，读 `list_head.timing` 分段（calc / write / total）。

    做法：把该 kind 的 `stale` 置 1（可逆）→ 打一次读接口 → 轮询 gen 递增。
    跑完把 `note` 复位（不留诊断标记，红线 16 尾注同理）。
    """
    kinds = kinds or ["repl", "purchase", "disposal"]
    out = {"mode": "cold", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
    for kind in kinds:
        if not K.init_db():
            out["error"] = "需要 DB 直连（.env）；cold 场景靠 stale 标记触发"
            break
        row = K.QY("SELECT gen FROM list_head WHERE kind=%s ORDER BY gen DESC LIMIT 1", [kind])
        g0 = (row[0] or {}).get("gen") or 0 if row else 0
        K.QY("UPDATE list_head SET stale=1, note='perf_baseline' WHERE kind=%s", [kind])
        t0 = time.time()
        trig = K.hit(kind if kind in ENDPOINTS else "repl",
                     path=KINDS[kind] + "?channel=jd&page=1&page_size=300", timeout=120)
        rec = {"kind": kind, "trigger_srv": trig["srv"], "trigger_status": trig["status"]}
        while time.time() - t0 < timeout:
            r = K.QY("SELECT gen,timing,stale,row_cnt FROM list_head WHERE kind=%s "
                     "ORDER BY gen DESC LIMIT 1", [kind])
            if r and r[0]["gen"] > g0 and not r[0]["stale"]:
                rec.update({"gen": r[0]["gen"], "rows": r[0]["row_cnt"],
                            "timing": r[0]["timing"],
                            "trigger_to_done_s": round(time.time() - t0, 1)})
                break
            time.sleep(1.5)
        else:
            rec["timeout"] = True
        try:
            K.QY("UPDATE list_head SET note='ok' WHERE kind=%s AND note='perf_baseline'", [kind])
        except Exception:
            pass
        out["items"].append(rec)
        print("[cold %s] %s" % (kind, json.dumps(rec, ensure_ascii=False)[:200]), flush=True)
    return out


# ── first：真首建（无旧版本可读） ───────────────────────────────
def first(kind="repl", days=91, conc=4):
    """用一个**从未建过**的 `days` scope 触发全新构建，测单发与 4 并发。

    结束后**必须清理**该 scope 的 snapshot/head（否则留下孤儿快照 —— 曾有过 4.8 万行事故）。
    """
    if not K.init_db():
        return {"mode": "first", "error": "需要 DB 直连（.env）"}
    path = KINDS[kind] + "?channel=jd&days=%d&page=1&page_size=300" % days
    out = {"mode": "first", "kind": kind, "days": days, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        single = K.hit(kind, path=path, timeout=150)
        out["single"] = {"srv": single["srv"], "status": single["status"],
                         "rows": single["rows"], "e2e": single["e2e"]}
        with ThreadPoolExecutor(max_workers=conc) as ex:
            t0 = time.time()
            rr = list(ex.map(lambda _: K.hit(kind, path=path, timeout=150), range(conc)))
            out["conc"] = {"wall_s": round(time.time() - t0, 2), "n": conc,
                           "srv": [x["srv"] for x in rr],
                           "status": [x["status"] for x in rr],
                           "rows": [x["rows"] for x in rr],
                           "p95": K.pct([x["srv"] for x in rr], 0.95)}
        print("[first %s] single=%s conc=%s" % (kind, out["single"], out["conc"]), flush=True)
    finally:
        try:
            n1 = K.QY("DELETE FROM list_snapshot WHERE kind=%s AND scope LIKE %s",
                      [kind, "%%|%d|%%" % days])
            n2 = K.QY("DELETE FROM list_head WHERE kind=%s AND days=%s AND scope LIKE %s",
                      [kind, days, "%%|%d|%%" % days])
            out["cleanup"] = {"snapshot": n1, "head": n2}
        except Exception as e:
            out["cleanup"] = {"err": str(e)[:120]}
    return out


# ── noise：验证"这轮测量是否可信" ───────────────────────────────
def noise(rounds=3, secs=12, readers=8):
    """同一配置多轮 + 相位标注。用来判断"某次差异是不是噪声"。

    一次跑两组：`with_write`（带写，预期 CV 很大）与 `read_only`（纯读锁相位，预期 CV≈0.04）。
    若 read_only 的 CV 也很小、with_write 很大 → 差异来自写触发的重建，不是代码。
    """
    out = {"mode": "noise", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "groups": {}}
    for name, writers in (("with_write", 2), ("read_only", 0)):
        vals = []
        for i in range(rounds):
            ph = K.wait_quiet(60)
            s = storm(scene="CUSTOM", readers=readers, writers=writers, secs=secs, write_gap=1.5)
            s["phase"] = ph
            vals.append(s["srv_p50"])
            out["groups"].setdefault(name, []).append(s)
        out.setdefault("dispersion", {})[name] = {"vals": vals, "cv": K.cv(vals),
                                                  "range_ratio": round(max(vals) / min(vals), 2)
                                                  if min(vals) else None}
    return out
