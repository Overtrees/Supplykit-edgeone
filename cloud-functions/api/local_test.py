"""本地回归测试: mock db + TestClient 跑全部路由(部署前置定位 Python 层 bug)

用法: cd cloud-functions/api && python3 local_test.py
覆盖: auth / dashboard summary(含 periods 环比/health_index/漏斗)
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET", "local-test-secret")

from datetime import datetime, timedelta, timezone

# ── mock db 层 ──────────────────────────────────────────────────────
import db as _db

_ORDERS = []
_NOW = datetime.now(timezone.utc)
for k in range(60):
    d = (_NOW - timedelta(days=k)).strftime("%Y-%m-%d")
    _ORDERS.append({"d": d, "order_status": "已完成", "store": "自营旗舰店", "g": 100.0, "sub": 5.0, "cnt": 2})
    _ORDERS.append({"d": d, "order_status": "待发货", "store": "自营旗舰店", "g": 50.0, "sub": 1.0, "cnt": 1})
    _ORDERS.append({"d": d, "order_status": "待确认", "store": "专营店B", "g": 0.0, "sub": 0.0, "cnt": 1})

_INV = [
    {"warehouse_type": "platform", "healthy": 40, "warning": 8, "out_of_stock": 2, "total": 50},
    {"warehouse_type": "platform_b", "healthy": 30, "warning": 5, "out_of_stock": 1, "total": 36},
    {"warehouse_type": "own", "healthy": 20, "warning": 2, "out_of_stock": 0, "total": 22},
]


def fake_query(sql, params=None):
    # health_index 查询(须在 FROM inventory WHERE 分支前, 避免被 _FAKE_INV 抢占致 total=0)
    if "GROUP BY warehouse_type" in sql:
        return list(_INV)
    if "warehouse_type IN ('platform','platform_b') GROUP BY sku" in sql:
        return [{"healthy": 65, "warning": 10, "out_of_stock": 2, "total": 77}]
    if "FROM orders WHERE" in sql and "COUNT" not in sql:
        return [{"id": 1, "order_no": "NO0000000001", "sku": "SKU0001", "barcode": "69-01",
                 "product_name": "禾味调味料1号", "store": "自营旗舰店", "warehouse": "华东C仓",
                 "quantity": 2, "unit_price": 25.0, "total_amount": 50.0, "order_status": "已完成",
                 "ordered_at": "2026-09-04 10:00:00", "paid_at": "2026-09-04 10:01:00",
                 "platform": "jd", "channel": "jd", "deleted_at": ""}]
    if "FROM inventory WHERE" in sql or "FROM inventory i" in sql:
        return [dict(x) for x in _FAKE_INV]
    if "FROM products WHERE" in sql:
        return [dict(x) for x in _FAKE_PROD]
    if "FROM daily_sales_snapshot" in sql:
        # 模拟 28 天日销: SKU0001 华东C仓 每天 5 件
        rows = []
        from datetime import datetime as _dt2, timedelta as _td2, timezone as _tz2
        _now2 = _dt2.now(_tz2.utc)
        for k in range(28):
            d = (_now2 - _td2(days=k)).strftime('%Y-%m-%d')
            rows.append({"date": d, "sku": "SKU0001", "warehouse": "华东C仓", "order_count": 5})
            rows.append({"date": d, "sku": "SKU0002", "warehouse": "华东C仓", "order_count": 2})
        return rows
    if "GROUP BY DATE(ordered_at), order_status, store" in sql:
        return list(_ORDERS)
    if "FROM sync_tasks" in sql:
        return [{"task_id": "seed_1", "task_type": "seed", "status": "done", "result": "{}",
                 "params": "{}", "channel": "jd", "created_at": "2026-09-05 10:00:00"}]
    if "FROM rules" in sql:
        return [{"id": 1, "name": "低库存预警", "event": "inventory.changed",
                 "condition_json": '{"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}',
                 "alert_type": "low_stock", "alert_title": "低库存预警: {product_name}",
                 "alert_desc": "可用 {avail} < 安全线 {safety}", "severity": "warning",
                 "is_active": 1, "channel": "jd", "mode": "", "deleted_at": ""}]
    return []


def fake_one(sql, params=None):
    if "FROM rules" in sql:
        rows = fake_query(sql, params)
        return rows[0] if rows else None
    if "FROM users WHERE" in sql:
        from routes.common import hash_password as _hp
        uname = params[0] if params else "admin"
        return {"username": uname, "password_hash": _hp("admin123"), "role": "admin"}
    if "FROM sync_tasks" in sql:
        if params and params[0] == "none":
            return None
        return {"status": "done", "result": '{"result": {"target": "order", "success": 1, "failed": 0}}'}
    if "FROM export_files" in sql:
        return {"content": "SKU,商品名\nSKU0001,禾味调味料1号\n"}
    if "GROUP BY DATE(ordered_at)" in sql or "GROUP BY warehouse_type" in sql or "warehouse_type IN" in sql:
        return fake_query(sql, params)[0] if fake_query(sql, params) else {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
    if "COUNT(*)" in sql:
        return {"c": 3}
    if "SELECT 1" in sql:
        return {"ok": 1}
    return None


_db.query = fake_query
_db.one = fake_one
_db.execute = lambda sql, params=None: 0
_db.executemany = lambda sql, seq: None


# ── replenishment mock ──
_FAKE_INV = [
    {"sku": "SKU0001", "warehouse": "华东C仓", "warehouse_type": "platform", "available_qty": 30, "in_transit_qty": 5, "c_transit": 10, "safety_qty": 50},
    {"sku": "SKU0001", "warehouse": "B仓", "warehouse_type": "platform_b", "available_qty": 100, "in_transit_qty": 20, "c_transit": 0, "safety_qty": 0},
    {"sku": "SKU0002", "warehouse": "华东C仓", "warehouse_type": "platform", "available_qty": 0, "in_transit_qty": 0, "c_transit": 0, "safety_qty": 40},
]
_FAKE_PROD = [
    {"sku": "SKU0001", "barcode": "69-01", "product_name": "禾味调味料1号", "brand": "禾味", "store": "自营旗舰店", "category": "调味", "box_qty": 12},
    {"sku": "SKU0002", "barcode": "69-02", "product_name": "山泉饮料2号", "brand": "山泉", "store": "自营旗舰店", "category": "饮料", "box_qty": 24},
]

# ── 导入 app 并测试 ────────────────────────────────────────────────
from fastapi.testclient import TestClient
import index

client = TestClient(index.app)
PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("✅ %s" % name)
    else:
        FAIL += 1
        print("❌ %s %s" % (name, extra))


# health(数据版本指纹)
r = client.get("/health")
d = r.json()
check("health 200", r.status_code == 200, r.text[:150])
check("health 含 version 字段", isinstance(d, dict) and "version" in d, r.text[:150])

# auth
r = client.post("/auth/login", json={"username": "demo", "password": "demo123"})
check("auth/login demo", r.status_code == 200 and r.json().get("ok"), r.text[:150])
TOKEN = r.json().get("token", "")
check("login 返回 token", bool(TOKEN))

r = client.post("/auth/login", json={"username": "x", "password": "bad"})
check("auth/login 错误密码", r.json().get("ok") is False)

r = client.get("/auth/check", headers={"Authorization": "Bearer " + TOKEN})
check("auth/check 带 token", r.status_code == 200 and r.json().get("ok"), r.text[:150])

# admin 用户(写操作测试用; demo 只读)
r = client.post("/auth/setup", json={"username": "admin", "password": "admin123"})
check("auth/setup admin", r.status_code == 200 and r.json().get("ok") is True, r.text[:150])
ADMIN = r.json().get("token", "")
check("setup 返回 admin token", bool(ADMIN))
r = client.post("/auth/login", json={"username": "admin", "password": "admin123"})
check("auth/login admin", r.status_code == 200 and r.json().get("ok"), r.text[:150])
ADMIN = r.json().get("token", "")

# 中间件
r = client.get("/dashboard/summary")
check("无 token 401", r.status_code == 401, str(r.status_code))

# dashboard summary
r = client.get("/dashboard/summary?channel=jd", headers={"Authorization": "Bearer " + TOKEN})
d = r.json()
dd = d.get("data") if isinstance(d, dict) else d
if d.get("ok") is False:
    check("summary 无错误", False, (d.get("detail") or "")[:200] + (d.get("tb") or "")[:300])
else:
    check("summary gmv>0", dd["summary"]["gmv"] > 0, str(dd))
    check("summary orders=240", dd["summary"]["total_orders"] == 240, str(dd["summary"]["total_orders"]))
    check("trend 60 点", len(dd.get("trend", [])) == 60)
    check("health score 存在", dd["health_index"]["score"] is not None)
    check("periods 环比非0", dd["periods"]["month"]["gmv"] > 0, str(dd["periods"]["month"]))
    check("stores 含店", len(dd.get("stores", [])) >= 1)
    check("funnel 5 段", len(dd.get("funnel", [])) == 5)

# 自定义日期
r = client.get("/dashboard/summary?channel=jd&start_date=2026-08-01&end_date=2026-08-31",
               headers={"Authorization": "Bearer " + TOKEN})
check("自定义日期 200", r.status_code == 200 and r.json().get("ok") is not False, r.text[:120])

# replenishment
r = client.get("/insights/replenishment?channel=jd&mode=bbcc", headers={"Authorization": "Bearer " + TOKEN})
d = r.json()
if d.get("ok") is False:
    check("replenish 无错误", False, (d.get("detail") or "")[:200])
else:
    items = d.get("data") or []
    check("replenish 返回列表", isinstance(items, list), str(d)[:150])
    check("replenish SKU0001 充足无建议", not any(i.get("sku") == "SKU0001" and (i.get("suggested_qty") or 0) > 0 for i in items), str(items)[:300])
    check("replenish SKU0002 有建议(缺货)", any(i.get("sku") == "SKU0002" for i in items))
r = client.get("/insights/replenishment?channel=jd&mode=traditional", headers={"Authorization": "Bearer " + TOKEN})
check("replenish traditional 200", r.status_code == 200, r.text[:120])

# orders / products / insights
r = client.get("/orders?page=1&page_size=5", headers={"Authorization": "Bearer " + TOKEN})
d = r.json()
check("orders 返回分页", d.get("ok") and len(d.get("data", {}).get("items", [])) >= 1, r.text[:150])
r = client.get("/products?page=1&page_size=5", headers={"Authorization": "Bearer " + TOKEN})
check("products 200", r.status_code == 200 and r.json().get("ok"), r.text[:120])
r = client.get("/insights/slow-moving?channel=jd", headers={"Authorization": "Bearer " + TOKEN})
d = r.json()
check("slow-moving 200", d.get("ok") is not False, (d.get("detail") or "")[:150])
r = client.get("/insights/with-sales?wh_type=own&channel=jd", headers={"Authorization": "Bearer " + TOKEN})
check("with-sales 200", r.json().get("ok") is not False, r.text[:150])

# ── 契约补齐组 A: ping/删除恢复/批量/缺货/批次/配置 ──
r = client.get("/insights/ping")
check("ping 免鉴权 {ok:true}", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
AH = {"Authorization": "Bearer " + ADMIN}
r = client.delete("/orders/1", headers={"Authorization": "Bearer " + TOKEN})
check("orders 软删 demo 只读403", r.status_code == 403, r.text[:120])
r = client.delete("/orders/1", headers=AH)
check("orders 软删 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.post("/orders/1/restore", headers=AH)
check("orders restore 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.post("/orders/1/permanent-delete", headers=AH)
check("orders 永久删除 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.get("/orders?include_deleted=1", headers=AH)
check("orders include_deleted 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])

r = client.post("/products/batch", json={"action": "active", "ids": [1, 2]}, headers=AH)
check("products batch active", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.post("/products/batch", json={"action": "delete", "ids": [1]}, headers=AH)
check("products batch delete", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])

r = client.post("/rules/batch", json={"action": "purge", "ids": [1]}, headers=AH)
check("rules batch purge", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.post("/rules/1/restore", headers=AH)
check("rules restore 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.get("/rules?include_deleted=1", headers=AH)
check("rules include_deleted 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])

r = client.get("/inventory/out-of-stock?channel=jd&wh=own", headers=AH)
check("out-of-stock 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.delete("/inventory/1", headers=AH)
check("inventory 删除 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])

r = client.get("/batches?channel=jd&sku=SKU0001", headers=AH)
check("batches 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])

r = client.get("/replenishment-config/history?channel=jd", headers=AH)
check("config history 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.get("/replenishment-config/slow-cats?channel=jd", headers=AH)
check("slow-cats GET 200", r.status_code == 200, r.text[:120])
r = client.put("/replenishment-config/slow-cats?channel=jd", json={"items": [{"cat": "调味", "days": 30}]}, headers=AH)
check("slow-cats PUT 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.get("/replenishment-config/seasons?channel=jd&mode=bbcc", headers=AH)
check("seasons GET 200", r.status_code == 200, r.text[:120])
r = client.put("/replenishment-config/seasons?channel=jd&mode=bbcc",
               json={"items": [{"key": "夏", "factor": 1.2, "enabled": True}]}, headers=AH)
check("seasons PUT 200", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])

r = client.get("/insights/with-sales?wh_type=own&channel=jd", headers=AH)
d = r.json()
if d.get("ok") is True:
    items = d.get("data") or []
    check("with-sales 增强字段(month/batch)",
          (len(items) == 0) or ("month_start" in items[0] and "month_inbound" in items[0]
                                and "batch_count" in items[0]), str(items)[:200])
else:
    check("with-sales 增强字段(month/batch)", False, (d.get("detail") or "")[:150])

# ── 契约补齐组 B: tasks / seed / exports ──
r = client.get("/tasks?channel=jd", headers=AH)
check("tasks 列表", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
r = client.get("/seed/fill/status?task_id=seed_1", headers=AH)
check("fill/status done", (r.json().get("data") or {}).get("status") == "done", r.text[:120])
r = client.get("/seed/fill/status?task_id=none", headers=AH)
check("fill/status not_found", (r.json().get("data") or {}).get("status") == "not_found", r.text[:120])
r = client.post("/seed/fill", json={}, headers=AH)
check("seed/fill 有数据 requires_reset", (r.json().get("data") or {}).get("requires_reset") is True, r.text[:120])
r = client.post("/seed/reset", json={}, headers=AH)
check("seed/reset 返回 task_id", r.json().get("ok") is True and bool((r.json().get("data") or {}).get("task_id")), r.text[:120])
r = client.post("/exports?type=replen&mode=bbcc&channel=jd", headers=AH)
check("exports 平铺 task_id", r.json().get("ok") is True and bool(r.json().get("task_id")), r.text[:120])
r = client.get("/exports/download/test.csv", headers=AH)
check("exports download csv", r.status_code == 200 and "csv" in r.headers.get("content-type", ""), r.text[:120])
r = client.get("/exports/download/test.xlsx", headers=AH)
check("exports download xlsx", r.status_code == 200 and "spreadsheetml" in r.headers.get("content-type", ""), r.text[:120])

# ── 契约补齐组 C: cleansing / purchase / disposal ──
CSV_DATA = "订单号,SKU,数量,商品名称\nNO9001,SKU0001,5,禾味调味料1号\n"
F = {"file": ("t.csv", CSV_DATA, "text/csv")}
r = client.post("/cleansing/detect", files=F, headers=AH)
d = r.json()
check("cleansing/detect 平铺 columns", d.get("ok") is True and len(d.get("columns", [])) == 4
      and d.get("total") == 1, r.text[:150])
MP = json.dumps({"订单号": {"target": "order_no", "type": "string"},
                 "SKU": {"target": "sku", "type": "string"},
                 "数量": {"target": "quantity", "type": "number"}})
r = client.post("/cleansing/preview", files=F, headers=AH,
                data={"mapping": MP, "target": "order", "channel": "jd"})
d = r.json()
pv = d.get("preview") or [{}]
check("cleansing/preview", d.get("ok") is True and d.get("total") == 1
      and pv[0].get("order_no") == "NO9001", r.text[:150])
r = client.post("/cleansing/execute-async", files=F, headers=AH,
                data={"mapping": "{}", "target": "order", "channel": "jd"})
d = r.json()
check("cleansing/execute-async", d.get("ok") is True and bool(d.get("task_id")), r.text[:150])
r = client.get("/cleansing/task/clean_1", headers=AH)
check("cleansing/task done", r.json().get("status") == "done", r.text[:120])
r = client.get("/cleansing/templates", headers=AH)
check("cleansing/templates GET", r.json().get("ok") is True, r.text[:120])
r = client.post("/cleansing/templates", json={"name": "t1", "doc_type": "order",
                                              "mapping": {"订单号": "order_no"}}, headers=AH)
check("cleansing/templates POST", r.json().get("ok") is True, r.text[:120])

r = client.get("/purchase-orders?channel=jd", headers=AH)
check("purchase-orders GET", r.json().get("ok") is True, r.text[:120])
r = client.post("/purchase-orders?sku=SKU0001&store=%E8%87%AA%E8%90%A5%E6%97%97%E8%88%B0%E5%BA%97&channel=jd", headers=AH)
check("purchase-orders POST", r.json().get("ok") is True, r.text[:120])
r = client.delete("/purchase-orders?sku=SKU0001&store=%E8%87%AA%E8%90%A5%E6%97%97%E8%88%B0%E5%BA%97&channel=jd", headers=AH)
check("purchase-orders DELETE", r.json().get("ok") is True, r.text[:120])
r = client.put("/purchase-orders/1", json={"arrival_date": "2026-09-10"}, headers=AH)
check("purchase-orders PUT", r.json().get("ok") is True, r.text[:120])
r = client.get("/insights/purchase?channel=jd&mode=bbcc", headers=AH)
d = r.json()
check("insights/purchase suggestions", d.get("ok") is True
      and isinstance((d.get("data") or {}).get("suggestions", []), list), r.text[:150])
r = client.get("/insights/disposal-suggestions?channel=jd", headers=AH)
check("disposal-suggestions 200", r.json().get("ok") is True, r.text[:150])
r = client.post("/disposals/batch",
                json={"channel": "jd", "action": "mark", "note": "test",
                      "items": [{"sku": "SKU0001", "warehouse": "华东C仓"}]}, headers=AH)
d = r.json()
check("disposals/batch", d.get("ok") is True and (d.get("data") or {}).get("updated") == 1, r.text[:150])

# ── 定时任务(cron, 白名单免 token, 无 CRON_SECRET 时放行) ──
r = client.post("/cron/snapshot")
check("cron/snapshot", r.status_code == 200 and r.json().get("ok") is not False, r.text[:150])
r = client.post("/cron/freshness")
d = r.json()
check("cron/freshness 200", r.status_code == 200 and d.get("ok") is not False, r.text[:150])
r = client.post("/cron/archive")
d = r.json()
check("cron/archive 200", r.status_code == 200 and d.get("ok") is not False, r.text[:150])
r = client.post("/cron/cleanup-logs")
check("cron/cleanup-logs", r.json().get("ok") is not False, r.text[:150])
r = client.post("/cron/daily-rules")
check("cron/daily-rules", r.json().get("ok") is not False, r.text[:150])
r = client.post("/cron/recycle")
check("cron/recycle", r.json().get("ok") is not False, r.text[:150])
r = client.post("/cron/push-alerts")
check("cron/push-alerts(无webhook跳过)", r.json().get("ok") is not False, r.text[:150])

# ── A1 规则引擎: 表达式解析 / 条件评估 / test 端点 / evaluate 触发告警 ──
from core.rules import _check_condition, _resolve_value, evaluate
check("rules 表达式解析(加法)", _resolve_value("inv.available_qty + inv.in_transit_qty",
      {"inv": {"available_qty": 10, "in_transit_qty": 5}}) == 15.0, "resolve fail")
check("rules 表达式解析(括号乘除)", _resolve_value("(inv.available_qty + inv.in_transit_qty) / 2",
      {"inv": {"available_qty": 10, "in_transit_qty": 4}}) == 7.0, "resolve fail")
check("rules 条件 avail<safety 触发", _check_condition(
      {"left": "inv.available_qty", "op": "<", "right": "inv.safety_qty"},
      {"inv": {"available_qty": 5, "safety_qty": 10}}), "cond fail")
check("rules 条件不触发", not _check_condition(
      {"left": "inv.available_qty", "op": "<", "right": "inv.safety_qty"},
      {"inv": {"available_qty": 15, "safety_qty": 10}}), "cond fail")
check("rules max() 条件", _check_condition(
      {"left": "inv.available_qty", "op": "<=", "right": "max(inv.safety_qty*0.3, 1)"},
      {"inv": {"available_qty": 2, "safety_qty": 10}}), "max fail")
r = client.post("/rules/1/test", json={"inv": {"available_qty": 5, "safety_qty": 10}}, headers=AH)
d = r.json()
td = d.get("data") or {}
check("rules test 真实评估 triggered", d.get("ok") is True and td.get("triggered") is True,
      r.text[:200])
check("rules test detail 计算值", (td.get("detail") or {}).get("left_value") == 5.0, r.text[:200])
trig = evaluate("inventory.changed", {"sku": "SKU0001", "channel": "jd",
                                      "inv": {"available_qty": 30, "safety_qty": 50,
                                              "in_transit_qty": 5, "warehouse_type": "platform",
                                              "product_name": "测试商品"}})
check("evaluate 触发低库存规则", "低库存预警" in trig, str(trig))

# ── 规则引擎融合回归(P1): health.score 键名 / params.* 引用 / test_rule 参数模拟 ──
from core.rules import _check_condition
# health.score 需要 ctx['health'] dict(曾注入 health_score 顶层致 0 恒触发)
check("rules health.score 72 不触发", not _check_condition(
      {"left": "health.score", "op": "<", "right": "params.health_warning"},
      {"health": {"score": 72}, "params": {"health_warning": 60}}), "health 72 误触发")
check("rules health.score 55 触发", _check_condition(
      {"left": "health.score", "op": "<", "right": "params.health_warning"},
      {"health": {"score": 55}, "params": {"health_warning": 60}}), "health 55 未触发")
check("rules health 无参数兜底 999 不触发", not _check_condition(
      {"left": "health.score", "op": "<", "right": "params.health_warning"},
      {"health": {"score": 999}, "params": {}}), "health 999 误触发")
# params.lit 引用(断货规则条件)
check("rules params.lit 2<=3 触发", _check_condition(
      {"left": "inv.adj_dos", "op": "<=", "right": "params.lit"},
      {"inv": {"adj_dos": 2}, "params": {"lit": 3}}), "adj_dos 2 lit 3 未触发")
check("rules params.lit 4>3 不触发", not _check_condition(
      {"left": "inv.adj_dos", "op": "<=", "right": "params.lit"},
      {"inv": {"adj_dos": 4}, "params": {"lit": 3}}), "adj_dos 4 lit 3 误触发")
# test_rule 带 params 模拟(条件引用 params.lit)
r = client.post("/rules/1/test", json={"inv": {"available_qty": 5, "safety_qty": 10, "adj_dos": 2.5},
                                       "params": {"lit": 3}}, headers=AH)
d = r.json()
check("rules test 带 params(adj_dos<=lit 触发)", d.get("ok") is True, r.text[:200])

# ── 告警开关兼容回归(P2 修复): 字符串 alert_enabled / 空规则 return_hits ──
from core.rules import evaluate_many
_ins_count = [0]
_db.executemany = lambda sql, seq: _ins_count.__setitem__(0, _ins_count[0] + (len(seq) if seq else 0))
_ins_count[0] = 0
_r, _h = evaluate_many("inventory.changed",
                       [{"sku": "S1", "channel": "jd", "inv": {"available_qty": 5, "safety_qty": 10, "warehouse": "北京仓"}}],
                       "jd",
                       [{"id": 9, "name": "关", "event": "inventory.changed",
                         "condition_json": '{"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}',
                         "alert_type": "low_stock", "alert_title": "t", "alert_desc": "d", "severity": "warning",
                         "is_active": 1, "channel": "jd", "mode": "", "deleted_at": "",
                         "params": '{"alert_enabled":"0"}'}], return_hits=True)
check("alert_enabled 字符串'0' 跳过不告警", not _r and not _h and _ins_count[0] == 0, str((_r, _h, _ins_count[0])))
_r2, _h2 = evaluate_many("scheduled.daily", [{"sku": "S1", "channel": "jd"}], "jd", [], return_hits=True)
check("evaluate_many 空规则 return_hits", _r2 == [] and _h2 == set(), str((_r2, _h2)))
check("rules 字符串参数 params.lit '3' 数值化比较", _check_condition(
      {"left": "inv.adj_dos", "op": "<=", "right": "params.lit"},
      {"inv": {"adj_dos": 2.5}, "params": {"lit": "3"}}), "字符串参数未数值化")

# ── P2 核心算法单测: 濒临断货三级分级边界(_grade_risk 纯函数) ──
from routes.dashboard import _grade_risk
_g = _grade_risk
check("grade red: adj_dos<lit", _g(2.0, 10.0, 3.0) == (True, "red"), str(_g(2.0, 10.0, 3.0)))
check("grade red 边界: adj_dos==lit", _g(3.0, 10.0, 3.0) == (True, "red"), str(_g(3.0, 10.0, 3.0)))
check("grade orange: 逼近+缓冲破位", _g(3.5, 1.0, 3.0, 1.0, 1.2, 1.0) == (True, "orange"), str(_g(3.5, 1.0, 3.0, 1.0, 1.2, 1.0)))
check("grade orange 边界: adj_dos==lit+slack 且 buffer<=orange", _g(4.0, 1.2, 3.0, 1.0, 1.2, 1.0) == (True, "orange"), str(_g(4.0, 1.2, 3.0, 1.0, 1.2, 1.0)))
check("grade yellow: 缓冲破位但时间尚够", _g(5.0, 0.9, 3.0, 1.0, 1.2, 1.0) == (True, "yellow"), str(_g(5.0, 0.9, 3.0, 1.0, 1.2, 1.0)))
check("grade 不入选: 缓冲充足", _g(5.0, 2.0, 3.0, 1.0, 1.2, 1.0) == (False, None), str(_g(5.0, 2.0, 3.0, 1.0, 1.2, 1.0)))
check("grade 不入选: 逼近但缓冲未破位", _g(3.5, 1.5, 3.0, 1.0, 1.2, 1.0) == (False, None), str(_g(3.5, 1.5, 3.0, 1.0, 1.2, 1.0)))

# ── 工程审计: SQL 双包裹防线(_status_cond 禁止嵌 IN %s —— 2026-10-06 品牌负数根因) ──
import re as _re
import os as _os
_ROUTES_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "routes")
_bad2 = []
for _f in sorted(_os.listdir(_ROUTES_DIR)):
    if not _f.endswith(".py"):
        continue
    _s = open(_os.path.join(_ROUTES_DIR, _f), encoding="utf-8").read()
    for _m in _re.finditer(r'IN\s*\(\s*%s\s*\)', _s):
        _seg = _s[max(0, _m.start() - 600):_m.end()]
        if "def _status_cond" in _seg or "def _paid_cond" in _seg:
            continue
        if "_status_cond" in _seg:
            _bad2.append("%s:%d" % (_f, _s[:_m.start()].count("\n") + 1))
check("审计: 无 IN (%s) 与 _status_cond 同段(双包裹)", not _bad2, "; ".join(_bad2[:4]))

# ── 工程审计: 缓存 key 维度参数完整性(2026-10-07 purchase/repl 缺 days 污染) ──
_expect_keys = [
    ("dash_summary|", ["channel", "start_date", "end_date", "mode"]),
    ("dash_aux|", ["channel", "mode"]),
    ("stock_risk|", ["channel", "full"]),
    ("accel|", ["channel", "ratio", "min_qty"]),
    ("purchase|", ["channel", "mode", "days"]),
    ("repl|", ["channel", "mode", "days", "source"]),
]
_keyok = True
_keymsg = ""
for _f2 in sorted(_os.listdir(_ROUTES_DIR)):
    if not _f2.endswith(".py"):
        continue
    _s2 = open(_os.path.join(_ROUTES_DIR, _f2), encoding="utf-8").read()
    for _m2 in _re.finditer(r'_key\s*=\s*"([^"]+)"\s*%\s*\(([^)]*)\)', _s2):
        _tmpl, _args = _m2.group(1), _m2.group(2)
        _argnames = [a.strip().split("=")[0].strip() for a in _args.split(",") if a.strip()]
        for _pfx, _need in _expect_keys:
            if _tmpl.startswith(_pfx):
                _missing = [p for p in _need if p not in _argnames]
                if _missing or _tmpl.count("%s") != len(_need):
                    _keyok = False
                    _keymsg = "%s: %s 缺 %s" % (_pfx, _tmpl, _missing)
check("审计: 缓存 key 含全部维度参数(purchase/repl 含 days/source)", _keyok, _keymsg)

# ── 工程审计: DATE_FORMAT %% 转义(2026-10-07 M3 迁移 TypeError 防复发——参数化 SQL 单 % 被 pymysql 当占位符) ──
_IDX_SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.py"), encoding="utf-8").read()
_mig_ok2 = True
for _m3 in _re.finditer(r"DATE_FORMAT\([^,]+,\s*'([^']*)'", _IDX_SRC):
    if "%%" not in _m3.group(1):
        _mig_ok2 = False
check("审计: index.py 迁移 DATE_FORMAT 全部 %% 转义", _mig_ok2)

# ── 核心算法单测: 采购 MOQ 聚合放大(同供应商合计<起订量按占比放大) ──
import db as _db3
_old_q3 = _db3.query

def _q3(sql, params=None):
    if "FROM replenishment_config" in sql:
        return [
            {"key": "purchase_lead_days", "value": "7"},
            {"key": "moq", "value": "100"},
            {"key": "purchase_safety_days", "value": "5"},
            {"key": "max_turnover_days", "value": "15"},
            {"key": "season_config_bbcc", "value": "[]"},
        ]
    if "FROM products WHERE" in sql:
        return [
            {"sku": "SKU0001", "product_name": "禾味调味料1号", "barcode": "69-01", "brand": "禾味", "store": "自营旗舰店", "category": "调味", "box_qty": 12, "price": 10.0, "supplier_code": "SUP-A"},
            {"sku": "SKU0002", "product_name": "山泉饮料2号", "barcode": "69-02", "brand": "山泉", "store": "自营旗舰店", "category": "饮料", "box_qty": 24, "price": 10.0, "supplier_code": "SUP-A"},
        ]
    if "FROM inventory WHERE" in sql:
        return [
            {"sku": "SKU0001", "warehouse_type": "platform", "warehouse": "华东C仓", "available_qty": 0, "in_transit_qty": 0, "safety_qty": 0, "safety_days": 0},
            {"sku": "SKU0002", "warehouse_type": "platform", "warehouse": "华东C仓", "available_qty": 0, "in_transit_qty": 0, "safety_qty": 0, "safety_days": 0},
        ]
    return _old_q3(sql, params)

_db3.query = _q3
import routes.purchase as _rp
_old_pq = _rp.query
_rp.query = _q3  # _build_purchase 内部 query 为模块名字绑定, 需直改路由模块
from routes.purchase import _build_purchase as _bp
_moq = _bp("jd", "bbcc", 28)
_supA = [r for r in _moq if (r.get("supplier_code") or "") == "SUP-A"]
check("MOQ: 同供应商 2 SKU 均进入建议", len(_supA) == 2, "got %d" % len(_supA))
_tot = sum((r.get("purchase_qty") or 0) for r in _supA)
check("MOQ: 合计 84 < 起订 100 放大到 ≥100", _tot >= 100, "total=%d" % _tot)
_has_note = any("起订" in (r.get("note") or "") for r in _supA)
check("MOQ: note 含起订说明", _has_note)
_rp.query = _old_pq
_db3.query = _old_q3

# ── except pass 治理链路验证: try_err 调用无 NameError(mock execute 环境) ──
from routes.common import try_err as _te
try:
    _te("local_test", "try_err 链路验证", None)
    check("try_err 调用链正常(except pass 治理落点)", True)
except Exception as _te_e:
    check("try_err 调用链正常(except pass 治理落点)", False, str(_te_e)[:120])

# ── 核心算法单测: 日销稀疏收缩 + 3σ 非零日统计(2026-10-07 补货日销全 0 根因防复发) ──
from biz.sales import calc_sales_multi
from datetime import datetime as _dt4, timedelta as _td4, timezone as _tz4
_now4 = _dt4.now(_tz4.utc)

def _mk_daily(sales_days):
    """sales_days: [(days_ago, qty)] → {date: qty}"""
    return {(_now4 - _td4(days=k)).strftime('%Y-%m-%d'): q for k, q in sales_days}

# 场景1: 近28天仅 1 天 12 件(真实数据 SKU-0014-J 形状) → shrink 收缩, plain 保持日均
_d1 = _mk_daily([(27, 12)])
_m1 = calc_sales_multi({'S': _d1}, windows=[28], sparse='shrink')
_m1p = calc_sales_multi({'S': _d1}, windows=[28], sparse='plain')
_v1 = _m1[28]['S']
check("日销: 稀疏1天12件 shrink 收缩≈0.14(非0非虚高)", abs(_v1 - 12 / 28 / 3) < 0.001, "got %s" % _v1)
check("日销: 同场景 plain 保持日均0.43(采购口径不受影响)", abs(_m1p[28]['S'] - 12 / 28) < 0.001, "got %s" % _m1p[28]['S'])
# 场景2: 稳定序列每天5件 → 3σ 路径结果≈5(语义统一为摊薄日均后不变)
_d2 = _mk_daily([(k, 5) for k in range(28)])
_m2 = calc_sales_multi({'S': _d2}, windows=[28], sparse='shrink')
check("日销: 稳定28天×5件 ≈5.27(近3天1.5倍加权正常)", abs(_m2[28]['S'] - 5.267857) < 0.01, "got %s" % _m2[28]['S'])
# 场景3: 稳定序列含促销尖峰(28天×5件 + 1天40件) → 尖峰被3σ削(≈5非40)
_d3 = _mk_daily([(k, 5) for k in range(28)] + [(7, 40)])
_m3 = calc_sales_multi({'S': _d3}, windows=[28], sparse='shrink')
check("日销: 稳定序列促销尖峰40件被削(≈5)", abs(_m3[28]['S'] - 5) < 1.0, "got %s" % _m3[28]['S'])
# 场景4: 60天窗口多销售日但28天子窗口仅1天 → 3σ 不再全剔(原 bug: 0)
_d4 = _mk_daily([(k, 8) for k in range(29, 62, 3)] + [(27, 12)])
_m4 = calc_sales_multi({'S': _d4}, windows=[7, 14, 28], sparse='shrink')
check("日销: 60天多日+28天1日 → s28=0.14非0(原3σ全剔)", abs(_m4[28]['S'] - 12 / 28 / 3) < 0.01, "got %s" % _m4[28]['S'])

# ── 促销尖峰削峰(2026-10-07 近窗口促销识别) ──
from biz.sales import smooth_promo_spikes
# 场景5: 近7天 1 天 100 件促销 + 历史 60 天日均 1 件 → 削峰到 2×基线
_d5 = _mk_daily([(k, 1) for k in range(28, 60)] + [(3, 100)])
_s5 = smooth_promo_spikes(_d5)
check("削峰: 近7天100件vs历史1件 → 截断到2件", _s5.get(_d5 and (_now4 - _td4(days=3)).strftime('%Y-%m-%d')) == 2.0,
      "got %s" % _s5.get((_now4 - _td4(days=3)).strftime('%Y-%m-%d')))
# 场景6: 新品(无历史)近7天每天10件 → 不削峰
_d6 = _mk_daily([(k, 10) for k in range(7)])
_s6 = smooth_promo_spikes(_d6)
check("削峰: 无历史基线(新品)不削", _s6 == _d6)
# 场景7: 真实增长(历史日均1, 近期每天5件) → 峰值5未超5×基线 → 不削
_d7 = _mk_daily([(k, 1) for k in range(28, 60)] + [(k, 5) for k in range(7)])
_s7 = smooth_promo_spikes(_d7)
check("削峰: 真实增长(5件≤5×1)不削", _s7 == _d7)
# 场景8: 削峰后近7天窗口日销不再被促销推高(1天100件 → 削到2件 → s7 收缩后≈0.095)
_d8 = _mk_daily([(k, 1) for k in range(28, 60)] + [(3, 100)])
_m8 = calc_sales_multi({'S': smooth_promo_spikes(_d8)}, windows=[7, 28], sparse='shrink')
check("削峰: 促销后 s7≈0.095(不推高趋势加权)", abs(_m8[7]['S'] - 2 / 7 / 3) < 0.001, "got %s" % _m8[7]['S'])

# ── 传统逐仓需求门控(2026-10-07: 安全天数口径+日销门控——静态安全线不再撑补货量) ──
from routes.replenishment import _trad_suggested, _MIN_DS
check("门控: 日销0.02 → 0(无需求不补)", _trad_suggested(0.02, 3, 3, 2, 0) == 0)
check("门控: 日销0.09<门槛 → 0", _trad_suggested(0.09, 3, 3, 2, 0) == 0)
check("门控: 日销5×安全天数3 → 5×(3+3)−2=28", _trad_suggested(5, 3, 3, 2, 0) == 28)
check("门控: 日销1 → 1×6−2=4", _trad_suggested(1, 3, 3, 2, 0) == 4)
check("门控: 安全天数0 → 仅lead覆盖", _trad_suggested(5, 3, 0, 2, 0) == 13)
check("门控: 缺口0.9件<1 → 0(防凑整箱)", _trad_suggested(0.15, 3, 3, 0, 0) == 0)
check("门控: 缺口0.6件<1 → 0", _trad_suggested(0.2, 3, 0, 0, 0) == 0)
check("门控: 缺口1.2件≥1 → round=1", _trad_suggested(0.2, 3, 3, 0, 0) == 1)

# ── 传统逐仓备注语境(2026-10-07: 对齐 bbcc——无销量/低需求说明) ──
from routes.replenishment import _trad_note
check("备注: 日销0+有库存 → 无销量积压", "近30天无销量，库存积压" in _trad_note(0, 0, 0, 0, 100, 196, 0, 0))
check("备注: 日销0+无库存 → ⚪无销量", "⚪ 近30天无销量" in _trad_note(0, 0, 0, 0, 0, 196, 0, 0))
check("备注: 需求低场景由动态安全线提示接管", "库存低于安全线，但需求低暂不补" not in _trad_note(0.02, 0, 0, 0.1, 2, 196, 0, 0))
check("备注: 正常补货 → 濒临前置+需补量", _trad_note(5, 5.1, 4.5, 4, 2, 196, 216, 209).startswith("🔴 已濒临") and "需补216件" in _trad_note(5, 5.1, 4.5, 4, 2, 196, 216, 209))
check("备注: 正常不补+库存充足 → 仅趋势", _trad_note(5, 5.1, 4.5, 4, 500, 196, 0, 0) == "近7➡️ 近14➡️")
# 周转红线(对齐 bbcc 综转 90 天)
check("备注: 补后综转超90天 → 🔴红线前置", "🔴 补后综转120天超90天" in _trad_note(1, 1.1, 1, 1, 30, 196, 96, 90, after_turnover=120, tw90=90))
check("备注: 当前综转接近90天 → ⚠️", "⚠️ 当前综转80天接近90天" in _trad_note(1, 1.1, 1, 1, 30, 196, 0, 0, after_turnover=80, tw90=90))
check("备注: 无销量不显示周转(无意义)", "综转" not in _trad_note(0, 0, 0, 0, 100, 196, 0, 0, after_turnover=999, tw90=90))
# 动态安全线提示(2026-10-07: 只提示不参与计算)
_h = "🔴 库存低于动态安全线：可撑3天<周期6天，存在断货风险，建议尽快补货"
check("备注: 动态安全线提示接入", _h in _trad_note(5, 5.1, 4.5, 4, 500, 196, 0, 0, safety_hint=_h))
_h2 = "⚠️ 接近动态安全线：约剩5天（周期6天），建议关注补货"
check("备注: 接近提示接入", _h2 in _trad_note(5, 5.1, 4.5, 4, 500, 196, 0, 0, safety_hint=_h2))

# ── 配置键引用完整性审计(2026-10-07: mode_traditional_safety_multiplier 配了但代码没用——静态安全线遗留) ──
# 扫描 replenishment.py 中 _config 读取的键名, 确保每个 cfg.get 键都有配置写入方(seed/清洗写入的键集)
_REPL_SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes", "replenishment.py"),
                 encoding="utf-8").read()
# seed_fill 写入的配置键(常量字符串) + replenishment.py cfg.get 键 → 交叉核对
_cfg_keys_in_code = set(_re.findall(r'cfg\.get\("([a-z_0-9]+)"', _REPL_SRC))
# replenishment_config 写入方: seed_fill/清洗/config 保存接口涉及的键
_cfg_written = {"b_to_c_days", "c_safety_days", "lead_time_days", "safety_multiplier",
                "ship_to_b_days", "b_free_days", "turnover_warning_90", "turnover_warning_15",
                "max_turnover_days", "moq", "purchase_lead_days", "purchase_safety_days",
                "target_turnover", "active_factor"}
_missing_cfg = _cfg_keys_in_code - _cfg_written
check("审计: replenishment cfg.get 键均有写入方(防配置静默失效)", not _missing_cfg,
      "缺写入方: %s" % ",".join(sorted(_missing_cfg)))

# ── 规则条件字段校验(2026-10-08: 防静默 0 恒真/恒假) ──
from core.rules import validate_condition
check("字段校验: 有效条件通过", validate_condition({"left": "inv.available_qty", "op": "<", "right": "inv.safety_qty"}) == [])
check("字段校验: 拼写错误拦截", len(validate_condition({"left": "inv.availabl_qty", "op": "<", "right": "inv.safety_qty"})) > 0)
check("字段校验: max 内字段校验", validate_condition({"left": "inv.available_qty", "op": "<", "right": "max(100, order.quantity)"}) == [])
check("字段校验: params 通配", validate_condition({"left": "params.lit", "op": "<", "right": "10"}) == [])

print("\n本地回归: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
