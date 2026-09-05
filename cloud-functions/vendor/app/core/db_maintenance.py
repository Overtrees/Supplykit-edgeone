"""数据库维护模块：VACUUM 压缩 + 大小监控 + 自动清理"""
import os, sqlite3, time, logging
from app.core.database import DB_PATH

logger = logging.getLogger("db_maintenance")

# 数据库膨胀阈值（超过则触发强制 VACUUM）
VACUUM_THRESHOLD_MB = 150
# 达到此阈值时预警
WARN_THRESHOLD_MB = 120


def get_db_size_mb(path=None):
    """返回数据库文件大小(MB)"""
    p = path or DB_PATH
    try:
        return os.path.getsize(p) / 1024 / 1024
    except Exception:
        return 0


def vacuum_database(timeout=120, max_retry=3):
    """执行 VACUUM 压缩数据库，带重试

    优先用独立连接 + DELETE 模式（获取独占锁），失败则用 VACUUM INTO 降级。
    """
    sz_before = get_db_size_mb()
    # 数据库 < 阈值，无需压缩
    if sz_before < WARN_THRESHOLD_MB:
        return {"ok": True, "skipped": True, "size_before": round(sz_before, 1)}

    result = {"ok": False, "size_before": round(sz_before, 1)}

    # 尝试 1：独立连接 + DELETE 模式（常规 VACUUM）
    for attempt in range(max_retry):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=timeout)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.close()
            sz_after = get_db_size_mb()
            logger.info(f"[db] VACUUM ok: {sz_before:.0f}MB → {sz_after:.0f}MB")
            result.update({"ok": True, "size_after": round(sz_after, 1), "method": "VACUUM"})
            return result
        except Exception as e:
            logger.warning(f"[db] VACUUM attempt {attempt+1} failed: {e}")
            time.sleep(3)

    # 尝试 2：VACUUM INTO 降级（写入临时文件后替换）
    try:
        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp')
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"vacuum_{int(time.time())}.db")
        conn = sqlite3.connect(DB_PATH, timeout=timeout)
        conn.execute("VACUUM INTO ?", (tmp_path,))
        conn.close()
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1024:
            os.replace(tmp_path, DB_PATH)
            sz_after = get_db_size_mb()
            logger.info(f"[db] VACUUM INTO ok: {sz_before:.0f}MB → {sz_after:.0f}MB")
            result.update({"ok": True, "size_after": round(sz_after, 1), "method": "VACUUM_INTO"})
            return result
        else:
            if os.path.exists(tmp_path): os.remove(tmp_path)
            logger.warning(f"[db] VACUUM INTO produced invalid file")
    except Exception as e:
        logger.warning(f"[db] VACUUM INTO failed: {e}")

    return result


def check_and_maintain():
    """数据库维护入口：检查大小，超过阈值则 VACUUM，返回维护报告"""
    sz = get_db_size_mb()
    report = {"size_mb": round(sz, 1), "action": "none"}

    if sz >= VACUUM_THRESHOLD_MB:
        r = vacuum_database()
        report["action"] = "vacuum"
        report["vacuum"] = r
    elif sz >= WARN_THRESHOLD_MB:
        report["action"] = "warn"
        logger.warning(f"[db] 数据库 {sz:.0f}MB 接近阈值，建议 VACUUM")

    return report