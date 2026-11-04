"""效期管理标记存量推导(2026-11-01, 迁自 schema_create.create_all_tables)

有批次行或 best_before 已填 → 视为"做效期管理"; 其余保持 ''(待标注, 导入时强制补)。

**不默认全 yes** —— 会把不做效期的老货误判; **不默认 no** —— 会废掉现有批次。
幂等: WHERE COALESCE(expiry_managed,'')='' 二次执行匹配 0 行(已标的不会重算)。
"""
from db import execute


def run():
    n = execute("UPDATE products SET expiry_managed='yes' "
                "WHERE COALESCE(expiry_managed,'')='' AND ("
                "  COALESCE(best_before,'')<>'' OR sku IN (SELECT sku FROM batches))")
    return "%d 个 SKU 标 yes" % int(n or 0)
