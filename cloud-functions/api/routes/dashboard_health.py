"""dashboard 健康指数模块(2026-10-11 拆分第三步——dashboard.py 减负)
函数: _sync_health_snapshot(健康快照写) / _health_index(动态安全线四档判定)
依赖: db/biz.sales/query——无 dashboard 内部依赖(独立)
"""
from datetime import datetime, timedelta, timezone

from db import query, one, execute
from routes.common import try_err, INVENTORY_COLS as _INV_COLS
from routes.dashboard_stock_risk import _rule_params  # health 判定用规则参数(2026-10-11 移动时补依赖)
from core.clock import now_cn


def _sync_health_snapshot(channel, health):
    """健康分快照幂等写入(每次 summary 请求, ON DUPLICATE 键 date+channel, 多实例安全)——
    健康趋势数据源: 不依赖 schedules, 看板每次访问刷新当天分

    ⚠️ 2026-11-06 起读路径**不再直接调用本函数**, 改用 `_sync_health_snapshot_async`
    (它把这个写入丢到后台线程)。原因: 这是写库动作, 每次缓存 miss 都要付一次往返,
    却被算进"用户打开看板"的耗时里(见 core/maintenance.py 的隔离原则)。
    本函数保持原样不动 —— 它的幂等性(ON DUPLICATE KEY)与失败吞掉语义都是对的,
    只是**不该在响应路径上等它**。
    """
    try:
        if not isinstance(health, dict):
            try:
                import json as _j2
                health = _j2.loads(health or "{}")
            except Exception:
                return
        _sc = health.get("score")
        if _sc is None and not health.get("own"):
            return
        execute("INSERT INTO health_snapshot(`date`, channel, score, own_score, platform_score, bc_score) "
                "VALUES(%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE score=VALUES(score), own_score=VALUES(own_score), "
                "platform_score=VALUES(platform_score), bc_score=VALUES(bc_score)",
                [now_cn().strftime("%Y-%m-%d"), channel,
                 int(_sc) if _sc is not None else -1,
                 int((health.get("own") or {}).get("score") or -1),
                 int((health.get("platform") or {}).get("score") or -1),
                 int((health.get("bc") or {}).get("score") or -1)])
    except Exception as _e:
            try_err('dashboard', '静默降级', _e)


def _sync_health_snapshot_async(channel, health):
    """健康快照写入 —— **同步**, 名字保留只为不打断调用点(2026-11-06 纠错)

    ## 为什么从"异步"改回"同步"

    我一开始把它丢进 daemon 线程, 理由是"日粒度趋势, 不值得阻塞交互"。这个理由**站不住**:

    1. **四维里的"可靠性"直接受损**。daemon 线程的存活不归任何人管 —— Makers 实例在响应
       结束后随时可能冻结, 线程就没了。而 `_sync_health_snapshot` 是
       `INSERT ... ON DUPLICATE KEY UPDATE`, **写失败没有任何重试与补偿**: 当天那一行
       就停在旧分数上(或干脆没有)。同步调用则不同 —— 它要么写成功, 要么抛错进
       `try_err` 留下证据, 而这正是"可靠"该有的样子。
    2. **它并不慢**。就是一条按主键 upsert 的单行写(TiDB 上 ~10-30ms), 相对 1.4s 的业务
       计算可以忽略。我把它和 `_rebuild_day_agg(90)`(那是真重活)混为一谈了 ——
       **重活该挪, 轻写不该挪**: 挪了省不下时间, 却把可靠性换掉了。这笔交易是亏的。
    3. **"下次访问会补上"不成立**。当天最后一行的语义是"今天此刻的健康分"。用户关掉页面、
       当天不再来, "下次"就是明天 —— 今天的分永远缺。趋势图会**静默缺一个点**,
       而且缺得没有痕迹(读侧把 `score='-1'` 直接跳过, 连断点都不显示)。

    ## 现在的口径(四维逐条)

    | 维度 | 保障方式 |
    |---|---|
    | 实时性 | 缓存 miss 即写; `invalidate("inventory")` 后下一次 summary 必 miss → 必刷新当天行 |
    | 准确性 | 写的是**本次响应里同一个 `health_index` 对象**(同源同快照), 不二次计算、不跨时刻 |
    | 完整性 | 每次 miss 都 upsert, 不依赖"每天一次"; cron 那条是补的兜底, 不是唯一来源 |
    | 可靠性 | 同步执行 + 失败落 `quality_logs`; 幂等 upsert 保证重放安全, 不会写脏 |

    性能上真正该挪走的是 `_track_data` / `_daily_maintenance` / `_daily_rules_guard`
    (见 `core/maintenance.py`)—— 那些是**重活且幂等、晚几分钟无害**。一条单行 upsert
    不属于这一类, 不能顺手一起异化。
    """
    _sync_health_snapshot(channel, health)


