"""Health check endpoint for monitoring"""
from fastapi import APIRouter, Request
from datetime import datetime, timezone, timedelta
import os, sqlite3

router = APIRouter(tags=["health"])


def _health_tidb():
    """TiDB 后端精简健康检查(SQLite 检查项无意义)"""
    from datetime import datetime, timezone
    from app.core.database import get_conn
    checks = {"db_backend": "tidb", "fragmentation": "n/a", "quota_auto_checkpoint": "skip"}
    _c = None
    try:
        _c = get_conn()
        _r = _c.execute("SELECT 1 AS ok").fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = "error: %s" % str(e)[:100]
    if _c is not None:
        try:
            _row = _c.execute("SELECT COALESCE(MAX(date),'') AS m FROM daily_sales_snapshot").fetchone()
            _sn = _row['m'] if _row and isinstance(_row, dict) else (_row[0] if _row else '')
            checks["snapshot_max"] = _sn or ''
        except Exception:
            checks["snapshot_max"] = ''
        try:
            _v = _c.execute("SELECT value FROM replenishment_config WHERE `key`='_cache_version'").fetchone()
            checks["cache_version"] = (_v.get('value') if _v and isinstance(_v, dict) else (_v[0] if _v else ''))
        except Exception:
            checks["cache_version"] = ''
    status = "ok" if checks.get("db") == "ok" else "degraded"
    return {"status": status, "timestamp": datetime.now(timezone.utc).isoformat(), "checks": checks}


@router.post("/api/backup")
def trigger_backup(request: Request):
    """手动触发数据库备份（验证备份机制 + 应急数据保护）"""
    from app.core.response import ok, fail
    from app.core.database import backup_db
    import logging
    # 简单鉴权：需要有效 token
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not token:
        return fail("未登录", status=401)
    try:
        from app.core.auth import verify_token
        if not verify_token(token):
            return fail("登录已失效", status=401)
    except Exception:
        return fail("鉴权失败", status=401)
    path = backup_db()
    if path and os.path.exists(path) and os.path.getsize(path) > 1024:
        # 进程内验证：解压 + 打开 SQLite 确认可恢复（不受 PA 下载通道 10MB 限制影响）
        try:
            import gzip, sqlite3
            _sz = os.path.getsize(path)
            with gzip.open(path, 'rb') as f:
                _raw = f.read(200 * 1024 * 1024)  # 最多读 200MB
            _verify_db = path + ".verify"
            with open(_verify_db, 'wb') as f:
                f.write(_raw)
            _c = sqlite3.connect(_verify_db)
            _orders = _c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            _products = _c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            _users = _c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            _c.close()
            try: os.remove(_verify_db)
            except Exception: pass
            logging.info(f"[backup] verify OK: orders={_orders} products={_products} users={_users}")
            return ok({"path": path, "size": _sz, "verify": {"orders": _orders, "products": _products, "users": _users}})
        except Exception as e:
            logging.error(f"[backup] verify failed: {e}")
            return fail(f"备份文件已验证失败: {e}", status=500)

@router.get("/api/vacuum")
def run_vacuum():
    """后台执行 VACUUM 压缩数据库"""
    from app.core.database import submit_task, DB_PATH
    import os, logging
    def _do_vacuum():
        import sqlite3, os, time, shutil
        _tmp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp', 'vacuumed.db')
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA busy_timeout=120000")
        try:
            conn.execute("VACUUM INTO ?", (_tmp,))
            conn.close()
            if os.path.exists(_tmp) and os.path.getsize(_tmp) > 1024:
                os.replace(_tmp, DB_PATH)
                import logging; logging.info(f"[vacuum] VACUUM INTO done")
        except Exception as e:
            import logging; logging.warning(f"[vacuum] VACUUM INTO: {e}")
            conn.close()
            if os.path.exists(_tmp): os.remove(_tmp)
        sz = os.path.getsize(DB_PATH) / 1024 / 1024
        logging.info(f"[vacuum] done: {sz:.0f}MB")
        return {"size_mb": round(sz, 1)}
    submit_task("vacuum", _do_vacuum)
    return {"ok": True, "message": "VACUUM 已在后台执行"}

