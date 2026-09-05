"""SQLite 数据库层，接口风格兼容 db-py，方便未来迁 PostgreSQL

用法：db = SQLiteDB("data.db")
      db.table("orders").select("*").eq("order_no","xxx").execute()
      db.table("orders").insert([{"order_no":"xxx"}]).execute()
"""
import sqlite3, json, os, threading, concurrent.futures, re
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any, Optional

DB_PATH = os.getenv("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "..", "supplykit.db"))
SCHEMA_VERSION = 23  # 当前 schema 版本，每次改表结构+1

# 版本化迁移注册表：{目标版本: 迁移函数}
# 迁移函数签名: def migrate(conn): 执行该版本的 schema 变更
_MIGRATIONS = {}


def _register_migration(version):
    def decorator(fn):
        _MIGRATIONS[version] = fn
        return fn
    return decorator


# 示例迁移 v2：创建 migration_log 表（记录迁移历史）
@_register_migration(2)
def _migrate_v2(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS migration_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "version INTEGER NOT NULL,"
        "applied_at TEXT DEFAULT (datetime('now')),"
        "description TEXT DEFAULT '')")


# 迁移 v3：将 init_db 中硬编码的 ALTER TABLE 补列迁移至版本化系统
@_register_migration(3)
def _migrate_v3(conn):
    import sqlite3
    _alters = [
        "ALTER TABLE products ADD COLUMN box_qty INTEGER DEFAULT 1",
        "ALTER TABLE products ADD COLUMN barcode TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN weight REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN volume REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN channel TEXT DEFAULT 'jd'",
        "ALTER TABLE products ADD COLUMN unit TEXT DEFAULT ''",
        "ALTER TABLE suppliers ADD COLUMN channel TEXT DEFAULT 'jd'",
        "ALTER TABLE orders ADD COLUMN channel TEXT DEFAULT 'jd'",
        "ALTER TABLE orders ADD COLUMN paid_at TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN barcode TEXT DEFAULT ''",
        "ALTER TABLE inventory ADD COLUMN channel TEXT DEFAULT 'jd'",
        "ALTER TABLE inventory ADD COLUMN beginning_stock INTEGER DEFAULT 0",
        "ALTER TABLE inventory ADD COLUMN month_inbound INTEGER DEFAULT 0",
        "ALTER TABLE inventory ADD COLUMN month_outbound INTEGER DEFAULT 0",
        "ALTER TABLE rules ADD COLUMN mode TEXT DEFAULT ''",
        "ALTER TABLE daily_sales_snapshot ADD COLUMN warehouse TEXT DEFAULT ''",
        "ALTER TABLE daily_sales_snapshot ADD COLUMN channel TEXT DEFAULT 'jd'",
    ]
    for sql in _alters:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在则跳过（幂等）


# 迁移 v4：alerts 表加 pushed 列（告警推送去重标记）
@_register_migration(4)
def _migrate_v4(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE alerts ADD COLUMN pushed INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 列已存在则跳过（幂等）


# 迁移 v5：products 表加 deleted_at（软删除 + 建议页联动过滤）
@_register_migration(5)
def _migrate_v5(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE products ADD COLUMN deleted_at TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在则跳过（幂等）


# 迁移 v6：处置记录表（滞销处置闭环：批量标记已处置，避免重复建议）
@_register_migration(6)
def _migrate_v6(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS disposal_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT DEFAULT '',
        warehouse TEXT DEFAULT '',
        warehouse_type TEXT DEFAULT '',
        channel TEXT DEFAULT 'jd',
        level TEXT DEFAULT '',
        turnover_days REAL DEFAULT 0,
        reason TEXT DEFAULT '',
        action TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )""")


# 迁移 v7：products 加 best_before（保质期/临期判断，清洗导入可映射）
@_register_migration(7)
def _migrate_v7(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE products ADD COLUMN best_before TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在则跳过（幂等）


# 迁移 v8：批次表（多批次效期管理：生产日期/截止日期/数量）
@_register_migration(8)
def _migrate_v8(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT DEFAULT '',
        warehouse TEXT DEFAULT '',
        warehouse_type TEXT DEFAULT '',
        channel TEXT DEFAULT 'jd',
        prod_date TEXT DEFAULT '',
        exp_date TEXT DEFAULT '',
        qty INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_batches_sku_wh ON batches(sku, warehouse, channel)")


# 迁移 v9：products/suppliers 加 brand（品牌列）
@_register_migration(9)
def _migrate_v9(conn):
    import sqlite3
    for sql in ["ALTER TABLE products ADD COLUMN brand TEXT DEFAULT ''",
                "ALTER TABLE suppliers ADD COLUMN brand TEXT DEFAULT ''"]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在则跳过（幂等）


# 迁移 v10：inbound_records/outbound_records 加批次字段（生产日期/截止日期，供进销存展开消耗占比）
@_register_migration(10)
def _migrate_v10(conn):
    import sqlite3
    for sql in ["ALTER TABLE inbound_records ADD COLUMN prod_date TEXT DEFAULT ''",
                "ALTER TABLE inbound_records ADD COLUMN exp_date TEXT DEFAULT ''",
                "ALTER TABLE outbound_records ADD COLUMN prod_date TEXT DEFAULT ''",
                "ALTER TABLE outbound_records ADD COLUMN exp_date TEXT DEFAULT ''"]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在则跳过（幂等）


# 迁移 v11：inbound_records/outbound_records 加 warehouse 列（批次出入库按仓库聚合）
@_register_migration(11)
def _migrate_v11(conn):
    import sqlite3
    for sql in ["ALTER TABLE inbound_records ADD COLUMN warehouse TEXT DEFAULT ''",
                "ALTER TABLE outbound_records ADD COLUMN warehouse TEXT DEFAULT ''"]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # 列已存在则跳过（幂等）


# 迁移 v12：入库/出库记录加唯一索引(日期+仓库+SKU+批次去重求和)
@_register_migration(12)
def _migrate_v12(conn):
    import sqlite3
    for tbl in ['inbound_records', 'outbound_records']:
        _date_col = 'inbound_date' if tbl == 'inbound_records' else 'outbound_date'
        try:
            conn.execute(f"DELETE FROM {tbl} WHERE id NOT IN (SELECT MIN(id) FROM {tbl} GROUP BY sku, warehouse, channel, COALESCE(prod_date,''), COALESCE(exp_date,''), {_date_col})")
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{tbl}_unique ON {tbl}(sku, warehouse, channel, COALESCE(prod_date,''), COALESCE(exp_date,''), {_date_col})")
        except sqlite3.OperationalError as _e:
            import logging; logging.warning(f"[migration v12] {tbl}: {_e}")


# 迁移 v13：alerts 表加 related_rule_id（规则禁用/删除时联动清理告警）
@_register_migration(13)
def _migrate_v13(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE alerts ADD COLUMN related_rule_id INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 列已存在则跳过（幂等）


# 迁移 v14：rules 表加 deleted_at（软删除列缺失导致 delete/restore 500 报错）
@_register_migration(14)
def _migrate_v14(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE rules ADD COLUMN deleted_at TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在则跳过（幂等）


# 迁移 v15：orders 表加 deleted_at（与 rules 同源缺陷：软删除列缺失，删单 500 + 列表不过滤）
@_register_migration(15)
def _migrate_v15(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN deleted_at TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # 列已存在则跳过（幂等）


# 迁移 v16：统一 deleted_at 语义——NULL → ''（允许查询简化为 deleted_at=''，去掉 OR 条件 3 倍提速）
@_register_migration(16)
def _migrate_v16(conn):
    # orders（restore 曾写 None 产生 NULL）、products、rules
    for _t in ['orders', 'products', 'rules']:
        try:
            conn.execute(f"UPDATE {_t} SET deleted_at = '' WHERE deleted_at IS NULL")
        except Exception:
            pass
    conn.commit()


# 迁移 v17：仓库标准映射注册表（warehouse_name → warehouse_type，保证跨导入类型一致）
@_register_migration(17)
def _migrate_v17(conn):
    import sqlite3
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS warehouse_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            warehouse TEXT NOT NULL DEFAULT '',
            warehouse_type TEXT NOT NULL DEFAULT '',
            channel TEXT DEFAULT 'jd',
            UNIQUE(warehouse, channel)
        )""")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 表已存在则跳过（幂等）


# 迁移 v18：订单金额明细化（GMV 口径: total - discount + freight + tax）
# freight_amount 买家运费(计入GMV) / subsidy_amount 平台补贴(单独拆解, 实际回款=净GMV-补贴)
# tax_amount 税费(计入GMV) / discount_amount 店铺满减(已扣减, 不计入GMV) / actual_amount 用户实付(派生快照)
@_register_migration(18)
def _migrate_v18(conn):
    import sqlite3
    for _col, _ddl in [
        ('freight_amount', 'REAL DEFAULT 0'),
        ('subsidy_amount', 'REAL DEFAULT 0'),
        ('tax_amount', 'REAL DEFAULT 0'),
        ('discount_amount', 'REAL DEFAULT 0'),
        ('actual_amount', 'REAL DEFAULT 0'),
    ]:
        try:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {_col} {_ddl}")
        except sqlite3.OperationalError:
            pass  # 列已存在则跳过（幂等）
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN paid_at TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# 迁移 v19：告警表加 warehouse_type（触发仓库主体）——看板待处理卡 B/C/自有 分布精确化
# 此前该分布走前端缺货SKU表 lookup, 漏缺货SKU以外的告警 → 系统性误算进 C 仓(失真)
@_register_migration(19)
def _migrate_v19(conn):
    import sqlite3
    try:
        conn.execute("ALTER TABLE alerts ADD COLUMN warehouse_type TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 存量告警回填: 优先取该 SKU 有低库存风险(available_qty<safety_qty)的库存行仓主体
    try:
        conn.execute("""UPDATE alerts SET warehouse_type = (
            SELECT i.warehouse_type FROM inventory i
            WHERE i.channel = alerts.channel AND i.sku = alerts.related_sku
            ORDER BY (i.available_qty < i.safety_qty) DESC, i.warehouse_type LIMIT 1
        ) WHERE related_sku != ''""")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# 迁移 v20：修正 v19 回填——曾用 MIN(warehouse_type) 按字母序把 own 排最前,
# 全部 SKU 被回填成 own(分布失真)。改为"低库存风险仓优先"后重刷。
@_register_migration(20)
def _migrate_v20(conn):
    import sqlite3
    try:
        conn.execute("""UPDATE alerts SET warehouse_type = (
            SELECT i.warehouse_type FROM inventory i
            WHERE i.channel = alerts.channel AND i.sku = alerts.related_sku
            ORDER BY (CASE WHEN i.safety_qty > 0 THEN i.available_qty * 1.0 / i.safety_qty ELSE 1 END) ASC, i.warehouse_type LIMIT 1
        ) WHERE related_sku != ''""")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# 迁移 v21：回填改"最缺仓优先"(avail/safety 比值最小)——多仓都有低库存风险时,
# 取相对最缺的仓作为告警归属(更贴合告警语义); 覆盖 v20 的"低库存风险仓+字母序own优先"偏差
@_register_migration(21)
def _migrate_v21(conn):
    import sqlite3
    try:
        conn.execute("""UPDATE alerts SET warehouse_type = (
            SELECT i.warehouse_type FROM inventory i
            WHERE i.channel = alerts.channel AND i.sku = alerts.related_sku
            ORDER BY (CASE WHEN i.safety_qty > 0 THEN i.available_qty * 1.0 / i.safety_qty ELSE 1 END) ASC, i.warehouse_type LIMIT 1
        ) WHERE related_sku != ''""")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# 迁移 v22：滞销告警 warehouse_type 回填——滞销=库存积压在哪类仓(自有/B/C), 取库存最多的仓
# (之前滞销生成不写仓→unknown; 现在生成即写, 存量回填)
@_register_migration(22)
def _migrate_v22(conn):
    import sqlite3
    try:
        conn.execute("""UPDATE alerts SET warehouse_type = (
            SELECT i.warehouse_type FROM inventory i
            WHERE i.channel = alerts.channel AND i.sku = alerts.related_sku
            ORDER BY i.available_qty DESC, i.warehouse_type LIMIT 1
        ) WHERE alert_type = 'slow_moving' AND related_sku != ''""")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# 迁移 v23：补货告警(replenishment_engine) warehouse_type 回填——曾 INSERT 不带仓(全部 unknown)
# 取该 SKU 库存主体: 优先有货仓, 回退任意仓
@_register_migration(23)
def _migrate_v23(conn):
    import sqlite3
    try:
        conn.execute("""UPDATE alerts SET warehouse_type = (
            SELECT i.warehouse_type FROM inventory i
            WHERE i.channel = alerts.channel AND i.sku = alerts.related_sku
            ORDER BY CASE WHEN i.available_qty > 0 THEN 0 ELSE 1 END, i.available_qty DESC, i.warehouse_type LIMIT 1
        ) WHERE alert_type = 'replenish' AND related_sku != ''""")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.commit()

_local = threading.local()

def backup_db():
    """备份数据库到同目录下（压缩备份，减少体积防撑爆配额）

    修复 0 字节备份 bug：
    1. 改用 sqlite3 官方 backup API（在线备份，无需独占锁，WAL 下安全）
    2. 替代 VACUUM INTO（PA 环境可能失败且被静默吞掉 → 空 gzip）
    3. 全程日志 + 生成后验证（gzip 可解压且超阈值），失败返回 None 并记录原因
    """
    import shutil, gzip, logging
    _logger = logging.getLogger("backup")
    bak_path = DB_PATH + f".bak.{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    try:
        # 1. 在线备份到临时文件（sqlite3 backup API：增量复制，不锁库）
        _tmp = DB_PATH + ".bak.tmp"
        if os.path.exists(_tmp):
            try: os.remove(_tmp)
            except Exception: pass
        try:
            _src = sqlite3.connect(DB_PATH, timeout=30)
            _dst = sqlite3.connect(_tmp, timeout=30)
            _src.backup(_dst)  # 官方在线备份
            _dst.close()
            _src.close()
        except Exception as e:
            _logger.error(f"[backup] online backup failed: {e}")
            try: _dst.close()
            except Exception: pass
            try: _src.close()
            except Exception: pass
            _tmp = None
        # 2. 若在线备份成功且有效 → 压缩
        if _tmp and os.path.exists(_tmp) and os.path.getsize(_tmp) > 1024:
            _gz = bak_path + ".gz"
            with open(_tmp, 'rb') as fi, gzip.open(_gz, 'wb', compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo, 1024*1024)
            try: os.remove(_tmp)
            except Exception: pass
            # 3. 验证备份有效：gzip 能解压且大小超阈值
            try:
                with gzip.open(_gz, 'rb') as f:
                    _head = f.read(16)
                if len(_head) > 8 and os.path.getsize(_gz) > 1024:
                    _logger.info(f"[backup] OK: {_gz} ({os.path.getsize(_gz)}B, 解压头 {len(_head)}B)")
                    return _gz
                _logger.error(f"[backup] invalid backup content: {_gz} size={os.path.getsize(_gz)}")
                return None
            except Exception as e:
                _logger.error(f"[backup] gzip verify failed: {e}")
                return None
        _logger.error("[backup] online backup produced empty file")
        # 3. 降级：直接复制主库 + 压缩（仍验证）
        try:
            _raw = bak_path + ".raw"
            shutil.copy2(DB_PATH, _raw)
            _gz = bak_path + ".gz"
            with open(_raw, 'rb') as fi, gzip.open(_gz, 'wb', compresslevel=6) as fo:
                shutil.copyfileobj(fi, fo, 1024*1024)
            os.remove(_raw)
            with gzip.open(_gz, 'rb') as f:
                _head = f.read(16)
            if len(_head) > 8 and os.path.getsize(_gz) > 1024:
                _logger.info(f"[backup] OK(fallback): {_gz} ({os.path.getsize(_gz)}B)")
                return _gz
            _logger.error(f"[backup] fallback invalid: {_gz}")
            return None
        except Exception as e:
            _logger.error(f"[backup] fallback failed: {e}")
            return None
    except Exception as e:
        _logger.error(f"[backup] unexpected: {e}")
        # 最终降级：直接复制压缩（即使失败也尝试）
        try:
            _fallback_gz = bak_path + ".gz"
            with open(DB_PATH, 'rb') as _fi, gzip.open(_fallback_gz, 'wb', compresslevel=6) as _fo:
                shutil.copyfileobj(_fi, _fo, 1024*1024)
            if os.path.getsize(_fallback_gz) > 1024:
                return _fallback_gz
            _logger.error(f"[backup] final fallback too small: {os.path.getsize(_fallback_gz)}B")
            return None
        except Exception as _e:
            _logger.error(f"[backup] final fallback failed: {_e}")
            return None

# ─── 轻量异步任务队列 ──────────────────────────────────────────────────────

_task_queue = []
_task_results = {}
_task_lock = threading.Lock()
# 线程池（限制最大并发任务数，避免无限创建线程）
_task_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="bg_task")

def _task_db_save(task_id, task_type='background', channel='jd', **fields):
    """持久化任务状态到 sync_tasks 表（跨重启可查）"""
    try:
        import json
        conn = get_conn()
        status = fields.get('status', 'running')
        result = fields.get('result')
        steps = fields.get('steps')
        payload = {}
        if result is not None: payload['result'] = result
        if steps is not None: payload['steps'] = steps
        for k, v in fields.items():
            if k not in ('status', 'result', 'steps'):
                payload[k] = v
        _ch = channel
        rows = conn.execute("SELECT id FROM sync_tasks WHERE task_id=?", (task_id,)).fetchall()
        if rows:
            conn.execute("UPDATE sync_tasks SET status=?, result=?, updated_at=datetime('now') WHERE task_id=?",
                (status, json.dumps(payload, ensure_ascii=False, default=str), task_id))
        else:
            conn.execute("INSERT INTO sync_tasks(task_id, task_type, status, result, channel, created_at, updated_at) VALUES(?,?,?,?,?,datetime('now'),datetime('now'))",
                (task_id, task_type, status, json.dumps(payload, ensure_ascii=False, default=str), _ch))
        conn.commit()
    except Exception as e:
        import logging; logging.error(f"[tasks] 持久化任务状态失败 {task_id}: {e}")

def submit_task(task_id: str, fn, *args, **kwargs):
    # 提取 channel 和 task_type 参数
    _channel = kwargs.pop('channel', 'jd')
    _task_type = kwargs.pop('task_type', 'background')
    """提交一个后台任务（独立线程运行，状态持久化到数据库）"""
    with _task_lock:
        _task_results[task_id] = {"status": "pending", "result": None, "error": None}
    _task_db_save(task_id, task_type=_task_type, channel=_channel, status='pending')
    def _run():
        try:
            with _task_lock:
                _task_results[task_id]["status"] = "running"
            _task_db_save(task_id, task_type=_task_type, channel=_channel, status='running')
            result = fn(*args, **kwargs)
            with _task_lock:
                _task_results[task_id]["status"] = "done"
                _task_results[task_id]["result"] = result
            _task_db_save(task_id, task_type=_task_type, channel=_channel, status='done', result=result)
        except Exception as e:
            with _task_lock:
                _task_results[task_id]["status"] = "error"
                _task_results[task_id]["error"] = str(e)
            _task_db_save(task_id, task_type=_task_type, channel=_channel, status='error', error=str(e))
    _task_executor.submit(_run)
    return task_id

def get_task(task_id: str):
    """读取任务状态：优先内存，内存缺失（重启后）回退查数据库"""
    with _task_lock:
        t = _task_results.get(task_id)
        if t:
            return dict(t)
    # 内存缺失：查数据库恢复
    try:
        import json
        conn = get_conn()
        rows = conn.execute("SELECT status, result, updated_at FROM sync_tasks WHERE task_id=?", (task_id,)).fetchall()
        if rows:
            status, result_json, updated_at = rows[0]
            # 卡死任务自愈: 内存缺失(PA重启线程被杀) + running/pending 超15分钟无更新 → 标记 error
            # 曾致前端轮询无限"进行中"直到列表接口30min清理——单任务轮询也应即时感知
            if status in ('running', 'pending') and updated_at:
                import time as _t
                try:
                    from datetime import datetime as _dt
                    _ut = _dt.strptime(str(updated_at)[:19], '%Y-%m-%d %H:%M:%S')
                    if (_dt.utcnow() - _ut).total_seconds() > 900:
                        _payload = json.dumps({"error": "任务可能因服务重启中断，已自动标记失败，请重新提交"}, ensure_ascii=False)
                        conn.execute("UPDATE sync_tasks SET status='error', result=?, updated_at=datetime('now') WHERE task_id=?", (_payload, task_id))
                        conn.commit()
                        status = 'error'
                        result_json = _payload
                except Exception:
                    pass
            data = {"status": status}
            try:
                payload = json.loads(result_json or '{}')
                if 'result' in payload: data['result'] = payload['result']
                if 'steps' in payload: data['steps'] = payload['steps']
                if 'error' in payload: data['error'] = payload['error']
            except Exception:
                pass
            # 回填内存
            with _task_lock:
                _task_results[task_id] = data
            return dict(data)
    except Exception:
        pass
    return None

def update_task(task_id: str, **kwargs):
    """更新任务的进度等信息（从任务内部调用，持久化到数据库）"""
    with _task_lock:
        if task_id in _task_results:
            _task_results[task_id].update(kwargs)
    # 读取已有 task_type/channel（避免覆盖成 background）
    _tt = 'background'; _ch = 'jd'
    try:
        conn = get_conn()
        r = conn.execute("SELECT task_type, channel FROM sync_tasks WHERE task_id=?", (task_id,)).fetchone()
        if r:
            _tt = r[0] or 'background'; _ch = r[1] or 'jd'
    except Exception:
        pass
    _task_db_save(task_id, task_type=kwargs.get('task_type', _tt), channel=kwargs.get('channel', _ch), **kwargs)

# 写入队列（串行化 SQLite 写操作，避免并发写入冲突）
_write_lock = threading.Lock()


def write_execute(sql, params=None):
    """串行化写操作，避免多线程并发写入 SQLite 冲突"""
    with _write_lock:
        conn = get_conn()
        if params:
            conn.execute(sql, params)
        else:
            conn.execute(sql)
        conn.commit()


class transaction:
    """事务上下文管理器：with transaction(): ... 自动 commit/rollback"""
    def __enter__(self):
        self.conn = get_conn()
        self.conn.execute("BEGIN")
        return self.conn
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try: self.conn.rollback()
            except Exception: pass
            return False
        try: self.conn.commit()
        except Exception: pass
        return False


def incremental_vacuum(db=None):
    """增量回收空间（不需要独占锁，auto_vacuum=INCREMENTAL 时生效）"""
    try:
        conn = db or get_conn()
        conn.execute("PRAGMA incremental_vacuum")
        conn.commit()
        return True
    except Exception:
        return False


def _backend():
    """当前数据后端: sqlite(默认) / tidb(Phase2 迁移)"""
    return os.getenv("DB_BACKEND", "sqlite").lower()


def _finalize_sql(sql):
    """SQL 后端适配: tidb 时 SQLite 方言→TiDB + 双引号标识符→反引号 + ? → %s"""
    if _backend() != "tidb":
        return sql
    from app.core.dialect import to_tidb
    sql = to_tidb(sql)
    sql = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)"', r'`\1`', sql)
    return sql.replace("?", "%s")


def get_conn():
    if _backend() == "tidb":
        from app.core.tidb_backend import get_conn as _tidb_conn
        return _tidb_conn()
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        try:
            _local.conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            _local.conn.execute("PRAGMA journal_mode=DELETE")
        _local.conn.execute("PRAGMA busy_timeout=15000")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    else:
        try:
            _local.conn.execute("SELECT 1")
        except Exception:
            _local.conn = sqlite3.connect(DB_PATH)
            _local.conn.row_factory = sqlite3.Row
            try:
                _local.conn.execute("PRAGMA journal_mode=WAL")
            except Exception:
                _local.conn.execute("PRAGMA journal_mode=DELETE")
            _local.conn.execute("PRAGMA busy_timeout=15000")
            _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn

def _quote_col(col):
    "转义列名(按后端: sqlite 双引号 / tidb 反引号), 防SQL注入"
    if _backend() == "tidb":
        return "`" + col.replace("`", "``") + "`"
    return '"' + col.replace('"', '""') + '"'


class QueryBuilder:
    def __init__(self, table, conn):
        self.table = table
        self.conn = conn
        self._where = []
        self._params = []
        self._order = ""
        self._limit = 0
        self._offset = 0
        self._select_cols = "*"

    def select(self, cols="*"):
        self._select_cols = cols
        return self

    def _quote_col(self, col):
        return _quote_col(col)

    def eq(self, col, val):
        self._where.append(f'{self._quote_col(col)} = ?')
        self._params.append(val)
        return self

    def neq(self, col, val):
        self._where.append(f'{self._quote_col(col)} != ?')
        self._params.append(val)
        return self

    def like(self, col, pattern):
        self._where.append(f'{self._quote_col(col)} LIKE ?')
        self._params.append(pattern)
        return self

    def in_(self, col, vals):
        if not vals:
            self._where.append("1=0")
            return self
        placeholders = ",".join(["?"] * len(vals))
        self._where.append(f'{self._quote_col(col)} IN ({placeholders})')
        self._params.extend(vals)
        return self

    def ilike(self, col, pattern):
        self._where.append(f'LOWER({self._quote_col(col)}) LIKE ?')
        self._params.append(pattern.lower())
        return self

    def single(self):
        result = self.limit(1).execute()
        return result.data[0] if result.data else None

    def or_(self, other):
        # 合并两个 QueryBuilder 的 WHERE 条件用 OR 连接
        combined_where = f"({' AND '.join(self._where)})" if self._where else "1=1"
        other_where = f"({' AND '.join(other._where)})" if other._where else "1=1"
        new_qb = QueryBuilder(self.table, self.conn)
        new_qb._where = [f"{combined_where} OR {other_where}"]
        new_qb._params = self._params + other._params
        return new_qb

    def gte(self, col, val):
        self._where.append(f'{self._quote_col(col)} >= ?')
        self._params.append(val)
        return self

    def lte(self, col, val):
        self._where.append(f'{self._quote_col(col)} <= ?')
        self._params.append(val)
        return self

    def order(self, col, desc=False):
        self._order = f'ORDER BY {self._quote_col(col)} {"DESC" if desc else "ASC"}'
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def _build_where(self):
        return " AND ".join(self._where) if self._where else "1=1"

    def execute(self):
        cur = self.conn.cursor()
        try:
            if self._select_cols.startswith("count"):
                sql = f'SELECT {self._select_cols} FROM "{self.table}" WHERE {self._build_where()}'
                cur.execute(_finalize_sql(sql), self._params)
                row = cur.fetchone()
                # sqlite3.Row 用下标; pymysql DictCursor 是 dict
                _c = row[0] if row is not None else 0
                if isinstance(_c, dict):
                    _c = list(_c.values())[0] if _c else 0
                result = ExecuteResult([], count=_c or 0)
                if os.getenv('DB_LOG'): import logging; logging.info(f"[DB] count {self.table} → {result.count}")
                return result
            sql = f'SELECT {self._select_cols} FROM "{self.table}" WHERE {self._build_where()}'
            if self._order: sql += " " + self._order
            if self._limit: sql += f" LIMIT {self._limit}"
            if self._offset: sql += f" OFFSET {self._offset}"
            cur.execute(_finalize_sql(sql), self._params)
            rows = [dict(r) for r in cur.fetchall()]
            result = ExecuteResult(rows)
            if os.getenv('DB_LOG'): import logging; logging.info(f"[DB] query {self.table} → {len(rows)} rows")
            return result
        finally:
            cur.close()

class ExecuteResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _write_execute(conn, sql, params=None, retries=3):
    """写操作执行：database is locked / busy 时自动重试（指数退避）

    根治线上"偶发 500 reload 恢复"问题：PA 慢磁盘 + WAL + 单 worker 下
    写锁竞争常见，busy_timeout 等待超时即抛 locked → 500。
    统一在此重试（SQLite 应用标准做法），替代逐接口打补丁。
    """
    # TiDB 后端: 走 tidb_backend.execute(已含方言转换/commit)
    if _backend() == "tidb":
        from app.core.tidb_backend import execute as _t_exec
        _t_exec(_finalize_sql(sql), params or ())
        return None
    import time as _t
    for attempt in range(retries + 1):
        try:
            cur = conn.execute(sql, params or [])
            conn.commit()
            return cur
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ('locked' in msg or 'busy' in msg) and attempt < retries:
                _t.sleep(0.25 * (attempt + 1))
                # 锁竞争后连接可能失效，重连一次更稳妥
                if attempt >= 1:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                continue
            raise

class InsertBuilder:
    def __init__(self, table, conn):
        self.table = table
        self.conn = conn

    def execute(self):
        sql = _finalize_sql(f'INSERT INTO "{self.table}" ({self._cols}) VALUES ({self._vals})')
        if _backend() == "tidb":
            from app.core.tidb_backend import execute_lastrowid
            rid = execute_lastrowid(sql, self._params)
            result = ExecuteResult([{"id": rid}])
            if os.getenv('DB_LOG'): import logging; logging.info(f"[DB] insert {self.table} → id={rid}")
            return result
        cur = _write_execute(self.conn, sql, self._params)
        result = ExecuteResult([{"id": cur.lastrowid}])
        if os.getenv('DB_LOG'): import logging; logging.info(f"[DB] insert {self.table} → id={cur.lastrowid}")
        return result

class UpdateBuilder:
    def __init__(self, table, conn, data):
        self.table = table
        self.conn = conn
        self.data = data
        self._where = []
        self._params = []

    def eq(self, col, val):
        self._where.append(f'{_quote_col(col)} = ?')
        self._params.append(val)
        return self

    def in_(self, col, vals):
        if not vals:
            self._where.append("1=0")
            return self
        placeholders = ", ".join(["?"] * len(vals))
        self._where.append(f'{_quote_col(col)} IN ({placeholders})')
        self._params.extend(vals)
        return self

    def execute(self):
        if not self._where:
            raise Exception("UPDATE without WHERE is not allowed")
        sets = ", ".join(f'"{k}" = ?' for k in self.data)
        vals = list(self.data.values()) + self._params
        sql = _finalize_sql(f'UPDATE "{self.table}" SET {sets} WHERE {" AND ".join(self._where)}')
        _write_execute(self.conn, sql, vals)
        return ExecuteResult([])

class DeleteBuilder:
    def __init__(self, table, conn):
        self.table = table
        self.conn = conn
        self._where = []
        self._params = []

    def eq(self, col, val):
        self._where.append(f'{_quote_col(col)} = ?')
        self._params.append(val)
        return self

    def in_(self, col, vals):
        if not vals:
            self._where.append("1=0")
            return self
        placeholders = ",".join(["?"] * len(vals))
        self._where.append(f'{_quote_col(col)} IN ({placeholders})')
        self._params.extend(vals)
        return self

    def ilike(self, col, pattern):
        self._where.append(f'LOWER("{col}") LIKE ?')
        self._params.append(pattern.replace("%", "%").lower())
        return self

    def execute(self):
        if not self._where:
            raise Exception("DELETE without WHERE is not allowed")
        sql = _finalize_sql(f'DELETE FROM "{self.table}" WHERE {" AND ".join(self._where)}')
        _write_execute(self.conn, sql, self._params)
        return ExecuteResult([])

class TableRef:
    def __init__(self, table, conn):
        self.table = table
        self.conn = conn

    def select(self, cols="*"):
        return QueryBuilder(self.table, self.conn).select(cols)

    def insert(self, rows):
        if not rows: raise Exception("insert requires at least one row")
        if isinstance(rows, dict): rows = [rows]
        builder = InsertBuilder(self.table, self.conn)
        cols = list(rows[0].keys())
        builder._cols = ", ".join(f'"{c}"' for c in cols)
        all_vals = []
        all_params = []
        for row in rows:
            placeholders = ", ".join(["?"] * len(cols))
            all_vals.append(placeholders)
            all_params.extend([row.get(c) for c in cols])
        builder._vals = "), (".join(all_vals)
        builder._params = all_params
        builder._multi = len(rows)
        return builder

    def update(self, data):
        return UpdateBuilder(self.table, self.conn, data)

    def upsert(self, row, conflict_col='id'):
        """INSERT OR REPLACE（SQLite 版 upsert; TiDB 走 REPLACE INTO）"""
        if not row: raise Exception("upsert requires a dict")
        cols = list(row.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(f'"{c}"' for c in cols)
        sql = _finalize_sql(f'INSERT OR REPLACE INTO "{self.table}" ({col_names}) VALUES ({placeholders})')
        params = [row.get(c) for c in cols]
        _write_execute(self.conn, sql, params)
        return ExecuteResult([])

    def delete(self):
        return DeleteBuilder(self.table, self.conn)

class SQLiteDB:
    def __init__(self, path=None):
        global DB_PATH
        if path:
            DB_PATH = path

    def table(self, name):
        conn = get_conn()
        return TableRef(name, conn)

    def close(self):
        if hasattr(_local, "conn") and _local.conn:
            _local.conn.close()
def _seed_builtin_rules():
    try:
        _local.conn = get_conn()
        existing = _local.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        if existing > 0:
            return
        rules = [
            ("低库存预警", "inventory.changed", '{"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}', "low_stock", "低库存预警: {product_name}", "可用 {avail} < 安全线 {safety}", "warning", 1),
            ("紧急补货", "inventory.changed", '{"left":"inv.available_qty","op":"<=","right":"max(1,inv.safety_qty*0.3)"}', "replenish", "紧急补货: {product_name}", "可用 {avail}，低于安全线 30%", "error", 1),
            ("超卖保护", "order.created", '{"left":"order.quantity","op":">","right":"inv.available_qty"}', "oversell", "超卖告警: {sku}", "订单数量超过可用库存", "error", 1),
            ("滞销识别", "scheduled.daily", '{"left":"inv.days_since_last","op":">","right":"30"}', "slow_moving", "滞销: {product_name}", "{days} 天无销售", "warning", 1),
        ]
        for r in rules:
            _local.conn.execute("INSERT INTO rules(name,event,condition_json,alert_type,alert_title,alert_desc,severity,is_active) VALUES(?,?,?,?,?,?,?,?)", r)
        _local.conn.commit()
    except:
        logging.warning("[db] seed builtin rules failed")
    _local.conn = None

db = SQLiteDB()

def get_db():
    return db

def init_db(path=None):
    """初始化数据库表结构"""
    if _backend() == "tidb":
        # TiDB 表结构由 Makers /migrate/build 管理(23表+31索引), 不在此建
        return
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=15000")
    # 增量自动回收：DELETE 后空间自动归还，避免数据库膨胀（需在创建表前设置）
    try:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    except Exception:
        pass
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL,
            store TEXT DEFAULT '',
            warehouse TEXT DEFAULT '',
            sku TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            unit_price REAL DEFAULT 0,
            total_amount REAL DEFAULT 0,
            data_source TEXT DEFAULT '',
            order_status TEXT DEFAULT '',
            ordered_at TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            supplier TEXT DEFAULT '',
            remark TEXT DEFAULT '',
            parent_order_no TEXT DEFAULT '',
            raw_data TEXT DEFAULT '',
            source TEXT DEFAULT '',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_no_sku ON orders(order_no, sku);
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            product_name TEXT DEFAULT '',
            store TEXT DEFAULT '',
            warehouse TEXT DEFAULT '',
            available_qty INTEGER DEFAULT 0,
            locked_qty INTEGER DEFAULT 0,
            in_transit_qty INTEGER DEFAULT 0,
            safety_qty INTEGER DEFAULT 0,
            safety_days REAL DEFAULT 0,
            warehouse_type TEXT DEFAULT 'platform',
            raw_data TEXT DEFAULT '',
            source TEXT DEFAULT '',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            product_name TEXT DEFAULT '',
            store TEXT DEFAULT '',
            category TEXT DEFAULT '',
            price REAL DEFAULT 0,
            box_qty INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            supplier_code TEXT DEFAULT '',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            barcode TEXT DEFAULT '',
            weight REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            channel TEXT DEFAULT 'jd',
            unit TEXT DEFAULT '',
            UNIQUE(sku, channel)
        );
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_code TEXT UNIQUE NOT NULL,
            supplier_name TEXT DEFAULT '',
            contact_person TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '',
            score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT DEFAULT '',
            title TEXT DEFAULT '',
            description TEXT DEFAULT '',
            severity TEXT DEFAULT 'info',
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT '',
            related_sku TEXT DEFAULT '',
            related_order_no TEXT DEFAULT '',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            event TEXT DEFAULT '',           -- inventory.changed / order.created / scheduled.daily
            condition_json TEXT DEFAULT '{}', -- {"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}
            alert_type TEXT DEFAULT '',
            alert_title TEXT DEFAULT '',
            alert_desc TEXT DEFAULT '',
            severity TEXT DEFAULT 'warning',
            is_active INTEGER DEFAULT 1,
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cleansing_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT DEFAULT '',
            row_index INTEGER DEFAULT 0,
            source_file TEXT DEFAULT '',
            error_type TEXT DEFAULT '',  -- invalid_sku / duplicate_order / missing_field / format_error
            field_name TEXT DEFAULT '',
            raw_value TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            raw_data TEXT DEFAULT '{}',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS quality_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_type TEXT DEFAULT '',
            level TEXT DEFAULT '',
            message TEXT DEFAULT '',
            details TEXT DEFAULT '',
            source TEXT DEFAULT '',
            entity_type TEXT DEFAULT '',
            entity_id TEXT DEFAULT '',
            field_name TEXT DEFAULT '',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_type TEXT DEFAULT '',
            entity_id TEXT DEFAULT '',
            title TEXT DEFAULT '',
            payload TEXT DEFAULT '{}',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sync_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT DEFAULT '',
            task_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            params TEXT DEFAULT '{}',
            result TEXT DEFAULT '',
            channel TEXT DEFAULT 'jd',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cleansing_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            doc_type TEXT DEFAULT 'order',
            mapping TEXT DEFAULT '{}',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS custom_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            key TEXT NOT NULL,
            label TEXT DEFAULT '',
            type TEXT DEFAULT 'string',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, channel TEXT DEFAULT 'jd',
            store TEXT DEFAULT '', sku TEXT DEFAULT '',
            order_status TEXT DEFAULT '',  -- 空=全部，已完成/待发货等
            gmv REAL DEFAULT 0, order_count INTEGER DEFAULT 0,
            quantity INTEGER DEFAULT 0,
            UNIQUE(date, channel, store, sku, order_status)
        );
        CREATE TABLE IF NOT EXISTS daily_sales_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, channel TEXT DEFAULT 'jd',
            sku TEXT NOT NULL, warehouse TEXT DEFAULT '',
            order_count INTEGER DEFAULT 0,
            UNIQUE(date, channel, sku, warehouse)
        );
        CREATE INDEX IF NOT EXISTS idx_orders_order_no ON orders(order_no);
        CREATE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku);
        CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
        CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);

        CREATE TABLE IF NOT EXISTS replenishment_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT DEFAULT '',
            channel TEXT DEFAULT 'jd',
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(key, channel)
        );
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('lead_time_days','10');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('safety_multiplier','1.0');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('max_turnover_days','17');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('turnover_warning_15','15');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('turnover_warning_90','90');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('purchase_lead_days','14');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('purchase_safety_days','3');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('moq','50');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('ship_to_b_days','3');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('b_to_c_days','3');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('season_618','1.5');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('season_1111','1.8');
        INSERT OR IGNORE INTO replenishment_config(key,value) VALUES('season_cny','1.6');
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            store TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            suggested_qty INTEGER DEFAULT 0,
            actual_qty INTEGER DEFAULT 0,
            arrival_date TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            channel TEXT DEFAULT 'jd',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_po_sku_store ON purchase_orders(sku, store);
        CREATE TABLE IF NOT EXISTS inbound_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            supplier TEXT DEFAULT '',
            inbound_date TEXT DEFAULT '',
            channel TEXT DEFAULT 'jd',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS outbound_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT DEFAULT '',
            product_name TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            target_warehouse TEXT DEFAULT '',
            outbound_date TEXT DEFAULT '',
            channel TEXT DEFAULT 'jd',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_quality_logs_level ON quality_logs(level);
        CREATE INDEX IF NOT EXISTS idx_orders_ordered_at ON orders(ordered_at);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(order_status);
        CREATE INDEX IF NOT EXISTS idx_orders_store ON orders(store);
        CREATE INDEX IF NOT EXISTS idx_orders_sku ON orders(sku);
        CREATE INDEX IF NOT EXISTS idx_orders_data_source ON orders(data_source);
        CREATE INDEX IF NOT EXISTS idx_inventory_store ON inventory(store);
        CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replenishment_config_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            old_value TEXT DEFAULT '',
            new_value TEXT DEFAULT '',
            channel TEXT DEFAULT 'jd',
            mode TEXT DEFAULT '',
            operator TEXT DEFAULT 'web',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # 兼容旧表：补加可能缺失的列
    try: conn.execute("ALTER TABLE products ADD COLUMN box_qty INTEGER DEFAULT 1")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE products ADD COLUMN weight REAL DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE products ADD COLUMN volume REAL DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE products ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE rules ADD COLUMN mode TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE rules ADD COLUMN deleted_at TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE orders ADD COLUMN deleted_at TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE products ADD COLUMN unit TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE suppliers ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE orders ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE orders ADD COLUMN paid_at TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE orders ADD COLUMN barcode TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN beginning_stock INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN month_inbound INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN month_outbound INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN turnover_days REAL DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN c_transit INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN weight REAL DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN volume REAL DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN barcode TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE alerts ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN warehouse_type TEXT DEFAULT 'platform'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inventory ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE replenishment_config ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE daily_stats ADD COLUMN order_status TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    # 修复表约束：UNIQUE(key) → UNIQUE(key, channel)
    try:
        conn.execute("SELECT 1 FROM replenishment_config WHERE 1=0")  # 表存在则继续
        info = conn.execute("PRAGMA index_list('replenishment_config')").fetchall()
        has_composite = any('key_channel' in (r[1] or '') for r in info)
        if not has_composite:
            conn.execute("""
                CREATE TABLE replenishment_config_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT DEFAULT '',
                    channel TEXT DEFAULT 'jd',
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(key, channel)
                )
            """)
            conn.execute("INSERT OR IGNORE INTO replenishment_config_new (id,key,value,channel,updated_at) SELECT id,key,value,COALESCE(channel,'jd'),updated_at FROM replenishment_config")
            conn.execute("DROP TABLE replenishment_config")
            conn.execute("ALTER TABLE replenishment_config_new RENAME TO replenishment_config")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE rules ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE purchase_orders ADD COLUMN actual_qty INTEGER DEFAULT 0")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE purchase_orders ADD COLUMN arrival_date TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE purchase_orders ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE inbound_records ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE outbound_records ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE sync_tasks ADD COLUMN task_id TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE daily_sales_snapshot ADD COLUMN warehouse TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE products ADD COLUMN supplier_code TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass
    try: conn.execute("ALTER TABLE sync_tasks ADD COLUMN channel TEXT DEFAULT 'jd'")
    except sqlite3.OperationalError: pass
    # ── P0 性能索引 ──
    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_orders_sku_ordered_at ON orders(sku, ordered_at, channel)",
        "CREATE INDEX IF NOT EXISTS idx_inventory_sku_wh_ch ON inventory(sku, warehouse_type, channel)",
        "CREATE INDEX IF NOT EXISTS idx_inventory_wh_ch ON inventory(warehouse_type, channel)",
        "CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date, channel)",
        "CREATE INDEX IF NOT EXISTS idx_products_sku_ch ON products(sku, channel)",
        "CREATE INDEX IF NOT EXISTS idx_orders_ch_status ON orders(channel, order_status)",
        "CREATE INDEX IF NOT EXISTS idx_orders_ch_ordered_at ON orders(channel, order_status, ordered_at)",
        "CREATE INDEX IF NOT EXISTS idx_orders_cdate ON orders(channel, substr(ordered_at,1,10), order_status)",
        "CREATE INDEX IF NOT EXISTS idx_inbound_date ON inbound_records(inbound_date)",
        "CREATE INDEX IF NOT EXISTS idx_outbound_date ON outbound_records(outbound_date)",
        "CREATE INDEX IF NOT EXISTS idx_snapshot_date ON daily_sales_snapshot(date, channel, sku)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_sku_wh_uq ON inventory(sku, warehouse, channel)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_inbound_sku_date ON inbound_records(sku, inbound_date)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_outbound_sku_date ON outbound_records(sku, outbound_date)",
    ]:
        try: conn.execute(idx)
        except sqlite3.OperationalError: pass
    conn.commit()
    # ── schema 版本检查 ──
    try:
        ver = conn.execute("SELECT value FROM _schema_version LIMIT 1").fetchone()
        ver = int(ver[0]) if ver else 0
    except:
        conn.execute("CREATE TABLE IF NOT EXISTS _schema_version (key TEXT PRIMARY KEY, value TEXT)")
        ver = 0
    # 版本化迁移：按顺序执行未完成版本
    if ver < SCHEMA_VERSION:
        import logging
        for v in range(ver + 1, SCHEMA_VERSION + 1):
            if v in _MIGRATIONS:
                try:
                    _MIGRATIONS[v](conn)
                    conn.commit()
                    logging.info(f"[DB] Migration {v} applied")
                except Exception as e:
                    logging.warning(f"[DB] Migration {v} failed: {e}")
                    raise
        conn.execute("INSERT OR REPLACE INTO _schema_version(key,value) VALUES('version',?)", (str(SCHEMA_VERSION),))
        conn.commit()
        logging.info(f"[DB] Schema migrated: {ver} → {SCHEMA_VERSION}")
    # 自愈前先确保 products 为 (sku, channel) 复合唯一（旧库是 sku 单列 UNIQUE，两渠道互斥）
    try:
        _ensure_products_composite_unique(conn)
    except Exception as e:
        logging.warning(f"[DB] ensure products composite unique: {e}")
    # 自愈：跨渠道共享 SKU 在 products 表缺行时补齐（历史版本 seed 共享 SKU 被 upsert 覆盖）
    try:
        _heal_shared_products(conn)
    except Exception as e:
        logging.warning(f"[DB] heal shared products: {e}")
    conn.close()

