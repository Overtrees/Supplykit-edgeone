# -*- coding: utf-8 -*-
"""页面 → 模块 → 接口 → 数据来源 → 归属机制：性能基准的**唯一代码真源**。

`docs/PERF.md` 里的页面表由本文件生成（`run.py --list-md`），**不许手改文档表格**：
新增/改接口只改这里，文档与压测集合同时生效，避免"文档写着 12 个接口、脚本打 8 个"。

字段说明
--------
path    线上真实路径（含默认参数；channel=jd 是主渠道，other 用于隔离性抽查）
page    所属页面（PAGE_ORDER 的 key）
module  页面内模块（卡片/表格/抽屉），文档按它分小节
src     数据来源：物化 kind / 表 / 现算 —— 用于判断"慢在哪一层"
mech    归属机制（M1~M8），用于反查"这条链路靠什么站稳"
expect  完整性口径（空结构/零数据判定），见 `complete()`
core    是否属于 12 个核心压测接口（与 2026-11-07~08 的历史数据可比）
"""

MECH = {
    "M1": "物化 + 域指纹：算一次落快照(list_snapshot / own_health_snapshot / unit_state / dmat)，读侧按 fp 命中",
    "M2": "SWR：过期但有旧值 → 立即返上一版 + 后台重建 + 顶层 __swr（读侧零等待）",
    "M3": "单飞：同 key/cache-key 并发 miss 只允许一个 builder（进程内 token）",
    "M4": "重建名额两级：实例内 token 槽 + 跨实例 heavy_slot 全局槽，**先抢全局再抢实例内**",
    "M5": "维护任务 DB 租约锁：maint_lock（600s），跨实例单飞；锁不可用即放行",
    "M6": "取数下推聚合：GROUP BY + SUM/MAX，把 8~9 万行合成 2~3 万行再搬",
    "M7": "快照批量并行写：seq 段互斥、≤3 路并行，write 与行数线性",
    "M8": "传输收敛：显式 X-Accept-Encoding 触发 gzip + 列裁剪(slim) + 真分页",
}

