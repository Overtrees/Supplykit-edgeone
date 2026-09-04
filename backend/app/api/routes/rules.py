"""规则管理 API"""
from fastapi import APIRouter, HTTPException
from app.core.database import get_db
from app.core.response import ok, fail
from app.core.schemas import RuleCreate, RuleUpdate
import json

router = APIRouter(prefix="/api/rules", tags=["rules"])

# 规则缓存（30s TTL，创建/更新/删除规则时自动失效）
_rules_cache = {}


def _bump_rules_version(db):
    """递增 _rules_version(规则变更版本)——alerts 缓存校验它, 规则操作后告警即时失效"""
    try:
        from datetime import datetime, timezone
        v = db.table("replenishment_config").select("*").eq("key", "_rules_version").execute().data
        nv = (int(v[0]["value"]) + 1) if v and v[0].get("value") else 1
        db.table("replenishment_config").upsert({"key": "_rules_version", "value": str(nv), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except Exception:
        pass

@router.get("")
def list_rules(channel: str = 'jd', include_deleted: bool = False, db = get_db()):
    import time
    try:
        _v = db.table("replenishment_config").select("*").eq("key", "_rules_version").execute().data
        _ver = int(_v[0]["value"]) if _v and _v[0].get("value") else 0
    except: _ver = 0
    key = f"rules_{channel}_{'del' if include_deleted else 'live'}_{_ver}"
    cached = _rules_cache.get(key)
    if cached and time.time() - cached['ts'] < 180:
        return cached['data']
    if channel == 'all':
        # 回收站等场景：跨渠道全量
        data = db.table("rules").select("*").order("id").execute().data
    else:
        data = db.table("rules").select("*").eq("channel", channel).order("id").execute().data
    if include_deleted:
        # 回收站：只取已软删除的
        data = [r for r in data if r.get("deleted_at")]
    else:
        # 正常列表：隐藏已软删除的（修复：删除规则后页面残留）
        data = [r for r in data if not (r.get("deleted_at") or "")]
    _rules_cache[key] = {'data': ok(data), 'ts': time.time()}
    return ok(data)

@router.post("")
def create_rule(data: RuleCreate, db = get_db()):
    _rules_cache.clear(); _bump_rules_version(db)
    try:
        payload = {
            "name": data.name, "event": data.event,
            "condition_json": json.dumps(data.condition),
            "alert_type": data.alert_type,
            "alert_title": data.alert_title,
            "alert_desc": data.alert_desc,
            "severity": data.severity,
            "channel": data.channel,
            "mode": data.mode,
            "is_active": 1 if data.is_active else 0,
        }
        db.table("rules").insert(payload).execute()
        from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated")
        return ok({"message": "规则已创建"})
    except Exception as e:
        import traceback, logging
        logging.error(f"[rules] create error: {traceback.format_exc()}")
        try:
            db.table("quality_logs").insert({"log_type": "rules_error", "level": "error",
                "message": f"创建规则失败: {e}", "source": "rules"}).execute()
        except Exception:
            pass
        return fail(f"创建规则失败: {e} | {traceback.format_exc().splitlines()[-2:]}", status=500)

@router.put("/{rule_id}")
def update_rule(rule_id: int, data: RuleUpdate, db = get_db()):
    _rules_cache.clear(); _bump_rules_version(db)
    if not db.table("rules").select("id").eq("id", rule_id).execute().data:
        raise HTTPException(status_code=404, detail="规则不存在")
    update = {}
    if data.name is not None: update["name"] = data.name
    if data.event is not None: update["event"] = data.event
    if data.alert_type is not None: update["alert_type"] = data.alert_type
    if data.alert_title is not None: update["alert_title"] = data.alert_title
    if data.alert_desc is not None: update["alert_desc"] = data.alert_desc
    if data.severity is not None: update["severity"] = data.severity
    if data.condition is not None: update["condition_json"] = json.dumps(data.condition)
    if data.mode is not None: update["mode"] = data.mode
    if data.is_active is not None: update["is_active"] = 1 if data.is_active else 0
    if update:
        db.table("rules").update(update).eq("id", rule_id).execute()
    from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated"); return ok({"message": "已更新"})

@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db = get_db()):
    _rules_cache.clear(); _bump_rules_version(db)
    try:
        # 软删除
        from datetime import datetime, timezone
        db.table("rules").update({"is_active": 0, "deleted_at": datetime.now(timezone.utc).isoformat()}).eq("id", rule_id).execute()
        _sync_alerts_for_rules([rule_id], True, db)
        from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated")
        return ok({"message": "已删除", "id": rule_id})
    except Exception as e:
        import traceback, logging
        logging.error(f"[rules] delete error: {traceback.format_exc()}")
        try:
            db.table("quality_logs").insert({"log_type": "rules_error", "level": "error",
                "message": f"删除规则{rule_id}失败: {e}", "source": "rules"}).execute()
        except Exception:
            pass
        return fail(f"删除规则失败: {e} | {traceback.format_exc().splitlines()[-2:]}", status=500)

@router.post("/{rule_id}/restore")
def restore_rule(rule_id: int, db = get_db()):
    _rules_cache.clear(); _bump_rules_version(db)
    db.table("rules").update({"is_active": 1, "deleted_at": ""}).eq("id", rule_id).execute()
    _sync_alerts_for_rules([rule_id], False, db)
    from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated"); return ok({"message": "已恢复", "id": rule_id})

@router.post("/{rule_id}/permanent-delete")
def permanent_delete_rule(rule_id: int, db = get_db()):
    _rules_cache.clear(); _bump_rules_version(db)
    db.table("rules").delete().eq("id", rule_id).execute()
    _sync_alerts_for_rules([rule_id], True, db)
    from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated")
    return ok({"message": "已永久删除", "id": rule_id})

def _sync_alerts_for_rules(ids: list, disabled: bool, db, channel: str = ''):
    """规则停用/启用时联动对应类型告警（不依赖 related_rule_id，兼容历史遗留告警）

    语义：告警按 (alert_type, channel) 与规则类型绑定。
    - 停用/删除：该 (alert_type, channel) 下已无 active 规则 → 整类告警置 inactive
    - 恢复/启用：该类型恢复 active 规则 → 该类 rules_engine 告警恢复 active
    主体隔离: 传入 channel 时仅收集该渠道规则的 (alert_type, channel)，防跨渠道联动
    """
    # 收集这些规则的 (alert_type, channel)
    pairs = set()
    try:
        q = db.table("rules").select("*").in_("id", ids)
        if channel and channel != 'all':
            q = q.eq("channel", channel)
        for r in q.execute().data:
            at = r.get('alert_type', '')
            if at:
                pairs.add((at, r.get('channel', 'jd')))
    except Exception as e:
        import logging; logging.warning(f"[rules] collect alert_type: {e}")
    for at, ch in pairs:
        others = db.table("rules").select("id").eq("alert_type", at).eq("channel", ch).eq("is_active", 1).execute().data
        if disabled:
            # 停用：该类型已无 active 规则 → 整类告警关闭（rules_engine + event_bus，不含 replenishment_engine）
            if not others:
                db.table("alerts").update({"status": "inactive"}).in_("source", ["rules_engine", "event_bus"]).eq("alert_type", at).eq("channel", ch).eq("status", "active").execute()
        else:
            # 恢复：有 active 规则 → 恢复该类 rules_engine 告警
            if others:
                db.table("alerts").update({"status": "active"}).eq("alert_type", at).eq("channel", ch).eq("source", "rules_engine").execute()
    # 增量修正看板缓存的 active_alerts(规则操作只影响告警数, 无需 invalidate_dashboard
    # 触发 summary 同步重建 14s + stockRisk 缓存失效——这是规则操作后回看板卡10s+的根因)
    try:
        from app.core.dashboard_cache import _cache_by_channel
        for _at, _ch in pairs:
            _cached = _cache_by_channel.get(_ch)
            if _cached and 'summary' in _cached.get('data', {}):
                _cnt = db.table("alerts").select("count(*)").eq("channel", _ch).eq("status", "active").execute()
                _n = _cnt.count if hasattr(_cnt, 'count') else len(_cnt.data or [])
                _cached['data']['summary']['active_alerts'] = _n
    except Exception:
        pass


@router.post("/batch")
def batch_rules(body: dict, channel: str = '', db = get_db()):
    """批量操作: {action: 'delete'|'restore'|'active'|'inactive', ids: [...]}

    主体隔离: 批量操作只允许命中本渠道规则(id 全局唯一但跨渠道误传时禁止生效, 双保险)
    """
    from datetime import datetime, timezone
    action = body.get("action", "")
    ids = [int(x) for x in (body.get("ids") or []) if isinstance(x, int) or str(x).isdigit()]
    if not ids:
        return ok({"updated": 0})
    _rules_cache.clear(); _bump_rules_version(db)
    # channel 由前端 api 拦截器自动注入; 'all'/空 表示全局(兼容任务/回收站调用)
    def _scoped(q):
        if channel and channel != 'all':
            return q.eq("channel", channel)
        return q
    if action == 'delete':
        _scoped(db.table("rules").update({"is_active": 0, "deleted_at": datetime.now(timezone.utc).isoformat()}).in_("id", ids)).execute()
        _sync_alerts_for_rules(ids, True, db, channel)
    elif action == 'purge':
        # 批量永久删除（回收站用）：硬删除规则，关联告警一并清理
        _scoped(db.table("rules").delete().in_("id", ids)).execute()
        _sync_alerts_for_rules(ids, True, db, channel)
    elif action == 'restore':
        _scoped(db.table("rules").update({"is_active": 1, "deleted_at": ""}).in_("id", ids)).execute()
        _sync_alerts_for_rules(ids, False, db, channel)
    elif action == 'active':
        _scoped(db.table("rules").update({"is_active": 1}).in_("id", ids)).execute()
        _sync_alerts_for_rules(ids, False, db, channel)
    elif action == 'inactive':
        _scoped(db.table("rules").update({"is_active": 0}).in_("id", ids)).execute()
        _sync_alerts_for_rules(ids, True, db, channel)
    else:
        return fail(f"未知操作: {action}")
    try:
        from app.api.routes.ws import broadcast_sync; broadcast_sync("data.updated")
    except Exception:
        pass
    return ok({"updated": len(ids)})


@router.post("/{rule_id}/test")
def test_rule(rule_id: int, body: dict, db = get_db()):
    """规则引擎可视化调试：传入模拟数据(库存/订单), 判断该规则条件是否触发"""
    rules_data = db.table("rules").select("*").eq("id", rule_id).execute().data
    if not rules_data:
        raise HTTPException(status_code=404, detail="规则不存在")
    rule = rules_data[0]
    try:
        cond = json.loads(rule.get("condition_json") or "{}")
    except Exception:
        cond = {}
    # 构造上下文：inv/order 从 body 取，默认 0
    inv = body.get("inv") or {}
    order = body.get("order") or {}
    ctx = {
        "inv": {
            "available_qty": inv.get("available_qty", 0),
            "safety_qty": inv.get("safety_qty", 0),
            "in_transit_qty": inv.get("in_transit_qty", 0),
            "warehouse_type": inv.get("warehouse_type", ""),
            "days_since_last": inv.get("days_since_last", 0),
        },
        "order": {
            "quantity": order.get("quantity", 0),
            "total_amount": order.get("total_amount", 0),
        },
        "channel": rule.get("channel", "jd"),
        "days_since_last": inv.get("days_since_last", 0),
        "stock": inv.get("available_qty", 0),
    }
    from app.core.rules import _check_condition, _resolve_value
    triggered = _check_condition(cond, ctx)
    # 计算明细（左右值解析结果）
    detail = {}
    try:
        left_raw = cond.get("left", "")
        right_raw = cond.get("right", "")
        detail["left"] = left_raw
        detail["right"] = right_raw
        detail["op"] = cond.get("op", "<")
        detail["left_value"] = _resolve_value(left_raw, ctx)
        # 简化 right 值解析（兼容 max() 与直接值）
        if str(right_raw).startswith("max("):
            detail["right_value"] = f"max({right_raw[4:-1]})"
        elif str(right_raw).replace('.','',1).isdigit():
            detail["right_value"] = float(right_raw)
        elif '.' in str(right_raw):
            detail["right_value"] = _resolve_value(right_raw, ctx)
        else:
            detail["right_value"] = right_raw
        detail["warehouse"] = cond.get("warehouse", "")
    except Exception:
        pass
    return ok({
        "triggered": triggered,
        "alert_title": rule.get("alert_title", ""),
        "alert_desc": rule.get("alert_desc", ""),
        "detail": detail,
    })
    rules_data = db.table("rules").select("*").eq("is_active", 1).execute().data
    from app.core.rules import evaluate as rule_evaluate
    count = 0
    for r in rules_data:
        try: rule_evaluate(r["event"], {"db": db, "rule": r, "channel": r.get('channel', 'jd')}); count += 1
        except Exception as e: import logging; logging.warning(f"[rules] evaluate rule {r.get('id')} error: {e}")
    return ok({"message": f"已评估 {count} 条规则", "count": count})
