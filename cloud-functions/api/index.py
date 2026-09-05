"""Makers 入口 —— 最小版 + TiDB 连接验证端点 + Phase2 迁移工具"""
import os
from fastapi import FastAPI
import pymysql


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "msg": "supplykit-edgeone"}


@app.get("/tidb-test")
def tidb_test():
    """验证 Makers 云函数 -> TiDB 链路: 认证/TLS/时区/建库/读写"""
    out = {}
    host = os.environ.get("TIDB_HOST")
    port = int(os.environ.get("TIDB_PORT", "4000"))
    user = os.environ.get("TIDB_USER")
    password = os.environ.get("TIDB_PASSWORD")
    db = os.environ.get("TIDB_DB", "supplykit")
    out["env"] = {"host": bool(host), "port": bool(port), "user": bool(user), "password": bool(password)}
    if not (host and user and password):
        out["error"] = "缺少 TIDB_HOST/TIDB_USER/TIDB_PASSWORD"
        return out
    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            ssl={"ca": None}, connect_timeout=10,
            read_timeout=20, write_timeout=20,
        )
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        out["version"] = cur.fetchone()[0]
        cur.execute("SELECT @@system_time_zone, @@time_zone, NOW()")
        row = cur.fetchone()
        out["timezone"] = {"system": row[0], "session": row[1], "now": str(row[2])}
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}`")
        cur.execute(f"USE `{db}`")
        cur.execute("CREATE TABLE IF NOT EXISTS _conn_test (id INT PRIMARY KEY, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("INSERT INTO _conn_test (id) VALUES (1) ON DUPLICATE KEY UPDATE ts=CURRENT_TIMESTAMP")
        conn.commit()
        cur.execute("SELECT id, ts FROM _conn_test")
        out["rw"] = str(cur.fetchone())
        cur.execute("DROP TABLE _conn_test")
        conn.commit()
        cur.close(); conn.close()
        out["status"] = "OK"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


@app.get("/")
def root():
    return {"ok": True}


# ─── Phase2 迁移工具端点(临时, 完成后删除) ─────────────────────────────

@app.get("/migrate/build")
def migrate_build_route():
    """建表 DDL(幂等)"""
    try:
        return build()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@app.get("/migrate/seed")
def migrate_seed_route(n_orders: int = 5000):
    """小批量虚拟数据(默认 5000 单)"""
    try:
        return seed_small(n_orders=n_orders)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@app.get("/migrate/ru-test")
def migrate_ru_route():
    """关键查询 EXPLAIN ANALYZE(RU 实测)"""
    try:
        return ru_test()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@app.get("/migrate/tables")
def migrate_tables_route():
    """TiDB 表清单"""
    try:
        return {"tables": tables()}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}



# ─── Phase2: TiDB schema DDL ─────────────────────────────────────
"""TiDB schema DDL (从 SQLite schema 自动转换, 2026-09-05 Phase2)"""
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS `alerts` (id BIGINT PRIMARYKEY AUTO_INCREMENT, alert_type VARCHAR(255) DEFAULT '', title VARCHAR(255) DEFAULT '', description VARCHAR(255) DEFAULT '', severity VARCHAR(255) DEFAULT 'info', status VARCHAR(255) DEFAULT 'active', source VARCHAR(255) DEFAULT '', related_sku VARCHAR(255) DEFAULT '', related_order_no VARCHAR(255) DEFAULT '', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , channel VARCHAR(255) DEFAULT 'jd', pushed BIGINT DEFAULT 0, related_rule_id BIGINT DEFAULT 0, warehouse_type VARCHAR(255) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `batches` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) DEFAULT '', warehouse VARCHAR(255) DEFAULT '', warehouse_type VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', prod_date VARCHAR(255) DEFAULT '', exp_date VARCHAR(255) DEFAULT '', qty BIGINT DEFAULT 0, created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `cleansing_errors` (id BIGINT PRIMARYKEY AUTO_INCREMENT, task_id VARCHAR(255) DEFAULT '', row_index BIGINT DEFAULT 0, source_file VARCHAR(255) DEFAULT '', error_type VARCHAR(255) DEFAULT '', field_name VARCHAR(255) DEFAULT '', raw_value VARCHAR(255) DEFAULT '', error_message VARCHAR(255) DEFAULT '', raw_data VARCHAR(255) DEFAULT '{}', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `cleansing_templates` (id BIGINT PRIMARYKEY AUTO_INCREMENT, name VARCHAR(255) NOT NULL, doc_type VARCHAR(255) DEFAULT 'order', mapping VARCHAR(255) DEFAULT '{}', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `custom_fields` (id BIGINT PRIMARYKEY AUTO_INCREMENT, target VARCHAR(255) NOT NULL, `key` VARCHAR(255) NOT NULL, label VARCHAR(255) DEFAULT '', type VARCHAR(255) DEFAULT 'string', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `daily_sales_snapshot` (id BIGINT PRIMARYKEY AUTO_INCREMENT, date VARCHAR(255) NOT NULL, channel VARCHAR(255) DEFAULT 'jd', sku VARCHAR(255) NOT NULL, warehouse VARCHAR(255) DEFAULT '', order_count BIGINT DEFAULT 0, UNIQUE(date, channel, sku, warehouse)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `daily_stats` (id BIGINT PRIMARYKEY AUTO_INCREMENT, date VARCHAR(255) NOT NULL, channel VARCHAR(255) DEFAULT 'jd', store VARCHAR(255) DEFAULT '', sku VARCHAR(255) DEFAULT '', order_status VARCHAR(255) DEFAULT '', gmv DOUBLE DEFAULT 0, order_count BIGINT DEFAULT 0, quantity BIGINT DEFAULT 0, UNIQUE(date, channel, store, sku, order_status)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `disposal_records` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) DEFAULT '', warehouse VARCHAR(255) DEFAULT '', warehouse_type VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', level VARCHAR(255) DEFAULT '', turnover_days DOUBLE DEFAULT 0, reason VARCHAR(255) DEFAULT '', action VARCHAR(255) DEFAULT '', note VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `events` (id BIGINT PRIMARYKEY AUTO_INCREMENT, event_type VARCHAR(255) NOT NULL, entity_type VARCHAR(255) DEFAULT '', entity_id VARCHAR(255) DEFAULT '', title VARCHAR(255) DEFAULT '', payload VARCHAR(255) DEFAULT '{}', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `inbound_records` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) DEFAULT '', product_name VARCHAR(255) DEFAULT '', quantity BIGINT DEFAULT 0, supplier VARCHAR(255) DEFAULT '', inbound_date VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , prod_date VARCHAR(255) DEFAULT '', exp_date VARCHAR(255) DEFAULT '', warehouse VARCHAR(255) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `inventory` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) NOT NULL, product_name VARCHAR(255) DEFAULT '', store VARCHAR(255) DEFAULT '', warehouse VARCHAR(255) DEFAULT '', available_qty BIGINT DEFAULT 0, locked_qty BIGINT DEFAULT 0, in_transit_qty BIGINT DEFAULT 0, safety_qty BIGINT DEFAULT 0, safety_days DOUBLE DEFAULT 0, warehouse_type VARCHAR(255) DEFAULT 'platform', raw_data VARCHAR(255) DEFAULT '', source VARCHAR(255) DEFAULT '', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , channel VARCHAR(255) DEFAULT 'jd', beginning_stock BIGINT DEFAULT 0, month_inbound BIGINT DEFAULT 0, month_outbound BIGINT DEFAULT 0, turnover_days DOUBLE DEFAULT 0, c_transit BIGINT DEFAULT 0, weight DOUBLE DEFAULT 0, volume DOUBLE DEFAULT 0, barcode VARCHAR(255) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `orders` (id BIGINT PRIMARYKEY AUTO_INCREMENT, order_no VARCHAR(255) NOT NULL, store VARCHAR(255) DEFAULT '', warehouse VARCHAR(255) DEFAULT '', sku VARCHAR(255) DEFAULT '', product_name VARCHAR(255) DEFAULT '', quantity BIGINT DEFAULT 0, unit_price DOUBLE DEFAULT 0, total_amount DOUBLE DEFAULT 0, data_source VARCHAR(255) DEFAULT '', order_status VARCHAR(255) DEFAULT '', ordered_at VARCHAR(255) DEFAULT '', platform VARCHAR(255) DEFAULT '', supplier VARCHAR(255) DEFAULT '', remark VARCHAR(255) DEFAULT '', parent_order_no VARCHAR(255) DEFAULT '', raw_data VARCHAR(255) DEFAULT '', source VARCHAR(255) DEFAULT '', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , channel VARCHAR(255) DEFAULT 'jd', paid_at VARCHAR(255) DEFAULT '', barcode VARCHAR(255) DEFAULT '', deleted_at VARCHAR(255) DEFAULT '', freight_amount DOUBLE DEFAULT 0, subsidy_amount DOUBLE DEFAULT 0, tax_amount DOUBLE DEFAULT 0, discount_amount DOUBLE DEFAULT 0, actual_amount DOUBLE DEFAULT 0) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `outbound_records` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) DEFAULT '', product_name VARCHAR(255) DEFAULT '', quantity BIGINT DEFAULT 0, target_warehouse VARCHAR(255) DEFAULT '', outbound_date VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , prod_date VARCHAR(255) DEFAULT '', exp_date VARCHAR(255) DEFAULT '', warehouse VARCHAR(255) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `products` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) NOT NULL, product_name VARCHAR(255) DEFAULT '', store VARCHAR(255) DEFAULT '', category VARCHAR(255) DEFAULT '', price DOUBLE DEFAULT 0, box_qty BIGINT DEFAULT 1, status VARCHAR(255) DEFAULT 'active', supplier_code VARCHAR(255) DEFAULT '', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, barcode VARCHAR(255) DEFAULT '', weight DOUBLE DEFAULT 0, volume DOUBLE DEFAULT 0, channel VARCHAR(255) DEFAULT 'jd', unit VARCHAR(255) DEFAULT '', deleted_at VARCHAR(255) DEFAULT '', best_before VARCHAR(255) DEFAULT '', brand VARCHAR(255) DEFAULT '', UNIQUE(sku, channel)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `purchase_orders` (id BIGINT PRIMARYKEY AUTO_INCREMENT, sku VARCHAR(255) NOT NULL, store VARCHAR(255) DEFAULT '', product_name VARCHAR(255) DEFAULT '', suggested_qty BIGINT DEFAULT 0, actual_qty BIGINT DEFAULT 0, arrival_date VARCHAR(255) DEFAULT '', status VARCHAR(255) DEFAULT 'pending', channel VARCHAR(255) DEFAULT 'jd', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `quality_logs` (id BIGINT PRIMARYKEY AUTO_INCREMENT, log_type VARCHAR(255) DEFAULT '', level VARCHAR(255) DEFAULT '', message VARCHAR(255) DEFAULT '', details VARCHAR(255) DEFAULT '', source VARCHAR(255) DEFAULT '', entity_type VARCHAR(255) DEFAULT '', entity_id VARCHAR(255) DEFAULT '', field_name VARCHAR(255) DEFAULT '', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `replenishment_config` (id BIGINT PRIMARYKEY AUTO_INCREMENT, `key` VARCHAR(255) NOT NULL, value VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, UNIQUE(`key`, channel)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `replenishment_config_history` (id BIGINT PRIMARYKEY AUTO_INCREMENT, `key` VARCHAR(255) NOT NULL, old_value VARCHAR(255) DEFAULT '', new_value VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', mode VARCHAR(255) DEFAULT '', operator VARCHAR(255) DEFAULT 'web', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `rules` (id BIGINT PRIMARYKEY AUTO_INCREMENT, name VARCHAR(255) DEFAULT '', event VARCHAR(255) DEFAULT '', condition_json VARCHAR(255) DEFAULT '{}', alert_type VARCHAR(255) DEFAULT '', alert_title VARCHAR(255) DEFAULT '', alert_desc VARCHAR(255) DEFAULT '', severity VARCHAR(255) DEFAULT 'warning', is_active BIGINT DEFAULT 1, owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , mode VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', deleted_at VARCHAR(255) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `suppliers` (id BIGINT PRIMARYKEY AUTO_INCREMENT, supplier_code VARCHAR(255) UNIQUE NOT NULL, supplier_name VARCHAR(255) DEFAULT '', contact_person VARCHAR(255) DEFAULT '', contact_phone VARCHAR(255) DEFAULT '', score BIGINT DEFAULT 0, status VARCHAR(255) DEFAULT 'active', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP , channel VARCHAR(255) DEFAULT 'jd', brand VARCHAR(255) DEFAULT '') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `sync_tasks` (id BIGINT PRIMARYKEY AUTO_INCREMENT, task_id VARCHAR(255) DEFAULT '', task_type VARCHAR(255) NOT NULL, status VARCHAR(255) DEFAULT 'pending', params VARCHAR(255) DEFAULT '{}', result VARCHAR(255) DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', owner_id VARCHAR(255) DEFAULT '', created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `users` (id BIGINT PRIMARYKEY AUTO_INCREMENT, username VARCHAR(255) UNIQUE NOT NULL, password_hash VARCHAR(255) NOT NULL, role VARCHAR(255) DEFAULT 'user', is_active BIGINT DEFAULT 1, created_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP, updated_at VARCHAR(255) DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS `warehouse_registry` (id BIGINT PRIMARYKEY AUTO_INCREMENT, warehouse VARCHAR(255) NOT NULL DEFAULT '', warehouse_type VARCHAR(255) NOT NULL DEFAULT '', channel VARCHAR(255) DEFAULT 'jd', UNIQUE(warehouse, channel)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
CREATE UNIQUE INDEX `idx_inbound_records_unique` ON `inbound_records` (sku, warehouse, channel, COALESCE(prod_date,'');
CREATE UNIQUE INDEX `idx_outbound_records_unique` ON `outbound_records` (sku, warehouse, channel, COALESCE(prod_date,'');
"""


# ─── Phase2: 迁移工具函数 ────────────────────────────────────────
"""Phase2 迁移工具端点(临时): 建表 + 小批量 seed + RU 实测

Makers 云函数临时工具, Phase2(TiDB 数据层)期间使用, 迁移完成后删除。
所有操作幂等, 只影响 TiDB 侧数据。
"""
from datetime import datetime, timezone

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
    """执行建表 DDL(幂等)"""
    out = {"tables_ok": [], "indexes_ok": [], "fail": []}
    conn = _conn()
    cur = conn.cursor()
    for stmt in SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            cur.execute(stmt)
            if "CREATE TABLE" in stmt.upper():
                out["tables_ok"].append(stmt.split("`")[1])
            else:
                out["indexes_ok"].append(stmt.split("`")[3])
        except Exception as e:
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