ENDPOINTS = {
    # ── 看板 ────────────────────────────────────────────────
    "boot": dict(path="/api/dashboard/boot?channel=jd", page="dash", module="首屏聚合",
                 src="dmat:dash_summary+dash_aux / unit_state / alerts 计数",
                 mech=["M1", "M2", "M4"], expect="三块非空", core=True),
    "summary": dict(path="/api/dashboard/summary?channel=jd&part=main", page="dash", module="GMV/转化卡",
                    src="dmat:dash_summary", mech=["M1", "M2"], expect="有 data", core=True),
    "aux": dict(path="/api/dashboard/aux?channel=jd", page="dash", module="健康/低库存/采购补货卡",
                src="dmat:dash_aux + list 快照", mech=["M1", "M2"], expect="有 data", core=True),
    "stock_risk": dict(path="/api/dashboard/stock-risk?channel=jd", page="dash", module="断货三级",
                       src="unit_state(head.risk_stat) + 明细分页", mech=["M1", "M2"],
                       expect="含 total", core=True),
    "own_health": dict(path="/api/dashboard/own-health?channel=jd", page="dash", module="自有仓健康",
                       src="own_health_snapshot", mech=["M1", "M2", "M8"], expect="有 data", core=True),
    "unit_state": dict(path="/api/dashboard/unit-state?channel=jd", page="dash", module="判定模型(诊断口)",
                       src="unit_state + unit_state_head", mech=["M1"], expect="有 data", core=True),
    "alerts": dict(path="/api/alerts?channel=jd&page=1&page_size=50", page="dash", module="告警列表",
                   src="alerts 表(分组配额 + 真分页)", mech=["M8"], expect="有 data", core=True),
    "out_of_stock": dict(path="/api/inventory/out-of-stock?channel=jd&wh=own", page="dash", module="缺货清单",
                         src="inventory 现算(paged)", mech=["M8"], expect="有 data", core=True),

    # ── 补货建议 ────────────────────────────────────────────
    "repl": dict(path="/api/insights/replenishment?channel=jd&page=1&page_size=300",
                 page="insights-repl", module="建议表",
                 src="list_snapshot kind=repl(fp 命中) / 冷算 calc→write",
                 mech=["M1", "M2", "M3", "M4", "M6", "M7"], expect="total>0 且 items 非空", core=True),

    # ── 采购建议 ────────────────────────────────────────────
    "purchase": dict(path="/api/insights/purchase?channel=jd&page=1&page_size=300",
                     page="insights-purchase", module="建议表",
                     src="list_snapshot kind=purchase",
                     mech=["M1", "M2", "M3", "M4", "M6", "M7"], expect="有 items", core=True),

    # ── 滞销/处置 ───────────────────────────────────────────
    "disposal": dict(path="/api/insights/disposal-suggestions?channel=jd&page=1&page_size=300",
                     page="insights-slow", module="处置建议表",
                     src="list_snapshot kind=disposal(逐行出建议, 不聚合)",
                     mech=["M1", "M2", "M4", "M7"], expect="有 items", core=True),
    "slow_moving": dict(path="/api/insights/slow-moving?channel=jd&page=1&page_size=100",
                        page="insights-slow", module="滞销榜",
                        src="现算(orders 聚合)", mech=["M8"], expect="有 data"),

    # ── 历史归档 ────────────────────────────────────────────
    "hist_coverage": dict(path="/api/history/coverage?channel=jd", page="history", module="归档状态",
                          src="orders + daily_stats(归档)", mech=["M1"], expect="有 data"),
    "hist_summary": dict(path="/api/history/summary?channel=jd&start=2026-08-01&end=2026-11-02",
                         page="history", module="汇总卡",
                         src="analysis_cache(300s) + 双表拼接", mech=["M2"], expect="有 data"),
    "hist_top": dict(path="/api/history/top?channel=jd&start=2026-08-01&end=2026-11-02",
                     page="history", module="Top20", src="analysis_cache(300s)",
                     mech=["M2"], expect="有 data"),
    "hist_yoy": dict(path="/api/history/yoy?channel=jd&start=2026-08-01&end=2026-11-02",
                     page="history", module="同比", src="analysis_cache(120s) + 双表拼接",
                     mech=["M2"], expect="有 data"),

    # ── 进销存台账 ──────────────────────────────────────────
    "ledger_preview": dict(path="/api/ledger/preview?channel=jd", page="inv", module="台账三视图",
                           src="ledger + inventory 现算", mech=["M8"], expect="有 data"),
    "batches": dict(path="/api/batches?channel=jd", page="inv", module="批次效期",
                    src="batches 表", mech=["M8"], expect="有 data"),

    # ── 订单明细 ────────────────────────────────────────────
    "orders": dict(path="/api/orders?channel=jd&page=1&page_size=50", page="orders", module="订单表",
                   src="orders 表(真分页, TTL 5s)", mech=["M8"], expect="有 items"),

    # ── 任务页 ──────────────────────────────────────────────
    "tasks": dict(path="/api/tasks?channel=jd&limit=8", page="tasks", module="任务列表",
                  src="sync_tasks 单条 SELECT", mech=[], expect="有 data", core=True),

    # ── 商品/供应商/规则/质量/设置（轻接口） ──────────────────
    "products": dict(path="/api/products?channel=jd&page=1&page_size=50", page="products", module="商品表",
                     src="products 表", mech=["M8"], expect="有 data"),
    "suppliers": dict(path="/api/suppliers?channel=jd&page=1&page_size=50", page="suppliers", module="供应商表",
                      src="suppliers 表", mech=["M8"], expect="有 data"),
    "rules": dict(path="/api/rules?channel=jd", page="rules", module="规则列表",
                  src="rules 表", mech=[], expect="有 data"),
    "quality_logs": dict(path="/api/quality-logs?channel=jd&page=1&page_size=50", page="quality",
                         module="异常记录", src="quality_logs(真分页)", mech=["M8"], expect="有 data"),
    "channels": dict(path="/api/channels", page="settings", module="渠道管理",
                     src="channels 表", mech=[], expect="有 data"),
}

