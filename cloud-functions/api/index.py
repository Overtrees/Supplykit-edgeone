"""Makers 原生后端入口(方案 B: 无 SQLite 适配, 直写 TiDB 方言)

构建器要求: 模块级行首 app = (正则 /^app\\s*=/m)
Makers FastAPI 框架模式: 路由无 /api 前缀(框架剥离后转发, root_path=/api)
"""
import os
import sys
import time
from datetime import datetime, timezone

# 函数包运行时 sys.path 只有函数根; 入口目录(api/)需自行加入(同目录模块 db.py 等)
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from fastapi import FastAPI
from fastapi import Request

from db import query, one, execute

app = FastAPI()


@app.get("/health")
def health():
    out = {"status": "ok", "db_backend": "tidb", "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        r = one("SELECT 1 AS ok")
        out["db"] = "ok" if r else "unknown"
    except Exception as e:
        out["db"] = "error: %s" % str(e)[:150]
        out["status"] = "degraded"
    try:
        r = one("SELECT COALESCE(MAX(date),'') AS m FROM daily_sales_snapshot")
        out["snapshot_max"] = (r or {}).get("m") or ""
    except Exception:
        pass
    return out


@app.get("/__p")
def path_probe(request: Request):
    """探针: 返回 FastAPI 视角的请求路径(定位前缀剥离行为)"""
    return {"path": request.url.path, "root_path": request.scope.get("root_path", "")}


@app.get("/debug/verify")
def debug_verify():
    """数据层验证: 三个核心聚合的原生 TiDB SQL"""
    out = {"ok": True}
    # 1. 看板 summary 核心(GMV 已支付口径)
    try:
        t0 = time.time()
        rows = query(
            "SELECT DATE(ordered_at) AS d, order_status, store, "
            "SUM(IF(order_status IN ('待发货','已发货','已完成','申请退款'), "
            "total_amount - COALESCE(discount_amount,0) + COALESCE(freight_amount,0) + COALESCE(tax_amount,0), 0)) AS g, "
            "COUNT(*) AS cnt FROM orders "
            "WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s "
            "GROUP BY DATE(ordered_at), order_status, store",
            ("jd", "2026-07-01 00:00:00"))
        out["q1_summary"] = {"rows": len(rows), "ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        out["q1_summary"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    # 2. 库存分组(补货核心)
    try:
        t0 = time.time()
        rows = query(
            "SELECT sku, warehouse_type, SUM(available_qty) AS avail, SUM(safety_qty) AS safety, "
            "SUM(in_transit_qty) AS transit FROM inventory WHERE channel=%s GROUP BY sku, warehouse_type",
            ("jd",))
        out["q2_inventory"] = {"rows": len(rows), "ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        out["q2_inventory"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    # 3. 快照日销
    try:
        t0 = time.time()
        rows = query(
            "SELECT date, sku, SUM(order_count) AS cnt FROM daily_sales_snapshot "
            "WHERE channel=%s AND date >= %s GROUP BY date, sku",
            ("jd", "2026-08-01"))
        out["q3_snapshot"] = {"rows": len(rows), "ms": round((time.time() - t0) * 1000)}
    except Exception as e:
        out["q3_snapshot"] = {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    # 4. 表行数
    try:
        rows = query("SHOW TABLE STATUS FROM `%s`" % os.environ.get("TIDB_DB", "supplykit"))
        out["table_rows"] = {r["Name"]: r["Rows"] for r in rows if r.get("Rows")}
    except Exception as e:
        out["table_rows"] = {"error": str(e)[:150]}
    return out