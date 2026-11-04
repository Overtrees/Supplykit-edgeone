"""低库存文案修正(2026-10-30, 迁自 schema_create.create_all_tables)

上一条迁移(lowstock_sellable)只换了占位符 {avail}→{sellable}, 前缀"可用"没改 →
现为"可用 {sellable}", 语义矛盾(sellable = 可用 + 在途)。本步补正为"可售 {sellable}"。

依赖: 必须在 lowstock_sellable **之后**执行(注册表顺序已保证)。
幂等: REPLACE 二次执行找不到 '可用 {sellable}' 即 0 行。
"""
from db import execute


def run():
    n = execute("UPDATE rules SET alert_desc=REPLACE(alert_desc, '可用 {sellable}', '可售 {sellable}') "
                "WHERE alert_type='low_stock' AND alert_desc LIKE '%%可用 {sellable}%%'")
    return "%s 条" % n
