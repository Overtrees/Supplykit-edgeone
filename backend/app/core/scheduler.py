"""APScheduler 定时任务"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler(daemon=True)
_started = False

def _task_inventory_sync():
    """每 30 分钟同步库存"""
    try:
        from app.core.database import get_db
        from app.api.routes.insights import auto_adjust_inventory
        db = get_db()
        orders = [o for o in db.table("orders").select("*").order("id", desc=True).limit(100).execute().data if not (o.get("deleted_at") or "")]
        count = 0
        for o in (orders or []):
            try:
                auto_adjust_inventory(o, 'cleansing', db)
                count += 1
            except Exception as e:
                logger.warning(f"Inventory sync error for order {o.get('id')}: {e}")
        logger.info(f"Inventory sync: {count}/{len(orders or [])}")
    except Exception as e:
        logger.error(f"Inventory sync error: {e}")

def _task_build_sales_snapshot():
    """每天凌晨 3:30 构建日销快照"""
    try:
        from app.core.database import get_db
        from app.core.sales_utils import build_daily_sales_snapshot
        db = get_db()
        count = build_daily_sales_snapshot(db)
        logger.info(f"Sales snapshot: {count} rows")
    except Exception as e:
        logger.info(f"Sales snapshot error: {e}")


def _task_snapshot_freshness():
    """快照新鲜度守护(治理项③): 每小时检查, 快照陈旧(>2天)自动重建——不依赖 03:30 CronTrigger
    (PA 上 CronTrigger 曾长期不触发致快照停 7/9; IntervalTrigger 经验证可靠: inventory_sync/checkpoint 均正常)"""
    try:
        from app.core.database import get_conn, get_db
        from app.core.sales_utils import build_daily_sales_snapshot
        from datetime import datetime, timedelta, timezone
        _sn = get_conn().execute("SELECT COALESCE(MAX(date),'') FROM daily_sales_snapshot").fetchone()[0]
        _stale = (not _sn) or _sn < (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%d')
        if _stale:
            logger.warning(f"[scheduler] freshness: 日销快照陈旧(max={_sn}), 自动重建")
            _n = build_daily_sales_snapshot(get_db())
            logger.info(f"[scheduler] freshness: 快照已重建({_n}行)")
    except Exception as e:
        logger.warning(f"[scheduler] freshness error: {e}")

def _task_archive_orders():
    """每天凌晨 1 点归档 90 天前的订单（与看板/滞销 90 天窗口一致，避免缺口）"""
    try:
        from app.core.database import get_db, get_conn
        from datetime import timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
        db = get_db()
        # 用 SQL 只取超期订单（避免全表加载）
        conn = get_conn()
        old_orders = [dict(r) for r in conn.execute("SELECT * FROM orders WHERE substr(ordered_at,1,10) < ? AND (deleted_at='')", (cutoff,)).fetchall()]
        if not old_orders:
            logger.info(f"Order archive: no orders before {cutoff}")
            return
        # 按天+渠道+店铺+SKU+状态 聚合
        from collections import defaultdict
        agg = defaultdict(lambda: {'gmv': 0, 'count': 0, 'qty': 0})
        for o in old_orders:
            key = (str(o.get('ordered_at',''))[:10], o.get('channel','jd'), o.get('store',''), o.get('sku',''), o.get('order_status','')[:10])
            agg[key]['gmv'] += float(o.get('total_amount') or 0)
            agg[key]['count'] += 1
            agg[key]['qty'] += int(o.get('quantity') or 0)
        # 写入 daily_stats（统计成功/失败）
        conn = get_conn()
        _ok = 0; _fail = 0
        for (date, channel, store, sku, order_status), v in agg.items():
            try:
                conn.execute(
                    "INSERT INTO daily_stats (date, channel, store, sku, order_status, gmv, order_count, quantity) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(date, channel, store, sku, order_status) DO UPDATE SET gmv=gmv+?, order_count=order_count+?, quantity=quantity+?",
                    (date, channel, store, sku, order_status, v['gmv'], v['count'], v['qty'], v['gmv'], v['count'], v['qty'])
                )
                _ok += 1
            except Exception as e:
                _fail += 1
                logger.warning(f"[archive] daily_stats insert fail: {e}")
        conn.commit()
        # 保护: 只要有任何 daily_stats 写入失败 → 不删除 orders(数据不丢, 下次归档重试)
        if _fail > 0:
            logger.error(f"[archive] ABORT: daily_stats 写入失败 {_fail}/{len(agg)}, 不删除 orders(防数据丢失)")
            try:
                _c2 = get_conn()
                _c2.execute("INSERT INTO quality_logs(log_type, level, message, source) VALUES('archive','error',?, 'scheduler')", (f"归档ABORT: daily_stats写入失败{_fail}/{len(agg)}, 未删除orders",))
                _c2.commit()
            except Exception: pass
            conn.close()
            return
        # 全部写入成功才删除已归档的原始订单（分批）
        ids = [o['id'] for o in old_orders]
        batch_size = 100
        deleted = 0
        for i in range(0, len(ids), batch_size):
            batch = ids[i:i+batch_size]
            try:
                cur = conn.execute(f"DELETE FROM orders WHERE id IN ({','.join(['?']*len(batch))})", batch)
                deleted += cur.rowcount
                conn.commit()
            except Exception as e: logger.info(f"{e}")
        logger.info(f"Order archive: {len(old_orders)} orders → {len(agg)} daily stats rows (deleted {deleted})")
        try:
            _c3 = get_conn()
            _c3.execute("INSERT INTO quality_logs(log_type, level, message, source) VALUES('archive','info',?, 'scheduler')", (f"归档: {len(old_orders)}订单→{len(agg)}行daily_stats, 删除{deleted}",))
            _c3.commit()
        except Exception: pass
        conn.close()
        # 归档后立即增量回收空间（不需要独占锁）
        try:
            from app.core.database import incremental_vacuum
            incremental_vacuum()
        except Exception: pass
    except Exception as e:
        logger.info(f"Order archive error: {e}")




def _task_cleanup_logs():
    """每天清理 30 天前的日志"""
    try:
        from app.core.database import get_db
        db = get_db()
        cutoff = datetime.now(timezone.utc).isoformat()
        # 简单清理 events 和 quality_logs
        for table in ['events', 'quality_logs']:
            rows = db.table(table).select("*").execute().data
            before = len(rows)
            # 只保留最近 500 条
            if before > 500:
                ids = [r['id'] for r in rows[:-500]]
                if ids:
                    for id_str in ids:
                        try:
                            db.table(table).delete().eq("id", id_str).execute()
                        except Exception as e: logger.info(f"{e}")
            logger.info(f"{table}: {before} → kept latest")
    except Exception as e:
        logger.info(f"Cleanup error: {e}")

def _task_backup():
    from app.core.database import _backend as _bk
    if _bk() == "tidb":
        return  # TiDB 无文件系统/无 WAL, 备份与磁盘清理由平台负责
    """每天凌晨 2 点备份数据库（自动检查配额，只保留最近 2 个备份）"""
    try:
        from app.core.database import backup_db, DB_PATH
        import glob, os
        # 备份前：清理所有非 .gz 的备份临时文件（.bak.tmp / .bak.raw / 无后缀 .bak.YYYYMMDD）
        for _f in glob.glob(DB_PATH + ".bak.*"):
            if not _f.endswith('.gz'):
                try: os.remove(_f); logger.info(f"Pre-backup cleanup: removed {_f}")
                except Exception: pass
        # 备份前检查配额：如果已有 2 个备份，先删最旧的再备份
        baks = sorted(glob.glob(DB_PATH + ".bak.*.gz"), key=os.path.getmtime, reverse=True)
        while len(baks) >= 2:
            old = baks.pop()
            try:
                os.remove(old)
                logger.info(f"Pre-backup cleanup: removed {old}")
            except Exception as e:
                logger.info(f"Pre-backup cleanup error: {e}")
        # 备份
        path = backup_db()
        import os as _os
        # 验证备份有效：文件存在且大小超阈值（否则视为失败，防止空/损坏备份被当作成功）
        valid = path and _os.path.exists(path) and _os.path.getsize(path) > 1024
        if valid:
            logger.info(f"Backup: {path} ({_os.path.getsize(path)}B)")
            try:
                from app.core.database import get_conn
                _c = get_conn()
                _c.execute("INSERT INTO quality_logs(log_type,level,message,details,source) VALUES(?,?,?,?,?)",
                    ("backup", "info", f"数据库备份成功: {path} ({_os.path.getsize(path)}B)", "", "scheduler"))
                _c.commit()
            except Exception as _e:
                logger.warning(f"Backup log write error: {_e}")
        else:
            logger.error(f"Backup failed: path={path} size={_os.path.getsize(path) if path and _os.path.exists(path) else 'N/A'}")
            try:
                from app.core.database import get_conn
                _c = get_conn()
                _c.execute("INSERT INTO quality_logs(log_type,level,message,details,source) VALUES(?,?,?,?,?)",
                    ("backup", "error", "数据库备份失败（文件为空或过小）", f"path={path} size={_os.path.getsize(path) if path and _os.path.exists(path) else 'N/A'}", "scheduler"))
                _c.commit()
            except Exception as _e:
                logger.warning(f"Backup log write error: {_e}")
        # 备份后复查配额，超限则继续清理
        baks = sorted(glob.glob(DB_PATH + ".bak.*.gz"), key=os.path.getmtime, reverse=True)
        while len(baks) > 2:
            old = baks.pop()
            try:
                os.remove(old)
                logger.info(f"Post-backup cleanup: removed {old}")
            except Exception as e:
                logger.info(f"Post-backup cleanup error: {e}")
        # 备份后 VACUUM 压缩数据库（使用 db_maintenance 模块，带重试和降级）
        try:
            from app.core.db_maintenance import vacuum_database
            r = vacuum_database()
            if r.get('ok'):
                logger.info(f"VACUUM: {r.get('size_before')}MB → {r.get('size_after')}MB ({r.get('method','')})")
            elif r.get('skipped'):
                logger.info(f"VACUUM: 跳过（{r.get('size_before')}MB < 阈值）")
        except Exception as e:
            logger.info(f"VACUUM error: {e}")
    except Exception as e:
        logger.info(f"Backup error: {e}")

def _task_disk_cleanup():
    from app.core.database import _backend as _bk
    if _bk() == "tidb":
        return  # TiDB 无文件系统/无 WAL, 备份与磁盘清理由平台负责
    """每日磁盘自检：清理旧备份/临时文件 + WAL checkpoint，防止撑爆存储配额"""
    try:
        from app.core.database import DB_PATH
        import glob, os
        cleaned = []
        # 1. 旧备份只保留 2 个
        baks = sorted(glob.glob(DB_PATH + ".bak.*.gz"), key=os.path.getmtime, reverse=True)
        for old in baks[2:]:
            try:
                os.remove(old)
                cleaned.append(os.path.basename(old))
            except Exception: pass
        # 2. 清理临时文件（tmp* / .bak.tmp / .bak.*.raw / .nfs*）
        app_dir = os.path.dirname(DB_PATH)
        for _p in ["tmp*", ".bak.tmp", ".bak.*.raw", ".bak.*", ".nfs*"]:
            # 跳过 .gz 备份文件（由单独的备份保留逻辑处理）
            for f in glob.glob(os.path.join(app_dir, _p)):
                try:
                    if f.endswith('.gz'): continue
                    if os.path.isfile(f) or os.path.islink(f):
                        os.remove(f); cleaned.append(os.path.basename(f))
                except Exception: pass
        # 3. WAL checkpoint 防膨胀（合并 WAL 到主库）
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            cleaned.append("wal_checkpoint")
        except Exception as e:
            logger.info(f"WAL checkpoint error: {e}")
        # 4. 增量回收空间（不需要独占锁，auto_vacuum=INCREMENTAL 生效）
        try:
            from app.core.database import incremental_vacuum
            if incremental_vacuum():
                cleaned.append("vacuum(incremental)")
        except Exception as e:
            logger.info(f"VACUUM error: {e}")
        # 5. 清理旧导出文件（保留最近 10 个）
        exports_dir = os.path.join(app_dir, "api", "exports")
        exps = sorted(glob.glob(os.path.join(exports_dir, "*.xlsx")), key=os.path.getmtime, reverse=True)
        for old in exps[10:]:
            try: os.remove(old); cleaned.append(os.path.basename(old))
            except Exception: pass
        # 6. 清理旧 quality_logs（保留最近 1000 条）
        try:
            import sqlite3
            _c = sqlite3.connect(DB_PATH)
            _c.execute("PRAGMA busy_timeout=10000")
            _c.execute("DELETE FROM quality_logs WHERE id NOT IN (SELECT id FROM quality_logs ORDER BY id DESC LIMIT 1000)")
            _c.commit()
            _c.close()
            cleaned.append("quality_logs_trim")
        except Exception: pass
        # 7. 报告数据库和 WAL 大小 + 写 quality_logs(可见) + 磁盘告警
        db_size = os.path.getsize(DB_PATH) / 1024 / 1024
        wal_path = DB_PATH + "-wal"
        wal_size = os.path.getsize(wal_path) / 1024 / 1024 if os.path.exists(wal_path) else 0
        logger.info(f"Disk cleanup: {cleaned} | db={db_size:.1f}MB wal={wal_size:.1f}MB")
        try:
            _ql = sqlite3.connect(DB_PATH); _ql.execute("PRAGMA busy_timeout=5000")
            _ql.execute("INSERT INTO quality_logs(log_type,level,message,source) VALUES('disk_cleanup','info',?, 'scheduler')",
                        (f"清理:{cleaned} db={db_size:.1f}MB wal={wal_size:.1f}MB",))
            # 磁盘用量告警(free<2GB 则 warning)
            import shutil
            _free = shutil.disk_usage(os.path.dirname(DB_PATH)).free / 1024/1024/1024
            if _free < 2:
                _ql.execute("INSERT INTO quality_logs(log_type,level,message,source) VALUES('disk_warning','error',?, 'scheduler')",
                            (f"磁盘余量不足 {_free:.1f}GB(<2GB), 需立即清理!",))
            _ql.commit(); _ql.close()
        except Exception as _e:
            logger.info(f"disk cleanup log: {_e}")
    except Exception as e:
        logger.info(f"Disk cleanup error: {e}")

# WAL checkpoint 全局互斥锁(与 health/seed 按需 checkpoint 共享, 防并发 TRUNCATE 互相等待超时)
_checkpoint_lock = None
def _get_checkpoint_lock():
    global _checkpoint_lock
    if _checkpoint_lock is None:
        import threading
        _checkpoint_lock = threading.Lock()
    return _checkpoint_lock

def _task_wal_checkpoint_periodic():
    from app.core.database import _backend as _bk
    if _bk() == "tidb":
        return  # TiDB 无文件系统/无 WAL, 备份与磁盘清理由平台负责
    """定时 WAL checkpoint 防膨胀: 仅当 WAL>15MB 才 TRUNCATE(小WAL跳过零阻塞;
    曾6h间隔内WAL暴涨89MB撑满配额3次事故, 但高频阻塞式TRUNCATE会与写请求争锁)"""
    try:
        from app.core.database import DB_PATH
        import sqlite3, os, threading
        assert os.path.exists(DB_PATH + '-wal')
        if os.path.getsize(DB_PATH + '-wal') / 1024 / 1024 <= 15:
            return  # WAL 小, 跳过(避免无谓阻塞)
        lock = _get_checkpoint_lock()
        if not lock.acquire(blocking=False):
            return  # 已有 checkpoint 在跑(health/seed), 跳过避免并发
        try:
            conn = sqlite3.connect(DB_PATH, timeout=20)
            conn.execute("PRAGMA busy_timeout=20000")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            try:
                _c = sqlite3.connect(DB_PATH); _c.execute("PRAGMA busy_timeout=5000")
                _c.execute("INSERT INTO quality_logs(log_type,level,message,source) VALUES('wal_checkpoint','info',?, 'scheduler')", ("定时WAL checkpoint 完成",))
                _c.commit(); _c.close()
            except Exception: pass
        finally:
            lock.release()
    except Exception as e:
        logger.info(f"periodic WAL checkpoint error: {e}")

def _task_daily_rules():
    """每天执行定时规则（内置滞销识别 + 用户自定义 scheduled.daily 规则）

    历史缺陷：只调 detect_slow_moving_products（内置逻辑），用户建的
    event='scheduled.daily' 规则从未被 evaluate → 每日定时规则功能失效。
    """
    try:
        from app.core.database import get_db
        from app.api.routes.insights import detect_slow_moving_products
        db = get_db()
        # 1. 内置滞销识别——先检查"滞销识别"规则是否启用（停用后不再生成 event_bus 告警）
        slow_rules = db.table("rules").select("id").eq("alert_type", "slow_moving").eq("is_active", 1).execute().data
        if slow_rules:
            results = detect_slow_moving_products(db, create_alerts=True)
            n = results.get('data', results) if isinstance(results, dict) else results
            logger.info(f"Daily rules: slow-moving checked {len(n) if hasattr(n, '__len__') else n} items")
        else:
            logger.info("Daily rules: slow_moving rule disabled, skip slow-moving alerts")
        # 2. 用户自定义每日定时规则（修复：之前永远不触发）
        _eval_daily_user_rules(db)
        # 3. 孤儿告警兜底清理（规则被停用但联动漏执行时自愈）
        _cleanup_orphan_alerts(db)
    except Exception as e:
        logger.info(f"Daily rules error: {e}")


def _cleanup_orphan_alerts(db):
    """孤儿告警兜底清理：active 告警的 (alert_type, channel) 已无 active 规则 → 关闭

    只处理 rules_engine/event_bus 来源（规则与滞销识别产出），
    replenishment_engine 告警与规则无关，不受影响。
    """
    try:
        from app.core.database import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT DISTINCT alert_type, channel FROM alerts WHERE status='active' AND source IN ('rules_engine','event_bus')"
        ).fetchall()
        cleared = 0
        for at, ch in rows:
            has_rule = db.table("rules").select("id").eq("alert_type", at).eq("channel", ch).eq("is_active", 1).execute().data
            if not has_rule:
                cur = conn.execute(
                    "UPDATE alerts SET status='inactive' WHERE alert_type=? AND channel=? AND status='active' AND source IN ('rules_engine','event_bus')",
                    (at, ch))
                cleared += cur.rowcount
        if cleared:
            conn.commit()
            from app.core.dashboard_cache import invalidate as invalidate_dashboard
            invalidate_dashboard()
            logger.info(f"Orphan alerts cleanup: {cleared} inactive")
        return cleared
    except Exception as e:
        logger.error(f"Orphan alerts cleanup error: {e}")
        return 0


def _eval_daily_user_rules(db):
    """评估用户自定义的每日定时规则（event='scheduled.daily'），按有库存 SKU 遍历

    内置"滞销识别"规则（_seed_builtin_rules 种入）也会被 evaluate 命中，
    与 detect_slow_moving_products 的告警由 _action_create_alert 的
    alert_type+sku+channel+source 去重兜底，不会重复。
    """
    try:
        from datetime import timedelta
        from app.core.database import get_conn
        from app.core.rules import evaluate
        conn = get_conn()
        inv_rows = conn.execute(
            "SELECT sku, available_qty, safety_qty, in_transit_qty, product_name, channel FROM inventory"
        ).fetchall()
        if not inv_rows:
            return
        last_map = {}
        for r in conn.execute("SELECT sku, MAX(date) FROM daily_sales_snapshot GROUP BY sku").fetchall():
            last_map[str(r[0])] = str(r[1] or '')[:10]
        now = datetime.now(timezone.utc)
        for r in inv_rows:
            sku = str(r[0] or '')
            if not sku:
                continue
            avail = int(r[1] or 0); safety = int(r[2] or 0); transit = int(r[3] or 0)
            pname = str(r[4] or '') or sku
            ch = str(r[5] or 'jd')
            last_date = last_map.get(sku, '')
            days = 999
            if last_date:
                try:
                    days = (now - datetime.strptime(last_date[:10], "%Y-%m-%d")).days
                except Exception:
                    pass
            try:
                evaluate('scheduled.daily', {
                    'db': db, 'sku': sku, 'channel': ch,
                    'inv': {'available_qty': avail, 'safety_qty': safety,
                            'in_transit_qty': transit, 'product_name': pname},
                    'stock': avail, 'days_since_last': days,
                    'product_name': pname,
                })
            except Exception as e:
                logger.warning(f"[daily-rules] eval {sku}: {e}")
        logger.info(f"Daily user rules: evaluated {len(inv_rows)} SKUs")
    except Exception as e:
        logger.error(f"Daily user rules error: {e}")

def _task_cleanup_recycle():
    """每天清理回收站：永久删除软删除超过 30 天的订单和规则（防数据无限累积）"""
    try:
        from app.core.database import get_conn
        conn = get_conn()
        # orders: 真软删(deleted_at非空非'')且超过 30 天
        # (修复: deleted_at IS NOT NULL 会匹配 ''(active), 导致每天删光所有订单)
        cur1 = conn.execute("DELETE FROM orders WHERE deleted_at != '' AND deleted_at IS NOT NULL AND deleted_at < datetime('now','-30 days')")
        # rules: 已软删(is_active=0 且 deleted_at 非空非'')且超过 30 天
        cur2 = conn.execute("DELETE FROM rules WHERE is_active=0 AND deleted_at != '' AND deleted_at IS NOT NULL AND deleted_at < datetime('now','-30 days')")
        conn.commit()
        _n = (cur1.rowcount or 0) + (cur2.rowcount or 0)
        if _n > 0:
            logger.info(f"Recycle cleanup: purged {_n} items (>30 days)")
            try:
                _c4 = get_conn()
                _c4.execute("INSERT INTO quality_logs(log_type, level, message, source) VALUES('recycle','info',?, 'scheduler')", (f"回收站清理: 永久删除{_n}条(orders+rules, 真软删超30天)",))
                _c4.commit()
            except Exception: pass
    except Exception as e:
        logger.warning(f"Recycle cleanup error: {e}")

def _task_push_alerts():
    """推送新告警到 Webhook（钉钉/企业微信机器人，设置页配置 webhook_url）。

    只推送最近 60 分钟新增且未推送过的 active 告警；推送成功后标记 pushed=1，
    避免重复推送。格式兼容钉钉/企业微信（均为 POST JSON {"msgtype":"text",...}）。
    """
    try:
        from app.core.database import get_conn
        import requests, json
        conn = get_conn()
        _cfg = conn.execute("SELECT value FROM replenishment_config WHERE key='webhook_url'").fetchone()
        if not _cfg or not (_cfg[0] or '').strip():
            return  # 未配置 webhook，跳过
        url = _cfg[0].strip()
        # 最近 60 分钟新增、未推送的 active 告警
        rows = conn.execute(
            "SELECT id, alert_type, title, description, severity, channel, related_sku FROM alerts "
            "WHERE status='active' AND (pushed IS NULL OR pushed=0) AND created_at >= datetime('now','-60 minutes')"
        ).fetchall()
        if not rows:
            return
        lines = [f"【SupplyKit 告警】"]
        for r in rows:
            _ch = '京东' if r[5] == 'jd' else '其他'
            lines.append(f"{r[4]}: {r[2]}{(' - ' + str(r[3])) if r[3] else ''} ({_ch}{' / ' + r[6] if r[6] else ''})")
        payload = {"msgtype": "text", "text": {"content": "\n".join(lines)}}
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code in (200, 201, 204):
            conn.execute("UPDATE alerts SET pushed=1 WHERE id IN (%s)" % ','.join('?' * len(rows)),
                tuple(r[0] for r in rows))
            conn.commit()
            logger.info(f"Alert push: {len(rows)} alerts to webhook")
        else:
            logger.warning(f"Alert push failed: HTTP {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Alert push error: {e}")

def _task_warmup_dashboard():
    """预热 dashboard 缓存（延迟执行，后台线程重建 jd+other，避免首个用户请求同步重建 10s）"""
    try:
        import threading
        def _w():
            try:
                from app.core.dashboard_cache import get_cached_dashboard
                get_cached_dashboard('jd')
                get_cached_dashboard('other')
                logger.info("Dashboard warmup done")
            except Exception as e:
                logger.warning(f"Dashboard warmup error: {e}")
        threading.Thread(target=_w, daemon=True).start()
    except Exception as e:
        logger.warning(f"Warmup job error: {e}")


def start():
    global _started
    if _started:
        return
    _started = True
    scheduler.add_job(_task_inventory_sync, IntervalTrigger(minutes=30), id='inventory_sync')
    scheduler.add_job(_task_build_sales_snapshot, CronTrigger(hour=3, minute=30), id='build_sales_snapshot')
    scheduler.add_job(_task_snapshot_freshness, IntervalTrigger(hours=1), id='snapshot_freshness')
    scheduler.add_job(_task_archive_orders, CronTrigger(hour=1, minute=0), id='archive_orders')
    scheduler.add_job(_task_cleanup_logs, CronTrigger(hour=3, minute=0), id='cleanup_logs')
    scheduler.add_job(_task_backup, CronTrigger(hour=2, minute=0), id='db_backup')
    scheduler.add_job(_task_daily_rules, CronTrigger(hour=4, minute=0), id='daily_rules')
    scheduler.add_job(_task_cleanup_recycle, CronTrigger(hour=4, minute=30), id='recycle_cleanup')
    scheduler.add_job(_task_push_alerts, IntervalTrigger(minutes=30), id='push_alerts')
    scheduler.add_job(_task_disk_cleanup, CronTrigger(hour=3, minute=20), id='disk_cleanup')
    scheduler.add_job(_task_wal_checkpoint_periodic, IntervalTrigger(minutes=15), id='wal_checkpoint_periodic')  # 15min(曾60/360min, 高频写下WAL暴涨撑爆配额→3次事故)
    # 每小时 WAL checkpoint（防 WAL 无限增长导致的慢/锁/配额问题）
    # 延迟预热 dashboard 缓存（reload 后 10s 执行，避开 CI health 探测窗口；修复预热线程饿死请求）
    scheduler.add_job(_task_warmup_dashboard, trigger='date', run_date=datetime.now(timezone.utc) + timedelta(seconds=10), id='dash_warmup')
    scheduler.start()
    # 快照新鲜度自愈(启动即查): PA 上 03:30 CronTrigger 可能长期不触发(快照曾停 7/9 致濒临断货 0 条),
    # 进程频繁 reload → 每次启动检查, 快照陈旧(>2天)立即重建, 不依赖单一定时
    try:
        _sn = get_conn().execute("SELECT COALESCE(MAX(date),'') FROM daily_sales_snapshot").fetchone()[0]
        _today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if not _sn or _sn < (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%d'):
            logger.warning(f"[scheduler] 日销快照陈旧(max={_sn}), 启动重建")
            from app.core.sales_utils import build_daily_sales_snapshot
            threading.Thread(target=lambda: build_daily_sales_snapshot(get_db()), daemon=True).start()
    except Exception as e:
        logger.warning(f"[scheduler] snapshot freshness check: {e}")
    logger.info(f"Started at {datetime.now(timezone.utc).isoformat()}")

def get_status():
    jobs = scheduler.get_jobs()
    return {
        'running': scheduler.running,
        'jobs': [{
            'id': j.id,
            'next_run': str(j.next_run_time) if j.next_run_time else None,
            'trigger': str(j.trigger),
        } for j in jobs]
    }
