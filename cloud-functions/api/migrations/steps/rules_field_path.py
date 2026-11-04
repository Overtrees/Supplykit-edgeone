"""规则字段路径迁移(2026-10-29, 迁自 schema_create.create_all_tables)

前端旧选项选出的表达式在后端 ctx 里永远取不到: `inv.days_since_last` / `inv.stock`
在 ctx 里是顶层字段。不迁则存量规则静默永不触发(解析缺 key 返回 None → 条件跳过)。

幂等: 两个 UPDATE 都是 REPLACE + LIKE 过滤, 再跑一次匹配 0 行。
"""
from db import execute


def run():
    n1 = execute("UPDATE rules SET condition_json=REPLACE(condition_json,"
                 "'inv.days_since_last','days_since_last') "
                 "WHERE condition_json LIKE '%%inv.days_since_last%%'")
    n2 = execute("UPDATE rules SET condition_json=REPLACE(condition_json,"
                 "'inv.stock','stock') WHERE condition_json LIKE '%%inv[.]stock%%'")
    return "days_since_last=%s stock=%s" % (n1, n2)
