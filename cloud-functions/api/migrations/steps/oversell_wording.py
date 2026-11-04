"""超卖告警文案迁移(2026-10-29, 迁自 schema_create.create_all_tables)

超卖判定口径由"单笔订单量"改为"累计订单量"。告警 description 是**生成时快照**,
已存在的告警仍是旧文案 → 一次性同步。

注意: 这一步原本**没有** migration_log 守卫, 每次冷启动都整表 UPDATE。迁入框架后
由 `schema_migrations` 保证只跑一次 —— 这也是 S3-5 的直接收益之一。
"""
from db import execute


def run():
    n = execute("UPDATE alerts SET description='累计订单量超过可用库存' "
                "WHERE alert_type='oversell' AND description='订单数量超过可用库存'")
    return "%s 条" % n
