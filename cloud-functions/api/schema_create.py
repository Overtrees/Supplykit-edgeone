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
    # 2026-10-30 渠道注册表(P0 可扩展性): 渠道从"代码常量"变为"数据驱动"
    #   supports_b  —— 该渠道是否走 BBCC 体系(B 仓调拨链)
    #   default_mode —— 该渠道默认补货模式(bbcc/traditional)
    "channels": (
        "CREATE TABLE IF NOT EXISTS channels ("
        "code VARCHAR(20) PRIMARY KEY, name VARCHAR(64) NOT NULL DEFAULT '', "
        "enabled TINYINT DEFAULT 1, supports_b TINYINT DEFAULT 0, "
        "default_mode VARCHAR(20) DEFAULT 'traditional', sort INT DEFAULT 0, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
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
        # 2026-11-02 S2-5 订单业务键 = 四维(channel, order_no, sku, barcode), 与写入层 upsert、
        #   查重口径三者同一套键。旧 uq_orders_no_sku(order_no, sku) 比业务键窄, 见下方迁移注释。
        "UNIQUE KEY uq_orders_biz (channel, order_no, sku, barcode), "
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
        # snapshot_qty: 库存导入送来的"当时可用"(对账基准)。已建账后它**不再写成期初**,
        #   只留在这里做差异核对 —— 覆盖期初就是"每月累加一次双算"的根因
        "snapshot_qty INT DEFAULT 0, "
        "turnover_days DECIMAL(10,2) DEFAULT 0, weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, "
        "barcode VARCHAR(64) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', "
        "deleted_at VARCHAR(32) DEFAULT '', ext_json TEXT, "
        "KEY idx_inv_ch_wh (channel, warehouse_type))"
    ),
    # 2026-11-02 库存删除快照(用户定案: 恢复删除 = 复原删除时的快照):
    #   inventory 的删除原是硬删(前端明示"不可恢复"), 快照无处可存。归档表方案:
    #   DELETE 前整行搬进来(保留原 id), 恢复 = 原样插回 —— 60 处读库存的 SQL 零改动、
    #   唯一键 uq_inventory_biz 不被占位(软删方案做不到这两点)。
    #   列必须与 inventory 逐一对应(恢复时 INSERT 全列), 新列只能"两边同加"。
    "inventory_archive": (
        "CREATE TABLE IF NOT EXISTS inventory_archive ("
        "id BIGINT PRIMARY KEY, sku VARCHAR(64) NOT NULL, "
        "product_name VARCHAR(128) DEFAULT '', store VARCHAR(64) DEFAULT '', "
        "warehouse VARCHAR(64) DEFAULT '', warehouse_type VARCHAR(20) DEFAULT 'own', "
        "available_qty INT DEFAULT 0, in_transit_qty INT DEFAULT 0, c_transit INT DEFAULT 0, "
        "safety_qty INT DEFAULT 0, safety_days DECIMAL(10,2) DEFAULT 0, locked_qty INT DEFAULT 0, "
        "beginning_stock INT DEFAULT 0, month_inbound INT DEFAULT 0, month_outbound INT DEFAULT 0, "
        "turnover_days DECIMAL(10,2) DEFAULT 0, weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, "
        "barcode VARCHAR(64) DEFAULT '', channel VARCHAR(20) DEFAULT 'jd', "
        "deleted_at VARCHAR(32) DEFAULT '', ext_json TEXT, "
        "archived_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "KEY idx_inv_arch_ch (channel, sku))"
    ),
    "products": (
        "CREATE TABLE IF NOT EXISTS products ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, sku VARCHAR(64) NOT NULL, barcode VARCHAR(64) DEFAULT '', "
        "product_name VARCHAR(128) DEFAULT '', brand VARCHAR(64) DEFAULT '', store VARCHAR(64) DEFAULT '', "
        "category VARCHAR(64) DEFAULT '', price DECIMAL(12,2) DEFAULT 0, box_qty INT DEFAULT 0, "
        "unit VARCHAR(16) DEFAULT '', status VARCHAR(16) DEFAULT 'active', "
        "weight DECIMAL(10,2) DEFAULT 0, volume DECIMAL(10,4) DEFAULT 0, best_before VARCHAR(32) DEFAULT '', "
        # 2026-11-01 是否做效期管理(三态): 'yes' 走精确扣减+FEFO; 'no' 走入库序 FIFO 且批次不带日期;
        #   '' 未标注 → 出入库/库存导入直接拒绝(不猜)。值由导入端"是/否"归一化。
        "expiry_managed VARCHAR(8) DEFAULT '', "
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
    # 2026-10-29 代理主键(根治): 业务键降为唯一索引 → 业务粒度变化(加/换 69码)只改唯一索引,
    # 永不触碰聚簇主键(TiDB 聚簇表 DROP PRIMARY KEY 不支持, 只能整表重建 → 见 routes/misc._migrate_surrogate)
    "daily_sales_snapshot": (
        "CREATE TABLE IF NOT EXISTS daily_sales_snapshot ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "date DATE NOT NULL, channel VARCHAR(20) NOT NULL, sku VARCHAR(64) NOT NULL, "
        "barcode VARCHAR(64) NOT NULL DEFAULT '', warehouse VARCHAR(64) NOT NULL DEFAULT '', "
        "order_count INT DEFAULT 0, "
        "UNIQUE KEY uq_snapshot_biz (date, channel, sku, barcode, warehouse), "
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
        "barcode VARCHAR(64) DEFAULT '', "
        # 2026-11-03 D8: 告警生命周期留痕 —— 关掉的原因与时间(原 8 个入口只改 status,
        #   "这条为什么消失"无法回答; 多入口竞争与"关了又开"抖动也无法识别)
        "closed_reason VARCHAR(32) DEFAULT '', closed_at DATETIME NULL, "
        # 抖动可数: 同一维度"恢复→再触发"的重开次数(0=首次开启)
        "reopen_count INT DEFAULT 0, "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "KEY idx_alerts_ch_status_created (channel, status, created_at), "
        "KEY idx_alerts_dim (alert_type, channel, related_sku, warehouse), "
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
    "data_freshness": (
        "CREATE TABLE IF NOT EXISTS data_freshness ("
        "doc_type VARCHAR(32) NOT NULL, channel VARCHAR(20) NOT NULL DEFAULT 'jd', "
        "last_import_at DATETIME, last_biz_date VARCHAR(32) DEFAULT '', "
        "rows_imported INT DEFAULT 0, PRIMARY KEY (doc_type, channel))"
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
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "channel VARCHAR(20) NOT NULL, sku VARCHAR(64) NOT NULL, barcode VARCHAR(64) NOT NULL DEFAULT '', "
        "warehouse VARCHAR(64) NOT NULL DEFAULT '', "
        "ds7 DECIMAL(12,3) DEFAULT 0, ds14 DECIMAL(12,3) DEFAULT 0, ds28 DECIMAL(12,3) DEFAULT 0, "
        "sigma DECIMAL(12,3) DEFAULT 0, "
        "ds7_cut DECIMAL(12,3) DEFAULT 0, ds14_cut DECIMAL(12,3) DEFAULT 0, ds28_cut DECIMAL(12,3) DEFAULT 0, "
        "nnz7 INT DEFAULT 0, nnz14 INT DEFAULT 0, nnz28 INT DEFAULT 0, "
        "last_sale DATE, updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), "
        "UNIQUE KEY uq_stats_biz (channel, sku, barcode, warehouse))"
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
    # ── 2026-11-02 S2-8: 原 index.py 启动段内联的 12 张运行表, 收敛为声明(单一真源) ──
    "maintenance_log": (
        "CREATE TABLE IF NOT EXISTS maintenance_log (`date` DATE NOT NULL, "
        "task VARCHAR(32) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(`date`, task))"
    ),
    "gmv_flows": (
        "CREATE TABLE IF NOT EXISTS gmv_flows (id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "platform VARCHAR(32) NOT NULL, shop VARCHAR(64) DEFAULT '', flow_no VARCHAR(64) NOT NULL, "
        "related_no VARCHAR(64) DEFAULT '', arrived_time DATETIME, amount DECIMAL(14,2) DEFAULT 0, "
        "flow_type VARCHAR(32) DEFAULT '', settled_amount DECIMAL(14,2) DEFAULT 0, "
        "channel VARCHAR(20) DEFAULT 'jd', data_source VARCHAR(32) DEFAULT '', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uq_platform_flow_ch (channel, platform, flow_no))"
    ),
    "health_snapshot": (
        "CREATE TABLE IF NOT EXISTS health_snapshot (`date` DATE NOT NULL, "
        "channel VARCHAR(20) NOT NULL, score INT DEFAULT 0, own_score INT DEFAULT -1, "
        "platform_score INT DEFAULT -1, bc_score INT DEFAULT -1, "
        "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(date, channel))"
    ),
    "analysis_cache": (
        "CREATE TABLE IF NOT EXISTS analysis_cache ("
        "`key` VARCHAR(160) PRIMARY KEY, value MEDIUMTEXT, "
        "created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), tok BIGINT DEFAULT 0)"
    ),
    "eval_pending": (
        "CREATE TABLE IF NOT EXISTS eval_pending ("
        "`task` VARCHAR(32) PRIMARY KEY, updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6))"
    ),
    # 2026-11-03 D4/D5: 规则评估任务化 —— 取代"只存一个脏标记"的做法。
    #   标记只能回答"要不要跑", 回答不了"跑了没有/跑了多久/影响了多少行/前端该等谁"。
    #   scope 是 JSON: {"channels": [...], "ats": [...], "deep": false}
    "eval_tasks": (
        "CREATE TABLE IF NOT EXISTS eval_tasks ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "scope VARCHAR(255) DEFAULT '', "
        "status VARCHAR(16) DEFAULT 'pending', "
        "rows_out INT DEFAULT 0, duration_ms INT DEFAULT 0, "
        "error VARCHAR(255) DEFAULT '', "
        "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "started_at DATETIME NULL, finished_at DATETIME NULL, "
        "KEY idx_eval_status (status, created_at))"
    ),
    "migration_log": (
        "CREATE TABLE IF NOT EXISTS migration_log "
        "(name VARCHAR(64) PRIMARY KEY, done_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    ),
    # 2026-11-04 S3-5 有序迁移登记(替代 migration_log 的自守模式; 语义: 某 step 已应用)
    #   note 记步骤自报的影响行数或 adopted:旧标记名, 便于事后核对"到底跑没跑、跑了多少"
    "schema_migrations": (
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "id VARCHAR(64) PRIMARY KEY, "
        "applied_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), "
        "note VARCHAR(255) DEFAULT '', "
        "status VARCHAR(16) NOT NULL DEFAULT 'applied', "
        "attempts INT NOT NULL DEFAULT 0)"
    ),
    "agg_dirty": (
        "CREATE TABLE IF NOT EXISTS agg_dirty "
        "(id TINYINT PRIMARY KEY, ts DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6))"
    ),
    "data_version": (
        "CREATE TABLE IF NOT EXISTS data_version (id TINYINT PRIMARY KEY, v BIGINT DEFAULT 0, "
        "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)"
    ),
    "log_archives": (
        "CREATE TABLE IF NOT EXISTS log_archives ("
        "`date` CHAR(10) NOT NULL, `scope` VARCHAR(16) NOT NULL DEFAULT 'all', "
        "markdown MEDIUMTEXT, `count` INT DEFAULT 0, "
        "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(`date`, `scope`))"
    ),
    "pipeline_runs": (
        "CREATE TABLE IF NOT EXISTS pipeline_runs ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, pipeline VARCHAR(64) NOT NULL, "
        "scope VARCHAR(32) NOT NULL DEFAULT 'all', status VARCHAR(16) NOT NULL DEFAULT 'running', "
        "started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at DATETIME NULL, "
        "duration_ms INT DEFAULT 0, rows_affected INT DEFAULT 0, "
        "error VARCHAR(500) DEFAULT NULL, detail MEDIUMTEXT DEFAULT NULL, "
        "KEY idx_pipe_id (pipeline, id), KEY idx_started (started_at))"
    ),
    "orders_day_agg": (
        "CREATE TABLE IF NOT EXISTS orders_day_agg ("
        "`date` DATE NOT NULL, channel VARCHAR(20) NOT NULL, order_status VARCHAR(20) NOT NULL, "
        "store VARCHAR(60) NOT NULL, gmv DECIMAL(14,2) DEFAULT 0, subsidy DECIMAL(14,2) DEFAULT 0, "
        "cnt INT DEFAULT 0, PRIMARY KEY(`date`, channel, order_status, store))"
    ),
    "agg_dirty_days": (
        "CREATE TABLE IF NOT EXISTS agg_dirty_days ("
        "channel VARCHAR(20) NOT NULL, `date` CHAR(10) NOT NULL, "
        "reason VARCHAR(32) DEFAULT 'write', "
        "ts DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), PRIMARY KEY(channel, `date`))"
    ),
    # ── 账期(月结)三表 2026-11-04 用户定案 ────────────────────────────────
    # inventory_month: 每账期一行(期初/入库/出库/期末)。期初 = 上月期末(月末结存),
    #   不反推; 只存切段结果, 不承担计算(通式见 core/ledger.py)
    "inventory_month": (
        "CREATE TABLE IF NOT EXISTS inventory_month ("
        "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
        "period CHAR(7) NOT NULL, channel VARCHAR(20) NOT NULL DEFAULT 'jd', "
        "warehouse_type VARCHAR(20) NOT NULL DEFAULT 'own', "
        "sku VARCHAR(64) NOT NULL, barcode VARCHAR(64) NOT NULL DEFAULT '', "
        "warehouse VARCHAR(64) NOT NULL DEFAULT '', "
        "begin_qty INT DEFAULT 0, in_qty INT DEFAULT 0, out_qty INT DEFAULT 0, end_qty INT DEFAULT 0, "
        # src: report(月报导入) / derived(流水推算) / gap(账期断档)
        "src VARCHAR(16) NOT NULL DEFAULT 'derived', closed TINYINT DEFAULT 1, "
        "as_of CHAR(10) DEFAULT '', updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE KEY uq_inv_month (period, channel, warehouse_type, sku, barcode, warehouse))"
    ),
    # batch_opening: 批次月初余额(**带日期**)。结账时从 batches 整表切片 —— 少了它, 上月批次
    #   到本月就退化成"无日期期初批次", 指定批次出库全部命中不了, 只能降级 FIFO
    "batch_opening": (
        "CREATE TABLE IF NOT EXISTS batch_opening ("
        "period CHAR(7) NOT NULL, channel VARCHAR(20) NOT NULL DEFAULT 'jd', "
        "warehouse_type VARCHAR(20) NOT NULL DEFAULT 'own', "
        "sku VARCHAR(64) NOT NULL, barcode VARCHAR(64) NOT NULL DEFAULT '', "
        "warehouse VARCHAR(64) NOT NULL DEFAULT '', "
        "prod_date VARCHAR(32) NOT NULL DEFAULT '', exp_date VARCHAR(32) NOT NULL DEFAULT '', "
        "qty INT DEFAULT 0, "
        "PRIMARY KEY (period, channel, warehouse_type, sku, barcode, warehouse, prod_date, exp_date))"
    ),
    # ledger_state: 每 (渠道×仓型) 一行, 记录当前生效账期与起算日 ——
    #   可用 = beginning_stock + Σ流水(>= begin_date); begin_date 为空 = 尚未建账(全期)
    "ledger_state": (
        "CREATE TABLE IF NOT EXISTS ledger_state ("
        "channel VARCHAR(20) NOT NULL DEFAULT 'jd', "
        "warehouse_type VARCHAR(20) NOT NULL DEFAULT 'own', "
        "period CHAR(7) NOT NULL DEFAULT '', status VARCHAR(16) NOT NULL DEFAULT 'bootstrap', "
        "begin_date CHAR(10) NOT NULL DEFAULT '', reason VARCHAR(500) NOT NULL DEFAULT '', "
        "rows_count INT DEFAULT 0, closed_at DATETIME NULL, "
        "PRIMARY KEY (channel, warehouse_type))"
    ),
}

# ── 索引声明(2026-11-02 S2-8: 原 index.py 启动段三组, 收敛到同一处) ─────────
_INDEXES = [
    ("idx_orders_channel_ordered", "orders", "channel, ordered_at"),
    ("idx_orders_ch_id", "orders", "channel, id"),  # 订单分页 ORDER BY id DESC 走索引(曾 TopN 全表扫 428 RU/次)
    ("idx_batches_ch_wt_sku", "batches", "channel, warehouse_type, sku"),
    ("idx_batches_ch_exp", "batches", "channel, exp_date"),  # 批次页 WHERE channel ORDER BY exp_date(曾 9.3s)
    ("idx_alerts_ch_status_created", "alerts", "channel, status, created_at"),
    ("idx_rules_ch_active", "rules", "channel, is_active"),
    ("idx_snapshot_ch_date", "daily_sales_snapshot", "channel, date"),
    ("idx_snapshot_ch_date_full", "daily_sales_snapshot", "channel, date, sku, warehouse, order_count"),  # 覆盖索引(快照查询免回表)
]
_ADD_INDEXES = [
    ("idx_inbound_ch_date", "inbound_records", "(channel, inbound_date)"),
    ("idx_outbound_ch_date", "outbound_records", "(channel, outbound_date)"),
    ("idx_alerts_status_source", "alerts", "(status, source)"),
    ("idx_products_barcode_ch", "products", "(barcode, channel)"),
    # 2026-11-03 D8: 告警按维度查(重开判定 / 关闭匹配) —— 没有它每次写告警都要扫全表
    ("idx_alerts_dim", "alerts", "(alert_type, channel, related_sku, warehouse)"),
]
_DROP_INDEXES = [
    ("idx_orders_sku", "orders"),        # 被 idx_orders_sku_ordered_at 前缀覆盖
    ("idx_orders_order_no", "orders"),   # 被 uq_orders_biz(channel,order_no,sku,barcode) 前缀覆盖
    ("idx_orders_ch_status", "orders"),  # 被 idx_orders_ch_ordered_at 前缀覆盖
]

# 存量表补列声明(2026-11-02 S2-9 统一, 用户定案"补列之类统一处理"):
#   此前三处各一套 —— create_all_tables 内嵌补列 / ensure_schema 的旧补列段 /
#   index.py 启动段四组列探测补列(每次冷启动都跑, 不受门禁管)。现合并为这一份,
#   由 ensure_schema 全量路径统一执行(每表一次列探测, 缺才 ALTER)。
#   新库不需要它(_TABLES DDL 已含同名列); 它只服务存量表(CREATE IF NOT EXISTS 不改已存在表)。
_COLUMN_ADDS = {
    "cleansing_templates": [("cf_meta", "TEXT")],
    # 2026-10-29 出入库备注(明细弹窗最右列; 原表无此列, 导入的 remark 被丢弃)
    "inbound_records": [("remark", "VARCHAR(255) DEFAULT ''"),
                        ("warehouse_type", "VARCHAR(20) DEFAULT ''"),
                        # 2026-09-10 出入库记录带商品属性列(明细展示与导出需要; 原 schema 仅 10 列)
                        ("barcode", "VARCHAR(64) DEFAULT ''"),
                        ("platform", "VARCHAR(32) DEFAULT ''"),
                        ("brand", "VARCHAR(64) DEFAULT ''"),
                        ("store", "VARCHAR(64) DEFAULT ''"),
                        ("category", "VARCHAR(64) DEFAULT ''"),
                        ("price", "DECIMAL(12,2) DEFAULT 0"),
                        ("box_qty", "INT DEFAULT 0"),
                        ("unit", "VARCHAR(16) DEFAULT ''"),
                        ("weight", "DECIMAL(10,2) DEFAULT 0"),
                        ("volume", "DECIMAL(10,4) DEFAULT 0"),
                        ("status", "VARCHAR(16) DEFAULT ''")],
    "outbound_records": [("remark", "VARCHAR(255) DEFAULT ''"),
                         ("warehouse_type", "VARCHAR(20) DEFAULT ''"),
                         ("barcode", "VARCHAR(64) DEFAULT ''"),
                         ("platform", "VARCHAR(32) DEFAULT ''"),
                         ("brand", "VARCHAR(64) DEFAULT ''"),
                         ("store", "VARCHAR(64) DEFAULT ''"),
                         ("category", "VARCHAR(64) DEFAULT ''"),
                         ("price", "DECIMAL(12,2) DEFAULT 0"),
                         ("box_qty", "INT DEFAULT 0"),
                         ("unit", "VARCHAR(16) DEFAULT ''"),
                         ("weight", "DECIMAL(10,2) DEFAULT 0"),
                         ("volume", "DECIMAL(10,4) DEFAULT 0"),
                         ("status", "VARCHAR(16) DEFAULT ''")],
    "inventory": [("safety_days", "DECIMAL(10,2) DEFAULT 0"),    # 曾漏(采购建议 SELECT 用, 1054 实证)
                  ("ext_json", "TEXT"),
                  # 2026-11-04 账期: 导入的"当时可用"存这里做对账, 不再覆盖期初
                  ("snapshot_qty", "INT DEFAULT 0"),
                  # 2026-11-05 账期: 建账基线(首次结账时固化, 此后**永不覆盖**)。
                  #   beginning_stock 会被结账回写成"当期期初", 若再拿它当基线,
                  #   重跑结账就会把已结转的量再叠一次(不幂等)。基线必须独立存放。
                  ("ledger_base", "INT DEFAULT NULL")],
    # 2026-11-01 商品是否做效期管理: 存量表补列(空=待标注, 由一次性推导/导入填)
    # 2026-09-10 自定义扩展列 ext_json(用户动态新增列数据存放, 方案 B)
    "products": [("expiry_managed", "VARCHAR(8) DEFAULT ''"), ("ext_json", "TEXT")],
    # 2026-10-29 批次四维绑定(仓型+SKU+69码+批次信息): 补 barcode
    "batches": [("barcode", "VARCHAR(64) DEFAULT ''")],
    # 2026-10-29 日销按 69码 精准绑定(用户定案: 补货也按码): 快照层与物化层都加, 空码降级
    "daily_sales_snapshot": [("barcode", "VARCHAR(64) DEFAULT ''")],
    "sku_sales_stats": [("barcode", "VARCHAR(64) DEFAULT ''")],
    # 2026-09-10 自定义扩展列 ext_json(用户动态新增列数据存放, 方案 B)
    "orders": [("ext_json", "TEXT"),
               ("synced_at", "DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")],
    "suppliers": [("ext_json", "TEXT")],
    # 2026-10-29 告警逐仓化 + 按 69码 去重(SKU×码 各自独立)
    "alerts": [("warehouse", "VARCHAR(64) DEFAULT ''"),
               ("barcode", "VARCHAR(64) DEFAULT ''"),
               # 2026-11-03 D8: 关闭留痕(新库见 _TABLES.alerts) —— 同表多列已合并为一条,
               #   不可另起 "alerts" 键(dict 重复键会覆盖前一条, 直接丢掉 warehouse/barcode 补列)
               ("closed_reason", "VARCHAR(32) DEFAULT ''"),
               ("closed_at", "DATETIME NULL"),
               ("reopen_count", "INT DEFAULT 0")],
    # 规则携带业务计算参数(断货/健康看板逻辑融合进规则配置)
    "rules": [("params", "TEXT")],
    # 2026-11-01 库存指纹(治"一天只评估一次"致看板停旧快照)
    "maintenance_log": [("details", "VARCHAR(255) DEFAULT NULL")],
    # 2026-11-02 S2-1 写入令牌(防"重建期间被失效"的旧值覆盖墓碑)
    "analysis_cache": [("tok", "BIGINT DEFAULT 0")],
}

# schema 版本(2026-11-02 S2-8): **改任何表/索引/迁移声明 → bump 此号**, 否则存量实例不会执行新声明
# v4(2026-11-03 D8): alerts 增 closed_reason/closed_at/reopen_count + 维度索引
# v5(2026-11-04 S3-5): 建 schema_migrations; 存量数据类步骤迁入 migrations/(本版本号变化即触发一次
#   全量路径 → run_pending 只执行未登记的步骤, 已应用的靠 legacy_log_sync 归并, 不会被重跑)
# v6(2026-11-04 账期): 新增 inventory_month / batch_opening / ledger_state(月结三表)
SCHEMA_VERSION = 6


def table_ddl(name):
    """建表语句单一真源(2026-10-29): 其它模块不要再维护 DDL 副本
    —— 副本漂移是本轮事故根源之一(biz/sales._SKU_STATS_DDL 缺 barcode/主键不同步)"""
    return _TABLES.get(name, "")


def create_all_tables():
    """幂等建全表(新集群空库引导; 失败单表降级不阻塞)"""
    from routes.common import try_err
    # 2026-10-30 渠道种子(幂等): 存量库补登记 jd/other, 语义与旧硬编码完全一致
    try:
        execute("INSERT IGNORE INTO channels(code,name,enabled,supports_b,default_mode,sort) VALUES"
                "('jd','京东渠道',1,1,'bbcc',1),('other','其他渠道',1,0,'traditional',2)")
    except Exception as _ec:
        try_err('schema', '渠道种子写入降级', _ec)
    ok_n = 0
    _failed = []
    for name, ddl in _TABLES.items():
        try:
            execute(ddl)
            ok_n += 1
        except Exception as e:
            try_err('schema', '建表失败 %s' % name, e)
            _failed.append(name)
    # 2026-11-02 S2-5 订单业务键: 旧键 uq_orders_no_sku(order_no, sku) → 新键
    #   uq_orders_biz(channel, order_no, sku, barcode) —— 四维, 与写入层 upsert / 查重口径同一套。
    #
    # 为什么旧键不对(实测 2026-11-02):
    #   · 不含 barcode: 同单同 SKU 两个 69 码(换包装/条码变更)本该是两行, 旧键只准一行 →
    #     第二行 1062 → 单行降级计入 failed → 数量/金额**静默少算**, 而提示只有 "Duplicate entry"。
    #   · 不含 channel: 跨渠道同订单号+SKU 互阻 —— 与 gmv_flows 修过的是同一个坑
    #     (uq_platform_flow → uq_platform_flow_ch), orders 没跟着修。
    #   · 不含 deleted_at 也没关系: 写入层已改 upsert 且显式 deleted_at='' —— 重导即恢复。
    #
    # 顺序: **先建新键, 再删旧键**。中间任何一步失败都还有一把约束在, 不会出现"裸奔"窗口;
    #   反过来先删会有一段既没旧键也没新键的时间, 期间并发导入可直接写重。
    # 建键前查四维重复: 有重则按"每组保留最新一行"(与 upsert 末次覆盖语义一致)合并并留痕;
    #   组数过大不自动动手 —— 删数据的事宁可不做, 留给人判断。
    try:
        from db import query as _q, one as _q1
        _idx = {str(r.get("Key_name") or "") for r in _q("SHOW INDEX FROM orders")}
        if "uq_orders_biz" not in _idx:
            _dup = _q1("SELECT COUNT(*) c FROM (SELECT 1 FROM orders "
                       "GROUP BY channel, order_no, sku, barcode HAVING COUNT(*)>1) t")
            _dn = int((_dup or {}).get("c") or 0)
            if _dn > 5000:
                raise RuntimeError("四维重复 %d 组 > 5000, 需人工清理后再建键" % _dn)
            if _dn:
                execute("DELETE FROM orders WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
                        "(PARTITION BY channel, order_no, sku, barcode ORDER BY id DESC) rn "
                        "FROM orders) t WHERE t.rn > 1)")
                try_err('schema', '订单四维重复已合并(每组保留最新一行) %d 组' % _dn, '')
            execute("ALTER TABLE orders ADD UNIQUE KEY uq_orders_biz "
                    "(channel, order_no, sku, barcode)")
        _idx2 = {str(r.get("Key_name") or "") for r in _q("SHOW INDEX FROM orders")}
        if "uq_orders_no_sku" in _idx2:
            execute("ALTER TABLE orders DROP INDEX uq_orders_no_sku")
    except Exception as e:
        try_err('schema', 'orders 四维业务键迁移降级(旧键仍在, 行为与迁移前一致)', e)
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
    # 2026-10-29 规则字段路径 / 2026-10-30 低库存口径与文案 / 渠道名 / 超卖文案 /
    # 2026-11-01 效期管理推导 —— 五类**改存量数据**的步骤 2026-11-04 S3-5 迁入
    # `migrations/steps/`(有序、单步事务、只跑一次)。此处只保留声明式 DDL。
    # 2026-10-29 日销主键契约检测(根治, 不做 DDL):
    #   目标结构 = 代理主键 `id` + 业务唯一键(含 barcode)。
    #   TiDB 聚簇表**不支持 DROP PRIMARY KEY**(主键值编码进 Row Key = 行物理地址),
    #   旧写法 `ALTER TABLE ... DROP PRIMARY KEY, ADD PRIMARY KEY ...` 必然失败 ——
    #   每次启动记一条 error, 主键从未迁移成功(实证)。
    #   现改为: 只检测契约; 未达则登记 migration_log 待办, 由 /maintenance/heal-own
    #   {"surrogate": true} 走影子表迁移(见 routes/misc._migrate_surrogate)。
    try:
        from db import query as _qk4, execute as _ek4
        # 本函数可能在 index.py 建表段之前执行 → 自建登记表(幂等)
        _ek4("CREATE TABLE IF NOT EXISTS migration_log (name VARCHAR(64) PRIMARY KEY, "
             "done_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        _pk_contract = {
            "daily_sales_snapshot": ("id", "uq_snapshot_biz"),
            "sku_sales_stats": ("id", "uq_stats_biz"),
        }
        for _tb, (_pk_col, _uq) in _pk_contract.items():
            try:
                _idx = _qk4("SHOW INDEX FROM `%s`" % _tb)
            except Exception:
                continue
            _pk_cols = [r.get("Column_name") for r in _idx if r.get("Key_name") == "PRIMARY"]
            _has_uq = any(str(r.get("Key_name") or "") == _uq for r in _idx)
            if _pk_cols == [_pk_col] and _has_uq:
                continue
            try:
                if _ek4("INSERT IGNORE INTO migration_log(name) VALUES(%s)",
                        ["pk_surrogate:%s" % _tb]):
                    try_err('schema', '日销主键契约未达(待 surrogate 迁移): %s pk=%s'
                            % (_tb, ",".join(_pk_cols)), None)
            except Exception as _el:
                try_err('schema', '主键契约登记降级', _el)
    except Exception as _epk:
        try_err('schema', '日销主键契约检测降级', _epk)
    # 2026-10-29 超卖文案 / 2026-11-01 效期管理推导 → 2026-11-04 S3-5 迁入 migrations/steps/。
    # 2026-11-02 S2-9: 幂等补列已统一为模块级 _COLUMN_ADDS, 由 ensure_schema 全量路径执行
    #   (原此处内嵌补列 —— 与 index.py 启动段补列并存两套, 现合并为单一声明)。
    # 2026-11-01 库存业务唯一键(幂等; 有重复则登记待办, 不阻断启动):
    #   写入路径一直是"先查后写", 少一层库约束 —— 并发/大小写/空格差异会插出重复行,
    #   而 inventory 是四维对账的基准: 重复行会让 "Σ批次=可用" 与 "月汇总=流水" 同时失真,
    #   且每行经 SUM() 汇总后被成倍放大(与月汇总那次根因同类)。
    #   注意: ALTER 走 TiDB 在线 DDL, 大表(数十万行)会有分钟级等待 —— 只在本键不存在时执行一次。
    try:
        from db import query as _qk5, execute as _ek5
        _idx5 = _qk5("SHOW INDEX FROM inventory") or []
        if not any(str(r.get("Key_name") or "") == "uq_inventory_biz" for r in _idx5):
            _d5 = _qk5("SELECT COUNT(*) AS c FROM (SELECT 1 FROM inventory GROUP BY sku, "
                       "COALESCE(barcode,''), warehouse, channel, warehouse_type "
                       "HAVING COUNT(*)>1) t") or []
            _dup5 = int((_d5[0].get("c") if _d5 else 0) or 0)
            if _dup5:
                _ek5("INSERT IGNORE INTO migration_log(name) VALUES('uq_inventory_biz_blocked')")
                try_err('schema', '库存唯一键待办: %d 组重复行, 需先人工合并再建键' % _dup5, None)
            else:
                _ek5("UPDATE inventory SET barcode='' WHERE barcode IS NULL")  # NULL 会让唯一键失效
                _ek5("ALTER TABLE inventory ADD UNIQUE KEY uq_inventory_biz "
                     "(sku, barcode, warehouse, channel, warehouse_type)")
                try_err('schema', 'inventory 唯一键 uq_inventory_biz 建立完成', '')
    except Exception as _eix5:
        try_err('schema', 'inventory 唯一键迁移降级', _eix5)
    return ok_n, _failed


def ensure_schema():
    """schema 版本门禁(2026-11-02 S2-8, 用户定案"从表源头解决"): **热路径一条 SELECT, 零 DDL**

    旧启动每次冷启动发 ~50 条幂等 DDL(IF NOT EXISTS 在 TiDB 上仍走 schema 路径)。
    现在: ①先 SELECT schema_state(表不存在 → 版本 0); ②版本 ≥ SCHEMA_VERSION → 直接返回;
    ③否则全量幂等建表/索引/迁移(与旧启动完全同一套声明), **全部成功**才写版本 ——
    任一失败不写, 下次冷启动自动重试(不会带着缺口跳过)。
    改表/索引/迁移 → 只需 bump SCHEMA_VERSION + 改声明, 不再写"SHOW INDEX 检查 + ALTER"式迁移代码。
    并发安全: DDL 幂等, 版本写入 GREATEST 防回退; 两实例同时首跑 = 重复执行无害。
    """
    from db import one as _one, execute as _e
    from routes.common import try_err
    # ① 热路径: 一次 SELECT(表不存在 → 异常 → 视为版本 0)
    try:
        _row = _one("SELECT version FROM schema_state WHERE name='main'")
        _ver = int((_row or {}).get("version") or 0)
        if _ver >= SCHEMA_VERSION:
            return
    except Exception:
        _ver = 0
    # ② 版本表自身(唯一必须先建的表; 建表失败 → 后面写版本也会失败 → 下次重试, 无害)
    try:
        _e("CREATE TABLE IF NOT EXISTS schema_state (name VARCHAR(32) PRIMARY KEY, "
           "version INT NOT NULL DEFAULT 0, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP "
           "ON UPDATE CURRENT_TIMESTAMP)")
    except Exception as _e0:
        try_err('schema', 'schema_state 建表降级', _e0)
        return
    # ③ 全量幂等执行(声明式, 与旧启动同一套)
    _errors = []
    try:
        _ok_n, _failed = create_all_tables()
        if _failed:
            _errors.append("建表失败: %s" % ",".join(_failed))
    except Exception as _e1:
        _errors.append("create_all_tables 异常: %s" % _e1)
        try_err('schema', 'create_all_tables 降级', _e1)
    for _iname, _tbl, _cols in _INDEXES:
        try:
            _e("CREATE INDEX IF NOT EXISTS `%s` ON `%s` (%s)" % (_iname, _tbl, _cols))
        except Exception as _e2:
            _errors.append("索引 %s: %s" % (_iname, _e2))
            try_err('schema', '索引降级 %s' % _iname, _e2)
    for _iname2, _itbl2, _icols2 in _ADD_INDEXES:
        try:
            _e("ALTER TABLE `%s` ADD INDEX `%s` %s" % (_itbl2, _iname2, _icols2))
        except Exception as _e3:
            # 已存在(重复报错)不算失败 —— 与旧启动"忽略重复"语义一致
            if "Duplicate key name" not in str(_e3):
                _errors.append("补索引 %s: %s" % (_iname2, _e3))
    for _iname3, _itbl3 in _DROP_INDEXES:
        try:
            _e("DROP INDEX IF EXISTS `%s` ON `%s`" % (_iname3, _itbl3))
        except Exception as _e4:
            _errors.append("删索引 %s: %s" % (_iname3, _e4))
    # 补列(统一声明 _COLUMN_ADDS): 每表一次 SHOW COLUMNS, 缺才 ALTER
    try:
        from db import query as _qc9
        for _t, _cols in _COLUMN_ADDS.items():
            try:
                _have = {str(r.get("Field") or "") for r in _qc9("SHOW COLUMNS FROM `%s`" % _t)}
            except Exception as _e9a:
                _errors.append("补列探测 %s: %s" % (_t, _e9a))
                continue
            for _c, _c_ddl in _cols:
                if _c in _have:
                    continue
                try:
                    _e("ALTER TABLE `%s` ADD COLUMN `%s` %s" % (_t, _c, _c_ddl))
                except Exception as _e9:
                    if "Duplicate column" not in str(_e9):
                        _errors.append("补列 %s.%s: %s" % (_t, _c, _e9))
    except Exception as _e9b:
        try_err('schema', '补列统一执行降级', _e9b)
    # ④ 存量数据类步骤(有序, 只跑未登记的): 2026-11-04 S3-5 从本函数的内联段迁出。
    #    失败不阻断后续, 也不写版本 → 下次冷启动重试。见 migrations/runner.py
    try:
        from migrations.runner import run_pending
        _m_ok, _m_fail = run_pending()
        if _m_fail:
            _errors.append("迁移失败: %s" % ",".join(_m_fail))
    except Exception as _em:
        _errors.append("迁移框架异常: %s" % _em)
        try_err('schema', '迁移框架降级', _em)
    # ⑤ 全部成功才写版本(任一失败 → 不写 → 下次冷启动重试)
    if _errors:
        try_err('schema', 'schema 初始化未完成(%d 处失败), 不写版本, 下次冷启动重试' % len(_errors),
                "; ".join(_errors)[:400])
        return
    try:
        _e("INSERT INTO schema_state(name, version) VALUES('main', %s) "
           "ON DUPLICATE KEY UPDATE version=GREATEST(version, VALUES(version))", [SCHEMA_VERSION])
        try_err('schema', 'schema v%d 就绪(下次实例启动零 DDL)' % SCHEMA_VERSION, '')
    except Exception as _e6:
        try_err('schema', 'schema 版本写入降级', _e6)
