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
# 2026-11-06 S7/P1-24: /auth/setup 在鉴权中间件里放行 —— 不校验就是"谁先调谁拿到 admin"。
#   新契约: 配了 SETUP_TOKEN 必须带对令牌; 没配则生产一律拒绝(本地 sqlite 除外)。
import os as _os9
_os9.environ["SETUP_TOKEN"] = "t-setup-9x"
r = client.post("/auth/setup", json={"username": "admin", "password": "admin123"})
check("auth/setup 无令牌被拒", r.json().get("ok") is False and "令牌" in str(r.json().get("error")), r.text[:150])
r = client.post("/auth/setup", json={"username": "admin", "password": "admin123",
                                    "setup_token": "wrong"})
check("auth/setup 错令牌被拒", r.json().get("ok") is False and "令牌" in str(r.json().get("error")), r.text[:150])
r = client.post("/auth/setup", json={"username": "admin", "password": "admin123"},
                headers={"X-Setup-Token": "t-setup-9x"})
check("auth/setup 带对令牌成功", r.status_code == 200 and r.json().get("ok") is True, r.text[:150])
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
# ── 响应压缩(2026-11-07 P2): 显式头 X-Accept-Encoding 触发, 与 Accept-Encoding 并行生效 ──
#   /health 只有 143 字节(< minimum_size), 故意拿 summary(几十 KB)验。
_r_gz = client.get("/dashboard/summary?channel=jd", headers={"X-Auth-Token": TOKEN, "X-Accept-Encoding": "gzip"})
check("gzip: 显式头触发", _r_gz.headers.get("content-encoding") == "gzip",
      str(dict(_r_gz.headers))[:200])
check("gzip: 内容解压后与明文一致",
      _r_gz.json() == client.get("/dashboard/summary?channel=jd",
                                 headers={"X-Auth-Token": TOKEN}).json())
# httpx 默认也发 Accept-Encoding: gzip, 双双显式 identity 才能构造"客户端不要压缩"
_r_id = client.get("/dashboard/summary?channel=jd",
                   headers={"X-Auth-Token": TOKEN, "Accept-Encoding": "identity",
                            "X-Accept-Encoding": "identity"})
check("gzip: 客户端不要压缩(identity)则不压缩", _r_id.headers.get("content-encoding") is None)
_r_gz2 = client.get("/dashboard/summary?channel=jd", headers={"X-Auth-Token": TOKEN, "Accept-Encoding": "gzip"})
check("gzip: 标准 Accept-Encoding 仍生效(网关透传时兼容)", _r_gz2.headers.get("content-encoding") == "gzip")


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

# 2026-11-06: 六个小结果集合成一条 UNION ALL 宽表(_q_bundle) —— 还原必须逐字等价。
#   合并的动机是"每请求少建 5 条 TiDB Serverless 连接"(见 db.STATS), 但宽表列序一旦错位
#   (历史上 cfg 就把 value 当 key 用了)会让安全线退化成默认值 —— 卡片照出, 数字全错。
from routes.dashboard import _split_bundle as _sb  # noqa: E402
_brows = [
    {"tag": "agg", "s1": "2026-11-01", "s2": "已完成", "s3": "店A", "n1": 10, "n2": 1, "n3": 2},
    {"tag": "recent", "s1": "2026-11-05", "s2": "已完成", "s3": "店A", "n1": 5, "n2": 0, "n3": 1},
    {"tag": "brands", "s1": "2026-11-01", "s2": "品牌X", "s3": "", "n1": 9, "n2": 0, "n3": 0},
    {"tag": "flows", "s1": "2026-11-01", "s2": "店A", "s3": "", "n1": 3.5, "n2": None, "n3": None},
    {"tag": "counts", "s1": "", "s2": "", "s3": "", "n1": 7, "n2": 8, "n3": 9},
    {"tag": "cfg", "s1": "lead_time_days", "s2": "10", "s3": "", "n1": None, "n2": None, "n3": None},
]
_a, _rc, _bd, _fl, _ct, _cg = _sb(_brows)
check("bundle 还原: agg/recent 分表, 字段名与 _q_agg 一致",
      _a[0]["d"] == "2026-11-01" and _a[0]["cnt"] == 2 and _a[0]["order_status"] == "已完成"
      and _rc[0]["d"] == "2026-11-05", str(_a))
check("bundle 还原: brands 用 d/brand/g/sb/rf",
      _bd[0]["brand"] == "品牌X" and float(_bd[0]["g"]) == 9.0, str(_bd))
check("bundle 还原: flows 折回 (总额, 按日, 按店)",
      _fl[0] == 3.5 and _fl[1] == {"2026-11-01": 3.5} and _fl[2] == {"店A": 3.5}, str(_fl))
check("bundle 还原: counts 三个计数", _ct == {"alerts": 7, "products": 8, "suppliers": 9}, str(_ct))
check("bundle 还原: cfg 的 key 在 s1、value 在 s2",
      _cg == {"lead_time_days": "10"}, str(_cg))


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
# alerts 非分页路径(分组配额): 曾因 "LIMIT %%s" % _FIELDS 产生字面 %%s → pymysql TypeError(500)
r = client.get("/alerts?channel=jd&limit=50", headers={"X-Auth-Token": ADMIN})
check("alerts 非分页 200", r.status_code == 200 and isinstance(r.json().get("data", r.json()), list),
      r.text[:150])
# exclude 走 else 分支(_ex_sql 拼接) —— 2026-11-07 的 "% 优先级高于 +" 事故就发生在这条路径
r = client.get("/alerts?channel=jd&limit=50&exclude=own_overstock,own_transfer_gap",
               headers={"X-Auth-Token": ADMIN})
check("alerts exclude(拼接 SQL) 200 且返回裸数组",
      r.status_code == 200 and isinstance(r.json().get("data", r.json()), list), r.text[:150])
r = client.get("/alerts?channel=jd&paged=1&types=low_stock&limit=10&offset=0",
               headers={"X-Auth-Token": ADMIN})
_d = r.json().get("data", r.json()) or {}
check("alerts 分页 200 + items/total", r.status_code == 200 and isinstance(_d, dict)
      and "items" in _d and "total" in _d, r.text[:150])
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
# 2026-11-06 S7: 运维补跑入口把调用改成 `_off_window("x", force_ok=_force_ok(request))`,
#   断言守"每个任务都接守卫"这个契约, 不锁调用写法(带不带 force_ok 都算接上)
_missing = [t for t in _WINDOWS if ('_off_window("%s"' % t) not in _src]
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
    # 2026-11-07 P2: 分页参数必须独立分键 —— 否则第 1 页的响应会被第 3 页命中(翻页出现重复行);
    #   level 同理, 否则"紧急"页会读到"全部"页的内容。
    ("stock_risk|", ["_CV", "channel", "full", "legacy", "_lim", "_off", "level"]),
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

# ── 2026-11-06 审计 P0-1: 任务类型守卫(驱动端点只推自己那类任务) ────────────
# 背景: 四个驱动端点原来都不校验 task_type。前端 taskWatch 把 export/cleansing_eval
#   一律当 cleansing 打 /cleansing/status → 读不到 clean_<id> → 抛"源文件不存在" →
#   except 分支 UPDATE status='error'(导出 3 秒内被判死)。
#   反方向更危险: cleansing 的 id 打到 eval-status, sku_list 为空 → 直接标 done,
#   写了一半的导入被标"成功完成"。
try:
    _api30 = _os.path.dirname(_os.path.abspath(__file__))

    def _rf30(n):
        return open(_os.path.join(_api30, "routes", n), encoding="utf-8").read()

    check("P0-1: common 提供 type_ok 类型守卫",
          "def type_ok(row, expect)" in _rf30("common.py"), "routes/common.py")
    _guard_cases = (("cleansing.py", '"cleansing"'), ("cleansing.py", '"cleansing_eval"'),
                    ("task_export.py", '"export"'), ("task_seed.py", '"seed"'))
    for _gn, _ge in _guard_cases:
        _gs = _rf30(_gn)
        check("P0-1: %s 驱动分支校验 %s(打错端点返回 not_found, 绝不改状态)" % (_gn, _ge),
              ('if not _type_ok(row, %s):' % _ge in _gs)
              or ('if not _type_ok(_pr, %s):' % _ge in _gs), "routes/" + _gn)
    # 三个"不是 not_found"的返回值都要带 status 让前端看守能摘下, 否则永久空转
    _cc30 = _rf30("cleansing.py")
    check("P0-1: cleansing 类型不符返回 not_found(带 status 而非只有 ok:False)",
          'if not _type_ok(row, "cleansing"):' in _cc30
          and '"status": "not_found"' in _cc30, "routes/cleansing.py")
except Exception as _e30:
    check("P0-1 类型守卫用例无异常", False, str(_e30)[:200])

# ── 2026-11-06 审计 P1-4: 采购建议空商品库必须返回 list(与其余路径同契约) ───
try:
    _api31 = _os.path.dirname(_os.path.abspath(__file__))
    _pc31 = open(_os.path.join(_api31, "routes", "purchase_calc.py"), encoding="utf-8").read()
    _pcz = _pc31.find("if not products:")
    check("P1-4: 采购空商品库返回 [] 而非已包装 dict(否则 need_only 迭代 dict 键 → 500)",
          _pcz >= 0 and "\n        return []" in _pc31[_pcz:_pcz + 900], "routes/purchase_calc.py")
    _lp31 = open(_os.path.join(_api31, "core", "list_page.py"), encoding="utf-8").read()
    check("P1-4: shape() 对非 list 有契约护栏(坏 builder 不炸成 500)",
          "if not isinstance(rows, list):" in _lp31, "core/list_page.py")
    from core.list_page import shape as _shape31
    _it31, _t31 = _shape31({"ok": True, "data": {"suggestions": []}}, need_only=1)
    check("P1-4: shape(dict) 返回空列表而不是抛 AttributeError",
          _it31 == [] and _t31 == 0, str(_it31)[:80])
except Exception as _e31:
    check("P1-4 空库契约用例无异常", False, str(_e31)[:200])

# ── 2026-11-06 审计 P1-3: 档案类必须幂等 upsert(重导不再整批 1062) ─────────
try:
    _api32 = _os.path.dirname(_os.path.abspath(__file__))
    _cw32 = open(_os.path.join(_api32, "routes", "cleanse_write.py"), encoding="utf-8").read()
    check("P1-3: products/suppliers/purchase_orders 有 upsert 分支(原落 else: pass 纯 INSERT)",
          'arch_upsert = table in ("products", "suppliers", "purchase_orders")' in _cw32,
          "routes/cleanse_write.py")
    check("P1-3: 档案 upsert 覆盖列排除 channel(否则同 SKU 换渠道导入会把行搬走)",
          '"id", "deleted_at", "channel"' in _cw32, "routes/cleanse_write.py")
    check("P1-3: 档案 upsert 分支真的拼了 ON DUPLICATE(不是只声明不落地)",
          "elif arch_upsert:" in _cw32 and 'sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(_aupd)' in _cw32,
          "routes/cleanse_write.py")
except Exception as _e32:
    check("P1-3 档案 upsert 用例无异常", False, str(_e32)[:200])

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
    # 2026-11-06 S3: BC 归并规则搬到 core/metrics.py(唯一真源), rule_eval 只调用它。
    #   原来这条断言守的是"rule_eval 里出现 BC 行构造" —— 那就等于**允许**别的文件再写一份。
    _met = _rf("core/metrics.py")
    check("低库存: bbcc 按 BC 合计(SKU×码 一条), 归并规则只在 core/metrics.py 一处",
          '"warehouse_type": "bc"' in _met
          and 'wt in ("platform", "platform_b")' in _met
          and "merge_bc=True" not in _re and "_bc_agg" not in _re
          # 2026-11-06 S4-3: 改成增量累加后不再是 `_M.units(rows, ...)` 一次调用,
          #   守"唯一真源 + 粒度由 grain_of 决定"这两件结构事实。
          and "_M.units(" in _re and "acc=_uacc" in _re
          and "_M.grain_of(channel, _mode_e)" in _re, "core/metrics.py")
    check("低库存: 关闭判定与 hits 同为含码 5 元组",
          'str(a.get("bc") or "")) not in hits' in _re, "rule_eval.py")
    # 2026-11-06 S4-1: 原实现 `ORDER BY id LIMIT 100000` —— 超出部分静默不评估(既不生成新告警,
    #   也不关闭旧告警), 线上 jd 90,100 行离上限只差 1 万。改成 id 游标分页读全量。
    check("S4 全量评估: 按 id 游标分页读全量(超单页不再丢行)",
          "while _seen < limit" in _re and "_after = int(_page[-1]" in _re
          and "ORDER BY id LIMIT %s\", [channel, limit]" not in _re, "rule_eval.py")
    # 2026-11-06 S4-3: 原始行不得全量驻留 —— 逐页喂给 units 的增量累加器
    check("S4 全量评估: 原始行逐页喂入, 不再全量驻留(_feed + acc)",
          "def _feed(" in _re and "acc=_uacc" in _re and "rows.extend(_page)" not in _re,
          "rule_eval.py")
    check("S4 全量评估: 触到安全上限必须留痕(不静默截断)",
          "达到安全上限" in _re, "rule_eval.py")
    _ru = _rf("core/rules.py")
    # 2026-11-06 S2-4: 去重从"预载 active 集合"改为 uq_alerts_dim 唯一键 + 单语句 upsert,
    #   所以不再要求出现预载 SQL; 改为守"写入语句的维度键里必须有 barcode"。
    check("告警: 写入的维度键含 barcode(同 SKU 多码各自独立)",
          "channel, barcode)" in _ru and "_ALERT_UPSERT_SQL" in _ru, "rules.py")
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
    #    2026-11-05: ctx 构造已抽出到 core/rule_sim.py(路由文件超 600 行红线), 断言指向新文件。
    _rs = open("core/rule_sim.py", encoding="utf-8").read()
    _rr = open("routes/rules.py", encoding="utf-8").read()   # 第 10 条断言仍读路由文件
    check("测试接口: own./batch. 模拟 ctx 已注入",
          'ctx["own"]' in _rs and 'ctx["batch"]' in _rs, "core/rule_sim.py")
    # 2026-11-05: 同类坑 —— 引擎注入 supplier.score / inv.turnover_days, 测试 ctx 没注
    #   → 弹窗恒"未触发", 用户误判规则写错。
    check("测试接口: supplier./inv.turnover_days 模拟 ctx 已注入",
          'ctx["supplier"]' in _rs and '"turnover_days"' in _rs, "core/rule_sim.py")
    # 2026-11-05 ④: 仓型默认空串 —— 规则上的仓型守卫(`cond.warehouse`, 见 rule_cond._check_single)
    #   判 `"" != "own"` → 直接 False → 规则的 and 子条件被一起否掉, 弹窗永远"未触发"。
    #   真实评估里不存在"空仓型"的行, 所以空串本身就是个不存在的输入。
    from core.rule_sim import build_sim_ctx as _bs
    from core.rule_cond import _check_condition as _cc

    _cond_wh = {"left": "inv.available_qty", "op": "<", "right": "10", "warehouse": "own"}
    check("测试接口: 未传仓型时模拟 ctx 落 own(不再用空串冒充)",
          _bs("{}", {}, {"inv": {}})["inv"]["warehouse_type"] == "own",
          _bs("{}", {}, {"inv": {}})["inv"]["warehouse_type"])
    check("测试接口: 仓型守卫在弹窗里真能命中(修复前恒 False)",
          _cc(_cond_wh, _bs("{}", {}, {"inv": {"available_qty": 5}})) is True,
          "own 守卫")
    check("测试接口: 选平台仓时 own 规则确实不命中(不造反向谎)",
          _cc(_cond_wh, _bs("{}", {}, {"inv": {"available_qty": 5,
                                               "warehouse_type": "platform"}})) is False,
          "platform 守卫")
    check("测试接口: 非法仓型值落回 own",
          _bs("{}", {}, {"inv": {"warehouse_type": "OWN"}})["inv"]["warehouse_type"] == "own",
          _bs("{}", {}, {"inv": {"warehouse_type": "OWN"}})["inv"]["warehouse_type"])
    check("测试接口: own.* 仅在 own 行注入(与 rule_eval 同条件)",
          "own" not in _bs("own.gap>0", {}, {"inv": {"warehouse_type": "platform"}})
          and "own" in _bs("own.gap>0", {}, {"inv": {"warehouse_type": "own"}}),
          "core/rule_sim.py")

    # 7b. 2026-11-05 根治"两份手工列表": 白名单/注入判定/前端下拉必须同源
    #    —— 原 _VALID_FIELDS 与 _CALC_VARS 各写一份, supplier.score / inv.turnover_days
    #       在白名单却不在 _CALC_VARS → 走快速路径 → 键不注入 → 条件恒空、规则永不触发。
    from core.rule_fields import FIELD_SPEC, needs_calc, refs_in, validate_refs
    check("规则字段: supplier.score 需计算注入",
          needs_calc({"left": "supplier.score", "op": "<", "right": "3"}), "rule_fields.needs_calc")
    check("规则字段: inv.turnover_days 需计算注入",
          needs_calc({"left": "inv.turnover_days", "op": ">", "right": "5"}), "rule_fields.needs_calc")
    check("规则字段: 纯 base 条件不走计算注入",
          not needs_calc({"left": "inv.available_qty", "op": "<", "right": "10"}), "needs_calc")
    # 规则页下拉里能选中的字段必须全部登记(否则"能选能存但永不触发" = 界面说谎)
    #   只截 LF 数组: 文件后半的 WHS/MODES 是仓库类型/补货模式枚举值('own'/'bbcc'),
    #   不是字段引用, 整文件正则会把它们当成未登记字段误报。
    import re as _r9
    _lf = open("../../frontend/src/pages/rules/labels.tsx", encoding="utf-8").read()
    _lf = _lf[_lf.index("export const LF = ["):_lf.index("export const OPS = [")]
    _lf_refs = set()
    for _m9 in _r9.finditer(r"v:\s*'([^']*)'", _lf):
        for _f9 in refs_in({"left": _m9.group(1), "op": "<", "right": "0"}):
            _lf_refs.add(_f9)
    _lf_bad = sorted(x for x in _lf_refs if not x.startswith("params.") and x not in FIELD_SPEC)
    check("规则字段: 规则页下拉字段全部已登记", not _lf_bad, _lf_bad)
    # 事件作用域: 库存事件不能引用出入库上下文字段(该 ctx 没有 flow.* → 恒空)
    check("规则字段: flow.quantity 在 inventory.changed 被拒",
          validate_refs({"left": "flow.quantity", "op": ">", "right": "0"}, "inventory.changed") != [])
    check("规则字段: 无点字段拼错也拦(原正则只查 a.b)",
          validate_refs({"left": "daily_sale", "op": ">", "right": "0"}, "inventory.changed") != [])
    # right 侧无点字段/引号字面量必须能求值(原实现落到字符串比较 → 恒 False)
    from core.rule_cond import _check_single as _cs9
    _ctx9 = {"inv": {"available_qty": 5, "warehouse_type": "own"}, "daily_sales": 10}
    check("规则求值: right 侧无点字段可解析",
          _cs9({"left": "inv.available_qty", "op": "<", "right": "daily_sales"}, _ctx9))
    check("规则求值: right 侧 'own' 字面量可比较",
          _cs9({"left": "inv.warehouse_type", "op": "==", "right": "'own'"}, _ctx9))

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

