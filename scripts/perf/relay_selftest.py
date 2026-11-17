"""§15.211~§15.213 改动的守门自检(零网络 / 零 DB / 零 RU)

跑法:
    python3 scripts/perf/relay_selftest.py          # 全部断言
    python3 scripts/perf/relay_selftest.py -v       # 打印每条的证据片段

为什么要有: 这四件改动(评估接力 / 导出全量 / 空转成功 / 仓型映射漂移)**都是"看起来正常"
的形态** —— 同步跑完也能返回 ok, 删 0 行也回 ok, bc 恒 0 也是个数。单元测试断言不了
"线上不再超时", 但能断言"旧路径真的删了、新路径真的接上了"。这是本脚本的唯一职责;
线上是否真的不再 timeout, 只能靠 `docs/PERF.md` §19 的线上实测。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API = os.path.join(ROOT, "cloud-functions", "api")
FE = os.path.join(ROOT, "frontend", "src")

_ROWS = []


def check(name, ok, detail=""):
    _ROWS.append({"name": name, "ok": bool(ok), "detail": detail})
    return ok


def strip_comments(src):
    """去掉整行注释与行尾注释 —— 断言"某写法已消失"时必须这么做, 否则解释性注释会自我否定"""
    out = []
    for ln in src.split("\n"):
        # 只处理简单情形: 引号内的 # 不切(本仓库断言用到的片段都不含)
        i = ln.find("#")
        out.append(ln if i < 0 else ln[:i])
    return "\n".join(out)


def read(*rel):
    with open(os.path.join(*rel), encoding="utf-8") as f:
        return f.read()


def fn_src(path, name):
    """取函数源码(含装饰器上方的 docstring 不算) —— 用于"这个函数里还能不能看到 X"这类断言"""
    src = read(path)
    tree = ast.parse(src)
    lines = src.split("\n")
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return "\n".join(lines[n.lineno - 1:n.end_lineno])
    return ""


def load_pg_of():
    """把 `_pg_of` 从生产文件里摘出来在隔离命名空间执行 —— 测的是真代码, 不是复制品"""
    src = read(API, "routes", "task_export.py")
    tree = ast.parse(src)
    lines = src.split("\n")
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_pg_of":
            seg = "\n".join(lines[n.lineno - 1:n.end_lineno])
            ns = {}
            exec(compile(seg, "<_pg_of>", "exec"), ns)     # noqa: S102 —— 只执行本仓库源码
            return ns["_pg_of"]
    return None


def main(verbose=False):
    cron = os.path.join(API, "routes", "cron.py")
    export = os.path.join(API, "routes", "task_export.py")
    relay = os.path.join(API, "core", "rule_eval_task.py")
    dash = os.path.join(API, "routes", "dashboard.py")
    alerts = os.path.join(API, "routes", "alerts.py")
    store = os.path.join(FE, "store", "useAppStore.ts")

    # ── 1. 评估接力 ────────────────────────────────────────────────
    rdr = fn_src(cron, "run_daily_rules")
    check("run_daily_rules 签名未被改(门禁 local_test 断言)",
          "def run_daily_rules(channels=None, deep=True, alert_types=None" in read(cron))
    check("run_daily_rules 体内不再同步跑全量评估(旧路径已删, 红线2)",
          rdr and "evaluate_stock_skus(" not in rdr,
          "残留: " + ("evaluate_stock_skus( 仍在" if "evaluate_stock_skus(" in rdr else "-"))
    check("run_daily_rules 已接入接力模块",
          "rule_eval_task" in rdr and "enqueue(" in rdr and "step(" in rdr)

    rsrc = read(relay)
    check("接力模块四阶段齐备",
          all(k in rsrc for k in ("STAGE_PREP", "STAGE_EVAL", "STAGE_POST", "STAGE_KICK")))
    check("分片走增量入口(only_ats 一并透传, 只关本片 SKU)",
          "evaluate_stock_skus(channel, skus=skus, only_ats=(ats or None))" in rsrc)
    check("每片落游标并刷新 updated_at(红线15 新鲜度)",
          "UPDATE sync_tasks SET params=%s, updated_at=NOW()" in rsrc)
    check("推进时续 claim 占位(否则 20 分钟后被兜底抢走重跑)",
          "def touch_claim(" in rsrc and "SET created_at=NOW()" in rsrc)
    check("完成才落指纹, 失败回滚成待跑",
          "_settle(p, ok=True)" in rsrc and "_settle(params, ok=False)" in rsrc)
    check("评估算法未被复制(仍调 core.rule_eval)",
          "from core.rule_eval import evaluate_stock_skus" in rsrc and "def judge(" not in rsrc)

    crsrc = read(cron)
    check("cron 端点不再用 @_pipe('daily_rules')(几秒后写 done 会误导)",
          '@_pipe("daily_rules"' not in crsrc)
    cr_daily = strip_comments(fn_src(cron, "cron_daily_rules"))
    check("cron 端点不再提前 settle(请求返回 ≠ 评估完成)",
          cr_daily and "settle_rules(" not in cr_daily)
    check("continue_tasks 能推进 rule_eval 任务",
          'tt == "rule_eval"' in fn_src(cron, "continue_tasks"))

    dsrc = fn_src(dash, "_daily_rules_guard")
    check("看板兜底只推首片, 不再在后台线程跑满 100s",
          dsrc and "settle_rules(_token, _fp, ok=True)" not in dsrc and "run_daily_rules()" in dsrc)

    # ── 2. 导出全量 ────────────────────────────────────────────────
    bsrc = fn_src(export, "_build_export_rows")
    check("_build_export_rows 返回 {rows,total}(可带出 total 判断是否取全)",
          bsrc and 'return {"rows": out, "total"' in bsrc and "return out\n" not in bsrc)
    check("补货导出走分页(不再被 UNPAGED_CAP 2000 截断)",
          "get_replenishment_suggestions(days=28, mode=mode, channel=channel,\n"
          "                                          page=page, page_size=page_size)" in bsrc)
    ces = strip_comments(fn_src(export, "create_export"))
    check("建任务不再真跑一遍导出判类型",
          ces and "_build_export_rows(" not in ces and "exp_type not in _EXPORT_NAMES" in ces)
    esrc = read(export)
    check("导出按片续跑且片内 CSV(多片可拼; xlsx 是 zip 拼不得)",
          "_EXPORT_PAGE" in esrc and 'page=page + 1, page_size=_EXPORT_PAGE' in esrc)
    check("分片 BOM 只在第一片, 后续片剥掉(修合并文件中段 \\ufeff)",
          '_b[3:] if _b.startswith(_bom) else _b' in esrc)

    _pg = load_pg_of()
    if _pg:
        a = _pg({"data": {"items": [1, 2], "total": 5}}, "items")
        b = _pg({"data": [1, 2, 3]}, "items")
        c = _pg({"data": {"suggestions": [7], "total": 9}}, "suggestions")
        d = _pg({"data": {"items": [], "total": 0}}, "items")
        check("_pg_of: 分页形状取 total", a == ([1, 2], 5), repr(a))
        check("_pg_of: 裸列表 total=len(不假装知道总数)", b == ([1, 2, 3], 3), repr(b))
        check("_pg_of: 采购 suggestions 键", c == ([7], 9), repr(c))
        check("_pg_of: 空页不抛", d == ([], 0), repr(d))
    else:
        check("_pg_of 可从生产文件摘出(可测)", False)

    # ── 3. 空转成功(7 端点) ────────────────────────────────────────
    for f, fns in (("routes/orders.py", ("soft_delete_order", "restore_order", "permanent_delete_order")),
                   ("routes/rules.py", ("delete_rule", "restore_rule", "permanent_delete_rule")),
                   ("routes/suppliers.py", ("delete_supplier",)),
                   ("routes/cleansing.py", ("cleansing_templates_delete",))):
        for fn in fns:
            s = fn_src(os.path.join(API, f), fn)
            check("%s 按 rowcount 反馈真实结果" % fn,
                  s and "if not _n:" in s and "fail(" in s)

    # 只看这三个端点体: 文件别处 `(deleted_at IS NULL OR deleted_at='')` 是列表查询的安全写法
    _rs_fns = "".join(strip_comments(fn_src(os.path.join(API, "routes", "rules.py"), f))
                      for f in ("delete_rule", "restore_rule", "permanent_delete_rule"))
    check("rules 三个写端点的 deleted_at 判定用 IFNULL 空串(线上该列恒为 '', 不是 NULL)",
          "IFNULL(deleted_at,'')=''" in _rs_fns and "deleted_at IS NULL" not in _rs_fns)

    # ── 4. 仓型映射漂移 ────────────────────────────────────────────
    check("wh_map 已收敛为唯一实现(routes.alerts)",
          "def wh_map(m)" in read(alerts))
    check("dashboard 不再自写第二份映射(旧版 bc = b + c, bc 恒 0)",
          '"bc": b + c' not in read(dash) and "wh_map as _wm" in read(dash))

    # ── 5. agg-sweep 护栏 ─────────────────────────────────────────
    check("agg_sweep 进了窗口表且端点真的用了它",
          '"agg_sweep":' in crsrc and '_off_window("agg_sweep"' in crsrc)
    check("agg_sweep 有限频(重复调用不重复扫)",
          '_throttle("agg_sweep"' in crsrc)

    # ── 5b. 评估状态行(横幅)不许留僵尸 ────────────────────────────
    #  §15.216: enqueue 建的行必须直接是 running+started_at, 不能靠表默认值落到
    #  pending/NULL —— 那样 close_stale_runs 收不到, eval_live_cond 恒真, 横幅永久挂。
    rtsrc = strip_comments(read(relay))
    check("enqueue 建行直接写 status='running' + started_at(不留 pending 僵尸)",
          "INSERT INTO eval_tasks(scope, status, started_at)" in rtsrc
          and "'running',NOW())" in rtsrc)
    check("超时路径有 abandon() 收尾 eval_tasks + 占位",
          "def abandon(" in rtsrc and "_close_eval(p, ok=False, err=reason)" in rtsrc
          and "_settle(p, ok=False)" in rtsrc)
    check("continue_tasks 判超时时对 rule_eval 调 abandon(不只是一条 UPDATE)",
          '_rt0.abandon(tid)' in crsrc)
    mtsrc = strip_comments(read(os.path.join(API, "core", "maintenance.py")))
    check("close_stale_runs 连 pending 一起收(§15.174 只堵了 running)",
          "status='pending' AND created_at < DATE_SUB" in mtsrc)

    # ── 6. 未登录首屏 ─────────────────────────────────────────────
    st = read(store)
    check("loadAll 未登录直接返回(不再 5 个 401)",
          "if (!safeGet('c_token'))" in st)

    # ── 输出 ──────────────────────────────────────────────────────
    bad = [r for r in _ROWS if not r["ok"]]
    for r in _ROWS:
        mark = "✓" if r["ok"] else "✗"
        print("  %s %s%s" % (mark, r["name"], ("  <- " + r["detail"]) if (verbose and r["detail"]) else ""))
    print("\n%d/%d 通过" % (len(_ROWS) - len(bad), len(_ROWS)))
    if bad:
        print("失败项:")
        for r in bad:
            print("  - %s  %s" % (r["name"], r["detail"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main("-v" in sys.argv))
