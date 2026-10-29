"""schema 重建模块(2026-10-17 新集群迁移): 按现有代码数据逻辑重建全表 DDL

来源权威=代码里的 INSERT/SELECT 列 + 索引断言(_INDEXES/_ADD/_DROP)——非 PA 旧版(已过时)
幂等 CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS; 新集群空库自动引导
调用: index.py 启动段(与现有建表同位置)
"""
from db import execute

# 全表 DDL(按代码数据逻辑: 列=INSERT/SELECT 使用, 类型按语义, 唯一键/索引=代码断言)
_TABLES = {
    "users": (
        "CREATE TABLE IF NOT EXISTS users ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL, "
        "password_hash VARCHAR(255) NOT NULL, role VARCHAR(32) DEFAULT 'user', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uk_users_username (username))"
    ),
    "orders": (
        "CREATE TABLE IF NOT EXISTS orders ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, order_no VARCHAR(64) DEFAULT '', "
        "store VARCHAR(64) DEFAULT '', warehouse VARCHAR(64) DEFAULT '', sku VARCHAR(64) DEFAULT '', "
        "barcode VARCHAR(64) DEFAULT '', product_name VARCHAR(128) DEFAULT '', "
        "quantity INT DEFAULT 0, unit_price DECIMAL(12,2) DEFAULT 0, "
        "total_amount DECIMAL(14,2) DEFAULT 0, discount_amount DECIMAL(14,2) DEFAULT 0, "
        "freight_amount DECIMAL(14,2) DEFAULT 0, subsidy_amount DECIMAL(14,2) DEFAULT 0, "
        "tax_amount DECIMAL(14,2) DEFAULT 0, actual_amount DECIMAL(14,2) DEFAULT 0, "
        "order_status VARCHAR(32) DEFAULT '', ordered_at DATETIME, paid_at DATETIME, "
        "platform VARCHAR(32) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', "
        "data_source VARCHAR(32) DEFAULT '', supplier VARCHAR(64) DEFAULT '', remark VARCHAR(255) DEFAULT '', "
        "deleted_at VARCHAR(32) DEFAULT '', ext_json TEXT, "
        "KEY idx_orders_channel_ordered (channel, ordered_at), "
        "KEY idx_orders_sku_ordered_at (sku, ordered_at, channel))"
    ),
    "inventory": (
        "CREATE TABLE IF NOT EXISTS inventory ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) NOT NULL, "
        "product_name VARCHAR(128) DEFAULT '', store VARCHAR(64) DEFAULT '', "
        "warehouse VARCHAR(64) DEFAULT '', warehouse_type VARCHAR(20) DEFAULT 'own', "
        "available_qty INT DEFAULT 0, in_transit_qty INT DEFAULT 0, c_transit INT DEFAULT 0, "
        "safety_qty INT DEFAULT 0, safety_days DECIMAL(10,2) DEFAULT 0, locked_qty INT DEFAULT 0, "
        "beginning_stock INT DEFAULT 0, month_inbound INT DEFAULT 0, month_outbound INT DEFAULT 0, "
        "turnover_days DECIMAL(10,2) DEFAULT 0, weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, "
        "barcode VARCHAR(64) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', "
        "deleted_at VARCHAR(32) DEFAULT '', ext_json TEXT, "
        "KEY idx_inv_ch_wh (channel, warehouse_type))"
    ),
    "products": (
        "CREATE TABLE IF NOT EXISTS products ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) NOT NULL, barcode VARCHAR(64) DEFAULT '', "
        "product_name VARCHAR(128) DEFAULT '', brand VARCHAR(64) DEFAULT '', store VARCHAR(64) DEFAULT '', "
        "category VARCHAR(64) DEFAULT '', price DECIMAL(12,2) DEFAULT 0, box_qty INT DEFAULT 0, "
        "unit VARCHAR(16) DEFAULT '', status VARCHAR(16) DEFAULT 'active', "
        "weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, best_before VARCHAR(32) DEFAULT '', "
        "supplier_code VARCHAR(64) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', "
        "deleted_at VARCHAR(32) DEFAULT '', ext_json TEXT, "
        "UNIQUE KEY uq_products_sku (sku), KEY idx_products_ch (channel))"
    ),
    "suppliers": (
        "CREATE TABLE IF NOT EXISTS suppliers ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, supplier_code VARCHAR(64) NOT NULL, "
        "supplier_name VARCHAR(128) DEFAULT '', contact_person VARCHAR(64) DEFAULT '', "
        "contact_phone VARCHAR(32) DEFAULT '', score INT DEFAULT 0, status VARCHAR(16) DEFAULT 'active', "
        "channel VARCHAR(20) DEFAULT 'jd', brand VARCHAR(128) DEFAULT '', ext_json TEXT, "
        "UNIQUE KEY uk_suppliers_code (supplier_code))"
    ),
    "daily_sales_snapshot": (
        "CREATE TABLE IF NOT EXISTS daily_sales_snapshot ("
        "date DATE NOT NULL, channel VARCHAR(20) NOT NULL, sku VARCHAR(64) NOT NULL, "
        "warehouse VARCHAR(64) DEFAULT '', order_count INT DEFAULT 0, "
        "PRIMARY KEY(date, channel, sku, warehouse), "
        "KEY idx_snapshot_ch_date_full (channel, date, sku, warehouse, order_count))"
    ),
    "alerts": (
        "CREATE TABLE IF NOT EXISTS alerts ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, alert_type VARCHAR(32) DEFAULT '', "
        "title VARCHAR(255) DEFAULT '', description TEXT, severity VARCHAR(16) DEFAULT 'warning', "
        "status VARCHAR(16) DEFAULT 'active', source VARCHAR(32) DEFAULT 'rules_engine', "
        "related_sku VARCHAR(64) DEFAULT '', related_order_no VARCHAR(64) DEFAULT '', "
        "related_rule_id BIGINT DEFAULT 0, warehouse_type VARCHAR(20) DEFAULT '', "
        "warehouse VARCHAR(64) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', pushed INT DEFAULT 0, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "KEY idx_alerts_ch_status_created (channel, status, created_at), "
        "KEY idx_alerts_status_source (status, source))"
    ),
    "rules": (
        "CREATE TABLE IF NOT EXISTS rules ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(128) NOT NULL, "
        "event VARCHAR(64) DEFAULT '', condition_json TEXT, alert_type VARCHAR(32) DEFAULT '', "
        "alert_title VARCHAR(255) DEFAULT '', alert_desc TEXT, severity VARCHAR(16) DEFAULT 'warning', "
        "is_active INT DEFAULT 1, channel VARCHAR(20) DEFAULT 'jd', mode VARCHAR(20) DEFAULT '', "
        "params TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "deleted_at VARCHAR(32) DEFAULT '', "
        "KEY idx_rules_ch_active (channel, is_active))"
    ),
    "replenishment_config": (
        "CREATE TABLE IF NOT EXISTS replenishment_config ("
        "`key` VARCHAR(80) NOT NULL, value TEXT, channel VARCHAR(20) DEFAULT '', "
        "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(`key`, channel))"
    ),
    "replenishment_config_history": (
        "CREATE TABLE IF NOT EXISTS replenishment_config_history ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, `key` VARCHAR(80) DEFAULT '', "
        "old_value TEXT, new_value TEXT, channel VARCHAR(20) DEFAULT '', mode VARCHAR(20) DEFAULT '', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
    "batches": (
        "CREATE TABLE IF NOT EXISTS batches ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) NOT NULL, "
        "warehouse VARCHAR(64) DEFAULT '', warehouse_type VARCHAR(20) DEFAULT '', "
        "channel VARCHAR(20) DEFAULT 'jd', prod_date VARCHAR(32) DEFAULT '', "
        "exp_date VARCHAR(32) DEFAULT '', qty INT DEFAULT 0, "
        "KEY idx_batches_sku (sku), KEY idx_batches_wh (warehouse_type, sku))"
    ),
    "purchase_orders": (
        "CREATE TABLE IF NOT EXISTS purchase_orders ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) NOT NULL, store VARCHAR(64) DEFAULT '', "
        "product_name VARCHAR(128) DEFAULT '', suggested_qty INT DEFAULT 0, actual_qty INT DEFAULT 0, "
        "arrival_date VARCHAR(32) DEFAULT '', status VARCHAR(16) DEFAULT 'pending', "
        "channel VARCHAR(20) DEFAULT 'jd', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uk_po_sku_store (sku, store))"
    ),
    "disposal_records": (
        "CREATE TABLE IF NOT EXISTS disposal_records ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) DEFAULT '', "
        "warehouse VARCHAR(64) DEFAULT '', warehouse_type VARCHAR(20) DEFAULT '', "
        "channel VARCHAR(20) DEFAULT 'jd', level VARCHAR(16) DEFAULT '', "
        "turnover_days DECIMAL(10,2) DEFAULT 0, reason VARCHAR(255) DEFAULT '', "
        "action VARCHAR(32) DEFAULT '', note VARCHAR(255) DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
    "inbound_records": (
        "CREATE TABLE IF NOT EXISTS inbound_records ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) DEFAULT '', product_name VARCHAR(128) DEFAULT '', "
        "quantity INT DEFAULT 0, supplier VARCHAR(64) DEFAULT '', inbound_date VARCHAR(32) DEFAULT '', "
        "channel VARCHAR(20) DEFAULT 'jd', prod_date VARCHAR(32) DEFAULT '', exp_date VARCHAR(32) DEFAULT '', "
        "warehouse VARCHAR(64) DEFAULT '', barcode VARCHAR(64) DEFAULT '', platform VARCHAR(32) DEFAULT '', "
        "brand VARCHAR(64) DEFAULT '', store VARCHAR(64) DEFAULT '', category VARCHAR(64) DEFAULT '', "
        "price DECIMAL(12,2) DEFAULT 0, box_qty INT DEFAULT 0, unit VARCHAR(16) DEFAULT '', "
        "weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, status VARCHAR(16) DEFAULT '', "
        "ext_json TEXT, KEY idx_ib_sku_date (sku, inbound_date))"
    ),
    "outbound_records": (
        "CREATE TABLE IF NOT EXISTS outbound_records ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) DEFAULT '', product_name VARCHAR(128) DEFAULT '', "
        "quantity INT DEFAULT 0, target_warehouse VARCHAR(64) DEFAULT '', outbound_date VARCHAR(32) DEFAULT '', "
        "channel VARCHAR(20) DEFAULT 'jd', prod_date VARCHAR(32) DEFAULT '', exp_date VARCHAR(32) DEFAULT '', "
        "warehouse VARCHAR(64) DEFAULT '', barcode VARCHAR(64) DEFAULT '', platform VARCHAR(32) DEFAULT '', "
        "brand VARCHAR(64) DEFAULT '', store VARCHAR(64) DEFAULT '', category VARCHAR(64) DEFAULT '', "
        "price DECIMAL(12,2) DEFAULT 0, box_qty INT DEFAULT 0, unit VARCHAR(16) DEFAULT '', "
        "weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, status VARCHAR(16) DEFAULT '', "
        "ext_json TEXT, KEY idx_ob_sku_date (sku, outbound_date))"
    ),
    "quality_logs": (
        "CREATE TABLE IF NOT EXISTS quality_logs ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, log_type VARCHAR(40) DEFAULT '', "
        "level VARCHAR(16) DEFAULT 'info', message VARCHAR(255) DEFAULT '', details TEXT, "
        "source VARCHAR(32) DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "KEY idx_ql_created (created_at), KEY idx_ql_type (log_type))"
    ),
    "sync_tasks": (
        "CREATE TABLE IF NOT EXISTS sync_tasks ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id VARCHAR(80) NOT NULL, "
        "task_type VARCHAR(32) DEFAULT '', status VARCHAR(16) DEFAULT 'pending', "
        "params TEXT, result TEXT, channel VARCHAR(20) DEFAULT 'jd', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uk_tasks_id (task_id), KEY idx_tasks_status (status))"
    ),
    "cleansing_templates": (
        "CREATE TABLE IF NOT EXISTS cleansing_templates ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(128) NOT NULL, "
        "doc_type VARCHAR(32) DEFAULT '', mapping MEDIUMTEXT, "
        "cf_meta TEXT, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uk_tpl_name_type (name, doc_type))"
    ),
    "cleansing_errors": (
        "CREATE TABLE IF NOT EXISTS cleansing_errors ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id VARCHAR(80) DEFAULT '', "
        "row_no INT DEFAULT 0, sku VARCHAR(64) DEFAULT '', reason VARCHAR(255) DEFAULT '', "
        "channel VARCHAR(20) DEFAULT 'jd', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
    "daily_stats": (
        "CREATE TABLE IF NOT EXISTS daily_stats ("
        "date DATE NOT NULL, channel VARCHAR(20) NOT NULL, store VARCHAR(64) NOT NULL, "
        "sku VARCHAR(64) NOT NULL, order_status VARCHAR(32) NOT NULL, gmv DECIMAL(14,2) DEFAULT 0, "
        "order_count INT DEFAULT 0, quantity INT DEFAULT 0, "
        "PRIMARY KEY(date, channel, store, sku, order_status))"
    ),
    "export_files": (
        "CREATE TABLE IF NOT EXISTS export_files ("
        "id BIGINT PRIMARY KEY AUTO_INCREMENT, filename VARCHAR(128) DEFAULT '', "
        "content LONGTEXT, channel VARCHAR(20) DEFAULT 'jd', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uk_filename (filename))"
    ),
    "gmv_flows": (
        "CREATE TABLE IF NOT EXISTS gmv_flows ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, platform VARCHAR(32) NOT NULL, "
        "shop VARCHAR(64) DEFAULT '', flow_no VARCHAR(64) NOT NULL, related_no VARCHAR(64) DEFAULT '', "
        "arrived_time DATETIME, amount DECIMAL(14,2) DEFAULT 0, flow_type VARCHAR(32) DEFAULT '', "
        "settled_amount DECIMAL(14,2) DEFAULT 0, channel VARCHAR(20) DEFAULT 'jd', "
        "data_source VARCHAR(32) DEFAULT '', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uq_platform_flow_ch (channel, platform, flow_no))"
    ),
    "sku_sales_stats": (
        "CREATE TABLE IF NOT EXISTS sku_sales_stats ("
        "channel VARCHAR(20) NOT NULL, sku VARCHAR(64) NOT NULL, warehouse VARCHAR(64) DEFAULT '', "
        "ds7 DECIMAL(12,3) DEFAULT 0, ds14 DECIMAL(12,3) DEFAULT 0, ds28 DECIMAL(12,3) DEFAULT 0, "
        "sigma DECIMAL(12,3) DEFAULT 0, "
        "ds7_cut DECIMAL(12,3) DEFAULT 0, ds14_cut DECIMAL(12,3) DEFAULT 0, ds28_cut DECIMAL(12,3) DEFAULT 0, "
        "nnz7 INT DEFAULT 0, nnz14 INT DEFAULT 0, nnz28 INT DEFAULT 0, "
        "last_sale DATE, updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), "
        "PRIMARY KEY(channel, sku, warehouse))"
    ),
    "custom_fields": (
        "CREATE TABLE IF NOT EXISTS custom_fields ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, entity VARCHAR(32) DEFAULT '', "
        "field_name VARCHAR(64) DEFAULT '', field_type VARCHAR(16) DEFAULT '', "
        "channel VARCHAR(20) DEFAULT 'jd', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
    "events": (
        "CREATE TABLE IF NOT EXISTS events ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, event_type VARCHAR(32) DEFAULT '', "
        "payload TEXT, channel VARCHAR(20) DEFAULT 'jd', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
    "warehouse_registry": (
        "CREATE TABLE IF NOT EXISTS warehouse_registry ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, warehouse VARCHAR(64) DEFAULT '', "
        "warehouse_type VARCHAR(20) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
}


def create_all_tables():
    """幂等建全表(新集群空库引导; 失败单表降级不阻塞)"""
    from routes.common import try_err
    ok_n = 0
    for name, ddl in _TABLES.items():
        try:
            execute(ddl)
            ok_n += 1
        except Exception as e:
            try_err('schema', '建表失败 %s' % name, e)
    # 2026-10-17 唯一索引兜底(职责: 前端三键检测拦截, 后端唯一索引兜底防并发/漏网):
    # orders uq_orders_no_sku——seed 订单号已修全局唯一(不取模), 清洗导入天然唯一, 幂等补建
    # (早前 seed 冲突源于 total%100000 轮回, 已修——现在补回兜底)
    try:
        from db import query as _q
        _idx = {str(r.get("Key_name") or "") for r in
                _q("SHOW INDEX FROM orders")}
        if "uq_orders_no_sku" not in _idx:
            execute("ALTER TABLE orders ADD UNIQUE KEY uq_orders_no_sku (order_no, sku)")
    except Exception as e:
        try_err('schema', 'orders 唯一索引兜底降级', e)
    # 2026-10-27 模板表迁移(幂等): 旧 uk_tpl_name(name 唯一) → uk_tpl_name_type(name, doc_type)
    # ——曾订单/库存同名模板互相覆盖; 改双键后旧唯一键必须移除(否则同名跨类型保存撞键报错)
    try:
        from db import query as _q3
        _ti = [str(r.get("Key_name") or "") for r in _q3("SHOW INDEX FROM cleansing_templates")]
        if "uk_tpl_name" in _ti:
            try:
                execute("ALTER TABLE cleansing_templates DROP INDEX uk_tpl_name")
            except Exception as _e3:
                try_err('schema', '模板旧唯一索引删除(可能被外键/重名占用)', _e3)
        if "uk_tpl_name_type" not in _ti:
            try:
                execute("ALTER TABLE cleansing_templates ADD UNIQUE KEY uk_tpl_name_type (name, doc_type)")
            except Exception as _e4:
                try_err('schema', '模板双键唯一索引补建(重名数据需先清理)', _e4)
    except Exception as e:
        try_err('schema', '模板索引迁移降级', e)
    # 2026-10-29 存量文案迁移(幂等): 超卖判定口径由"单笔订单量"改为"累计订单量",
    # 告警 description 是**生成时快照**, 已存在的告警仍是旧文案 → 一次性同步
    try:
        execute("UPDATE alerts SET description='累计订单量超过可用库存' "
                "WHERE alert_type='oversell' AND description='订单数量超过可用库存'")
    except Exception as e:
        try_err('schema', '超卖告警文案迁移降级', e)
    # 2026-10-17 幂等补列(已建表 vs DDL 差异自愈——CREATE IF NOT EXISTS 不改已存在表):
    # inventory.safety_days 曾漏(采购建议 SELECT 用, 1054 实证)——SHOW COLUMNS 差异→ALTER ADD
    try:
        from db import query as _q2
        _extra_cols = {
            "cleansing_templates": [("cf_meta", "TEXT")],
            # 2026-10-29 出入库备注(明细弹窗最右列; 原表无此列, 导入的 remark 被丢弃)
            "inbound_records": [("remark", "VARCHAR(255) DEFAULT ''"),
                                ("warehouse_type", "VARCHAR(20) DEFAULT ''")],
            "outbound_records": [("remark", "VARCHAR(255) DEFAULT ''"),
                                 ("warehouse_type", "VARCHAR(20) DEFAULT ''")],
            "inventory": [("safety_days", "DECIMAL(10,2) DEFAULT 0")],
        }
        for _t, _cols in _extra_cols.items():
            try:
                _have = {str(r.get("Field") or "") for r in _q2("SHOW COLUMNS FROM `%s`" % _t)}
            except Exception:
                continue
            for _c, _c_ddl in _cols:
                if _c not in _have:
                    try:
                        execute("ALTER TABLE `%s` ADD COLUMN `%s` %s" % (_t, _c, _c_ddl))
                        try_err('schema', '%s.%s 补列完成' % (_t, _c), '')
                    except Exception as _e:
                        try_err('schema', '%s.%s 补列失败' % (_t, _c), _e)
    except Exception as e:
        try_err('schema', '补列自愈降级', e)
    return ok_n
