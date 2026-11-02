"""本地回归测试: mock db + TestClient 跑全部路由(部署前置定位 Python 层 bug)

用法: cd cloud-functions/api && python3 local_test.py
覆盖: auth / dashboard summary(含 periods 环比/health_index/漏斗)
"""
import sys
import os
import json
import io
import urllib.parse

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
    # 2026-11-02 历史归档聚合(_H 的 summary/top): 真实聚合在 TiDB 侧, 这里只回形状
    if "SUM(gmv) AS gmv, SUM(cnt) AS cnt, SUM(qty) AS qty" in sql:
        if "2023-01-01" in str(params or ""):
            return []   # 模拟"这段区间两表都没有数据"(归档未生成 + 订单更早于保留期)
        if " AS k" in sql:
            return [{"k": "2026-10-01", "gmv": 100.0, "cnt": 2, "qty": 4, "total": 250.0},
                    {"k": "2026-10-02", "gmv": 150.0, "cnt": 3, "qty": 6, "total": 250.0}]
        return [{"gmv": 250.0, "cnt": 5, "qty": 10}]
    # health_index 查询(须在 FROM inventory WHERE 分支前, 避免被 _FAKE_INV 抢占致 total=0)
    if "GROUP BY warehouse_type" in sql:
        return list(_INV)
    if "warehouse_type IN ('platform','platform_b') GROUP BY sku" in sql:
        return [{"healthy": 65, "warning": 10, "out_of_stock": 2, "total": 77}]
    if "FROM orders o WHERE" in sql and "COUNT" not in sql:
        # 2026-10-25 品牌子查询(barcode 优先/sku 兜底)——mock 补 brand 字段
        _r = {"id": 1, "order_no": "NO0000000001", "sku": "SKU0001", "barcode": "69-01",
              "product_name": "禾味调味料1号", "brand": "禾味", "store": "自营旗舰店", "warehouse": "华东C仓",
              "quantity": 2, "unit_price": 25.0, "total_amount": 50.0, "order_status": "已完成",
              "ordered_at": "2026-09-04 10:00:00", "paid_at": "2026-09-04 10:01:00",
              "platform": "jd", "channel": "jd", "deleted_at": ""}
        return [_r]
    if "FROM orders o LEFT JOIN products" in sql and "COUNT" not in sql:
        # 2026-10-25 品牌列: orders JOIN products 取 brand(mock 补 brand 字段)
        _r = {"id": 1, "order_no": "NO0000000001", "sku": "SKU0001", "barcode": "69-01",
              "product_name": "禾味调味料1号", "brand": "禾味", "store": "自营旗舰店", "warehouse": "华东C仓",
              "quantity": 2, "unit_price": 25.0, "total_amount": 50.0, "order_status": "已完成",
              "ordered_at": "2026-09-04 10:00:00", "paid_at": "2026-09-04 10:01:00",
              "platform": "jd", "channel": "jd", "deleted_at": ""}
        return [_r]
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
    # 历史归档同比窗口: 只有沾到订单表的窗口才有数据(归档段=去年, 无数据)
    if "SUM(gmv) AS gmv, SUM(cnt) AS cnt, SUM(qty) AS qty" in sql:
        return ({"gmv": 250.0, "cnt": 5, "qty": 10} if "FROM orders" in sql
                else {"gmv": 0, "cnt": 0, "qty": 0})
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
        return {"status": "done", "result": '{"result": {"target": "order", "success": 1, "failed": 0, "filename": "exp_test.csv", "parts": ["p0.csv"]}}'}
    if "FROM export_files" in sql:
        return {"content": "SKU,商品名\nSKU0001,禾味调味料1号\n"}
    if "GROUP BY DATE(ordered_at)" in sql or "GROUP BY warehouse_type" in sql or "warehouse_type IN" in sql:
        return fake_query(sql, params)[0] if fake_query(sql, params) else {"healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0}
    if "FROM daily_stats" in sql and "COUNT(*)" in sql:
        # 归档为空(线上现状: 订单最早 2026-09-03, 365 天保留期) —— 走"暂无归档"提示分支
        return {"c": 0, "mn": None, "mx": None, "skus": 0}
    if "COUNT(*)" in sql:
        return {"c": 3}
    if "SELECT 1" in sql:
        return {"ok": 1}
    return None


# 假 conn(transaction 不真连库——沙箱 TiDB 连接超时)
class _FakeConn:
    def __init__(self):
        self._ac = True
    def autocommit(self, v):
        self._ac = v
    def commit(self):
        pass
    def rollback(self):
        pass
_db.conn = lambda: _FakeConn()
_db._acquire = lambda: _FakeConn()  # 连接池取用 mock(transaction 用 _acquire——沙箱不真连库)
_db._release = lambda c: None

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

r = client.get("/auth/check", headers={"X-Auth-Token": TOKEN})
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
r = client.get("/dashboard/summary?channel=jd", headers={"X-Auth-Token": TOKEN})
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
               headers={"X-Auth-Token": TOKEN})
check("自定义日期 200", r.status_code == 200 and r.json().get("ok") is not False, r.text[:120])

# replenishment
r = client.get("/insights/replenishment?channel=jd&mode=bbcc", headers={"X-Auth-Token": TOKEN})
d = r.json()
if d.get("ok") is False:
    check("replenish 无错误", False, (d.get("detail") or "")[:200])
else:
    items = d.get("data") or []
    check("replenish 返回列表", isinstance(items, list), str(d)[:150])
    check("replenish SKU0001 充足无建议", not any(i.get("sku") == "SKU0001" and (i.get("suggested_qty") or 0) > 0 for i in items), str(items)[:300])
    check("replenish SKU0002 有建议(缺货)", any(i.get("sku") == "SKU0002" for i in items))
r = client.get("/insights/replenishment?channel=jd&mode=traditional", headers={"X-Auth-Token": TOKEN})
check("replenish traditional 200", r.status_code == 200, r.text[:120])

# orders / products / insights
r = client.get("/orders?page=1&page_size=5", headers={"X-Auth-Token": TOKEN})
d = r.json()
check("orders 返回分页", d.get("ok") and len(d.get("data", {}).get("items", [])) >= 1, r.text[:150])
r = client.get("/products?page=1&page_size=5", headers={"X-Auth-Token": TOKEN})
check("products 200", r.status_code == 200 and r.json().get("ok"), r.text[:120])
r = client.get("/insights/slow-moving?channel=jd", headers={"X-Auth-Token": TOKEN})
d = r.json()
check("slow-moving 200", d.get("ok") is not False, (d.get("detail") or "")[:150])
r = client.get("/insights/with-sales?wh_type=own&channel=jd", headers={"X-Auth-Token": TOKEN})
check("with-sales 200", r.json().get("ok") is not False, r.text[:150])

# ── 契约补齐组 A: ping/删除恢复/批量/缺货/批次/配置 ──
r = client.get("/insights/ping")
check("ping 免鉴权 {ok:true}", r.status_code == 200 and r.json().get("ok") is True, r.text[:120])
AH = {"X-Auth-Token": ADMIN}
r = client.delete("/orders/1", headers={"X-Auth-Token": TOKEN})
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
                params={"q_mp": MP, "q_target": "order", "q_channel": "jd"})
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
# 2026-10-16 分离: 导入 done 应自动创建评估任务 + 续跑可推进
_ev = client.get("/cleansing/eval-status?task_id=clean_1_eval", headers=AH)
check("cleansing eval 任务存在", _ev.status_code == 200, _ev.text[:120])
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

# ── 2026-10-29 缺失字段 None 语义(防"缺失当0"误报) ──
check("缺失字段不误报(days_to_expire)", not _check_condition(
      {"left": "batch.days_to_expire", "op": "<", "right": "30"}, {"sku": "S1", "inv": {}}),
      "missing field should not trigger")
check("缺失字段表达式返回 None", _resolve_value("inv.aa_x + inv.bb_y", {"inv": {}}) is None,
      "expr with missing should be None")
check("缺失字段乘法返回 None", _resolve_value("batch.days_to_expire * 2", {"sku": "S1"}) is None,
      "muldiv with missing should be None")
check("字段存在时正常判定", _check_condition(
      {"left": "batch.days_to_expire", "op": "<", "right": "30"},
      {"sku": "S1", "batch": {"days_to_expire": 10}}), "present field should trigger")
check("params 缺失不误判", not _check_condition(
      {"left": "inv.adj_dos", "op": "<=", "right": "params.lit"},
      {"inv": {"adj_dos": 5}, "params": {}}), "missing params should not trigger")
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
    ("stock_risk|", ["_CV", "channel", "full"]),   # 2026-11-02 S2-1: 键族归首, 版本号进第二段
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
import routes.purchase_calc as _rpc
_old_pq = _rp.query
_old_pcq = _rpc.query
_rp.query = _q3  # _build_purchase 内部 query 为模块名字绑定, 需直改路由模块(拆分后函数在 purchase_calc)
_rpc.query = _q3
from routes.purchase import _build_purchase as _bp
_moq = _bp("jd", "bbcc", 28)
_supA = [r for r in _moq if (r.get("supplier_code") or "") == "SUP-A"]
check("MOQ: 同供应商 2 SKU 均进入建议", len(_supA) == 2, "got %d" % len(_supA))
_tot = sum((r.get("purchase_qty") or 0) for r in _supA)
check("MOQ: 合计 84 < 起订 100 放大到 ≥100", _tot >= 100, "total=%d" % _tot)
_has_note = any("起订" in (r.get("note") or "") for r in _supA)
check("MOQ: note 含起订说明", _has_note)
_rp.query = _old_pq
_rpc.query = _old_pcq
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