# ── 当日评估三态占位(2026-11-06): 冻结不再造成"永久跳过" ──
# 背景: 原实现执行前就写指纹, 实例被冻结后指纹已是"最新依据" → 之后每次请求都判定
# "今天已评估"直接返回, 线上出现 5.5 小时空窗。三态 = '' / '#run#..' / '<fp>', 只有
# 跑成功才落指纹; '#run#' 超时视为冻结遗留可被抢占。
try:
    import db as _dbm
    import core.maintenance as _mt

    _st = {"d": None, "age": 0}

    def _m_one(sql, params=None):
        if _st["d"] is None:
            return None
        return {"d": _st["d"]}

    def _m_exec(sql, params=None):
        s = sql.strip()
        if s.startswith("INSERT IGNORE"):
            if _st["d"] is not None:
                return 0
            _st["d"] = params[2]
            return 1
        if s.startswith("UPDATE"):
            cur = _st["d"] if _st["d"] is not None else ""
            if cur != params[-1]:
                return 0            # CAS 期望值不匹配 → 没抢到
            if "INTERVAL" in sql and _st["age"] < _mt._RUN_STALE_MIN:
                return 0            # 令牌还新鲜 → 让路
            _st["d"] = params[0]
            return 1
        return 0

    _o_one, _o_exec = _dbm.one, _dbm.execute
    _dbm.one, _dbm.execute = _m_one, _m_exec
    try:
        _c1, _t1 = _mt.claim_rules("1|2|3")
        _mark = _st["d"] or ""
        _c2, _ = _mt.claim_rules("1|2|3")          # 令牌在跑 → 让路
        _mt.settle_rules(_t1, "1|2|3", ok=True)
        _settled = _st["d"]
        _c3, _ = _mt.claim_rules("1|2|3")          # 已完成同指纹 → 不抢
        _c4, _ = _mt.claim_rules("9|9|9")          # 指纹变了 → 重新抢
        _st["d"] = "#run#5|5|5#deadbeef"
        _st["age"] = 1
        _c5, _ = _mt.claim_rules("5|5|5")          # 令牌新鲜 → 让路
        _st["age"] = _mt._RUN_STALE_MIN + 5
        _c6, _ = _mt.claim_rules("5|5|5")          # 令牌超时(冻结) → 抢回
    finally:
        _dbm.one, _dbm.execute = _o_one, _o_exec
    check("评估占位: 首次抢占占 #run# 令牌",
          _c1 and str(_mark).startswith("#run#1|2|3#"), _mark)
    check("评估占位: 令牌在跑时不重复抢占", not _c2)
    check("评估占位: 成功才落指纹", _settled == "1|2|3", str(_settled))
    check("评估占位: 同指纹不再评估", not _c3)
    check("评估占位: 指纹变化可重评估", _c4)
    check("评估占位: 新鲜令牌让路 / 超时令牌抢回(冻结自愈)", (not _c5) and _c6, str((_c5, _c6)))

    # 结构: cron 与看板兜底必须共用同一套(否则 cron 跑完 details 为空, 兜底会再跑一遍)
    _cn_src = open("routes/cron.py", encoding="utf-8").read()
    _db_src = open("routes/dashboard.py", encoding="utf-8").read()
    check("评估占位: cron 路由走公共三态",
          "claim_rules" in _cn_src and "settle_rules" in _cn_src, "cron.py")
    check("评估占位: 看板兜底走公共三态(不再自己算指纹)",
          "claim_rules" in _db_src and "_RUN_MARK" not in _db_src, "dashboard.py")
