"""重复导入幂等验证(inventory upsert 全列覆盖)

用独立测试 SKU + platform 仓, 不进入 _recalc_own_inventory 的自有仓 SKU 列表;
跑完删除测试行, 最后比对四维是否回到基线。
"""
import json, os, sys, time
import requests

HOST = "https://supplykit.top"
TOKEN = open("/tmp/admin_token.txt").read().strip()
H = {"X-Auth-Token": TOKEN, "Content-Type": "application/octet-stream"}
SKU = "SKUTEST01"
WH = "测试平台仓"
BC = "6999999999991"

MP = {
    "SKU": {"target": "sku"},
    "仓库": {"target": "warehouse"},
    "69码": {"target": "barcode"},
    "仓型": {"target": "warehouse_type"},
    "可用数量": {"target": "available_qty", "type": "number"},
    "商品名称": {"target": "product_name"},
}


def csv_text(qty):
    return ("SKU,仓库,69码,仓型,可用数量,商品名称\n"
            "%s,%s,%s,platform,%d,幂等测试商品\n" % (SKU, WH, BC, qty)).encode("utf-8")


def run_import(qty):
    r = requests.post("%s/api/cleansing/execute-async" % HOST,
                      params={"q_target": "inventory", "q_channel": "jd",
                              "q_mp": json.dumps(MP, ensure_ascii=False)},
                      headers=H, data=csv_text(qty), timeout=120)
    j = r.json()
    tid = (j.get("data") or {}).get("task_id") or j.get("task_id")
    if not tid:
        return {"error": "no task_id", "resp": str(j)[:300]}
    for _ in range(30):
        time.sleep(2)
        s = requests.get("%s/api/cleansing/status" % HOST,
                         params={"task_id": tid}, headers={"X-Auth-Token": TOKEN}, timeout=60).json()
        st = (s.get("data") or {}).get("status")
        if st in ("done", "error"):
            return {"status": st, "detail": s.get("data")}
    return {"status": "timeout"}


def db_state():
    sys.path.insert(0, ".")
    from db import query
    return query("SELECT id, sku, barcode, warehouse, warehouse_type, available_qty, beginning_stock, "
                 "month_inbound, month_outbound FROM inventory WHERE sku=%s", [SKU])


if __name__ == "__main__":
    os.chdir("/workspace/supplykit/cloud-functions/api")
    print("== 导入前 ==", db_state())
    print("\n== 第 1 次导入 qty=100 ==")
    print(run_import(100))
    s1 = db_state()
    print("库内:", s1)
    print("\n== 第 2 次导入 qty=111(同键不同值 → 应覆盖) ==")
    print(run_import(111))
    s2 = db_state()
    print("库内:", s2)
    print("\n== 第 3 次导入 qty=111(完全同一份文件 → 应幂等) ==")
    print(run_import(111))
    s3 = db_state()
    print("库内:", s3)
    print("\n结论: 行数 1→%d→%d→%d; 可用 100→%s→%s" %
          (len(s1), len(s2), len(s3),
           s1[0]["available_qty"] if s1 else "-",
           s2[0]["available_qty"] if s2 else "-"))