# ── 日销 digest 共享缓存(2026-10-08 性能优化: 准确性=与直算数学等价 / 完整性=全量 / 可靠性=幂等) ──
from biz.sales import get_sales_digest, load_daily_sales_grouped, calc_sales_multi, rolling_predict
_d0 = get_sales_digest("jd")
_d1 = get_sales_digest("jd")
check("digest: 缓存命中幂等(同一对象)", _d0 is _d1)
# 等价性: 缓存结果 vs 直接计算(同一 mock 数据下)
_b0, _f0, _s0 = _d0
_bd, _, _ = load_daily_sales_grouped(28, "jd")
_md = calc_sales_multi(_bd, windows=[7, 14, 28])
_fd = {s: rolling_predict(_md[7].get(s, 0), _md[14].get(s, 0), _md[28].get(s, 0)) for s in _bd}
_sd = {}
for _s, _d in _bd.items():
    if len(_d) >= 7:
        _vl = list(_d.values())
        _mm = sum(_vl) / len(_vl)
        _vv = sum((x - _mm) ** 2 for x in _vl) / len(_vl)
        _sd[_s] = _vv ** 0.5
check("digest: fused 与直算等价", set(_f0.keys()) == set(_fd.keys()) and all(abs(_f0[k] - _fd[k]) < 1e-9 for k in _f0))
check("digest: sigma 与直算等价", set(_s0.keys()) == set(_sd.keys()) and all(abs(_s0[k] - _sd[k]) < 1e-9 for k in _s0))
check("digest: by_sku 全量(完整性)", len(_b0) == len(_bd))

# ── agg 路径回归(2026-10-09: _build_summary_agg 缺 mode 曾线上 NameError——local_test 未覆盖 agg 路径) ──
def fake_agg_query(sql, params=None):
    if "FROM orders_day_agg" in sql:
        return [{"d": _NOW.strftime("%Y-%m-%d"), "order_status": "已完成", "store": "自营",
                 "g": 200.0, "sub": 10.0, "cnt": 4}]
    if "GROUP BY DATE(ordered_at)" in sql:
        return [{"d": _NOW.strftime("%Y-%m-%d"), "order_status": "已完成", "store": "自营",
                 "g": 100.0, "sub": 5.0, "cnt": 2}]
    return fake_query(sql, params)

_db.query = fake_agg_query
try:
    from routes.dashboard import dashboard_summary
    _ar = dashboard_summary(channel="jd", mode="bbcc")
    check("agg 路径: summary 无 NameError(mode 传参)", _ar.get("ok") is True, str(_ar.get("error") or "")[:120])
    _asum = (_ar.get("data") or {}).get("summary") or {}
    check("agg 路径: 数据聚合(总数≥agg+当天)", int(_asum.get("total_orders") or 0) >= 6, str(_asum.get("total_orders")))
    check("agg 路径: health_index 生成", ((_ar.get("data") or {}).get("health_index") or {}).get("score") is not None)
except Exception as _e:
    check("agg 路径: summary 调用无异常", False, str(_e)[:200])
# 假 conn(transaction 不真连库——沙箱 TiDB 连接超时; 类定义在文件前部, 此处只重绑)
_db.conn = lambda: _FakeConn()
_db._acquire = lambda: _FakeConn()  # 连接池取用 mock(transaction 用 _acquire——沙箱不真连库)
_db._release = lambda c: None

_db.query = fake_query

# ── 接力 status 双层 result 解析回归(2026-10-09: filename 曾取错层级致下载失败) ──
from routes.tasks import export_status
_r2 = export_status(task_id="none")
check("export status: 任务不存在返回", _r2.get("ok") is False or _r2.get("error") is not None, str(_r2))
_r3 = export_status(task_id="t")
check("export status: done 双层解析 filename", _r3.get("status") == "done" and _r3.get("filename"), str(_r3))

# ── stock-risk 回归(2026-10-11 P2-1: 无锁缓存/冷算稳定性——mock 下结构正确) ──
try:
    from routes.dashboard import _stock_risk, stock_risk
    _sr = _stock_risk("jd")
    check("stock-risk: 计算无异常", isinstance(_sr, dict), str(_sr)[:100])
    check("stock-risk: bcItems 列表", isinstance(_sr.get("bcItems"), list) and isinstance(_sr.get("cItems"), list)
          and isinstance(_sr.get("ownItems"), list), str(_sr.get("bcItems"))[:60])
    _sr2 = _stock_risk("jd", full=1)
    check("stock-risk: full 模式同结构", isinstance(_sr2.get("bcItems"), list), "")
    check("stock-risk: 计数字段存在", "bcTotal" in _sr and "bcCritical" in _sr and "bcWarning" in _sr, "")
except Exception as _e:
    check("stock-risk: 调用无异常", False, str(_e)[:200])

# 2026-10-16 GMV 回款(gmv_flow): 写入分支触发 + 非 fall-through(0, len)
try:
    _ins_count[0] = 0
    _gf = io.BytesIO(("platform,shop,flow_no,arrived_time,amount,flow_type\npdd,x,FLOW-1,2026-09-10,100,交易收入\npdd,x,FLOW-2,2026-09-11,50,交易收入").encode("utf-8"))
    _gf.name = "f.csv"
    _gm = client.post("/cleansing/execute-async?q_target=gmv_flow&q_channel=jd&q_conflict=sum&q_mp=" + urllib.parse.quote('{"platform":{"target":"platform"},"shop":{"target":"shop"},"flow_no":{"target":"flow_no"},"arrived_time":{"target":"arrived_time"},"amount":{"target":"amount"},"flow_type":{"target":"flow_type"}}'),
                      files={"file": ("f.csv", _gf.getvalue(), "text/csv")}, headers=AH)
    _gd = _gm.json()
    check("gmv_flow execute ok", _gd.get("ok") is True, str(_gd)[:120])
    check("gmv_flow target 传递", _gd.get("target") == "gmv_flow", "target=%s resp=%s" % (_gd.get("target"), str(_gd)[:140]))
except Exception as _e:
    check("gmv_flow 用例无异常", False, str(_e)[:200])

# ── 2026-10-17 导入同键多行聚合(订单三键求和/商品覆盖末行——文件内聚合, 防撞唯一键 1062) ──
try:
    from routes.cleanse_write import _write_batch as _wb, executemany as _cs_em
    _WROTE = []
    from routes import cleanse_write as _cs
    _orig_em = _cs.executemany
    _cs.executemany = lambda sql, seq: _WROTE.extend(list(seq))
    # 订单: 同单同SKU同69码两行(2+3) → 求和一条 quantity=5
    _r1 = _wb("orders", ["order_no","sku","barcode","quantity","total_amount","product_name"],
              [{"order_no":"T1","sku":"S1","barcode":"69-1","quantity":2,"total_amount":10,"product_name":"p"},
               {"order_no":"T1","sku":"S1","barcode":"69-1","quantity":3,"total_amount":15,"product_name":"p"}],
              "sum", "order_no", "sku", agg_key3="barcode", agg_sum=["quantity","total_amount"])
    # 列并集后顺序: order_no,sku,barcode,quantity,total_amount,product_name → quantity 在 index3
    check("同键聚合: 订单三键求和(2+3=5)", _r1[0] == 1 and _r1[1] == 0 and _WROTE and _WROTE[0][3] == 5, str(_WROTE[:1]))
    # 商品: 同SKU两行 → 覆盖末行(档案类走 agg_overwrite 分支)
    _WROTE.clear()
    _r2 = _wb("products", ["sku","product_name","price"],
              [{"sku":"P1","product_name":"旧","price":10.0},{"sku":"P1","product_name":"新","price":20.0}],
              "sum", "sku", agg_overwrite=True)
    check("同键聚合: 商品覆盖末行", _r2[0] == 1 and _WROTE and _WROTE[0][1] == "新", str(_WROTE[:1]))
    _cs.executemany = _orig_em
except Exception as _e:
    check("同键聚合用例无异常", False, str(_e)[:200])

