"""本地直连验证: 自有仓库存导入后可用是否重算(2026-11-01 修复)

问题: 库存导入(own)写完 beginning_stock 后, available 必须由 _recalc_own_inventory 重算
      (期初 + Σ入库 − Σ出库)。原重算白名单只有 inbound/outbound, 库存导完可用长期不动。

场景(全部用测试 SKU, 跑完清理):
  1. 商品: 不做效期管理
  2. 库存: 期初 100 / 在途 20  → available 应 = 100
  3. 入库: 30                 → available 应 = 130
  4. 库存再导(期初 100, 在途 25, 文件里还带 available_qty=100)
                              → available 必须仍 = 130(文件里的可用是干扰项, 不采信)
  5. 库存再导(期初 120)       → available 应 = 150
  6. 清理, 四维回基线
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "cloud-functions", "api")
sys.path.insert(0, API)

for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ.setdefault("JWT_SECRET", "local-test-secret")

from fastapi.testclient import TestClient  # noqa: E402
from index import app  # noqa: E402
from routes.common import create_token  # noqa: E402
from db import query, execute  # noqa: E402

SKU = "SKUTESTINV01"
WH = "测试自有仓"
BC = "6999999999992"
client = TestClient(app)
_tk = create_token("admin")
H = {"X-Auth-Token": _tk, "Authorization": "Bearer " + _tk,
     "Content-Type": "application/octet-stream"}


def post_import(target, csv_text, mapping):
    r = client.post("/cleansing/execute-async",
                    params={"q_target": target, "q_channel": "jd",
                            "q_mp": json.dumps(mapping, ensure_ascii=False)},
                    headers=H, content=csv_text.encode("utf-8"))
    j = r.json()
    tid = j.get("task_id") or (j.get("data") or {}).get("task_id")
    assert tid, "no task_id: %s" % str(j)[:300]
    for _ in range(60):
        time.sleep(0.6)
        s = client.get("/cleansing/status", params={"task_id": tid},
                       headers=H).json()
        if (s.get("status") or "") in ("done", "error"):
            break
    # 评估联动任务(重算/批次重建在这里)
    for _ in range(120):
        e = client.get("/cleansing/eval-status", params={"task_id": tid + "_eval"},
                       headers=H).json()
        if (e.get("status") or "") in ("done", "error"):
            return s, e
        time.sleep(0.6)
    return s, {"status": "timeout"}


def state():
    r = query("SELECT available_qty, beginning_stock, in_transit_qty, barcode, warehouse_type "
              "FROM inventory WHERE sku=%s AND channel='jd'", [SKU])
    return r[0] if r else None


def four_dims():
    return query(
        "SELECT (SELECT COUNT(*) FROM inventory WHERE COALESCE(deleted_at,'')='') n,"
        " (SELECT COALESCE(SUM(available_qty),0) FROM inventory WHERE COALESCE(deleted_at,'')='') avail,"
        " (SELECT COALESCE(SUM(beginning_stock),0) FROM inventory WHERE COALESCE(deleted_at,'')='') bgn,"
        " (SELECT COALESCE(SUM(month_inbound),0) FROM inventory WHERE COALESCE(deleted_at,'')='') mi,"
        " (SELECT COALESCE(SUM(month_outbound),0) FROM inventory WHERE COALESCE(deleted_at,'')='') mo")[0]


def cleanup():
    for t in ("batches", "inventory", "inbound_records", "outbound_records"):
        execute("DELETE FROM %s WHERE sku=%%s AND channel='jd'" % t, [SKU])
    execute("DELETE FROM products WHERE sku=%s", [SKU])
    try:
        from routes.analysis_cache import invalidate_all
        invalidate_all()
    except Exception as e:
        print("cache invalidate failed:", e)


PROD_MP = {"SKU": {"target": "sku"}, "名称": {"target": "product_name"},
           "69码": {"target": "barcode"}, "是否做效期管理": {"target": "expiry_managed"}}
INV_MP = {"SKU": {"target": "sku"}, "仓库": {"target": "warehouse"},
          "69码": {"target": "barcode"}, "期初库存": {"target": "beginning_stock", "type": "number"},
          "可用数量": {"target": "available_qty", "type": "number"},
          "在途": {"target": "in_transit_qty", "type": "number"}}
INB_MP = {"SKU": {"target": "sku"}, "仓库": {"target": "warehouse"},
          "69码": {"target": "barcode"}, "数量": {"target": "quantity", "type": "number"},
          "入库日期": {"target": "inbound_date", "type": "date"},
          "仓型": {"target": "warehouse_type"}}

_FAIL=[]
def check(name, cond, actual=None):
    print(("  PASS " if cond else "  FAIL ")+name+("" if cond else "  → 实得 %s" % (actual,)))
    if not cond:
        _FAIL.append(name)

print("=== 四维基线 ===", four_dims())
_fresh = query("SELECT * FROM data_freshness WHERE doc_type='inventory' AND channel='jd'")
cleanup()

# 1. 商品
s, e = post_import("product", "SKU,名称,69码,是否做效期管理\n%s,重算测试品,%s,否\n" % (SKU, BC), PROD_MP)
print("\n[1] 商品导入:", s.get("status"), "| eval:", e.get("status"))

# 2. 库存 期初 100 / 在途 20
csv2 = "SKU,仓库,69码,期初库存,可用数量,在途\n%s,%s,%s,100,100,20\n" % (SKU, WH, BC)
s, e = post_import("inventory", csv2, INV_MP)
print("[2] 库存首次导入:", s.get("status"), "|", s.get("message") or ""[:200])
print("    state:", state())
check("首次导入 available=100", state() and state()["available_qty"] == 100, state())

# 3. 入库 30
s, e = post_import("inbound", "SKU,仓库,69码,数量,入库日期,仓型\n%s,%s,%s,30,2026-10-30,own\n" % (SKU, WH, BC), INB_MP)
print("[3] 入库 30:", s.get("status"))
print("    state:", state())
check("入库后 available=130", state()["available_qty"] == 130, state())

# 4. 库存再导: 期初不变 100, 在途 25, 文件里 available_qty 仍写 100(干扰项)
csv4 = "SKU,仓库,69码,期初库存,可用数量,在途\n%s,%s,%s,100,100,25\n" % (SKU, WH, BC)
s, e = post_import("inventory", csv4, INV_MP)
print("\n[4] 库存重导(期初100/文件可用100/在途25):", s.get("status"))
print("    state:", state())
check("库存重导后 available 仍 130(不采信文件可用)", state()["available_qty"] == 130, state())
check("在途更新为25", state()["in_transit_qty"] == 25, state())

# 5. 库存再导: 期初改 120
csv5 = "SKU,仓库,69码,期初库存,可用数量,在途\n%s,%s,%s,120,120,25\n" % (SKU, WH, BC)
s, e = post_import("inventory", csv5, INV_MP)
print("\n[5] 库存重导(期初120):", s.get("status"))
print("    state:", state())
print("    提示语:", s.get("message") or "")
check("期初改120 → available=150", state()["available_qty"] == 150, state())

# 6. 效期商品(是否做效期管理=是) 只有期初/无到期日批次 → 结果页必须提示缺口
execute("UPDATE products SET expiry_managed='yes' WHERE sku=%s", [SKU])
s6, e6 = post_import("inventory", csv5, INV_MP)
_msg6 = s6.get("message") or ""
print("\n[6] 效期商品库存导入提示:", _msg6)
check("提示含批次缺口(效期商品无到期日批次)", "不会进临期预警" in _msg6, _msg6)

# 7. 效期商品入库缺到期日 → 结果页提示(原提示写在废弃函数里, 从未生效)
s7, _e7 = post_import("inbound", "SKU,仓库,69码,数量,入库日期,仓型\n%s,%s,%s,10,2026-10-31,own\n" % (SKU, WH, BC), INB_MP)
_msg7 = s7.get("message") or ""
print("\n[7] 入库缺到期日提示:", _msg7)
check("提示含入库缺到期日", "无到期日期" in _msg7, _msg7)

print("\n=== 清理 ===")
cleanup()
if _fresh:
    execute("UPDATE data_freshness SET last_import_at=%s, last_biz_date=%s, rows_imported=%s "
            "WHERE doc_type='inventory' AND channel='jd'",
            [_fresh[0].get("last_import_at"), _fresh[0].get("last_biz_date"),
             _fresh[0].get("rows_imported")])
print("=== 四维复核 ===", four_dims())
print("\nRESIDUE!" if query("SELECT id FROM inventory WHERE sku=%s", [SKU]) else ("\nALL PASS" if not _FAIL else "\nFAILED: %s" % _FAIL))