@router.get("/api/vacuum/status")
def vacuum_status():
    from app.core.database import get_task
    t = get_task("vacuum")
    if not t: return {"ok": False, "status": "not_found"}
    return {"ok": True, "data": t}

@router.get("/api/health")
def health():
    """系统健康检查 + 缓存版本号"""
    from app.core.database import _backend
    if _backend() == "tidb":
        return _health_tidb()
    status = "ok"
    checks = {}
    
    # 数据库大小监控 + 自动 VACUUM（超过阈值后台执行）
    try:
        from app.core.db_maintenance import get_db_size_mb, VACUUM_THRESHOLD_MB
        _sz = get_db_size_mb()
        checks["db_size_mb"] = round(_sz, 1)
        # 配额监控(9-2 事故沉淀): PA 512MB 硬配额, db+wal+备份 接近上限会导致 SQLite 写失败→malformed
        # 提前预警(>=80% degraded, >=90% degraded), 治本防再次写满
        try:
            import glob as _gq, os as _oq
            from app.core.database import DB_PATH as _DBQ
            _pq = _sz
            for _x in ([_DBQ + '-wal'] + sorted(_gq.glob(_DBQ + '.bak.*.gz'), key=_oq.path.getmtime, reverse=True)[:2]):
                if _oq.path.exists(_x): _pq += _oq.path.getsize(_x) / 1024 / 1024
            _QUOTA_MB = 512
            checks["db_quota_used_mb"] = round(_pq, 1)
            checks["db_quota_pct"] = round(_pq / _QUOTA_MB * 100, 0)
            # 碎片监控(只读 PRAGMA, 零阻塞): freelist 页>阈值提示手动 VACUUM——不做定时回收
            # 定时常 VACUUM 会独占写锁数分钟阻塞所有请求(8-28 教训), 改为健康检查提示 + 手动一次性执行
            try:
                import sqlite3 as _sqlf
                _cf = _sqlf.connect(_DBQ)
                _cf.execute("PRAGMA busy_timeout=5000")
                _pc = _cf.execute("PRAGMA page_count").fetchone()[0]
                _fl = _cf.execute("PRAGMA freelist_count").fetchone()[0]
                _cf.close()
                checks["db_pages"] = _pc
                checks["db_freelist_pages"] = _fl
                _frag_mb = round(_fl * _cf.page_size / 1024 / 1024, 1) if False else round(_fl * 4096 / 1024 / 1024, 1)
                checks["db_freelist_mb"] = _frag_mb
                if _fl > 2000:
                    checks["fragmentation"] = f"warning: 碎片 {_frag_mb}MB({_fl}页), 建议手动 VACUUM 回收(一次性, 需避峰值)"
                else:
                    checks["fragmentation"] = "ok"
            except Exception:
                checks["fragmentation"] = "n/a"""
            # WAL 按需 checkpoint: 仅当 WAL 实际大小 >15MB 才触发(阈值防事故, 但避免高频TRUNCATE阻塞写)
            # 3次事故: 6h间隔内WAL可暴涨89MB撑满配额; 但health高频调用时小WAL TRUNCATE也浪费+可能锁竞争
            try:
                _wal_path = _DBQ + '-wal'
                if _oq.path.exists(_wal_path) and _oq.path.getsize(_wal_path) / 1024 / 1024 > 15:
                    import sqlite3 as _sqlq
                    import threading as _thq
                    # 与 scheduler 的定时 checkpoint 互斥(防并发 TRUNCATE 互相等待超时)
                    from app.core.scheduler import _checkpoint_lock
                    if _checkpoint_lock.acquire(blocking=False):
                        try:
                            _cq = _sqlq.connect(_DBQ, timeout=20)
                            _cq.execute("PRAGMA busy_timeout=20000")
                            _cq.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                            _cq.close()
                            checks["quota_auto_checkpoint"] = "done"
                        finally:
                            _checkpoint_lock.release()
                else:
                    checks["quota_auto_checkpoint"] = "skip"
            except Exception:
                checks["quota_auto_checkpoint"] = "err"
            if _pq > _QUOTA_MB * 0.8:
                if _pq > _QUOTA_MB * 0.9:
                    checks["quota"] = f"danger: db+wal+备份 {_pq:.0f}MB ≥ {int(_QUOTA_MB*0.9)}MB(90%), 已自动checkpoint, 接近上限请归档"
                    status = "degraded"
                else:
                    checks["quota"] = f"warning: db+wal+备份 {_pq:.0f}MB ≥ {int(_QUOTA_MB*0.8)}MB(80%), 已自动checkpoint防膨胀"
                    status = "degraded"
            else:
                checks["quota"] = "ok"
        except Exception:
            pass
        # 临时诊断：orders 索引
        try:
            from app.core.database import get_conn
            _c2 = get_conn()
            checks["orders_idx"] = [r[0] for r in _c2.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='orders'").fetchall()]
        except Exception as _e: checks["orders_idx"] = str(_e)[:80]
        if _sz >= VACUUM_THRESHOLD_MB:
            from app.core.database import submit_task, get_task
            _tv = get_task("health_vacuum")
            if not _tv or _tv.get('status') in ('done', 'error', 'not_found'):
                submit_task("health_vacuum", lambda: __import__('app.core.db_maintenance', fromlist=['vacuum_database']).vacuum_database())
    except: pass
    
    # 数据库检查（含完整性快速检测 + 运行中自动修复）
    try:
        db_path = os.getenv("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "..", "supplykit.db"))
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("SELECT 1")
        checks["database"] = "ok"
        # 轻量完整性检测（quick_check 比 integrity_check 快，用于早期发现损坏）
        try:
            qc = conn.execute("PRAGMA quick_check").fetchone()
            if qc and qc[0] == 'ok':
                checks["integrity"] = "ok"
            else:
                checks["integrity"] = f"error: {qc}（尝试后台修复中）"
                status = "degraded"
                # 后台异步修复（不影响健康检查响应）
                def _repair():
                    try:
                        _c2 = sqlite3.connect(db_path)
                        _c2.execute("PRAGMA busy_timeout=30000")
                        _c2.execute("VACUUM")
                        _qc2 = _c2.execute("PRAGMA quick_check").fetchone()
                        _c2.close()
                        if _qc2 and _qc2[0] == 'ok':
                            import logging; logging.info("[db] 健康检查触发 VACUUM 修复成功")
                        else:
                            raise Exception("VACUUM 后仍损坏")
                    except Exception as _ve:
                        import logging; logging.warning(f"[db] VACUUM 修复失败，尝试备份恢复: {_ve}")
                        import glob, shutil, os
                        baks = sorted(glob.glob(db_path + ".bak.*"), key=os.path.getmtime, reverse=True)
                        for b in baks:
                            try:
                                shutil.copy2(b, db_path)
                                logging.info(f"[db] 从备份恢复成功: {b}")
                                break
                            except Exception:
                                pass
                import threading
                threading.Thread(target=_repair, daemon=True).start()
        except Exception as e:
            checks["integrity"] = f"error: {e}"
        # 检查 WAL 文件是否异常膨胀（>200MB 提示）
        try:
            wal = db_path + "-wal"
            if os.path.exists(wal):
                wal_mb = os.path.getsize(wal) / 1024 / 1024
                checks["wal_mb"] = round(wal_mb, 1)
                if wal_mb > 200:
                    checks["wal"] = f"warning: {wal_mb:.0f}MB"
                    status = "degraded"
                else:
                    checks["wal"] = "ok"
        except Exception:
            pass
        conn.close()
    except Exception as e:
        checks["database"] = f"error: {e}"
        status = "degraded"
    
    # 磁盘空间
    try:
        stat = os.statvfs("/")
        free_gb = stat.f_bavail * stat.f_frsize / 1024 / 1024 / 1024
        checks["disk_free_gb"] = round(free_gb, 1)
        if free_gb < 0.5:
            checks["disk"] = "warning: low disk space"
            status = "degraded"
        else:
            checks["disk"] = "ok"
    except Exception as e:
        import logging; logging.warning(f"[health] disk check error: {e}")
        checks["disk"] = "unknown"

    # 备份状态（最近一次备份结果 + 备份文件时间）
    try:
        import glob as _glob
        from app.core.database import DB_PATH as _BDB
        _baks = sorted(_glob.glob(_BDB + ".bak.*.gz"), key=os.path.getmtime, reverse=True)
        if _baks:
            checks["last_backup"] = os.path.basename(_baks[0])
            import datetime as _dt
            checks["last_backup_time"] = _dt.datetime.fromtimestamp(os.path.getmtime(_baks[0])).strftime('%Y-%m-%d %H:%M')
        else:
            checks["last_backup"] = "none"
        # 最近备份是否失败（查 quality_logs）
        try:
            from app.core.database import get_conn
            _c3 = get_conn()
            _row = _c3.execute("SELECT message, created_at FROM quality_logs WHERE log_type='backup' ORDER BY id DESC LIMIT 1").fetchone()
            if _row:
                checks["last_backup_result"] = f"{_row[0]} @ {_row[1]}"
                if '失败' in _row[0]:
                    checks["backup"] = "warning"
                    if status == "ok": status = "degraded"
        except Exception:
            pass
    except Exception as e:
        checks["last_backup"] = f"error: {e}"
    
    # 缓存版本号（用于前端轮询）
    version = 0
    try:
        from app.core.database import get_db
        db = get_db()
        ver = db.table("replenishment_config").select("*").eq("key", "_cache_version").execute().data
        version = int(ver[0]["value"]) if ver else 0
    except Exception:
        pass
    
    # 调度器与快照新鲜度(自愈监控: 快照陈旧→self-heal 可感知并重建)
    try:
        from app.core.database import get_conn
        _sn = get_conn().execute("SELECT COALESCE(MAX(date),'') FROM daily_sales_snapshot").fetchone()[0]
        _ordmax = get_conn().execute("SELECT COALESCE(MAX(substr(ordered_at,1,10)),'') FROM orders WHERE deleted_at=''").fetchone()[0]
        checks["snapshot_max"] = _sn
        checks["orders_max"] = _ordmax
        # 快照新鲜度 = 对比订单最新日前一天(快照只到昨天, 今天订单走实时补足——曾误报陈旧致degraded)
        _expect = _ordmax[:10]
        from datetime import timedelta as _td2
        try:
            _y = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%d')
            _expect_min = _y  # 快照至少应覆盖到昨天
        except Exception:
            _expect_min = _ordmax[:10]
        checks["snapshot_stale"] = (not _sn) or (bool(_ordmax) and _sn < _expect_min)
        if checks["snapshot_stale"]:
            checks["snapshot"] = "warning: 日销快照陈旧, 需重建"
            status = "degraded"
        else:
            checks["snapshot"] = "ok"
        try:
            from app.core.scheduler import get_status
            checks["scheduler"] = get_status()
        except Exception:
            pass
    except Exception as e:
        checks["snapshot"] = f"error: {e}"

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "version": version,
    }


@router.get("/api/diag-orders")
def diag_orders(action: str = ''):
    # 运维操作: rebuild_rules=重建规则引擎告警, rule_stat=规则匹配统计
    if action == 'restore_db':
        # 灾难恢复: 从最近备份解压覆盖主库(修复 database disk image is malformed)
        # 可安全重复执行: 每次先写 .new 校验 integrity 再原子替换, 坏库备份为 .corrupt.<ts>
        try:
            import gzip, shutil, glob as _glob
            from app.core.database import DB_PATH, get_conn
            import sqlite3 as _s
            _backup_base = DB_PATH + '.bak.'
            _baks = sorted(_glob.glob(_backup_base + '*.gz'), key=os.path.getmtime, reverse=True)
            if not _baks:
                return {"ok": False, "error": "未找到备份文件"}
            _src = _baks[0]
            _new = DB_PATH + '.restore.new'
            with gzip.open(_src, 'rb') as _f_in, open(_new, 'wb') as _f_out:
                shutil.copyfileobj(_f_in, _f_out)
            # 校验新库完整性
            _c = _s.connect(_new); _qc = _c.execute("PRAGMA integrity_check").fetchone(); _c.close()
            if not _qc or _qc[0] != 'ok':
                if os.path.exists(_new): os.remove(_new)
                return {"ok": False, "error": f"备份 {os.path.basename(_src)} 校验失败: {_qc}"}
            # 原子替换: 坏库保留为 .corrupt.<ts>, 移除可能冲突的 WAL/SHM
            for _ext in ('.corrupt.' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'),):
                pass
            _corrupt = DB_PATH + '.corrupt.' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
            try:
                _g = get_conn(); _g.close()
            except Exception: pass
            for _f in (DB_PATH, DB_PATH + '-wal', DB_PATH + '-shm'):
                if os.path.exists(_f):
                    if _f == DB_PATH: os.rename(_f, _corrupt)
                    else: os.remove(_f)
            os.rename(_new, DB_PATH)
            return {"ok": True, "restored_from": os.path.basename(_src), "corrupt_backup": os.path.basename(_corrupt), "size": os.path.getsize(DB_PATH)}
        except Exception as e:
            import traceback
            return {"ok": False, "error": str(e)[:200], "tb": traceback.format_exc()[-300:]}
    if action == 'rebuild_snapshot':
        try:
            from app.core.database import get_db
            from app.core.sales_utils import build_daily_sales_snapshot
            _n = build_daily_sales_snapshot(get_db())
            return {"ok": True, "action": "rebuild_snapshot", "result": f"日销快照已重建, 聚合行数={_n}"}
        except Exception as e:
            import traceback
            return {"ok": False, "action": "rebuild_snapshot", "error": str(e)[:200], "tb": traceback.format_exc()[-500:]}
    if action == 'rebuild_rules':
        try:
            from app.api.routes.seed import _seed_rules
            from app.core.database import get_db, get_conn
            from datetime import datetime, timezone
            _seed_rules(get_db(), {'jd': [], 'other': []})
            # 递增全部相关版本号(alerts 列表缓存 + 看板 summary 缓存失效):
            #   _rules_version/_replen_version → alerts 缓存
            #   _cache_version               → dashboard summary(active_alerts 是全量 COUNT, 必须失效)
            _c = get_conn()
            for _k in ['_rules_version', '_replen_version', '_cache_version']:
                _v = _c.execute("SELECT value FROM replenishment_config WHERE key=?", (_k,)).fetchone()
                _nv = str(int(_v[0]) + 1) if _v and _v[0] else '1'
                _c.execute("INSERT OR REPLACE INTO replenishment_config(key,value,channel,updated_at) VALUES(?,?,?,?)",
                           (_k, _nv, 'jd', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')))
            _c.commit()
            # 返回生成统计
            _cnt = _c.execute("SELECT COUNT(*) FROM alerts WHERE source='rules_engine' AND status='active'").fetchone()[0]
            return {"ok": True, "action": "rebuild_rules", "result": f"规则引擎告警已重建, active rules_engine={_cnt}"}
        except Exception as e:
            return {"ok": False, "action": "rebuild_rules", "error": str(e)[:200]}
    if action == 'rule_stat':
        try:
            from app.core.database import get_conn
            _c = get_conn()
            res = {}
            for ch in ['jd', 'other']:
                n = _c.execute("SELECT COUNT(*) FROM (SELECT sku FROM inventory WHERE channel=? GROUP BY sku HAVING SUM(available_qty) < SUM(safety_qty))", (ch,)).fetchone()[0]
                n2 = _c.execute("SELECT COUNT(*) FROM (SELECT sku FROM inventory WHERE channel=? GROUP BY sku HAVING SUM(available_qty) < SUM(safety_qty) OR (SUM(available_qty) <= MAX(1, SUM(safety_qty)*0.3) AND SUM(available_qty)+SUM(in_transit_qty) <= SUM(safety_qty)))", (ch,)).fetchone()[0]
                res[ch] = {'avail_lt_safety': n, 'rules_match': n2}
            return {"ok": True, "action": "rule_stat", "data": res}
        except Exception as e:
            return {"ok": False, "action": "rule_stat", "error": str(e)[:200]}
    """诊断: orders 表真实状态（不过滤软删）"""
    import sqlite3
    from app.core.database import DB_PATH
    db_path = DB_PATH
    try:
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        orders_exists = 'orders' in tables
        if not orders_exists:
            return {"orders_exists": False, "tables": tables, "db_path": db_path}
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM orders WHERE (deleted_at IS NULL OR deleted_at='')").fetchone()[0]
        soft_del = conn.execute("SELECT COUNT(*) FROM orders WHERE deleted_at IS NOT NULL AND deleted_at != ''").fetchone()[0]
        mmin = conn.execute("SELECT MIN(ordered_at) FROM orders").fetchone()[0]
        mmax = conn.execute("SELECT MAX(ordered_at) FROM orders").fetchone()[0]
        daily_stats = conn.execute("SELECT COUNT(*), COALESCE(SUM(order_count),0) FROM daily_stats").fetchone()
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='orders'").fetchone()
        max_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM orders").fetchone()[0]
        inv_cnt = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
        prod_cnt = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        last_arc = conn.execute("SELECT value FROM replenishment_config WHERE key='_last_archive_check'").fetchone()
        replen_ver = conn.execute("SELECT value FROM replenishment_config WHERE key='_replen_version'").fetchone()
        wt_dist = [{"channel": r[0], "warehouse_type": r[1], "warehouse": r[2], "c": r[3]} for r in conn.execute("SELECT channel, warehouse_type, warehouse, COUNT(*) c FROM inventory GROUP BY channel, warehouse_type, warehouse ORDER BY channel, warehouse_type").fetchall()]
        # 快照与订单状态诊断(conn 关闭前查询)
        _snap = _diag_snapshot(conn)
        _ob = {r[0]: r[1] for r in conn.execute("SELECT order_status, COUNT(*) FROM orders GROUP BY order_status").fetchall()}
        _o30 = conn.execute("SELECT COUNT(*) FROM orders WHERE substr(ordered_at,1,10) >= date('now','-30 day')").fetchone()[0]
        conn.close()
        return {"total": total, "active": active, "soft_del": soft_del, "min_date": mmin, "max_date": mmax,
                "daily_stats_rows": daily_stats[0], "daily_stats_orders": daily_stats[1],
                "sqlite_seq": seq[0] if seq else 0, "orders_max_id": max_id,
                "inventory_cnt": inv_cnt, "products_cnt": prod_cnt,
                "last_archive_check": last_arc[0] if last_arc else None,
                "replen_version": replen_ver[0] if replen_ver else None,
                "warehouse_type_dist": wt_dist,
                "sales_snapshot": _snap, "orders_by_status": _ob, "orders_last30d": _o30}
    except Exception as e:
        return {"error": str(e)}

def _diag_snapshot(conn):
    try:
        r = conn.execute("SELECT COUNT(*), COALESCE(MAX(date),''), COALESCE(MIN(date),'') FROM daily_sales_snapshot").fetchone()
        r2 = conn.execute("SELECT COUNT(*) FROM daily_sales_snapshot WHERE date >= date('now','-28 day')").fetchone()
        return {"rows": r[0], "max": r[1], "min": r[2], "last28d": r2[0]}
    except Exception as e:
        return {"error": str(e)[:100]}

@router.get("/api/health/last-errors")
def last_errors(limit: int = 15):
    """免登录读最近全局异常(quality_logs exception——定位500/登录失败堆栈)"""
    import sqlite3, os
    from app.core.database import DB_PATH
    try:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        rows = _conn.execute("SELECT created_at, log_type, message, details FROM quality_logs WHERE log_type IN ('exception','error') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        _conn.close()
        return {"ok": True, "errors": [dict(r) for r in rows]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