# ── 2026-10-29 回归用例: 口径单一真源 + 静默失败类(线上坑沉淀) ────────────
try:
    import io as _io, os as _os
    _HERE = _os.path.dirname(_os.path.abspath(__file__))
    def _rf(_p):
        return _io.open(_os.path.join(_HERE, _p), encoding="utf-8").read()
    from routes.common import SALES_SQL as _SQL1, SALES_STATUSES as _ST1
    check("口径: 销量池常量唯一(已完成)", _ST1 == ("已完成",) and _SQL1 == "'已完成'", str(_SQL1))
    _dm = _rf("routes/dashboard_maintain.py")
    check("口径: 快照续段不再硬编码 3 状态",
          "'待发货','已发货','已完成'" not in _dm and "_SALES_SQL" in _dm, "dashboard_maintain.py")
    _ix = _rf("index.py")
    check("health: 版本指纹不再引用 config.id",
          "MAX(id),0) FROM replenishment_config" not in _ix and "MAX(updated_at)" in _ix, "index.py")
    _mi = _rf("routes/misc.py")
    check("回填: 快照幂等 upsert(不再 1062)",
          "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count)" in _mi and "_SALES_SQL" in _mi, "misc.py")
    check("迁移: 主键走影子表原子切换",
          "_migrate_pk" in _mi and "RENAME TABLE" in _mi.split("def _migrate_pk")[1][:3000], "misc.py")
    _cr = _rf("routes/cron.py")
    check("对账: 日销断言端点存在且与快照同口径",
          "def run_reconcile" in _cr and "_PAID_SQL" in _cr.split("def run_reconcile")[1][:2000], "cron.py")
    check("对账: 应用层每日兜底已挂",
          "run_reconcile" in _dm and "maintenance_log" in _dm, "dashboard_maintain.py")
    # 2026-11-02 S2-3: 对账必须含 GMV 段(只比行数/件数的话, 金额错了发现不了)
    _rc_src = _cr.split("def run_reconcile")[1] if "def run_reconcile" in _cr else ""
    check("对账: 含 GMV 段(物化金额 vs 源表同口径)",
          "def _reconcile_gmv" in _cr and "_reconcile_gmv(_ch" in _rc_src
          and "total_amount - COALESCE(discount_amount,0)" in _cr,
          "cron.py")
    check("对账: GMV 段晚一天收口(躲开未自愈的软删残留)",
          "_end_gmv" in _rc_src and "_reconcile_gmv(_ch, _start, _end_gmv)" in _rc_src, "cron.py")
    _sc = _rf("schema_create.py")
    check("根治: 日销两表用代理主键 + 业务唯一键",
          "id BIGINT AUTO_INCREMENT PRIMARY KEY" in _sc
          and "uq_snapshot_biz" in _sc and "uq_stats_biz" in _sc
          and "PRIMARY KEY(date, channel, sku, warehouse)" not in _sc, "schema_create.py")
    check("根治: 启动期不再执行必然失败的 DROP PRIMARY KEY",
          "DROP PRIMARY KEY, ADD PRIMARY KEY %s" not in _sc, "schema_create.py")
    check("根治: 代理主键迁移器就绪(影子表 + 原子切换)",
          "def _migrate_surrogate" in _mi and "SURROGATE_CONTRACT" in _mi, "misc.py")
    _cl = _rf("routes/cleansing.py")
    check("联动: 出入库先重算再评估(ctx 用新库存)",
          0 < _cl.find("adjusted = _rc0(") < _cl.find("evaluate_stock_skus"),
          "cleansing.py")
    check("联动: 出入库追加库存变更评估(复用 evaluate_stock_skus, 含动态安全线)",
          "evaluate_stock_skus" in _cl and "skus=list(_batch)" in _cl, "cleansing.py")
    _ac = _rf("routes/analysis_cache.py")
    check("版本: 写操作中央失效点递增 data_version",
          "data_version" in _ac and "v=v+1" in _ac, "analysis_cache.py")
    check("版本: health 读计数版(覆盖 UPDATE 类变更)",
          "data_version" in _ix and "_dvz" in _ix, "index.py")
    _re = _rf("core/rule_eval.py")
    check("低库存: bbcc 按 BC 合计(SKU×码 一条)",
          "warehouse_type: \"bc\"" in _re or '"warehouse_type": "bc"' in _re, "rule_eval.py")
    check("低库存: 关闭判定与 hits 同为含码 5 元组",
          'str(a.get("bc") or "")) not in hits' in _re, "rule_eval.py")
    _ru = _rf("core/rules.py")
    check("告警: 去重/写入均带 barcode", "COALESCE(barcode,'') AS bc" in _ru and "channel, barcode)" in _ru, "rules.py")
    check("采购: 可售不再重复计 own", "_avl_all = sys_total" in _rf("routes/purchase_calc.py"), "purchase_calc.py")
    _rp = _rf("../../frontend/src/pages/RulesPage.tsx")
    check("规则页: 字段路径与 ctx 一致(无 inv.days_since_last/inv.stock)",
          "inv.days_since_last" not in _rp and "inv.stock" not in _rp, "RulesPage.tsx")
    check("规则页: 仓储选项含 BC 合计", "BC合计(BBCC)" in _rp, "RulesPage.tsx")
    check("规则 ctx: locked_qty / daily_sales 已注入",
          "locked_qty\": int(r.get(\"locked_qty\")" in _re.replace("'", '"')
          or '"locked_qty": int(r.get("locked_qty") or 0)' in _re, "rule_eval.py")
    check("规则 ctx: order.total_amount/unit_price 已注入",
          '"total_amount": round(_g[\"amt\"]' in _cl or '"total_amount": round(_g["amt"], 2)' in _cl, "cleansing.py")
    check("规则字段存量迁移已登记", "rules_field_path_v1" in _sc, "schema_create.py")
    check("导入: 仓型映射单一真源(平台库存/B仓导入不再 NameError)",
          "TARGET_WH =" in _rf("routes/common.py")
          and "TARGET_WH as _TARGET_WH" in _rf("routes/cleanse_write.py"), "cleanse_write.py")
except Exception as _e2:
    check("口径/静默失败用例无异常", False, str(_e2)[:200])

# ── 批次扣减分配(2026-11-01: 期初/无批次/多仓) ──────────────────────
try:
    from routes.cleanse_write import _allocate_to_batches as _alloc
    _p = [{"quantity": 100, "prod_date": "", "exp_date": "", "_opening": True}]
    _ov, _he, _hf, _mk = _alloc(_p, {}, 30)
    check("批次: 期初被无批次出库扣减", _p[0]["quantity"] == 70 and _hf == 30 and _ov == 0, str(_p))
    _p = [{"quantity": 100, "prod_date": "", "exp_date": "", "_opening": True}]
    _ov, _he, _hf, _mk = _alloc(_p, {("2026-08-01", "2026-11-01"): 30}, 0)
    check("批次: 批次未命中降级扣期初+记录",
          _p[0]["quantity"] == 70 and _hf == 30 and _mk == [("2026-08-01", "2026-11-01")], (_p, _mk))
    _p = [{"quantity": 100, "prod_date": "", "exp_date": "", "_opening": True},
          {"quantity": 100, "prod_date": "2026-08-01", "exp_date": "2026-11-01"}]
    _ov, _he, _hf, _mk = _alloc(_p, {("2026-08-01", "2026-11-01"): 40}, 0)
    check("批次: 精确命中不动期初", _p[0]["quantity"] == 100 and _p[1]["quantity"] == 60 and _he == 40, _p)
    _p = [{"quantity": 50, "prod_date": "", "exp_date": "", "_opening": True}]
    _ov, _he, _hf, _mk = _alloc(_p, {}, 80)
    check("批次: 超卖返回超出量且扣至0", _p[0]["quantity"] == 0 and _ov == 30, (_p, _ov))
    _p = [{"quantity": 10, "prod_date": "2026-09-01", "exp_date": "2027-03-01"},
          {"quantity": 10, "prod_date": "2026-08-01", "exp_date": "2026-12-01"}]
    _alloc(_p, {}, 10)
    check("批次: 降级按 FEFO 先扣早效期", _p[1]["quantity"] == 0 and _p[0]["quantity"] == 10, _p)
    _pa = [{"quantity": 100, "prod_date": "2026-08-01", "exp_date": "2026-11-01"}]
    _pb = [{"quantity": 100, "prod_date": "2026-08-01", "exp_date": "2026-11-01"}]
    _alloc(_pa, {("2026-08-01", "2026-11-01"): 20}, 0)
    _alloc(_pb, {("2026-08-01", "2026-11-01"): 30}, 0)
    check("批次: 多仓同批号各扣各的", _pa[0]["quantity"] == 80 and _pb[0]["quantity"] == 70, (_pa, _pb))

    # 2026-11-01 不做效期管理(expiry_managed=no): 按到货顺序 FIFO, 不看效期
    #   池顺序 = 到货顺序; 故意让"先到的"效期更晚 → FEFO 会扣第二条, FIFO 必须扣第一条
    _p = [{"quantity": 10, "prod_date": "2026-08-01", "exp_date": "2027-06-01"},
          {"quantity": 10, "prod_date": "2026-09-01", "exp_date": "2026-12-01"}]
    _ov, _he, _hf, _mk = _alloc(_p, {}, 10, mode="fifo")
    check("批次: 不做效期按到货序 FIFO(不按效期)",
          _p[0]["quantity"] == 0 and _p[1]["quantity"] == 10, _p)
    # 不做效期时即使出库填了批次, 也忽略批次(不产生未命中告警)
    _p = [{"quantity": 10, "prod_date": "2026-08-01", "exp_date": "2027-06-01"}]
    _ov, _he, _hf, _mk = _alloc(_p, {("2026-01-01", "2026-02-01"): 8}, 0, mode="fifo")
    check("批次: 不做效期忽略批次日期且无未命中",
          _p[0]["quantity"] == 2 and _he == 0 and _mk == [] and _hf == 8, (_p, _he, _mk, _hf))

    # 是否做效期管理取值归一(是/否 → yes/no; 无法识别 → ?)
    from routes.cleanse_write import _norm_expiry as _ne
    check("效期标记: 是/否归一",
          _ne("是") == "yes" and _ne("否") == "no" and _ne("Y") == "yes" and _ne("0") == "no"
          and _ne("") == "" and _ne("待定") == "?", (_ne("是"), _ne("否"), _ne("待定")))
except Exception as _e3:
    check("批次分配用例无异常", False, str(_e3)[:200])

