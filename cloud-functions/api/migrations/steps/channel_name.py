"""渠道显示名对齐原 UI(2026-10-30, 迁自 schema_create.create_all_tables)

'京东' → '京东渠道': 切主体胶囊/下拉文案与旧版一致。

幂等: WHERE code='jd' AND name='京东' —— 二次执行匹配 0 行(新库种子直接就是"京东渠道")。
"""
from db import execute


def run():
    n = execute("UPDATE channels SET name='京东渠道' WHERE code='jd' AND name='京东'")
    return "%s 条" % n
