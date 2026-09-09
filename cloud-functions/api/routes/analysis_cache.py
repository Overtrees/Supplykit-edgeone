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
import json

_registry = {}


def register(clear_fn):
    """兼容旧 API: 内存清理函数已无意义(表缓存), 保留空实现防崩溃"""
    _registry[len(_registry)] = clear_fn


_FAILED = object()


def _loads(value):
    try:
        return json.loads(value)
    except Exception:
        return _FAILED


def cache_get(key, ttl, builder):
    """共享表缓存读: 命中(存在且 TTL 内)返回缓存; 否则 builder() 重算落表"""
    try:
        from db import one, execute
        row = one("SELECT value, TIMESTAMPDIFF(SECOND, created_at, NOW(6)) AS age "
                  "FROM analysis_cache WHERE `key`=%s", [key])
        if row:
            age = row.get("age")
            if age is not None and int(age) < ttl:
                v = _loads(row.get("value") or "")
                if v is not _FAILED:
                    return v
        val = builder()
        execute("REPLACE INTO analysis_cache(`key`, value, created_at) "
                "VALUES(%s,%s,NOW(6))",
                (key, json.dumps(val, ensure_ascii=False, default=str)))
        return val
    except Exception:
        return builder()  # 任何异常降级直算(表缺失/连接问题不阻塞业务)


def invalidate_all():
    """数据变更后全局失效: 删共享表缓存(跨实例一致)"""
    try:
        from db import execute as _e
        _e("DELETE FROM analysis_cache")
        for fn in list(_registry.values()):
            try:
                fn()
            except Exception:
                pass
    except Exception:
        pass