# ── 规则字段/口径一致性(2026-11-01 全面梳理——防"可选但静默失效") ──────────
try:
    from core.rules import _CALC_VARS, _VALID_FIELDS

    # 1. 白名单里不得有 ctx 永不存在的字段(可选可保存但条件恒 None → 规则静默失效)
    check("规则字段: 白名单无 inv.days_since_last(实为顶层 days_since_last)", 
          "inv.days_since_last" not in _VALID_FIELDS, "core/rules.py _VALID_FIELDS")

    # 2. 计算变量白名单必须覆盖 own.* 与 batch.*
    #    —— 缺 own. → 自有仓规则走快速路径, own 键不注入 → 恒不触发;
    #    —— 缺 batch. → 纯 batch 条件的效期规则同样静默失效(历史上只因同时引用 params. 才侥幸生效)
    check("规则字段: _CALC_VARS 含 own.(自有仓供给侧)", "own." in _CALC_VARS, _CALC_VARS)
    check("规则字段: _CALC_VARS 含 batch.(效期)", "batch." in _CALC_VARS, _CALC_VARS)

    # 3. own.* 字段必须同时在白名单(否则规则页保存被 validate_condition 拦下)
    _own_need = {"own.available", "own.transit", "own.po_transit", "own.can_ship",
                 "own.downstream_ds", "own.downstream_avail", "own.b2c_days",
                 "own.need", "own.gap", "own.cover_days"}
    check("规则字段: own.* 全部在 _VALID_FIELDS", _own_need <= _VALID_FIELDS,
          sorted(_own_need - _VALID_FIELDS))

    # 4. 超卖口径必须与 close_oversell 同源: 都是 SUM(available_qty) 跨仓合计
    #    曾用 MAX → 多仓分散库存被当成单仓库存 → 超卖低估
    _ord = open("routes/orders.py", encoding="utf-8").read()
    check("超卖口径: 事件 ctx 用 SUM(available_qty) 而非 MAX",
          "SUM(available_qty) AS avail FROM inventory" in _ord.replace("\n", " ")
          or "SELECT sku, SUM(available_qty) AS avail" in _ord,
          "routes/orders.py 超卖库存口径")

    # 5. order ctx 的金额/批量字段必须注入(规则页可选)
    _re2 = open("core/rule_eval.py", encoding="utf-8").read()
    check("规则 ctx: order 金额/批量字段已注入",
          all(k in _re2 for k in ("total_amount", "max_quantity", "min_quantity",
                                  "line_count", "unit_price")), "core/rule_eval.py order ctx")

    # 6. 自有仓规则条件必须带 warehouse_type=='own' 前置(own.* 键只对 own 行注入)
    #    注: 本文件里 _db.query 已被 fake_query 替换(见上), 查真实库拿不到规则 →
    #    改为**静态校验 seed 源码**: 三条内置规则的定义必须都带该前置。
    _seed = open("routes/seed_fill.py", encoding="utf-8").read()
    _own_ats = ("own_transfer_gap", "own_overstock", "own_expiry_risk")
    _miss_pre = []
    for _at in _own_ats:
        _i = _seed.find('"%s"' % _at)
        if _i < 0:
            _miss_pre.append(_at + ":缺失")
            continue
        # 规则定义块(alert_type 字面量往前 300 字符内应能找到条件串)
        _blk = _seed[max(0, _i - 400):_i]
        if "inv.warehouse_type" not in _blk:
            _miss_pre.append(_at + ":无条件前置")
    check("自有仓规则: seed 三条均带 warehouse_type 前置", not _miss_pre, _miss_pre)

    # 7. 规则测试接口必须给 own./batch. 注入 ctx(2026-11-01)
    #    原测试 ctx 只有 inv/order/health → own.* 取不到键 → 条件判 False →
    #    自有仓三级规则在测试弹窗里**永远显示未触发**, 用户会误判规则写错。
    _rr = open("routes/rules.py", encoding="utf-8").read()
    check("测试接口: own./batch. 模拟 ctx 已注入",
          'ctx["own"]' in _rr and 'ctx["batch"]' in _rr, "routes/rules.py test_rule")

    # 8. 规则保存链路不得丢掉 and 子条件
    #    内置自有仓规则是 `warehouse_type==own and {own.gap>N}` 结构, 前端原只序列化 or →
    #    用户一编辑保存 and 就被静默删除, 语义永久变更。
    _rp = open("../../frontend/src/pages/RulesPage.tsx", encoding="utf-8").read()
    check("规则保存: 前端保留 and 子条件",
          "and: cond.and" in _rp and "and: andSub" in _rp, "RulesPage.tsx")
    # 9. 规则页必须给自有仓三条暴露可编辑阈值(否则只能在测试弹窗临时覆盖)
    check("规则页: 自有仓阈值可视化可配",
          all(k in _rp for k in ("own_overstock_days", "own_expiry_buffer_days",
                                 "transfer_gap_min")), "RulesPage.tsx 参数区")
    # 10. 测试结果弹窗必须逐条回显 and/or 子条件命中情况
    check("测试结果: 嵌套子条件逐条回显",
          'detail["and"]' in _rr and "testResult.detail.and" in _rp,
          "routes/rules.py + RulesPage.tsx")
except Exception as _e4:
    check("规则字段/口径用例无异常", False, str(_e4)[:200])

# ── 11. restore 联动的 channel 解析(2026-11-02) ─────────────────────────
#     _evaluate_restored(ids) 原函数体直接引用未定义的 channel → NameError 被自身
#     except 吞成"restore 联动评估降级"留痕 → 恢复订单的规则重评估从未真正跑过。
#     这里断言: 不传 channel 时按订单实际渠道查库解析, 并把 channel 透传给 evaluate_many。
try:
    import routes.orders as _ro
    _seen = {}

    def _fq(sql, params=None):
        if "DISTINCT channel" in sql:
            return [{"channel": "jd"}]
        if "SUM(quantity)" in sql:
            return [{"sku": "SKU0001", "q": 2}]
        if "FROM inventory" in sql:
            return [{"sku": "SKU0001", "avail": 10}]
        if "MAX(quantity)" in sql:
            return [{"sku": "SKU0001", "q": 2, "amt": 50, "mx": 2, "mn": 2, "lc": 1, "up": 25}]
        return []

    import core.rules as _cr
    _old_q, _old_em, _old_lr = _ro.query, _cr.evaluate_many, _cr.load_rules_for
    _ro.query = _fq
    _cr.evaluate_many = lambda ev, ctxs, ch, rules: _seen.update({"ev": ev, "ch": ch, "n": len(ctxs)})
    _cr.load_rules_for = lambda ev, ch: []
    try:
        _ro._evaluate_restored([1, 2])
    finally:
        _ro.query, _cr.evaluate_many, _cr.load_rules_for = _old_q, _old_em, _old_lr
    check("restore 联动: 按订单 channel 解析并重评估",
          _seen.get("ch") == "jd" and _seen.get("ev") == "order.created" and _seen.get("n") == 1,
          str(_seen))
except Exception as _e5:
    check("restore 联动用例无异常", False, str(_e5)[:200])

# ── cron 调度护栏(2026-11-02 S0): 无法识别调用方, 改守执行次数 ──
try:
    from routes.cron import _throttle, _claim, _unclaim, _RUN_AT
    _RUN_AT.clear()
    check("cron 限频: 首次放行", _throttle("ut1", 60) is True)
    check("cron 限频: 窗口内拦截", _throttle("ut1", 60) is False)
    check("cron 限频: 不同 key 互不影响", _throttle("ut2", 60) is True)

    # 当日抢占: 用假 execute 记录 SQL, 模拟 maintenance_log 主键冲突
    import routes.cron as _cn
    _calls = []
    _rows = set()

    def _fake_exec(sql, params=None):
        _calls.append(sql.split()[0])
        if sql.strip().startswith("INSERT IGNORE"):
            k = tuple(params or ())
            if k in _rows:
                return 0
            _rows.add(k)
            return 1
        if sql.strip().startswith("DELETE"):
            _rows.discard(tuple(params or ()))
            return 1
        return 0

    _old_exec = _cn.execute
    _cn.execute = _fake_exec
    try:
        a = _claim("t:demo")
        b = _claim("t:demo")      # 同一天第二次
        _unclaim("t:demo")
        c = _claim("t:demo")      # 回滚后应可重新抢占
    finally:
        _cn.execute = _old_exec
    check("cron 当日抢占: 首次 True / 重复 False / 回滚后可重抢", (a, b, c) == (True, False, True), str((a, b, c)))
except Exception as _e6:
    check("cron 护栏用例无异常", False, str(_e6)[:200])

# ── traced 不回传 traceback(2026-11-02 S0) ──
try:
    from routes.common import traced

    @traced
    def _boom():
        raise ValueError("内部细节: SELECT * FROM secret_table")

    _r = _boom()
    _blob = json.dumps(_r, ensure_ascii=False)
    check("traced 不回传堆栈/表名(只回 request_id)",
          ("tb" not in _r) and ("secret_table" not in _blob) and bool(_r.get("request_id")),
          _blob[:160])
except Exception as _e7:
    check("traced 用例无异常", False, str(_e7)[:200])

