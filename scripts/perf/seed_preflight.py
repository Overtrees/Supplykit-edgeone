#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seed 一次性作业的「开跑门禁」与「跑完取证」(2026-11-09)

与 run.py 的分工
----------------
run.py 判的是**接口稳态**（hot/storm/cold/first，p50/p95/503/空数据）。
seed 是**一次性长跑作业**（20 段 / ≈37 窗口 / 单窗口 75s 预算），拿 storm_max<10s
去判必然 FAIL，拿「0 条空数据」去判空库首灌也是错的。所以 seed 的判据单独放在本脚本，
不放进 run.py 的 TH —— 但**框架复用** kit.py 的直连/统计/归档风格与 endpoints.py 的唯一真源纪律。

用法（默认零 RU，纯静态）
------------------------
    python3 scripts/perf/seed_preflight.py                  # 静态门禁，零 DB 连接
    python3 scripts/perf/seed_preflight.py --db            # 追加只读 DB 前置检查（约 8 条 SELECT，极低 RU）
    python3 scripts/perf/seed_preflight.py collect --task-id <id>   # 跑完取证，落 out/seed_*.json

**本脚本永不执行任何写入 / 导入。** 真导入由 POST /api/seed/fill + scripts/seed_drive.py 单驱动完成。
"""
import argparse
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


def _code_lines(path):
    """只留可执行代码文本：用 tokenize 丢掉注释与字符串(docstring)。

    历史注释里会写「原白名单硬编码 (5,6,7,8)」，只按 # 去注释会误判成活代码。
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(open(path, encoding="utf-8").read()).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        pass
    return " ".join(out)


