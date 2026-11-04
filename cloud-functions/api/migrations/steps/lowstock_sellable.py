"""低库存口径统一: 可用 + 在途(2026-10-30, 迁自 schema_create.create_all_tables)

背景: BC 维度原先把"在途"并入 available_qty, 与逐仓行(纯可用)同模板不同义 →
语义对齐后规则条件显式相加。只动 `alert_type='low_stock'` 且尚未含 in_transit 的规则,
自定义规则与非低库存规则不受影响。

幂等: NOT LIKE '%in_transit_qty%' 保证二次执行匹配 0 行。
"""
from db import execute


def run():
    n = execute(
        "UPDATE rules SET "
        "condition_json=REPLACE(condition_json, '\"inv.available_qty\"', "
        "  '\"inv.available_qty + inv.in_transit_qty\"'), "
        "alert_desc=REPLACE(alert_desc, '{avail}', '{sellable}') "
        "WHERE alert_type='low_stock' AND condition_json LIKE '%%inv.available_qty%%' "
        "AND condition_json NOT LIKE '%%in_transit_qty%%'")
    return "%s 条" % n
