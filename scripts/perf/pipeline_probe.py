#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链路「集中导入 → 失效 → 物化/评估联动」结构性验证 + 基准覆盖矩阵（2026-11-09）

回答四件事（用户 §1）：
  ① 一次全程跑暴露的优化点，能否映射到「现实集中导入不同渠道/不同类型数据」；
  ② 物化重建 / 评估联动等任务是否达标；
  ③ 按目前性能基准（scripts/perf/run.py 的 TH）是否拦得住；
  ④ 昨天 100 万单 / 10000 SKU 的预测性能是否站得住脚。

红线 7：本脚本**只做结构/代码判定**，零本地计时、不产出任何 Xms/Xs 结论。
所有时间/行数系数只引用线上实测：docs/PERF.md §9.7（一次全程）、§3（模块基准）、
/workspace/predict_scale.json（11-08 容量预测）。本脚本不连线上接口计时。

用法（默认零 RU，纯静态）：
    python3 scripts/perf/pipeline_probe.py check                    # 链路节点 + 机制常量门禁
    python3 scripts/perf/pipeline_probe.py check --db              # 追加只读 DB 状态（约 10 SELECT）
    python3 scripts/perf/pipeline_probe.py coverage               # 基准覆盖矩阵：拦得住吗
    python3 scripts/perf/pipeline_probe.py findings               # 一次全程的优化点 → 代码位置
    python3 scripts/perf/pipeline_probe.py simulate --batches 6 --types 4   # 结构推演（零 RU）
    python3 scripts/perf/pipeline_probe.py predict                # 100 万单 / 10000 SKU 复核
    python3 scripts/perf/pipeline_probe.py selftest               # 反证：门禁真能报红