def _health_index(channel, mode=None, _inv_rows=None, _ds_map=None, _cfg=None, _series=None,
                  _tally=None):
    """健康指数四档 —— 判定全部走 `core.metrics`(口径单一真源)

    ## 2026-11-06 S3 改了什么(以及为什么)

    原实现逐行做 `ds_map.get(sku, 0)`: **日销只有 SKU 一个数**(`__C__` 维的融合值),
    却被拿去判每一个仓、每一个 69码 的可用量。后果有三个, 都不是"显示不准"级别:

    1. **逐仓分虚低**。一个 SKU 在 7 个仓各有一份库存, 每个仓都去比"整盘需求 × 周期"
       → 只有一个仓能过线。而 bc 卡把这些仓加起来比同一条线 → 同一批货两个结论。
    2. **单元不一致**。own/platform 的 `total` 是**库存行数**, bc 的 `total` 是**SKU 数**,
       而卡片上两个都写作 "SKU" —— 两个数字放在一起比较本身没有意义(P0-14(2))。
    3. **周期用错**。签名默认 `mode="bbcc"`, cron 对每个渠道都 `_health_index(ch)`,
       other 渠道于是按 jd 的 12 天周期算安全线。

    现在: 桶由 `metrics.units()` 归并(BC 只含 platform+platform_b, 不再混入 own),
    日销由 `metrics.judge()` 按桶的维度取, 周期由 `metrics.period_of()` 按渠道模式定。
    四个 tab 的 `total` 都是 **SKU 粒度**, 可横向比较。

    参数 `_ds_map` 保留但不再使用(旧调用方还在传) —— 它只有 SKU 级 `__C__` 一个数,
    正是上面那个 bug 的来源。
    """
    from core import metrics as _M
    _mode = _M.mode_of(channel, mode)
    rows = _inv_rows if _inv_rows is not None else query(
        "SELECT " + _INV_COLS + " FROM inventory "
        "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!=''", [channel])
    if _cfg is not None:
        _cfg = dict(_cfg) if not isinstance(_cfg, dict) else _cfg
    else:
        _cfg = {}
        try:
            _cfg = {r.get("key"): r.get("value") for r in
                    query("SELECT `key`, value FROM replenishment_config WHERE channel=%s OR channel=''", [channel])}
        except Exception as _e:
                try_err('dash', '健康指数配置加载降级', _e)
    _p = _M.periods(_cfg)
    if _series is None:
        _series = _M.load(channel, dims=_M.dims_needed(rows, channel, _mode), fields=("ds28",))
    # 判定单元: 保留逐仓行(own/platform/platform_b 三个 tab 各看各的), **另外**追加 BC 单元
    #   传统模式下 B 仓不参与(它只是 jd 的中转仓) —— 与告警规则的 `_close_wt` 白名单同口径
    #
    # 2026-11-06 第二刀: 调用方(看板摘要)已经把 units+判定 跑过一遍时, 直接接 `_tally`,
    #   不再重算 —— 否则同一批 8 万个单元会被建两遍、判两遍(组装 3.14s 的主因)。
    #   `_tally` 与 `judge_units` 共用 `metrics.classify()`, 计数口径逐单元一致。
    if _tally is None:
        _us = _M.units(rows, merge_bc=False, with_bc=True,
                       grain=_M.grain_of(channel, _mode, _cfg),
                       drop_platform_b=(_mode != "bbcc"))
        _tally = _M.tally(_us, _series, channel, _mode, metric="ds28",
                          include_transit=_M.include_transit_default(), p=_p)
    _c_own = _M.roll(_tally, kinds=("own",))
    _c_plat = _M.roll(_tally, wt_in=("platform",))
    _c_pb = _M.roll(_tally, wt_in=("platform_b",))
    _c_bc = _M.roll(_tally, kinds=("bc",))
    # 总分不含 bc: bc 是 platform+platform_b 的合并, 与它们同时计入就是把同一批货数两遍
    _c_all = _M.roll(_tally, kinds=("own", "wh"))

    # 健康分档参数: 内置'库存健康监控'规则 params 配置(默认 85/60 与硬编码一致)
    _hrp = _rule_params("health", channel)
    try:
        _hg = float(_hrp.get("health_good", 85))
        _hw = float(_hrp.get("health_warning", 60))
    except Exception:
        _hg, _hw = 85.0, 60.0

    _sc_all = _M.score(_c_all, _hg, _hw)
    return {"own": _M.score(_c_own, _hg, _hw), "platform": _M.score(_c_plat, _hg, _hw),
            "platform_b": _M.score(_c_pb, _hg, _hw), "bc": _M.score(_c_bc, _hg, _hw),
            "score": _sc_all["score"], "level": _sc_all["level"]}
