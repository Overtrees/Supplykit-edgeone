"""本地回归测试: mock db + TestClient 跑全部路由(部署前置定位 Python 层 bug)

用法: cd cloud-functions/api && python3 local_test.py
覆盖: auth / dashboard summary(含 periods 环比/health_index/漏斗)
"""
import sys
import os

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
    if "warehouse_type IN ('platform','platform_b') GROUP BY sku" in sql:
        return [{"healthy": 65, "warning": 10, "out_of_stock": 2, "total": 77}]
    if "GROUP BY warehouse_type" in sql:
        return list(_INV)
    if "FROM sync_tasks" in sql:
        return [{"task_id": "seed_1", "task_type": "seed", "status": "done", "result": "{}",
                 "params": "{}", "channel": "jd", "created_at": "2026-09-05 10:00:00"}]
    return []


def fake_one(sql, params=None):
    if "FROM users WHERE" in sql:
        from routes.common import hash_password as _hp
        uname = params[0] if params else "admin"
        return {"username": uname, "password_hash": _hp("admin123"), "role": "admin"}
    if "SELECT status FROM sync_tasks" in sql:
        if params and params[0] == "none":
            return None
        return {"status": "done"}
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

print("\n本地回归: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