"""
import argparse
import ast
import io
import json
import os
import sys
import time
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = os.path.join(ROOT, "cloud-functions", "api")
OUT_DIR = os.path.join(HERE, "out")
sys.path.insert(0, HERE)
sys.path.insert(0, API)
sys.path.insert(0, os.path.join(API, "routes"))

_RES = []
_SKIP = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
         tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)


def chk(cond, msg):
    _RES.append((bool(cond), msg))
    print("  %s | %s" % ("OK  " if cond else "FAIL", msg), flush=True)
    return bool(cond)


def _toks(path, drop_strings=False):
    """tokenize 取 token 文本：丢注释/换行/缩进，可选丢字符串字面量。

    丢注释是必须的：历史注释里写过「原白名单 (5,6,7,8)」，只按 # 去注释会误判成活代码。
    """
    out = []
    try:
        src = open(path, encoding="utf-8").read()
        for t in tokenize.generate_tokens(io.StringIO(src).readline):
            if t.type in _SKIP:
                continue
            if drop_strings and t.type == tokenize.STRING:
                continue
            out.append(t.string)
    except (tokenize.TokenError, OSError):
        pass
    return " ".join(out)


def _text(path):
    try:
        return open(path, encoding="utf-8").read()
    except OSError:
        return ""


def _th_keys():
    """从 run.py 的 TH 字面量提取判据键（ast，零 import、零副作用）。"""
    p = os.path.join(HERE, "run.py")
    tree = ast.parse(_text(p))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TH" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                return [k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def _src(rel):
    p = os.path.join(API, rel)
    return p if os.path.isfile(p) else os.path.join(ROOT, rel)


# ── 一次全程实测的线上系数（docs/PERF.md §9.7，禁止改成本地计时） ──────────
REAL = {
    "source": "docs/PERF.md §9.7 / docs/history/2026-11-09.md §15.187（线上 X-Srv-Ms / params.steps）",
    "wall_s": 1265.0, "pure_s": 1123.6,
    "segments": 20, "windows": 36, "drive_requests": 50,
    "orders": 446974, "products": 20000, "inventory": 170200,
    "batches": 182174, "snapshot": 206461, "sku_sales_stats": 256512,
    "sku_per_channel": 10000, "order_days": 90,
    "phase": [
        ("库存·JD·自有仓", 375.8, 4), ("库存·OTHER·自有仓", 295.5, 4),
        ("订单", 262.7, 6), ("物化·JD/OTHER", 79.5, 2), ("其余 14 阶段", 110.0, 20),
    ],
    "hot_p50_ms": 42, "hot_p95_ms": 449, "baseline_p50_ms": 32,
    "baseline_p95_ms": 509, "quiet_after_s": 151,
    "dv": "40→78，跑完只读 78→78→78 稳定",
}

# 11-08 容量预测（/workspace/predict_scale.json，线上系数外推，非本地计时）
PRED_1108 = {
    "target": {"sku": 5000, "orders_90d": 1000000, "days": 90},
    "agg_incremental_full_s": 444.0, "snapshot_full_s": 24.3,
    "repl_build_total_s": 1.4, "purchase_build_total_s": 1.4,
    "disposal_build_total_s": 1.5, "first_request_s": 1.7,
    "coverage_tgt": 0.892, "ratio_dss": 4.01,
}

LIMITS = {"edgeone_function_timeout_s": 180, "gateway_s": 120,
          "seed_window_budget_s": 75, "heavy_global": 2,
          "agg_budget_segments_per_round": 24, "cold_total_ms": 6000}


# ── 1. 链路节点 + 机制常量（零 RU 静态门禁） ────────────────────────────────
def static_checks():
    print("=== 1. 集中导入链路节点（存在性 + 顺序） ===")
    cl = _toks(_src("routes/cleansing.py"))
    ac = _toks(_src("routes/analysis_cache.py"))
    ts = _toks(_src("routes/task_seed.py"))
    ls = _toks(_src("core/list_store.py"))
    db = _toks(_src("db.py"))
    ek = _toks(_src("core/eval_kick.py"))
    sc = _text(os.path.join(HERE, "scenes.py"))
    kit = _text(os.path.join(HERE, "kit.py"))

    # ① 前端导入目标 → 域级失效映射（现实集中导入的真源）
    try:
        import seed_import as SI
        spec = set(SI._SPEC)
    except Exception as e:
        spec = set()
        chk(False, "seed_import._SPEC 可导入（%s）" % str(e)[:60])
    chk(len(spec) >= 8, "导入类型 %d 类 ≥ 8（%s）" % (len(spec), ",".join(sorted(spec))))
    dom = _toks(_src("routes/cleansing.py"))
    for t in ("order", "inventory", "product", "supplier", "gmv_flow", "inbound", "outbound"):
        chk(('"' + t + '"') in dom or ("'" + t + "'") in dom,
            "_TARGET_DOMAIN 覆盖 %s" % t)

    # ② 导入 done 分支的四条联动（评估/采购/新鲜度/失效），顺序不要求但必须齐全
    for name, needle in (
            ("评估联动 kick_eval_task", "kick_eval_task"),
            ("采购需求 kick_purchase_alerts", "kick_purchase_alerts"),
            ("数据新鲜度 data_freshness", "data_freshness"),
            ("域级失效 invalidate", "invalidate ( _TARGET_DOMAIN")):
        chk(needle in cl.replace("'", '"') or needle in cl, "cleansing.done 含 %s" % name)

    # ③ invalidate 中央失效点触发的 6 类后台重建（这是「一次写放大」的结构根因）
    rebuild_nodes = (
        ("unit_store 判定模型", "_US . ensure_async"),
        ("own_store 自有仓健康", "_OS . ensure_async"),
        ("list_store 建议行 ×3 kind", "_LS . ensure_async"),
        ("agg 日销增量重建", "_drain_agg_dirty"),
        ("data_version_dom 按域计数", "data_version_dom"),
        ("analysis_cache 打墓碑", "analysis_cache SET created_at"),
    )
    for name, needle in rebuild_nodes:
        chk(needle in ac, "invalidate 连锁节点存在：%s" % name)
    chk("_MAT_WINDOW = 3" in _text(_src("routes/analysis_cache.py")),
        "agg 合并窗口 3s（只合并同批连续调用，跨窗口/跨批次无效）")

    # ④ 全局名额与 HeavyBusy 跳过语义（决定「是否全部建成」）
    chk('"HEAVY_GLOBAL" , "2"' in db,
        "heavy 全局名额默认 2（跨实例 heavy_slot）")
    chk("str ( int ( _HEAVY_GLOBAL_LEASE ) )" in db,
        "INTERVAL 用 str(int()) 内联（红线 14）")
    lst = _text(_src("core/list_store.py"))
    chk("exch HeavyBusy" not in ls and "except HeavyBusy" in lst,
        "list_store 捕获 HeavyBusy")
    chk("_BUSY_COOL = 2.0" in lst, "HeavyBusy 后同 key 有界冷却 2s")
    chk("本轮跳过" in lst, "HeavyBusy = 本轮跳过（不排队、抢不到名额只留痕）")
    # §15.189 P0: 跳过不再等于永久丢失 —— drain_pending 按 head.fp 落后统一补建。
    #   下面用 tokenize(丢注释/字符串) 切函数体, 避免「注释里提到」造成假 PASS。
    lst_toks = _toks(_src("core/list_store.py"), drop_strings=True)
    dp = lst_toks.split("def drain_pending")[1].split("def _swr_meta")[0]
    chk("def drain_pending" in lst, "P0 补建自愈入口 drain_pending 存在")
    chk("_DRAIN_GAP" in lst and "_DRAIN_SCOPE_COOL" in lst and "_DRAIN_LIMIT" in lst,
        "P0 有界三件套：全局间隔 / 单 scope 冷却 / 单轮上限（红线 15）")
    chk("fp_now" in dp and "FROM list_head" in lst,
        "P0 待补建真源 = list_head 的 fp 落后行（DB 持久，不新增表/队列）")
    chk("submit_background" in dp and "build" in dp,
        "P0 补建复用 submit_background → build 同一路径（红线 2 不叠加）")
    chk("invalidate" not in dp, "P0 补建不触发数据域失效（红线 19）")
    idx = _text(os.path.join(API, "index.py"))
    chk("drain_pending" in idx and "continue_tasks" in idx,
        "P0 补建挂在已有 60s 请求驱动线程（不新增调度源）")
    chk("_KICK_LOCK" in ek and "_MAX_SEGMENTS = 60" in ek,
        "评估联动单线程 + 分片上限 60（多批导入串行，超出靠 60s 兜底）")

    # ⑤ §15.189 P1: seed 中间窗口不再全清（36 轮 → 完成 1 轮）
    n_all = _text(_src("routes/task_seed.py")).count('invalidate("all")')
    chk(n_all >= 3,
        "task_seed 保留 %d 处 invalidate(\"all\")（完成/回滚/reset/月汇总等一次性操作）" % n_all)
    chk("mark_progress" in _text(_src("routes/task_seed.py")),
        "P1 seed 中间步改 mark_progress（零重建）")
    ac_txt = _text(_src("routes/analysis_cache.py"))
    chk("def mark_progress" in ac_txt, "P1 analysis_cache.mark_progress 存在")
    ac_toks = _toks(_src("routes/analysis_cache.py"), drop_strings=True)
    mp = ac_toks.split("def mark_progress")[1].split("def invalidate")[0]
    chk("INSERT INTO data_version" in ac_txt and "invalidate" not in mp,
        "P1 mark_progress 只 bump data_version，不触发失效/重建（红线 19）")
    chk("_rebuild_day_agg" in ts, "seed 完成阶段另起 _rebuild_day_agg(90) 后台线程")

    # ⑥ 现有基准的写操作不是「CSV 集中导入」
    chk("replenishment-config/seasons" in kit and "cleansing/execute" not in kit,
        "storm 的 real_write 只改季节系数（不覆盖 CSV 导入）")
    chk("def real_write" in kit, "kit.real_write 单一真源")
    chk("STORM_PRESETS" in sc and "S5" in sc,
        "storm 预设 S2/S4/S5 均为「单条轻写 + 并发读」")

    # ⑦ 判据集合本身的形状（供 coverage 复用）
    th = _th_keys()
    chk(len(th) >= 8, "run.py TH 判据键 %d 个：%s" % (len(th), ",".join(th)))
    return th


# ── 2. 基准覆盖矩阵：按目前性能基准是否拦得住 ──────────────────────────────
# 节点 → (链路位置, 达标判据(人类可读), 判据层关键字, 采集层精确锚点, 说明)
# 「拦得住」的唯一定义：判据层有对应断言，且采集层真采到该指标。
# 判据层只查 TH 键与 _judge_* 的 res.append 行；采集层只查 scenes.py 的精确锚点。
# 不用宽松子串（曾经 "import" 命中了 `import threading` 造成假 PASS）。
NODES = [
    ("物化集中重建是否全部建成", "invalidate→list_store.ensure_async×3 / unit / own",
     "所有 kind 的 head.gen 推进、fp 追平；P0 drain_pending 对 HeavyBusy 跳过有界补建",
     ("materialize_gen_ok", "fp_match", "built_all"),
     ("list_head", "judge_evidence"),
     "§15.189 P3: import_smoke.judge_evidence 判 materialize_gen_ok；P0 保证补建自愈"),
    ("评估联动任务是否完成", "cleansing.done→kick_eval_task→cleansing_eval_status",
     "_eval 任务非 running（done / 兜底接上），新鲜度/租约未僵死（红线 15）",
     ("eval_task_done", "task_done", "lease_ok"),
     ("eval_tasks", "cleansing_eval_status"),
     "§15.189 P3: judge_evidence 取 eval_tasks 判 running=0"),
    ("告警是否真的生成", "评估→rules→alerts 表",
     "告警增量可核，且**不取瞬时值**（收敛期持续增长是评估分片续跑）",
     ("alert_delta_ok", "generated", "nonempty_alerts"),
     ("alerts", "alert_delta"),
     "§15.189 P3: judge_evidence 判已生成 + 显式不取瞬时值"),
    ("集中导入作业是否完成且等价", "execute-async→status 接力→行数/四维键对账",
     "行数变化 0、四维键重复组 0、0 次重灌（§9.3 第四类判据）",
     ("import_equivalent", "watermark", "rowcount", "equivalent"),
     ("before", "tables"),
     "§15.189 P3: judge_evidence 用 before/after.tables 判行数对账"),
    ("导入后冷算相位是否安静", "完成阶段集中重建→heavy_slot 占用",
     "hot 前 phase_now().busy==0；未静默则**跳轮**而非把 p95 判 FAIL（红线 17）",
     ("phase_quiet_gate", "skipped_rounds", "phase_not_quiet", "quiet_gate"),
     ("wait_quiet", "skipped_rounds"),
     "§15.189 P5: scenes.hot 跳轮 + 留痕；run._judge_hot 对空有效轮判 FAIL"),
    ("已知空数据接口是否标注", "alerts 恒空（safety_qty=0）",
     "判据里显式列白名单，不把已知空接口算进 incomplete",
     ("known_empty", "KNOWN_EMPTY", "allow_empty", "whitelist"),
     ("KNOWN_EMPTY", "def complete"),
     "§15.189 P5: endpoints.KNOWN_EMPTY 唯一真源，complete() 只对合法空形状豁免"),
]


def _judged(th, texts, keys):
    """判据层：TH 键 或 判据定义行（丢纯注释行），是否含对应精确关键字。

    §15.189: 从「只扫 run.py 的 _judge_*」扩到「run.py + import_smoke 第四类判据
    + endpoints 白名单」—— 第四类判据按 §9.2 独立、不进 TH, 但同样是真判据,
    覆盖矩阵必须一起扫, 否则「只看 run.py」会得出恒 GAP 的错误结论。
    丢纯注释行是必须的: 历史注释里写过判据名, 只按字面扫会假 PASS。
    """
    if any(any(k.lower() in t.lower() for k in keys) for t in th):
        return True
    body = "\n".join(l for l in texts.splitlines()
                     if l.strip() and not l.strip().startswith("#"))
    return any(k.lower() in body.lower() for k in keys)


def _collected(sc_text, anchors):
    low = sc_text.lower()
    return any(a.lower() in low for a in anchors)


def coverage(th=None, quiet=False):
    th = th if th is not None else _th_keys()
    run = _text(os.path.join(HERE, "run.py"))
    sc = _text(os.path.join(HERE, "scenes.py"))
    im = _text(os.path.join(HERE, "import_smoke.py"))
    ep = _text(os.path.join(HERE, "endpoints.py"))
    # §15.189 判据层 = 接口稳态 TH/_judge_*(run.py) + **独立**第四类作业/联动判据
    #   (import_smoke.judge_evidence) + 已知空接口白名单事实(endpoints.KNOWN_EMPTY)。
    #   采集层 = scenes.py + import_smoke.verify 取证 + endpoints.complete 执行。
    #   §9.2/§10.5 纪律: 第四类判据独立、不进 run.py 的 TH —— 所以只看 run.py
    #   会把它们误判成 GAP；把它们纳入扫描才是「基准体系」的真实覆盖。
    judge_text = run + "\n" + im + "\n" + ep
    collect_text = sc + "\n" + im + "\n" + ep
    rows = []
    for name, chain, criterion, keys, anchors, note in NODES:
        j = _judged(th, judge_text, keys)
        s = _collected(collect_text, anchors)
        # 只有「判据 + 采集」双全才算拦得住；缺任一 = 拦不住
        verdict = "COVERED" if (j and s) else ("NOT_JUDGED" if s else "GAP")
        rows.append({"node": name, "chain": chain, "criterion": criterion,
                     "judge_layer": bool(j), "scene_layer": bool(s),
                     "verdict": verdict, "note": note})
    if not quiet:
        print("=== 基准覆盖矩阵（判据层 = run.py TH + import_smoke 第四类 + endpoints 白名单；采集层 = scenes/import_smoke/endpoints） ===")
        print("  %-30s %-8s %-8s %s" % ("联动节点", "判据层", "采集层", "结论"))
        for r in rows:
            print("  %-30s %-8s %-8s %s" % (
                r["node"][:30], "有" if r["judge_layer"] else "无",
                "有" if r["scene_layer"] else "无", r["verdict"]))
        gaps = [r for r in rows if r["verdict"] != "COVERED"]
        print("\n  → %d/%d 节点未被现有基准完整覆盖（%s）" % (
            len(gaps), len(rows), "拦不住" if gaps else "拦得住"))
        print("  逐项说明：")
        for r in rows:
            if r["verdict"] != "COVERED":
                print("    · %s：%s" % (r["node"], r["note"]))
    return rows


# ── 3. 一次全程跑的优化点（证据 → 代码位置 → 现实业务映射） ────────────────
FINDINGS = [
    ("自有仓两段 671.3s / 59.7%，单窗 72~79s 贴 75s 预算",
     "core/list_store.py M7 分批并行写 / routes/seed_import.py 自有仓 SPEC",
     "现实集中导入同样按 SKU 分片；多渠道 × 多仓型会线性叠加，是第一批该下推/并行的段"),
    ("每窗口 invalidate(\"all\") × 36 → 7 域 + 3 kind + unit/own + agg 全链路重建",
     "routes/task_seed.py:282 / routes/analysis_cache.py invalidate()",
     "现实前端导入是域级失效（更省），但每完成一批仍触发 1 轮全链路；连续多批 = 连续多轮"),
    ("完成阶段 HeavyBusy 跳过部分物化重建，且无后台重试",
     "core/list_store.py:520 本轮跳过 / db.py heavy(global_=True) 名额 2",
     "集中导入多批时更易撞名额；被跳过的 kind 要等下次读请求才补建，不打开页面就停在旧 gen"),
    ("_MAT_WINDOW=3s 合并不了 30~80s 间隔的窗口 → 缓存恒冷",
     "routes/analysis_cache.py:32", "现实导入批次间隔通常 >3s，合并窗口同样失效"),
    ("双驱动抢锁空转 14 次（0.8~1.9s），2 次 status 内嵌 40s 慢请求",
     "scripts/seed_drive.py / sync_tasks 租约", "现实前端轮询 + 中间件 continue_tasks 同构，空转不是错但占请求"),
    ("既有未修：available_balanced 终态差额、alerts 恒 0",
     "seed_fill._assert_available_balanced / safety_qty=0",
     "现实数据若 safety_qty 有值则 alerts 不恒 0；但差额类缺口会随集中导入放大"),
    ("hot 判据把 alerts 恒空算 incomplete、把导入后冷算相位算性能 FAIL",
     "scripts/perf/run.py _judge_hot / kit.wait_quiet",
     "基准自身缺口：缺「已知空接口白名单」与「相位安静」前置，会把集中导入后的正常态误判"),
]


def findings(quiet=False):
    if not quiet:
        print("=== 一次全程跑的优化点 → 代码位置 → 现实集中导入映射（证据：%s） ===" % REAL["source"])
        for i, (f, loc, biz) in enumerate(FINDINGS, 1):
            print("  %d. %s\n     代码: %s\n     映射: %s" % (i, f, loc, biz))
    return [{"finding": f, "code": loc, "business": biz} for f, loc, biz in FINDINGS]


# ── 4. 结构推演（纯计数，零 RU；红线 7：不产出任何耗时结论） ───────────────
def simulate(batches=6, types=4, quiet=False):
    # 每完成一批导入的结构事件数（从 static_checks 的链路节点读出，不猜）
    per_batch = {
        "import_tasks": 1, "eval_tasks": 1, "purchase_alert_kick": 1,
        "freshness_upsert": 1, "invalidate": 1,
        # invalidate 连锁：unit + own + list×3 + agg = 6 类后台重建请求
        "rebuild_requests": 6,
    }
    total = {k: v * batches for k, v in per_batch.items()}
    global_slots = LIMITS["heavy_global"]
    # 结构判断（不做时间推演）：同时发起的重建请求数 vs 跨实例名额
    conflict = max(0, per_batch["rebuild_requests"] - global_slots)
    # 一次全程的校准事实：36 窗口，完成阶段即出现 HeavyBusy 跳过
    calib = {"windows": REAL["windows"],
             "observed": "完成阶段集中重建触发 HeavyBusy，repl/purchase/disposal 部分重建被跳过"}
    out = {
        "input": {"batches": batches, "types": types,
                  "note": "batches = 渠道数×类型数（例：jd/other × order/inventory/inbound/outbound = 8）"},
        "per_batch_events": per_batch, "total_events": total,
        "structure": {
            "global_slots": global_slots,
            "rebuild_requests_per_batch": per_batch["rebuild_requests"],
            "oversubscribed_per_batch": conflict,
            "eval_thread": "单线程 _KICK_LOCK，_MAX_SEGMENTS=60；多批 _eval 任务串行，超出靠 60s 兜底",
            "heavybusy_policy": "快速失败 + 2s 冷却 + 等下次读请求补建（不排队、不自动重试）",
            "mat_window": "3s 合并窗口只吸收同批连续调用，跨批次无效",
        },
        "calibration_from_real_run": calib,
        "conclusion": [
            "机理与一次全程同源：一次写 → 一次全链路联动；连续 N 批 = 连续 N 轮，"
            "不是「数据量大才慢」，而是「联动轮数 × 每轮 6 类重建」。",
            "差异（现实更优）：前端导入按域失效（非 all）、单批行数远小于 seed 单窗 12 万行；"
            "但多出 eval_kick / purchase_alerts / data_freshness 三条 seed 没有的联动链。",
            "风险（现实更劣）：多渠道多类型并发完成时，6 类重建争 2 个全局名额；"
            "被 HeavyBusy 跳过的物化不自动补建，评估任务单线程排队。",
        ],
    }
    if not quiet:
        print("=== 集中导入结构推演（纯计数，零 RU；不产出耗时结论） ===")
        print("  输入：%d 批 × %d 类型，全局名额 %d" % (batches, types, global_slots))
        for k, v in total.items():
            print("    %-22s %d" % (k, v))
        print("  每批重建请求 %d 类 / 名额 %d → 结构性超额 %d 类/批" % (
            per_batch["rebuild_requests"], global_slots, conflict))
        print("  校准（一次全程）：%s" % calib["observed"])
        for c in out["conclusion"]:
            print("  · " + c)
    return out


# ── 5. 100 万单 / 10000 SKU 预测复核（线上实测系数，红线 7） ───────────────
def predict(quiet=False):
    import math
    tgt_orders = 1000000
    tgt_sku = 10000
    days = REAL["order_days"]
    # 当前实测规模：单渠道 10000 SKU，全库 orders 446,974（§9.7）
    cur_orders = REAL["orders"]
    ratio_orders = tgt_orders / float(cur_orders)
    # SKU 维度：目标 10000 SKU == 当前单渠道 SKU 数 → 物化/读接口行数不放大
    ratio_sku = tgt_sku / float(REAL["sku_per_channel"])
    # 日销覆盖率（11-08 泊松模型，口径同 predict_scale.py）
    lam_cur = cur_orders / float(REAL["sku_per_channel"]) / days
    lam_tgt = tgt_orders / float(tgt_sku) / days
    cov_tgt = 1 - math.exp(-lam_tgt)
    # 日销快照行数 ∝ SKU × 天 × 覆盖率；当前线上 jd 侧 99,951（predict_scale.json）
    dss_cur = 99951
    dss_tgt = tgt_sku * days * cov_tgt
    ratio_dss = dss_tgt / float(dss_cur * 2)  # 两渠道合计近似
    # 实测系数：agg_incremental 全量 110.6s（predict_scale.json pipeline_runs）
    agg_cur = 110.602
    agg_full_tgt = agg_cur * ratio_dss
    # seed 阶段线性外推（行数线性，§9.7 已证「规模 10× 是主因」）
    ord_phase = [p for p in REAL["phase"] if p[0] == "订单"][0]
    ord_s_tgt = ord_phase[1] * ratio_orders
    ord_win_tgt = ord_phase[2] * ratio_orders
    wall_tgt = REAL["wall_s"] * (1 + (ratio_orders - 1) * (ord_phase[1] / REAL["pure_s"]))
    out = {
        "target": {"orders": tgt_orders, "sku": tgt_sku, "days": days},
        "coefficients": {"source": REAL["source"],
                         "cur_orders": cur_orders, "cur_sku_per_channel": REAL["sku_per_channel"],
                         "agg_incremental_full_s": agg_cur,
                         "predict_1108": PRED_1108},
        "derived": {"ratio_orders": round(ratio_orders, 3), "ratio_sku": ratio_sku,
                    "lam_cur": round(lam_cur, 3), "lam_tgt": round(lam_tgt, 3),
                    "coverage_tgt": round(cov_tgt, 3), "dss_rows_tgt": int(dss_tgt),
                    "ratio_dss": round(ratio_dss, 2)},
        "extrapolation": {
            "seed_order_phase_s": round(ord_s_tgt, 1),
            "seed_order_windows": round(ord_win_tgt, 1),
            "seed_wall_s_upper": round(wall_tgt, 1),
            "agg_full_rebuild_s": round(agg_full_tgt, 1),
        },
        "limits": LIMITS,
        "verdict": [],
    }
    v = out["verdict"]
    v.append("读接口稳态：目标 10000 SKU == 当前实测单渠道 SKU 数，物化行数不放大；"
             "§3 的 repl/purchase/disposal build 2.1/2.1/3.0s 均 ≤ cold_total 6s 判据 → 站得住。")
    v.append("单次长作业：日销三窗口全量重建外推 ≈%.0fs（11-08 预测 444s），"
             "仍 > 180s 函数上限 / 120s 网关 → **单次调用站不住**。" % agg_full_tgt)
    v.append("分段续跑架构：一次全程已证 36 窗口 / 20 段接力可在 21.1 分钟完成；"
             "agg 有 24 段/轮预算 + 队列留痕 → **架构站得住**（前提是别把续跑改成单次全量）。")
    v.append("集中导入（现实口径）：单批行数远小于 seed 单窗 12 万行，"
             "约束不在「单次超时」而在「联动轮数」——见 simulate。")
    v.append("结论：昨天的预测结论仍成立且更精确 —— 瓶颈是长作业与联动轮数，不是读接口；"
             "100 万单 / 10000 SKU 不会击穿 §3 稳态基准，但会击穿「单次全量重建」的调用上限。")
    if not quiet:
        print("=== 100 万单 / 10000 SKU 预测复核（只用线上实测系数，红线 7） ===")
        print("  目标：%d 单 / %d SKU / %d 天；当前实测 %d 单 / %d SKU·渠道" % (
            tgt_orders, tgt_sku, days, cur_orders, REAL["sku_per_channel"]))
        for k, val in out["derived"].items():
            print("    %-18s %s" % (k, val))
        for k, val in out["extrapolation"].items():
            print("    %-22s %s" % (k, val))
        print("  对照 11-08 预测：agg_full 444s / snapshot 24.3s / 首请求 1.7s")
        for x in v:
            print("  · " + x)
    return out


# ── 6. 只读 DB 状态（约 10 SELECT，极低 RU） ───────────────────────────────
def db_checks():
    import kit as K
    if not K.init_db():
        chk(False, "无法直连 DB（.env 缺凭据）")
        return {}
    q = K.QY

    def scalar(sql):
        try:
            r = q(sql, [])
            return list(r[0].values())[0] if r else None
        except Exception as e:
            return "ERR:" + str(e)[:60]

    print("=== 只读 DB 状态（极低 RU，绝不写入） ===")
    st = {
        "data_version": scalar("SELECT v FROM data_version WHERE id=1"),
        "heavy_busy": scalar("SELECT COUNT(*) FROM heavy_slot WHERE holder<>'' "
                             "AND ts > NOW(6) - INTERVAL 300 SECOND"),
        "running_tasks": scalar("SELECT COUNT(*) FROM sync_tasks WHERE status='running'"),
        "alerts": scalar("SELECT COUNT(*) FROM alerts"),
        "list_head_kinds": scalar("SELECT COUNT(*) FROM list_head"),
        "unit_state_gen": scalar("SELECT MAX(gen) FROM unit_state_head"),
    }
    for k, val in st.items():
        print("  INFO| %-18s %s" % (k, val))
    chk(str(st["data_version"]).isdigit(), "data_version 可读（红线 19 收敛基线）")
    return st


# ── 7. 反证：门禁真能报红（DEVELOPMENT.md §7.1） ───────────────────────────
def selftest():
    print("=== 反证（门禁必须能报红，否则「全绿」只是没扫到） ===")
    global _RES
    before = len(_RES)
    _RES.append((False, "[反证] 故意 False（不打印，仅验 chk 会计红）"))
    red = sum(1 for ok, _ in _RES[before:] if not ok)
    _RES = _RES[:before]
    chk(red == 1, "chk(False) 被记为 FAIL（实测 %d）" % red)

    base = _th_keys()
    rows0 = coverage(base, quiet=True)
    covered0 = [r for r in rows0 if r["verdict"] == "COVERED"]
    chk(len(rows0) == len(NODES) and len(covered0) == len(NODES),
        "真实判据下 %d/%d 个联动节点被完整覆盖（实测 %d）" % (
            len(NODES), len(NODES), len(covered0)))

    # 反证 ① 纯注释里的判据名不算判据；真判据定义行才算（防假 PASS / 恒 COVERED）
    chk(_judged([], "# materialize_gen_ok 只是注释\n", ("materialize_gen_ok",)) is False,
        "纯注释里的判据名不算判据（防假 PASS）")
    chk(_judged([], 'checks.append(C("materialize_gen_ok", ...))',
                 ("materialize_gen_ok",)) is True,
        "真判据定义行才算判据（门禁不是恒 GAP）")

    # 反证 ② 行为级：白名单只对 alerts 生效，不放宽其它接口、不豁免真失败
    try:
        import endpoints as EP
        chk(EP.complete("alerts", {}) is True, "alerts 空结构判完整（白名单生效）")
        chk(EP.complete("repl", {}) is False, "repl 空结构仍判不完整（白名单未放宽）")
        chk(EP.complete("alerts", None) is False, "alerts 形状不对仍判失败（不豁免真失败）")
    except Exception as _e:
        chk(False, "endpoints 可导入（%s）" % str(_e)[:60])

    # 反证 ③ 行为级：第四类判据在「repl gen 未推进」上必须报红，推进后转 PASS
    #   （正是 §10.9 确定性实证的尾巴；证明判据既非恒 PASS 也非恒 FAIL）
    try:
        import import_smoke as IS
        fr = {"before": {"list_head": [{"kind": "repl", "gen": 40}],
                         "tables": {"orders": 10}},
              "after": {"tables": {"orders": 10}}, "results": [1]}
        fv = {"list_head": [{"kind": "repl", "gen": 40}],
              "eval_tasks": [{"status": "done"}], "alerts": 5,
              "heavybusy_logs_recent": 11, "data_version_converged": True}
        j0 = IS.judge_evidence(fr, fv)
        chk(j0["verdict"] == "FAIL", "repl gen 未推进 → 第四类判据报红（反证）")
        j1 = IS.judge_evidence(fr, dict(fv, list_head=[{"kind": "repl", "gen": 41}]))
        chk(j1["verdict"] == "PASS", "repl gen 推进 → 判据转 PASS（不是恒 FAIL）")
    except Exception as _e2:
        chk(False, "import_smoke.judge_evidence 可调用（%s）" % str(_e2)[:60])

    # 结构推演反证：0 批 → 0 事件；名额调大 → 超额归零
    s0 = simulate(batches=0, types=1, quiet=True)
    chk(s0["total_events"]["rebuild_requests"] == 0, "0 批 → 0 重建请求")
    s1 = simulate(batches=3, types=2, quiet=True)
    chk(s1["structure"]["oversubscribed_per_batch"] == 4,
        "6 类重建 / 2 名额 → 每批结构性超额 4 类（实测 %s）"
        % s1["structure"]["oversubscribed_per_batch"])
    ok = all(v for v, _ in _RES)
    print("  → selftest %s" % ("PASS" if ok else "FAIL"))
    return ok


def _save(obj, mode):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_pipeline_%s.json" % (time.strftime("%Y%m%d_%H%M%S"), mode))
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[out] %s" % p)


def main():
    ap = argparse.ArgumentParser(description="集中导入全链路结构性验证")
    ap.add_argument("mode", nargs="?", default="check",
                    choices=["check", "coverage", "findings", "simulate", "predict", "selftest"])
    ap.add_argument("--db", action="store_true", help="check 追加只读 DB 状态")
    ap.add_argument("--batches", type=int, default=6)
    ap.add_argument("--types", type=int, default=4)
    a = ap.parse_args()

    if a.mode == "check":
        th = static_checks()
        st = db_checks() if a.db else {}
        ok = all(v for v, _ in _RES)
        print("\n[check] %d 项：%s" % (len(_RES), "PASS" if ok else "FAIL"))
        _save({"checks": [{"ok": o, "msg": m} for o, m in _RES],
               "th_keys": th, "db": st, "real": REAL}, "check")
        return 0 if ok else 1
    if a.mode == "coverage":
        rows = coverage()
        _save({"coverage": rows, "conclusion": "拦不住" if any(
            r["verdict"] != "COVERED" for r in rows) else "拦得住"}, "coverage")
    elif a.mode == "findings":
        _save({"findings": findings(), "real": REAL}, "findings")
    elif a.mode == "simulate":
        _save(simulate(a.batches, a.types), "simulate")
    elif a.mode == "predict":
        _save(predict(), "predict")
    elif a.mode == "selftest":
        return 0 if selftest() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