def _ensure_products_composite_unique(conn):
    """幂等：将 products 的 sku 唯一约束升级为 (sku, channel) 复合唯一。
    旧表（sku TEXT UNIQUE）两渠道无法共存同 SKU → 重建表。返回 True 表示执行了重建。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='products'").fetchone()
    if not row:
        return False
    sql = row[0] or ''
    if 'UNIQUE(sku, channel)' in sql or 'UNIQUE (sku, channel)' in sql:
        return False
    conn.execute("""
        CREATE TABLE products_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            product_name TEXT DEFAULT '',
            store TEXT DEFAULT '',
            category TEXT DEFAULT '',
            price REAL DEFAULT 0,
            box_qty INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            supplier_code TEXT DEFAULT '',
            owner_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            barcode TEXT DEFAULT '',
            weight REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            channel TEXT DEFAULT 'jd',
            unit TEXT DEFAULT '',
            UNIQUE(sku, channel)
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO products_new
            (sku, product_name, store, category, price, box_qty, status, supplier_code, owner_id, created_at, updated_at, barcode, weight, volume, channel, unit)
        SELECT sku, product_name, store, category, price, box_qty, status, supplier_code, owner_id, created_at, updated_at,
               COALESCE(barcode,''), COALESCE(weight,0), COALESCE(volume,0), COALESCE(channel,'jd'), COALESCE(unit,'')
        FROM products
    """)
    conn.execute("DROP TABLE products")
    conn.execute("ALTER TABLE products_new RENAME TO products")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_sku_ch ON products(sku, channel)")
    conn.commit()
    return True

def _heal_shared_products(conn):
    """幂等自愈：products 表中跨渠道同 SKU 缺行时补齐（从已有渠道行复制 + supplier 渠道后缀替换）。

    背景：历史 seed 的 200 个共享 SKU 共用 jd 的 '-J' 字符串，products.sku 单一 UNIQUE +
    upsert(INSERT OR REPLACE) 导致两渠道互相覆盖，只剩后写入渠道(channel)一行
    → jd 渠道搜不到自己商品、sku_to_channel 推断错误。此函数检测 inventory 双渠道都存在
    但 products 只有单渠道行的 SKU，补齐缺失渠道行（幂等，不修改已有行）。
    """
    try:
        # jd 缺行 ← 从 other 行复制（supplier_code 后缀 -OTHER → -JD）
        conn.execute("""
            INSERT OR IGNORE INTO products
                (sku, product_name, store, category, price, box_qty, barcode, weight, volume, unit, status, supplier_code, channel)
            SELECT p.sku, p.product_name, p.store, p.category, p.price, p.box_qty, p.barcode, p.weight, p.volume, p.unit, p.status,
                   REPLACE(p.supplier_code, '-OTHER', '-JD'), 'jd'
            FROM products p
            WHERE p.channel = 'other'
              AND EXISTS (SELECT 1 FROM inventory i WHERE i.sku = p.sku AND i.channel = 'jd')
              AND NOT EXISTS (SELECT 1 FROM products p2 WHERE p2.sku = p.sku AND p2.channel = 'jd')
        """)
        # other 缺行 ← 从 jd 行复制（supplier_code 后缀 -JD → -OTHER）
        conn.execute("""
            INSERT OR IGNORE INTO products
                (sku, product_name, store, category, price, box_qty, barcode, weight, volume, unit, status, supplier_code, channel)
            SELECT p.sku, p.product_name, p.store, p.category, p.price, p.box_qty, p.barcode, p.weight, p.volume, p.unit, p.status,
                   REPLACE(p.supplier_code, '-JD', '-OTHER'), 'other'
            FROM products p
            WHERE p.channel = 'jd'
              AND EXISTS (SELECT 1 FROM inventory i WHERE i.sku = p.sku AND i.channel = 'other')
              AND NOT EXISTS (SELECT 1 FROM products p2 WHERE p2.sku = p.sku AND p2.channel = 'other')
        """)
        conn.commit()
    except Exception as e:
        import logging
        logging.warning(f"[DB] heal shared products: {e}")
def _seed_builtin_rules():
    try:
        _local.conn = get_conn()
        existing = _local.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
        if existing > 0:
            return
        rules = [
            ("低库存预警", "inventory.changed", '{"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}', "low_stock", "低库存预警: {product_name}", "可用 {avail} < 安全线 {safety}", "warning", 1),
            ("紧急补货", "inventory.changed", '{"left":"inv.available_qty","op":"<=","right":"max(1,inv.safety_qty*0.3)"}', "replenish", "紧急补货: {product_name}", "可用 {avail}，低于安全线 30%", "error", 1),
            ("超卖保护", "order.created", '{"left":"order.quantity","op":">","right":"inv.available_qty"}', "oversell", "超卖告警: {sku}", "订单数量超过可用库存", "error", 1),
            ("滞销识别", "scheduled.daily", '{"left":"inv.days_since_last","op":">","right":"30"}', "slow_moving", "滞销: {product_name}", "{days} 天无销售", "warning", 1),
        ]
        for ch in ['jd','other']:
            for r in rules:
                _local.conn.execute("INSERT INTO rules(name,event,condition_json,alert_type,alert_title,alert_desc,severity,is_active,channel) VALUES(?,?,?,?,?,?,?,?,?)", (*r, ch))
        _local.conn.commit()
    except Exception as e:
        logging.warning(f"[db] seed builtin rules for channels: {e}")


    # 播种内置规则
    try:
        _seed_builtin_rules()
    except Exception as e:
        pass
