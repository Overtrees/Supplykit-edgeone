"""中央缓存失效注册表 → 共享表缓存(2026-09-09 治本重构)

背景: Makers cloud-functions 为请求模式(无状态/每请求环境/多实例), 模块级内存缓存
      (dict) 命中率≈0(每次请求新环境), 且进程内失效只清当前实例 → 跨实例旧缓存
      不一致(参数保存后其他实例仍返回旧值)+ 接口每次全量重算(慢→前端超时兜底)。

方案: 聚合缓存存 TiDB 表 analysis_cache(跨实例共享, 行级一致性):
  - cache_get(key, ttl, builder): SELECT 命中且未过期(DB 侧 TIMESTAMPDIFF, 免时区坑)
    → 返回; 否则 builder() 重算 → REPLACE 落表 → 返回
  - invalidate(域): 按数据域打墓碑 → 所有实例立即失效(全局一致, 不再依赖实例内存)
  - 单 key 并发重建: 多实例同时 miss → 各自 REPLACE, TiDB 行锁幂等, 最终一致

2026-11-02 S2-1 域级失效(本节重构的关键语义):
  改一个供应商也曾把看板/规则/采购的缓存全打掉 —— 一次无关写入, 全站冷算。
  现改为「缓存族声明依赖哪些数据域」, 写入方只声明自己动了哪个域:
    invalidate("suppliers") → 只清 dash_summary / dash_aux / stock_risk
  未登记的缓存族(新加的键忘了登记)一律清 —— 宁多清一次, 不漏清一次。
"""
from routes.common import try_err
import json

_registry = {}

# 2026-10-17 物化追平限频(写放大控制——35 个 invalidate 调用点曾全量重建 RU 爆):
# 非阻塞锁防并发; 窗口只用于**合并同一次导入的多次调用**
# 2026-11-02 S2-2: 300 秒 → 3 秒。原来 5 分钟的语义是"这次不重建", 而补录历史日期后
# 若没有后续写操作, 那 5 分钟窗口过去也不会有人再触发 → agg 永久落后。现在队列里的
# 标记不会因为窗口被丢弃, 窗口只用来避免一次导入连起十几个线程。
_MAT_LOCK_OBJ = None
_MAT_LAST = 0.0
_MAT_WINDOW = 3


def register(clear_fn):
    """兼容旧 API: 内存清理函数已无意义(表缓存), 保留空实现防崩溃"""
    _registry[len(_registry)] = clear_fn


_FAILED = object()

# 业务口径/算法版本: 变更时递增(cache busting) —— 表缓存跨部署存活(与内存缓存不同,
# 部署新代码后 TTL 内可能命中旧口径数据), 升版本后旧缓存自然 miss 重建, 保四维准确性
_CACHE_V = "c1"  # 响应结构版本(2026-10-11 约定): 后端响应结构变更(字段/包装)时 bump(如 c1→c2)
# ——旧缓存(旧结构)跨部署存活会致前端解包错/无数据(products 双重包装事故教训); bump 后全部缓存 key 变化=自然失效"""
# 附: 前端 axios 30s 内存缓存不受此影响(部署后浏览器刷新即新)——后端表缓存需 bump


def _loads(value):
    try:
        return json.loads(value)
    except Exception:
        return _FAILED


# ── 2026-11-02 S2-1: 缓存族 → 数据域 依赖矩阵 ───────────────────────────────
# 键的第一段就是"缓存族"(见各 cache_get 调用点); 这里声明每一族**读哪些表**。
# 写入方只声明自己动了哪些域, 由矩阵反推要清哪些族 —— 依赖关系只在这里维护一份。
#
# 域(见 common/各路由写入点):
#   orders     订单 + 日聚合(orders_day_agg) + 日销物化(daily_sales_snapshot/sku_sales_stats) + 历史归档(daily_stats)
#   inventory  库存/批次/入出库流水(含月汇总)
#   products   商品档案(含 69 码、箱规、供应商归属)
#   suppliers  供应商档案/评分(评分进断货系数与规则 ctx, 不改 sales)
#   config     补货采购参数(前置期/安全天数/季节/滞销品类/列映射/订单状态映射)
#   rules      规则表 + alerts 表(两者一一联动, 合成一个域)
#   purchases  采购单 + 处置记录
#   flows      回款流水(gmv_flows, 只进看板 payout)
#
# ⚠️ 加新缓存族必须在这里登记, 否则该族的键会落在"未登记"集合里 —— 任何写入都会清它
#    (保守但正确: 不会被漏清, 只是少一次复用)。local_test 有静态用例盯住这件事。
_FAMILY_DEPS = {
    # 缓存族            依赖的数据域
    "orders":       {"orders"},
    "history":      {"orders"},
    "accel":        {"orders"},
    "sales_digest": {"orders"},
    "dash_summary": {"orders", "inventory", "products", "suppliers", "rules", "config", "flows"},
    "dash_aux":     {"orders", "inventory", "products", "suppliers", "rules", "config"},
    "stock_risk":   {"orders", "inventory", "products", "suppliers", "rules", "config"},
    "purchase":     {"orders", "inventory", "products", "config", "rules", "purchases"},
    "repl":         {"orders", "inventory", "products", "config"},
}

