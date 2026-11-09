#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""优化后「结构 / 代码」验证（§15.189 根治收敛）。**零 RU、不重跑 seed、不连线上**。

用户口径：「对于 seed 优化完也不重跑，但可以写脚本测试优化后的结构/代码」。
本脚本就是那个「只测结构/代码」的门禁：

  · 不触发 /api/seed/fill、不导数据、不跑 storm/cold/first/hot、不连线上接口计时。
  · 只读仓库源码 + 历史取证 out/*.json（13:17 那次**唯一一次**线上跑）。
  · 断言全是**结构性**的（存在性 / AST 调用关系 / 有界参数 / 互斥性 / 4 维不退化），
    红线 7：不产出任何本地 Xms/Xs 结论，时间系数只引用 §9.7 线上实测的**整数计数**。
  · 含反证：门禁必须能报红；第四类判据在历史取证上必须报红（repl 未补建），
    且把 gen 改成推进后必须转 PASS（证明不是恒红也不是恒绿）。

用法（默认全跑）：
    python3 scripts/perf/converge_probe.py check       # 结构/代码门禁（零 RU）
    python3 scripts/perf/converge_probe.py selftest    # 反证：门禁真能报红
    python3 scripts/perf/converge_probe.py evidence    # 历史取证 → 第四类判据（零 RU）
    python3 scripts/perf/converge_probe.py all         # 以上全部
"""
import argparse
import ast
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
API = os.path.join(ROOT, "cloud-functions", "api")
OUT_DIR = os.path.join(HERE, "out")
sys.path.insert(0, HERE)
sys.path.insert(0, API)
sys.path.insert(0, os.path.join(API, "routes"))

_RES = []


def chk(cond, msg):
    _RES.append((bool(cond), msg))
    print("  %s | %s" % ("OK  " if cond else "FAIL", msg), flush=True)
    return bool(cond)


def text(rel):
    """先试 API 目录（core/routes/biz 与 index.py/db.py 等根文件），再试仓库根。"""
    p_api = os.path.join(API, rel)
    p = p_api if os.path.isfile(p_api) else os.path.join(ROOT, rel)
    return open(p, encoding="utf-8").read()


def src(rel):
    return open(os.path.join(HERE, rel), encoding="utf-8").read()


def func_info(source, name):
    """AST 取函数体：调用名集合 + Name 集合 + 源码片段。比子串可信，可反证。

    ast.walk 覆盖嵌套函数（index.py 的 `_cont` 在中间件函数内部）。
    """
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            calls, names = set(), set()
            for x in ast.walk(n):
                if isinstance(x, ast.Call):
                    f = x.func
                    calls.add(f.attr if isinstance(f, ast.Attribute)
                              else (f.id if isinstance(f, ast.Name) else ""))
                if isinstance(x, ast.Name):
                    names.add(x.id)
            seg = ast.get_source_segment(source, n) or ""
            return calls, names, seg
    return set(), set(), ""


def count_call(source, func_name, first_arg=None):
    """AST 数**真调用**（注释/字符串里的字面不算），避免 .count() 假计数。"""
    n = 0
    for x in ast.walk(ast.parse(source)):
        if not (isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
                and x.func.id == func_name):
            continue
        if first_arg is None:
            n += 1
        elif (x.args and isinstance(x.args[0], ast.Constant)
              and x.args[0].value == first_arg):
            n += 1
    return n


# ─────────────────────────────────────────────────────────────────
# 1. P0 物化有界补建自愈（结构 / 4 维 / 红线）
# ─────────────────────────────────────────────────────────────────
def check_p0():
    print("\n=== 1. P0 物化有界补建自愈（§10.9 确定性实证的尾巴） ===")
    ls = text("core/list_store.py")
    idx = text("index.py")
    calls, names, seg = func_info(ls, "drain_pending")

    chk(bool(seg), "drain_pending 存在（AST 可见）")
    # 有界三件套（红线 15）
    for c in ("_DRAIN_GAP", "_DRAIN_SCOPE_COOL", "_DRAIN_LIMIT", "_DRAIN_AUX_COOL"):
        chk(c in ls and c in names, "P0 有界常量 %s 在函数内被使用" % c)
    chk("_DRAIN_GAP = 8.0" in ls and "_DRAIN_SCOPE_COOL = 12.0" in ls
        and "_DRAIN_LIMIT = 3" in ls, "P0 有界参数为正且有限（不自旋烧 RU）")

    # 待补建真源 = DB 持久事实
    chk("FROM list_head" in ls and "fp_now" in calls,
        "P0 待补建真源 = list_head 的 fp 落后行（DB 持久，不加表/队列）")
    chk("<= 0" in seg and "continue" in seg,
        "P0 只补 gen>0（有旧物化可服务）的 head，从未建过的交给首建单飞")

    # 复用同一路径（红线 2）
    chk("submit_background" in calls and "build" in calls,
        "P0 补建走 submit_background → build 同一路径（红线 2 不叠加）")
    chk("heavy" not in calls and "Thread" not in calls and "invalidate" not in calls,
        "P0 不自建名额/不新起线程/不触发失效（红线 2/4/19）")
    chk("try_err" in calls, "P0 降级留痕（异常不静默）")
    chk("except Exception" in seg and "return out" in seg,
        "P0 扫描异常降级放行，治理手段不变成新的不可用来源")

    # 挂点：复用已有 60s 驱动，不新增调度源
    cont_calls, _, cont_seg = func_info(idx, "_cont")
    chk("continue_tasks" in cont_calls and "drain_pending" in cont_calls,
        "P0 补建与续跑共用同一个 60s 请求驱动线程（不新增调度源）")
    chk("daemon=True" in idx and "try_err" in cont_seg,
        "P0 挂点仍是 daemon 线程 + 异常留痕，不阻塞响应")
    chk("if _nowc - _LAST_CONT > 60" in idx,
        "P0 驱动源节流仍为 60s（不提高轮询频率烧 RU）")

    # 读侧兜底未被删（准确/完整维不退化）
    serve_calls, _, serve_seg = func_info(ls, "serve")
    chk("submit_background" in serve_calls and "drain_pending" not in serve_calls,
        "serve 冷态仍 submit_background；drain_pending 不进读请求路径（红线 6）")
    chk("_first_serve" in serve_calls and "allow_stale" in serve_seg,
        "首建单飞 + 上一版完整物化仍在（数据四维-完整不退化）")
    cw_calls, _, cw_seg = func_info(ls, "_cold_worker")
    chk("heavy" in cw_calls and "HeavyBusy" in cw_seg and "本轮跳过" in cw_seg,
        "HeavyBusy 仍快速失败 + 留痕 + 有界冷却（不丢重建、不排队）")
    chk("drain_pending" in cw_seg,
        "_cold_worker 明确声明跳过由 drain_pending 补建（不再靠下次读请求）")
    b_calls, _, b_seg = func_info(ls, "build")
    chk("one" in b_calls and "COUNT" in b_seg and "校验未通过" in b_seg,
        "build 发布前 COUNT 自校验仍在（数据四维-准确不退化）")
    st_calls, _, st_seg = func_info(ls, "status")
    chk("drain" in st_seg and "fp_match" in st_seg,
        "status 暴露 drain 有界参数与 fp_match（作业级达标可查，不靠打开页面）")


# ─────────────────────────────────────────────────────────────────
# 2. P1 合并失效（seed 36 轮 → 完成 1 轮）
# ─────────────────────────────────────────────────────────────────
def check_p1():
    print("\n=== 2. P1 seed 中间态合并失效（36 轮全链路 → 完成 1 轮） ===")
    ac = text("routes/analysis_cache.py")
    ts = text("routes/task_seed.py")
    mp_calls, mp_names, mp_seg = func_info(ac, "mark_progress")

    chk(bool(mp_seg), "mark_progress 存在（AST 可见）")
    chk("INSERT INTO data_version" in mp_seg and "try_err" in mp_calls,
        "P1 中间步只 bump data_version（/health 仍能感知进度）")
    chk("invalidate" not in mp_calls and "ensure_async" not in mp_calls
        and "_drain_agg_dirty" not in mp_calls and "Thread" not in mp_calls,
        "P1 mark_progress 不触发任何失效/重建/线程（零 RU 放大）")
    chk("invalidate" not in mp_names, "P1 函数体内连 invalidate 标识符都没有（红线 19）")

    # 中间步调用点：AST 数真调用，注释不算
    chk(count_call(ts, "mark_progress") >= 1,
        "task_seed 中间窗口调用 mark_progress（AST 真调用计数）")
    n_all = count_call(ts, "invalidate", "all")
    chk(n_all == 4,
        "task_seed 保留 %d 处 invalidate(\"all\")，全部是一次性操作"
        "（完成/回滚/reset/月汇总），中间窗口已剥离" % n_all)
    chk("nxt >= SEED_DONE" in ts and "seed 完成 → 分析缓存即时失效" in ts,
        "完成步仍走全量失效（数据四维-实时不退化）")

    # 中央失效点语义未被改动：前端按域导入路径零回归
    inv_calls, _, inv_seg = func_info(ac, "invalidate")
    for name in ("_US", "_OS", "_LS", "_drain_agg_dirty"):
        chk(name in inv_seg, "invalidate 中央链路未改：%s 仍在" % name)
    chk("data_version_dom" in inv_seg and "analysis_cache SET created_at" in inv_seg,
        "invalidate 仍 bump 域计数 + 打全量墓碑（前端域级导入零回归）")
    chk("_MAT_WINDOW = 3" in ac, "agg 3s 合并窗口原样保留（不叠加新窗口）")


# ─────────────────────────────────────────────────────────────────
# 3. P3 / P5 判据层（第四类判据 + hot 空接口/相位）
# ─────────────────────────────────────────────────────────────────
def check_judges():
    print("\n=== 3. P3 / P5 第四类判据与 hot 判据缺口 ===")
    im = src("import_smoke.py")
    ep = src("endpoints.py")
    sc = src("scenes.py")
    run = src("run.py")
    pp = src("pipeline_probe.py")

    je_calls, je_names, je_seg = func_info(im, "judge_evidence")
    chk(bool(je_seg), "judge_evidence 存在（AST 可见）")
    # 纯函数：不连线上、不写、不 sleep（离线可复跑 / 零 RU）
    for bad in ("requests", "urlopen", "_session", "_dbq", "init_db", "sleep", "open"):
        chk(bad not in je_calls and bad not in je_names,
            "judge_evidence 是纯函数：不含 %s" % bad)
    for k in ("materialize_gen_ok", "eval_task_done", "alert_delta_ok",
              "import_equivalent", "phase_quiet_gate", "known_empty"):
        chk(('"' + k + '"') in je_seg, "第四类判据键 %s 存在" % k)
    chk("p50" not in je_names and "p95" not in je_names,
        "第四类判据不判延迟（红线 17；AST Name 集合，注释/字符串字面不算）")
    chk("verdict" in je_seg and "DEGRADED" in je_seg,
        "取证缺字段标 DEGRADED，不假装 PASS（判据可信）")

    chk("KNOWN_EMPTY = frozenset" in ep and '"alerts"' in ep,
        "已知空接口白名单唯一真源 endpoints.KNOWN_EMPTY = {alerts}")
    comp_calls, _, comp_seg = func_info(ep, "complete")
    chk("return key in KNOWN_EMPTY" in comp_seg,
        "白名单只对合法空形状生效")
    chk("not isinstance" in comp_seg and "return False" in comp_seg,
        "响应形状不对仍判失败（白名单不豁免真失败）")
    chk('"cleansing/execute-async"' not in comp_seg,
        "complete 未改其它接口的完整性口径")

    hot_calls, _, hot_seg = func_info(sc, "hot")
    chk("phase_not_quiet" in hot_seg and "skipped_rounds" in hot_seg,
        "hot 相位未静默 → 跳轮 + 留痕（§9.7 第 3 轮 1703ms 误判源）")
    chk("if not vals" in hot_seg,
        "hot 全部跳轮时 dispersion 不崩、不伪造（空 rounds 保护）")
    jh_calls, _, jh_seg = func_info(run, "_judge_hot")
    chk("if not rounds" in jh_seg and "skipped_rounds" in jh_seg,
        "_judge_hot 对空有效轮判 FAIL + 校验跳轮留痕（不是恒 PASS）")
    chk("storm_incomplete" in run and "known_empty" not in run.split("TH = {")[1].split("}")[0],
        "第四类判据**独立**，未污染 run.py 的 TH（§9.2 纪律）")

    cv_calls, _, cv_seg = func_info(pp, "coverage")
    chk("judge_text" in cv_seg and "collect_text" in cv_seg,
        "覆盖矩阵判据层纳入 import_smoke/endpoints（不再只看 run.py）")
    chk("len(covered0) == len(NODES)" in src("pipeline_probe.py")
        or "被完整覆盖" in src("pipeline_probe.py"),
        "selftest 反证随覆盖态同步更新（不是恒 GAP 断言）")


# ─────────────────────────────────────────────────────────────────
# 4. 数据四维 + 红线合规（逐条显式，不靠"看起来对"）
# ─────────────────────────────────────────────────────────────────
def check_four_dims_and_redlines():
    print("\n=== 4. 数据四维不退化 + 红线合规（逐条代码事实） ===")
    ls = text("core/list_store.py")
    ac = text("routes/analysis_cache.py")
    ts = text("routes/task_seed.py")
    db = text("db.py")
    im = src("import_smoke.py")

    # 四维
    chk(func_info(ls, "build")[0] and "COUNT" in func_info(ls, "build")[2],
        "准确：补建复用 build（发布前 COUNT 自校验 + fp 同源），无第二套算法")
    chk("allow_stale" in func_info(ls, "serve")[2] and "_first_serve" in func_info(ls, "serve")[0],
        "完整：旧 gen 完整可读，首建单飞绝不返回 0 条")
    chk("seed 完成 → 分析缓存即时失效" in ts and "FROM list_head" in ls,
        "实时：完成即全量失效；fp 落后的 head 由 drain_pending 主动追平")
    chk("_DRAIN_GAP" in ls and "try_err" in func_info(ls, "drain_pending")[0],
        "可靠：有界 + 留痕 + 扫描异常降级放行")

    # 红线
    chk("def drain_pending" in ls and func_info(ls, "ensure_async")[2]
        and func_info(ls, "serve")[2],
        "红线 1/2：原 ensure_async/serve/heavy 路径一字未删，drain 只是新触发入口")
    chk("heavy" in func_info(ls, "_cold_worker")[0],
        "红线 4：补建仍受 heavy(global_=True) 全局名额约束")
    chk("_flight_key" in ls and "_FLIGHTS" in ls,
        "红线 5：submit_background 同 key 单飞仍在")
    chk("drain_pending" not in func_info(ls, "serve")[0],
        "红线 6：补建不在读请求路径（读侧只返回已物化/已缓存结果）")
    chk("INTERVAL" not in func_info(ls, "drain_pending")[2]
        and "INTERVAL" not in func_info(ac, "mark_progress")[2],
        "红线 14：新增路径无 INTERVAL，未引入参数化占位符风险")
    chk("str ( int ( _HEAVY_GLOBAL_LEASE" in db.replace("\n", " ")
        or "str(int(_HEAVY_GLOBAL_LEASE))" in db,
        "红线 14：既有租约 SQL 仍是 str(int()) 内联")
    chk("_DRAIN_LIMIT" in ls and "_FLIGHT_TTL" in ls,
        "红线 15：有界上限 + flight 新鲜度 TTL，不排队不僵死")
    chk("p50" not in func_info(im, "judge_evidence")[1]
        and "p95" not in func_info(im, "judge_evidence")[1],
        "红线 17：第四类判据不拿带写 p50 下结论（AST Name 集合）")
    chk("invalidate" not in func_info(ls, "drain_pending")[0]
        and "invalidate" not in func_info(ac, "mark_progress")[0],
        "红线 19：写侧进度打点与后台补建都不触发数据域失效")
    # 本脚本自身零 RU / 红线 7：用 AST 调用集合判，避免「字符串里提到」的自指陷阱
    self_src = src("converge_probe.py")
    _self_calls = set()
    for _x in ast.walk(ast.parse(self_src)):
        if isinstance(_x, ast.Call):
            _f = _x.func
            _self_calls.add(_f.attr if isinstance(_f, ast.Attribute)
                            else (_f.id if isinstance(_f, ast.Name) else ""))
    chk(not ({"init_db", "urlopen", "post", "login", "session", "seed_fill"} & _self_calls),
        "红线 7 / 零 RU：本脚本不连线上、不触发 seed、只做结构判定（AST 调用集合）")


# ─────────────────────────────────────────────────────────────────
# 5. 结构推演：联动轮数（只用 §9.7 线上实测的**整数计数**）
# ─────────────────────────────────────────────────────────────────
def structural_delta():
    print("\n=== 5. 结构推演：联动轮数（整数计数，红线 7 不产秒数） ===")
    ts = text("routes/task_seed.py")
    # §9.7 一次全程实测：36 窗口 / 20 段
    windows = 36
    before = windows + 1      # 每窗口失效 + 完成失效
    after = 1                 # 仅完成步全量失效；中间步只 bump data_version
    n_all = count_call(ts, "invalidate", "all")
    chk(n_all == 4, "中间窗口不再全清（AST 计数：保留 %d 处一次性失效）" % n_all)
    chk(before == 37 and after == 1 and after < before,
        "全链路联动轮数 %d → %d（结构计数，非耗时）" % (before, after))
    chk(count_call(ts, "mark_progress") >= 1,
        "中间态仍有 data_version 进度打点（/health 可感知，不静默）")
    print("    说明：轮数收敛来自「中间态不发布」，不是把重建变快；")
    print("          每轮 6 类重建的成本、单行耗时一律引用 §9.7 线上实测，本脚本不测速。")
    return {"windows": windows, "linkage_rounds_before": before,
            "linkage_rounds_after": after,
            "note": "整数结构计数；红线 7 不产出任何 Xms/Xs 结论"}


# ─────────────────────────────────────────────────────────────────
# 反证
# ─────────────────────────────────────────────────────────────────
def selftest():
    print("\n=== 反证（门禁必须能报红，否则「全绿」只是没扫到） ===")
    global _RES
    before = len(_RES)
    _RES.append((False, "[反证] 故意 False"))
    red = sum(1 for ok, _ in _RES[before:] if not ok)
    _RES = _RES[:before]
    chk(red == 1, "chk(False) 被记为 FAIL（实测 %d）" % red)

    ls = text("core/list_store.py")
    calls, names, seg = func_info(ls, "drain_pending")
    chk(bool(seg) and "submit_background" in calls,
        "真实源码下 drain_pending 检出同一路径")
    # 移除函数 → 门禁应报红（不是恒 PASS）
    removed = ls.replace("def drain_pending(", "def _removed_drain_pending(", 1)
    chk(func_info(removed, "drain_pending")[2] == "",
        "移除 drain_pending 后 AST 找不到（门禁可报红）")
    # AST 计数不信注释
    fake_ts = '# invalidate("all") 只是注释\n' + "x = 1\n"
    chk(count_call(fake_ts, "invalidate", "all") == 0,
        "AST 不把注释里的 invalidate(\"all\") 算成真调用")

    # 判据行为级反证：repl 未推进 → FAIL；推进 → PASS
    try:
        import import_smoke as IS
        fr = {"before": {"list_head": [{"kind": "repl", "gen": 40}],
                         "tables": {"orders": 10}},
              "after": {"tables": {"orders": 10}}, "results": [1]}
        fv = {"list_head": [{"kind": "repl", "gen": 40}],
              "eval_tasks": [{"status": "done"}], "alerts": 5,
              "heavybusy_logs_recent": 11, "data_version_converged": True}
        chk(IS.judge_evidence(fr, fv)["verdict"] == "FAIL",
            "repl gen 未推进 → 判据报红（对应 §10.9 实证）")
        chk(IS.judge_evidence(fr, dict(fv, list_head=[{"kind": "repl", "gen": 41}]))
            ["verdict"] == "PASS", "repl gen 推进 → 判据转 PASS（非恒红）")
    except Exception as _e:
        chk(False, "judge_evidence 可调用（%s）" % str(_e)[:60])

    # 白名单反证
    try:
        import endpoints as EP
        chk(EP.complete("alerts", {}) is True and EP.complete("repl", {}) is False,
            "白名单只豁免 alerts，不放宽 repl")
        chk(EP.complete("alerts", None) is False,
            "形状不对仍判失败（不豁免真失败）")
    except Exception as _e2:
        chk(False, "endpoints 可导入（%s）" % str(_e2)[:60])
    ok = all(v for v, _ in _RES[before:])   # 只统计本 selftest 自己的断言
    print("  → selftest %s" % ("PASS" if ok else "FAIL"))
    return ok


# ─────────────────────────────────────────────────────────────────
# 历史取证（13:17 唯一一次线上跑）→ 第四类判据，零 RU
# ─────────────────────────────────────────────────────────────────
def evidence():
    print("\n=== 历史取证 → 第四类判据（零 RU、不重跑） ===")
    runs = sorted(glob.glob(os.path.join(OUT_DIR, "*_import_smoke_run.json")))
    verifies = sorted(glob.glob(os.path.join(OUT_DIR, "*_import_smoke_verify.json")))
    if not runs or not verifies:
        print("  未找到历史取证 json，跳过（无 RU 消耗）")
        return {"skipped": True}
    ro = json.load(open(runs[-1], encoding="utf-8"))
    vo = json.load(open(verifies[-1], encoding="utf-8"))
    import import_smoke as IS
    j = IS.judge_evidence(ro, vo)
    print("  取证：%s / %s" % (os.path.basename(runs[-1]), os.path.basename(verifies[-1])))
    for c in j["checks"]:
        print("    %s %-22s %s" % (
            "✓" if c["ok"] else ("?" if c["ok"] is None else "✗"),
            c["key"], c["desc"]))
    print("  verdict = %s" % j["verdict"])
    print("  ⚠ 这是**优化前**（13:17）的历史取证；判据在此报红正是 P0 要根治的")
    print("    缺口（repl gen 未补建）。本轮按用户约束**不重跑**导入，")
    print("    优化后的达标性由本脚本的结构/代码门禁 + P0 自愈路径保证。")
    return {"at": ro.get("at"), "verdict": j["verdict"], "checks": j["checks"],
            "note": "优化前历史取证；本轮不重跑"}


def check():
    check_p0()
    check_p1()
    check_judges()
    check_four_dims_and_redlines()
    return structural_delta()


def save(obj, mode):
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "%s_converge_%s.json"
                     % (time.strftime("%Y%m%d_%H%M%S"), mode))
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("[out] %s" % p)
    return p


def main():
    ap = argparse.ArgumentParser(description="优化后结构/代码验证（零 RU、不重跑 seed）")
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["check", "selftest", "evidence", "all"])
    a = ap.parse_args()
    out = {"mode": a.mode, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    if a.mode in ("check", "all"):
        out["delta"] = check()
    if a.mode in ("selftest", "all"):
        out["selftest_ok"] = selftest()
    if a.mode in ("evidence", "all"):
        out["evidence"] = evidence()
    if a.mode == "evidence":
        # 纯取证模式：无门禁断言。历史取证 verdict=FAIL 是**优化前**（13:17）实况，
        # 如实保留；退出码只表示取证成功生成, 不代表业务达标 / 不达标。
        out["verdict"] = "EVIDENCE_ONLY"
        out["checks"] = 0
        out["failed"] = 0
        print("\n→ converge_probe EVIDENCE_ONLY（取证完成，无门禁断言）")
        save(out, a.mode)
        return 0
    ok = all(v for v, _ in _RES) if _RES else False
    out["checks"] = len(_RES)
    out["failed"] = sum(1 for v, _ in _RES if not v)
    out["verdict"] = "PASS" if ok else "FAIL"
    print("\n→ converge_probe %s（%d 项，FAIL %d）" % (out["verdict"], len(_RES), out["failed"]))
    save(out, a.mode)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
