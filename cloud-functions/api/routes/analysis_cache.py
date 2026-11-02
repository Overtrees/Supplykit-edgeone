"""中央缓存失效注册表 → 共享表缓存(2026-09-09 治本重构)

背景: Makers cloud-functions 为请求模式(无状态/每请求环境/多实例), 模块级内存缓存
      (dict) 命中率≈0(每次请求新环境), 且 invalidate_all 只清当前实例 → 跨实例旧缓存
      不一致(参数保存后其他实例仍返回旧值)+ 接口每次全量重算(慢→前端超时兜底)。

方案: 聚合缓存存 TiDB 表 analysis_cache(跨实例共享, 行级一致性):
  - cache_get(key, ttl, builder): SELECT 命中且未过期(DB 侧 TIMESTAMPDIFF, 免时区坑)
    → 返回; 否则 builder() 重算 → REPLACE 落表 → 返回
  - invalidate_all(): DELETE 全表 → 所有实例立即失效(全局一致, 不再依赖实例内存)
  - 单 key 并发重建: 多实例同时 miss → 各自 REPLACE, TiDB 行锁幂等, 最终一致
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


_CALC_INFLIGHT = {}


def cache_get(key, ttl, builder):
    """共享表缓存读: 命中(TTL 内)返回; 否则 builder() 重算落表
    2026-10-25 去悲观锁(性能根治): 原 SELECT ... FOR UPDATE 持锁重算(10-11 并发根治设计),
    但 builder 重算 2-3s >> TiDB lock_wait_timeout(默认 1s)→ 并发请求 9004 锁超时 → 降级直算
    → 缓存从不命中, 每次请求全量重算(summary 85-174s/补货采购 2-3s 实测)——去锁:
    无锁快读命中返回(miss 无锁重算 + REPLACE 幂等, 并发重复算可接受最终一致)"""
    try:
        from db import one, execute
        row = one("SELECT value, TIMESTAMPDIFF(SECOND, created_at, NOW(6)) AS age "
                  "FROM analysis_cache WHERE `key`=%s", [key])
        if row:
            age = row.get("age")
            v0 = row.get("value")
            if v0 != "__CALC__" and age is not None and int(age) < ttl:
                v = _loads(v0 or "")
                if v is not _FAILED:
                    return v
        val = builder()
        execute("REPLACE INTO analysis_cache(`key`, value, created_at) "
                "VALUES(%s,%s,NOW(6))",
                (key, json.dumps(val, ensure_ascii=False, default=str)))
        return val
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
    """消费 agg_dirty_days: 逐日精准重建「日聚合 + 日销快照」, 重建完出队

    为什么逐日而不是整段:
      · 单日 DELETE + INSERT 走 (channel, ordered_at) 索引, 几十毫秒;
        整段重建要扫全窗口(90 天 ≈ 20 万行), 且大事务持锁(实测 9004 锁超时)。
      · 补录通常只涉及几天(某月月初/某个促销周), 逐日天然贴合实际改动量。
    天数过多(> batch*2, 例如补录整个季度)才退化为全量一次 —— 逐日往返反而更慢。

    出队用 ts 比较(见 common.clear_agg_day): 重建期间又有新写入 → 保留, 下轮再来。
    失败不出队, 且本轮立即停止(不原地打转, 留给下一次 invalidate / 每日 sweep)。
    """
    from routes.common import (take_agg_days, clear_agg_day, agg_pending, _runs,
                               AGG_WINDOW_DAYS, track_run)
    from routes.dashboard_maintain import (_rebuild_day_agg, _rebuild_agg_range,
                                           _rebuild_snapshot_range,
                                           _mark_agg_dirty, _clear_agg_dirty)
    _pend = agg_pending()
    out = {"pending": _pend, "days": 0, "snap": 0, "failed": 0, "mode": "incremental"}
    if not _pend:
        _clear_agg_dirty()
        return out
    with track_run("agg_incremental", "all") as rec:
        if _pend > batch * 2:
            out["mode"] = "full"
            _ok = _rebuild_day_agg(AGG_WINDOW_DAYS)
            if _ok:
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                from db import execute as _e
                _cut = (_dt.now(_tz.utc) - _td(days=AGG_WINDOW_DAYS)).strftime("%Y-%m-%d")
                # 只清窗口内的标记: 窗口外的日期 agg 本来就不服务(mark 侧已过滤, 这里是双保险)
                _e("DELETE FROM agg_dirty_days WHERE `date` >= %s", [_cut])
                _clear_agg_dirty()
                out["days"] = _pend
            else:
                _mark_agg_dirty()
                out["failed"] = 1
            rec.rows = _pend if _ok else 0
            rec.detail = {"mode": "full", "ok": bool(_ok), "pending": _pend}
            return out
        for _ in range(max_rounds):
            items = take_agg_days(batch)
            if not items:
                break
            # 按渠道分组 → 每组内合成连续区间(补录通常是连续的几天/一周)
            by_ch = {}
            for (ch, day, ts) in items:
                if ch and day:
                    by_ch.setdefault(ch, []).append((day, ts))
            for ch, pairs in by_ch.items():
                for (a, b) in _runs([d for (d, _t) in pairs], 7):
                    if not _rebuild_agg_range(ch, a, b):
                        out["failed"] += 1
                        break
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
            if out["failed"] or len(items) < batch:
                break
        _left = agg_pending()
        # 队列非空 = agg 仍可能落后 → 保持降级直查; 清空才恢复物化路径
        if _left:
            _mark_agg_dirty()
        else:
            _clear_agg_dirty()
        rec.rows = out["days"]
        rec.detail = {"mode": "incremental", "pending": _pend, "left": _left,
                      "snap": out["snap"], "failed": out["failed"]}
    return out


def invalidate_all():
    """数据变更后全局失效: 删共享表缓存(跨实例一致) + 物化即时追平(写操作后 agg/快照增量——
    导入/删单/规则/参数变更后物化立即跟数据, 非轮询延迟——数据实时性根治)"""
    try:
        from db import execute as _e
        # 2026-10-29 数据版本根治: invalidate_all 是**所有写操作必经的中央失效点** →
        # 在此递增计数, /health 读它即可感知 UPDATE 类变更(告警关闭/库存重算/参数保存),
        # 补掉 MAX(id) 指纹"只认新增、不认更新"的盲区。零额外查询(写操作内 +1 次 UPDATE)。
        try:
            _e("INSERT INTO data_version(id, v) VALUES(1,1) ON DUPLICATE KEY UPDATE v=v+1")
        except Exception as _dv:
            try_err('analysis_cache', '数据版本递增降级', _dv)
        _e("DELETE FROM analysis_cache")
        for fn in list(_registry.values()):
            try:
                fn()
            except Exception as _e:
                    try_err('analysis_cache', '静默降级', _e)
        # 物化追平(异步线程尽力——写操作不阻塞)
        # 2026-11-02 S2-2: 由「5 分钟限频 + 全量 90 天」改为「按业务日期增量」。
        #   旧做法两个硬伤:
        #     ① 补录历史日期后, 若 5 分钟内没有后续写操作, 重建被窗口挡掉 → agg 永不更新
        #        (读侧只直查「昨天+今天」, 补录 3 天前的数据没人兜底)
        #     ② 只能全量 90 天 → 单次 RU 高, 才不得不限频; 限频又放大 ①
        #   增量后单日重建很便宜(走 idx_orders_channel_ordered), 不需要限频;
        #   只在一次性铺开很多天(补录整个季度)时退化为全量, 省往返。
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
    except Exception as e:
        # 失效失败可见性(四维-实时性): 缓存未清则 TTL 内读旧值, 记 quality_logs 供审计
        try:
            from db import execute as _e2
            _e2("INSERT INTO quality_logs(log_type, level, message, source) "
                "VALUES('cache_invalidate','error',%s,'api')", (str(e)[:200],))
        except Exception as _e:
                try_err('analysis_cache', '静默降级', _e)