_KNOWN_DOMAINS = set()
for _f, _d in _FAMILY_DEPS.items():
    _KNOWN_DOMAINS |= _d

# 失效"墓碑": 把 created_at 打到 1970(读侧 age 极大 → 必然 miss), 同时改写 tok(写入令牌)。
# 不 DELETE 而是留墓碑, 是为了挡住这个竞态:
#   读线程 A 开始重建(t=0) → 写线程 B 提交并失效(t=1) → A 把 t=0 的旧值落表
# 若 B 是 DELETE, A 的新插入没有冲突行, 旧值直接写进去 → 缓存脏满一个 TTL(采购 300s)。
# 有了 tok: B 把行 tok 打到 t=1, A 落表时 `VALUES(tok)>=tok` 不成立 → 不覆盖 → 下次读仍是
# 墓碑 → 重新重建。代价是可能多算一次, 换来"永不用旧值覆盖新失效"。
_DEAD_TS = "1970-01-01 00:00:00"


def _now_tok():
    """写入令牌(微秒): 纯应用侧单调时间戳, 不进 DB 时区比较"""
    import time as _t
    return int(_t.time() * 1000000)


def cache_put(key, value, tok=None, raw=False):
    """带令牌守卫的落表: 只有"比当前行更新的令牌"才允许覆盖
    raw=True 时 value 已是字符串(调用方自己序列化)
    """
    from db import execute as _e
    _v = value if (raw and isinstance(value, str)) else json.dumps(value, ensure_ascii=False, default=str)
    _t = int(tok if tok is not None else _now_tok())
    _e("INSERT INTO analysis_cache(`key`, value, created_at, tok) VALUES(%s,%s,NOW(6),%s) "
       "ON DUPLICATE KEY UPDATE "
       "value=IF(VALUES(tok)>=tok, VALUES(value), value), "
       "created_at=IF(VALUES(tok)>=tok, VALUES(created_at), created_at), "
       "tok=GREATEST(tok, VALUES(tok))",
       (key, _v, _t))


_CALC_INFLIGHT = {}


def cache_get(key, ttl, builder, max_bytes=None):
    """共享表缓存读: 命中(TTL 内)返回; 否则 builder() 重算落表
    2026-10-25 去悲观锁(性能根治): 原 SELECT ... FOR UPDATE 持锁重算(10-11 并发根治设计),
    但 builder 重算 2-3s >> TiDB lock_wait_timeout(默认 1s)→ 并发请求 9004 锁超时 → 降级直算
    → 缓存从不命中, 每次请求全量重算(summary 85-174s/补货采购 2-3s 实测)——去锁:
    无锁快读命中返回(miss 无锁重算 + REPLACE 幂等, 并发重复算可接受最终一致)
    2026-11-02: 落表改 cache_put(令牌守卫) —— 重建期间被失效 → 旧值不许覆盖墓碑;
    max_bytes: 超过阈值不落缓存(防 TiDB 单行上限, stock_risk 曾 11.6MB 教训), 只本次返回
    """
    try:
        from db import one
        row = one("SELECT value, TIMESTAMPDIFF(SECOND, created_at, NOW(6)) AS age "
                  "FROM analysis_cache WHERE `key`=%s", [key])
        if row:
            age = row.get("age")
            v0 = row.get("value")
            if v0 != "__CALC__" and age is not None and int(age) < ttl:
                v = _loads(v0 or "")
                if v is not _FAILED:
                    return v
        _tok = _now_tok()   # 令牌必须在 builder() **之前**取: 之后取会把重建期间的新写入盖掉
        val = builder()
        _s = json.dumps(val, ensure_ascii=False, default=str)
        if max_bytes and len(_s.encode("utf-8")) > max_bytes:
            return val
        cache_put(key, _s, tok=_tok, raw=True)
        return val
    except Exception as e:
        # 治本自愈(非纯降级): 删损坏 key + 幂等建表 + 记日志 → 下次请求恢复缓存;
        # 自愈本身也失败才降级直算(保底不阻塞业务)
        try:
            from db import execute as _e3
            _e3("DELETE FROM analysis_cache WHERE `key`=%s", [key])
            _e3("CREATE TABLE IF NOT EXISTS analysis_cache ("
                "`key` VARCHAR(160) PRIMARY KEY, value MEDIUMTEXT, "
                "created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), tok BIGINT DEFAULT 0)")
            _e3("INSERT INTO quality_logs(log_type, level, message, source) "
                "VALUES('cache_error','error',%s,'api')",
                ("cache_get 降级直算[%s]: %s" % (str(key)[:80], str(e)[:150]),))
        except Exception as _e:
                try_err('analysis_cache', '静默降级', _e)
        return builder()
    except Exception as e:
        # 治本自愈(非纯降级): 删损坏 key + 幂等建表 + 记日志 → 下次请求恢复缓存;
        # 自愈本身也失败才降级直算(保底不阻塞业务)
        try:
            from db import execute as _e3
            _e3("DELETE FROM analysis_cache WHERE `key`=%s", [key])
            _e3("CREATE TABLE IF NOT EXISTS analysis_cache ("
                "`key` VARCHAR(160) PRIMARY KEY, value MEDIUMTEXT, "
                "created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6))")
            _e3("INSERT INTO quality_logs(log_type, level, message, source) "
                "VALUES('cache_error','error',%s,'api')",
                ("cache_get 降级直算[%s]: %s" % (str(key)[:80], str(e)[:150]),))
        except Exception as _e:
                try_err('analysis_cache', '静默降级', _e)
        return builder()


