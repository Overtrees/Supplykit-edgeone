"""SQL 方言转换器: SQLite → TiDB/MySQL

供 ORM 双后端适配使用。将 SQLite 专有语法翻译为 TiDB 兼容语法。
用法:
    from app.core.dialect import to_tidb
    sql = to_tidb("SELECT strftime('%Y-%m-%d', ordered_at) ...")
"""
import re

# ── strftime → DATE_FORMAT ─────────────────────────────────────────────
# strftime('%Y-%m-%d', col)  → DATE_FORMAT(col, '%Y-%m-%d')
# strftime('%Y-%m-%d %H:%M:%S', col) → DATE_FORMAT(col, '%Y-%m-%d %H:%M:%S')
_STRFTIME_FMT = {
    '%Y-%m-%d': '%Y-%m-%d',
    '%Y-%m-%d %H:%M:%S': '%Y-%m-%d %H:%M:%S',
    '%Y-%m': '%Y-%m',
    '%m-%d': '%m-%d',
}
_STRFTIME_RE = re.compile(
    r"strftime\(\s*'([^']*)'\s*,\s*([^)]+)\)", re.I)


def _conv_strftime(m):
    fmt, col = m.group(1), m.group(2).strip()
    f = _STRFTIME_FMT.get(fmt)
    if f is None:
        return m.group(0)  # 未知格式保持原样(告警)
    return "DATE_FORMAT(%s, '%s')" % (col, f)


# ── datetime('now', ...) → NOW() / DATE_ADD/DATE_SUB ───────────────────
_DT_NOW_RE = re.compile(
    r"datetime\(\s*'now'(\s*,\s*'([+-]?\d+)\s*(second|minute|hour|day|month|year)s?')?\s*\)", re.I)


def _conv_datetime_now(m):
    if m.group(2):
        num, unit = m.group(2), m.group(3).lower()
        fn = 'DATE_ADD' if int(num) >= 0 else 'DATE_SUB'
        num = abs(int(num))
        if unit == 'second':
            unit = 'SECOND'
        elif unit == 'minute':
            unit = 'MINUTE'
        elif unit == 'hour':
            unit = 'HOUR'
        elif unit == 'day':
            unit = 'DAY'
        elif unit == 'month':
            unit = 'MONTH'
        else:
            unit = 'YEAR'
        return "DATE_%s(NOW(), INTERVAL %d %s)" % ('ADD' if fn == 'DATE_ADD' else 'SUB', num, unit)
    return "NOW()"


# ── substr(col, a, b) → SUBSTRING(col, a, b) ───────────────────────────
_SUBSTR_RE = re.compile(r"\bsubstr\(\s*([^,]+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)


def _conv_substr(m):
    return "SUBSTRING(%s, %s, %s)" % (m.group(1).strip(), m.group(2), m.group(3))


# ── INSERT OR IGNORE → INSERT IGNORE ───────────────────────────────────
_INSERT_OR_IGNORE_RE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
# ── INSERT OR REPLACE → REPLACE INTO ───────────────────────────────────
_INSERT_OR_REPLACE_RE = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.I)


# ── ON CONFLICT(cols) DO UPDATE SET a=excluded.a → ON DUPLICATE KEY UPDATE a=VALUES(a)
_ON_CONFLICT_RE = re.compile(
    r"\bON\s+CONFLICT\s*\(([^)]*)\)\s*DO\s+UPDATE\s+SET\s+([^;]*?)(?=\s*;|\s*$)", re.I | re.S)


def _conv_on_conflict(m):
    assigns = []
    for part in m.group(2).split(','):
        part = part.strip()
        mm = re.match(r"([\w`]+)\s*=\s*excluded\.([\w`]+)", part, re.I)
        if mm:
            col = mm.group(1).strip('`')
            assigns.append('`%s`=VALUES(`%s`)' % (col, col))
        else:
            assigns.append(part)
    return "ON DUPLICATE KEY UPDATE " + ", ".join(assigns)


def to_tidb(sql):
    """SQLite SQL → TiDB 兼容 SQL(幂等, 未知语法保持原样)"""
    s = _INSERT_OR_REPLACE_RE.sub("REPLACE INTO", sql)
    s = _INSERT_OR_IGNORE_RE.sub("INSERT IGNORE INTO", s)
    s = _STRFTIME_RE.sub(_conv_strftime, s)
    s = _DT_NOW_RE.sub(_conv_datetime_now, s)
    s = _SUBSTR_RE.sub(_conv_substr, s)
    s = _ON_CONFLICT_RE.sub(_conv_on_conflict, s)
    return s


# ── 测试 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cases = [
        ("SELECT strftime('%Y-%m-%d', ordered_at) FROM orders",
         "SELECT DATE_FORMAT(ordered_at, '%Y-%m-%d') FROM orders"),
        ("SELECT strftime('%Y-%m-%d %H:%M:%S', updated_at) FROM products",
         "SELECT DATE_FORMAT(updated_at, '%Y-%m-%d %H:%M:%S') FROM products"),
        ("datetime('now')", "NOW()"),
        ("datetime('now', '-30 days')", "DATE_SUB(NOW(), INTERVAL 30 DAY)"),
        ("datetime('now', '+10 minutes')", "DATE_ADD(NOW(), INTERVAL 10 MINUTE)"),
        ("substr(ordered_at,1,10)", "SUBSTRING(ordered_at, 1, 10)"),
        ("INSERT OR IGNORE INTO alerts VALUES(1)", "INSERT IGNORE INTO alerts VALUES(1)"),
        ("INSERT OR REPLACE INTO alerts VALUES(1)", "REPLACE INTO alerts VALUES(1)"),
        ("INSERT INTO t VALUES(1) ON CONFLICT(sku, channel) DO UPDATE SET qty=excluded.qty",
         "INSERT INTO t VALUES(1) ON DUPLICATE KEY UPDATE `qty`=VALUES(`qty`)"),
        ("SELECT COALESCE(prod_date,'') FROM batches", "SELECT COALESCE(prod_date,'') FROM batches"),
        ("UPDATE orders SET status='x' WHERE substr(ordered_at,1,10)>='2026-01-01'",
         "UPDATE orders SET status='x' WHERE SUBSTRING(ordered_at, 1, 10)>='2026-01-01'"),
    ]
    ok = 0
    for src, want in cases:
        got = to_tidb(src)
        mark = '✅' if got == want else '❌'
        if got == want:
            ok += 1
        print('%s %-70s → %s' % (mark, src[:68], got))
        if got != want:
            print('     期望: %s' % want)
    print('\n通过 %d/%d' % (ok, len(cases)))