# ── 链路运行流水 pipeline_runs(2026-11-02 S1-2) ──
# 背景: 之前"每日评估/物化重建到底跑没跑"只能翻 quality_logs 猜, 且只留 500 条。
# 这组用例守住三件事: ① 建表语句还在 ② track_run 各种退出状态都落到正确的 status
# ③ /health?detail=1 能读出来(且默认路径不带 last_runs, 不增加 15s 轮询开销)
try:
    import os as _os
    _idx_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "index.py"),
                    encoding="utf-8").read()
    check("pipeline_runs 建表语句存在", "CREATE TABLE IF NOT EXISTS pipeline_runs" in _idx_src)

    from routes.common import track_run, last_runs

    _RUN_SQL = []
    _old_ei, _old_ex = _db.execute_id, _db.execute
    _db.execute_id = lambda sql, params=None: (_RUN_SQL.append(("ins", sql, params)), 77)[1]
    _db.execute = lambda sql, params=None: (_RUN_SQL.append(("upd", sql, params)), 1)[1]

    with track_run("demo_pipe", "jd") as _rc:
        _rc.rows = 5
    with track_run("demo_pipe", "jd") as _rc2:
        _rc2.status = "skipped"
    _err_ok = False
    try:
        with track_run("demo_pipe", "jd"):
            raise RuntimeError("boom")
    except RuntimeError:
        _err_ok = True
    _db.execute_id, _db.execute = _old_ei, _old_ex

    _upd = [x for x in _RUN_SQL if x[0] == "upd"]
    check("track_run 建行+回写各一次", len([x for x in _RUN_SQL if x[0] == "ins"]) == 3 and len(_upd) == 3,
          str(len(_RUN_SQL)))
    check("track_run 正常退出记 ok", _upd and _upd[0][2][0] == "ok", str(_upd[:1]))
    check("track_run 可显式 skipped", len(_upd) > 1 and _upd[1][2][0] == "skipped", str(_upd[1:2]))
    check("track_run 异常记 failed 且原样抛出",
          len(_upd) > 2 and _upd[2][2][0] == "failed" and _err_ok, str(_upd[2:3]))

    r = client.get("/health")
    check("health 默认不带 last_runs(零额外查询)", "last_runs" not in r.json(), r.text[:150])
    r = client.get("/health?detail=1")
    check("health?detail=1 返回 last_runs 列表",
          r.status_code == 200 and isinstance(r.json().get("last_runs"), list), r.text[:200])

    # 运行流水不得外泄 error 正文/堆栈(本端点匿名可读)
    _lr = last_runs()
    check("last_runs 不含 error 正文", all("error" not in x for x in _lr), str(_lr[:1])[:160])
except Exception as _e8:
    check("pipeline_runs 用例无异常", False, str(_e8)[:200])

# ── 日志保留改按天(2026-11-02 S1-3) ──
try:
    from routes.common import prune_quality_logs, QUALITY_LOG_KEEP_DAYS
    _DEL_SQL = []
    _old_ex2 = _db.execute
    _db.execute = lambda sql, params=None: (_DEL_SQL.append(sql), 0)[1]
    _res = prune_quality_logs()
    _db.execute = _old_ex2
    check("日志保留按天(不再是「留最近 N 条」)",
          QUALITY_LOG_KEEP_DAYS >= 7 and any("created_at < DATE_SUB" in s for s in _DEL_SQL),
          str(_DEL_SQL)[:160])
    check("prune 返回结构化结果", isinstance(_res, dict) and {"old", "cap", "total"} <= set(_res), str(_res))
except Exception as _e9:
    check("日志保留用例无异常", False, str(_e9)[:200])

# ── 历史归档查询(daily_stats 读出口, 2026-11-02 S2-4/A) ──
try:
    import routes.history as _H

    _s, _e, _c = _H._range("", "")
    check("history._range 缺省 30 天", _c is False and _H._shift(_e, -29) == _s, "%s~%s" % (_s, _e))

    _s2, _e2, _ = _H._range("2026-10-31", "2026-10-01")
    check("history._range 反序自动纠正", (_s2, _e2) == ("2026-10-01", "2026-10-31"), "%s~%s" % (_s2, _e2))

    _s3, _e3, _c3 = _H._range("2020-01-01", "2026-11-01")
    check("history._range 超 400 天截断并置 clamped",
          _c3 and _H._shift(_s3, _H._MAX_SPAN - 1) == _e3, "%s~%s" % (_s3, _e3))

    _s4, _e4, _ = _H._range("abc", "def")
    check("history._range 脏输入回落为合法区间", len(_s4) == 10 and _s4 <= _e4, "%s~%s" % (_s4, _e4))

    _cut = _H._cutoff()
    _sq_a, _ = _H._union("jd", _H._shift(_cut, -60), _H._shift(_cut, -1))
    check("history._union 全在归档段时只查 daily_stats",
          "FROM daily_stats" in _sq_a and "FROM orders" not in _sq_a, _sq_a[:60])
    _sq_o, _ = _H._union("jd", _cut, _H._shift(_cut, 10))
    check("history._union 全在订单段时只查 orders",
          "FROM orders" in _sq_o and "FROM daily_stats" not in _sq_o, _sq_o[:60])
    _sq_x, _p_x = _H._union("jd", _H._shift(_cut, -5), _H._shift(_cut, 5))
    check("history._union 跨切线两段都在(各 3 参)", "FROM daily_stats" in _sq_x
          and "FROM orders" in _sq_x and len(_p_x) == 6, str(len(_p_x)))

    _dl = _H._delta({"gmv": 150, "orders": 12, "qty": 30}, {"gmv": 100, "orders": 10, "qty": 0})
    check("history._delta 正常算增幅", _dl["gmv"] == 50.0 and _dl["orders"] == 20.0, str(_dl))
    check("history._delta 分母为 0 给 None(不显示 -100%)", _dl["qty"] is None, str(_dl))

    _h = {"X-Auth-Token": TOKEN}
    r = client.get("/history/summary?channel=jd&start=2026-10-01&end=2026-10-02", headers=_h)
    _d = r.json().get("data") or {}
    check("history/summary 返回汇总+序列",
          bool(_d.get("totals")) and _d["totals"].get("gmv") == 250.0
          and len(_d.get("series") or []) == 2, r.text[:160])
    check("history/summary 带数据来源分段(可核对取自哪张表)",
          isinstance(_d.get("sources"), dict) and _d["sources"].get("orders"), str(_d.get("sources")))

    r = client.get("/history/top?channel=jd&start=2026-10-01&end=2026-10-02&by=sku", headers=_h)
    _d = r.json().get("data") or {}
    check("history/top 返回占比(Top 合计=100%)",
          len(_d.get("items") or []) == 2
          and abs(sum(x["share"] for x in _d["items"]) - 100.0) < 0.2, r.text[:160])

    # end 取 2026-01-31: 去年同窗(2025-01-31~2025-03-01)整段落在归档侧, 才是"无去年数据"的干净用例
    r = client.get("/history/yoy?channel=jd&days=30&end=2026-01-31", headers=_h)
    _d = r.json().get("data") or {}
    check("history/yoy 无去年数据时 has_prev=false 且不编造同比",
          _d.get("has_prev") is False and _d.get("delta") is None and "无数据" in (_d.get("note") or ""),
          r.text[:200])

    r = client.get("/history/summary?channel=jd&start=2023-01-01&end=2023-03-01", headers=_h)
    _d = r.json().get("data") or {}
    check("history/summary 无数据时不谎报来源(两段都为 null)",
          (_d.get("sources") or {}).get("orders") is None
          and (_d.get("sources") or {}).get("archive") is None, str(_d.get("sources")))

    r = client.get("/history/coverage?channel=jd", headers=_h)
    _d = r.json().get("data") or {}
    check("history/coverage 归档为空时说清原因",
          _d.get("archive", {}).get("rows") == 0 and "暂无归档" in (_d.get("note") or ""), r.text[:200])

    r = client.get("/history/summary?channel=jd")
    check("history/* 未登录被拦(业务数据不裸奔)", r.status_code == 401, str(r.status_code))
except Exception as _e10:
    check("历史归档用例无异常", False, str(_e10)[:200])


