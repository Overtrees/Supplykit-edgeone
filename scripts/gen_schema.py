#!/usr/bin/env python3
"""从 SQLite backend/app/supplykit.db 生成 TiDB SCHEMA_SQL(索引并入 CREATE TABLE)

Phase2 迁移工具: 转换规则与 database.py 双后端适配一致的固化版。
运行: python3 scripts/gen_schema.py > /tmp/schema.sql
索引并入表定义(KEY/UNIQUE KEY)——避免 TiDB 异步 DDL 下独立 CREATE INDEX 的竞争。
"""
import re
import sqlite3

DB = 'backend/app/supplykit.db'

DATETIME_DEFAULT = {'created_at', 'updated_at'}
DATETIME_COLS = {'ordered_at', 'paid_at', 'inbound_date', 'outbound_date', 'prod_date', 'exp_date', 'arrival_date', 'date'}
VARCHAR_LEN = {'channel': 20, 'warehouse': 64, 'warehouse_type': 20, 'sku': 64, 'order_no': 64, 'barcode': 64,
               'store': 128, 'status': 30, 'order_status': 30, 'task_id': 64, 'supplier_code': 64, 'key': 64,
               'target': 50, 'doc_type': 30, 'error_type': 30, 'field_name': 50, 'event': 30, 'event_type': 30,
               'alert_type': 30, 'source': 30, 'platform': 30, 'level': 20, 'log_type': 30, 'entity_type': 50,
               'entity_id': 64, 'mode': 20, 'reason': 100, 'action': 50, 'note': 200, 'username': 64, 'role': 20,
               'data_source': 50}
TEXT_COLS = {'description', 'message', 'details', 'payload', 'raw_data', 'raw_value', 'result', 'params', 'mapping',
             'remark', 'title', 'product_name', 'supplier_name', 'contact_person', 'contact_phone', 'name',
             'condition_json', 'alert_title', 'alert_desc', 'display_name'}


def quote_cols(m):
    return '(' + re.sub(r'(\w+)', lambda x: '`%s`' % x.group(1), m.group(1)) + ')'


def convert_table(name, sql):
    body = sql[sql.find('(') + 1: sql.rfind(')')]
    body = re.sub(r'--[^\n]*', '', body)
    body = re.sub(r'\s+', ' ', body).strip()
    cols, depth, cur = [], 0, ''
    for ch in body:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            cols.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        cols.append(cur.strip())
    out = []
    for c in cols:
        c = c.strip()
        if re.match(r'^(UNIQUE|PRIMARY|FOREIGN|CONSTRAINT|CHECK)\b', c, re.I):
            out.append(re.sub(r'\(([^)]*)\)', quote_cols, c, count=1))
            continue
        m = re.match(r'^`?(\w+)`?\s+(.*)$', c)
        if not m:
            out.append(c)
            continue
        cname, rest = m.group(1), m.group(2)
        uniq = ' UNIQUE' if re.search(r'\bUNIQUE\b', rest, re.I) else ''
        rest = re.sub(r'\bUNIQUE\b', '', rest, flags=re.I).strip()
        dflt = ''
        dm = re.search(r'DEFAULT\s+(.+)$', rest, re.I)
        if dm:
            dflt = ' DEFAULT ' + dm.group(1).strip()
            rest = rest[:dm.start()].strip()
        rest = re.sub(r'\s+', ' ', rest).strip()
        if cname == 'id':
            out.append('`id` BIGINT PRIMARY KEY AUTO_INCREMENT')
            continue
        if cname in DATETIME_DEFAULT:
            out.append('`%s` DATETIME DEFAULT CURRENT_TIMESTAMP%s' % (cname, uniq))
            continue
        if cname in DATETIME_COLS:
            out.append('`%s` DATETIME%s' % (cname, uniq))
            continue
        if cname in VARCHAR_LEN:
            out.append('`%s` VARCHAR(%d)%s%s' % (cname, VARCHAR_LEN[cname], uniq, dflt))
            continue
        if re.search(r'\bINTEGER\b', rest, re.I):
            out.append('`%s` BIGINT%s%s' % (cname, uniq, dflt))
            continue
        if re.search(r'\bREAL\b', rest, re.I):
            out.append('`%s` DOUBLE%s%s' % (cname, uniq, dflt))
            continue
        if cname in TEXT_COLS or re.search(r'\bTEXT\b', rest, re.I):
            out.append('`%s` TEXT%s' % (cname, uniq))
            continue
        out.append('`%s` VARCHAR(100)%s%s' % (cname, uniq, dflt))
    return out


def main():
    db = sqlite3.connect(DB)
    # 表
    tables = {}
    for name, sql in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT IN ('migration_log','_schema_version') ORDER BY name"):
        tables[name] = convert_table(name, sql)
    # 索引(并入表定义)
    skip = {'idx_orders_cdate'}  # 表达式索引 substr() TiDB 不支持, idx_orders_ch_ordered_at 覆盖
    for name, tbl, sql in db.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        if name in skip or name.startswith('sqlite_') or tbl not in tables:
            continue
        uniq = 'UNIQUE ' if 'UNIQUE' in sql.upper() else ''
        cols = re.sub(r"COALESCE\(\s*(\w+)\s*,\s*'?'?\s*\)", r'\1',
                      re.search(r'ON\s+\w+\s*\((.*)\)\s*;?\s*$', sql, re.S).group(1))
        cols = re.sub(r'\s+', ' ', cols).strip()
        tables[tbl].append('%sKEY `%s` (%s)' % (uniq, name, cols))
        if tbl == 'orders' and name == 'idx_orders_ch_ordered_at':
            tables[tbl].append('KEY `idx_orders_channel_ordered` (channel, ordered_at)')
    # 输出
    lines = []
    for name in sorted(tables):
        body = ', '.join(tables[name])
        lines.append('CREATE TABLE IF NOT EXISTS `%s` (%s) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;' % (name, body))
    print('\n'.join(lines))


if __name__ == '__main__':
    main()