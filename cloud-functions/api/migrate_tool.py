"""Phase2 迁移工具(自包含): 建表/seed/RU 实测, 随入口挂载 /migrate/* 端点"""
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS `alerts` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `alert_type` VARCHAR(30) DEFAULT '', `title` TEXT, `description` TEXT, `severity` TEXT, `status` VARCHAR(30) DEFAULT 'active', `source` VARCHAR(30) DEFAULT '', `related_sku` TEXT, `related_order_no` TEXT, `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `channel` VARCHAR(20) DEFAULT 'jd', `pushed` BIGINT DEFAULT 0, `related_rule_id` BIGINT DEFAULT 0, `warehouse_type` VARCHAR(20) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `batches` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64) DEFAULT '', `warehouse` VARCHAR(64) DEFAULT '', `warehouse_type` VARCHAR(20) DEFAULT '', `channel` VARCHAR(20) DEFAULT 'jd', `prod_date` DATETIME, `exp_date` DATETIME, `qty` BIGINT DEFAULT 0, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `cleansing_errors` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `task_id` VARCHAR(64) DEFAULT '', `row_index` BIGINT DEFAULT 0, `source_file` TEXT, `error_type` VARCHAR(30) DEFAULT '', `field_name` VARCHAR(50) DEFAULT '', `raw_value` TEXT, `error_message` TEXT, `raw_data` TEXT, `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `cleansing_templates` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `name` TEXT, `doc_type` VARCHAR(30) DEFAULT 'order', `mapping` TEXT, `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `custom_fields` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `target` VARCHAR(50), `key` VARCHAR(64), `label` TEXT, `type` TEXT, `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `daily_sales_snapshot` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `date` DATETIME, `channel` VARCHAR(20) DEFAULT 'jd', `sku` VARCHAR(64), `warehouse` VARCHAR(64) DEFAULT '', `order_count` BIGINT DEFAULT 0, UNIQUE(`date`, `channel`, `sku`, `warehouse`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `daily_stats` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `date` DATETIME, `channel` VARCHAR(20) DEFAULT 'jd', `store` VARCHAR(128) DEFAULT '', `sku` VARCHAR(64) DEFAULT '', `order_status` VARCHAR(30) DEFAULT '', `gmv` DOUBLE DEFAULT 0, `order_count` BIGINT DEFAULT 0, `quantity` BIGINT DEFAULT 0, UNIQUE(`date`, `channel`, `store`, `sku`, `order_status`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `disposal_records` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64) DEFAULT '', `warehouse` VARCHAR(64) DEFAULT '', `warehouse_type` VARCHAR(20) DEFAULT '', `channel` VARCHAR(20) DEFAULT 'jd', `level` VARCHAR(20) DEFAULT '', `turnover_days` DOUBLE DEFAULT 0, `reason` VARCHAR(100) DEFAULT '', `action` VARCHAR(50) DEFAULT '', `note` VARCHAR(200) DEFAULT '', `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `events` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `event_type` VARCHAR(30), `entity_type` VARCHAR(50) DEFAULT '', `entity_id` VARCHAR(64) DEFAULT '', `title` TEXT, `payload` TEXT, `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `inbound_records` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64) DEFAULT '', `product_name` TEXT, `quantity` BIGINT DEFAULT 0, `supplier` TEXT, `inbound_date` DATETIME, `channel` VARCHAR(20) DEFAULT 'jd', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `prod_date` DATETIME, `exp_date` DATETIME, `warehouse` VARCHAR(64) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `inventory` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64), `product_name` TEXT, `store` VARCHAR(128) DEFAULT '', `warehouse` VARCHAR(64) DEFAULT '', `available_qty` BIGINT DEFAULT 0, `locked_qty` BIGINT DEFAULT 0, `in_transit_qty` BIGINT DEFAULT 0, `safety_qty` BIGINT DEFAULT 0, `safety_days` DOUBLE DEFAULT 0, `warehouse_type` VARCHAR(20) DEFAULT 'platform', `raw_data` TEXT, `source` VARCHAR(30) DEFAULT '', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `channel` VARCHAR(20) DEFAULT 'jd', `beginning_stock` BIGINT DEFAULT 0, `month_inbound` BIGINT DEFAULT 0, `month_outbound` BIGINT DEFAULT 0, `turnover_days` DOUBLE DEFAULT 0, `c_transit` BIGINT DEFAULT 0, `weight` DOUBLE DEFAULT 0, `volume` DOUBLE DEFAULT 0, `barcode` VARCHAR(64) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `orders` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `order_no` VARCHAR(64), `store` VARCHAR(128) DEFAULT '', `warehouse` VARCHAR(64) DEFAULT '', `sku` VARCHAR(64) DEFAULT '', `product_name` TEXT, `quantity` BIGINT DEFAULT 0, `unit_price` DOUBLE DEFAULT 0, `total_amount` DOUBLE DEFAULT 0, `data_source` VARCHAR(50) DEFAULT '', `order_status` VARCHAR(30) DEFAULT '', `ordered_at` DATETIME, `platform` VARCHAR(30) DEFAULT '', `supplier` TEXT, `remark` TEXT, `parent_order_no` TEXT, `raw_data` TEXT, `source` VARCHAR(30) DEFAULT '', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `channel` VARCHAR(20) DEFAULT 'jd', `paid_at` DATETIME, `barcode` VARCHAR(64) DEFAULT '', `deleted_at` TEXT, `freight_amount` DOUBLE DEFAULT 0, `subsidy_amount` DOUBLE DEFAULT 0, `tax_amount` DOUBLE DEFAULT 0, `discount_amount` DOUBLE DEFAULT 0, `actual_amount` DOUBLE DEFAULT 0) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `outbound_records` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64) DEFAULT '', `product_name` TEXT, `quantity` BIGINT DEFAULT 0, `target_warehouse` TEXT, `outbound_date` DATETIME, `channel` VARCHAR(20) DEFAULT 'jd', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `prod_date` DATETIME, `exp_date` DATETIME, `warehouse` VARCHAR(64) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `products` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64), `product_name` TEXT, `store` VARCHAR(128) DEFAULT '', `category` TEXT, `price` DOUBLE DEFAULT 0, `box_qty` BIGINT DEFAULT 1, `status` VARCHAR(30) DEFAULT 'active', `supplier_code` VARCHAR(64) DEFAULT '', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `barcode` VARCHAR(64) DEFAULT '', `weight` DOUBLE DEFAULT 0, `volume` DOUBLE DEFAULT 0, `channel` VARCHAR(20) DEFAULT 'jd', `unit` TEXT, `deleted_at` TEXT, `best_before` TEXT, `brand` TEXT, UNIQUE(`sku`, `channel`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `purchase_orders` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `sku` VARCHAR(64), `store` VARCHAR(128) DEFAULT '', `product_name` TEXT, `suggested_qty` BIGINT DEFAULT 0, `actual_qty` BIGINT DEFAULT 0, `arrival_date` DATETIME, `status` VARCHAR(30) DEFAULT 'pending', `channel` VARCHAR(20) DEFAULT 'jd', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `quality_logs` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `log_type` VARCHAR(30) DEFAULT '', `level` VARCHAR(20) DEFAULT '', `message` TEXT, `details` TEXT, `source` VARCHAR(30) DEFAULT '', `entity_type` VARCHAR(50) DEFAULT '', `entity_id` VARCHAR(64) DEFAULT '', `field_name` VARCHAR(50) DEFAULT '', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `replenishment_config` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `key` VARCHAR(64), `value` TEXT, `channel` VARCHAR(20) DEFAULT 'jd', `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(`key`, `channel`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `replenishment_config_history` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `key` VARCHAR(64), `old_value` TEXT, `new_value` TEXT, `channel` VARCHAR(20) DEFAULT 'jd', `mode` VARCHAR(20) DEFAULT '', `operator` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `rules` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `name` TEXT, `event` VARCHAR(30) DEFAULT '', `condition_json` TEXT, `alert_type` VARCHAR(30) DEFAULT '', `alert_title` TEXT, `alert_desc` TEXT, `severity` TEXT, `is_active` BIGINT DEFAULT 1, `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `mode` VARCHAR(20) DEFAULT '', `channel` VARCHAR(20) DEFAULT 'jd', `deleted_at` TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `suppliers` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `supplier_code` VARCHAR(64) UNIQUE, `supplier_name` TEXT, `contact_person` TEXT, `contact_phone` TEXT, `score` BIGINT DEFAULT 0, `status` VARCHAR(30) DEFAULT 'active', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `channel` VARCHAR(20) DEFAULT 'jd', `brand` TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `sync_tasks` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `task_id` VARCHAR(64) DEFAULT '', `task_type` TEXT, `status` VARCHAR(30) DEFAULT 'pending', `params` TEXT, `result` TEXT, `channel` VARCHAR(20) DEFAULT 'jd', `owner_id` TEXT, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `users` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `username` VARCHAR(64) UNIQUE, `password_hash` TEXT, `role` VARCHAR(20) DEFAULT 'user', `is_active` BIGINT DEFAULT 1, `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP, `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `warehouse_registry` (`id` BIGINT PRIMARY KEY AUTO_INCREMENT, `warehouse` VARCHAR(64) DEFAULT '', `warehouse_type` VARCHAR(20) DEFAULT '', `channel` VARCHAR(20) DEFAULT 'jd', UNIQUE(`warehouse`, `channel`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE UNIQUE INDEX `idx_orders_no_sku` ON `orders` (order_no, sku);
CREATE INDEX `idx_orders_order_no` ON `orders` (order_no);
CREATE INDEX `idx_inventory_sku` ON `inventory` (sku);
CREATE INDEX `idx_products_sku` ON `products` (sku);
CREATE INDEX `idx_alerts_status` ON `alerts` (status);
CREATE UNIQUE INDEX `idx_po_sku_store` ON `purchase_orders` (sku, store);
CREATE INDEX `idx_events_type` ON `events` (event_type);
CREATE INDEX `idx_quality_logs_level` ON `quality_logs` (level);
CREATE INDEX `idx_orders_ordered_at` ON `orders` (ordered_at);
CREATE INDEX `idx_orders_status` ON `orders` (order_status);
CREATE INDEX `idx_orders_store` ON `orders` (store);
CREATE INDEX `idx_orders_sku` ON `orders` (sku);
CREATE INDEX `idx_orders_data_source` ON `orders` (data_source);
CREATE INDEX `idx_inventory_store` ON `inventory` (store);
CREATE INDEX `idx_events_created_at` ON `events` (created_at);
CREATE INDEX `idx_orders_sku_ordered_at` ON `orders` (sku, ordered_at, channel);
CREATE INDEX `idx_inventory_sku_wh_ch` ON `inventory` (sku, warehouse_type, channel);
CREATE INDEX `idx_inventory_wh_ch` ON `inventory` (warehouse_type, channel);
CREATE INDEX `idx_daily_stats_date` ON `daily_stats` (date, channel);
CREATE INDEX `idx_products_sku_ch` ON `products` (sku, channel);
CREATE INDEX `idx_orders_ch_status` ON `orders` (channel, order_status);
CREATE INDEX `idx_orders_ch_ordered_at` ON `orders` (channel, order_status, ordered_at);
CREATE INDEX `idx_inbound_date` ON `inbound_records` (inbound_date);
CREATE INDEX `idx_outbound_date` ON `outbound_records` (outbound_date);
CREATE INDEX `idx_snapshot_date` ON `daily_sales_snapshot` (date, channel, sku);
CREATE UNIQUE INDEX `idx_inventory_sku_wh_uq` ON `inventory` (sku, warehouse, channel);
CREATE UNIQUE INDEX `idx_inbound_sku_date` ON `inbound_records` (sku, inbound_date);
CREATE UNIQUE INDEX `idx_outbound_sku_date` ON `outbound_records` (sku, outbound_date);
CREATE INDEX `idx_batches_sku_wh` ON `batches` (sku, warehouse, channel);
CREATE UNIQUE INDEX `idx_inbound_records_unique` ON `inbound_records` (sku, warehouse, channel, prod_date, exp_date, inbound_date);
CREATE UNIQUE INDEX `idx_outbound_records_unique` ON `outbound_records` (sku, warehouse, channel, prod_date, exp_date, outbound_date);
"""


"""Phase2 迁移工具端点(临时): 建表 + 小批量 seed + RU 实测

Makers 云函数临时工具, Phase2(TiDB 数据层)期间使用, 迁移完成后删除。
所有操作幂等, 只影响 TiDB 侧数据。
"""
import os
from datetime import datetime, timezone

import pymysql


_BRANDS = ["禾味", "山泉", "椒香", "酱乡", "净洁", "薯乐", "谷香", "醇味", "鲜禾", "禾田"]
_STORES = ["自营旗舰店", "自营直营店", "调味品专营店", "零食旗舰店", "日化专营店"]
_WH = [("华东C仓", "platform"), ("华南C仓", "platform"), ("华北C仓", "platform"), ("B仓", "platform_b"), ("自有仓", "own")]
_SUP = [("SUP001", "云味食品(演示)"), ("SUP002", "禾香调味(演示)"), ("SUP003", "净洁日化(演示)")]


def _cfg():
    return {
        "host": os.environ.get("TIDB_HOST"),
        "port": int(os.environ.get("TIDB_PORT", "4000")),
        "user": os.environ.get("TIDB_USER"),
        "password": os.environ.get("TIDB_PASSWORD"),
        "db": os.environ.get("TIDB_DB", "supplykit"),
    }


def _conn():
    c = _cfg()
    return pymysql.connect(host=c["host"], port=c["port"], user=c["user"], password=c["password"],
                           db=c["db"], ssl={"ca": None}, connect_timeout=15,
                           read_timeout=90, write_timeout=90, autocommit=True)


def build():
    """执行建表 DDL(幂等, 抗 TiDB 异步 DDL 竞争)"""
    import re as _re2
    import time as _t2
    out = {"tables_ok": [], "indexes_ok": [], "fail": []}
    conn = _conn()
    cur = conn.cursor()
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            _is_index = "CREATE INDEX" in stmt.upper()
            _name = _tbl = None
            if _is_index:
                _m = _re2.search(r"INDEX `([\w]+)` ON `([\w]+)`", stmt)
                if _m:
                    _name, _tbl = _m.group(1), _m.group(2)
                # 先 DROP 旧索引(DROP TABLE 异步竞争下防 Duplicate key name)
                if _name and _tbl:
                    try:
                        cur.execute("DROP INDEX IF EXISTS `%s` ON `%s`" % (_name, _tbl))
                    except Exception:
                        pass
            cur.execute(stmt)
            if "CREATE TABLE" in stmt.upper():
                out["tables_ok"].append(stmt.split("`")[1])
            else:
                out["indexes_ok"].append(_name or stmt.split("`")[3])
        except Exception as e:
            # 异步 DDL 未完成: 等 3s 重试一次
            if "Duplicate" in str(e) and ("INDEX" in stmt.upper() or "TABLE" in stmt.upper()):
                _t2.sleep(3)
                try:
                    cur.execute(stmt)
                    if "CREATE TABLE" in stmt.upper():
                        out["tables_ok"].append(stmt.split("`")[1])
                    else:
                        out["indexes_ok"].append(_name or stmt.split("`")[3])
                    continue
                except Exception as e2:
                    out["fail"].append("%s: %s" % (stmt[:80], str(e2)[:200]))
                    continue
            out["fail"].append("%s: %s" % (stmt[:80], str(e)[:200]))
    cur.close()
    conn.close()
    return {"ok": len(out["tables_ok"]), "tables": out["tables_ok"],
            "indexes": len(out["indexes_ok"]), "fail": out["fail"]}


def seed_small(n_orders: int = 5000):
    """小批量 seed: 100 商品 / 3 供应商 / 600 库存 / n_orders 订单(虚拟数据)"""
    out = {}
    conn = _conn()
    cur = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 清空旧测试数据(幂等)
    for t in ["orders", "inventory", "products", "suppliers", "daily_sales_snapshot"]:
        try:
            cur.execute("DELETE FROM `%s`" % t)
        except Exception:
            pass

    # 商品 + 供应商
    prod_rows = []
    sup_rows = [(c, n) for c, n in _SUP]
    import random
    random.seed(42)
    for i in range(1, 101):
        sku = "SKU%04d" % i
        brand = _BRANDS[i % len(_BRANDS)]
        prod_rows.append((sku, brand + "调味料%d号" % i, _STORES[i % len(_STORES)], brand,
                          round(random.uniform(5, 80), 2), random.randint(6, 24), "jd", "active"))
    cur.executemany("INSERT INTO products(sku, product_name, store, brand, price, box_qty, channel, status) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE product_name=VALUES(product_name)", prod_rows)
    cur.executemany("INSERT INTO suppliers(supplier_code, supplier_name, channel) "
                    "VALUES(%s,%s,'jd') ON DUPLICATE KEY UPDATE supplier_name=VALUES(supplier_name)", sup_rows)

    # 库存: 每 SKU 6 仓, 部分低库存
    inv_rows = []
    for i in range(1, 101):
        sku = "SKU%04d" % i
        for wh, wt in _WH:
            avail = random.randint(0, 200)
            if i % 8 == 0:
                avail = 0  # 12% 缺货场景
            safety = random.randint(20, 80)
            inv_rows.append((sku, wh, wt, avail, random.randint(0, 30), safety, "jd"))
    cur.executemany("INSERT INTO inventory(sku, warehouse, warehouse_type, available_qty, in_transit_qty, safety_qty, channel) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)", inv_rows)

    # 订单: n 天历史, 状态分布
    statuses = ["已完成", "已发货", "待发货", "申请退款", "待确认"]
    order_rows = []
    from datetime import timedelta
    for i in range(n_orders):
        sku = "SKU%04d" % (i % 100 + 1)
        qty = random.randint(1, 5)
        price = random.uniform(5, 80)
        d = (datetime.now(timezone.utc) - timedelta(days=random.randint(0, 60))).strftime("%Y-%m-%d")
        st = "已完成" if random.random() < 0.75 else random.choice(statuses)
        order_rows.append(("NO%010d" % i, sku, qty, round(qty * price, 2), st, d + " 10:00:00", "jd", _STORES[i % 5]))
    cur.executemany("INSERT INTO orders(order_no, sku, quantity, total_amount, order_status, ordered_at, channel, store) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", order_rows)

    # 日销快照(最近 60 天 × 100 SKU × 5 仓)
    snap_rows = []
    for i in range(1, 101):
        sku = "SKU%04d" % i
        for wh, wt in _WH:
            if wt != "platform":
                continue
            for k in range(60):
                d = (datetime.now(timezone.utc) - timedelta(days=k)).strftime("%Y-%m-%d")
                cnt = random.randint(0, 8) if random.random() > 0.3 else 0
                if cnt:
                    snap_rows.append((d, "jd", sku, wh, cnt))
    for batch in [snap_rows[i:i + 5000] for i in range(0, len(snap_rows), 5000)]:
        cur.executemany("INSERT INTO daily_sales_snapshot(date, channel, sku, warehouse, order_count) "
                        "VALUES(%s,%s,%s,%s,%s)", batch)

    cur.close()
    conn.close()
    out["products"] = len(prod_rows)
    out["inventory"] = len(inv_rows)
    out["orders"] = n_orders
    out["snapshot_rows"] = len(snap_rows)
    out["note"] = "虚拟演示数据, 用于 RU 实测"
    return out


def ru_test():
    """跑关键业务查询并 EXPLAIN ANALYZE(观察 RU/耗时)"""
    out = {}
    conn = _conn()
    cur = conn.cursor()

    # 查询1: 看板 summary 核心聚合(模拟全量重建)
    q1 = ("SELECT substr(ordered_at,1,10) as d, order_status, store, "
          "SUM(total_amount) as g, COUNT(*) as cnt FROM orders "
          "WHERE channel='jd' AND ordered_at >= '2026-07-01' GROUP BY d, order_status, store")
    try:
        cur.execute("EXPLAIN ANALYZE " + q1)
        rows = cur.fetchall()
        out["q1_summary_analyze"] = [str(r)[:200] for r in rows[-3:]]
        cur.execute(q1)
        out["q1_rows"] = len(cur.fetchall())
    except Exception as e:
        out["q1_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])

    # 查询2: 补货核心(库存×日销)
    q2 = ("SELECT i.sku, SUM(i.available_qty) avail, SUM(i.safety_qty) safety "
          "FROM inventory i WHERE i.channel='jd' GROUP BY i.sku")
    try:
        cur.execute("EXPLAIN ANALYZE " + q2)
        rows = cur.fetchall()
        out["q2_inventory_analyze"] = [str(r)[:200] for r in rows[-3:]]
        cur.execute(q2)
        out["q2_rows"] = len(cur.fetchall())
    except Exception as e:
        out["q2_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])

    # 查询3: 快照日销读取
    q3 = ("SELECT date, sku, SUM(order_count) FROM daily_sales_snapshot "
          "WHERE channel='jd' AND date >= '2026-08-01' GROUP BY date, sku")
    try:
        cur.execute("EXPLAIN ANALYZE " + q3)
        rows = cur.fetchall()
        out["q3_snapshot_analyze"] = [str(r)[:200] for r in rows[-3:]]
        cur.execute(q3)
        out["q3_rows"] = len(cur.fetchall())
    except Exception as e:
        out["q3_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])

    # 表统计
    try:
        cur.execute("SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema=%s", (os.environ.get("TIDB_DB", "supplykit"),))
        out["table_rows"] = {r[0]: r[1] for r in cur.fetchall() if r[1]}
    except Exception as e:
        out["tables_error"] = str(e)[:200]

    cur.close()
    conn.close()
    return out


def tables():
    """列出 TiDB 中的表(验证建表结果)"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT table_name, table_rows, engine FROM information_schema.tables WHERE table_schema=%s ORDER BY table_name",
                (os.environ.get("TIDB_DB", "supplykit"),))
    rows = [{"name": r[0], "rows": r[1], "engine": r[2]} for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows
