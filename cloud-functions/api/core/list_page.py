"""core/list_page.py —— 建议行「筛选 / 切页」纯函数（唯一真源）

2026-11-07 P0: 补货/采购的「读时全量现算 + TTL 300s 分块表缓存」已由
`core/list_store.py` 的**域指纹物化**整体替换（红线 2: 替换并删除旧路径, 不许并存兜底）。
本模块只保留筛选/切页纯函数, 两处共用:
  · `list_store.serve()` 的 fp 不命中降级路径（现算, 慢但正确）;
  · `list_store._row_vals()` 复用 `_num` 算 need 列（不复制筛选算法, 红线 9）。
物化读侧的 SQL WHERE 与 `filter_rows` 逐字等价（`INSTR` 对齐 Python `in`,
`LOWER()` 对齐 `.lower()`）, 见 `list_store._where`。

历史（v1 全量一条缓存 → v2 每页一条 → v3 全量分块）见 docs/history/2026-11-06.md:
那段演进解决的是「6MB 结果进不了 TiDB 单行」的**出网/缓存容量**问题, 但没解决
「TTL 到点 / 写操作打墓碑 → 下一个请求在请求路径重算」—— 后者才是本轮根治对象。
`split_chunks` / `full_get` / `full_get_slice` / `shape_cached` 四个函数随旧路径一并删除。
"""


def _num(row, key):
    """数值收敛: None/''/非数字一律当 0（与前端 `(x||0)` 同义, 不改变判定）"""
    v = row.get(key)
    if v is None or v == "":
        return 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def filter_rows(rows, need_only=0, search="", need_keys=None):
    """按 need_only / search 收窄。

    need_keys 默认 `("actual_purchase",)`（采购口径）; 补货传
    `("suggested_qty", "b_suggested")` —— 任一 >0 即"确实需要补"。
    这是**唯一**筛选真源: 物化读侧 SQL 与它逐字等价（见 list_store._where）。
    """
    out = rows
    if need_only:
        keys = need_keys or ("actual_purchase",)
        out = [r for r in out if any(_num(r, k) > 0 for k in keys)]
    if search:
        sq = str(search).lower()
        out = [r for r in out
               if sq in str(r.get("sku", "")).lower()
               or sq in str(r.get("product_name", "")).lower()
               or sq in str(r.get("barcode", "")).lower()]
    return out


def paginate(rows, page=0, page_size=0):
    """切页。page<=0 或 page_size<=0 → 保持全量（兼容导出/内部调用方）

    返回 (items, total)。total 永远是**收窄后**的总数, 不是当前页条数 ——
    卡面计数与列表可以不等（口径 4）, 但同一列表的 total 必须与 items 同源。
    """
    total = len(rows)
    if page and page_size and page > 0 and page_size > 0:
        start = (page - 1) * page_size
        return rows[start:start + page_size], total
    return rows, total


def shape(rows, need_only=0, search="", page=0, page_size=0, need_keys=None):
    """一步到位: 筛选 → 分页 → 返回 (items, total)

    :param need_keys: `need_only` 的判定字段名元组, 任一字段 >0 即保留。
    """
    # builder 契约护栏 —— 只有 list 能进筛。采购曾在"商品库为空"分支 return dict,
    # 于是这里迭代出 key 字符串, need_only 时对 str 调 `.get` → 500。契约违约时
    # 按空列表处理（前端语义就是"暂无需采购", 准确且不炸）。
    if not isinstance(rows, list):
        try:
            from routes.common import try_err
            try_err('list_page', 'builder 返回非 list(契约违约)——已按空列表处理',
                    TypeError(type(rows).__name__))
        except Exception:
            pass
        rows = []
    return paginate(filter_rows(rows, need_only=need_only, search=search, need_keys=need_keys),
                    page=page, page_size=page_size)


def slice_key(base, need_only=0, search="", page=0, page_size=0):
    """分片键（旧表缓存时代遗留, 仅供 test_audit 的键维度审计兼容）。

    物化路径已不使用它: 读侧按 (kind, scope, gen, seq) 直查, 筛选参数不参与键。
    保留是为了不打断既有审计断言; 新代码不要再用它拼缓存键。
    """
    return "%s|%d|%s|%d|%d" % (base, 1 if need_only else 0, str(search or "")[:64],
                               int(page or 0), int(page_size or 0))
