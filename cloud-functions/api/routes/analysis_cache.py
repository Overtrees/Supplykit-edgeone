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
# 重建(agg/日销物化)5 分钟窗口内只跑一次; 非阻塞锁防并发; 窗口内多余写操作合并
_MAT_LOCK_OBJ = None
_MAT_LAST = 0.0
_MAT_WINDOW = 300  # 5 分钟限频窗口(写操作后 5min 内多次 invalidate → 一次重建)


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
    2026-10-11 并发根治(行锁): SELECT ... FOR UPDATE 串行化同 key 计算——第一请求持锁算,
    其余阻塞在 FOR UPDATE(事务内)——释放后读结果共享(跨 Makers 实例可靠——替代轮询判定)"""
    try:
        from db import one, execute
        try:
            from db import transaction
            # 死锁重试(TiDB 1213): builder() 持锁重算 + 并发 invalidate_all DELETE/其他 key FOR UPDATE
            # → 事务死锁; 1213 时事务已回滚, 重试干净——保留 10-11"行锁串行化同 key 计算"设计
            for _try in range(3):
                try:
                    with transaction():
                        row = one("SELECT value, TIMESTAMPDIFF(SECOND, created_at, NOW(6)) AS age "
                                  "FROM analysis_cache WHERE `key`=%s FOR UPDATE", [key])
                        if row:
                            age = row.get("age")
                            v0 = row.get("value")
                            if v0 != "__CALC__" and age is not None and int(age) < ttl:
                                v = _loads(v0 or "")
                                if v is not _FAILED:
                                    return v
                            if v0 == "__CALC__":
                                execute("DELETE FROM analysis_cache WHERE `key`=%s", [key])
                        val = builder()
                        execute("REPLACE INTO analysis_cache(`key`, value, created_at) "
                                "VALUES(%s,%s,NOW(6))",
                                (key, json.dumps(val, ensure_ascii=False, default=str)))
                        return val
                except Exception as _e:
                    if _try < 2 and ("1213" in str(_e) or "deadlock" in str(_e).lower()):
                        continue
                    raise
        except Exception as _elock:
            # 锁不可用(连接/环境)降级: 无锁直算(生产正常; 测试/mock 环境兜底)
            try_err('analysis_cache', '缓存锁降级直算', _elock)
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


def invalidate_all():
    """数据变更后全局失效: 删共享表缓存(跨实例一致) + 物化即时追平(写操作后 agg/快照增量——
    导入/删单/规则/参数变更后物化立即跟数据, 非轮询延迟——数据实时性根治)"""
    try:
        from db import execute as _e
        _e("DELETE FROM analysis_cache")
        for fn in list(_registry.values()):
            try:
                fn()
            except Exception as _e:
                    try_err('analysis_cache', '静默降级', _e)
        # 物化追平(异步线程尽力——写操作不阻塞; 兜底链: 每 miss 即时检查续段 + 读路径当天直查保证数据完整)
        # 2026-10-17 限频: 5 分钟窗口一次(写放大控制——35 调用点 × 全量重建曾 RU 爆)
        try:
            import threading as _thm
            import time as _t2
            global _MAT_LOCK_OBJ, _MAT_LAST
            if _MAT_LOCK_OBJ is None:
                _MAT_LOCK_OBJ = _thm.Lock()
            _now2 = _t2.time()
            if _now2 - _MAT_LAST < _MAT_WINDOW:
                pass  # 窗口内已有重建(数据时效由读侧当天直查兜底, 不重复打 RU)
            elif _MAT_LOCK_OBJ.acquire(blocking=False):
                _MAT_LAST = _now2

                def _mat():
                    try:
                        from routes.dashboard import _rebuild_day_agg, _snapshot_step
                        from routes.dashboard_maintain import _mark_agg_dirty as _mk, _clear_agg_dirty as _ck
                        from db import one as _o1
                        _db = _o1("SELECT COALESCE(MAX(DATE(ordered_at)),'') AS m FROM orders "
                                  "WHERE (deleted_at IS NULL OR deleted_at='')") or {}
                        _dbs = str(_db.get("m") or "")[:10]
                        if _rebuild_day_agg(60):
                            _ck()
                        else:
                            _mk()
                        # 2026-10-17 日销物化表重建(写操作后预重建——限频窗口上方已合并;
                        # 重建含当天订单, 消费方读表零重算; 实时性: 库存仍每次直查, 物化只慢变日销)
                        try:
                            from biz.sales import _rebuild_sku_sales_stats as _rss
                            _rss("jd")
                            _rss("other")
                        except Exception as _e2b:
                                try_err('analysis_cache', '物化重建降级', _e2b)
                        if _dbs:
                            _snapshot_step("jd", _dbs)
                            _snapshot_step("other", _dbs)
                    except Exception as _e2:
                            try_err('analysis_cache', '物化追平降级', _e2)
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