# 12 个核心压测接口（与 2026-11-07~08 的历史数据同口径，**不要随意增删**）
CORE = [k for k, v in ENDPOINTS.items() if v.get("core")]

PAGE_ORDER = [
    ("dash", "看板", "首屏 4 重接口并发 + 30s 刷新；卡面数据全部来自物化"),
    ("insights-repl", "补货建议", "list_snapshot kind=repl；BBCC 两步法，行数=建议条数"),
    ("insights-purchase", "采购建议", "list_snapshot kind=purchase；14+28 融合日销"),
    ("insights-slow", "滞销/处置", "kind=disposal；一库存行一条建议，不聚合（红线 11）"),
    ("history", "历史归档", "orders + daily_stats 双表拼接；analysis_cache + SWR"),
    ("inv", "进销存台账", "自有/平台/B 仓三视图，模式跟随"),
    ("orders", "订单明细", "真分页 + 软删撤销；TTL 5s 不接 SWR"),
    ("tasks", "任务页", "sync_tasks 单条 SELECT；维护任务走 maint_lock 单飞"),
    ("products", "商品", "CRUD；批次效期"),
    ("suppliers", "供应商", "CRUD；采购参数"),
    ("rules", "规则引擎", "规则=计算引擎配置；评估任务异步"),
    ("quality", "异常记录", "quality_logs 分页加载更多"),
    ("settings", "设置", "渠道/回收站/系统信息"),
]


def _rows(d):
    if not isinstance(d, dict):
        return -1
    for k in ("items", "suggestions", "rows", "list", "records"):
        v = d.get(k)
        if isinstance(v, list):
            return len(v)
    return -1


# §15.189 P5: 已知「空是合法业务态」的接口 —— §9.7 的 22 条 incomplete 误报
#   全部来自 alerts 恒空(safety_qty=0 / 无触发规则时 alerts 表本来就是 0 行)。
#   红线 8① 要求「没算成」与「真的是 0」可分: 这里的 0 是**真的 0**, 应显式列
#   白名单, 不混入 incomplete; 但 HTTP 非 200 / 响应非 dict|list 仍判失败。
#   白名单是判据自身的事实声明, 不放宽任何其它接口的完整性口径。
KNOWN_EMPTY = frozenset({"alerts"})


def complete(key, data):
    """完整性口径（数据四维之"完整"）：空结构 / 零数据 / 卡片清零。

    只对**已知形状**的接口做严格判定，其余判"有没有 data"，避免把
    "这个接口本来就可能为空"误判成失败（红线 8 ①：没算成与真的是 0 必须可分）。
    """
    if not isinstance(data, (dict, list)):
        return False                     # 形状都不对 = 真失败, 白名单不豁免
    if not data:
        return key in KNOWN_EMPTY        # 空结构: 仅已知空接口合法(§15.189 P5)
    if key == "boot":
        return (bool(data.get("summary")) and isinstance(data.get("aux"), dict) and bool(data.get("aux"))
                and isinstance(data.get("stock_risk"), dict) and "total" in data.get("stock_risk", {}))
    if key == "repl":
        return int(data.get("total") or 0) > 0 and bool(data.get("items"))
    if key == "stock_risk":
        return "total" in data
    return True


def markdown_table():
    """生成 docs/PERF.md 的页面总表（run.py --list-md 输出）。"""
    out = []
    for pid, pname, pdesc in PAGE_ORDER:
        mods = [(k, v) for k, v in ENDPOINTS.items() if v["page"] == pid]
        out.append("### %s\n\n%s\n" % (pname, pdesc))
        out.append("| 模块 | 接口 | 数据来源 | 机制 | 核心 |")
        out.append("|---|---|---|---|---|")
        for k, v in mods:
            out.append("| %s | `%s` | %s | %s | %s |"
                       % (v["module"], v["path"].split("?")[0],
                          v["src"], "/".join(v["mech"]) or "—", "✓" if v.get("core") else ""))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(markdown_table())