# ── S2-2 增量重建(2026-11-02): 按业务日期重建, 不按导入时间 ──
try:
    from routes.common import (_norm_day, mark_agg_days, clear_agg_day, AGG_WINDOW_DAYS)
    from datetime import datetime as _dt4, timedelta as _td4, timezone as _tz4

    _MARKED = []
    _old_em4, _old_q4, _old_o4, _old_e4 = (_db.executemany, _db.query, _db.one, _db.execute)
    _db.executemany = lambda sql, seq: _MARKED.extend(list(seq))
    _db.query = lambda sql, params=None: (
        [{"channel": "jd", "d": "2026-11-01", "ts": "2026-11-02 10:00:00"}]
        if "FROM agg_dirty_days" in sql else _old_q4(sql, params))
    _db.one = lambda sql, params=None: (
        {"c": 0} if "FROM agg_dirty_days" in sql else _old_o4(sql, params))
    _db.execute = lambda sql, params=None: (1 if "agg_dirty_days" in sql else 0)

    from routes.common import _runs
    check("S2-2 _runs 合并连续日期, 断点切段",
          _runs(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-09"]) ==
          [("2026-10-01", "2026-10-03"), ("2026-10-09", "2026-10-09")],
          str(_runs(["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-09"])))
    check("S2-2 _runs 单段不超过 span 天(防大事务持锁)",
          _runs(["2026-10-%02d" % i for i in range(1, 21)], 7) ==
          [("2026-10-01", "2026-10-07"), ("2026-10-08", "2026-10-14"),
           ("2026-10-15", "2026-10-20")],
          str(_runs(["2026-10-%02d" % i for i in range(1, 21)], 7)))

    check("S2-2 _norm_day 只认日期形状(不接受脏值)",
          _norm_day("abc") == "" and _norm_day("2026-11") == ""
          and _norm_day("2026-11-01 10:00:00") == "2026-11-01", "")

    _oday = (_dt4.now(_tz4.utc) - _td4(days=AGG_WINDOW_DAYS + 5)).strftime("%Y-%m-%d")
    _nday = (_dt4.now(_tz4.utc) - _td4(days=1)).strftime("%Y-%m-%d")
    _MARKED.clear()
    _n = mark_agg_days("jd", [_nday, _oday, "bad", _nday], reason="t")
    check("S2-2 mark 去重 + 丢掉窗口外日期 + 拒绝脏值",
          _n == 1 and len(_MARKED) == 1 and _MARKED[0][1] == _nday,
          "n=%s marked=%s" % (_n, _MARKED))

    _db.execute = lambda sql, params=None: (0 if "ts<=%s" in sql else 1)
    check("S2-2 clear 在重建期间被重新标记时不误删",
          clear_agg_day("jd", _nday, "2026-11-02 10:00:00") is False, "")
    _db.execute = lambda sql, params=None: 1
    check("S2-2 clear 正常出队", clear_agg_day("jd", _nday, "2026-11-02 10:00:00") is True, "")

    _cw = open('routes/cleanse_write.py', encoding='utf-8').read()
    check("S2-2 订单导入成功后登记重建",
          "mark_agg_days(channel, _nd" in _cw and "agg_sum=[\"quantity\"" in _cw, "cleanse_write.py")
    check("S2-2 登记发生在写入之后(顺序反了会丢数据)",
          _cw.index('_res = _write_batch("orders"') < _cw.index("mark_agg_days(channel, _nd"),
          "cleanse_write.py")
    # 2026-11-02 S2-5: upsert 换日期时旧日期也得重建, 否则同一笔订单在两天各算一次
    check("S2-5 旧业务日期并入重建集合",
          "_old_days = {_d for _p in _prev.values()" in _cw
          and "(_days | _old_days)" in _cw, "cleanse_write.py")

    _od = open('routes/orders.py', encoding='utf-8').read()
    check("S2-2 删单/恢复前先取受影响日期",
          "_touch_days = _days_of(ids)" in _od and "_tdays = _days_of([oid])" in _od, "orders.py")
    _mk_at = _od.index("_mark_days(_touch_days")
    check("S2-2 先入队再 invalidate(否则线程读到空队列)",
          _mk_at < _od.index('invalidate("orders")', _mk_at), "orders.py")

    _ac = open('routes/analysis_cache.py', encoding='utf-8').read()
    check("S2-2 物化追平改为按日增量消费",
          "_drain_agg_dirty" in _ac and "_MAT_WINDOW = 3" in _ac, "analysis_cache.py")
    check("S2-2 增量重建同时重建日销快照(否则日销口径掉)",
          "_rebuild_snapshot_range(ch, a, b)" in _ac, "analysis_cache.py")

    _cr3 = open('routes/cron.py', encoding='utf-8').read()
    check("S2-2 兜底扫描用 synced_at 反查业务日期",
          "def run_agg_sweep" in _cr3 and "synced_at >= DATE_SUB" in _cr3, "cron.py")
    check("S2-2 回收站永久删前先取日期",
          "回收站取受影响日期" in _cr3, "cron.py")

    _ix2 = open('index.py', encoding='utf-8').read()
    check("S2-2 建 agg_dirty_days 表 + orders.synced_at 列",
          "CREATE TABLE IF NOT EXISTS agg_dirty_days" in _ix2
          and "synced_at DATETIME(6)" in _ix2, "index.py")

    _dm2 = open('routes/dashboard_maintain.py', encoding='utf-8').read()
    check("S2-2 每日维护跑兜底扫描",
          "run_agg_sweep(48)" in _dm2, "dashboard_maintain.py")
    check("S2-2 快照单日重建已抽成函数(对账与增量共用同一实现)",
          "def _rebuild_snapshot_day" in _dm2 and "_rebuild_snapshot_day(channel, d)" in _dm2,
          "dashboard_maintain.py")

    _db.executemany, _db.query, _db.one, _db.execute = _old_em4, _old_q4, _old_o4, _old_e4
except Exception as _e11:
    check("S2-2 用例无异常", False, str(_e11)[:200])

# ── 2026-11-02 S2-5 订单四维业务键(channel|order_no|sku|barcode) ──────────────
try:
    from routes import cleanse_write as _cs3
    _SQL3, _ROWS3 = [], []
    _old_em3 = _cs3.executemany
    _cs3.executemany = lambda sql, seq: (_SQL3.append(sql), _ROWS3.extend(list(seq)))
    _r3 = _cs3._write_batch("orders",
                            ["order_no", "sku", "barcode", "quantity", "total_amount", "channel"],
                            [{"order_no": "K1", "sku": "S1", "barcode": "69-1",
                              "quantity": 2, "total_amount": 20, "channel": "jd"}],
                            "sum", "order_no", "sku", agg_key3="barcode",
                            agg_sum=["quantity", "total_amount"])
    _sql3 = _SQL3[-1] if _SQL3 else ""
    check("S2-5 订单写入是 upsert(重导不再整批 1062)",
          "ON DUPLICATE KEY UPDATE" in _sql3 and _r3[0] == 1, _sql3[:120])
    check("S2-5 upsert 覆盖各列但不碰代理键",
          "`quantity` = VALUES(`quantity`)" in _sql3 and "`id` = VALUES" not in _sql3, _sql3[:160])
    check("S2-5 upsert 显式恢复软删(deleted_at='')",
          "`deleted_at` = ''" in _sql3, _sql3[:160])
    _cs3.executemany = _old_em3

    _sc3 = open('schema_create.py', encoding='utf-8').read()
    check("S2-5 四维唯一键建在 channel|order_no|sku|barcode 上",
          "uq_orders_biz (channel, order_no, sku, barcode)" in _sc3, "schema_create.py")
    check("S2-5 先建新键再删旧键(中间不留裸奔窗口)",
          _sc3.index("ADD UNIQUE KEY uq_orders_biz") < _sc3.index("DROP INDEX uq_orders_no_sku"),
          "schema_create.py")
    check("S2-5 建键前先合并四维重复(否则 ALTER 直接失败)",
          "PARTITION BY channel, order_no, sku, barcode" in _sc3, "schema_create.py")

    _cw3 = open('routes/cleanse_write.py', encoding='utf-8').read()
    check("S2-5 写前对齐码(防止同一行因补码分叉成两行)",
          "def _align_order_keys" in _cw3
          and _cw3.index("_align_order_keys(channel, cleaned)") < _cw3.index('_res = _write_batch("orders"'),
          "cleanse_write.py")
    check("S2-5 对齐在补码之后(补码会改变键)",
          _cw3.index("_fill_barcode_from_products(channel, cleaned)") < _cw3.index("_align_order_keys(channel, cleaned)"),
          "cleanse_write.py")
    check("S2-5 多码 SKU 不参与对齐(那本来就是多行)",
          'len(_nonempty) == 1 and "" not in _codes' in _cw3, "cleanse_write.py")
    # 实测 TiDB 单条 UPDATE 222ms —— 逐行升级 5000 行要 18 分钟, 必须是批量 CASE 语句
    check("S2-5 空码升级走批量 CASE(不是逐行 UPDATE)",
          "def _promote_empty_barcode" in _cw3 and "CASE CONCAT(order_no,'|',sku)" in _cw3
          and "UPDATE orders SET barcode=%s WHERE channel=%s AND order_no=%s" not in _cw3,
          "cleanse_write.py")

    _cl3 = open('routes/cleansing.py', encoding='utf-8').read()
    check("S2-5 订单不再被入口查重拦死(否则覆盖逻辑成死代码)",
          '"order": ("orders", "order_no", "sku", "")' not in _cl3, "cleansing.py")

    _cp3 = open('../../frontend/src/pages/CleansingPage.tsx', encoding='utf-8').read()
    check("S2-5 前端同键改为提示而不是跳过",
          "将按本次文件覆盖更新" in _cp3 and "文件已导入过（同键全部已存在），已跳过" not in _cp3,
          "CleansingPage.tsx")
except Exception as _e12:
    check("S2-5 用例无异常", False, str(_e12)[:200])

# ── 2026-11-02 S2-1: 缓存按域失效 ────────────────────────────────────
try:
    import glob as _glob
    import re as _re

    _ac4 = open('routes/analysis_cache.py', encoding='utf-8').read()
    check("S2-1 声明了缓存族→数据域矩阵", "_FAMILY_DEPS" in _ac4 and "_KNOWN_DOMAINS" in _ac4,
          "analysis_cache.py")
    check("S2-1 失效用墓碑而非 DELETE(令牌才有落点)",
          "_DEAD_TS" in _ac4 and "DELETE FROM analysis_cache\n" not in _ac4, "analysis_cache.py")
    check("S2-1 令牌取在 builder 之前(之后取会盖掉重建期间的新写入)",
          _ac4.index("_tok = _now_tok()") < _ac4.index("val = builder()"), "analysis_cache.py")
    check("S2-1 落表走令牌守卫 cache_put", "cache_put(key, _s, tok=_tok" in _ac4, "analysis_cache.py")
    check("S2-1 未登记域按全清处理(写错域名不能漏清)",
          'bool(_doms - _KNOWN_DOMAINS)' in _ac4, "analysis_cache.py")
    check("S2-1 只有订单域才起物化追平线程",
          'if not (_wipe or ("orders" in _doms))' in _ac4, "analysis_cache.py")

    # 逐文件扫描: 不许裸 invalidate(); 域名必须已登记; invalidate_all 只许出现在白名单
    from routes.analysis_cache import (_FAMILY_DEPS as _FD4, _KNOWN_DOMAINS as _KD4)
    _bare, _bad, _nuc = [], [], []
    for _f in sorted(_glob.glob('routes/*.py')):
        _s4 = open(_f, encoding='utf-8').read()
        for _m in _re.finditer(r"[^_]invalidate\(\)", _s4):
            _bare.append(_f)
        for _m in _re.finditer(r"invalidate\(([^)\n]*)\)", _s4):
            if "def " in _s4[max(0, _m.start() - 12):_m.start()]:
                continue
            for _d in _re.findall(r"\"([a-z_]+)\"", _m.group(1)):
                if _d != "all" and _d not in _KD4:
                    _bad.append("%s:%s" % (_f, _d))
        for _m in _re.finditer(r"from routes\.analysis_cache import invalidate_all", _s4):
            _nuc.append(_f)
    check("S2-1 无裸 invalidate() 调用", not _bare, str(sorted(set(_bare))))
    check("S2-1 调用点域名全部已登记", not _bad, str(sorted(set(_bad))[:5]))
    check("S2-1 invalidate_all 只用于跨表场景(种子/重置/迁移)",
          set(_nuc) <= {"routes/task_seed.py", "routes/misc.py"}, str(sorted(set(_nuc))))

    # 矩阵语义: 改供应商不能连带清采购/补货/订单/历史
    check("S2-1 供应商只清 看板summary/aux/断货风险",
          {f for f, d in _FD4.items() if "suppliers" in d} == {"dash_summary", "dash_aux", "stock_risk"},
          str({f for f, d in _FD4.items() if "suppliers" in d}))
    check("S2-1 采购单/处置记录只清采购建议",
          {f for f, d in _FD4.items() if "purchases" in d} == {"purchase"},
          str({f for f, d in _FD4.items() if "purchases" in d}))
    check("S2-1 订单域必须波及全部派生聚合(4维任一改都会改数)",
          {f for f, d in _FD4.items() if "orders" in d} == set(_FD4), "矩阵漏项")
    check("S2-1 参数域不动订单/历史/加速/日销摘要",
          not ({"orders", "history", "accel", "sales_digest"} & {f for f, d in _FD4.items() if "config" in d}),
          "config 域过宽")

    # 动态: 桩掉 db 只看"该清哪些族"
    _old_ex4 = _db.execute
    _db.execute = lambda sql, params=None: 0
    try:
        from routes.analysis_cache import invalidate as _inv4
        _r_sup = _inv4("suppliers")
        _r_pur = _inv4("purchases")
        _r_typ = _inv4("no_such_domain")
    finally:
        _db.execute = _old_ex4
    check("S2-1 invalidate(suppliers) 实际只清 3 族", _r_sup == ["dash_aux", "dash_summary", "stock_risk"],
          str(_r_sup))
    check("S2-1 invalidate(purchases) 实际只清 1 族", _r_pur == ["purchase"], str(_r_pur))
    check("S2-1 未知域名回落全清", len(_r_typ) == len(_FD4), str(len(_r_typ)))

    _ds4 = open('routes/dashboard.py', encoding='utf-8').read()
    check("S2-1 断货风险键族归首(否则域级匹配不到)",
          '"stock_risk|%s|%s|%s"' in _ds4 and '"%s|stock_risk|' not in _ds4, "dashboard.py")
    _sv4 = open('biz/sales.py', encoding='utf-8').read()
    check("S2-1 日销摘要走 cache_put(令牌)", "cache_put" in _sv4 and "tok=_w_tok" in _sv4, "sales.py")
    _sp4 = open('routes/suppliers.py', encoding='utf-8').read()
    check("S2-1 供应商评分变更触发规则重算(评分是规则 ctx 输入)",
          '_schedule_rule_eval' in _sp4 and 'if "score" in d' in _sp4, "suppliers.py")
    _rl4 = open('routes/rules.py', encoding='utf-8').read()
    check("S2-1 后台评估完补一次失效(否则告警变了看板还旧)",
          '_inv("rules")' in _rl4, "rules.py")

    # S2-2 删掉"写操作后全量重建日销三窗口"却没补增量 → 线上 sku_sales_stats 比订单晚一天。
    #   补货/采购/断货/健康全读它, 不修的话"缓存失效后重建出来的还是旧数"。
    check("S2-1 脏日期会重算日销三窗口(否则补货读旧日销)",
          "rebuild_sku_stats_for_days" in _ac4 and "_rst(ch, _done_days)" in _ac4,
          "analysis_cache.py")
    _sv4b = open('biz/sales.py', encoding='utf-8').read()
    check("S2-1 日销物化支持按 SKU 子集重建(不整渠道删)",
          "def rebuild_sku_stats_for_days" in _sv4b
          and "DELETE FROM sku_sales_stats WHERE channel=%s AND sku IN (" in _sv4b, "sales.py")
    check("S2-1 子集重建不误删其它 SKU(整渠道 DELETE 只能在全量分支)",
          "_scope" in _sv4b and "if _scope:" in _sv4b, "sales.py")
except Exception as _e13:
    check("S2-1 用例无异常", False, str(_e13)[:200])

# ── 2026-11-02 S2-1b: 全量重建必须与增量覆盖同一组物化 ──────────────────
# 线上实测缺陷: 脏日期 > batch*2 时走 `_rebuild_day_agg(90)` 单条 upsert ——
#   ① upsert 删不掉"软删订单留下的组合"(agg/快照都是组合粒度);
#   ② 只重建 agg + 日销三窗口, 没重建快照 → 删单后 daily_sales_snapshot 留残行,
#      补货/采购继续读旧日销。修法: 全量只是"一次取完 + 不设批次上限", 算法与增量同一套。
try:
    _ac5 = open('routes/analysis_cache.py', encoding='utf-8').read()
    check("S2-1b 全量模式不再用 upsert 覆盖全窗口(残留组合删不掉)",
          "_rebuild_day_agg(AGG_WINDOW_DAYS)" not in _ac5, "analysis_cache.py")
    check("S2-1b 全量模式不再整表清脏标记(清标记必须伴随重建)",
          "DELETE FROM agg_dirty_days" not in _ac5, "analysis_cache.py")
    check("S2-1b 全量/增量共用一条区间重建路径(agg+快照成对)",
          "_cap = (_pend + 8) if _full else batch" in _ac5
          and _ac5.count("_rebuild_snapshot_range(ch, a, b)") == 1, "analysis_cache.py")
    check("S2-1b 全量单轮有段预算(防云函数超时, 余下留队列续跑)",
          "_budget = 24 if _full else 0" in _ac5 and "if _budget and _nr >= _budget" in _ac5,
          "analysis_cache.py")

    # 动态: 桩掉 db, 90 个脏日期 → 每个 agg 区间都必须配一次快照重建, 且队列清空
    from contextlib import contextmanager as _cm5
    from datetime import date as _d5, timedelta as _td5
    from routes import common as _cmn5, dashboard_maintain as _dmt5
    import biz.sales as _bs5

    class _Rec5(object):
        rows = 0
        detail = {}

    @_cm5
    def _fake_track5(*a, **k):
        yield _Rec5()

    def _mk_queue5(n):
        _b = _d5(2026, 6, 1)
        return [("CH1", (_b + _td5(days=_i)).strftime("%Y-%m-%d"), 1000 + _i) for _i in range(n)]

    _q5 = _mk_queue5(90)
    _seen5 = []
    _orig5 = (_cmn5.agg_pending, _cmn5.take_agg_days, _cmn5.clear_agg_day, _cmn5.track_run,
              _dmt5._rebuild_agg_range, _dmt5._rebuild_snapshot_range,
              _dmt5._mark_agg_dirty, _dmt5._clear_agg_dirty,
              _bs5.rebuild_sku_stats_for_days)
    _cmn5.agg_pending = lambda channel=None: len(_q5)
    _cmn5.take_agg_days = lambda limit=60: list(_q5[:limit])

    def _clr5(ch, d, ts):
        for _x in list(_q5):
            if _x[0] == ch and _x[1] == d and _x[2] <= ts:
                _q5.remove(_x)
                return True
        return False

    _cmn5.clear_agg_day = _clr5
    _cmn5.track_run = _fake_track5
    _dmt5._rebuild_agg_range = lambda ch, a, b: (_seen5.append(("agg", ch, a, b)) or True)
    _dmt5._rebuild_snapshot_range = lambda ch, a, b: _seen5.append(("snap", ch, a, b))
    _dmt5._mark_agg_dirty = lambda: None
    _dmt5._clear_agg_dirty = lambda: None
    _bs5.rebuild_sku_stats_for_days = lambda ch, days, max_skus=2000: len(days)
    try:
        from routes.analysis_cache import _drain_agg_dirty as _dr5
        _r5 = _dr5()
    finally:
        (_cmn5.agg_pending, _cmn5.take_agg_days, _cmn5.clear_agg_day, _cmn5.track_run,
         _dmt5._rebuild_agg_range, _dmt5._rebuild_snapshot_range, _dmt5._mark_agg_dirty,
         _dmt5._clear_agg_dirty, _bs5.rebuild_sku_stats_for_days) = _orig5

    _aggs5 = [x for x in _seen5 if x[0] == "agg"]
    _snps5 = [x for x in _seen5 if x[0] == "snap"]
    check("S2-1b 全量模式 agg 与快照区间一一对应(漏一个就留残行)",
          len(_aggs5) == len(_snps5) and len(_aggs5) > 0
          and [x[1:] for x in _aggs5] == [x[1:] for x in _snps5],
          "agg=%d snap=%d" % (len(_aggs5), len(_snps5)))
    check("S2-1b 全量模式脏日期全部出队(mode=full)",
          _r5.get("mode") == "full" and _r5.get("left") == 0 and _r5.get("days") == 90,
          str({k: _r5.get(k) for k in ("mode", "days", "left")}))
    check("S2-1b 全量模式顺带重算日销三窗口(否则补货读旧日销)",
          int(_r5.get("stats") or 0) == 90, str(_r5.get("stats")))

    # 段预算: 脏日期远超预算时不许"假完成"——余下必须留在队列, agg_dirty 保持置位
    _q5 = _mk_queue5(400)
    _seen5 = []
    _marked5 = []
    _dmt5._mark_agg_dirty = lambda: _marked5.append(1)
    _cmn5.agg_pending = lambda channel=None: len(_q5)
    _cmn5.take_agg_days = lambda limit=60: list(_q5[:limit])
    _cmn5.clear_agg_day = _clr5
    _cmn5.track_run = _fake_track5
    _dmt5._rebuild_agg_range = lambda ch, a, b: (_seen5.append(("agg", ch, a, b)) or True)
    _dmt5._rebuild_snapshot_range = lambda ch, a, b: _seen5.append(("snap", ch, a, b))
    _bs5.rebuild_sku_stats_for_days = lambda ch, days, max_skus=2000: len(days)
    try:
        _r5b = _dr5()
    finally:
        _dmt5._mark_agg_dirty = lambda: None
    check("S2-1b 段预算用尽不算完成(余下留队列 + agg_dirty 置位继续直查)",
          int(_r5b.get("left") or 0) > 0 and _marked5
          and len([x for x in _seen5 if x[0] == "agg"]) == 24,
          str({k: _r5b.get(k) for k in ("mode", "days", "left")}))
except Exception as _e15:
    check("S2-1b 全量物化用例无异常", False, str(_e15)[:200])

# ── 2026-11-02 S2-1c: 硬删订单后的日销孤儿行 ────────────────────────────
# 同一类"组合消失"问题的第二段, 且**单向不可自愈**:
#   sku_sales_stats 是"有销量才建行"。软删能自愈(行还在 orders → 按日反查得到 → 先删后建);
#   硬删之后 SKU 既不在 orders 也不在快照 → 任何"按受影响 SKU 重建"都反查不到 → 旧行永久残留,
#   补货/断货/健康继续按已删订单的日销算。线上实测: 快照残行已回收, 日销行仍 2 行。
try:
    _sv6 = open('biz/sales.py', encoding='utf-8').read()
    check("S2-1c 有日销孤儿行清理(硬删订单后旧日销行没人清)",
          "def purge_orphan_sku_stats" in _sv6, "sales.py")
    check("S2-1c 孤儿判据取 快照∪订单 并集(只卖今天的 SKU 也要保住)",
          "daily_sales_snapshot " in _sv6
          and "SELECT DISTINCT sku FROM orders WHERE channel=%s " in _sv6, "sales.py")
    _ac6 = open('routes/analysis_cache.py', encoding='utf-8').read()
    check("S2-1c 只在删单类原因下清孤儿(导入不必多付一遍扫描)",
          "agg_has_delete" in _ac6 and "if _is_del:" in _ac6, "analysis_cache.py")
    check("S2-1c 删单原因在出队前判断(出队后 reason 随行消失)",
          _ac6.index("_is_del = _ch_deleted(ch)") < _ac6.index("clear_agg_day(ch, d, t)"),
          "analysis_cache.py")
    _dmt6 = open('routes/dashboard_maintain.py', encoding='utf-8').read()
    check("S2-1c 每日维护兜底清一次(直接改库/漏标记的最后一道)",
          "purge_orphan_sku_stats" in _dmt6, "dashboard_maintain.py")

    # 动态: 快照里有 A, 订单里有 A/C, 日销表里有 A/B(已硬删) → 只删 B
    import db as _db6
    from biz.sales import purge_orphan_sku_stats as _pos6
    _o_q6, _o_e6 = _db6.query, _db6.execute
    _dele6 = []

    def _fq6(sql, params=None):
        if "daily_sales_snapshot" in sql:
            return [{"sku": "A"}]
        if "FROM orders" in sql:
            return [{"sku": "A"}, {"sku": "C"}]
        if "sku_sales_stats" in sql:
            return [{"sku": "A"}, {"sku": "B"}]
        return []

    _db6.query = _fq6
    _db6.execute = lambda sql, params=None: (_dele6.append(list(params)) or 1)
    try:
        _n6 = _pos6("jd", 90)
    finally:
        _db6.query, _db6.execute = _o_q6, _o_e6
    check("S2-1c 孤儿 SKU 被删, 存活 SKU 不动",
          _n6 == 1 and len(_dele6) == 1 and _dele6[0][1:] == ["B"], str(_dele6))
except Exception as _e16:
    check("S2-1c 孤儿行用例无异常", False, str(_e16)[:200])

# ── 2026-11-02 S2-1d: 自有仓健康 —— 采购在途取导入值 + 组合键对齐 ──────────
# 用户定案: "采购在途以导入类型库存自有仓维度, 导入填写的为准"。
#   现状: own_health 里另有一段 purchase_orders 推算(SUM(qty-received_qty)),
#   而该表只有 suggested_qty/actual_qty/arrival_date —— 每次 Unknown column, 恒按 0,
#   且与 inventory.in_transit_qty 语义重复(导入值已是权威)。→ 删掉, 只认导入。
# 组合键: inventory 业务键是 sku+barcode+warehouse+channel+warehouse_type 五维,
#   而 own_health 原来压扁到 SKU、批次 JOIN 只按 sku+channel —— 会拿别的码/别的仓的批次
#   报效期(线上 51 条里 20 条是 platform/B 仓的批次, 自有仓那批根本没填效期)。
try:
    _oh7 = open('routes/dashboard_own_health.py', encoding='utf-8').read()
    check("S2-1d 采购在途不再从 purchase_orders 反推(字段不存在, 恒 0 且重复计)",
          "FROM purchase_orders" not in _oh7, "dashboard_own_health.py")
    check("S2-1d 采购在途取库存导入的自有仓 in_transit_qty",
          "SUM(IFNULL(in_transit_qty,0)) AS transit" in _oh7
          and "warehouse_type='own'" in _oh7, "dashboard_own_health.py")
    check("S2-1d 自有仓按 sku×69码×仓 聚合(与 inventory 业务键同粒度)",
          "GROUP BY sku, IFNULL(barcode,''), IFNULL(warehouse,'')" in _oh7
          and "_k = (sku," in _oh7, "dashboard_own_health.py")
    check("S2-1d 批次效期不再 JOIN inventory 跨码跨仓(只按 own 仓批次自身维度)",
          "JOIN inventory inv ON inv.sku=b.sku" not in _oh7
          and "warehouse_type='own' \"\n" in _oh7, "dashboard_own_health.py")
    check("S2-1d 批次取值逐级降级: 同码同仓 → 同仓 → 该 SKU 最早",
          "def _pick_batch" in _oh7 and "_pick_batch(sku, bc, wh_raw)" in _oh7,
          "dashboard_own_health.py")
    check("S2-1d 日销按码取值走 pick_ds(单一真源, 只有码可降级)",
          "pick_ds(_bc_ds, _agg_ds, sku, bc, WH_C)" in _oh7, "dashboard_own_health.py")
    check("S2-1d 日销一次取合计+按码(分两次查只是多一次往返)",
          'barcode="all"' in _oh7 and 'barcode == "all"' in open('biz/sales.py', encoding='utf-8').read(),
          "sales.py")

    # 动态: 桩掉 query/get_sku_sales_stats, 断言 ①在途进 can ②缺口数值 ③在途够则不报
    import db as _db7
    from biz import sales as _bs7
    from routes import dashboard_own_health as _ohmod7
    _o7 = (_ohmod7.query, _ohmod7.one, _bs7.get_sku_sales_stats)
    _state7 = {"avail": 10, "transit": 5}

    def _fq7(sql, params=None):
        if "FROM inventory" in sql and "warehouse_type='own'" in sql:
            return [{"sku": "SKU-A", "barcode": "BC-A", "warehouse": "集货仓",
                     "avail": _state7["avail"], "transit": _state7["transit"]}]
        if "FROM products" in sql:
            return [{"sku": "SKU-A", "product_name": "测试A", "barcode": "BC-A",
                     "supplier_code": "", "expiry_managed": "no"}]
        return []

    _bs7.get_sku_sales_stats = lambda *a, **k: {
        "SKU-A|BC-A|__C__": {"sku": "SKU-A", "barcode": "BC-A", "warehouse": "__C__",
                             "ds7": 12, "ds14": 12, "ds28": 12}}
    _ohmod7.query = _fq7
    _ohmod7.one = lambda sql, params=None: {}
    try:
        _r7 = _ohmod7.own_health("jd", full=1)
        _gap7 = [x for x in _r7["items"] if x["alert_type"] == "own_transfer_gap"]
        _state7["avail"], _state7["transit"] = 70, 5
        _r7b = _ohmod7.own_health("jd", full=1)
        _gap7b = [x for x in _r7b["items"] if x["alert_type"] == "own_transfer_gap"]
    finally:
        _ohmod7.query, _ohmod7.one, _bs7.get_sku_sales_stats = _o7

    _need7 = round(12 * float((_r7.get("limits") or {}).get("b2c_days") or 6))
    check("S2-1d 在仓+采购在途一起算可发(缺 = 需求 - 10 - 5)",
          len(_gap7) == 1
          and int(_gap7[0]["gap_qty"]) == _need7 - 10 - 5
          and int(_gap7[0]["need_qty"]) == _need7
          and int(_gap7[0]["available_qty"]) == 10
          and "采购在途 5" in _gap7[0]["description"], str(_gap7[:1])[:240])
    check("S2-1d 在途够则不报缺口(70+5>=72)",
          len(_gap7b) == 0, str(_gap7b[:1])[:200])
    check("S2-1d 告警行带真实码与仓(不再用档案码顶替)",
          _gap7 and _gap7[0].get("barcode") == "BC-A" and _gap7[0].get("warehouse") == "集货仓",
          str(_gap7[:1])[:200])
except Exception as _e17:
    check("S2-1d 自有仓在途用例无异常", False, str(_e17)[:200])

print("\n本地回归: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
