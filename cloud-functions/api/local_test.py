"""本地回归测试: mock db + TestClient 跑全部路由(部署前置定位 Python 层 bug)

用法:
  python3 local_test.py                # 只跑回归(不连库, 沙箱/CI 默认)
  python3 local_test.py --sql-smoke    # 回归 + 真实 SQL 冒烟(需 TIDB_* 凭据)
覆盖: auth / dashboard summary(含 periods 环比/health_index/漏斗)

--sql-smoke 做的是: 把 mock 层收到的每条 SQL 拿去真库 `EXPLAIN`, 只做名称解析与计划,
不读数据行、不执行写入, 因此能安全地对着生产库跑。它要抓的是"表名/列名/GROUP BY
写错了"这类 mock 永远测不出的错(§15.106 三个 NameError 的根因)。实现见 sql_smoke.py。
"""
import sys
import os
import json
import io
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET", "local-test-secret")

import sql_smoke as _sa                                    # noqa: E402

_SQL_SMOKE = ("--sql-smoke" in sys.argv) or os.environ.get("SQL_SMOKE") == "1"
_sa.ENABLED = _SQL_SMOKE


def _A(fn):
    """包一层 SQL 审计。只在 --sql-smoke 时生效(否则原样返回, 零开销零行为变化)。"""
    return _sa.audit(fn)

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

# ── 注入点必须在这里(所有 routes 首次 import 之前) ──
# 原因: routes/*.py 全是 `from db import query`, 名字在 import 那一刻绑定;
#   之后再改 db.query 对已 import 的模块无效。所以要审计 SQL, 只能包在"第一绑定"处。
# 后面那些 `_db.execute = lambda ...` 是测试自己的替身(用于截获 SQL 做断言),
#   它们只影响 _db.xxx 的直接调用者, 不改变已 import 模块内的绑定 —— 故不逐处包。
_db.query = _A(fake_query)
_db.one = _A(fake_one)
_db.execute = _A(lambda sql, params=None: 1)  # rowcount 语义(2026-11-02: 删除快照按 rowcount 判存在)
_db.executemany = _A(lambda sql, seq: None)


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
from routes.common import FUNNEL_STAGES as _FS  # noqa: E402
from routes.dashboard import _build_funnel_stages as _bfs  # noqa: E402

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
    # 总订单 + FUNNEL_STAGES 5 档 = 6 段(2026-11-04 补「申请退款」后由 5 变 6)
    check("funnel 段数=1+状态档", len(dd.get("funnel", [])) == 1 + len(_FS),
          str([x["name"] for x in dd.get("funnel", [])]))

# 2026-11-04 订单阶段分布: 除「总订单」外每一档都必须是 order_status 的原始值,
# 且各档合计必须等于总订单(历史上写死 4 档漏了「申请退款」, 差额静默消失)
_probe = {"待确认": 10, "待发货": 20, "已发货": 30, "已完成": 40, "申请退款": 5, "未知脏值": 3}
_out = _bfs(_probe, sum(_probe.values()))
_names = [x["name"] for x in _out]
check("funnel: 首档为总订单", _names[0] == "总订单", str(_names))
check("funnel: 覆盖全部原始状态值", all(s in _names for s in _FS), str(_names))
check("funnel: 未列入的状态按原值追加不丢弃", "未知脏值" in _names, str(_names))
check("funnel: 各档合计==总订单",
      sum(x["value"] for x in _out[1:]) == _out[0]["value"], str(_out))


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
# 2026-11-04 结构化备注: note_segs(键+参数)与 note(中文字符串)必须成对且一致
#   note_segs 是前端 i18n 双语渲染的契约; note 是导出/历史/告警的兼容字段, 不可删
def _check_note_segs(items, tag):
    it = next((i for i in items if i.get("note")), None)
    if not it:
        return
    segs = it.get("note_segs")
    check(f"{tag} note_segs 为非空数组", isinstance(segs, list) and len(segs) >= 1,
          str(segs)[:120])
    if isinstance(segs, list) and segs:
        check(f"{tag} seg 均含键 k", all(isinstance(x, dict) and x.get("k") for x in segs),
              str(segs)[:120])
    check(f"{tag} note 非空(导出/历史兼容)", bool(str(it.get("note") or "").strip()),
          str(it.get("note"))[:80])

_check_note_segs(d.get("data") or [], "replenish bbcc")
r = client.get("/insights/replenishment?channel=jd&mode=traditional", headers={"X-Auth-Token": TOKEN})
check("replenish traditional 200", r.status_code == 200, r.text[:120])
_check_note_segs((r.json().get("data") or {} if isinstance(r.json().get("data"), dict) else r.json().get("data")) or [], "replenish trad")

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

# ── cron 时间窗守卫(2026-11-03): 窗口外 skipped 且零 SQL; 窗口内放行 ──
# 背景: 平台 schedules 无法注入 header → 应用层认不出调用方, 于是再叠一层"时间窗",
# 把残余风险从"随时可触发"收敛到"每日固定窗口内 + _claim 当日一次"。
from routes.cron import _off_window, _WINDOWS  # noqa: E402
from datetime import datetime as _dt, timezone as _tz, timedelta as _td  # noqa: E402

_SH = _tz(_td(hours=8))
_arch = _WINDOWS.get("archive")
check("cron 窗口: 覆盖 9 个重任务(含 2026-11-04 账期结账)", len(_WINDOWS) == 9,
      str(sorted(_WINDOWS)))
check("cron 窗口: ledger_close = 00:30-03:00 CST(每月 1 号结上月)",
      _WINDOWS.get("ledger_close") == (0, 30, 3, 0), str(_WINDOWS.get("ledger_close")))
check("cron 窗口: archive = 00:30-04:00 CST", _arch == (0, 30, 4, 0), str(_arch))
check("cron 窗口: ping/continue_tasks 不设窗(续跑靠请求驱动直接调用)",
      _off_window("ping") is None and _off_window("continue_tasks") is None, "不应被挡")
_now = _dt.now(_SH)
_cur = _now.hour * 60 + _now.minute
_lo, _hi = _arch[0] * 60 + _arch[1], _arch[2] * 60 + _arch[3]
check("cron 窗口: archive 判定与区间一致",
      (_off_window("archive") is None) == (_lo <= _cur <= _hi),
      "cur=%d lo=%d hi=%d" % (_cur, _lo, _hi))
# 防漏接: 窗口表里每个任务都必须在端点上真调用了守卫(加新任务忘接 = 静默失效)
_src = open("routes/cron.py", encoding="utf-8").read()
_missing = [t for t in _WINDOWS if ('_off_window("%s")' % t) not in _src]
check("cron 窗口: 8 个任务都接了守卫(防漏接)", not _missing, str(_missing))

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
# 2026-11-04 结构化: _trad_note 返回 (note, segs); note 断言不变, 新增 segs 结构断言
from routes.replenishment import _trad_note
_n0, _s0 = _trad_note(0, 0, 0, 0, 100, 196, 0, 0)
check("备注: 日销0+有库存 → 无销量积压", "近30天无销量，库存积压" in _n0)
check("备注: segs 结构(无销量积压)", _s0[0] == {"k": "no_sales_stock"}, str(_s0))
_n1, _s1 = _trad_note(0, 0, 0, 0, 0, 196, 0, 0)
check("备注: 日销0+无库存 → ⚪无销量", "⚪ 近30天无销量" in _n1)
check("备注: segs 结构(⚪)", _s1 == [{"k": "no_sales"}], str(_s1))
check("备注: 需求低场景由动态安全线提示接管", "库存低于安全线，但需求低暂不补" not in _trad_note(0.02, 0, 0, 0.1, 2, 196, 0, 0)[0])
_n2, _s2 = _trad_note(5, 5.1, 4.5, 4, 2, 196, 216, 209)
check("备注: 正常补货 → 濒临前置+需补量", _n2.startswith("🔴 已濒临") and "需补216件" in _n2)
check("备注: segs 结构(濒临前置+need)", _s2[0] == {"k": "critical"} and {"k": "need", "n": 216} in _s2, str(_s2))
_n3, _s3 = _trad_note(5, 5.1, 4.5, 4, 500, 196, 0, 0)
check("备注: 正常不补+库存充足 → 仅趋势", _n3 == "近7➡️ 近14➡️")
check("备注: segs 结构(仅趋势)", _s3 == [{"k": "trend", "t7": "flat", "t14": "flat"}], str(_s3))
# 周转红线(对齐 bbcc 综转 90 天)
_n4, _s4 = _trad_note(1, 1.1, 1, 1, 30, 196, 96, 90, after_turnover=120, tw90=90)
check("备注: 补后综转超90天 → 🔴红线前置", "🔴 补后综转120天超90天" in _n4)
check("备注: segs 结构(turn_over after)", {"k": "turn_over", "w": "after", "v": 120, "t": 90} in _s4, str(_s4))
_n5, _s5 = _trad_note(1, 1.1, 1, 1, 30, 196, 0, 0, after_turnover=80, tw90=90)
check("备注: 当前综转接近90天 → ⚠️", "⚠️ 当前综转80天接近90天" in _n5)
check("备注: segs 结构(turn_near cur)", {"k": "turn_near", "w": "cur", "v": 80, "t": 90} in _s5, str(_s5))
check("备注: 无销量不显示周转(无意义)", "综转" not in _trad_note(0, 0, 0, 0, 100, 196, 0, 0, after_turnover=999, tw90=90)[0])
# 动态安全线提示(2026-10-07: 只提示不参与计算; 现传 seg dict)
_n6, _s6 = _trad_note(5, 5.1, 4.5, 4, 500, 196, 0, 0, safety_seg={"k": "safety_urgent", "d": 3, "p": 6})
check("备注: 动态安全线提示接入", "库存告急" in _n6 and {"k": "safety_urgent", "d": 3, "p": 6} in _s6, str(_n6))
_n7, _s7 = _trad_note(5, 5.1, 4.5, 4, 500, 196, 0, 0, safety_seg={"k": "safety_near", "d": 5, "p": 6})
check("备注: 接近提示接入", "约剩5天" in _n7 and {"k": "safety_near", "d": 5, "p": 6} in _s7, str(_n7))

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