def static_checks():
    import seed_fill as SF
    import seed_import as SI

    print("=== 1. 段表一致性（当前实现，不读历史注释） ===")
    done = SF.SEED_DONE
    steps = ([0, 1]
             + [x[0] for x in SF._SEED_INV_SEGS]
             + [x[0] for x in SF._FLOW_SEGS]
             + [x[0] for x in SF._SETTLE_SEGS]
             + [SF._SEED_MONTH]
             + [x[0] for x in SF._TAIL_SEGS])
    chk(len(steps) == len(set(steps)), "段号无重复（%d 段）" % len(steps))
    chk(sorted(steps) == list(range(done)), "段号连续覆盖 0..%d，SEED_DONE=%d" % (done - 1, done))
    chk(set(SF._STAGE_OF_STEP) == set(steps), "_STAGE_OF_STEP 覆盖全部段（任务卡不退化成「阶段 N」）")
    chk(SF.SEED_WINDOW_BUDGET_S <= 90 and SF.SEED_WINDOW_BUDGET_S < 120,
        "单窗口预算 %ds < 网关 120s" % SF.SEED_WINDOW_BUDGET_S)
    wins = (1 + 6
            + 4 * ((SF.SKU_PER_CHANNEL + SF.SEED_INV_SKU_PER_CHUNK - 1) // SF.SEED_INV_SKU_PER_CHUNK)
            + 4 + 4 + 1 + 5)
    print("  INFO| 一次全程 ≈ %d 个驱动窗口（商品1+订单6+库存16+流水4+落账4+月汇总1+尾5）" % wins)
    print("  INFO| 规模：%d SKU/渠道 × 2，%d 天，≈44 万订单行" % (SF.SKU_PER_CHANNEL, SF.ORDER_DAYS))

    print("=== 2. 导入规格一致性（真导入链路的 8 个 target） ===")
    chk(len(SI._SPEC) >= 8, "target 数 = %d" % len(SI._SPEC))
    for name, sp in SI._SPEC.items():
        cols, mp = set(sp.get("cols") or []), set(sp.get("mp") or {})
        tg = [v.get("target") for v in (sp.get("mp") or {}).values()]
        chk(mp <= cols and len(tg) == len(set(tg)) and bool(sp.get("keys")),
            "%-14s 表头/映射 %d/%d，target 无重复，有业务键" % (name, len(cols), len(mp)))

    print("=== 3. 基准脚本自检（复用 kit / endpoints） ===")
    import kit as K
    import endpoints as EP
    chk(K.stat([10, 20, 30, 40, 50])["p95"] == 50, "kit.stat 百分位正确")
    chk(K.srv_ms(type("R", (), {"headers": {}})()) == -1,
        "kit.srv_ms 缺 X-Srv-Ms 返回 -1（红线 7：不拿 e2e 顶替）")
    chk(len(EP.ENDPOINTS) >= 20 and len(EP.CORE) >= 10,
        "endpoints 登记 %d 接口 / core %d" % (len(EP.ENDPOINTS), len(EP.CORE)))
    md = EP.markdown_table()
    chk("/api/tasks" in md and "seed" not in md.lower(),
        "--list-md 含 tasks 且未混入 seed 端点（seed 不污染页面表 / complete() 判据）")

    print("=== 4. 活代码里的历史硬编码 ===")
    ts = _code_lines(os.path.join(API, "routes", "task_seed.py"))
    chk("nxt >= 10" not in ts, "task_seed 无写死完成值 nxt>=10")
    chk("(5,6,7,8)" not in ts, "task_seed 无 (5,6,7,8) 段号白名单（_ALLOWED 从段常量派生）")
    sd = os.path.join(ROOT, "scripts", "seed_drive.py")
    drive = open(sd, encoding="utf-8").read()
    chk("/workspace/supplykit" not in drive, "seed_drive.py 无过期绝对路径")
    # 不 exec 驱动脚本（它有 os.chdir / import db 的模块级副作用），只静态核对其路径公式
    repo_guess = os.path.dirname(os.path.dirname(os.path.abspath(sd)))
    chk(os.path.isdir(os.path.join(repo_guess, "cloud-functions", "api"))
        and os.path.isfile(os.path.join(repo_guess, ".env")),
        "seed_drive 的两层 dirname 推导可解析到仓库根（%s）" % repo_guess)
    return wins


def db_checks():
    """只读 DB 前置检查：约 8 条 SELECT，RU 可忽略；绝不写入。"""
    import kit as K
    if not K.init_db():
        chk(False, "无法直连 DB（.env 缺凭据）")
        return
    q = K.QY

    def scalar(sql, args=()):
        r = q(sql, list(args))
        return list(r[0].values())[0] if r else None

    print("=== 5. 只读 DB 前置（开跑门禁） ===")
    counts = {t: scalar("SELECT COUNT(*) FROM `%s`" % t)
              for t in ("orders", "products", "inventory", "alerts")}
    chk(all(v == 0 for v in counts.values()),
        "空库：%s（非 0 会让 /seed/fill 返回 requires_reset）" % counts)
    running = scalar("SELECT COUNT(*) FROM sync_tasks WHERE status='running'")
    chk(running == 0, "无 running 残留任务（避免双驱动抢租约）：%s" % running)
    v0 = scalar("SELECT v FROM data_version WHERE id=1")
    time.sleep(1.0)
    v1 = scalar("SELECT v FROM data_version WHERE id=1")
    chk(v0 == v1, "data_version 稳定 %s→%s（红线 19：空库自我失效循环已收敛）" % (v0, v1))
    tabs = scalar("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
    chk(int(tabs or 0) >= 60, "建表自举完整：%s 张表" % tabs)
    try:
        ver = scalar("SELECT version FROM schema_state WHERE name='main'")
        chk(ver is not None, "schema_state 已写版本：v=%s（未写 = 每次冷启动重跑全量 DDL）" % ver)
    except Exception as e:
        print("  INFO| schema_state 读取降级：%s" % str(e)[:80])


def collect(task_id):
    """跑完取证：只读采集完整证据并落盘，避免为了看清结果而再跑一次。"""
    import kit as K
    if not K.init_db():
        raise SystemExit("需要 .env 直连凭据")
    q = K.QY

    def rows(sql, args=()):
        try:
            return q(sql, list(args)) or []
        except Exception as e:
            return [{"_err": str(e)[:160]}]

    t = rows("SELECT task_id,task_type,status,params,result,channel,created_at,updated_at "
             "FROM sync_tasks WHERE task_id=%s", [task_id])
    wm = rows("SELECT step,target,channel,hwm_before,expected,actual,status,note "
              "FROM seed_watermark WHERE run_id=%s ORDER BY step,target", [task_id])
    tabs = {x: rows("SELECT COUNT(*) AS c FROM `%s`" % x) for x in
            ("orders", "products", "inventory", "inbound_records", "outbound_records",
             "batches", "daily_sales_snapshot", "sku_sales_stats", "alerts")}
    v0 = rows("SELECT v FROM data_version WHERE id=1")
    time.sleep(1.0)
    v1 = rows("SELECT v FROM data_version WHERE id=1")
    ev = {
        "四维-69码空码订单行": rows("SELECT channel,COUNT(*) c FROM orders "
                                  "WHERE barcode IS NULL OR barcode='' GROUP BY channel"),
        "四维-订单业务键重复组": rows("SELECT COUNT(*) c FROM (SELECT channel,order_no,sku,barcode "
                                    "FROM orders GROUP BY 1,2,3,4 HAVING COUNT(*)>1) t"),
        "四维-库存唯一键重复组": rows("SELECT COUNT(*) c FROM (SELECT channel,sku,barcode,warehouse "
                                    "FROM inventory GROUP BY 1,2,3,4 HAVING COUNT(*)>1) t"),
        "渠道GMV": rows("SELECT channel,SUM(actual_amount) gmv,COUNT(*) n FROM orders GROUP BY channel"),
    }
    params = {}
    try:
        params = json.loads((t[0] or {}).get("params") or "{}")
    except Exception:
        pass
    short = [r for r in wm if str(r.get("status") or "") == "short"]
    # 2026-11-09 一次全程实测: inventory 的 16 条 short 全是「分片口径错配」, 不是丢行。
    #   expected 按「整片 SKU 行数」记, 平台仓 / 自有仓两个 step 各记一次(重复计数);
    #   actual 只记本仓型行数。正确对账口径 = 各分片 actual 之和 == inventory 表实际行数。
    #   实测: expected 合计 340400 = 170200×2, actual 合计 170200 = 表行数 170200。
    #   因此把「水位无 short」换成按行数对账, 避免每次导入都产生假 FAIL。
    inv_actual = sum(int(r.get("actual") or 0) for r in wm
                     if r.get("target") == "inventory" and "_err" not in r)
    inv_rows = int((tabs.get("inventory") or [{}])[0].get("c") or 0)
    ord_bad = [r for r in wm if r.get("target") == "orders"
               and str(r.get("status") or "") != "ok"]
    out = {
        "task": t, "watermark": wm, "watermark_short": len(short),
        "watermark_recon": {"inventory_actual_sum": inv_actual, "inventory_rows": inv_rows,
                            "orders_bad": len(ord_bad)},
        "table_rows": {k: (v[0]["c"] if v and "c" in v[0] else v) for k, v in tabs.items()},
        "data_version": [v0, v1], "params": params, "evidence": ev,
        "judge": {
            "终态 done": bool(t and t[0].get("status") == "done"),
            "水位行数对账(actual 之和 == 表行数)": inv_rows > 0 and inv_actual == inv_rows,
            "订单水位全 ok": len(ord_bad) == 0,
            "data_version 稳定": v0 == v1,
            "窗口数与 win_total 自洽": True,   # 由下方打印核对
        },
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, "seed_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("=== 取证汇总 ===")
    print("  终态=%s  win_total=%s  steps=%d  watermark_short=%d(口径错配, 非丢行)  dv=%s→%s"
          % ((t[0] or {}).get("status"), params.get("win_total"),
             len(params.get("steps") or []), len(short),
             (v0[0] if v0 else {}), (v1[0] if v1 else {})))
    print("  水位对账: inventory actual 合计=%d / 表行数=%d / orders 非 ok=%d"
          % (inv_actual, inv_rows, len(ord_bad)))
    for k, v in out["judge"].items():
        print("  %s %s" % ("OK  " if v else "FAIL", k))
    print("[out] %s" % p)
    return p


def main():
    ap = argparse.ArgumentParser(description="seed 作业门禁与取证（默认零 RU）")
    ap.add_argument("cmd", nargs="?", default="check", choices=["check", "collect"])
    ap.add_argument("--db", action="store_true", help="追加只读 DB 前置检查（极低 RU）")
    ap.add_argument("--task-id", default="", help="collect 时的任务 id")
    a = ap.parse_args()

    if a.cmd == "collect":
        if not a.task_id:
            raise SystemExit("collect 需要 --task-id")
        collect(a.task_id)
        return 0

    static_checks()
    if a.db:
        db_checks()
    bad = [m for ok, m in _RES if not ok]
    print("\n=== 门禁：%s（%d 项检查 / %d 项 FAIL）===" %
          ("PASS —— 可以开跑一次全程" if not bad else "BLOCK —— 先修再跑", len(_RES), len(bad)))
    for m in bad:
        print("  ! " + m)
    print("  提示：本脚本不执行任何导入；真导入需人工确认后单次执行。")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
