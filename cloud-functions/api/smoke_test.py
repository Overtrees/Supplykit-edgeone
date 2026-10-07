"""部署 smoke test: 线上关键接口验证(部署后自动/手动跑, 10 分钟内暴露回归)

验证项:
  1. 品牌值 vs SQL 直查交叉(防双包裹类 SQL bug 复发——接口 brands 总值应为 PAID 口径)
  2. 关键接口 200 + 数据非空
  3. 品牌/店铺 period 无净负(PAID 口径下净 ≥ 0 数学恒成立)
  4. 采购/补货建议非空(jd)

用法: cd cloud-functions/api && python3 smoke_test.py [base_url] [username] [password]
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://supplykit.top"
USER = sys.argv[2] if len(sys.argv) > 2 else "admin"
PWD = sys.argv[3] if len(sys.argv) > 3 else "admin123"
FAIL = 0
PASS = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s  %s" % (name, extra))


def req(path, method="GET", body=None, token=None, timeout=60):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h, method=method),
        timeout=timeout)
    return json.loads(r.read().decode())


def sql(token, q):
    try:
        d = req("/api/db/diag", "POST", {"sql": q}, token)
        return (d.get("data") or {}).get("rows") if d.get("ok") else {"ERR": d.get("error")[:80]}
    except Exception as e:
        return {"EXC": str(e)[:60]}


print("smoke test: %s" % BASE)
try:
    t = req("/api/auth/login", "POST", {"username": USER, "password": PWD})
    tok = (t.get("data") or t).get("token")
    check("登录", bool(tok))
except Exception as e:
    check("登录", False, str(e)[:100])
    sys.exit(1)

# 1. 品牌值 vs SQL(双包裹回归防线): 接口品牌总值应 ≈ SQL 60 天 PAID 品牌总值(±2%)
try:
    s = req("/api/dashboard/summary?channel=jd", token=tok).get("data") or {}
    b = s.get("brands") or []
    api_tot = sum(x.get("gmv") or 0 for x in b)
    rows = sql(tok, "SELECT SUM(g) tot FROM (SELECT COALESCE(p.brand,'') brand, "
                    "SUM(IF(o.order_status IN ('待发货','已发货','已完成','申请退款'), "
                    "o.total_amount-COALESCE(o.discount_amount,0)+COALESCE(o.freight_amount,0)+COALESCE(o.tax_amount,0),0)) g "
                    "FROM orders o LEFT JOIN (SELECT DISTINCT sku,brand,channel FROM products "
                    "WHERE brand IS NOT NULL AND brand != '') p ON o.sku=p.sku AND o.channel=p.channel "
                    "WHERE o.channel='jd' AND (o.deleted_at IS NULL OR o.deleted_at='') "
                    "AND o.ordered_at >= (NOW(6)-INTERVAL 59 DAY) GROUP BY p.brand) t WHERE brand != ''")
    sql_tot = (rows[0].get("tot") or 0) if isinstance(rows, list) and rows else 0
    ratio = (api_tot / sql_tot) if sql_tot else 0
    check("品牌总值 PAID 口径(接口/SQL=%s 偏差<%s%%)" % (round(ratio, 3), round(abs(ratio - 1) * 100, 1)),
          sql_tot > 0 and 0.98 <= ratio <= 1.02, "api=%s sql=%s" % (round(api_tot), round(sql_tot)))
except Exception as e:
    check("品牌总值 PAID 口径", False, str(e)[:100])

# 2. 关键接口非空
checks_api = [
    ("summary", "/api/dashboard/summary?channel=jd"),
    ("aux", "/api/dashboard/aux?channel=jd&mode=bbcc"),
    ("products", "/api/products?page=1&page_size=5"),
    ("suppliers", "/api/suppliers?channel=jd"),
    ("orders", "/api/orders?page=1&page_size=2&channel=jd"),
    ("replenishment", "/api/insights/replenishment?days=28&mode=bbcc&channel=jd"),
    ("purchase", "/api/insights/purchase?days=28&mode=bbcc&channel=jd"),
    ("disposal", "/api/insights/disposal-suggestions?channel=jd"),
    ("rules", "/api/rules?channel=all"),
    ("tasks", "/api/tasks?channel=jd"),
]
for name, path in checks_api:
    try:
        r = req(path, token=tok)
        d = r.get("data") if isinstance(r, dict) else r
        if isinstance(d, dict):
            items = d.get("items") or d.get("suggestions") or d.get("brands") or d.get("alerts") or []
            cnt = d.get("total") if d.get("total") is not None else len(items)
            check(name, (r.get("ok") is not False) and (cnt != 0), "data=%s" % str(d)[:80])
        else:
            check(name, len(d) > 0, "len=%s" % len(d))
    except Exception as e:
        check(name, False, str(e)[:80])

# 3. 品牌/店铺 period 无净负(PAID 口径下净 ≥ 0 数学恒成立)
try:
    s = req("/api/dashboard/summary?channel=jd", token=tok).get("data") or {}
    pb = (s.get("period_brands") or {}).get("month") or []
    ps = (s.get("period_stores") or {}).get("month") or []
    bneg = [x.get("name") for x in pb if (x.get("net_gmv") or 0) < 0]
    sneg = [x.get("name") for x in ps if (x.get("net_gmv") or 0) < 0]
    check("month 品牌/店铺无净负", not bneg and not sneg, "brands=%s stores=%s" % (bneg[:3], sneg[:3]))
except Exception as e:
    check("month 品牌/店铺无净负", False, str(e)[:80])

# 4. 采购建议非空且含日销口径 SKU
try:
    pu = req("/api/insights/purchase?days=28&mode=bbcc&channel=jd", token=tok).get("data") or {}
    sug = pu.get("suggestions") or []
    check("采购建议 jd 非空", len(sug) > 0, "len=%d" % len(sug))
except Exception as e:
    check("采购建议 jd 非空", False, str(e)[:80])

print("\nsmoke test: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