def _drain_agg_dirty(batch=40, max_rounds=6):
    """消费 agg_dirty_days: 按渠道把脏日期合成 ≤7 天区间, 精准重建「日聚合 + 日销快照 + 日销三窗口」

    为什么按区间而不是整段:
      · 单段 DELETE + INSERT 走 (channel, ordered_at) 索引, 几秒内完成;
        整段重建要扫全窗口(90 天 ≈ 20 万行), 且大事务持锁(实测 9004 锁超时)。
      · 补录通常只涉及几天(某月月初/某个促销周), 合区间天然贴合实际改动量。
    脏日期过多(> batch*2, 例如补录整个季度)时 **不换算法**, 只放大单轮取数上限 ——
      agg 与快照都是"组合粒度"(date×状态×店 / date×sku×码×仓), 只有 DELETE+INSERT 才能
      删掉"软删订单留下的组合", 单条 upsert 删不掉。2026-11-02 线上实测旧全量分支两个硬伤:
        ① `_rebuild_day_agg(90)` 只 upsert → 删 2 单后 agg 合计对了, 但**消失的组合仍残留**;
        ② 只重建 agg + 日销三窗口, **压根没重建快照** → daily_sales_snapshot 留 1 行
           (ZZ-S21-SKU2), 补货/采购继续读旧日销(30/60/90 天窗口全偏)。
      两者都靠区间 DELETE+INSERT 覆盖, 所以"全量"现在只是"一次取完 + 不设批次上限"。

    出队用 ts 比较(见 common.clear_agg_day): 重建期间又有新写入 → 保留, 下轮再来。
    失败不出队, 且本轮立即停止(不原地打转, 留给下一次 invalidate / 每日 sweep)。
    """
    from routes.common import (take_agg_days, clear_agg_day, agg_pending, agg_has_delete,
                               _runs, track_run)
    from routes.dashboard_maintain import (_rebuild_agg_range, _rebuild_snapshot_range,
                                           _mark_agg_dirty, _clear_agg_dirty)
    _pend = agg_pending()
    _full = _pend > batch * 2
    out = {"pending": _pend, "days": 0, "snap": 0, "failed": 0, "stat_purge": 0,
           "mode": "full" if _full else "incremental"}
    if not _pend:
        _clear_agg_dirty()
        out["left"] = 0
        return out
    with track_run("agg_incremental", "all") as rec:
        # 全量: 一次取完(不设批次上限), 单轮最多 24 段(≈60s, 云函数超时内) → 余下下次续跑;
        #   队列未清空时 agg_dirty 保持置位, 看板继续降级直查(慢但准), 不会读到半成品。
        _cap = (_pend + 8) if _full else batch
        _rounds = 1 if _full else max_rounds
        _budget = 24 if _full else 0
        _nr = 0
        _del_cache = {}

        def _ch_deleted(_c):
            """本批该渠道是否有删单类原因(懒查 + 缓存; 只为决定要不要清日销孤儿行)"""
            if _c not in _del_cache:
                _del_cache[_c] = bool(agg_has_delete(_c))
            return _del_cache[_c]

        for _ in range(_rounds):
            items = take_agg_days(_cap)
            if not items:
                break
            # 按渠道分组 → 每组内合成连续区间(补录通常是连续的几天/一周)
            by_ch = {}
            for (ch, day, ts) in items:
                if ch and day:
                    by_ch.setdefault(ch, []).append((day, ts))
            _stop = False
            for ch, pairs in by_ch.items():
                # 删单原因必须在**出队前**问(出队后 reason 随行消失)
                _is_del = _ch_deleted(ch)
                _done_days = []
                for (a, b) in _runs([d for (d, _t) in pairs], 7):
                    if not _rebuild_agg_range(ch, a, b):
                        out["failed"] += 1
                        break
                    _nr += 1
                    try:
                        _rebuild_snapshot_range(ch, a, b)
                        out["snap"] += 1
                    except Exception as _es:
                        # 快照失败不阻塞 agg 出队: agg 已正确, 快照由每日 _snapshot_reconcile 兜底
                        try_err('analysis_cache', '快照区间重建降级(%s %s~%s)' % (ch, a, b), _es)
                    # 区间内逐日出队 —— 各天带自己的 ts, 期间被重新标记的那天会保留
                    for (d, t) in pairs:
                        if a <= d <= b and clear_agg_day(ch, d, t):
                            out["days"] += 1
                            _done_days.append(d)
                    if _budget and _nr >= _budget:
                        _stop = True   # 本轮预算用尽: 余下留队列, 下次续跑(agg_dirty 保持置位)
                        break
                # 2026-11-02 S2-1: 二阶物化(日销三窗口)跟着这批日期重算 —— 它从快照派生,
                #   快照刚变, 不重算就等于"补货/采购读旧日销"。只算这些天出现过的 SKU。
                if _done_days:
                    try:
                        from biz.sales import rebuild_sku_stats_for_days as _rst
                        _n2 = _rst(ch, _done_days)
                        out["stats"] = int(out.get("stats") or 0) + int(_n2 or 0)
                    except Exception as _es2:
                        # 失败不影响 agg 出队(已正确); 下次写操作/每日对账还会再来
                        try_err('analysis_cache', '日销三窗口重建降级(%s)' % ch, _es2)
                    # 2026-11-02 S2-1b: 删单/永久删还要清"日销孤儿行"——某 SKU 的订单被**硬删**
                    #   之后, 它不在 orders 也不在快照里, 上面那个"按受影响 SKU 重建"反查不到它,
                    #   旧日销行会一直留着(补货/断货继续按已删订单算)。软删能自愈, 硬删不能。
                    if _is_del:
                        try:
                            from biz.sales import purge_orphan_sku_stats as _pos
                            out["stat_purge"] += int(_pos(ch, 90) or 0)
                        except Exception as _es4:
                            try_err('analysis_cache', '日销孤儿行清理降级(%s)' % ch, _es4)
                if _stop or out["failed"]:
                    break
            if out["failed"] or _stop or len(items) < _cap:
                break
        _left = agg_pending()
        # 队列非空 = agg 仍可能落后 → 保持降级直查; 清空才恢复物化路径
        if _left:
            _mark_agg_dirty()
        else:
            _clear_agg_dirty()
        rec.rows = out["days"]
        rec.detail = {"mode": out["mode"], "pending": _pend, "left": _left,
                      "snap": out["snap"], "failed": out["failed"],
                      "stats": out.get("stats", 0), "stat_purge": out.get("stat_purge", 0)}
        out["left"] = _left
    return out


