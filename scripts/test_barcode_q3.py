"""补码象限3 + 效期非法值回显 验证

象限3: 档案 barcode 为空 + 流水带码 → 接受流水码, 并回写 products.barcode
同时顺带验: product 导入时 expiry_managed 非法值 → 归一为空 + 结果 message 回显

用完即清理(products/orders 测试行)。
"""
import json, os, sys, time
import requests

HOST = "https://supplykit.top"
TOKEN = open("/tmp/admin_token.txt").read().strip()
H = {"X-Auth-Token": TOKEN, "Content-Type": "application/octet-stream"}
SKU = "SKUTEST02"
BC = "6911111111111"
ORD = "TESTWB0001"

MP_ORDER = {
    "订单号": {"target": "order_no"},
    "商品编码": {"target": "sku"},
    "69码": {"target": "barcode"},
    "数量": {"target": "quantity", "type": "number"},
    "金额": {"target": "total_amount", "type": "number"},
    "下单时间": {"target": "ordered_at"},
}
MP_PROD = {
    "SKU": {"target": "sku"},
    "名称": {"target": "product_name"},
    "69码": {"target": "barcode"},
    "是否做效期管理": {"target": "expiry_managed"},
}


def post_import(target, mp, csv_bytes):
    r = requests.post("%s/api/cleansing/execute-async" % HOST,
                      params={"q_target": target, "q_channel": "jd",
                              "q_mp": json.dumps(mp, ensure_ascii=False)},
                      headers=H, data=csv_bytes, timeout=120)
    j = r.json()
    tid = j.get("task_id") or (j.get("data") or {}).get("task_id")
    if not tid:
        return {"sync": {k: j.get(k) for k in ("success", "failed", "duplicate", "message", "overlap")}}
    last = {}
    for _ in range(30):
        time.sleep(2)
        s = requests.get("%s/api/cleansing/status" % HOST,
                         params={"task_id": tid}, headers={"X-Auth-Token": TOKEN}, timeout=60).json()
        last = s
        if (s.get("status") or (s.get("data") or {}).get("status")) in ("done", "error"):
            break
    return {"status": last.get("status"), "message": last.get("message"),
            "result": last.get("result")}


if __name__ == "__main__":
    os.chdir("/workspace/supplykit/cloud-functions/api")
    sys.path.insert(0, ".")
    from db import query, execute

    print("== 0. 造无码档案 SKU=%s ==" % SKU)
    execute("DELETE FROM products WHERE sku=%s", [SKU])
    execute("INSERT INTO products(sku, barcode, product_name, channel, status, expiry_managed) "
            "VALUES(%s,'',%s,'jd','active','')", [SKU, "补码象限3测试品"])
    print("   档案:", query("SELECT sku, barcode, expiry_managed FROM products WHERE sku=%s", [SKU]))

    print("\n== 1. 导入带码订单(档案空 + 流水有码) ==")
    csv1 = ("订单号,商品编码,69码,数量,金额,下单时间\n"
            "%s,%s,%s,2,19.90,2026-11-01 09:00:00\n" % (ORD, SKU, BC)).encode("utf-8")
    print("   导入返回:", post_import("order", MP_ORDER, csv1))
    o = query("SELECT order_no, sku, barcode FROM orders WHERE order_no=%s", [ORD])
    p = query("SELECT sku, barcode FROM products WHERE sku=%s", [SKU])
    print("   订单行:", o)
    print("   档案回写后:", p)
    print("   判定: 订单保留流水码=%s | 档案被回写=%s" %
          (bool(o and o[0]["barcode"] == BC), bool(p and p[0]["barcode"] == BC)))

    print("\n== 2. 商品导入: 效期列取值非法('乱填') + 合法('否') ==")
    csv2 = ("SKU,名称,69码,是否做效期管理\n"
            "%s,补码象限3测试品,%s,乱填\n"
            "SKUTEST03,效期合法值测试,6922222222222,否\n" % (SKU, BC)).encode("utf-8")
    r2 = post_import("product", MP_PROD, csv2)
    print("   导入返回:", json.dumps(r2, ensure_ascii=False, default=str)[:400])
    print("   两行结果:", query("SELECT sku, barcode, expiry_managed FROM products WHERE sku IN (%s,'SKUTEST03')", [SKU]))

    print("\n== 3. 结果 message 是否回显非法值 ==")
    try:
        q = query("SELECT message, level, created_at FROM quality_logs WHERE source='cleansing' "
                  "ORDER BY id DESC LIMIT 3")
        for x in q:
            print("   ", dict(x))
    except Exception as e:
        print("   quality_logs 查询失败:", e)

    print("\n== 4. 清理 ==")
    execute("DELETE FROM orders WHERE order_no=%s", [ORD])
    execute("DELETE FROM products WHERE sku IN (%s,'SKUTEST03')", [SKU])
    print("   orders 残留:", query("SELECT COUNT(*) c FROM orders WHERE order_no=%s", [ORD]))
    print("   products 残留:", query("SELECT COUNT(*) c FROM products WHERE sku LIKE 'SKUTEST%'"))
