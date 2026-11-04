"""历史 migration_log 标记归并(2026-11-04, S3-5 一次性)

`migration_log` 是迁移框架之前的一次性标记表(每个步骤自己 `INSERT IGNORE` 自守)。
迁入 `schema_migrations` 后, 若不归并, 线上会**把已跑过的步骤再跑一遍** ——
其中 oversell_wording 与 products_expiry_managed 原本就靠"重复执行无害"兜着,
但仍多花一次全表 UPDATE。

做法: 对注册表里每个 step, 若 migration_log 已有**同名**记录 → 直接登记为已应用(不执行)。
命名对应关系写在 `_LEGACY_ALIASES`(旧名与 step id 不同名的那几条)。

只读 + 单条 INSERT IGNORE, 无数据改写。
"""
from db import query, execute

# step id → 旧 migration_log.name
_LEGACY_ALIASES = {
    "20261029_rules_field_path": "rules_field_path_v1",
    "20261030_lowstock_sellable": "lowstock_sellable_v1",
    "20261030_channel_name": "channel_name_v1",
    "20261030_lowstock_wording": "lowstock_wording_v2",
    "20261101_products_expiry_managed": "products_expiry_managed_v1",
}


def run():
    from migrations import step_ids
    names = {str(r.get("name") or "") for r in (query("SELECT name FROM migration_log") or [])}
    adopted = []
    for sid in step_ids():
        legacy = _LEGACY_ALIASES.get(sid) or sid
        if legacy in names:
            # 登记为已应用, 但 **不执行**(线上早已跑过)
            execute("INSERT IGNORE INTO schema_migrations(id, note) VALUES(%s, %s)",
                    [sid, "adopted:migration_log:%s" % legacy])
            adopted.append(sid)
    return "归并 %d 条" % len(adopted)