def invalidate(*domains):
    """数据变更后按**域**失效: 只清依赖这些域的缓存族(其余族继续复用)

    · invalidate("orders")    导入/删单/补录 → 订单、历史、加速、日销摘要 + 全部派生聚合
    · invalidate("suppliers") 供应商改评分/档案 → 只清 看板summary/aux/断货风险
    · invalidate("all")       迁移/重置/种子(动了很多表) → 全清

    未登记域的调用一律按全清处理 —— 写错域名宁可多清, 不能漏清。
    返回失效的族名列表(便于日志/测试断言)。
    """
    _doms = set()
    for _d in domains:
        if isinstance(_d, (list, tuple, set)):
            _doms |= {str(x) for x in _d if x}
        elif _d:
            _doms.add(str(_d))
    _wipe = (not _doms) or ("all" in _doms) or bool(_doms - _KNOWN_DOMAINS)
    _hit = sorted(_FAMILY_DEPS) if _wipe else sorted(
        f for f, deps in _FAMILY_DEPS.items() if deps & _doms)
    try:
        from db import execute as _e
        # 2026-10-29 数据版本根治: 这里是**所有写操作必经的中央失效点** →
        # 在此递增计数, /health 读它即可感知 UPDATE 类变更(告警关闭/库存重算/参数保存),
        # 补掉 MAX(id) 指纹"只认新增、不认更新"的盲区。零额外查询(写操作内 +1 次 UPDATE)。
        # 2026-11-02: 仍保持"每次写都涨", 因为它记录的是**数据变更次数**, 不是缓存代次。
        try:
            _e("INSERT INTO data_version(id, v) VALUES(1,1) ON DUPLICATE KEY UPDATE v=v+1")
        except Exception as _dv:
            try_err('analysis_cache', '数据版本递增降级', _dv)
        _tok = _now_tok()
        _keep = [f for f in _FAMILY_DEPS if f not in _hit]
        if _keep:
            # 留下的族 = 本次没动的域; 其余(含未登记键)一律打墓碑
            _cond = " OR ".join(["`key` LIKE %s"] * len(_keep))
            _e("UPDATE analysis_cache SET created_at=%s, tok=%s WHERE NOT (" + _cond + ")",
               [_DEAD_TS, _tok] + ["%s|%%" % f for f in _keep])
        else:
            _e("UPDATE analysis_cache SET created_at=%s, tok=%s", [_DEAD_TS, _tok])
        for fn in list(_registry.values()):
            try:
                fn()
            except Exception as _ec:
                    try_err('analysis_cache', '静默降级', _ec)
        # 物化追平(异步线程尽力——写操作不阻塞)
        # 2026-11-02 S2-2: 由「5 分钟限频 + 全量 90 天」改为「按业务日期增量」。
        #   旧做法两个硬伤:
        #     ① 补录历史日期后, 若 5 分钟内没有后续写操作, 重建被窗口挡掉 → agg 永不更新
        #        (读侧只直查「昨天+今天」, 补录 3 天前的数据没人兜底)
        #     ② 只能全量 90 天 → 单次 RU 高, 才不得不限频; 限频又放大 ①
        #   增量后单日重建很便宜(走 idx_orders_channel_ordered), 不需要限频;
        #   只在一次性铺开很多天(补录整个季度)时退化为全量, 省往返。
        # 2026-11-02 S2-1: 只有订单域变了才需要追平 —— 供应商/参数/采购单写入从来没改过
        #   orders, 起线程只是白扫一遍队列(还占连接)。队列有标记就说明订单动过, 所以这里
        #   不再加 `agg_pending()` 判断(标记可能刚写还没读到, 会把重建卡住)。
        if not (_wipe or ("orders" in _doms)):
            return _hit
        try:
            import threading as _thm
            import time as _t2
            global _MAT_LOCK_OBJ, _MAT_LAST
            if _MAT_LOCK_OBJ is None:
                _MAT_LOCK_OBJ = _thm.Lock()
            _now2 = _t2.time()
            # 3 秒合并窗口: 一次导入写完会连调多次 invalidate, 只起一个线程。
            # 注意这不是「这次不重建」—— 队列里的标记不会丢, 下一轮还能捞到。
            if _now2 - _MAT_LAST < _MAT_WINDOW:
                pass
            elif _MAT_LOCK_OBJ.acquire(blocking=False):
                _MAT_LAST = _now2

                def _mat():
                    try:
                        _drain_agg_dirty()
                    except Exception as _e2:
                        try_err('analysis_cache', '增量重建降级', _e2)
                    finally:
                        try:
                            _MAT_LOCK_OBJ.release()
                        except Exception:
                            pass

                _thm.Thread(target=_mat, daemon=True).start()
        except Exception:
            pass
        return _hit
    except Exception as e:
        # 失效失败可见性(四维-实时性): 缓存未清则 TTL 内读旧值, 记 quality_logs 供审计
        try:
            from db import execute as _e2
            _e2("INSERT INTO quality_logs(log_type, level, message, source) "
                "VALUES('cache_invalidate','error',%s,'api')", (str(e)[:200],))
        except Exception as _e:
                try_err('analysis_cache', '静默降级', _e)
        return _hit


def invalidate_all():
    """全量失效(迁移/重置/种子/跨表重建用): 等价于 invalidate("all")

    日常写入**不要**用它 —— 用 invalidate("<域>")。这里保留是为了让"确实动了很多表"的
    少数场景有个显式出口, 而不是让每个调用点都背一次"我知道我只动了供应商"的判断。
    """
    return invalidate("all")