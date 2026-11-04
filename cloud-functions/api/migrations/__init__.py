"""有序迁移框架(2026-11-04 S3-5): 把「会改到存量数据/约束」的一次性步骤从建表声明里分离

## 与 schema_create 的分工

| 要做的事 | 放哪 | 判据 |
|---|---|---|
| 新建表 / 加列 / 加索引 | `schema_create._TABLES / _COLUMN_ADDS / _INDEXES` | 幂等 DDL, 重复执行无副作用 |
| 改存量数据 / 换约束 / 合并重复行 | **`migrations/steps/*.py`** | 有"改数据"或"不可重复执行"的动作 |

旧做法把这类步骤内联在 `create_all_tables()` 里, 结果是: 一次 schema 版本升级会把
**全部历史步骤重跑一遍** —— 每一步都靠自己 `INSERT IGNORE migration_log` 自守,
漏写一处就是重复执行(改数据类步骤重复跑可能把金额算两遍)。

## 机制

1. `_REGISTRY` 是有序列表, 每个 step 三项: `id` / `run(conn-less)` / `describe`;
2. 执行前先读 `schema_migrations` 已应用集合 → **只跑 id 不在集合里的**;
3. **单步事务**: `run()` 内的写与"登记已应用"在同一事务提交 —— 半途异常整步回滚,
   不会出现"改了数据但没登记"(下次重跑)或"登记了但没改"(永久缺口);
4. 一个失败**不阻断后续**: 后续步骤可能不依赖它, 而阻断会让整个 schema 版本卡住;
   失败的 step 不登记, 下次冷启动重试;
5. 加新步骤: 建 `steps/<日期>_<主题>.py` 导出 `run()`, 在 `_REGISTRY` 末尾追加一行。
   **不要修改已应用的 step** —— 线上和本地都可能已经跑过; 要改就加新 step。
"""
import importlib
import re


def _step(step_id, module, describe):
    """登记项(模块名延迟导入: 建表阶段 migrations/steps 可能还没就绪)"""
    return {"id": step_id, "module": module, "describe": describe}


# 顺序即执行顺序。新步骤一律追加在末尾, 不改中间项的 id。
_REGISTRY = [
    _step("20261029_rules_field_path", "rules_field_path",
          "规则字段路径 inv.days_since_last/inv.stock → 顶层"),
    _step("20261030_lowstock_sellable", "lowstock_sellable",
          "低库存条件补 in_transit + {avail}→{sellable}"),
    _step("20261030_channel_name", "channel_name",
          "渠道显示名 '京东' → '京东渠道'"),
    _step("20261030_lowstock_wording", "lowstock_wording",
          "低库存文案 '可用 {sellable}' → '可售 {sellable}'"),
    _step("20261029_oversell_wording", "oversell_wording",
          "超卖告警文案改为'累计订单量超过可用库存'"),
    _step("20261101_products_expiry_managed", "products_expiry_managed",
          "效期管理标记存量推导(有批次或 best_before → yes)"),
    _step("20261104_legacy_log_sync", "legacy_log_sync",
          "历史 migration_log 标记归并到 schema_migrations(幂等)"),
]

_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def registry():
    return list(_REGISTRY)


def step_ids():
    return [s["id"] for s in _REGISTRY]


def load_run(step):
    """导入 step 的 run 函数; 导入失败抛异常(由调用方记为失败, 不阻断其它步)"""
    mod = importlib.import_module("migrations.steps." + step["module"])
    run = getattr(mod, "run", None)
    if not callable(run):
        raise RuntimeError("迁移步骤 %s 缺少 run()" % step["module"])
    return run


def validate():
    """静态自检: id 合法且不重复; 供 local_test 调用(不连库)"""
    ids = step_ids()
    bad = [i for i in ids if not _ID_RE.match(i)]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    return {"ids": ids, "bad": bad, "dup": dup}