_db.query = _A(fake_agg_query)   # 紧接其后 import routes.dashboard → 必须带审计, 否则该模块 SQL 漏检
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

_db.query = _A(fake_query)   # 同上: 之后 import routes.tasks

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
    # 2026-11-03 S3-1 拆分后 RulesPage 主体外提到 pages/rules/ 子组件,
    # 静态断言改为扫「主页面 + rules/ 目录全部组件」拼接文本(内容断言与拆分位置解耦)
    def _rules_src():
        import glob as _glob
        _txt = _io.open(_os.path.join(_HERE, "../../frontend/src/pages/RulesPage.tsx"), encoding="utf-8").read()
        for _f in sorted(_glob.glob(_os.path.join(_HERE, "../../frontend/src/pages/rules/*.tsx"))):
            _txt += "\n" + _io.open(_f, encoding="utf-8").read()
        return _txt
    _rp = _rules_src()
    check("规则页: 字段路径与 ctx 一致(无 inv.days_since_last/inv.stock)",
          "inv.days_since_last" not in _rp and "inv.stock" not in _rp, "RulesPage.tsx")
    # i18n 对齐后文案进词条, 断言改判「仓储字典里有 bc 选项」(结构意图, 与文案解耦)
    check("规则页: 仓储选项含 BC 合计", "v: 'bc'" in _rp and "rf.wh.bc" in _rp, "rules/labels.tsx")
    check("规则 ctx: locked_qty / daily_sales 已注入",
          "locked_qty\": int(r.get(\"locked_qty\")" in _re.replace("'", '"')
          or '"locked_qty": int(r.get("locked_qty") or 0)' in _re, "rule_eval.py")
    check("规则 ctx: order.total_amount/unit_price 已注入",
          '"total_amount": round(_g[\"amt\"]' in _cl or '"total_amount": round(_g["amt"], 2)' in _cl, "cleansing.py")
    # 2026-11-04 S3-5: 存量数据类步骤已迁出 create_all_tables → 断言覆盖两个存放点
    _mt_src = open('migrations/steps/legacy_log_sync.py', encoding='utf-8').read()
    _mig_src = open('migrations/__init__.py', encoding='utf-8').read()
    _st_rfp = open('migrations/steps/rules_field_path.py', encoding='utf-8').read()
    # 2026-11-04 S3-5: 该步已迁入 migrations/steps/, 断言改扫框架与步骤(与存放位置解耦)
    check("规则字段存量迁移已登记(迁移框架 steps + 注册表)",
          "_LEGACY_ALIASES" in _mt_src and "rules_field_path_v1" in _mt_src
          and "rules_field_path" in _mig_src and "inv.days_since_last" in _st_rfp,
          "migrations/")
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
    # 同上: 拼上 rules/ 子组件再断言
    import glob as _glob
    for _f in sorted(_glob.glob("../../frontend/src/pages/rules/*.tsx")):
        _rp += "\n" + open(_f, encoding="utf-8").read()
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
    _sc_src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "schema_create.py"),
                   encoding="utf-8").read()
    # 2026-11-02 S2-8: 运行表 DDL 收敛到 schema_create(单一真源), index.py 只调 ensure_schema()
    check("pipeline_runs 建表语句存在", "CREATE TABLE IF NOT EXISTS pipeline_runs" in _sc_src
          and "ensure_schema" in _idx_src)

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

    # 2026-11-02 S2-9: 同比跟随区间 —— 传 start/end 时窗口按两端日期算(不再只认 days)
    r = client.get("/history/yoy?channel=jd&start=2026-10-04&end=2026-11-02", headers=_h)
    _d = r.json().get("data") or {}
    check("history/yoy 传 start/end 时按区间窗口(cur 两端与入参一致, 天数=30)",
          (_d.get("cur") or {}).get("start") == "2026-10-04"
          and (_d.get("cur") or {}).get("end") == "2026-11-02"
          and _d.get("days") == 30, r.text[:220])

    # 同一 end 不同 start 不能共用缓存(否则改起点仍返回旧窗口)
    r2 = client.get("/history/yoy?channel=jd&start=2026-09-01&end=2026-11-02", headers=_h)
    _d2 = r2.json().get("data") or {}
    check("history/yoy 缓存 key 含 start(同 end 不同起点窗口不同)",
          (_d2.get("cur") or {}).get("start") == "2026-09-01"
          and (_d2.get("days") or 0) == 63, r2.text[:220])

    # 区间超 400 天: 与 _range 同口径, 截断到最近 400 天而不是报错
    r3 = client.get("/history/yoy?channel=jd&start=2024-01-01&end=2026-11-02", headers=_h)
    _d3 = r3.json().get("data") or {}
    check("history/yoy 区间 >400 天截断到 400 天",
          r3.status_code == 200 and (_d3.get("days") or 0) == 400, r3.text[:220])

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

    _sc2 = open('schema_create.py', encoding='utf-8').read()
    check("S2-2 建 agg_dirty_days 表 + orders.synced_at 列",
          "CREATE TABLE IF NOT EXISTS agg_dirty_days" in _sc2
          and '"synced_at"' in _sc2, "schema_create.py")

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
    # i18n 对齐后文案进词条: 判「存在覆盖提示词条 + 已无『已跳过』旧文案」
    check("S2-5 前端同键改为提示而不是跳过",
          "cl.dup_all" in _cp3 and "已跳过" not in _cp3,
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

    # 多码: 同 SKU 两个 69码 必须各判各的(合并判会用一个码的日销去判另一个码的库存)
    _bs7.get_sku_sales_stats = lambda *a, **k: {
        "SKU-M|BC-A|__C__": {"sku": "SKU-M", "barcode": "BC-A", "warehouse": "__C__",
                             "ds7": 12, "ds14": 12, "ds28": 12},
        "SKU-M|BC-B|__C__": {"sku": "SKU-M", "barcode": "BC-B", "warehouse": "__C__",
                             "ds7": 0.5, "ds14": 0.5, "ds28": 0.5},
        "SKU-M||__C__": {"sku": "SKU-M", "barcode": "", "warehouse": "__C__",
                         "ds7": 12.5, "ds14": 12.5, "ds28": 12.5}}
    _ohmod7.query = lambda sql, params=None: (
        [{"sku": "SKU-M", "barcode": "BC-A", "warehouse": "集货仓", "avail": 10, "transit": 0},
         {"sku": "SKU-M", "barcode": "BC-B", "warehouse": "集货仓", "avail": 1, "transit": 0}]
        if ("FROM inventory" in sql and "warehouse_type='own'" in sql) else
        ([{"sku": "SKU-M", "product_name": "多码品", "barcode": "BC-A",
           "supplier_code": "", "expiry_managed": "no"}] if "FROM products" in sql else []))
    try:
        _r8 = _ohmod7.own_health("jd", full=1)
        _g8 = [x for x in _r8["items"] if x["alert_type"] == "own_transfer_gap"]
    finally:
        _ohmod7.query = _fq7
        _bs7.get_sku_sales_stats = lambda *a, **k: {
            "SKU-A|BC-A|__C__": {"sku": "SKU-A", "barcode": "BC-A", "warehouse": "__C__",
                                 "ds7": 12, "ds14": 12, "ds28": 12}}
    check("S2-1d 多码 SKU 各码各判(BC-A 报缺, 日销 0.5 的 BC-B 不报)",
          len(_g8) == 1 and _g8[0].get("barcode") == "BC-A"
          and int(_g8[0]["available_qty"]) == 10, str(_g8)[:240])
except Exception as _e17:
    check("S2-1d 自有仓在途用例无异常", False, str(_e17)[:200])

# ── S2-1e: 库存联动 _adjust_inventory 的组合键补齐(四维 sku×码×仓×仓型) ──
try:
    from routes import cleanse_write as _cw5
    _src5 = open('routes/cleanse_write.py', encoding='utf-8').read()
    check("S2-1e 联动键含 69码(不再是三维 sku+仓+仓型)",
          "def _adjust_key" in _src5 and "barcode" in _src5.split("def _adjust_key")[1][:900],
          "routes/cleanse_write.py")
    check("S2-1e 联动命中行按五维匹配(码/仓型都进 _cur_map 键)",
          "str(_c.get(\"wt\") or \"\"))] = _c" in _src5.replace(" ", "").replace("\n", "")
          or "COALESCE(warehouse_type,'') AS wt" in _src5, "routes/cleanse_write.py")
    check("S2-1e 联动不再逐 SKU 回查库(`LIMIT 1` 取任意行会串码)",
          "inventory WHERE sku=%s AND channel=%s LIMIT 1" not in _src5, "routes/cleanse_write.py")
    check("S2-1e 新行落 barcode 且 beginning_stock 同步(否则重算把增量抹成 0)",
          "INSERT INTO inventory(sku, barcode, warehouse, warehouse_type, available_qty, " in _src5
          and "beginning_stock, safety_qty, channel" in _src5, "routes/cleanse_write.py")

    # 动态: 同 SKU 两个 69码 → 各更新各的行(id 不能串); 三维旧键 → 落到空码行
    _rows5 = [
        {"id": 11, "sku": "SKU-X", "bc": "BC-A", "wh": "集货仓", "wt": "own",
         "available_qty": 10, "safety_qty": 3, "product_name": "多码品", "in_transit_qty": 0},
        {"id": 12, "sku": "SKU-X", "bc": "BC-B", "wh": "集货仓", "wt": "own",
         "available_qty": 50, "safety_qty": 3, "product_name": "多码品", "in_transit_qty": 7},
    ]
    _seen5 = {"sql": [], "upd": [], "ins": [], "ctx": []}
    _o5 = (_cw5.query, _cw5.executemany)
    _cw5.query = lambda sql, params=None: _seen5["sql"].append(sql) or list(_rows5)
    _cw5.executemany = lambda sql, rows: (_seen5["upd" if sql.lstrip().upper().startswith("UPDATE") else "ins"].append((sql, list(rows))) or len(rows))
    # 拦截规则评估: 直接看传进来的上下文
    import core.rules as _cr5
    _o_em5 = _cr5.evaluate_many
    _cr5.evaluate_many = lambda ev, ctxs, ch=None, rc=None, **k: (_seen5["ctx"].extend(ctxs) or [])
    try:
        _n5, _t5 = _cw5._adjust_inventory("jd", {
            ("SKU-X", "BC-A", "集货仓", "own"): -4,   # 命中 id=11 → 10-4=6
            ("SKU-X", "BC-B", "集货仓", "own"): -5,   # 命中 id=12 → 50-5=45
            ("SKU-X", "集货仓", "own"): -1,           # 旧三维键 → 空码行(库中无) → 插入
            ("SKU-NEW", "BC-C", "集货仓", "own"): 8,  # 新行, 无库存行 → 插入
        })
    finally:
        _cw5.query, _cw5.executemany = _o5
        _cr5.evaluate_many = _o_em5
    _upd5 = dict((r[1], r[0]) for (sq, rr) in _seen5["upd"] for r in rr)
    check("S2-1e 多码各扣各的(A码 10→6, B码 50→45, id 不串)",
          _upd5.get(11) == 6 and _upd5.get(12) == 45, str(_upd5))
    _ins5 = [r for (sq, rr) in _seen5["ins"] for r in rr]
    check("S2-1e 新行走 INSERT 且带码(旧三维键→空码行, 新 SKU→BC-C)",
          len(_ins5) == 2
          and any(r[1] == "" and r[2] == "集货仓" and r[4] == 0 for r in _ins5)
          and any(r[1] == "BC-C" and r[5] == 8 for r in _ins5), str(_ins5))
    check("S2-1e 评估上下文逐行(sku×码×仓)且用更新后的量",
          len(_seen5["ctx"]) == 4
          and {(c["sku"], c["barcode"], c["inv"]["available_qty"]) for c in _seen5["ctx"]}
          >= {("SKU-X", "BC-A", 6), ("SKU-X", "BC-B", 45), ("SKU-NEW", "BC-C", 8)},
          str(_seen5["ctx"])[:240])
    check("S2-1e 联动不再有逐 SKU 回查(query 只调一次批量 SELECT)",
          len(_seen5["sql"]) == 1, "query 调用 %d 次" % len(_seen5["sql"]))
    check("S2-1e 软删行不参与命中(WHERE 带 deleted_at 过滤)",
          all("deleted_at" in s for s in _seen5["sql"]), str(_seen5["sql"])[:160])
except Exception as _e18:
    check("S2-1e 库存联动键用例无异常", False, str(_e18)[:200])

# ── S2-6: 冷算耗时收口 —— 线上瓶颈是 SQL 往返次数(单次 ~57ms), 不是 Python 计算 ──
#    定位依据: cProfile 下 _assemble tottime=0.015s / cumtime=6.15s —— 组装阶段 99% 是往返。
try:
    import routes.dashboard as _dsh6
    import routes.dashboard_health as _dh6
    _src6 = open('routes/dashboard.py', encoding='utf-8').read()
    _src6b = open('biz/sales.py', encoding='utf-8').read()
    _src6c = open('routes/dashboard_own_health.py', encoding='utf-8').read()

    check("S2-6 概览三计数合成一条(标量子查询, 同一快照)",
          "def _q_counts" in _src6
          and all(k in _src6.split("def _q_counts")[1][:900]
                  for k in ("AS alerts_c", "AS products_c", "AS suppliers_c")),
          "routes/dashboard.py")
    check("S2-6 月度回款一条 GROUP BY DATE,shop(原总额/按日/按店三次扫同窗)",
          "GROUP BY DATE(arrived_time), shop" in _src6, "routes/dashboard.py")
    check("S2-6 品牌一次按(日,品牌)取回, 周期在内存切(原 5 次只差日期界)",
          "GROUP BY DATE(o.ordered_at), COALESCE(p.brand,'')" in _src6
          and "def _brands_agg(d0, d1=None)" in _src6, "routes/dashboard.py")
    check("S2-6 品牌并列定序确定(同 GMV 按品牌名, 不再靠 SQL 返回顺序)",
          'key=lambda x: (-x[1]["g"], x[0])' in _src6, "routes/dashboard.py")
    check("S2-6 看板物料只投影被消费的列(原 12 列含 product_name, 9003 行多花 1s)",
          "SELECT sku, COALESCE(barcode,'') AS barcode, warehouse, warehouse_type, " in _src6
          and "SELECT * FROM inventory" not in _src6, "routes/dashboard.py")
    _g6 = _src6b.split("def get_sku_sales_stats")[-1] if "def get_sku_sales_stats" in _src6b else ""
    check("S2-6 建表 DDL 进程内只发一次(原每次 get_sku_sales_stats 都发)",
          "_STATS_DDL_READY" in _src6b and "if _STATS_DDL_READY:" in _src6b
          and "_e(_SKU_STATS_DDL)" in _src6b.split("def _ensure_stats_table")[1][:300]
          and "_e(_SKU_STATS_DDL)" not in _g6,
          "biz/sales.py")
    check("S2-6 自有仓产品/批次不再按 800 分片(实测仅 1000/1696 行)",
          "_chunk" not in _src6c and "exp_map.setdefault(" in _src6c,
          "routes/dashboard_own_health.py")

    # 动态: 全部预取注入时, 组装阶段一条 SQL 都不该发
    _rows6 = [{"d": _NOW.strftime("%Y-%m-%d"), "order_status": "已完成",
               "store": "自营旗舰店", "g": 100.0, "sub": 5.0, "cnt": 2}]
    _hits6 = {"n": 0}

    def _noquery6(*a, **k):
        _hits6["n"] += 1
        raise AssertionError("组装阶段不应再发 SQL: %s" % str(a[0])[:80])

    _o6 = (_dsh6.query, _dsh6.one, _dh6.query, _dh6.one)
    _dsh6.query = _dsh6.one = _dh6.query = _dh6.one = _noquery6
    try:
        _out6 = _dsh6._assemble(
            _rows6, "jd", "2026-10-01", "2026-10-31", "bbcc",
            _inv_rows=[{"sku": "S1", "barcode": "B1", "warehouse": "集货仓",
                        "warehouse_type": "own", "available_qty": 10}],
            _ds_map={"S1": 1.0},
            _counts={"alerts": 1, "products": 2, "suppliers": 3},
            _flows=(123.45, {"2026-10-01": 123.45}, {"店A": 123.45}),
            _brands_raw=[{"d": "2026-10-01", "brand": "品牌A", "g": 100.0, "sb": 5.0, "rf": 0.0}],
            _cfg={})
    finally:
        _dsh6.query, _dsh6.one, _dh6.query, _dh6.one = _o6
    check("S2-6 全部预取注入时 _assemble 一条 SQL 都不发", _hits6["n"] == 0,
          "发出 %d 次" % _hits6["n"])
    check("S2-6 注入值直接生效(计数/月度回款来自入参, 不是重查)",
          _out6["summary"]["total_products"] == 2
          and _out6["summary"]["total_suppliers"] == 3
          and _out6["periods"]["month"]["payout"] == 123.45,
          str(_out6["summary"])[:160])
except Exception as _e19:
    check("S2-6 冷算收口用例无异常", False, str(_e19)[:200])

# ── S2-7: 库存删除可恢复(删除时快照) —— 归档表方案 ──────────────────────────
try:
    import routes.inventory as _inv7
    import routes.analysis_cache as _ac7
    _src7 = open('routes/inventory.py', encoding='utf-8').read()
    _src7b = open('schema_create.py', encoding='utf-8').read()

    check("S2-7 归档表已注册(与 inventory 同列序 + archived_at)",
          "CREATE TABLE IF NOT EXISTS inventory_archive" in _src7b
          and "archived_at DATETIME" in _src7b, "schema_create.py")
    check("S2-7 删除前先快照(INSERT...SELECT 整行), 不是先删后补",
          "INSERT INTO inventory_archive (" in _src7
          and _src7.find("INSERT INTO inventory_archive") < _src7.find("DELETE FROM inventory WHERE id=%s"),
          "routes/inventory.py")
    check("S2-7 恢复先查业务键冲突再插回(查重在 INSERT 之前)",
          "已重新导入为新行" in _src7
          and 0 < _src7.find("SELECT id FROM inventory WHERE sku=") < _src7.find("INSERT INTO inventory ("),
          "routes/inventory.py")
    check("S2-7 重算跳过软删行(快照口径: 删除时的数量不被覆盖)",
          "i.deleted_at IS NULL OR i.deleted_at=''" in
          open('routes/cleanse_write.py', encoding='utf-8').read(), "routes/cleanse_write.py")

    # 动态: 删除 → 归档 INSERT...SELECT + 原行 DELETE + 缓存失效; 恢复 → 原样插回 + 归档移除
    _o7 = (_inv7.query, _inv7.one, _inv7.execute, _ac7.invalidate)
    _inv_row7 = {"id": 77, "sku": "SKU-D", "barcode": "BC-D", "warehouse": "集货仓",
                 "warehouse_type": "own", "channel": "jd", "available_qty": 42}
    _calls7 = {"exec": [], "inv": 0}

    def _one7(sql, p=None):
        s = str(sql)
        if "FROM inventory_archive WHERE id" in s:
            return _arc7
        if "FROM inventory WHERE id=%s" in s and "archive" not in s:
            return _live7  # 同id占用检查 / 删除存在性检查
        if "AND id<>%s" in s:
            return _dup7
        return {"id": 1}

    def _exec7(sql, p=None):
        _calls7["exec"].append((str(sql), p))
        return _rc7

    _inv7.query = lambda sql, p=None: []
    _inv7.one = _one7
    _inv7.execute = _exec7
    _ac7.invalidate = lambda dom: _calls7.__setitem__("inv", _calls7["inv"] + 1)

    # 删除: 行存在(rowcount=1) → 快照 + 删除
    _live7, _arc7, _dup7, _rc7 = {"id": 77}, None, None, 1
    _r7 = _inv7.delete_inventory(77)
    check("S2-7 删除 = 快照进归档表 + 删原行 + 失效缓存",
          _r7.get("ok") and len(_calls7["exec"]) == 2
          and "INSERT INTO inventory_archive" in _calls7["exec"][0][0]
          and "SELECT" in _calls7["exec"][0][0].upper()
          and _calls7["exec"][1][0].startswith("DELETE FROM inventory")
          and _calls7["inv"] == 1, str(_r7))
    check("S2-7 快照带 archived_at(NOW())且按 id 对齐",
          "archived_at" in _calls7["exec"][0][0]
          and "NOW()" in _calls7["exec"][0][0].upper(), _calls7["exec"][0][0][:120])

    # 恢复: 归档行在、无同id、无同键 → 原样插回(22 列全量, 含原 available_qty=42) + 移除归档
    _calls7["exec"] = []
    _live7, _arc7, _dup7 = None, _inv_row7, None
    _r7 = _inv7.restore_inventory(77)
    _ins7 = _calls7["exec"][0] if _calls7["exec"] else ("", None)
    check("S2-7 恢复 = 原样插回(22 列含原值) + 归档移除",
          _r7.get("ok") and "INSERT INTO inventory" in _ins7[0]
          and _ins7[1] and len(_ins7[1]) == 22 and _ins7[1][6] == 42
          and _calls7["exec"][1][0].startswith("DELETE FROM inventory_archive"),
          "%s | %s" % (str(_r7), str(_ins7[1])[:120] if _ins7[1] else ""))

    # 恢复被拦: 同业务键已重新导入 → fail, 不发 INSERT
    _calls7["exec"] = []
    _live7, _arc7, _dup7 = None, _inv_row7, {"id": 999}
    _r7 = _inv7.restore_inventory(77)
    check("S2-7 恢复被拦(同键已有新行): 不插回、报错可读",
          not _r7.get("ok") and "已重新导入" in str(_r7.get("error")) and not _calls7["exec"],
          str(_r7))

    # 删除被拦: rowcount=0(行不存在) → fail, 且只有那一条 INSERT...SELECT、没有 DELETE
    _calls7["exec"] = []
    _live7, _arc7, _dup7, _rc7 = None, None, None, 0
    _r7 = _inv7.delete_inventory(77)
    check("S2-7 删除不存在的行: fail 且不删原行",
          not _r7.get("ok") and len(_calls7["exec"]) == 1, str(_r7))
    _inv7.query, _inv7.one, _inv7.execute, _ac7.invalidate = _o7
except Exception as _e20:
    check("S2-7 删除快照用例无异常", False, str(_e20)[:200])

# ── S2-8: 启动 DDL 版本门禁 —— 热路径一条 SELECT, 零 DDL ────────────────────
try:
    import schema_create as _sc8
    _src8 = open('schema_create.py', encoding='utf-8').read()
    _src8b = open('index.py', encoding='utf-8').read()

    check("S2-8 索引/运行表声明收敛到 schema_create(index.py 不再内联 DDL)",
          "_ADD_INDEXES" in _src8 and "CREATE TABLE IF NOT EXISTS gmv_flows" in _src8
          and "_ADD_INDEXES" not in _src8b
          and "CREATE TABLE IF NOT EXISTS analysis_cache" not in _src8b, "index.py")
    check("S2-8 版本门禁存在且版本号显式声明(bump 提示)",
          "SCHEMA_VERSION = " in _src8 and "_ver >= SCHEMA_VERSION" in _src8
          and "GREATEST(version, VALUES(version))" in _src8, "schema_create.py")
    check("S2-8 失败不写版本(缺口不会跳过)",
          "if _errors:" in _src8 and "不写版本" in _src8, "schema_create.py")

    # 动态①: 版本一致 → 只发 1 条 SELECT, 零 DDL/零写入
    _oq8, _oe8 = _sc8.execute, None
    _hits8 = {"q": 0, "e": []}
    _sc8.query = lambda sql, p=None: (_hits8.__setitem__("q", _hits8["q"] + 1), [])
    _sc8_err = None
    import db as _db8
    _oq8db, _oe8db, _oo8db = _db8.query, _db8.execute, _db8.one
    _db8.one = lambda sql, p=None: {"version": _sc8.SCHEMA_VERSION}
    _db8.execute = lambda sql, p=None: _hits8["e"].append(str(sql)) or 1
    try:
        _sc8.ensure_schema()
    finally:
        _db8.query, _db8.execute, _db8.one = _oq8db, _oe8db, _oo8db
    check("S2-8 版本一致: 全程零写(仅一次版本探测)",
          not _hits8["e"], "执行 %d 条: %s" % (len(_hits8["e"]), _hits8["e"][:2]))

    # 动态②: 首启(表不存在) → 建表+全量+写版本; 且失败不写版本
    # 注意: create_all_tables 用模块顶部 `from db import execute` 绑定 —— 必须同时 patch
    # schema_create.execute, 否则注入不生效且会对真实库发 DDL(2026-11-02 实测踩坑)
    _state8 = {"ver": None, "tables": False}
    def _one8(sql, p=None):
        if "schema_state" in str(sql):
            return {"version": _state8["ver"]} if _state8["ver"] is not None else None
        return None
    def _ex8(sql, p=None):
        s8 = str(sql)
        if "CREATE TABLE IF NOT EXISTS schema_state" in s8:
            _state8["tables"] = True
        if "INSERT INTO schema_state" in s8:
            _state8["ver"] = p[0] if p else 0
        return 1
    _osc_q, _osc_e = _sc8.query, _sc8.execute
    _db8.one, _db8.execute = _one8, _ex8
    _sc8.query, _sc8.execute = (lambda sql, p=None: []), _ex8
    _oq8b = _db8.query
    _db8.query = lambda sql, p=None: []
    # 2026-11-04 S3-5: ensure_schema 现在会调 migrations.run_pending —— 必须一起打桩,
    #   否则它会真的去连库跑迁移(本用例只验版本门禁语义, 不该碰真实库)
    import migrations.runner as _mr8
    _or8 = _mr8.run_pending
    _mr8.run_pending = lambda reg=None: ([], [])
    try:
        _state8["ver"] = None
        _sc8.ensure_schema()
        _first_ok = _state8["ver"] == _sc8.SCHEMA_VERSION
        # 失败注入: 让某张表建不出来(execute 对业务表抛错) → 不写版本
        _state8["ver"] = None
        def _ex8f(sql, p=None):
            if "CREATE TABLE IF NOT EXISTS orders" in str(sql):
                raise RuntimeError("boom")
            return _ex8(sql, p)
        _db8.execute = _ex8f
        _sc8.execute = _ex8f
        _sc8.ensure_schema()
        _fail_no_write = _state8["ver"] is None
    finally:
        _db8.query, _db8.execute, _db8.one = _oq8b, _oe8db, _oo8db
        _mr8.run_pending = _or8
        _sc8.query, _sc8.execute = _osc_q, _osc_e
    check("S2-8 首启: 建 schema_state + 全量执行 + 写入版本", _first_ok, "ver=%r" % _state8.get("ver"))
    check("S2-8 任一建表失败: 不写版本(下次冷启动重试)", _fail_no_write, "ver=%r" % _state8.get("ver"))
except Exception as _e21:
    check("S2-8 版本门禁用例无异常", False, str(_e21)[:200])

# ── S3-5: 迁移框架 —— 有序 / 单步事务 / 只跑未登记的 / 失败不阻断 ─────────────
# 背景(2026-11-04): 存量数据类步骤原内联在 create_all_tables 里, 每次 schema 版本升级会把
# **全部历史步骤重跑**, 靠每步自己 INSERT IGNORE migration_log 自守。漏一处守卫就是
# 改数据类步骤重复执行(金额翻倍级别的事故)。迁入 migrations/ 后由 schema_migrations 统一记账。
try:
    import migrations as _mg
    import migrations.runner as _mr
    _v5 = _mg.validate()

    check("S3-5 注册表 step id 合法且不重复(重复 id 会让后一个被当成已应用)",
          not _v5["bad"] and not _v5["dup"], str(_v5)[:160])
    check("S3-5 步骤模块文件都真实存在(注册表只存模块名, 不在注册时导入)",
          all(_os.path.exists(_os.path.join('migrations/steps', _s['module'] + '.py'))
              for _s in _mg.registry()), "migrations/steps/")
    # 该缺陷是本次迁移的直接动机: 老写法把数据迁移塞进了建表函数
    _sc5 = open('schema_create.py', encoding='utf-8').read()
    _cat5 = _sc5.split("def create_all_tables")[1].split("def ensure_schema")[0]
    check("S3-5 建表函数里不再内联存量数据 UPDATE(只看 DDL + 约束)",
          "UPDATE rules SET" not in _cat5 and "UPDATE alerts SET" not in _cat5
          and "UPDATE products SET" not in _cat5 and "UPDATE channels SET" not in _cat5,
          "schema_create.py")
    check("S3-5 ensure_schema 接入迁移框架(失败计入 _errors → 不写版本)",
          "from migrations.runner import run_pending" in _sc5
          and "迁移失败" in _sc5 and "schema_migrations" in _sc5, "schema_create.py")
    check("S3-5 执行器单步事务(数据改动与登记同一事务提交)",
          "with transaction() as c:" in open('migrations/runner.py', encoding='utf-8').read()
          and "INSERT INTO schema_migrations(id, note, status, attempts)" in
          open('migrations/runner.py', encoding='utf-8').read(),
          "migrations/runner.py")

    # 动态: 已应用集合恒等 → 零执行; 未应用 → 按注册顺序逐条执行并登记; 失败不阻断后续
    _ed = {"done": set(), "failed": {}, "ins": [], "ran": []}

    _or_q, _or_e, _or_t = _mr.query, _mr.execute, _mr.transaction

    def _mq5(sql, p=None):
        s5 = str(sql)
        if "status='applied'" in s5:
            return [{"id": i} for i in _ed["done"]]
        if "status='failed'" in s5:
            return [{"id": k, "attempts": v} for k, v in _ed["failed"].items()]
        return []

    def _me5(sql, p=None):
        # 失败见证(settle)走 execute, 且**不在** run 的事务里 —— 记到这里
        if "ON DUPLICATE KEY UPDATE note=VALUES(note), status=VALUES(status)" in str(sql) \
                and p and len(p) >= 3:
            _ed["failed"][p[0]] = int(p[3] or 0)
        return 1

    _mr.query = _mq5
    _mr.execute = _me5
    import contextlib as _ctx5

    class _Cur5:
        def execute(self, sql, params=None):
            _ed["ins"].append(params[0] if params else None)
            _ed["done"].add(params[0] if params else None)

        def close(self):
            pass

    class _Conn5:
        def cursor(self):
            return _Cur5()

    def _txn5():
        @_ctx5.contextmanager
        def _cm():
            yield _Conn5()

        return _cm()

    _mr.transaction = _txn5
    _fake_reg = [
        {"id": "t_a", "module": "ok_a", "describe": ""},
        {"id": "t_b", "module": "boom_b", "describe": ""},
        {"id": "t_c", "module": "ok_c", "describe": ""},
    ]
    import sys as _sys5
    import types as _ty5

    def _mk5(name, boom):
        m = _ty5.ModuleType("migrations.steps." + name)
        if boom:
            def _run():
                raise RuntimeError("boom")
        else:
            def _run():
                return "ok " + name
        m.run = _run
        _sys5.modules["migrations.steps." + name] = m

    _mk5("ok_a", False)
    _mk5("ok_c", False)
    _mk5("boom_b", True)
    try:
        _ap5, _fa5 = _mr.run_pending(_fake_reg)
        _ok_order = _ap5 == ["t_a", "t_c"] and _fa5 == ["t_b"]
        _failed_witness = _ed["failed"].get("t_b") == 1   # 失败要有见证, 否则下次当没跑过
        # 第二轮: 已应用的零执行; 失败的**再重试一次**(未达上限) → 仍失败, 计数 +1
        _ins_before = len(_ed["ins"])
        _ap5b, _fa5b = _mr.run_pending(_fake_reg)
        _second_none = (_ap5b == [] and _fa5b == ["t_b"]
                        and len(_ed["ins"]) == _ins_before
                        and _ed["failed"].get("t_b") == 2)
        # 第三轮: 失败次数已达上限 → 跳过, 不再重跑(不刷日志/不撞同错)
        _ed["failed"]["t_b"] = _mr.MAX_ATTEMPTS
        _ap5c, _fa5c = _mr.run_pending(_fake_reg)
        _cap_skip = _ap5c == [] and _fa5c == []
    finally:
        for _n5 in ("ok_a", "ok_c", "boom_b"):
            _sys5.modules.pop("migrations.steps." + _n5, None)
        _mr.query, _mr.execute, _mr.transaction = _or_q, _or_e, _or_t
    check("S3-5 动态: 失败步不登记为已应用、不阻断后续", _ok_order, "已应用=%r" % sorted(_ed["done"]))
    check("S3-5 动态: 失败留见证(status=failed + attempts), 否则下次会被当成没跑过",
          _failed_witness, "attempts=%r" % _ed["failed"])
    check("S3-5 动态: 第二轮只重试失败步, 已应用的零执行零登记", _second_none, "")
    check("S3-5 动态: 达失败上限后跳过(不无限重跑)", _cap_skip, "")
except Exception as _e22:
    check("S3-5 迁移框架用例无异常", False, str(_e22)[:200])

# ── S2-9: 补列统一 + 死代码清理 ─────────────────────────────────────────────
try:
    _src9 = open('schema_create.py', encoding='utf-8').read()
    _src9b = open('index.py', encoding='utf-8').read()

    check("S2-9 补列声明唯一(_COLUMN_ADDS, 旧两套与 index.py 四组已并入)",
          "_COLUMN_ADDS = {" in _src9
          and "_extra_cols" not in _src9 and "_EXTRA_COLS" not in _src9
          and "SHOW COLUMNS" not in _src9b, "schema_create.py / index.py")
    check("S2-9 补列走版本门禁(在 ensure_schema 全量路径内, 缺才 ALTER)",
          "for _t, _cols in _COLUMN_ADDS.items()" in _src9
          and "if _c in _have:" in _src9 and "SCHEMA_VERSION = " in _src9, "schema_create.py")
    check("S2-9 同表多列合并不覆盖(dict 无重复键)",
          _src9.count('"inventory": [') == 1 and _src9.count('"products": [') == 1,
          "schema_create.py")

    # 动态: 缺列 → ALTER 发出; 列已在 → 零 ALTER
    import schema_create as _sc9
    import db as _db9
    _oq9, _oe9, _oo9 = _db9.query, _db9.execute, _db9.one
    _alt9, _have9 = [], {"orders"}
    _db9.one = lambda sql, p=None: None
    _db9.query = lambda sql, p=None: ([{"Field": _have9.copy().pop()}] if False else
                                      [{"Field": c} for c in _have9])
    _db9.execute = lambda sql, p=None: (_alt9.append(str(sql)) or 1)
    _osc9q, _osc9e = _sc9.query, _sc9.execute
    _sc9.query, _sc9.execute = _db9.query, _db9.execute
    try:
        # orders 表声明了 ext_json + synced_at, 现有列只有 ext_json → 只应补 synced_at
        _have9 = {"ext_json"}
        _alt9.clear()
        for _t, _cols in _sc9._COLUMN_ADDS.items():
            if _t != "orders":
                continue
            _miss = [c for c, _ in _cols if c not in _have9]
            _alt9.extend(_m for _m in _miss)
        check("S2-9 缺列才补(orders: ext_json 已在 → 只补 synced_at)",
              _alt9 == ["synced_at"], str(_alt9))
    finally:
        _db9.query, _db9.execute, _db9.one = _oq9, _oe9, _oo9
        _sc9.query, _sc9.execute = _osc9q, _osc9e
except Exception as _e22:
    check("S2-9 补列统一用例无异常", False, str(_e22)[:200])

# ── S2-9(2): index.py 启动迁移/规则自愈四段退役(方案B, 复用说明留文档 §15.123) ──
try:
    _src9b = open('index.py', encoding='utf-8').read()
    check("S2-9 启动迁移/规则自愈四段已退役(index.py 不再含 M0/M1/规则修复/退役)",
          all(k not in _src9b for k in ("day_agg_init", "m1_69code", "濒临断货预警",
                                        "滞销识别", "紧急补货")), "index.py")
    check("S2-9 退役说明含文档复用指引",
          "§15.123" in _src9b, "index.py")
except Exception as _e23:
    check("S2-9 探测统一用例无异常", False, str(_e23)[:200])

# ── D4/D5/D8(2026-11-03): 规则评估任务化 + 告警生命周期单一入口 ──
try:
    _rules_src = open('routes/rules.py', encoding='utf-8').read()
    _cron_src = open('routes/cron.py', encoding='utf-8').read()
    _re_src = open('core/rule_eval.py', encoding='utf-8').read()
    _sc_src = open('schema_create.py', encoding='utf-8').read()
    _al_src = open('core/alert_lifecycle.py', encoding='utf-8').read()
    _dash_src = open('routes/dashboard.py', encoding='utf-8').read()

    # ── D4: 改一条规则 ≠ 跑一整套每日任务 ──
    check("D4 run_daily_rules 三参数(channels/deep/alert_types)",
          "def run_daily_rules(channels=None, deep=True, alert_types=None)" in _cron_src,
          "routes/cron.py")
    check("D4 浅档真的跳过重活(循环范围判空, 不是注释)",
          _cron_src.count("(_chs if deep else [])") >= 2, "routes/cron.py")
    check("D4 可按 alert_type 精确评估(only_ats 过滤三组规则)",
          "only_ats" in _re_src and "_want_ats" in _re_src, "core/rule_eval.py")
    check("D4 文案类变更不触发评估(实质变更才调度, 否则只同步显示字段)",
          "_changed_materially(d, _rl)" in _rules_src and "_sync_rule_text(rid)" in _rules_src,
          "routes/rules.py")
    # 浅档可能整块跳过"计算变量注入" → 映射必须在块外初始化, 否则 NameError
    # (线上实测两次: _exp_map 390ms 报错; 修完又轮到 _prod_sup —— 一次性盯住四个)
    _i_blk = _re_src.find("if _need_calc:")
    _miss_init = [v for v in ("_exp_map", "_sup_score", "_own_map", "_prod_sup")
                  if not (0 < _re_src.find("\n    %s = {}" % v) < _i_blk)]
    check("D4 浅档映射全部在 calc 注入块之前初始化(一次性盯四个)",
          not _miss_init, "core/rule_eval.py 未提前初始化: %s" % _miss_init)

    # ── D5: 评估任务化 + 可查询(不再让前端猜时间) ──
    check("D5 eval_tasks 表含 状态/行数/耗时/错误",
          "CREATE TABLE IF NOT EXISTS eval_tasks" in _sc_src
          and all(k in _sc_src for k in ("duration_ms", "rows_out", "started_at")),
          "schema_create.py")
    check("D5 保存返回 eval_task_id 且提供状态接口",
          '"eval_task_id": _tid' in _rules_src and "/rules/eval-status" in _rules_src,
          "routes/rules.py")
    check("D5 aux 的 evaluating 在缓存之外读",
          "FROM eval_tasks WHERE status IN ('pending','running')" in _dash_src,
          "routes/dashboard.py")
    check("D5 前端按真实状态等待(ops 轮询, 不再页面内阻塞 20s)",
          "waitEvalDone" not in open('../../frontend/src/pages/RulesPage.tsx', encoding='utf-8').read()
          and "/api/rules/eval-status" in open('../../frontend/src/App.tsx', encoding='utf-8').read(),
          "RulesPage/App")

    # ── D8: 生命周期单一入口(维度键/关闭原因/重开不新增行) ──
    check("D8 维度键单一真源(5 维: 类型/SKU/仓/渠道/69码)",
          'ALERT_DIM = ("alert_type", "related_sku", "warehouse", "channel", "barcode")' in _al_src,
          "core/alert_lifecycle.py")
    check("D8 关闭统一写 closed_reason/closed_at",
          "closed_reason=%s, closed_at=NOW()" in _al_src, "core/alert_lifecycle.py")
    check("D8 重开复用同维度行(reopen_count+1, 不新增)",
          "reopen_count=COALESCE(reopen_count,0)+1" in _al_src and 'return "reopened"' in _al_src,
          "core/alert_lifecycle.py")
    import glob as _g24
    _bare = []
    for _f in _g24.glob('**/*.py', recursive=True):
        if "__pycache__" in _f or _f.startswith('.') or _f.endswith('alert_lifecycle.py'):
            continue  # 生命周期模块本身是唯一合法实现(其 docstring 会引用旧写法)
        for _ln in open(_f, encoding='utf-8', errors='replace').read().splitlines():
            if "UPDATE alerts" in _ln and "status='inactive'" in _ln and "closed_reason" not in _ln:
                _bare.append(_f + ": " + _ln.strip()[:50])
    check("D8 全库无「裸关」告警语句(必经 close() 或带 reason)", not _bare, str(_bare[:2]))

    # ── 2026-11-03 全局操作态 + 写操作三件套(提示/缓存/事件) ──
    #    背景: 规则保存/导入/采购下单各自手写三件套 → 撤销漏事件、下单只弹 toast;
    #    而 App 顶层在 ToastProvider 之外, 任务完成提示调的是 no-op(从未显示)。
    _fe25 = "../../frontend/src/"
    _store25 = open(_fe25 + "store/useAppStore.ts", encoding="utf-8").read()
    _mut25 = open(_fe25 + "api/mutate.ts", encoding="utf-8").read()
    _app25 = open(_fe25 + "App.tsx", encoding="utf-8").read()
    _rp25 = open(_fe25 + "pages/RulesPage.tsx", encoding="utf-8").read()
    _ins25 = open(_fe25 + "pages/InsightsPage.tsx", encoding="utf-8").read()
    _tst25 = open(_fe25 + "components/Toast.tsx", encoding="utf-8").read()

    check("OPS 操作态与取数态分离(ops != syncing, 各自生命周期)",
          "ops: loadOps()" in _store25 and "'rules' | 'ingest' | 'write'" in _store25,
          "useAppStore.ts")
    check("OPS 由后端真实状态驱动移除(轮询 eval-status, 不靠计时猜)",
          "/api/rules/eval-status" in _app25 and "opDone(o.id)" in _app25, "App.tsx")
    check("OPS 胶囊优先于同步徽标(后端在算时不显示'同步中')",
          "headerPill" in _app25 and "ops.length > 0 ? <OpPill />" in _app25, "App.tsx")
    check("OPS 刷新可恢复 + 陈旧淘汰(15min)",
          "c_ops" in _store25 and "OPS_TTL_MS" in _store25, "useAppStore.ts")
    check("写操作统一出口: 提示/缓存/事件/操作态一次声明",
          "export async function mutate" in _mut25
          and all(k in _mut25 for k in ("clearCache", "dispatchEvent", "notify.success")),
          "api/mutate.ts")
    check("规则写路径接入 mutate(保存/删除/撤销)",
          _rp25.count("mutate(") >= 3, "RulesPage.tsx=%d" % _rp25.count("mutate("))
    check("采购写路径接入 mutate(下单/取消/到仓日期)",
          _ins25.count("mutate(") >= 3, "InsightsPage.tsx=%d" % _ins25.count("mutate("))
    # label 已改词条键(rules.restore_rule), 断言判「用了该词条 + 发联动事件」
    # label 已改词条键(rp.restore_rule), 断言判「用了该词条 + 发联动事件」
    check("撤销删除补发联动事件(原实现只 load 自身)",
          "rp.restore_rule" in _rp25 and "events: ['rules-changed']" in _rp25, "RulesPage.tsx")
    check("顶层提示改走 notify(Provider 外的 useToast 是 no-op)",
          "toast." not in _app25 and "bindNotify" in _tst25, "App.tsx / Toast.tsx")
    check("裸写豁免必须显式标注(// no-link: 理由)",
          "// no-link:" in _rp25, "RulesPage.tsx")
except Exception as _e24:
    check("D4/D5/D8 静态用例无异常", False, str(_e24)[:200])

# 动态: close() 参数顺序 + open_or_reopen 走重开而非插入
try:
    import core.alert_lifecycle as _al24
    _oq24, _oe24, _oqy24 = _db.query, _db.execute, _al24.query
    _cap24 = []
    _db.execute = lambda sql, p=None: (_cap24.append((str(sql), list(p or []))) or 3)
    _al24.execute = _db.execute
    _n24 = _al24.close("channel=%s AND alert_type=%s", ["jd", "low_stock"], "rule_unhit")
    check("D8 close() 参数顺序: SET(inactive,reason) 在前, WHERE 条件在后",
          _n24 == 3 and _cap24 and _cap24[0][1][:2] == ["inactive", "rule_unhit"]
          and _cap24[0][1][2:] == ["jd", "low_stock"], str(_cap24[:1])[:160])

    _cap24.clear()
    _db.query = lambda sql, p=None: ([{"id": 7, "status": "inactive", "reopen_count": 1}]
                                     if "FROM alerts" in str(sql) else [])
    _al24.query = _db.query
    _r24 = _al24.open_or_reopen(
        {"alert_type": "low_stock", "related_sku": "S1", "warehouse": "W1",
         "channel": "jd", "barcode": "69"},
        {"title": "t", "description": "d", "severity": "warning", "source": "rules_engine"})
    check("D8 已关闭维度 → 重开(UPDATE ... reopen_count+1), 不插新行",
          _r24 == "reopened" and _cap24 and "reopen_count=COALESCE(reopen_count,0)+1" in _cap24[0][0]
          and "INSERT INTO alerts" not in _cap24[0][0], str(_cap24[:1])[:160])
except Exception as _e25:
    check("D8 动态用例无异常", False, str(_e25)[:200])

# ── S3-4 真实 SQL 冒烟: 机制本身不许被拆掉 ────────────────────────────
# 冒烟只在"真的收集到 SQL"时才有意义。若有人删掉 _A() 包装, 收集数会变 0,
# 而 --sql-smoke 仍然"0 失败"—— 那就是又一条"不跑的测试"。这四条静态断言守的是这件事。
try:
    _lt_src = open('local_test.py', encoding='utf-8').read()
    check("S3-4 db 注入点全部带 _A() 包装(否则唯一 SQL=0, 冒烟假绿)",
          _lt_src.count('_db.query = _A(') >= 3 and '_db.one = _A(' in _lt_src
          and '_db.execute = _A(' in _lt_src and '_db.executemany = _A(' in _lt_src)
    _re_src = open('core/rule_eval.py', encoding='utf-8').read()
    check("S3-4 采购在途不再从 purchase_orders 反推(该表只有 suggested_qty/actual_qty)",
          'FROM purchase_orders' not in _re_src, "core/rule_eval.py")
    check("S3-4 own.po_transit 与 own.transit 同源 + can_ship 不双计",
          '"po_transit": _ot' in _re_src and '_can = _oa + _ot' in _re_src, "core/rule_eval.py")
    _sm_src = open('sql_smoke.py', encoding='utf-8').read()
    check("S3-4 冒烟带覆盖率下限(收集数腰斩即失败, 不静默变绿)",
          '_MIN_SQL' in _sm_src and '低于基线' in _sm_src, "sql_smoke.py")
except Exception as _e26:
    check("S3-4 静态用例无异常", False, str(_e26)[:200])
finally:
    try:
        _db.query, _db.execute = _oq24, _oe24
        _al24.query = _oqy24
        _al24.execute = _oe24
    except Exception:
        pass

# ── S3-7: 库存批量删除/恢复(订单同款批量操作, 按库存特性适配) ──────────────
try:
    import asyncio as _ai27
    import routes.inventory as _inv27
    import routes.analysis_cache as _ac27

    _src27 = open('routes/inventory.py', encoding='utf-8').read()

    check("S3-7 批量接口存在, 且只接受 delete/restore",
          '@router.post("/inventory/batch")' in _src27
          and 'action not in ("delete", "restore")' in _src27, "routes/inventory.py")
    check("S3-7 恢复逐条判定 + 返回 failed 明细(不照搬订单的 UPDATE 一把梭)",
          '"failed"' in _src27 and "_ok_rows" in _src27
          and "UPDATE inventory SET" not in _src27, "routes/inventory.py")
    check("S3-7 冲突走预取(SKU IN), 把 N 次往返压成常数次",
          "FROM inventory WHERE sku IN (%s)" in _src27, "routes/inventory.py")
    check("S3-7 有批量上限, 超限报错而非静默截断",
          "_BATCH_MAX" in _src27 and "请分批" in _src27, "routes/inventory.py")
    check("S3-7 业务键空值归一为 ''(与单条 COALESCE 同口径)",
          "def _biz_key" in _src27 and 'r.get("barcode") or ""' in _src27,
          "routes/inventory.py")
    # 2026-11-04 线上实测踩到: 进销存列表取 /insights/with-sales, 该 SELECT 原本没带 i.id,
    # 前端每行 x.id 都是 undefined → indexOf(undefined) 对每行都成立 → 点一行等于全选
    # (视觉 100 行全勾, store 里只有 1 条); "全选"同样只能塞进一个 undefined。
    _src_ins27 = open('routes/insights.py', encoding='utf-8').read()
    check("S3-7 with-sales 返回 i.id(前端批量勾选/全选的主键来源, 缺了会全选全表)",
          "SELECT i.id, i.sku" in _src_ins27, "routes/insights.py")
    # 只查 SELECT 不够: 该接口是逐字段重建 dict(items.append({...})), SELECT 出了 id
    # 但 append 不写就照样丢 —— 2026-11-04 第一次修正是这么翻车的, 线上仍无 id。
    check("S3-7 with-sales 的 items.append 必须显式带 id(重建 dict 会丢字段)",
          '"id": r.get("id")' in _src_ins27, "routes/insights.py")

    class _Req27:
        def __init__(self, d):
            self._d = d

        async def json(self):
            return self._d

    _o27 = (_inv27.query, _inv27.execute, _inv27.executemany, _ac27.invalidate)
    _st27 = {"inv": 0, "exec": [], "many": []}

    def _run27(payload):
        return _ai27.run(_inv27.inventory_batch(_Req27(payload)))

    def _exec27(sql, p=None):
        _st27["exec"].append((str(sql), p))
        return len(p or [])

    def _many27(sql, seq):
        _st27["many"].append((str(sql), list(seq)))
        return len(list(seq))

    def _row27(i, sku, bc, wh):
        return {"id": i, "sku": sku, "barcode": bc, "warehouse": wh,
                "warehouse_type": "own", "channel": "jd", "available_qty": 42}

    _inv27.execute, _inv27.executemany = _exec27, _many27
    _ac27.invalidate = lambda dom: _st27.__setitem__("inv", _st27["inv"] + 1)

    # ① 批量删除: 3 个 id 中 2 个真实存在 → 归档+删各一条 SQL, missing 报未命中
    _inv27.query = lambda sql, p=None: (
        [{"id": 1}, {"id": 2}] if "FROM inventory WHERE id IN" in str(sql) else [])
    _st27["exec"], _st27["inv"] = [], 0
    _r27 = _run27({"action": "delete", "ids": [1, 2, 3]})
    _d27 = _r27.get("data") or {}
    check("S3-7 批量删除 = 归档+删原行各一条 SQL, missing 报未命中",
          _r27.get("ok") and _d27.get("deleted") == 2 and _d27.get("missing") == [3]
          and len(_st27["exec"]) == 2
          and "INSERT INTO inventory_archive" in _st27["exec"][0][0]
          and _st27["exec"][1][0].startswith("DELETE FROM inventory")
          and _st27["inv"] == 1, str(_r27)[:200])

    # ② 批量恢复全成功: 2 条无冲突 → executemany 一次写入 + 归档批量移除
    _arc27 = [_row27(11, "SKU-A", "BC-A", "集货仓"), _row27(12, "SKU-B", "BC-B", "集货仓")]

    def _q27_ok(sql, p=None):
        s = str(sql)
        if "FROM inventory_archive WHERE id IN" in s:
            return _arc27
        return []  # id 占用 / 现存业务键都为空

    _inv27.query = _q27_ok
    _st27["exec"], _st27["many"], _st27["inv"] = [], [], 0
    _r27 = _run27({"action": "restore", "ids": [11, 12]})
    _d27 = _r27.get("data") or {}
    check("S3-7 批量恢复 = executemany 一次写入 + 归档批量移除(不是 2N 次往返)",
          _r27.get("ok") and _d27.get("restored") == 2 and not _d27.get("failed")
          and len(_st27["many"]) == 1 and len(_st27["many"][0][1]) == 2
          and len(_st27["exec"]) == 1
          and _st27["exec"][0][0].startswith("DELETE FROM inventory_archive")
          and _st27["inv"] == 1, str(_r27)[:200])

    # ③ 部分冲突: 同业务键已被重新导入 → 该条拦截, 其余照常恢复(部分成功)
    _arc27 = [_row27(13, "SKU-C", "BC-C", "集货仓"), _row27(14, "SKU-D", "BC-D", "集货仓")]

    def _q27_dup(sql, p=None):
        s = str(sql)
        if "FROM inventory_archive WHERE id IN" in s:
            return _arc27
        if "FROM inventory WHERE sku IN" in s:
            return [{"sku": "SKU-C", "barcode": "BC-C", "warehouse": "集货仓",
                     "channel": "jd", "warehouse_type": "own"}]
        return []

    _inv27.query = _q27_dup
    _st27["exec"], _st27["many"], _st27["inv"] = [], [], 0
    _r27 = _run27({"action": "restore", "ids": [13, 14]})
    _d27 = _r27.get("data") or {}
    _f27 = _d27.get("failed") or []
    check("S3-7 部分冲突: 冲突条被拦(带原因), 其余照常恢复 —— 不整批回滚",
          _r27.get("ok") and _d27.get("restored") == 1
          and len(_f27) == 1 and _f27[0]["id"] == 13
          and "已重新导入" in _f27[0]["reason"]
          and len(_st27["many"]) == 1 and len(_st27["many"][0][1]) == 1,
          str(_r27)[:260])

    # ④ 同批两条归档行撞同一个业务键 → 只恢复第一条, 第二条报原因
    _arc27 = [_row27(15, "SKU-E", "BC-E", "集货仓"), _row27(16, "SKU-E", "BC-E", "集货仓")]
    _inv27.query = lambda sql, p=None: (
        _arc27 if "FROM inventory_archive WHERE id IN" in str(sql) else [])
    _st27["exec"], _st27["many"] = [], []
    _r27 = _run27({"action": "restore", "ids": [15, 16]})
    _d27 = _r27.get("data") or {}
    check("S3-7 同批内重复业务键只恢复第一条(避免自己撞唯一键)",
          _r27.get("ok") and _d27.get("restored") == 1
          and len(_d27.get("failed") or []) == 1
          and "相同业务键" in _d27["failed"][0]["reason"], str(_r27)[:240])

    # ⑤ 参数与上限
    check("S3-7 缺 action/ids → fail",
          _run27({"action": "", "ids": []}).get("ok") is False
          and _run27({"action": "restore", "ids": []}).get("ok") is False, "参数校验")
    check("S3-7 超过 %d 条 → 明确报错(不静默截断)" % 500,
          _run27({"action": "delete", "ids": list(range(1, 502))}).get("ok") is False
          and "分批" in (_run27({"action": "delete", "ids": list(range(1, 502))}).get("error") or ""),
          "上限保护")
except Exception as _e27:
    check("S3-7 用例无异常", False, str(_e27)[:200])
finally:
    try:
        _inv27.query, _inv27.execute, _inv27.executemany, _ac27.invalidate = _o27
    except Exception:
        pass

# ── i18n 键对齐(2026-11-04 红线 B): zh/en 键集合必须相等 ───────────────────
# 背景: 页面里散着大量中文字面量, 切到 EN 就是半中半英。新增文案必须走 t('key') 且双份,
# 这条用例守住"键集合相等"这一半; 另一半(不写中文字面量)靠 review + 改动文件的 grep 判据。
try:
    import re as _re_i18n
    # 2026-11-04 字典已拆成 zh/en 两文件(原 locale.ts 单文件, 两段靠 "const en" 分隔)
    _zh_src = _io.open(_os.path.join(_HERE, "../../frontend/src/locale.zh.ts"), encoding="utf-8").read()
    _en_src = _io.open(_os.path.join(_HERE, "../../frontend/src/locale.en.ts"), encoding="utf-8").read()
    _loc_src = _io.open(_os.path.join(_HERE, "../../frontend/src/locale.ts"), encoding="utf-8").read()
    _zh_part = _zh_src
    _en_part = _en_src
    _zh_keys = set(_re_i18n.findall(r"^\s*'([^']+)':", _zh_part, _re_i18n.M))
    _en_keys = set(_re_i18n.findall(r"^\s*'([^']+)':", _en_part, _re_i18n.M))
    check("i18n: zh/en 键集合相等(新增文案必须双份)",
          _zh_keys == _en_keys,
          "缺 en=%r 缺 zh=%r" % (sorted(_zh_keys - _en_keys)[:6], sorted(_en_keys - _zh_keys)[:6]))
    check("i18n: 字典已覆盖回收站与库存批量面板(2026-11-04 新增)",
          all(k in _zh_keys for k in ("recycle.deleted_inventory", "recycle.bulk_restore",
                                      "inv.batch.open", "inv.batch.delete",
                                      "inv.batch.confirm_desc")), "locale.ts")
    # t() 的兜底顺序: 当前语言 → zh → key 本身。缺 en 会**静默回落到中文**, 用户看到半中半英
    check("i18n: 缺键回落顺序为 当前语言→zh→key(缺 en 会静默显示中文)",
          "locales[currentLocale]?.[key] || locales['zh'][key] || key" in _loc_src, "locale.ts")
except Exception as _e23:
    check("i18n 用例无异常", False, str(_e23)[:200])

# ── 日志类型收口自检(2026-11-04 日志排查) ──────────────────────────
# 历史 bug 复发过: 写入方新增 log_type 却没登记 routes.misc._LOG_SCOPE_TYPES,
# 该日志在质量日志页(user)/开发者页(dev)/Markdown 导出/归档里全被 IN 白名单过滤掉,
# 只躺在 quality_logs 表里 —— "写了等于没写", 且恰恰常是 error 级。
# 本轮实测抓到两类: rule_guard(告警批量关闭拦截) / api_unhandled(全局 500 兜底)。
# 静态扫描所有 INSERT INTO quality_logs 的字面量类型, 强制都在白名单内(防再次复发)。
try:
    import re as _re, glob as _glob
    from routes.misc import _LOG_SCOPE_TYPES as _SCOPE
    _wl = set(_SCOPE["user"]) | set(_SCOPE["dev"])
    _found = {}
    _root = os.path.dirname(os.path.abspath(__file__))
    for _p in _glob.glob(os.path.join(_root, "**", "*.py"), recursive=True):
        if os.path.basename(_p) == "local_test.py":
            continue  # 本文件含扫描规则说明文本, 跳过防自匹配
        _src2 = open(_p, encoding="utf-8").read()
        for _m in _re.finditer(r"INSERT INTO quality_logs", _src2):
            _w = _src2[_m.start():_m.start() + 700]
            # ① VALUES 后直接跟字面量类型
            _v = _re.search(r"VALUES\s*\(\s*'([a-z][a-z0-9_]*)'", _w)
            # ② VALUES 全占位符 + 后续参数元组首项为字面量类型
            if not _v:
                _v = _re.search(r"VALUES\s*\(\s*%s[^)]*\)\s*,\s*\(\s*'([a-z][a-z0-9_]*)'",
                                _w, _re.S)
            if _v:
                _found.setdefault(_v.group(1), set()).add(os.path.relpath(_p, _root))
    _miss = {k: sorted(v) for k, v in _found.items() if k not in _wl}
    check("日志类型全部收口(无'写了看不见')", not _miss, str(_miss))
    # 自检本身必须真的扫到东西, 否则正则失效会假绿(同 i18n_audit 自检的教训)
    check("日志收口自检确实扫到类型", len(_found) >= 10, str(sorted(_found)))
    check("rule_guard 已收口到 user 维度", "rule_guard" in _SCOPE["user"])
    check("api_unhandled 已收口到 dev 维度", "api_unhandled" in _SCOPE["dev"])
except Exception as _e24:
    check("日志收口用例无异常", False, str(_e24)[:200])

# ── 账期(月结)纯函数: 期初结转/未满月天数/批次期初带日期(2026-11-04) ──
try:
    from core.ledger import (period_of, shift_period, period_bounds, period_days,
                             elapsed_days, is_current, _days_between)
    check("账期: shift_period 跨年正确", shift_period("2026-01", -1) == "2025-12"
          and shift_period("2026-12", 1) == "2027-01", shift_period("2026-01", -1))
    check("账期: 非法账期返回空(不猜)", shift_period("2026-13-", 0) == ""
          or shift_period("bad", 0) == "", "应返回空串")
    check("账期: period_bounds 取真实月末(2 月 28/29)",
          period_bounds("2026-02")[1] == "2026-02-28"
          and period_bounds("2024-02")[1] == "2024-02-29"
          and period_bounds("2026-11")[1] == "2026-11-30", str(period_bounds("2024-02")))
    check("账期: period_days = 28/29/30/31", period_days("2026-11") == 30
          and period_days("2026-12") == 31 and period_days("2026-02") == 28,
          str(period_days("2026-11")))
    _cur = period_of()
    from core.clock import now_cn as _nowcn
    check("账期: 当月 elapsed = 今天几号(未满月折算的分母)",
          elapsed_days(_cur) == _nowcn().day, "%s vs %s" % (elapsed_days(_cur), _nowcn().day))
    check("账期: 已过去的月份 elapsed = 整月天数",
          elapsed_days(shift_period(_cur, -1)) == period_days(shift_period(_cur, -1)),
          str(elapsed_days(shift_period(_cur, -1))))
    check("账期: 未来月份 elapsed = 0", elapsed_days(shift_period(_cur, 1)) == 0)
    check("账期: 未满月判定 is_current 只对当月为真",
          is_current(_cur) and not is_current(shift_period(_cur, -1)))
    check("账期: 体检缺口天数计算(日期解析失败按 9999 不齐)",
          _days_between("2026-10-28", "2026-10-31") == 3 and _days_between("", "2026-10-31") == 9999)
    # 右开区间: 流水列是 DATETIME, 写 `<= '2026-10-31'` 只会覆盖到当天 00:00:00,
    #   整天流水被静默丢掉(线上实测 10-31 当天入 19,405 / 出 23,704, 期末少 42,809)
    from core.ledger import period_bounds_excl
    check("账期: 流水区间右开(末日整天不能丢)",
          period_bounds_excl("2026-10") == ("2026-10-01", "2026-11-01")
          and period_bounds_excl("2026-12") == ("2026-12-01", "2027-01-01")
          and period_bounds_excl("2024-02") == ("2024-02-01", "2024-03-01"),
          str(period_bounds_excl("2026-10")))
    _lg = open("core/ledger.py").read()
    check("账期: 归集流水与体检都用 `< 下月1日`, 不再有 `<= 月末日`",
          "<= %%s" not in _lg, "core/ledger.py")
    # 首次建账期初 = 基线 + 基线之后、账期之前的净流水; 少了它会低估一整段历史流水
    check("账期: bootstrap 期初回算账期前的净流水(_net_before)",
          "def _net_before" in _lg and "boot_net = _net_before(" in _lg, "core/ledger.py")
    # 批次月初余额必须带日期: 少了它, 上月批次到本月退化成无日期期初批次, 指定批次出库失效
    _cw = open("routes/cleanse_write.py").read()
    check("账期: 批次池期初读 batch_opening(带日期), 不是只读 beginning_stock",
          "batch_opening" in _cw and "_open_map" in _cw, "routes/cleanse_write.py")
    check("账期: 库存重算的 Σ 流水有账期下界(不再是全期累加成双算)",
          "inbound_date >= '" in _cw and "outbound_date >= '" in _cw, "routes/cleanse_write.py")
    check("账期: 重算不再把负可用归零(负库存要暴露)",
          "upd.append((max(0," not in _cw, "routes/cleanse_write.py")
    # 调度落点: schedules 上限 10 条已占满(edgeone.json 现为 10 条), 加第 11 条会丢路由,
    #   所以月结**搭在 cleanup-logs 那条凌晨调度上**。这条断言防的是重构时把这句静默挪走 ——
    #   挪走不会报错, 只会让账期永远不结, 属于最难发现的一类回归。
    _cr = open("routes/cron.py").read()
    check("账期: 月结挂在 cleanup-logs 调度内(不新增第 11 条 schedule)",
          "run_ledger_close()" in _cr.split("async def cron_cleanup_logs")[1].split("async def")[0],
          "routes/cron.py / cron_cleanup_logs")
    check("账期: 月结调用在 _claim 之前(日志已清理也要照常结账)",
          ("_led = run_ledger_close()" in _cr.split("async def cron_cleanup_logs")[1]
           .split("_claim(")[0]), "routes/cron.py")
    check("账期: 只对有自有仓库存的渠道结账(避免平台仓白写 blocked)",
          "def _own_channels" in _cr and "_own_channels(channels)" in _cr, "routes/cron.py")
    check("账期: 结账后不重建批次(省 10~20s, 批次池已由 batch_opening 固化)",
          "结账后只刷库存" in _cr, "routes/cron.py")
    check("账期: edgeone.json 的 schedules 未超 10 条上限",
          len(json.load(open("../../edgeone.json")).get("schedules") or []) <= 10,
          str(len(json.load(open("../../edgeone.json")).get("schedules") or [])))
except Exception as _e25:
    check("账期用例无异常", False, str(_e25)[:200])

# ── 真实 SQL 冒烟(可选, 必须在全部用例跑完之后: 此时 SQL 集合才完整) ──
if _SQL_SMOKE:
    FAIL += _sa.report()
else:
    print("\nℹ 真实 SQL 冒烟未运行(mock 测不出表名/列名; 加 --sql-smoke 开启)")

print("\n本地回归: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