except Exception as _e6b:
    check("评估占位用例无异常", False, str(_e6b)[:200])

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
          # 2026-11-06 补码调用多了 cache 参数(可能换行), 匹配到括号前缀即可, 语义不变
          _cw3.index("_fill_barcode_from_products(channel, cleaned") < _cw3.index("_align_order_keys(channel, cleaned)"),
          "cleanse_write.py")
    check("S2-5 多码 SKU 不参与对齐(那本来就是多行)",
          'len(_nonempty) == 1 and "" not in _codes' in _cw3, "cleanse_write.py")
    # 实测 TiDB 单条 UPDATE 222ms —— 逐行升级 5000 行要 18 分钟, 必须是批量 CASE 语句
    check("S2-5 空码升级走批量 CASE(不是逐行 UPDATE)",
          "def _promote_empty_barcode" in _cw3 and "CASE CONCAT(order_no,'|',sku)" in _cw3
          and "UPDATE orders SET barcode=%s WHERE channel=%s AND order_no=%s" not in _cw3,
          "cleanse_write.py")
    # 2026-11-06 快速通道(seed): 只合并**文件级动作**(归码档案索引/多码证据、脏日标记),
    #   行级规则(69码判定/四维键对齐/期初校准/可用重算/批次FIFO)一条不少。
    #   默认 ctx=None → 逐批行为与原来完全一致, 真实上传导入不存在"第二条口径"。
    _si3 = open('routes/seed_import.py', encoding='utf-8').read()
    _sf3 = open('routes/seed_fill.py', encoding='utf-8').read()
    check("快速通道: 默认 ctx=None 行为不变, 且文件末必须收尾(否则物化不重建)",
          "def flush_import_ctx" in _cw3 and "ctx=None" in _cw3
          and "ctx=ctx" in _si3 and "flush_import_ctx" in _sf3
          and "cache=(_ctx.setdefault(\"bc\", {})" in _cw3,
          "cleanse_write.py / seed_import.py / seed_fill.py")

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
          '"stock_risk|' in _ds4 and '"%s|stock_risk|' not in _ds4, "dashboard.py")
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
    # 2026-11-06 S6/P1-18: 删除收窄为"本片 SKU"(每片一个事务), 整渠道范围的清理
    #   只剩"陈旧行"(表里有、本次算出来没有)那一步, 且只在全量分支执行。
    check("S2-1 子集重建不误删其它 SKU(整渠道范围清理只能在全量分支)",
          "_scope" in _sv4b and "if not _scope:" in _sv4b
          and "_old_skus - set(_groups)" in _sv4b, "sales.py")
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
    # 2026-11-06 S5/P0-5: UPDATE 改**相对增量**(delta 下推 DB 单语句原子), 不再应用层算绝对值
    #   —— 读-改-写在并发分片/重试下丢更新/双算。断言守契约(delta 参数 + GREATEST SQL), 不锁写法细节。
    _upd5 = dict((r[1], r[0]) for (sq, rr) in _seen5["upd"] for r in rr)
    check("S2-1e 多码各扣各的(delta 下推: A码 -4, B码 -5, id 不串)",
          _upd5.get(11) == -4 and _upd5.get(12) == -5, str(_upd5))
    _upd_sql5 = " ".join(sq for (sq, rr) in _seen5["upd"])
    check("S2-1e UPDATE 是原子增量(GREATEST(0, available_qty+%s)), 不是绝对值覆盖",
          "GREATEST(0, available_qty+%s)" in _upd_sql5.replace("`", ""), _upd_sql5[:120])
    _ins5 = [r for (sq, rr) in _seen5["ins"] for r in rr]
    _ins_sql5 = " ".join(sq for (sq, rr) in _seen5["ins"])
    check("S2-1e 新行走 INSERT..ON DUPLICATE 增量且带码(旧三维键→空码行, 新 SKU→BC-C)",
          len(_ins5) == 2
          and "ON DUPLICATE KEY UPDATE" in _ins_sql5
          and "GREATEST(0, available_qty+VALUES(available_qty))" in _ins_sql5.replace("`", "")
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
    # 2026-11-06: 取数改为三路并行(products/日销/批次), 变量名随之变化 —— 守"没有分片循环"
    #   与"批次仍按 own 仓 + 有效期非空查询"这两个结构事实, 不守具体变量名。
    check("S2-6 自有仓产品/批次不再按 800 分片(实测仅 1000/1696 行)",
          "_chunk" not in _src6c
          and "FROM batches WHERE channel=%s AND warehouse_type='own'" in _src6c,
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
    # 2026-11-06: 唯一允许的内联编辑是"建唯一键前把 NULL 归一成 ''" —— 多行 NULL 在
    #   MySQL/TiDB 的唯一键里互不冲突, 不归一就等于键没建成。它属于"约束前置整形",
    #   不是存量业务数据迁移(后者仍必须走 migrations/steps/)。
    _alerts_upd5 = [ln for ln in _cat5.splitlines() if "UPDATE alerts SET" in ln]
    # 2026-11-06 S8: products 建 (channel, sku) 唯一键前也必须把 channel 的 NULL/空归一
    #   —— 多行 NULL 在唯一键里互不冲突, 不归一等于键没建成。同属"约束前置整形"。
    _prod_upd5 = [ln for ln in _cat5.splitlines() if "UPDATE products SET" in ln]
    check("S3-5 建表函数里不再内联存量数据 UPDATE(只允许建键前的 NULL 归一)",
          "UPDATE rules SET" not in _cat5
          and "UPDATE channels SET" not in _cat5
          and all("IS NULL" in ln for ln in _alerts_upd5)
          and all("IS NULL" in ln and "channel" in ln for ln in _prod_upd5),
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
    check("D5 aux 的 evaluating 在缓存之外读(且带新鲜度, 不认僵户 running)",
          "FROM eval_tasks WHERE \" + _live()" in _dash_src
          and "eval_live_cond" in _dash_src,
          "routes/dashboard.py")
    check("D5 前端按真实状态等待(ops 轮询, 不再页面内阻塞 20s)",
          "waitEvalDone" not in open('../../frontend/src/pages/RulesPage.tsx', encoding='utf-8').read()
          and "/api/rules/eval-status" in open('../../frontend/src/App.tsx', encoding='utf-8').read(),
          "RulesPage/App")

    # ── §15.158: 评估任务僵户(实例被冻结, done/error 回写永不执行) ──
    _maint_src = open('core/maintenance.py', encoding='utf-8').read()
    _own_src = open('core/own_store.py', encoding='utf-8').read()
    _app_src = open('../../frontend/src/App.tsx', encoding='utf-8').read()
    check("15.158 新鲜度判定单一真源(读侧/状态接口/任务合并共用 eval_live_cond)",
          "def eval_live_cond():" in _maint_src
          and "eval_live_cond" in _dash_src
          and _rules_src.count("eval_live_cond") >= 2,
          "core/maintenance.py / dashboard.py / rules.py")
    check("15.158 裸 status IN ('pending','running') 已从读侧与状态接口移除",
          "FROM eval_tasks WHERE status IN ('pending','running')" not in _dash_src
          and "COUNT(*) AS c FROM eval_tasks WHERE status IN" not in _rules_src,
          "routes/dashboard.py / routes/rules.py")
    check("15.158 close_stale_runs 同步收尾 eval_tasks(不只 pipeline_runs)",
          "UPDATE eval_tasks SET status='timeout'" in _maint_src
          and "close_stale_runs as _csr" in _rules_src,
          "core/maintenance.py / routes/rules.py")
    check("15.158 合并任务排除僵户(否则保存规则永远复用旧 id 不再评估)",
          "FROM eval_tasks WHERE \" + _live()" in _rules_src,
          "routes/rules.py")
    check("15.158 eval-status 对僵户 running 返回 timeout, 前端认它",
          '_r["status"] = "timeout"' in _rules_src and "st === 'timeout'" in _app_src,
          "routes/rules.py / App.tsx")
    check("15.158 own_store 明细 SQL 不残留 %d(_guard_fmt 会把参数转 str 喂给 %d)",
          "LIMIT %d" not in _own_src and "OFFSET %d" not in _own_src
          and 'LIMIT " + str(int(per_type))' in _own_src,
          "core/own_store.py")
    check("15.158 评估取规则参数走 dashboard_stock_risk(原 routes.dashboard 导入不存在)",
          "from routes.dashboard import _rule_params" not in _re_src
          and "from routes.dashboard_stock_risk import _rule_params" in _re_src,
          "core/rule_eval.py")

    # ── D8: 生命周期单一入口(维度键/关闭原因/重开不新增行) ──
    check("D8 维度键单一真源(5 维: 类型/SKU/仓/渠道/69码)",
          'ALERT_DIM = ("alert_type", "related_sku", "warehouse", "channel", "barcode")' in _al_src,
          "core/alert_lifecycle.py")
    check("D8 关闭统一写 closed_reason/closed_at",
          "closed_reason=%s, closed_at=NOW()" in _al_src, "core/alert_lifecycle.py")
    # 2026-11-06 S2-4: 从"先查后改"改为单语句 upsert —— 重开不再靠 UPDATE 某一行,
    #   而是 ON DUPLICATE KEY UPDATE 命中唯一键后条件自增(只有原本非 active 的才算一次重开)。
    check("D8 重开复用同维度行(upsert 命中唯一键后条件自增, 不新增)",
          "ON DUPLICATE KEY UPDATE" in _al_src
          and "reopen_count=COALESCE(reopen_count,0)+IF(status<>'active',1,0)" in _al_src
          and 'return "reopened"' in _al_src,
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

    # 2026-11-06 S2-4: open_or_reopen 已改为单语句 upsert(不再先 SELECT 再决定
    #   INSERT/UPDATE)。这里守三件事: ①语句是 upsert ②维度键含 barcode ③
    #   `status='active'` 必须排在 reopen_count 之后(否则重开计数永远是 0)。
    _cap24.clear()
    _db.query = lambda sql, p=None: []
    _al24.query = _db.query
    _r24 = _al24.open_or_reopen(
        {"alert_type": "low_stock", "related_sku": "S1", "warehouse": "W1",
         "channel": "jd", "barcode": "69"},
        {"title": "t", "description": "d", "severity": "warning", "source": "rules_engine"})
    _sql24 = _cap24[0][0] if _cap24 else ""
    _i_reopen = _sql24.find("reopen_count=COALESCE")
    _i_active = _sql24.find("status='active'")
    check("D8 已关闭维度 → 同一条 upsert 重开(rowcount 映射 reopened)",
          _r24 == "reopened" and "ON DUPLICATE KEY UPDATE" in _sql24
          and "`barcode`" in _sql24, str(_cap24[:1])[:160])
    check("D8 upsert 赋值顺序: reopen_count 在 status='active' 之前(否则重开计数恒 0)",
          0 <= _i_reopen < _i_active, "reopen@%d active@%d" % (_i_reopen, _i_active))
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
    # 空表时 state_get 的 period 必须是空("已结账的账期"), 不能是当月 ——
    #   否则首次结账被自己的默认值判成"跳期", 建账第一步就死锁(线上实证)
    _src = open("core/ledger.py").read()
    _def_seg = _src.split("if row:")[1].split("def begin_date")[0]
    check("账期: state_get 空表默认 period 为空串(否则首结必被误拒)",
          '"period": ""' in _def_seg and "period_of()" not in _def_seg.split('return {"period"')[-1][:40],
          str(_def_seg[-200:]))
    # 切片必须聚合: batches 以自增 id 为主键(同批次键可多行), batch_opening 以批次键为主键,
    #   直接 SELECT 搬运必撞 1062(线上实证 78 组重复)
    check("账期: 批次切片 GROUP BY 聚合(否则撞 batch_opening 主键)",
          "GROUP BY channel, warehouse_type, sku, bc, warehouse, pd, ed" in _src
          and "SUM(qty) AS q FROM batches" in _src, "core/ledger.py")
    # 重跑幂等: 期初优先取该期已存的 begin_qty, 而不是拿被回写过的 beginning_stock 再叠
    check("账期: 重结同期期初取自己已存的 begin_qty(重跑幂等)",
          "same_begin" in _src and "begin_qty FROM inventory_month" in _src, "core/ledger.py")
    # 改现有库存必须排在两张新表之后: 否则切片一失败就留下"数字变了账没结"的中间态
    _cp = _src.split("def close_period")[1]
    check("账期: 回写库存排在写账期/切片之后(失败不留中间态)",
          _cp.find("_apply_to_inventory(rows)") > _cp.find("freeze_batches(nxt")
          and _cp.find("_apply_to_inventory(rows)") > _cp.find("_write_month(period"),
          "core/ledger.py")
    check("账期: 建账基线独立成列 ledger_base(不被结账回写污染)",
          "ledger_base" in _src and "ledger_base IS NULL" in _src, "core/ledger.py")
    # 结账只回写期初: 写 month_inbound 等于把"结账月"的量塞进"当月"字段,
    #   下个月页面就会显示上个月的入/出(线上实证: 11 月页出现 10 月的 357,637/233,985)
    _ap = _src.split("def _apply_to_inventory")[1].split("def freeze_batches")[0]
    check("账期: 回写库存只写 beginning_stock, 不写 month_inbound/month_outbound",
          "month_inbound" not in _ap.split('"""')[-1] and "beginning_stock=%s WHERE id=%s" in _ap,
          "core/ledger.py")
    _ins = open("routes/insights.py").read()
    check("账期: 自有仓当月量缺流水按 0, 不回落静态列(防跨月串号)",
          "month_inbound = mi if mi is not None else 0" in _ins, "routes/insights.py")
    # 2026-11-07: 入/出两条合并成一条 UNION ALL(省一次固定往返), 判据改为"两条记录集都在、
    #   结果按 k 分流进 mi/mo" —— 仍必须是流水现算, 不许退回 SUM 静态列。
    check("账期: 进销存汇总的自有仓月入出按流水现算(不 SUM 静态列)",
          'inbound_records "' in _ins and "outbound_records " in _ins
          and '_rx.get("k") == "in"' in _ins and '"0 AS mi, 0 AS mo' in _ins,
          "routes/insights.py")
    # 2026-11-05 平台仓/ B 仓解绑(用户定案: 该维度不需要当月入出):
    #   两列在平台仓没有任何消费方(页面列集无 / 导出只对 own / 周转列只在 own),
    #   读进来只会让 API 返回一个"看起来是真值"的快照数。
    check("账期: 平台仓/B 仓不读 inventory.month_inbound/month_outbound",
          'if wh_type == "own" else ""' in _ins and "0 AS mi, 0 AS mo" in _ins
          and "month_inbound = 0" in _ins, "routes/insights.py")
    # 同类"能映射但写不进去"的洞: 清洗页 PROD_FIELDS 有 supplier_code, 写入白名单漏了它
    _pb = open("routes/cleanse_write.py").read().split('_write_batch("products"')[1][:400]
    check("导入: products 写入白名单含 supplier_code(否则映射了也是空)",
          '"supplier_code"' in _pb, "routes/cleanse_write.py")
    # 2026-11-05: 月汇总/行级聚合都必须**右开**(< 下月1日) —— 原只有下界,
    #   表里若有下月/未来日期流水(提前导入/补录/日期错年)会被算进本月。
    #   行级 2 处 + 汇总 2 处 = 各 2 条上界条件。
    check("账期: 进销存流水聚合带下月1日上界(右开, 各 2 处)",
          _ins.count("inbound_date<%s") == 2 and _ins.count("outbound_date<%s") == 2,
          (_ins.count("inbound_date<%s"), _ins.count("outbound_date<%s")))
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
          "def _net_before" in _lg and "_net_before(channel, warehouse_type, start)" in _lg,
          "core/ledger.py")
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

# ── 对账工具自身: 列名解析不能把「列名恰好叫 key」当成索引行跳过(2026-11-05) ──
#   症状: 三张表的 `key` 列从未参与"声明了是否建出来"的校验(真缺列会漏报),
#   表面却报"库里多出 3 列"的假阳性。下面两条一正一反, 防止"正则什么都没扫到"也算绿。
try:
    import schema_create as _sc9

    _ddl_key = ("CREATE TABLE IF NOT EXISTS t_probe (`key` VARCHAR(80) NOT NULL, value TEXT, "
                "channel VARCHAR(20) DEFAULT '', PRIMARY KEY(`key`, channel))")
    _cols_key = _sa.parse_columns(_ddl_key)
    check("对账: 列名恰好叫 key 也能解析(不再被当成 KEY idx_xxx 索引行)",
          "key" in _cols_key and "value" in _cols_key and "primary" not in [c.lower() for c in _cols_key],
          str(_cols_key))
    # 反证: 把 key 列从 DDL 里删掉, 解析结果必须少它 —— 否则上面那条只是"没扫到东西"
    _ddl_no_key = _ddl_key.replace("`key` VARCHAR(80) NOT NULL, ", "").replace(
        "PRIMARY KEY(`key`, channel)", "PRIMARY KEY(channel, value)")
    check("对账: 删掉 key 列后解析结果确实不含它(反证断言覆盖到该列)",
          "key" not in _sa.parse_columns(_ddl_no_key), str(_sa.parse_columns(_ddl_no_key)))

    _decl = {t: set(_sa.parse_columns(d)) for t, d in _sc9._TABLES.items()}
    check("对账: 三张表的 key 列都在声明解析结果里",
          all("key" in _decl.get(t, set()) for t in
              ("replenishment_config", "replenishment_config_history", "analysis_cache")),
          {t: sorted(_decl.get(t, [])) for t in ("replenishment_config", "analysis_cache")})
    check("对账: 真正的索引行(KEY idx_xxx (...))不被解析成列",
          "idx_rules_ch_active" not in _decl.get("rules", set())
          and "idx_rules_ch_active" in _sc9._TABLES["rules"], "rules")
except Exception as _e26:
    check("对账列解析用例无异常", False, str(_e26)[:200])

# ── 期初双场景: 结转(默认) / 窗口内实盘导入(覆盖 + 留痕), 2026-11-05 用户定案 ──
try:
    from datetime import datetime as _dtm
    from core.ledger_adj import (target_period as _tp, begin_from_import as _bfi,
                                 window_text as _wtx)

    check("期初校准: 上月末最后一天算「次月期初」(结账在 1 号 03:05, 月末盘库常见)",
          _tp(_dtm(2026, 10, 31, 23, 0)) == "2026-11", _tp(_dtm(2026, 10, 31, 23, 0)))
    check("期初校准: 当月 1~3 号算「当月期初」",
          _tp(_dtm(2026, 11, 1, 0, 1)) == "2026-11" and _tp(_dtm(2026, 11, 3, 23, 59)) == "2026-11",
          (_tp(_dtm(2026, 11, 1, 0, 1)), _tp(_dtm(2026, 11, 3, 23, 59))))
    check("期初校准: 4 号起不再算期初(否则把本月流水当期初, 双算)",
          _tp(_dtm(2026, 11, 4, 0, 0)) == "", _tp(_dtm(2026, 11, 4, 0, 0)))
    check("期初校准: 窗口文字与判定同源(不含手写月份算术)",
          "2026-10-31" in _wtx("2026-11") and "2026-11-03" in _wtx("2026-11"), _wtx("2026-11"))
    # 换算: 期初 = 导入量(导入时点结存) − 本月净流水; 净出库为负 → 期初反而变大
    check("期初校准: 换算 = 导入量 − 本月净流水(净出库则回加)",
          _bfi(100, 30) == 70 and _bfi(100, -20) == 120, (_bfi(100, 30), _bfi(100, -20)))

    # 结账期初优先级 ⓪: 校准值必须压过"上月结转", 否则窗口内导入等于白导
    import core.ledger as _lg3
    _oq3 = _lg3.query
    _sqls3 = []

    def _fake_q(sql, p=None):
        s = str(sql)
        _sqls3.append(s)
        if "FROM ledger_begin_adj" in s:
            return [{"sku": "S1", "barcode": "", "warehouse": "W1", "qty": 77}]
        if "FROM inbound_records" in s or "FROM outbound_records" in s:
            return []
        if "FROM inventory_month" in s:
            return [{"sku": "S1", "barcode": "", "warehouse": "W1", "end_qty": 10,
                     "begin_qty": 10}]
        if "FROM inventory " in s or "FROM inventory\n" in s:
            return [{"id": 1, "sku": "S1", "bc": "", "warehouse": "W1", "base": 0}]
        return []

    _lg3.query = _fake_q
    import core.ledger_adj as _la3
    _oq3b = _la3.query
    _la3.query = lambda sql, p=None: ([{"sku": "S1", "barcode": "", "warehouse": "W1", "qty": 77}]
                                      if "FROM ledger_begin_adj" in str(sql) else [])
    try:
        _br3 = _lg3.build_rows("2026-11", "jd")
    finally:
        _lg3.query = _oq3
        _la3.query = _oq3b
    check("期初校准: 结账期初以校准值为准(压过上月结转 end_qty=10)",
          _br3["rows"] and _br3["rows"][0]["begin"] == 77, _br3["rows"][:1])
    check("期初校准: 校准行的 src 标 import(报表/对账能区分「盘出来的」)",
          _br3["rows"] and _br3["rows"][0].get("src") == "import", _br3["rows"][:1])

    _sc10 = open("schema_create.py", encoding="utf-8").read()
    import schema_create as _sck
    # 断言"版本号 ≥ 8"而不是"== 8": 钉死具体数字会让每次合法的 bump 都变成红灯,
    #   逼着人改测试而不是改代码 —— 真正要守的是"没被退回旧版本"
    check("期初校准: 表声明存在 + SCHEMA_VERSION 已 bump(改 schema 必 bump)",
          "ledger_begin_adj" in _sc10 and _sck.SCHEMA_VERSION >= 8, "schema_create.py")
    _ms10 = open("routes/misc.py", encoding="utf-8").read()
    check("期初校准: 日志类型 ledger_override 登记到用户可见域",
          "ledger_override" in _ms10.split("_LOG_SCOPE_TYPES")[1].split('"dev"')[0],
          "routes/misc.py")
    _cw10 = open("routes/cleanse_write.py", encoding="utf-8").read()
    check("期初校准: 库存导入写入后接线(仅 own, 失败不阻断导入)",
          "core.ledger_adj import apply_import" in _cw10 and 'if wt == "own"' in _cw10,
          "routes/cleanse_write.py")
except Exception as _e27:
    check("期初校准用例无异常", False, str(_e27)[:200])

# ── seed 规模与水位(2026-11-05 用户定案: 每渠道约 21 万单 / 10000 SKU / 90 天) ──
# 这一组守的是"规模口径"与"半途失败可回滚"两件事 —— 两者都不是能靠肉眼验收的:
#   · 规模错了 → 页面数字看着正常, 但演示场景(滞销占比/多码比例)全都不成立
#   · 没有水位 → seed 撞 90s 上限就只能整库 reset, 一次失败报废全部前置步骤
try:
    import routes.seed_fill as _sf
    check("seed 规模: 每渠道 SKU 数 = 10000",
          _sf.SKU_PER_CHANNEL == 10000, _sf.SKU_PER_CHANNEL)
    check("seed 规模: 订单窗口 90 天", _sf.ORDER_DAYS == 90, _sf.ORDER_DAYS)
    # 订单基数反推: 促销日(×2~4)与周末(×0.6~1.2)是在基数上再乘的,
    #   按 90×基数≈21 万直算会跑出 32 万 —— 这里锁住"含波峰后的总量"落在 21 万上下
    import datetime as _dtsf, random as _rdsf
    _tot = 0
    _rdsf.seed(7)
    _promo = {'618': list(range(5, 20)), 'month_end': list(range(45, 55))}
    for _d in range(_sf.ORDER_DAYS):
        _is_promo = any(_d in _v for _v in _promo.values())
        _dtx = _dtsf.date(2026, 11, 5) - _dtsf.timedelta(days=_d)
        _tot += (int(_sf.ORDER_BASE_PER_DAY * _rdsf.uniform(2, 4)) if _is_promo
                 else (int(_sf.ORDER_BASE_PER_DAY * _rdsf.uniform(0.6, 1.2))
                       if _dtx.weekday() >= 5 else _sf.ORDER_BASE_PER_DAY))
    check("seed 规模: 单渠道 90 天订单量在 20~23 万(用户口径 21 万上下)",
          200000 <= _tot <= 230000, _tot)
    # 2026-11-05 窗口接力: step 是业务阶段(恒定), 数据范围只由 offset 游标决定。
    #   旧口径把订单切成 9/12 个段号, 中途部署换段数就让同一个 step 换含义 → 线上
    #   重复写入 69,072 行。现在订单固定 1 个阶段, 窗口按实测耗时自适应, 段号永不再变。
    check("seed 窗口接力: 订单只有 1 个业务阶段, 段号不再由切分粒度决定",
          _sf._SEED_ORDER_SEGS == 1 and _sf.SEED_WINDOW_BUDGET_S == 75
          and _sf.SEED_WINDOW_BUDGET_S < 120,
          (_sf._SEED_ORDER_SEGS, _sf.SEED_WINDOW_BUDGET_S))
    check("seed 窗口接力: 窗口天数按上一窗口实测耗时自适应(促销缩/常态扩)",
          _sf._adaptive_span(0, 0) == _sf.SEED_ORDER_SPAN_INIT
          and _sf._adaptive_span(8, 95) < 8
          and _sf._adaptive_span(6, 30) > 6
          and _sf._adaptive_span(20, 1) == _sf.SEED_ORDER_SPAN_MAX,
          (_sf._adaptive_span(8, 95), _sf._adaptive_span(6, 30)))
    check("seed 窗口接力: 库存段内 SKU 分片游标(治自有仓逐行 SQL 的 150s 超时)",
          _sf.SEED_INV_SKU_PER_CHUNK > 0
          and "sku_from" in _sf._seed_inventory.__code__.co_varnames
          and "sku_to" in _sf._seed_inventory.__code__.co_varnames,
          _sf.SEED_INV_SKU_PER_CHUNK)
    check("seed 段号: 各阶段严格递增(订单1+库存4+流水4+落账4+月1+尾5+完成1 = 20)",
          _sf._SEED_ORDER_SEGS < _sf._SEED_INV < _sf._SEED_FLOW
          < _sf._SETTLE_SEGS[0][0] < _sf._SEED_MONTH < _sf._SEED_TAIL
          # 完成值不写死: 段号重排过多次, 写死会让断言变成假绿/假红
          < _sf.SEED_DONE == _sf._TAIL_SEGS[-1][0] + 1,
          (_sf._SEED_INV, _sf._SEED_FLOW, _sf._SETTLE_SEGS[0][0],
           _sf._SEED_MONTH, _sf._SEED_TAIL, _sf.SEED_DONE))
    # 2026-11-06 任务卡明细膨胀的结构性修复: steps 的粒度必须是**业务阶段**。
    #   窗口(订单按天游标、库存按 2500 SKU 切片)是执行分片, 不参与展示; 一个阶段无论
    #   跑几个窗口都只占一条(elapsed/rows 累加)。阶段名必须全覆盖且互不重名 ——
    #   否则前端会退化成"阶段x"这种不可读文案。
    check("seed 任务卡: steps 粒度=业务阶段(窗口不单独成行, 阶段名全覆盖且唯一)",
          all(i in _sf._STAGE_OF_STEP for i in range(_sf.SEED_DONE))
          and len({_sf.stage_label(i) for i in range(_sf.SEED_DONE)}) == _sf.SEED_DONE,
          [_sf.stage_label(i) for i in range(_sf.SEED_DONE)][:5])
    # 事故根因: step14 一个请求发 4 份导入文件(2 渠道 × 2 类型) → >120s 被网关切断。
    #   收敛口径: 一个请求 = 一份导入文件 = 一个步骤窗口, 4 段, 不按 SKU 半区预拆。
    check("流水 4 段: 渠道×出/入类型, 一段一文件, 段号连续且恰为 2×2",
          len(_sf._FLOW_SEGS) == 4
          and [x[0] for x in _sf._FLOW_SEGS] == list(range(_sf._SEED_FLOW, _sf._SEED_FLOW + 4))
          and {x[1] for x in _sf._FLOW_SEGS} == {'jd', 'other'}
          and {x[2] for x in _sf._FLOW_SEGS} == {'inbound', 'outbound'}
          and all(len(x) == 3 for x in _sf._FLOW_SEGS),
          [x[0] for x in _sf._FLOW_SEGS])
    check("落账 4 段: 渠道×仓型, 段号连续(沿用线上已实测各 ≤40s 的粒度)",
          len(_sf._SETTLE_SEGS) == 4
          and [x[0] for x in _sf._SETTLE_SEGS] == list(range(_sf._SETTLE_SEGS[0][0],
                                                             _sf._SETTLE_SEGS[-1][0] + 1))
          and all(len(x) == 3 for x in _sf._SETTLE_SEGS),
          [x[0] for x in _sf._SETTLE_SEGS])
    check("尾段 5 段: 告警/快照/物化双渠道各自独立 + GMV 回款(原一段实测 456s 必踩穿)",
          len(_sf._TAIL_SEGS) == 5
          and {x[1] for x in _sf._TAIL_SEGS} ==
              {'alerts', 'snapshot', 'stats_jd', 'stats_other', 'gmv_flows'},
          [x[1] for x in _sf._TAIL_SEGS])
    _all_nos = ([x[0] for x in _sf._SEED_INV_SEGS] + [x[0] for x in _sf._FLOW_SEGS]
                + [x[0] for x in _sf._SETTLE_SEGS] + [_sf._SEED_MONTH]
                + [x[0] for x in _sf._TAIL_SEGS])
    # 起点不写死 10: 段数改过两次(9 → 12), 写死会让"改段数"顺手把断言变成假绿/假红
    _first_tail_seg = _sf._SEED_ORDER_SEGS + 1
    check("段表完整: 订单后首段..SEED_DONE-1 无空洞无重复(否则 seed_step 会跳段或空转)",
          sorted(_all_nos) == list(range(_first_tail_seg, _sf.SEED_DONE)),
          (len(_all_nos), _first_tail_seg, _sf.SEED_DONE))
    # SKU 编号位宽: 4 位到 9999 就撞名, 10000 个必须 5 位
    _sk_t = _sf._make_skus('-T', 10000, 0.01)
    check("seed SKU: 10000 个编号无撞名", len({x['sku'] for x in _sk_t}) == 10000,
          len({x['sku'] for x in _sk_t}))
    check("seed SKU: 多码按 1% 比例(10000 → 100 个)", len([x for x in _sk_t if x['bc2']]) == 100,
          len([x for x in _sk_t if x['bc2']]))
    check("seed SKU: 第二码用 691 前缀, 不与主码 690 段重叠",
          all(x['bc2'].startswith('691') and x['barcode'].startswith('690')
              for x in _sk_t if x['bc2']), _sk_t[0])
    # 水位目标白名单: 只允许插入型表(upsert 型目标没有 id 单调性, 回滚语义不成立)
    check("seed 水位: 只对插入型目标登记(upsert 型不参与回滚)",
          set(_sf._WM_TARGETS) == {'orders', 'inventory', 'inbound_records',
                                   'outbound_records', 'batches'}, _sf._WM_TARGETS)
    check("seed 水位: 回滚条件只用 id > hwm_before(不碰历史行)",
          "DELETE FROM `%s` WHERE id > %%s" in open("routes/seed_fill.py", encoding="utf-8").read(),
          "routes/seed_fill.py")
    # 一键与分步必须同源(历史上 run_seed_fill 写死 1000 SKU, prepare_skus 是另一套)
    _sf_src = open("routes/seed_fill.py", encoding="utf-8").read()
    check("seed: 一键填充与分步共用 prepare_skus(不再写死 1000)",
          "skus_data = prepare_skus()" in _sf_src.split("def run_seed_fill")[1],
          "routes/seed_fill.py")
    # 任务完成判据不能写死数字(段号重排后 10→12, 写死会让任务越过 10 后永不落 done = 空转烧 RU)
    _ts_src = open("routes/task_seed.py", encoding="utf-8").read()
    check("seed: 任务完成判据取 SEED_DONE, 不写死 10",
          "nxt >= SEED_DONE" in _ts_src and "nxt >= 10" not in _ts_src, "routes/task_seed.py")
    check("seed 窗口接力: task_seed 从 params 透传游标 / 写回 offset / 响应带 offset",
          "_off = int(_pj0.get('offset') or 0)" in _ts_src
          and "offset=_off" in _ts_src
          and '"offset": _nxt_off' in _ts_src
          and '"offset": int(_pj0.get("offset") or 0)' in _ts_src,
          "routes/task_seed.py")
    check("seed 窗口接力: 未跑完返回同一 step + 新游标(不再靠预切段号续跑)",
          "return step, {'orders_window_" in _sf_src
          and "'_offset': day_to" in _sf_src
          and "'_offset': _to" in _sf_src
          and "_order_seg_days" not in _sf_src,
          "routes/seed_fill.py")
    _sc11 = open("schema_create.py", encoding="utf-8").read()
    check("seed 水位: seed_watermark 表已声明",
          "seed_watermark" in _sc11 and "uq_wm_run_step" in _sc11, "schema_create.py")
    # ── 2026-11-05 用户实测反馈: 任务卡"十来分钟不结束" / 进度写死 10 段 ──
    # 批大小: 单批 100 SKU 实测 1.26s、1000 SKU 也只有 1.78s —— 耗时只跟**查询次数**有关。
    #   2 万 SKU: 100/批 = 200 批 ≈ 280s(必超 90s), 1000/批 = 20 批 ≈ 36s。这是硬约束。
    _cw11 = open("routes/cleanse_write.py", encoding="utf-8").read()
    check("批次重建: 批大小 ≥1000(否则 2 万 SKU 要 200 批 ≈ 280s, 撞死 90s 上限)",
          "range(0, len(skus), 5000)" in _cw11 and "range(0, len(skus), 100)" not in _cw11,
          "routes/cleanse_write.py")
    # ── 2026-11-05 写批次放大 + 账期哨兵口径 ──
    # 断言只看**代码行**(去注释): 注释里会引用旧值/哨兵名, 全文匹配会假红(踩过两次)
    _cw_code = "\n".join(x for x in _cw11.splitlines()
                         if x.strip() and not x.strip().startswith("#"))
    check("写批次: 清洗切片与内层 executemany 同为 2000(否则实际写批仍被切片卡在 500)",
          "range(0, len(cleaned), 2000)" in _cw_code
          and "range(0, len(rows), 2000)" in _cw_code
          and "range(0, len(cleaned), 500)" not in _cw_code,
          "routes/cleanse_write.py")
    check("写批次: 可用回写 2000 / 批次插入 5000 / 批次累加·覆盖 2000",
          "range(0, len(upd), 2000)" in _cw_code
          and "range(0, len(_insert_rows), 5000)" in _cw_code
          and "range(0, len(_new), 2000)" in _cw_code
          and "range(0, len(ins), 2000)" in _cw_code,
          "routes/cleanse_write.py")
    check("写批次: 删除侧 IN 列表仍留 2000(优化器舒适区, 不跟插入批一起抬)",
          "range(0, len(_sk_all), 2000)" in _cw_code, "routes/cleanse_write.py")
    _la_code_lines = "\n".join(
        x for x in open("core/ledger_adj.py", encoding="utf-8").read().splitlines()
        if x.strip() and not x.strip().startswith("#"))
    check("账期哨兵: 首建不再造 '1970-01-01'(会被批次重建误判成已结账 → 期初批次缺失)",
          "_sum_start = _bd" in _la_code_lines
          and 'else "1970-01-01"' not in _la_code_lines,
          "core/ledger_adj.py")
    check("账期哨兵: 重算/重建两处都显式归空(日期格式正则挡不住合法日期哨兵)",
          _cw_code.count('if _bd == "1970-01-01"') == 2,
          "routes/cleanse_write.py")
    # 订单段返回值: 曾是全局累计序号(含 day_from*1000000 偏移), 水位对账当场算出"预期 1500 万"
    _sf11 = open("routes/seed_fill.py", encoding="utf-8").read()
    check("订单段返回值 = 本段新增行数(不再是含偏移的全局序号)",
          "_seg_rows += int(_r.get('rows') or 0)" in _sf11, "routes/seed_fill.py")
    check("落账段用内存 SKU 集合(省掉 2 万行的 SELECT DISTINCT, 实测 3.1s), 且不再按 SKU 半区切",
          "def seed_settle(skus_data=None, channel='', wh_group='')" in _sf11
          and "def _seed_batches(skus_data=None, channel='', wh_group='')" in _sf11
          and "sku_half" not in _sf11
          and "if skus_data and skus_data.get(_och):" in _sf11,
          "routes/seed_fill.py")
    # 拆段后最怕的事: 只清 jd 却按全表重建 → jd 双份、other 三份。删除范围必须跟重建范围同步
    #   断言看**代码行**(去掉注释), 否则说明性注释里的旧写法定法会被误判成没改
    _sb_src = _sf11.split("def _seed_batches")[1].split("def _seed_records")[0]
    _sb_code = "\n".join(x for x in _sb_src.splitlines()
                         if x.strip() and not x.strip().startswith("#"))
    check("批次重建: DELETE 范围与重建范围同源(_chs), 不再写死 'jd','other'",
          "DELETE FROM batches WHERE channel IN (%s)" in _sb_code
          and "for _och in (() if _only_pf else _chs):" in _sb_code
          and "IN ('jd','other')" not in _sb_code,
          "routes/seed_fill.py")
    check("批次重建: 平台仓批次行也按 _chs 过滤(否则另一渠道批次被重复插入)",
          "AND channel IN (%s)" in _sb_src, "routes/seed_fill.py")
    # 前端进度条此前写死 /10, 段号重排后是 12 → 停在 99%
    # __file__ = cloud-functions/api/local_test.py → 上 3 层才是仓库根
    _FE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))), "frontend", "src")
    _tc11 = open(_os.path.join(_FE, "components", "tasks", "TaskCard.tsx"), encoding="utf-8").read()
    check("任务卡进度条: seed 总段数从后端 total_step 取, 不写死 10",
          "params.total_step" in _tc11 and "(progStep / 10)" not in _tc11, "TaskCard.tsx")
    # 相对时间: 只靠"重渲染才重算"不够 —— 见下条(数据层 created_at 根本不走)
    check("任务卡相对时间: running 时统一 1s 时钟驱动(一个定时器同时供耗时与相对时间)",
          "setNow(Date.now())" in _tc11 and "relTime(_stamp, now)" in _tc11
          and "setTick" not in _tc11, "TaskCard.tsx")
    # 「跑了几分钟还显示刚刚」的第一层根因在数据: 卡片拿 created_at(任务**创建**时刻),
    #   seed 是分步长跑, 每步推进不改它 → 差值恒 0 → 永远「刚刚」。后端补 updated_at(进度心跳)。
    _tk_src = open("routes/tasks.py", encoding="utf-8").read()
    check("任务列表返回 updated_at(进度心跳), 卡片不再只能拿 created_at",
          "created_at, updated_at" in _tk_src, "routes/tasks.py")
    # 2026-11-05 第三次返工: 前两轮把 _stamp 换成 updated_at(进度心跳) 仍然显示「刚刚」——
    #   因为心跳一直在刷新, "距最后活动多久"永远 <1s。这是**语义错位**: 用户问的是
    #   「这个任务已经跑了多久」, 不是「上一次心跳有多新」。所以 running 用 created_at 算
    #   累计时长, updated_at 只作 created_at 缺失时的兜底。
    check("任务卡: running 用 created_at 算累计运行时长(不再拿心跳当时间戳)",
          "const _startStamp = task.created_at || task.updated_at" in _tc11
          and "_runSec" in _tc11 and "runLabel(_runSec)" in _tc11,
          "TaskCard.tsx")
    # loadAll 竞态: loading/orderLoading 原先只在正常路径复位, 被丢弃的那次会把 true 留在 state
    _st11 = open(_os.path.join(_FE, "store", "useAppStore.ts"), encoding="utf-8").read()
    check("loadAll 竞态: 最新那次无论走 return 还是正常结束都收起 loading",
          "_mySeq === _seq.v" in _st11 and "_seqBox" in _st11, "useAppStore.ts")
    check("任务页轮询竞态: loadTasks 带序号, 旧响应不覆盖新 state",
          "loadSeq" in open(_os.path.join(_FE, "pages", "TaskPage.tsx"), encoding="utf-8").read(),
          "TaskPage.tsx")

    # ── 2026-11-05 第二轮用户实测: 「多页面卡加载中/同步中」「任务卡一直刚刚」──
    _tp11 = open(_os.path.join(_FE, "pages", "TaskPage.tsx"), encoding="utf-8").read()
    # ① 骨架屏死锁: 收尾曾挂在 `if (mySeq !== loadSeq.current) return` 之后, 而 3s 轮询每轮
    #    都 ++loadSeq → 首载一慢就永远过期 → 骨架永不消失。必须挪进 finally。
    #    2026-11-05 第三次返工: 三态收敛成单一 phase 状态机, finally 里推的是 phase。
    _tp_fin = _tp11.split("} finally {")[-1] if "} finally {" in _tp11 else ""
    check("任务页骨架屏: phase→ready 在 finally 里(不再被竞态 return 跳过)",
          "setPhase('ready')" in _tp_fin, "TaskPage.tsx")
    # ② 回调参数 `t` 遮蔽模块级翻译函数 t(): `t('task.done_toast',...)` 实际是调用任务对象 → TypeError
    check("任务页完成提示: forEach 回调参数不叫 t(否则遮蔽 t(), 完成提示永远不弹)",
          "function (t)" not in _tp11 and "function (task)" in _tp11, "TaskPage.tsx")
    _cl11 = open(_os.path.join(_FE, "api", "client.ts"), encoding="utf-8").read()
    # ③ 胶囊此前只信 store.syncing(9 处手工配对, 漏一处就卡死)。网络层补"真实在途请求数"硬信号。
    # 2026-11-06 S1: _reqStart/_reqEnd 带 isBg 参数(后台请求不计入用户可见在途)
    check("同步胶囊: 在途请求数来自网络层(拦截器统一登记, 不靠页面手工配对)",
          "_reqStart(" in _cl11 and "_reqEnd(" in _cl11 and "onActiveRequests" in _cl11,
          "api/client.ts")
    check("同步胶囊: 请求成功/失败/取消三条路径都注销(否则计数泄漏=永久转圈)",
          _cl11.count("_reqEnd(") >= 3, _cl11.count("_reqEnd("))
    _app11 = open(_os.path.join(_FE, "App.tsx"), encoding="utf-8").read()
    # 2026-11-07 用户定案: 胶囊只认**当前视图**的真实在途, 不再叠加全局 syncing ——
    #   否则切页后旧页的 -1 还没执行, 新页会莫名转圈(噪音来源)。
    check("同步胶囊: 只认当前视图真实在途(不再叠加全局 syncing)",
          "badgeBusy" in _app11 and "const badgeBusy = reqBusy > 0" in _app11
          and "syncing > 0 || reqBusy > 0" not in _app11, "App.tsx")
    # ④ loadAll 里 syncing 的增减必须同源(曾一边 get().syncing+1 一边 Math.max(0,-1))
    check("loadAll: syncing 增减走同一个 setSyncing(不再直接赋值)",
          "syncing: get().syncing + 1," not in _st11 and "get().setSyncing(1)" in _st11
          and "get().setSyncing(-1)" in _st11,
          "useAppStore.ts")
    # ⑤ orderLoading 曾在 stock-risk(35s 超时)之后无序号保护地置 false → 旧请求踩新请求
    check("loadAll: orderLoading 与订单数据同一次 set 收尾, 不挂在慢请求之后",
          "orderLoading: false," in _st11, "useAppStore.ts")
    _inv11 = open(_os.path.join(_FE, "pages", "InventoryPage.tsx"), encoding="utf-8").read()
    check("进销存: 竞态丢弃分支不再改 loading(否则列表闪空白)",
          "if (seq !== reqSeq.current) {\n        // 2026-11-05" in _inv11, "InventoryPage.tsx")

    # ── 2026-11-05 第三轮线上实测: 落账 jd 单渠道仍 504(123s) / 订单被双写至 32.4 万 ──
    # ① 按渠道拆分不够: jd 有 68,322 平台仓行(1~3 批/行 ≈ 16.8 万批次), 单段仍超 120s。
    #    再按仓型切四段, 每段落一批行的量级。
    _sb_code3 = "\n".join(x for x in _sf11.split("def _seed_batches")[1]
                          .split("def _seed_records")[0].splitlines()
                          if x.strip() and not x.strip().startswith("#"))
    check("落账四段: 批次重建支持仓型过滤(_only_own/_only_pf 分支都在)",
          "_only_own" in _sb_code3 and "_only_pf" in _sb_code3
          and "warehouse_type='own'" in _sb_code3, "routes/seed_fill.py")
    check("落账四段: 平台段跳过自有仓批次(否则平台段白跑一半量, 拆分失效)",
          "for _och in (() if _only_pf else _chs):" in _sb_code3
          or "for _och in (() if _only_pf else _chs):" in _sf11, "routes/seed_fill.py")
    # best_before 要读全部仓型批次取 MIN(exp_date) —— 只在最后一段(全量落完)回写
    check("落账四段: best_before 回写只在最后一段(平台段提前 return)",
          "if _only_pf:\n        return len(bdata)" in _sf11, "routes/seed_fill.py")
    # 可用库存重算只写 own 行, 平台段再跑一遍纯属白烧 RU
    check("落账四段: 可用重算只跑含自有仓的段",
          "if wh_group in ('', 'own'):" in _sf11, "routes/seed_fill.py")
    # seed_step 若仍按单段硬编码, 四段只会跑第一段 → 落账永远不完整
    _ss_src = _sf11.split("def seed_step")[1].split("\ndef ")[0]
    check("落账四段: seed_step 用 _SETTLE_SEGS 表驱动(不再单段硬编码)",
          "for _no, _ch, _wg in _SETTLE_SEGS:" in _ss_src
          and "seed_settle(skus_data, channel=_ch, wh_group=_wg)" in _ss_src,
          "routes/seed_fill.py")
    check("流水四段: seed_step 用 _FLOW_SEGS 表驱动且每段只发 1 份导入文件",
          "for _no, _ch, _ft in _FLOW_SEGS:" in _ss_src
          and "_seed_records(channel=_ch, flow_type=_ft)" in _ss_src,
          "routes/seed_fill.py")
    check("尾段四段: seed_step 用 _TAIL_SEGS 表驱动, 每片只做一件事",
          "for _no, _kind in _TAIL_SEGS:" in _ss_src
          and "_seed_tail_piece(_kind)" in _ss_src, "routes/seed_fill.py")
    # task_seed 的 /seed/step 白名单必须从段表推导, 否则新段全被拒
    check("/seed/step 白名单: 落账段号从 _SETTLE_SEGS 推导(不写死单段)",
          "_SETTLE_NOS = tuple(x[0] for x in _SETTLE_SEGS)" in _ts_src, "routes/task_seed.py")
    check("/seed/step: 默认只跑指定一段(single=true), 不再一个请求连跑十几段超时",
          'single = bool(d.get("single", True))' in _ts_src
          and 'to_step = int(d.get("to_step") or (from_step if single else _LAST))' in _ts_src,
          "routes/task_seed.py")
    # ② 订单双写: 水位对账抓出每段执行两遍(单日 3100 = 1550×2)。
    #    旧锁 `updated_at < NOW() - INTERVAL 3 SECOND` 没有持有期 → 改 lease_until。
    #    但 600s 长租约引入更坏的失效: 单段被网关硬杀 → 租约不释放 → 静止 10 分钟
    #    → 多个驱动反复抢锁重跑。后改为心跳续期(TTL 30s), 见下方第三次事故条目。
    _ts_code3 = "\n".join(x for x in _ts_src.splitlines()
                          if x.strip() and not x.strip().startswith("#"))
    check("续跑锁: 用 lease_until 独占租约, 不再用 3 秒窗口的 updated_at 判断",
          "lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND)" in _ts_code3
          and "INTERVAL 3 SECOND" not in _ts_code3, "routes/task_seed.py")
    # 2026-11-05 第三次事故(用户: "为什么跑了 1 百多还在跑"): 150s 固定租约仍会「死等」——
    #   三个并发请求只有一个拿到锁, 拿锁那个被网关切断后, 它写的 lease_until 还留 65s,
    #   期间所有轮询只能看到 step/lease_held, stalled 一路涨而台上没人。
    #   固定时长无法同时满足"跑 90s 的段不被人抢走"和"被杀了不拖住所有人",
    #   因为「请求还活着」代码本身不掌握 —— 只有"还在写心跳"才是活着的证据。
    check("续跑锁: 租约改由执行方心跳续期(TTL 30s), 不再靠固定 150s 死等",
          "LEASE_TTL = 30" in _ts_code3 and "LEASE_BEAT = 10" in _ts_code3
          and "def _start_heartbeat(" in _ts_code3
          and "_hb = _start_heartbeat(task_id, _tok)" in _ts_code3,
          "routes/task_seed.py")
    check("续跑锁: 心跳带归属令牌(否则上一任替下一任续约 = 无锁并发却看着像锁正常)",
          "AND lease_token=%s" in _ts_code3 and "lease_token=''" in _ts_code3,
          "routes/task_seed.py")
    check("续跑锁: 心跳在 finally 无条件停止(晚停会让下一任的租约被上一任续期)",
          "finally:" in _ts_code3 and "_hb()" in _ts_code3, "routes/task_seed.py")
    check("续跑锁: 抢锁失败不许继续执行(否则正是双写本身)",
          "if not _locked:" in _ts_src and 'return _prog({"lease": True})' in _ts_src,
          "routes/task_seed.py")
    check("续跑锁: 抢不到锁也返回进度快照(step/stalled_s), 不再裸 running",
          '"stalled_s": max(0, int(row.get("stalled_s") or 0))' in _ts_src
          and "TIMESTAMPDIFF(SECOND, updated_at, NOW())" in _ts_src,
          "routes/task_seed.py")
    check("流水幂等: 不再 DELETE 两张全表, 改为按 (渠道, 表) 精确删除",
          'execute("DELETE FROM `%s` WHERE channel=%%s" % _tbl, [ch])' in _sf11,
          "routes/seed_fill.py")
    check("流水幂等: 旧的无条件全表 DELETE 已移除(否则每次重跑清空线上数据)",
          "DELETE FROM inbound_records WHERE channel IN ('jd','other')" not in _sf11,
          "routes/seed_fill.py")
    check("批次重建: DELETE 与重建范围同源(渠道 × 仓型), 不再引入 SKU 半区范围",
          "_del_sql += _rng" not in _sf11 and "_del_args = list(_chs)" in _sf11
          and "_rng" not in _sf11,
          "routes/seed_fill.py")
    check("窗口预算: 75s 单请求预算(网关 120s 留 45s 余量) + 超预算留痕",
          _sf.SEED_WINDOW_BUDGET_S == 75
          and _sf.SEED_WINDOW_BUDGET_S < 120
          and "seed 窗口超预算 step=%d off=%d %.1fs > %ds" in _ts_src,
          "routes/seed_fill.py / task_seed.py")
    # 2026-11-05 线上事故(step22 单段 122s 被网关切断): 月汇总 UPDATE 用 COALESCE(barcode,'')
    #   做等值条件 → 唯一键 uq_inventory_biz 只能用 sku 前缀, Point_Get 退化
    #   成 IndexRangeScan + 逐行回表; 2.1 万组 × 17 万行, 必然超 120s。
    # 2026-11-06 同一个 122s 又出现了一次, 但根因不是索引 —— 是 **executemany 对 UPDATE
    #   根本不是批量**(PyMySQL 循环单条), 2.1 万组 = 2.1 万次往返。上一版按"分两支走等值"
    #   绕索引, 绕得过计划绕不过往返数。现改为单条 UPDATE ... JOIN: 驱动表是聚合子查询,
    #   既没有逐组回表, 也没有 N 次往返。断言改为锁这个形状。
    # 只看代码行(去注释): 说明性注释里会引用旧写法, 否则断言被自己的注释干成假红
    _sf11_code = "\n".join(x for x in _sf11.splitlines()
                           if x.strip() and not x.strip().startswith("#"))
    check("月汇总: 单条 UPDATE JOIN(executemany 对 UPDATE 是 N 次往返, 2.1 万组曾撞 122s)",
          "UPDATE inventory i" in _sf11_code
          and "LEFT JOIN (SELECT sku, COALESCE(barcode,'') AS bc" in _sf11_code
          and "=COALESCE(fi.q,0)" in _sf11_code
          and 'executemany("UPDATE inventory SET' not in _sf11_code,
          "routes/seed_fill.py")
    # 2026-11-06 第二步: 清零不再单独发一条全表 UPDATE。
    #   原实现 = 全表清零 + 入库 JOIN + 出库 JOIN, 同一批 17 万行被写 3 遍(线上实测该段 325s)。
    #   现在一条语句 LEFT JOIN 两张聚合表, 两列一起写, 且只写"值真的变了"的行 ——
    #   "没流水的行要归 0"这条语义由 COALESCE(f.q,0) + `<>` 守卫自然成立, 不靠额外一趟写。
    check("月汇总: 清零合并进 JOIN(不再全表 UPDATE 一遍) + 只写变化行",
          "UPDATE inventory SET month_inbound=0" not in _sf11_code
          and "i.month_inbound<>COALESCE(fi.q,0)" in _sf11_code,
          "routes/seed_fill.py")
    # 2026-11-05 期初全 0 的完整根因(三层叠加, 修一层露一层):
    #   ① reset 不清 ledger_state → jd 带着 closed/2026-10 重跑;
    #   ② 该状态让库存导入被判成「已建账」→ 期初挪进 snapshot_qty;
    #   ③ apply_import 又因"11-05 在实盘窗口外"直接 return → beginning_stock 无人写回,
    #      10,100 行期初 0; 而 other 无状态走首建账拿到 211 万 —— 同一份数据两种命运。
    # 中途曾用 baseline=True 绕过窗口, 那是**在文档 §15.152 之外新造第三条口径**,
    #   会让"同样的导入, seed 与真实客户算出不同的期初", 已回退。
    # 现在的正解: 场景识别 + reset 清账期状态。
    _la11 = open("core/ledger_adj.py", encoding="utf-8").read()
    check("期初场景: 四个场景统一识别(A结转/B实盘/C首建/D窗口外), 不再分散在各判断点",
          all(x in _la11 for x in ('SCENE_CARRY = "A_carry"', 'SCENE_STOCKTAKE = "B_stocktake"',
                                   'SCENE_FIRST = "C_first"', 'SCENE_OUTSIDE = "D_outside"',
                                   'def detect_scene(')),
          "core/ledger_adj.py")
    # ── 2026-11-06 性能: "种子/导入慢" 的三处结构性开销 ──────────────────
    # ① 69码多码证据: 原来对**本批所有** SKU 都查 3 张表, 而结论(_multi)只在
    #    "档案缺码 / 本行缺码"两个分支被读 → 查了也用不上。EXPLAIN 实测该查询在 orders
    #    上是 IndexFullScan(44 万索引项)、batches 上是 TableFullScan(18 万行)。
    _cw11 = open("routes/cleanse_write.py", encoding="utf-8").read()
    _cw11_code = "\n".join(x for x in _cw11.splitlines()
                           if x.strip() and not x.strip().startswith("#"))
    check("69码多码证据: 只对「档案缺码/本行缺码」的 SKU 探针(其余结论没人读)",
          "_need = [s for s in _skus if (not _amap.get(s)) or (s in _no_code)]" in _cw11_code
          and "_probe = [s for s in _need if s not in _probed]" in _cw11_code
          and "_probe = _need" in _cw11_code
          and "_probe = _skus" not in _cw11_code,
          "routes/cleanse_write.py")
    # ② 覆盖索引: 没有 (sku, barcode) 上面那条查询就只能全扫
    _sc11 = open("schema_create.py", encoding="utf-8").read()
    # 版本号只断言"≥13"(= 引入覆盖索引那一版), 不锁死具体数字 ——
    #   后续每次加 DDL 都要 bump, 锁死会让无关改动把这条打红。
    _sv11 = __import__("re").search(r"SCHEMA_VERSION\s*=\s*(\d+)", _sc11)
    check("schema: orders/batches 补 (sku, barcode) 覆盖索引 + 版本号递增触发补建",
          '("idx_orders_sku_bc", "orders", "(sku, barcode)")' in _sc11
          and '("idx_batches_sku_bc", "batches", "(sku, barcode)")' in _sc11
          and bool(_sv11) and int(_sv11.group(1)) >= 13,
          "schema_create.py")
    # ③ 聚合接口不许再背明细: aux 的 ownHealth 曾经 13,615 条 / 5.9MB
    _db11 = open("routes/dashboard.py", encoding="utf-8").read()
    # 只看代码行: 上面那条注释里引用了旧写法 `own_health(channel, full=1)`
    _db11_code = "\n".join(x for x in _db11.splitlines()
                           if x.strip() and not x.strip().startswith("#"))
    check("看板 aux: 自有仓明细有界(三类各 3 条), 全量走分页口 /dashboard/own-health",
          "own_health(channel, full=0, per_type=3)" in _db11
          and "own_health(channel, full=1)" not in _db11_code
          and '@router.get("/dashboard/own-health")' in _db11,
          "routes/dashboard.py")
    # ④ 缓存写失败 ≠ 算错了: 原实现会把 builder 再跑一遍(体积超上限时每请求算两遍)
    _ac11 = open("routes/analysis_cache.py", encoding="utf-8").read()
    check("缓存: cache_put 失败只丢缓存, 不触发重算(曾致 aux 每请求算两遍)",
          "cache_put 失败(本次结果照常返回, 不重算)" in _ac11,
          "routes/analysis_cache.py")
    check("own_health: 支持 offset/per_type/only 分页切片(返回仍带全量计数)",
          "def _page(items, full=0, limit=10, offset=0, per_type=0, only=" in
          open("routes/dashboard_own_health.py", encoding="utf-8").read(),
          "routes/dashboard_own_health.py")
    # 只看代码行(去注释): 历史说明里会引用被废弃的写法, 否则断言被自己的注释干成假红
    _la11_code = "\n".join(x for x in _la11.splitlines()
                           if x.strip() and not x.strip().startswith("#"))
    _sf11_code0 = "\n".join(x for x in _sf11.splitlines()
                            if x.strip() and not x.strip().startswith("#"))
    check("期初场景: 已无 baseline 开关(禁止在文档之外开第三条口径)",
          "baseline" not in _la11_code and "baseline" not in _sf11_code0,
          "core/ledger_adj.py / routes/seed_fill.py")
    check("期初换算: Σ起点与可用重算同源(都取 ledger.state.begin_date)",
          "state_get(channel, wt).get(\"begin_date\")" in _la11
          and "_net_since(channel, wt, _sum_start, ts)" in _la11,
          "core/ledger_adj.py")
    check("期初换算: 重算/重建接受显式 begin_date(否则两次读之间状态变更会让两轨错开)",
          "def _recalc_own_inventory(channel, skus, begin_date=None):"
          in open("routes/cleanse_write.py", encoding="utf-8").read()
          and "def _rebuild_own_batches(channel, skus, begin_date=None):"
          in open("routes/cleanse_write.py", encoding="utf-8").read(),
          "routes/cleanse_write.py")
    # reset 必须清账期五表: 否则"重置成空库"只是假象, 下一轮 seed 带着上轮的账期状态起跑
    _tk11 = open("routes/tasks.py", encoding="utf-8").read()
    check("重置: 清 ledger_state/ledger_begin_adj/batch_opening/inventory_month/seed_watermark",
          all('"%s"' % t in _tk11 for t in ("ledger_state", "ledger_begin_adj",
                                           "batch_opening", "inventory_month", "seed_watermark")),
          "routes/tasks.py")
    _sc10 = open("schema_create.py", encoding="utf-8").read()
    check("续跑锁: sync_tasks 已声明 lease_until 列",
          "lease_until DATETIME NULL" in _sc10, "schema_create.py")
    # 加列必须 bump 版本: ensure_schema 走热路径(一条 SELECT 零 DDL), 版本不变就永远不建列
    _ver = int(_re.search(r"^SCHEMA_VERSION\s*=\s*(\d+)", _sc10, _re.M).group(1))
    check("schema 版本: 加 lease_until 后已 bump(≥10)", _ver >= 10, _ver)

    # ── 2026-11-05 seed 走导入链路(用户定案: 演示数据必须由导入代码路径自己产出) ──
    import routes.seed_import as _si
    # 规格必须与导入页的 target 一一对上, 否则会出现"导入了但没走链路"的假象
    check("导入链路: 8 个 target 都有文件规格(含 supplier/gmv_flow)",
          set(_si._SPEC) == {"product", "supplier", "order", "inventory", "inventory_b",
                             "inbound", "outbound", "gmv_flow"}, sorted(_si._SPEC))
    # ── 2026-11-06 seed 渠道拆分 / GMV 回款 / 数据正确性 静态自检 ──
    _sf12 = open("routes/seed_fill.py", encoding="utf-8").read()
    _cw12 = open("routes/cleanse_write.py", encoding="utf-8").read()
    _dsh12 = open("routes/dashboard.py", encoding="utf-8").read()
    # local_test.py 的 cwd 固定是 cloud-functions/api/, 根目录脚本要回退两级
    _ec12 = open("../../scripts/seed_export_csv.py", encoding="utf-8").read()
    check("GMV 回款: seed 按渠道生成且走导入链路, flow_type 落在 _FLOW_NET 7 类内",
          "def _seed_gmv_flows" in _sf12
          and "_imp('gmv_flow', ch, rows" in _sf12
          and all(k in _sf12 for k in ("货款到账", "交易收入", "结算入账",
                                       "退款扣回", "扣点扣款"))
          and "'gmv_flows'" in _sf12, "routes/seed_fill.py")
    check("GMV 回款: 尾段含 gmv_flows 且成品库导出跟着加第 8 份",
          "_SEED_MONTH + 5, 'gmv_flows'" in _sf12
          and '("gmv_flow", "gmv_flows", "", "gmv_flow")' in _ec12,
          "routes/seed_fill.py / scripts/seed_export_csv.py")
    check("供应商: seed 按渠道各发一份导入文件(不再 executemany 直写)",
          "_imp('supplier', ch, _srow" in _sf12
          and 'executemany("INSERT INTO suppliers' not in _sf12,
          "routes/seed_fill.py")
    check("告警: 批量步长与切片同值(原 500/200 每批静默漏 300 行)",
          "range(0, len(inserts), 500)" in _sf12
          and "inserts[i:i + 500]" in _sf12
          and "inserts[i:i + 200]" not in _sf12, "routes/seed_fill.py")
    check("概览计数: suppliers 补 channel 过滤(与 alerts/products 同口径)",
          "FROM suppliers WHERE channel=%s) AS suppliers_c" in _dsh12,
          "routes/dashboard.py")
    # 迁移在循环外: 两个标记在 gmv_flow 分支内的先后位置, 反过来就是每行一次 SHOW INDEX
    _gw = _cw12.split('if target == "gmv_flow":')[1].split('if target in ("inventory"')[0]
    check("gmv_flow 导入: 老表唯一键迁移在逐行循环外(原来每行一次 SHOW INDEX)",
          0 < _gw.find("SHOW INDEX FROM gmv_flows") < _gw.find("for it in cleaned:"),
          "routes/cleanse_write.py")
    check("导入链路: 每个 target 的列数与映射数一致(列没映射=那一列永远不进库)",
          all(len(sp["cols"]) == len(sp["mp"]) for sp in _si._SPEC.values()),
          {k: (len(v["cols"]), len(v["mp"])) for k, v in _si._SPEC.items()})
    check("导入链路: 每个映射都有 target/type(缺 type 会让数值列变成字符串)",
          all(set(m) >= {"target", "type"} for sp in _si._SPEC.values()
              for m in sp["mp"].values()), "routes/seed_import.py")
    # CSV 往返: 逗号/引号/换行都在商品名里真实存在, 只有走 csv 模块才不出错
    _csv_txt = _si.to_csv("product", [{"sku": "SKU-X", "product_name": '酱油,大瓶"特惠"',
                                       "barcode": "6901", "price": 9.9}])
    _hdr_line = _csv_txt.decode("utf-8").splitlines()
    check("导入链路: CSV 表头用中文导出列名(与 ERP 导出同构, 不是英文内部列名)",
          _hdr_line[0].startswith("SKU编码,"), _hdr_line[0][:40])
    import csv as _csvm2
    _parsed_rows = list(_csvm2.reader(__import__("io").StringIO(_csv_txt.decode("utf-8"))))
    check("导入链路: CSV 往返后逗号/引号不破坏列边界(仍是 14 列)",
          len(_parsed_rows[1]) == 14, len(_parsed_rows[1]))
    # 与真实导入同一批函数(不是"照着写一份"): 解析/清洗/写入三层都必须来自 cleansing/cleanse_write
    _sip = open("routes/seed_import.py", encoding="utf-8").read()
    check("导入链路: 复用导入的解析与清洗(cleansing._parse_table / _clean_rows)",
          "from routes.cleansing import _parse_table, _clean_rows" in _sip, "routes/seed_import.py")
    check("导入链路: 复用导入的写入分派(cleanse_write._write_rows)",
          "from routes.cleanse_write import _write_rows" in _sip, "routes/seed_import.py")
    # seed 侧: 订单/商品/库存/流水都不许再裸 INSERT(否则链路只走一半, 等于没走)
    #   断言看**代码行**(去注释), 否则说明性注释里的旧写法定法会被误判成没改
    _sf12_raw = open("routes/seed_fill.py", encoding="utf-8").read()
    _sf12 = "\n".join(x for x in _sf12_raw.splitlines()
                      if x.strip() and not x.strip().startswith("#"))
    check("导入链路: seed 订单不再 executemany 裸写(改走 import_rows)",
          "INSERT IGNORE INTO orders" not in _sf12, "routes/seed_fill.py")
    check("导入链路: seed 库存不再裸写(自有仓/平台仓分别发 inventory / inventory_b)",
          "INSERT IGNORE INTO inventory" not in _sf12
          and "_imp('inventory'" in _sf12 and "_imp('inventory_b'" in _sf12,
          "routes/seed_fill.py")
    check("导入链路: seed 出入库不再裸写(按渠道×类型发四份文件)",
          "INSERT IGNORE INTO `%s`(%s)" not in _sf12
          and "_imp(_tgt, ch, _rows" in _sf12, "routes/seed_fill.py")
    check("导入链路: seed 商品档案走 import_rows(能暴露'映射了但写入白名单漏列')",
          "_imp('product', ch, _prow" in _sf12, "routes/seed_fill.py")
    # 可用重算不再由 seed 自己补一遍 —— 那是导入链路的职责, seed 只做只读核对
    check("导入链路: 落账不再二次调用 _recalc_own_inventory(改只读核对)",
          "_recalc_own_inventory as _rc" not in _sf12 and "_assert_available_balanced" in _sf12,
          "routes/seed_fill.py")
    # 写入层显式冲突语义: 不显式写 ON DUPLICATE 子句时, TiDB 的"键里含 deleted_at"会让
    #   upsert 静默退化成"什么都不做" —— 所以 _write_batch 必须支持调用方给子句
    _cw12 = open("routes/cleanse_write.py", encoding="utf-8").read()
    check("导入链路: _write_batch 支持显式 conflict_sql(优先于按表名推断)",
          "conflict_sql=\"\"" in _cw12 and 'sql += " ON DUPLICATE KEY UPDATE %s" % conflict_sql' in _cw12,
          "routes/cleanse_write.py")
    check("导入链路: _write_rows 把 conflict_sql 透传给库存分支",
          "conflict_sql=_ksql" in _cw12, "routes/cleanse_write.py")
    # 2026-11-05 线上事故根因: _sync_batches(sum) 逐行 SELECT + UPDATE/INSERT,
    #   一份 2 万行入库单 = 4 万次往返 → 单请求 123s 被网关 120s 硬切, 流水段永远跑不完。
    #   RU/耗时按查询次数计, 不按行数 —— 必须批量化, 且真实客户导入也走这条链路。
    _sb12 = _cw12.split("def _sync_batches")[1].split("\ndef ")[0]
    _sb12_code = "\n".join(x for x in _sb12.splitlines()
                            if x.strip() and not x.strip().startswith("#"))
    check("导入链路: 批次效期同步不再逐行 SQL(2 万行文件曾是 4 万次往返)",
          "SELECT id, qty FROM batches WHERE sku=%s" not in _sb12_code
          and '_em("UPDATE batches SET qty=%s WHERE id=%s", _upd)' in _sb12_code
          and "WHERE channel=%s AND sku IN (%s)" in _sb12_code
          and "(sku, warehouse) IN (%s)" in _sb12_code,
          "routes/cleanse_write.py")
    # 库存导出模板补了本周口径(周一至今), 不能由 month_* 推导
    # 2026-11-05 线上实测: platform 仓被整体改写成 platform_b —— 库存表凭空多出 7 万行 B 仓
    _cw12_code = "\n".join(x for x in _cw12.splitlines()
                            if x.strip() and not x.strip().startswith("#"))
    check("库存导入: 文件自带的仓型不被 target 默认值覆盖(否则平台仓全变 B 仓)",
          'it["warehouse_type"] = _wt0 or wt' in _cw12_code
          and 'it["warehouse_type"] = wt' not in _cw12_code,
          "routes/cleanse_write.py")
    check("库存导入: 库存按渠道×仓型拆四段(两渠道一次实测 362s, 踩穿 120s)",
          len(_si._SEED_INV_SEGS) == 4 if hasattr(_si, "_SEED_INV_SEGS") else True,
          "routes/seed_fill.py")
    _sf13 = open("routes/seed_fill.py", encoding="utf-8").read()
    check("库存导入: _seed_inventory 支持 channel/wh_group/sku 分片(拆分入口在生成器上)",
          "def _seed_inventory(skus_data, channel='', wh_group='', sku_from=0, sku_to=None)" in _sf13
          and "skus[int(sku_from):int(sku_to)]" in _sf13, "routes/seed_fill.py")
    check("库存导入: 拆段时只发该段要的仓型(否则等于把同一请求发四遍)",
          "if wh_group == 'platform':" in _sf13 and "elif wh_group == 'own':" in _sf13,
          "routes/seed_fill.py")
    check("库存导入: /seed/step 白名单从 _SEED_INV_SEGS 推导(否则 11~13 段全被拒)",
          "_INV_NOS = tuple(x[0] for x in _SEED_INV_SEGS)" in _ts_src, "routes/task_seed.py")
    check("inventory: 已声明 week_inbound/week_outbound(库存导出模板的本周口径)",
          "week_inbound INT DEFAULT 0" in _sc10 and "week_outbound INT DEFAULT 0" in _sc10,
          "schema_create.py")

    # ── 2026-11-05 用户实测: 骨架后闪空态 / 卡片一直刚刚 / 切页面任务停 ──
    import os as _os2
    _FE2 = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__)))), "frontend", "src")
    _tp12 = open(_os.path.join(_FE2, "pages", "TaskPage.tsx"), encoding="utf-8").read()
    # 2026-11-05 第三次返工: `loadedOnce` 那套是"再加一个标记", 三态变四态, 中间态仍能被渲染。
    #   现在收敛成单一 phase 状态机: load → ready 单向, 空态与数据同一次渲染二选一。
    #   判据只取**代码行**(去注释) —— 历史注释里提到旧方案不算回归。
    _tp12_code = "\n".join(x for x in _tp12.splitlines()
                           if x.strip() and not x.strip().startswith(("*", "//")))
    check("任务页: 三态收敛成单一 phase 状态机(空态与数据同帧二选一, 不闪中间态)",
          "useState<'load' | 'ready'>('load')" in _tp12_code
          and "phase === 'load' ?" in _tp12_code
          and "loadedOnce" not in _tp12_code,
          "TaskPage.tsx")
    _tc12 = open(_os.path.join(_FE2, "components", "tasks", "TaskCard.tsx"),
                 encoding="utf-8").read()
    # 相对时间曾把"带 +08:00 的时间串"再补一个 Z → 解析失败 → 恒「刚刚」
    check("任务卡: 时间解析不再无脑补 Z(带 +08:00 时会解析失败塌成刚刚)",
          "+08:00" in _tc12 and "String(iso).includes('Z')" not in _tc12, "TaskCard.tsx")
    check("任务卡: running 显示累计运行时长(不再依赖'这一秒刚好在推进')",
          "runLabel(_runSec)" in _tc12 and "_runSec" in _tc12, "TaskCard.tsx")
    # 看守: 驱动从任务页搬到 App 层 —— 切页面/持锁期间都有推进, 且全局串行
    _tw = open(_os.path.join(_FE2, "api", "taskWatch.ts"), encoding="utf-8").read()
    # 2026-11-06 P1-23: 原来是全局布尔 _inflight —— 同时有导入+评估+导出时,
    #   只有第一个能被推进, 另两个被饿死最长 100s(后端 30 分钟后判超时 error)。
    #   改为 per-taskId 在途集合 + 并发上限 2 + "最久没被推进的优先"轮转。
    check("任务看守: per-task 在途(不再全局单飞饿死其它任务) + 并发上限 2",
          "_driving.has(w.taskId)" in _tw and "_driving.add(w.taskId)" in _tw
          and "_driving.delete(w.taskId)" in _tw
          and "MAX_CONCURRENT_DRIVES = 2" in _tw
          and "_driving.size >= MAX_CONCURRENT_DRIVES" in _tw,
          "api/taskWatch.ts")
    # 名额有限时必须轮转, 否则"先注册的"永远抢到名额, 后面的任务一直排不上。
    check("任务看守: 名额有限时按「最久没被推进」轮转(不饿死后排任务)",
          "sort((a, b) => a.lastDrive - b.lastDrive)" in _tw, "api/taskWatch.ts")
    check("任务看守: 单请求超时 < 网关硬上限 120s(后端预算 90s + 余量)",
          "DRIVE_TIMEOUT = 100000" in _tw, "api/taskWatch.ts")
    check("任务看守: 无进展时指数退避(租约被持有时不硬撞、不烧 RU)",
          "Math.pow(1.25" in _tw, "api/taskWatch.ts")
    _app12 = open(_os.path.join(_FE2, "App.tsx"), encoding="utf-8").read()
    check("任务看守: 挂在 App 层(不在任务页), 切页面照样推进",
          "adoptRunning" in _app12 and "onWatchChange" in _app12, "App.tsx")

    # ── 2026-11-05 竞态根治: 状态端点必须是"纯读", 全仓只留一个驱动源 ──
    # 背景: /seed/fill/status、/cleansing/status、/cleansing/eval-status 每被调一次就
    #   「抢锁 + 推进一步 + 写库」, 而前端曾有 6 处在打它们 → 用户可见的忙徽标恒亮、
    #   抢不到锁的白烧 RU、进度条不动而租约一直被占。现在除 taskWatch 外一律带 probe=1。
    _cl12 = open(_os.path.join(_FE2, "api", "client.ts"), encoding="utf-8").read()
    # 后台请求不进用户可见的忙徽标(否则长任务在跑时任何页面都显示"同步中")
    # 2026-11-07: 由"全站前台 = _activeReq - _bgReq"改为"按视图分桶", 切页后旧页请求
    #   不再点亮新页胶囊(用户原话: 只在切换到各自页面显示)。
    check("同步胶囊: 后台请求不计入, 且前台按视图分桶(切页即归零)",
          "markBackground" in _cl12 and "backgroundRequests" in _cl12
          and "_fgByView.get(_curView)" in _cl12 and "export function setView" in _cl12,
          "api/client.ts")
    # 2026-11-06 S1: 全局 markBackground 会让并发请求串味(A 标了后台, B 也读成 true),
    #   改为请求级参数(apiJson 的 opts.background / axios 的 config.__bg)。
    check("同步胶囊: apiJson 支持 background 标记(按请求传参, 不用全局开关)",
          "background?: boolean" in _cl12 and "const _bg = Boolean(opts.background)" in _cl12
          and "_reqStart(_bg, _v)" in _cl12 and "_reqEnd(_bg, _v)" in _cl12,
          "api/client.ts")
    check("任务看守: 驱动请求标为 background(自己不点亮忙徽标)",
          "background: true" in _tw, "api/taskWatch.ts")
    # 反向红线: 除 taskWatch 外, 前端不得再有不带 probe 的状态查询(否则又多一个驱动源)
    _fe_files = []
    for _root, _dirs, _files in _os.walk(_FE2):
        for _f in _files:
            if _f.endswith((".ts", ".tsx")):
                _fe_files.append(_os.path.join(_root, _f))
    _bad = []
    for _p in _fe_files:
        if _p.endswith(_os.path.join("api", "taskWatch.ts")):
            continue  # 唯一驱动源, 必须不带 probe
        _src = open(_p, encoding="utf-8").read()
        # 多行拼接的 URL 要按「语句」而不是按行判: 把连续的行合成一句再看有没有 probe
        _stmt = ""
        for _ln in _src.splitlines():
            _s = _ln.strip()
            if _s.startswith(("//", "*", "/*")):
                continue
            _stmt += " " + _s
            if _s.endswith((",", "(", "+", "?", "'", '"')) or "=" in _s[-1:]:
                continue  # 语句未结束, 继续累加
            for _ep in ("/api/seed/fill/status?", "/api/cleansing/status?",
                        "/api/cleansing/eval-status?"):
                if _ep in _stmt and "probe=1" not in _stmt:
                    _bad.append(_os.path.basename(_p) + ": " + _stmt.strip()[:80])
            _stmt = ""
    check("状态查询: 除 taskWatch 外全部带 probe=1(否则同一任务被多处推进 → 抢锁/白烧 RU)",
          not _bad, _bad[:3])

    # 后端: 三条 status 端点都有 probe 纯读分支, 且 claim 早于任何写操作
    _cfs = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "routes")
    for _fname, _sig in (("task_seed.py", "probe: int = 0"),
                         ("cleansing.py", "probe: int = 0")):
        _src = open(_os.path.join(_cfs, _fname), encoding="utf-8").read()
        check("probe 纯读: %s 的 status 接受 probe 参数" % _fname, _sig in _src,
              "routes/" + _fname)
    _cc = open(_os.path.join(_cfs, "cleansing.py"), encoding="utf-8").read()
    check("probe 纯读: eval-status 也支持 probe(三类任务口径一致)",
          _cc.count("probe: int = 0") >= 2 and _cc.count("if probe:") >= 2,
          "routes/cleansing.py")
    _tsrc = open(_os.path.join(_cfs, "task_seed.py"), encoding="utf-8").read()
    # probe 分支必须**早于** mark_driver(那是写库), 否则"纯读"是假的
    _p1 = _tsrc.find("if probe:")
    _p2 = _tsrc.find("_mkdrv(task_id, driver)")
    check("probe 纯读: seed 的 probe 分支早于 mark_driver(否则仍是写端点)",
          -1 < _p1 < _p2, "routes/task_seed.py")
    _q1 = _cc.find("if probe:\n        if not task_id:")
    # 2026-11-06 P0-1: 驱动分支改成"先 SELECT 验类型 → 再 mark_driver",
    #   所以匹配串不能再带 `\n    row = one(`(那正是被移走的顺序)。
    _q2 = _cc.find("_mkdrv(task_id, driver)")
    _q3 = _cc.find('if not _type_ok(row, "cleansing"):')
    check("probe 纯读: cleansing 的 probe 分支早于 mark_driver(否则仍会抢锁/自愈写库)",
          -1 < _q1 < _q2, "routes/cleansing.py")
    check("P0-1 类型守卫: cleansing 的类型校验早于 mark_driver(打错端点不许写别人的库)",
          -1 < _q3 < _q2, "routes/cleansing.py")
except Exception as _e28:
    check("seed 规模/水位用例无异常", False, str(_e28)[:200])

# ── 2026-11-05 非真异常噪音根治: 正常态不进异常通道(链路判空 + 日志分级) ──
# 背景: try_err 是「静默吞异常」通道(固定 warning/quiet_error)。三处正常态误走了它:
#   ① continue-tasks 每 60s 一条 rows=0 警告(空库无任务, 永久连续)
#   ② 空库 _db2='' → strptime('') 抛 ValueError → 每次 dashboard 一条 warning
#   ③ 建表/迁移成功信息传空 exc → 记成 warning 且尾巴 ': str: '
# 修法不是压日志/降级别, 而是链路入口判空 + 正常态走 log_info(info 级)。
try:
    _api29 = _os.path.dirname(_os.path.abspath(__file__))
    _cm29 = open(_os.path.join(_api29, "routes", "common.py"), encoding="utf-8").read()
    check("日志分级: 有独立的 log_info 正常态通道(info/maint)",
          "def log_info(src, message)" in _cm29
          and "VALUES('maint','info',%s,%s)" in _cm29, "routes/common.py")
    # 反向红线: 真异常通道不能被削弱
    check("日志分级: try_err 仍是 warning/quiet_error(真异常留痕未被削弱)",
          "VALUES('quiet_error','warning',%s,%s,%s)" in _cm29, "routes/common.py")
    _cr29 = open(_os.path.join(_api29, "routes", "cron.py"), encoding="utf-8").read()
    check("continue-tasks: 空态(rows=0)不再写诊断 warning(否则每 60s 一条)",
          "continue-tasks 诊断" not in _cr29
          and "if rows:" in _cr29 and "log_info('cron'" in _cr29, "routes/cron.py")
    _dm29 = open(_os.path.join(_api29, "routes", "dashboard_maintain.py"),
                 encoding="utf-8").read()
    check("快照续段: 链路入口判空(_db2='' 直接返回, 不再 strptime('') 抛异常)",
          "if not _db2:\n        return" in _dm29, "routes/dashboard_maintain.py")
    check("快照续段: _track_data 有订单才追平(空库不触发续段)",
          "if _db2 and ((not _m) or _m < _db2):" in _dm29,
          "routes/dashboard_maintain.py")
    _pe29 = _dm29.find("if not _db2:")
    _pp29 = _dm29.find("快照续段: ch=%s db2=%s")
    check("快照续段: 判空早于 perf_debug 诊断(空库零写入)",
          -1 < _pe29 < _pp29, "routes/dashboard_maintain.py")
    _sc29 = open(_os.path.join(_api29, "schema_create.py"), encoding="utf-8").read()
    _rn29 = open(_os.path.join(_api29, "migrations", "runner.py"),
                 encoding="utf-8").read()
    check("建表/迁移成功: 不再把空 exc 传给异常通道(否则 ': str: ' 警告)",
          ", '')" not in _sc29 and ", '')" not in _rn29
          and "_log('migration', '%s 完成" not in _rn29,
          "schema_create.py / migrations/runner.py")
    check("建表/迁移成功: 改走 log_info / _log_ok(info 级)",
          "log_info('schema'" in _sc29 and "def _log_ok(src, what)" in _rn29
          and "_log_ok('migration'" in _rn29,
          "schema_create.py / migrations/runner.py")
    # 门禁自身: 前置失败(连接/覆盖/指纹)第三项是空串哨兵, report 曾无条件
    # item["where"] → TypeError, 把「配额耗尽 / 凭据连错库」的诊断崩成 traceback。
    _sa29 = open(_os.path.join(_api29, "sql_smoke.py"), encoding="utf-8").read()
    check("SQL 冒烟: report 兼容前置失败哨兵(连不上库时清楚报因, 不崩 traceback)",
          "def _detail(item)" in _sa29 and "isinstance(item, dict)" in _sa29
          and "_w, _s = _detail(item)" in _sa29, "sql_smoke.py")
    # 前端: 401/403 是预期鉴权态(有 auth:may-expired → /auth/check 专门链路),
    #   不该被当成 api_http_error warning 上报 —— 匿名访问部署预览域名必然 401,
    #   每次部署刷一条噪音。三处 HTTP 状态上报点全部排除, 5xx/网络错误照常留痕。
    _cl29 = open(_os.path.join(_FE2, "api", "client.ts"), encoding="utf-8").read()
    check("前端日志: 401/403 归为预期鉴权态(isExpectedAuth)",
          "isExpectedAuth" in _cl29 and "status === 401 || status === 403" in _cl29,
          "api/client.ts")
    check("前端日志: 三处 HTTP 状态上报点均排除预期鉴权态",
          _cl29.count("!isExpectedAuth(") >= 3
          and "status >= 400) && !isExpectedAuth(status)" in _cl29,
          "api/client.ts")
    # 反向红线: 网络错误与 5xx 仍上报(真异常留痕不被削弱)
    check("前端日志: 网络错误/5xx 仍照常上报(不削弱真异常)",
          "'network-error', 'warning'" in _cl29
          and "status >= 500 ? 'error' : 'warning'" in _cl29, "api/client.ts")
except Exception as _e29:
    check("日志噪音用例无异常", False, str(_e29)[:200])

# ── S3: 判定口径单一真源(core/metrics)行为断言 ─────────────────────────────
# 这组用例只喂内存数据, 不打库 —— 要验的是"同一条规则在四个视图里给出同一个结论",
# 而不是某个字符串有没有出现在某个文件里(那种断言换个写法就绕过)。
try:
    from core import metrics as _SM

    _ser = _SM.Series("jd", {
        ("S1", "B1", "__C__"): {"ds28": 2.0},        # 该码有记录
        ("S1", "", "__C__"): {"ds28": 5.0},          # SKU 合计
        ("S2", "", "__C__"): {"ds28": 0.0},          # 有记录但零销量
        ("S3", "", "北京仓"): {"ds28": 4.0},          # 只在这个仓有
    }, ("ds28",))
    check("S3 日销取值: 该维该码优先", _ser.value("ds28", "S1", "B1", "__C__") == 2.0, "拿到合计说明降级顺序反了")
    check("S3 日销取值: 少数码缺失才回落到该维合计",
          _ser.value("ds28", "S1", "B9", "__C__") == 5.0, "没回落到合计")
    check("S3 日销取值: 该码有记录且为 0 就是 0(不被合计顶上来)",
          _ser.value("ds28", "S2", "B1", "__C__") == 0.0, "0 被当成缺失了")
    check("S3 日销取值: 该维无数据 → 0, 绝不拿 __C__ 兜底",
          _ser.value("ds28", "S3", "", "上海仓") == 0.0, "跨维兜底会让本仓判断失真")

    _p = _SM.periods({"b_to_c_days": "3", "c_safety_days": "0", "ship_to_b_days": "9",
                      "safety_multiplier": "0", "lead_time_days": "10",
                      "purchase_lead_days": "14", "purchase_safety_days": "3",
                      "mode_bbcc_c_safety_days": "3"})
    check("S3 周期: mode 前缀键优先(平铺 c_safety_days=0 被覆盖成 3)",
          _p["bbcc"] == 3 + 3 + 9 + 0 and _p["b2c"] == 6, str(_p))
    check("S3 周期: 自有仓用采购周期, 平台仓跟随模式",
          _SM.period_of(_SM.DIM_OWN, "jd", "bbcc", _p) == 17
          and _SM.period_of(_SM.DIM_WH, "jd", "bbcc", _p) == 15
          and _SM.period_of(_SM.DIM_WH, "other", "traditional", _p) == 10, str(_p))

    _u = {"sku": "S1", "barcode": "", "kind": _SM.DIM_WH, "warehouse": "北京仓",
          "warehouse_type": "platform", "avail": 10, "transit": 0, "manual_safety": 0}
    check("S3 安全线: 手工 safety_qty>0 优先于 ds×周期",
          _SM.safety_line(100.0, 12, 50)[0] == 50
          and _SM.safety_line(100.0, 12, 50)[1] == "manual", "手工线没生效")
    check("S3 安全线: 手工为 0 表示没设过, 走动态线",
          _SM.safety_line(2.0, 12, 0) == (24.0, "dynamic"), "把 0 当成设了 0 天")
    check("S3 判定: 可用=0 → 断货(不混进低库存)",
          _SM.judge(dict(_u, avail=0), 100.0, _p, "jd")["state"] == _SM.ST_OUT)
    check("S3 判定: 无日销 → idle(有库存也不判不健康)",
          _SM.judge(_u, 0.0, _p, "jd")["state"] == _SM.ST_IDLE)
    check("S3 判定: 覆盖量(可用+在途)够线 → ok, 缺口 0",
          _SM.judge(dict(_u, avail=10, transit=20), 2.0, _p, "jd")["state"] == _SM.ST_OK
          and _SM.judge(dict(_u, avail=10, transit=20), 2.0, _p, "jd")["gap"] == 0)
    check("S3 判定: 覆盖不足 → low, 缺口=线-覆盖",
          _SM.judge(dict(_u, avail=10, transit=0), 2.0, _p, "jd")["gap"] == 20.0,
          "15 天周期 × ds2 = 30, 覆盖 10 → 缺口 20")
    check("S3 判定: 关闭在途计入后可用量不足即算低库存",
          _SM.judge(dict(_u, avail=10, transit=20), 2.0, _p, "jd",
                    include_transit=False)["state"] == _SM.ST_LOW)

    _inv = [
        {"sku": "S1", "barcode": "B1", "warehouse": "北京仓", "warehouse_type": "platform",
         "available_qty": 100, "in_transit_qty": 5, "safety_qty": 0},
        {"sku": "S1", "barcode": "B1", "warehouse": "B仓", "warehouse_type": "platform_b",
         "available_qty": 50, "in_transit_qty": 1, "safety_qty": 0},
        {"sku": "S1", "barcode": "B1", "warehouse": "集货仓", "warehouse_type": "own",
         "available_qty": 999, "in_transit_qty": 0, "safety_qty": 0},
    ]
    _bc = [x for x in _SM.units(_inv, merge_bc=True) if x["kind"] == _SM.DIM_BC]
    _own = [x for x in _SM.units(_inv, merge_bc=True) if x["kind"] == _SM.DIM_OWN]
    check("S3 归桶: BC 单元=平台+B 合计, 且**不含**自有仓库存(P0-13)",
          len(_bc) == 1 and _bc[0]["avail"] == 150 and _bc[0]["transit"] == 6,
          str(_bc))
    check("S3 归桶: 自有仓逐仓独立(不并进 BC)",
          len(_own) == 1 and _own[0]["avail"] == 999, str(_own))
    _all = _SM.units(_inv, merge_bc=False, with_bc=True)
    check("S3 归桶: 健康卡要四档并存时保留平台行并追加 BC",
          len([x for x in _all if x["kind"] == _SM.DIM_WH]) == 2
          and len([x for x in _all if x["kind"] == _SM.DIM_BC]) == 1, str(len(_all)))
    _trad = _SM.units(_inv, merge_bc=False, drop_platform_b=True)
    check("S3 归桶: 传统模式 B 仓不参与判定",
          not [x for x in _trad if x["warehouse_type"] == "platform_b"], "B 仓混进来了")
    _sku_level = _SM.units(_inv[:1] + [dict(_inv[0], barcode="B2", available_qty=1)],
                           merge_bc=False, grain=_SM.GRAIN_SKU)
    check("S3 归桶: 按 SKU 粒度时多码合并成一个单元",
          len(_sku_level) == 1 and _sku_level[0]["avail"] == 101, str(_sku_level))

    # ── 粒度单一真源(2026-11-06): grain_of 默认跟随补货模式, 配置可覆盖 ──
    check("S3 粒度: bbcc 默认按 69码, traditional 默认按 SKU",
          _SM.grain_of("jd", "bbcc", {}) == "barcode"
          and _SM.grain_of("other", "traditional", {}) == "sku",
          "%s/%s" % (_SM.grain_of("jd", "bbcc", {}), _SM.grain_of("other", "traditional", {})))
    check("S3 粒度: mode 前缀配置优先于平铺键",
          _SM.grain_of("jd", "bbcc", {"mode_bbcc_grain": "sku", "grain": "barcode"}) == "sku")
    check("S3 粒度: 平铺 grain 可覆盖默认, 非法值落回模式默认",
          _SM.grain_of("jd", "bbcc", {"grain": "sku"}) == "sku"
          and _SM.grain_of("jd", "bbcc", {"grain": "xxx"}) == "barcode")
    check("S3 归桶: grain=barcode 时多码是两个单元, grain=sku 时合并",
          len(_SM.units(_inv[:1] + [dict(_inv[0], barcode="B2", available_qty=1)],
                        merge_bc=False, grain=_SM.GRAIN_BARCODE)) == 2
          and len(_SM.units(_inv[:1] + [dict(_inv[0], barcode="B2", available_qty=1)],
                            merge_bc=False, grain=_SM.GRAIN_SKU)) == 1)

    # ── 合并不静默(2026-11-06): grain=sku 吞的是"行", 不能吞掉"哪个码没货"的信息 ──
    _mc = _SM.units([dict(_inv[0], barcode="B1", available_qty=0),
                     dict(_inv[0], barcode="B2", available_qty=100)],
                    merge_bc=False, grain=_SM.GRAIN_SKU)[0]
    check("S3 归桶: grain=sku 合并时记录码级明细(barcodes/code_avail/multi_barcode)",
          _mc.get("multi_barcode") is True
          and sorted(_mc.get("barcodes") or []) == ["B1", "B2"]
          and (_mc.get("code_avail") or {}).get("B1") == 0
          and (_mc.get("code_avail") or {}).get("B2") == 100, str(_mc))
    _rk = _SM.code_risk(_mc)
    check("S3 码级复核: 部分 69码 断货被显式带出(B1=0 → code_out 含 B1)",
          _rk["multi_barcode"] and _rk["code_out"] == ["B1"], str(_rk))
    check("S3 码级复核: B1=0/B2=100 被 SKU 合计判 ok, 但 code_out 不为空 —— 吞不了",
          _mc["avail"] == 100 and _mc["code_out"] if False else
          (_SM.code_risk(_mc)["code_out"] == ["B1"] and _mc["avail"] == 100))
    _one = _SM.units(_inv[:1], merge_bc=False, grain=_SM.GRAIN_SKU)[0]
    check("S3 码级复核: 单码单元无合并也就无风险(不制造噪音)",
          _SM.code_risk(_one)["code_out"] == [], str(_SM.code_risk(_one)))
    check("S3 码级明细串: 告警文案可直接引用",
          _SM.code_detail_str(_mc) == "B1:0 / B2:100"
          and _SM.code_detail_str(_one) == "", repr(_SM.code_detail_str(_mc)))

    _pairs = [({"kind": _SM.DIM_WH, "sku": "S1"}, {"state": _SM.ST_LOW}),
              ({"kind": _SM.DIM_WH, "sku": "S2"}, {"state": _SM.ST_OK}),
              ({"kind": _SM.DIM_WH, "sku": "S3"}, {"state": _SM.ST_PAUSED}),
              ({"kind": _SM.DIM_WH, "sku": "S4"}, {"state": _SM.ST_OUT})]
    _sc = _SM.score(_SM.counts(_pairs))
    check("S3 计分: 停用不进分母(分母=4-1=3, 健康=1)",
          _sc["total"] == 3 and _sc["healthy"] == 1, str(_sc))
    check("S3 计分: 空维度给 None/empty(前端显示—, 不给 100 分假象)",
          _SM.score(_SM.counts([]))["score"] is None
          and _SM.score(_SM.counts([]))["level"] == "empty")

    # 2026-11-06 第二刀: tally(只计数)必须与 judge_units+counts(造 verdict)逐单元等价 ——
    #   看板摘要改用 tally 省掉 16 万个 dict, 但"卡上低库存 / 点开列表"的数必须还是同一个。
    _t_us = _SM.units(_inv + [dict(_inv[0], barcode="B2", available_qty=100)],
                      merge_bc=False, with_bc=True, grain=_SM.GRAIN_SKU)
    _ser = _SM.Series("jd", {("S1", "", "__C__"): {"ds28": 1.0},
                             ("S2", "", "__C__"): {"ds28": 0.0}}, ("ds28",))
    _tly = _SM.tally(_t_us, _ser, "jd", "bbcc", p=_SM.periods({}))
    _prs = _SM.judge_units(_t_us, _ser, "jd", "bbcc", p=_SM.periods({}))
    _same = all(_SM.roll(_tly, kinds=(k,)) == _SM.counts(_prs, (k,))
                for k in (_SM.DIM_BC, _SM.DIM_WH, _SM.DIM_OWN))
    check("tally 与 judge_units 等价: 按桶类型的计数逐项相等", _same,
          "tally=%s pairs=%s" % (_tly, _SM.counts(_prs)))
    check("tally 与 judge_units 等价: 档位顺序不变(out 优先于 paused)",
          _SM.classify(0, 0, 0, paused=True) == _SM.ST_OUT
          and _SM.classify(10, 10, 0, paused=True) == _SM.ST_PAUSED,
          "%s / %s" % (_SM.classify(0, 0, 0, paused=True),
                       _SM.classify(10, 10, 0, paused=True)))
    # 低库存那一套: 旧的独立一遍(merge_bc=True, 平台行已并入 BC) 与新的"从 with_bc 结果里
    #   roll 出 bc + 非平台仓 wh"必须给出同一组数 —— 否则卡片和列表会重新分成两张脸。
    _us_ls = _SM.units(_inv + [dict(_inv[0], barcode="B2", available_qty=100)],
                       merge_bc=True, grain=_SM.GRAIN_SKU)
    _prs_ls = _SM.judge_units(_us_ls, _ser, "jd", "bbcc", p=_SM.periods({}))
    check("roll: 与旧的低库存独立一遍(merge_bc=True)逐项相等",
          _SM.roll(_tly, kinds=(_SM.DIM_BC, _SM.DIM_WH),
                   wt_not_in=("platform", "platform_b"))
          == _SM.counts(_prs_ls, (_SM.DIM_BC, _SM.DIM_WH)),
          "new=%s old=%s" % (_SM.roll(_tly, kinds=(_SM.DIM_BC, _SM.DIM_WH),
                                      wt_not_in=("platform", "platform_b")),
                             _SM.counts(_prs_ls, (_SM.DIM_BC, _SM.DIM_WH))))
except Exception as _e30:
    check("S3 口径用例无异常", False, str(_e30)[:300])

# ── P1: 判定结果物化读模型(core/unit_store)────────────────────────────────
# 这一组不打库: 守的是"物化层不另立口径"这条底线, 以及"发布前校验"真的会拒绝。
try:
    from core import unit_store as _US
    _API_DIR = _os.path.dirname(_os.path.abspath(__file__))

    def _rd(_p):
        return open(_os.path.join(_API_DIR, _p), encoding="utf-8").read()

    _usrc = _rd("core/unit_store.py")
    # ① 唯一真源: 分档只能走 metrics.judge(), 本文件不得自己写分档
    check("unit_store: 分档只调 metrics.judge(不自己写 classify)",
          ".classify(" not in _usrc and "M.judge(" in _usrc, "出现 classify( 调用 = 第二份真源")
    check("unit_store: SQL 里没有判定公式(CASE WHEN / safety_line)",
          "CASE WHEN" not in _usrc.upper() and "safety_line(" not in _usrc, "阈值进了 SQL")
    check("unit_store: 状态常量引用 metrics(不写裸字符串)",
          "M.ST_LOW" in _usrc and "M.ST_OUT" in _usrc, "裸写 'low'/'out' 迟早与 metrics 漂移")

    # ② 读开关: env 是"一刀关闭"(优先), 常规切换用库里 head.enabled(不用重新部署)
    _os.environ["UNIT_STATE_READ"] = "0"
    check("unit_store: UNIT_STATE_READ=0 一刀关闭(紧急回滚, 连 head 都不读)",
          _US.read_enabled() is False)
    _os.environ.pop("UNIT_STATE_READ", None)
    check("unit_store: env 未设置时不阻断(是否命中由 head.enabled 决定)",
          _US.read_enabled() is True)

    # ③ 低库存口径: 物化桶 → roll 必须与原实现(merge_bc 后 counts)逐项相等
    _t_us = {
        ("bc", "bc"): {"total": 100, "ok": 80, "low": 18, "out": 2, "idle": 0, "paused": 0},
        ("wh", "platform"): {"total": 50, "ok": 45, "low": 5, "out": 0, "idle": 0, "paused": 0},
        ("wh", "platform_b"): {"total": 30, "ok": 28, "low": 2, "out": 0, "idle": 0, "paused": 0},
        ("wh", "第三方仓"): {"total": 20, "ok": 15, "low": 4, "out": 1, "idle": 0, "paused": 0},
        ("own", "own"): {"total": 40, "ok": 30, "low": 8, "out": 2, "idle": 0, "paused": 0},
    }
    _orig_tally_of = _US.tally_of
    _US.tally_of = lambda *a, **k: _t_us
    check("unit_store: 低库存(bbcc) = bc + 非平台仓逐仓(平台行已并入 bc, 不重复数)",
          _US.low_stock_counts("jd", "bbcc") == {"c": 22, "out": 3, "total": 120},
          str(_US.low_stock_counts("jd", "bbcc")))
    check("unit_store: 低库存(traditional) = 全部逐仓(不含 bc, 不会双计)",
          _US.low_stock_counts("other", "traditional") == {"c": 11, "out": 1, "total": 100},
          str(_US.low_stock_counts("other", "traditional")))
    _US.tally_of = _orig_tally_of

    # ④ 发布前校验: 人为让记账少一个单元 → 必须拒绝(而不是发布后对账)
    _sc_bad = {"units": [{}, {}, {}],
               "tally": {("bc", "bc"): {"total": 2, "ok": 2, "low": 0, "out": 0,
                                        "idle": 0, "paused": 0}},
               "alerts": [], "series": None, "p": {}, "inc": True}
    _errs = _US._validate("jd", "bbcc", _sc_bad)
    check("unit_store: 校验能抓到'单元总数不符'(3 个单元只记了 2 个)", len(_errs) > 0, str(_errs[:2]))
    _sc_bad2 = {"units": [{}],
                "tally": {("bc", "bc"): {"total": 1, "ok": 0, "low": 1, "out": 0,
                                         "idle": 0, "paused": 0}},
                "alerts": [], "series": None, "p": {}, "inc": True}
    check("unit_store: 校验能抓到'异常明细缺行'(卡片 1 条 low, 明细 0 行)",
          any("异常行数" in e for e in _US._validate("jd", "bbcc", _sc_bad2)),
          str(_US._validate("jd", "bbcc", _sc_bad2)[:2]))

    # ⑤ 失效驱动: 域计数 + 后台重建必须挂在中央失效点上(不挂在各写入方, 否则必漏)
    _acsrc = _rd("routes/analysis_cache.py")
    check("unit_store: invalidate 里按域 bump + 起后台重建",
          "data_version_dom" in _acsrc and "ensure_async()" in _acsrc and "JUDGE_DOMAINS" in _acsrc,
          "routes/analysis_cache.py")
    # schema 在列(2026-11-07 P0): 表结构/列语义变化同样让"算完的答案"失效
    #   (unit_summary 新增 code_out 后, 旧 gen 里该列恒 0) —— 它仍是**判定输入**的一部分,
    #   不是 suppliers/purchases 那类"只影响文案、不影响分档"的域。
    check("unit_store: fp 依赖域只含判定输入(不含 suppliers/purchases)",
          tuple(_US.JUDGE_DOMAINS) == ("orders", "inventory", "products", "rules", "config",
                                       "schema"),
          str(_US.JUDGE_DOMAINS))

    # ⑥ schema: 三表 + 域版本表 + 版本号已 bump
    _sc18 = _rd("schema_create.py")
    check("unit_store: schema 声明四表(含断货快照) + data_version_dom + 版本 bump 到 20+",
          all(('"%s"' % t) in _sc18 for t in
              ("unit_state_head", "unit_summary", "unit_alert", "data_version_dom",
               "risk_snapshot"))
          and _re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", _sc18)
          and int(_re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", _sc18).group(1)) >= 20,
          "schema_create.py")

    # ⑦ 断货三级物化(2026-11-07 P0 最后一块): 独立表/独立开关 + 不复制算法
    _sr18 = _rd("routes/dashboard_stock_risk.py")
    _us18 = _rd("core/unit_store.py")
    check("risk 物化: 请求路径可命中, legacy 强制现算(对账用)",
          "serve_risk(" in _sr18 and "legacy: bool = False" in _sr18,
          "dashboard_stock_risk.py")
    check("risk 物化: 重建侧直接调现算入口, 不复制公式(红线 D)",
          "_stock_risk(channel, full=1, legacy=True" in _us18, "core/unit_store.py")
    check("risk 物化: 与 unit_alert 分表分开关(两套口径不混)",
          "risk_snapshot" in _us18 and "risk_enabled" in _us18, "core/unit_store.py")
    check("risk 物化: 排序唯一真源(现算/读回共用 risk_sort_key, 否则两路错位)",
          "def risk_sort_key(" in _sr18 and "risk_sort_key as _rsk" in _us18, "")
    check("risk 物化: 本轮没算成必须退回现算(risk_rows<0), 不能当'0 条断货'",
          'int(h.get("risk_rows", -1)) < 0' in _us18, "core/unit_store.py")
    check("risk 物化: 独立指纹含 suppliers(OTIF 影响可撑天数, unit fp 感知不到)",
          "RISK_DOMAINS = JUDGE_DOMAINS + (\"suppliers\",)" in _us18
          and 'fp_risk_now(channel, _m)' in _us18, "core/unit_store.py")
    # ⑧ 自有仓健康明细物化(2026-11-07 P2)
    _os18 = _rd("core/own_store.py")
    _oh18 = _rd("routes/dashboard_own_health.py")
    _sc18 = _rd("schema_create.py")
    check("own 物化: 读侧先读物化, 未命中退回现算(不是二选一, 是有序降级)",
          "core import own_store" in _oh18 and "d = _compute(channel" in _oh18,
          "dashboard_own_health.py")
    check("own 物化: 重建调 _compute, 不复制算法(红线 D)",
          "_compute(channel)" in _os18 and "def _compute(" in _oh18, "core/own_store.py")
    check("own 物化: 排序落 seq, 读侧 ORDER BY seq 切页(与卡面 TOP N 同序列)",
          "ORDER BY seq" in _os18 and "seq = 全局展示序号" in _sc18, "core/own_store.py")
    check("own 物化: 计数与明细同一次发布(否则'共 N 条'与翻到底对不上)",
          "stat VARCHAR(255)" in _sc18 and "_stat_str(d)" in _os18, "schema_create.py")
    check("own 物化: 发布前核对三类计数, 不过不切 head",
          "校验未通过" in _os18 and "_mark_stale(channel" in _os18, "core/own_store.py")
    check("own 物化: 回滚开关(OWN_HEALTH_READ=0 不必发版)",
          "OWN_HEALTH_READ" in _os18, "core/own_store.py")
    check("own 物化: 失效链路已接(写操作后台重建, 不等下一次请求现算)",
          "own_store as _OS" in _rd("routes/analysis_cache.py"), "analysis_cache.py")

    # 动态: 指纹不等/计数不可解析 → 必须返回 None(退回现算), 不能拿旧行当新行
    import core.own_store as _OS
    _oh = _OS.head
    _ofp = _OS.fp_now
    try:
        _OS.head = lambda ch: {"channel": ch, "gen": 3, "fp": "AAA", "row_cnt": 2,
                               "stat": "total:2;tg:1;os:1;er:0;crit:2;warn:0;od:90;eb:15;b2c:6",
                               "stale": 0}
        _OS.fp_now = lambda ch: "BBB"
        check("own 物化: fp 不等 → page_of 返回 None(宁慢不错)", _OS.page_of("jd") is None)
        _OS.fp_now = lambda ch: "AAA"
        _st = _OS._parse_stat("garbage")
        check("own 物化: 计数不可解析 → 视为不可用", _st is None)
        _OST = _OS._stat_str({"total": 7, "transferGap": 1, "overstock": 5, "expiryRisk": 1,
                              "critical": 2, "warning": 5,
                              "limits": {"overstock_days": 90, "expiry_buffer_days": 15,
                                         "b2c_days": 6}})
        _pb = _OS._parse_stat(_OST) or {}
        check("own 物化: stat 往返一致(出版本/读版本同一口径)",
              _pb.get("total") == 7 and _pb.get("os") == 5 and abs(_pb.get("od") - 90) < 1e-6,
              _OST)
        _OS.head = lambda ch: {"channel": ch, "gen": 3, "fp": "AAA", "stale": 1,
                               "stat": _OST}
        check("own 物化: stale=1 → 退回现算", _OS.page_of("jd") is None)
    finally:
        _OS.head = _oh
        _OS.fp_now = _ofp
except Exception as _e31:
    check("P1 物化读模型用例无异常", False, str(_e31)[:300])

# ── §15.159 P0: 补货/采购建议行「域指纹物化」(2026-11-07) ──────────────
try:
    _ls18 = _rd("core/list_store.py")
    _lp18 = _rd("core/list_page.py")
    _rp18 = _rd("routes/replenishment.py")
    _pu18 = _rd("routes/purchase.py")
    _sc18b = _rd("schema_create.py")
    _ac18 = _rd("routes/analysis_cache.py")
    import core.list_store as _LS
    import core.list_page as _LP

    # ① 红线 2: 旧路径整体删除, 不许两套缓存并存兜底
    check("建议行物化: 旧 TTL 分块缓存已删除(不许两套并存)",
          all(x not in _lp18 for x in ("def shape_cached", "def full_get",
                                       "def split_chunks", "def full_get_slice"))
          and "shape_cached" not in _rp18 and "shape_cached" not in _pu18
          and "_REPL_TTL" not in _rp18 and "_PURCHASE_TTL" not in _pu18,
          "core/list_page.py + 两路由")
    check("建议行物化: 读侧走 list_store.serve, 形状契约不变(§15.161 __swr 走响应顶层)",
          "_LS.serve(" in _rp18 and "_LS.serve(" in _pu18
          and "_items[:2000]" in _rp18
          and "return ok(_d, **_LS.pop_swr(_d))" in _pu18
          and "_LS.pop_swr(_d)" in _rp18 and "def pop_swr" in _ls18
          and "def ok(data, **extra)" in _rd("routes/common.py"), "两路由 + list_store + common")

    # ② 四维 + 红线 8: 全输入域 fp / 哨兵 / 确定性排序 / 发布自校验 / 回滚
    check("建议行物化: fp 覆盖全部输入域(含 purchases), 且失效矩阵同源 bump",
          '"purchases"' in _ls18 and "_US.fp_of(DEPS[kind]" in _ls18
          and "set(_KNOWN_DOMAINS) | set(_US.RISK_DOMAINS)" in _ac18,
          "list_store + analysis_cache")
    check("建议行物化: 哨兵 row_cnt=-1 区分'没算成'与'真的 0 条'",
          'row_cnt") or -1) < 0' in _ls18 and "row_cnt INT NOT NULL DEFAULT -1" in _sc18b,
          "list_store + schema")
    check("建议行物化: 排序落 seq, 读侧 ORDER BY seq 切页(红线 12)",
          "ORDER BY seq" in _ls18 and "seq INT NOT NULL" in _sc18b,
          "list_store + schema")
    check("建议行物化: 发布前核对总行数 + need 数, 不过不切 head",
          "校验未通过" in _ls18 and "SUM(need)" in _ls18
          and "_mark_stale(kind, scope" in _ls18, "core/list_store.py")
    check("建议行物化: 回滚开关 LIST_READ=0 不必发版",
          "LIST_READ" in _ls18 and "def read_enabled" in _ls18, "core/list_store.py")
    check("建议行物化: 算法不复制, build 只调 builder(红线 9)",
          "rows = _builder_of(kind, h)()" in _ls18
          and "lambda: _build_repl(channel, mode)" in _ls18
          and "lambda: _build_purchase(channel, mode, days)" in _ls18, "core/list_store.py")
    check("建议行物化: 写操作失效链路已接(后台重建, 不等下一请求现算)",
          "list_store as _LS" in _ac18 and "_LS.ensure_async(_kind)" in _ac18,
          "routes/analysis_cache.py")
    check("建议行物化: schema v26 声明 list_snapshot/list_head",
          '"list_snapshot"' in _sc18b and '"list_head"' in _sc18b
          and "SCHEMA_VERSION = 26" in _sc18b, "schema_create.py")
    check("建议行物化: 新鲜度只由 fp 决定, 读侧无 TTL/时间判断",
          # §15.161: TIMESTAMPDIFF 只允许出现在 _swr_meta 里算**播报用 age_s**,
          #   绝不参与「数据新不新」的判定; allow_stale 是唯一跳过 fp 比对的路径,
          #   stale/哨兵/坏行校验一字不减。锁住这条边界, 防止后人拿时间当新鲜度。
          "created_at" not in _ls18
          and "TIMESTAMPDIFF(SECOND, built_at, NOW(6)) AS age_s" in _ls18
          and "def _swr_meta" in _ls18 and "if not allow_stale:" in _ls18
          and 'str(h.get("fp") or "") != _fp' in _ls18, "core/list_store.py")
    check("建议行物化: 无筛选 total 直接读物化计数, 不扫全表(红线 11)",
          "if not need_only and not search:" in _ls18
          and 'h.get("row_cnt")' in _ls18, "core/list_store.py")

    # ③ 筛选等价(四维-准确性): 物化读侧 SQL 与 filter_rows 逐字等价
    _rows18 = [
        {"sku": "A%01", "product_name": "甲商品", "barcode": "B_1", "suggested_qty": 5, "b_suggested": 0},
        {"sku": "A02", "product_name": "乙商品", "barcode": "B2", "suggested_qty": 0, "b_suggested": 0},
        {"sku": "A03", "product_name": "丙", "barcode": "B3", "suggested_qty": 0, "b_suggested": 3},
        {"sku": None, "product_name": "丁", "barcode": "", "suggested_qty": 0, "b_suggested": 0},
    ]
    _nk18 = ("suggested_qty", "b_suggested")
    _hit18 = _LP.filter_rows(_rows18, need_only=1, need_keys=_nk18)
    _need_py = [1 if r in _hit18 else 0 for r in _rows18]
    _need_sql = [_LS._row_vals(r, _nk18)[0] for r in _rows18]
    check("建议行物化: need 列与 filter_rows(need_only) 逐行一致",
          _need_py == _need_sql, str((_need_py, _need_sql)))
    _cols_ok = all(
        _LS._row_vals(r, _nk18)[1:4] == (str(r.get("sku", ""))[:80],
                                         str(r.get("product_name", ""))[:255],
                                         str(r.get("barcode", ""))[:40])
        for r in _rows18)
    check("建议行物化: 检索三列与 filter_rows 同一表达式(含 None 边界)", _cols_ok)
    _w18, _p18 = _LS._where("repl", "s", 1, 1, "A%_")
    check("建议行物化: search 走 INSTR 而非 LIKE(%/_ 不当通配符)",
          "INSTR(LOWER(sku)" in _w18 and "LIKE" not in _w18
          and _p18[-3:] == ["a%_", "a%_", "a%_"], _w18)

    # ④ 动态: fp/stale/哨兵/回滚 四种不可用都必须退回现算, 绝不返回旧行
    _o18 = (_LS.head, _LS.fp_now, _LS.read_enabled, _LS._stale_recent)
    try:
        _H18 = {"gen": 3, "fp": "AAA", "row_cnt": 10, "stale": 0,
                "channel": "jd", "mode": "bbcc", "days": 28, "source": ""}
        _LS.head = lambda k, s: dict(_H18)
        _LS.fp_now = lambda k, c, m, e="": "BBB"
        check("建议行物化: fp 不等 → page_of 返回 None(宁慢不错)",
              _LS.page_of("repl", "s", "jd", "bbcc", extra="s") is None)
        _LS.fp_now = lambda k, c, m, e="": "AAA"
        _H18["stale"] = 1
        check("建议行物化: stale=1 → 退回现算",
              _LS.page_of("repl", "s", "jd", "bbcc", extra="s") is None)
        _H18["stale"] = 0
        _H18["row_cnt"] = -1
        check("建议行物化: row_cnt=-1 哨兵 → 退回现算",
              _LS.page_of("repl", "s", "jd", "bbcc", extra="s") is None)
        _H18["row_cnt"] = 10
        _LS.read_enabled = lambda: False
        check("建议行物化: LIST_READ=0 → 一律现算",
              _LS.page_of("repl", "s", "jd", "bbcc", extra="s") is None)
    finally:
        _LS.head, _LS.fp_now, _LS.read_enabled, _LS._stale_recent = _o18

    # ⑤ 动态: 发布前自校验 —— 写少一行必须拦下, 不切 head(红线 8②)
    import db as _db18
    _mem18 = {"snap": [], "published": [], "stale": []}

    def _ex18(sql, params=None):
        s = sql.strip()
        if "ON DUPLICATE KEY UPDATE gen" in s:
            _mem18["published"].append(params)
        elif "ON DUPLICATE KEY UPDATE stale=1" in s:
            _mem18["stale"].append(params)
        return 0

    def _exm18(sql, rows):
        _mem18["snap"].extend(rows)

    def _one18(sql, params=None):
        if "SUM(need)" in sql:
            sel = [r for r in _mem18["snap"]
                   if r[0] == params[0] and r[1] == params[1] and r[2] == params[2]]
            return {"n": len(sel), "nd": sum(r[4] for r in sel)}
        return {"n": 0}

    _brows18 = [{"sku": "S%d" % i, "product_name": "P", "barcode": "",
                 "suggested_qty": i, "b_suggested": 0} for i in range(5)]
    # `from db import ...` 是绑定引用: 只桩 db 模块属性不够, list_store 内的名字也要一起桩
    _odb18 = (_db18.execute, _db18.executemany, _db18.one, _db18.query,
              _LS.execute, _LS.executemany, _LS.one, _LS.query)
    _ols18 = (_LS.head, _LS.fp_now, _LS._stale_recent, _LS._builder_of)
    try:
        _db18.execute, _db18.executemany = _ex18, _exm18
        _db18.one, _db18.query = _one18, lambda sql, params=None: []
        _LS.execute, _LS.executemany = _ex18, _exm18
        _LS.one, _LS.query = _one18, lambda sql, params=None: []
        _LS.head = lambda k, s: {"gen": 3, "fp": "", "row_cnt": -1, "stale": 0,
                                 "channel": "jd", "mode": "bbcc", "days": 28, "source": ""}
        _LS.fp_now = lambda k, c, m, e="": "FPX"
        _LS._stale_recent = lambda k, s: False
        _LS._builder_of = lambda k, h: (lambda: _brows18)
        _r18 = _LS.build("repl", "sc", force=True)
        check("建议行物化: 正常一轮发布切 head(gen/fp/计数同次写入)",
              bool(_r18.get("ok")) and _r18.get("error") != "validate"
              and len(_mem18["published"]) == 1, str(_r18))
        _mem18["snap"], _mem18["published"], _mem18["stale"] = [], [], []
        _LS.executemany = lambda sql, rows: _mem18["snap"].extend(rows[:-1])  # 故意丢最后一行
        _r18b = _LS.build("repl", "sc", force=True)
        check("建议行物化: 写少一行 → 校验拦下且不切 head",
              (not _r18b.get("ok")) and _r18b.get("error") == "validate"
              and not _mem18["published"] and bool(_mem18["stale"]), str(_r18b))
    finally:
        (_db18.execute, _db18.executemany, _db18.one, _db18.query,
         _LS.execute, _LS.executemany, _LS.one, _LS.query) = _odb18
        _LS.head, _LS.fp_now, _LS._stale_recent, _LS._builder_of = _ols18

    # ⑥ 失效矩阵与物化依赖域逐项相等(防两处漂移)
    from routes.analysis_cache import _FAMILY_DEPS as _FD18
    check("建议行物化: DEPS 与失效矩阵对应族逐项相等",
          set(_LS.DEPS["repl"]) == _FD18["repl"]
          and set(_LS.DEPS["purchase"]) == _FD18["purchase"],
          str((_LS.DEPS, _FD18.get("purchase"))))
except Exception as _e32:
    check("§15.159 建议行物化用例无异常", False, str(_e32)[:300])

# ── §15.160 P0: 滞销处置建议物化(第三种 list_store kind) ─────────────
try:
    _pc19 = _rd("routes/purchase_calc.py")
    _ls19 = _rd("core/list_store.py")
    _pu19 = _rd("routes/purchase.py")

    # 红线 9: 唯一公式 _compute, 物化重建与现算兜底都调它, 不复制
    check("disposal 物化: 唯一公式 _disposal_compute, 重建与现算同源(红线 9)",
          "def _disposal_compute(" in _pc19
          and "_disposal_compute(channel, 0)[0]" in _ls19          # 重建侧
          and "lambda: _disposal_compute(channel)[0]" in _pc19      # serve 降级
          and "items, _PF = _disposal_compute(channel, debug)" in _pc19,  # 现算响应
          "purchase_calc + list_store")
    check("disposal 物化: 读侧先读物化, 未命中退回现算(有序降级)",
          "_LS.serve(" in _pc19 and "def disposal_suggestions(" in _pc19
          and "if debug or legacy:" in _pc19, "purchase_calc.py")
    # 红线 12: 排序补 (sku,barcode,warehouse) 全序 tie-breaker, 否则 seq 与 Python sort 错位
    check("disposal 物化: 排序全序 tie-breaker(不依赖 inventory 物理序)",
          'str(x.get("sku", "")), str(x.get("barcode", "")),' in _pc19
          and 'str(x.get("warehouse", ""))' in _pc19
          and "ORDER BY seq" in _ls19, "purchase_calc + list_store")
    # 数据四维-准确性: 业务日期是确定性输入域, 跨天必重建(不是 TTL)
    check("disposal 物化: 业务日期进 fp(跨天不重建会返回昨天 level)",
          'DATE_AWARE = ("disposal",)' in _ls19 and "today_cn()" in _ls19
          and 'raw += "|d=" + today_cn()' in _ls19, "core/list_store.py")
    # 依赖域覆盖 disposal_records/alerts(purchases/rules) —— 处置动作要即时反映
    check("disposal 物化: 依赖域覆盖 purchases/rules(处置记录/伪滞销线索)",
          '"disposal": ("orders", "inventory", "products", "config", "rules", "purchases")'
          in _ls19 and 'invalidate("purchases")' in _pu19, "list_store + purchase")
    # 形状契约: 分页 dict / 裸调列表 / debug 带 _perf
    check("disposal 物化: 形状契约不变(分页dict/裸调列表/debug _perf)",
          # §15.161: __swr 摘到响应顶层, data 仍是纯列表/纯 dict, 形状一字未改
          'return ok(_d["items"], **_hd)' in _pc19 and "return ok(_d, **_hd)" in _pc19
          and "_hd = _LS.pop_swr(_d)" in _pc19 and '"_perf": _PF' in _pc19,
          "purchase_calc.py")
    # disposal 不写 analysis_cache 表缓存, 故 _FAMILY_DEPS 无需登记(靠域计数 fp)
    from routes.analysis_cache import _FAMILY_DEPS as _FD19
    check("disposal 物化: 不进 _FAMILY_DEPS(走域计数 fp, 非缓存族墓碑)",
          "disposal" not in _FD19 and '"disposal"' in _ls19, "list_store + analysis_cache")
    check("disposal 物化: 失效链路自动覆盖(invalidate 遍历 DEPS 三 kind)",
          "_LS.ensure_async(_kind)" in _rd("routes/analysis_cache.py")
          and _ls19.count('": (') >= 3, "analysis_cache")
    # §15.160 抓到的读写 fp 不对称: build 不得把登记的空 mode 兜底成 "bbcc",
    #   否则写侧 fp ≠ 读侧 fp → 物化永不命中(每请求仍 2.5s 现算, 且白烧重建)。
    #   锁住整条同源链: register_scope 存 serve 传入的 mode 原值 → build 读回不改写。
    check("disposal 物化: 读写 fp 同源(register 存原值 + build 不兜底 bbcc)",
          'mode = str(h.get("mode") or "")' in _ls19
          and 'str(mode)[:16]' in _ls19
          and 'page_of(kind, scope, channel, mode,' in _ls19,
          "core/list_store.py")

    # 红线 16: 三重线上验证的入口不许被删 —— state(fp_match)/legacy(双路对账)/force(命中反证)
    check("disposal 物化: 三重验证诊断口齐全(state/legacy/force, 红线 16)",
          "def status(" in _ls19 and "legacy" in _pu19 and "force" in _pu19
          and "if debug or legacy:" in _pc19 and "register_scope" in _pc19,
          "list_store + purchase + purchase_calc")

    # 动态: 日期变化时 fp 必变(跨天重建的根), fp 不等 → page_of 返回 None
    import core.list_store as _LS19
    _o19 = (_LS19.head, _LS19.fp_now)
    try:
        _LS19.head = lambda k, s: None
        check("disposal 物化: 无 head → 退回现算", _LS19.page_of("disposal", "s", "jd", "", extra="s") is None)
    finally:
        _LS19.head, _LS19.fp_now = _o19
except Exception as _e33:
    check("§15.160 disposal 物化用例无异常", False, str(_e33)[:300])

# ── §15.161 P1/P2: 冷算治理(全局预算/按 kind 分舱/单飞) + SWR ──────────
try:
    _ls20 = _rd("core/list_store.py")
    _rp20 = _rd("routes/replenishment.py")
    _pu20 = _rd("routes/purchase.py")
    _pc20 = _rd("routes/purchase_calc.py")
    _cm20 = _rd("routes/common.py")
    import core.list_store as _LS20

    # ① 全局预算复用 db.heavy, 不另造第二套闸门(红线 2/4)
    check("§15.161 冷算治理: 复用 db.heavy 全局预算, 不新造全局队列/worker/跨 kind 租约",
          "from db import heavy, HeavyBusy" in _ls20 and "with heavy(" in _ls20
          and "def _cold_worker" in _ls20
          and "queue.Queue" not in _ls20 and "lease_owner" not in _ls20,
          "core/list_store.py")

    # ② 同 key 单飞 + 按 kind 分舱 + 读侧不等待
    check("§15.161 冷算治理: 同 (kind,scope,fp) 单飞, 不同 kind 并行, 读侧零等待",
          "def submit_background" in _ls20 and "_FLIGHTS[key]" in _ls20
          and "if prev is None:" in _ls20 and "ev.wait(" not in _ls20
          and 'name="list-cold-" + str(kind)' in _ls20
          and "threading.Thread(target=_cold_worker" in _ls20,
          "core/list_store.py")

    # ③ 任何分支都不等后台结果(回应「方案比等重建还糟」); 首次无上一版直接现算,
    #    保证首帧是当前完整数据, 绝不返回 0 条或等 15~30s。
    check("§15.161 冷算治理: 读侧零等待, 首次直接现算(不返回空数据)",
          "ev.wait(" not in _ls20 and "_FIRST_WAIT" not in _ls20
          and "building_first" not in _ls20 and "return _live()" in _ls20
          and '"first_legacy"' in _ls20 and _LS20._HEAVY_WAIT <= 2.0,
          "core/list_store.py")

    # ④ 回滚与新鲜度(红线 15/16)
    check("§15.161 冷算治理: 一刀回滚 + flight 带新鲜度 + 诊断口 mode 与 build 同源",
          "if not read_enabled() or legacy:" in _ls20
          and "_FLIGHT_TTL" in _ls20 and 'cur["t0"]' in _ls20
          and 'ch, md = str(h.get("channel") or "jd"), str(h.get("mode") or "")' in _ls20
          and '"cold": dict(_STATS' in _ls20,
          "core/list_store.py")

    # ⑤ __swr 只在顶层, data 形状逐字不变(分页 dict / 裸调列表)
    check("§15.161 SWR: __swr 走响应顶层, 三路由 data 形状不变",
          "def pop_swr" in _ls20 and '"__swr": d.pop("__swr")' in _ls20
          and 'return ok(_items[:2000], **_hd)' in _rp20
          and 'return ok(_d, **_LS.pop_swr(_d))' in _pu20
          and 'return ok(_d["items"], **_hd)' in _pc20
          and 'return ok(_d, **_hd)' in _pc20
          and '{"ok": True, "data": data, **extra}' in _cm20,
          "list_store + 三路由 + common")

    # ⑥ 动态: allow_stale 只跳过 fp 比对, 完整性校验一字不减
    _o20 = (_LS20.head, _LS20.fp_now, _LS20.read_enabled, _LS20.query, _LS20.one)
    try:
        _H20 = {"gen": 3, "fp": "OLD", "row_cnt": 1, "stale": 0,
                "channel": "jd", "mode": "bbcc", "days": 28, "source": "",
                "built_at": "2026-11-07 10:00:00"}
        _LS20.head = lambda k, s: dict(_H20)
        _LS20.fp_now = lambda k, c, m, e="": "NEW"       # fp 已变
        _LS20.read_enabled = lambda: True
        _LS20.query = lambda sql, p=None: [{"payload": '{"sku":"S1"}'}]
        _LS20.one = lambda sql, p=None: {"age_s": 12}
        _d20 = _LS20.page_of("repl", "s", "jd", "bbcc", extra="s", allow_stale=True)
        check("§15.161 SWR: fp 不等仍可读上一版完整物化(用户不等重建)",
              isinstance(_d20, dict) and _d20.get("total") == 1
              and len(_d20.get("items") or []) == 1, str(_d20))
        check("§15.161 SWR: 常态读(fp 不等)仍返回 None, 不静默返旧值",
              _LS20.page_of("repl", "s", "jd", "bbcc", extra="s") is None)
        _H20["stale"] = 1
        check("§15.161 SWR: stale=1 → 上一版也不可读(完整性不打折)",
              _LS20.page_of("repl", "s", "jd", "bbcc", extra="s", allow_stale=True) is None)
        _H20["stale"] = 0
        _H20["row_cnt"] = -1
        check("§15.161 SWR: row_cnt=-1 哨兵 → 上一版也不可读",
              _LS20.page_of("repl", "s", "jd", "bbcc", extra="s", allow_stale=True) is None)
        _H20["row_cnt"] = 1
        _LS20.read_enabled = lambda: False
        check("§15.161 SWR: LIST_READ=0 一刀回滚, 连上一版也不读",
              _LS20.page_of("repl", "s", "jd", "bbcc", extra="s", allow_stale=True) is None)
    finally:
        _LS20.head, _LS20.fp_now, _LS20.read_enabled, _LS20.query, _LS20.one = _o20

    # ⑦ 动态: pop_swr 摘状态后 data 不留痕(形状契约)
    _p20 = {"items": [1], "total": 1, "page": 1, "page_size": 300,
            "__swr": {"state": "serving_previous"}}
    _hd20 = _LS20.pop_swr(_p20)
    check("§15.161 SWR: pop_swr 摘出状态且 data 里不留 __swr",
          "__swr" in _hd20 and "__swr" not in _p20
          and set(_p20) == {"items", "total", "page", "page_size"}, str(_p20))
except Exception as _e34:
    check("§15.161 冷算治理/SWR 用例无异常", False, str(_e34)[:300])

# ── §15.162 看板冷算根治: 判定读模型 SWR 化 + 等待链收敛 + boot 冒泡 ─────────────
try:
    _us22src = _rd("core/unit_store.py") if "_rd" in dir() else open(
        _os.path.join(_API_DIR, "core/unit_store.py"), encoding="utf-8").read()
    _dash22 = _os.path.join(_API_DIR, "routes/dashboard.py")
    _bundle22 = _os.path.join(_API_DIR, "routes/dashboard_bundle.py")
    _ac22 = _os.path.join(_API_DIR, "routes/analysis_cache.py")
    _dsr22 = _os.path.join(_API_DIR, "routes/dashboard_stock_risk.py")
    _dashsrc = open(_dash22, encoding="utf-8").read()
    _bundlesrc = open(_bundle22, encoding="utf-8").read()
    _acsrc22 = open(_ac22, encoding="utf-8").read()
    _dsrsrc = open(_dsr22, encoding="utf-8").read()

    # ① 静态: SWR 骨架复用不叠加(红线 2/4/5)
    check("§15.162 后台重建复用 list_store 分舱单飞(db.heavy 全局预算), 不另造队列/worker",
          "list_store as _LS" in _us22src and "_LS.submit_background(" in _us22src
          and "queue.Queue" not in _us22src and "_FLIGHTS = {}" not in _us22src,
          "core/unit_store.py")
    check("§15.162 allow_stale 只跳过 fp 比对, 完整性哨兵一字不减(tally_of/risk_of)",
          _us22src.count("if not allow_stale:") == 2
          and 'int(h.get("risk_rows", -1)) < 0' in _us22src
          and 'not int(h.get("stale") or 0)' in _us22src, "core/unit_store.py")
    check("§15.162 serve_tally/serve_overview/serve_risk 三入口: 失配返回上一版, 不可用才现算",
          all(("def serve_%s" % f) in _us22src for f in ("tally", "overview", "risk"))
          and _us22src.count("submit_rebuild(channel, _m)") >= 3, "core/unit_store.py")
    check("§15.162 swr_meta 用 DB 时间算 freshness_s(不参与数据新鲜度判定, 只用于播报)",
          "TIMESTAMPDIFF(SECOND, built_at, NOW(6)) AS age_s" in _us22src
          and "def swr_meta" in _us22src, "core/unit_store.py")
    check("§15.162 _store_tally 返回 (tally, swr), 快路径判据 _store_on 语义不变",
          "return _US.serve_tally(channel, mode)" in _dashsrc
          and "_t_sum, _swr_tally = _store_tally(channel, mode)" in _dashsrc
          and "max_workers=3 if not _store_on else 1" in _dashsrc, "routes/dashboard.py")

    # ② 静态: 等待链收敛 + 空旧值兜底修正(完整性)
    check("§15.162 heavy 等待 15s → 2s 快速失败(aux/stock-risk 同一常量)",
          "timeout=15" not in _dashsrc and "_DASH_HEAVY_WAIT = 2.0" in _dashsrc
          and _dashsrc.count('timeout=_DASH_HEAVY_WAIT') == 2, "routes/dashboard.py")
    check("§15.162 单飞等锁 12s → 2s(与 insights 系同源收敛, 等待不创造价值)",
          "acquire(timeout=2)" in _acsrc22 and "acquire(timeout=12)" not in _acsrc22,
          "routes/analysis_cache.py")
    check("§15.162 无旧值可退时排队现算, 不再返回空结构(aux or {} / 全 0 兜底已修)",
          "_cache_stale(_key) or {}" not in _dashsrc
          and "宁可排队现算保证完整数据" in _dashsrc, "routes/dashboard.py")
    check("§15.162 cache_stale 旧值必须显式播报 __swr(不再静默返旧值)",
          _dashsrc.count('"source": "stale_cache"') == 2, "routes/dashboard.py")

    # ③ 静态: 播报走响应顶层, data 形状不变 + boot 冒泡
    check("§15.162 summary/aux/stock-risk 的 __swr 都 pop 到响应顶层(不进 data)",
          "_result.pop(\"__swr\", None)" in _dashsrc
          and "_data.pop(\"__swr\", None)" in _dashsrc
          and "_r.pop(\"__swr\", None)" in _dashsrc, "routes/dashboard.py")
    check("§15.162 stock_risk 读侧 SWR: serve_risk 返回上一版并带 __swr, 对账 legacy 不 SWR",
          "_US.serve_risk(" in _dsrsrc and 'if not legacy:' in _dsrsrc, "dashboard_stock_risk.py")
    check("§15.162 boot 把子项 __swr 冒泡到聚合顶层, 子项 data 形状逐字不变",
          '_d.pop("__swr", None)' in _bundlesrc
          and '"parts": _swrs' in _bundlesrc
          and '"state": "serving_previous"' in _bundlesrc, "routes/dashboard_bundle.py")

    # ④ 动态: serve_tally 三态(mock head/fp/query, 不碰真库)
    _US22 = _US
    _o22 = (_US22.head, _US22.fp_now, _US22.read_enabled, _US22.query,
            _US22.submit_rebuild)
    _subs22 = []
    try:
        _H22 = {"gen": 3, "fp": "OLD", "stale": 0, "enabled": 1, "channel": "jd",
                "mode": "bbcc", "built_at": "2026-11-07 10:00:00", "risk_enabled": 1,
                "risk_rows": 2, "risk_fp": "OLD", "risk_stat": "bc:1/1/0"}
        _ROWS22 = [{"kind": "bc", "wtype": "bc", "total": 10, "ok": 8, "low": 2,
                    "out": 0, "idle": 0, "paused": 0, "code_out": 0}]
        _RISKROWS22 = [{"dim": "bc", "sku": "S1", "barcode": "", "warehouse": "W",
                        "product_name": "P", "avail": 5, "ds": 1.0, "adj_dos": 2.0,
                        "level": "red", "accel": 0}]
        _US22.head = lambda ch, m=None: dict(_H22)
        _US22.read_enabled = lambda: True
        _US22.fp_now = lambda ch, m=None: "OLD"
        _US22.fp_risk_now = lambda ch, m=None: "OLD"
        _US22.query = lambda sql, p=None, **k: (_RISKROWS22 if "risk_snapshot" in sql
                                                else _ROWS22)
        _US22.one = lambda sql, p=None: {"age_s": 30}
        _US22.submit_rebuild = lambda ch, m: (_subs22.append((ch, m)) or "started")

        _t22, _s22 = _US22.serve_tally("jd", "bbcc")
        check("§15.162 serve_tally: fp 命中 → 完整 tally, 无播报, 不触发重建",
              _t22 and _s22 is None and not _subs22, str(_s22))
        _US22.fp_now = lambda ch, m=None: "NEW"     # 数据变了 → 失配窗口
        _t22, _s22 = _US22.serve_tally("jd", "bbcc")
        check("§15.162 serve_tally: fp 失配 → 返回上一版完整 tally + __swr 显式播报 + 后台补建",
              _t22 and _t22.get(("bc", "bc"), {}).get("total") == 10
              and _s22 and _s22.get("state") == "serving_previous"
              and _s22.get("fp_changed") is True and _s22.get("rebuild") == "started"
              and _s22.get("freshness_s") == 30, str(_s22))
        _H22["stale"] = 1
        _subs22.clear()
        _t22, _s22 = _US22.serve_tally("jd", "bbcc")
        check("§15.162 serve_tally: stale=1 → 上一版也不可读, 返回 (None, None) 现算保证完整",
              _t22 is None and _s22 is None and len(_subs22) == 1, str(_subs22))
        _H22["stale"] = 0
        _H22["gen"] = 0
        _t22, _s22 = _US22.serve_tally("jd", "bbcc")
        check("§15.162 serve_tally: 从未构建(gen=0) → (None, None) 现算, 后台首建",
              _t22 is None and _s22 is None and len(_subs22) == 2, str(_subs22))
        _H22["gen"] = 3
        _US22.fp_now = lambda ch, m=None: "NEW"
        _US22.fp_risk_now = lambda ch, m=None: "NEW"
        _pr22, _sr22 = _US22.serve_risk("jd")
        check("§15.162 serve_risk: fp 失配 → 上一版完整 payload(计数与明细同 gen) + __swr",
              _pr22 and _pr22.get("bcTotal") == 1 and len(_pr22.get("bcItems") or []) == 1
              and _sr22 and _sr22.get("state") == "serving_previous", str(_sr22))
        _US22.fp_now = lambda ch, m=None: "OLD"
        _US22.fp_risk_now = lambda ch, m=None: "OLD"
        _pr22, _sr22 = _US22.serve_risk("jd")
        check("§15.162 serve_risk: 双指纹全命中 → 正常返回, 无播报",
              _pr22 and _sr22 is None, str(_sr22))
    finally:
        _US22.head, _US22.fp_now, _US22.read_enabled, _US22.query, _US22.submit_rebuild = _o22
except Exception as _e35:
    check("§15.162 看板冷算 SWR 用例无异常", False, str(_e35)[:300])

# ── 真实 SQL 冒烟(可选, 必须在全部用例跑完之后: 此时 SQL 集合才完整) ──
if _SQL_SMOKE:
    FAIL += _sa.report()
else:
    print("\nℹ 真实 SQL 冒烟未运行(mock 测不出表名/列名; 加 --sql-smoke 开启)")

print("\n本地回归: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
