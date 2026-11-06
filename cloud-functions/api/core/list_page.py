"""采购建议的"分页 + 分片缓存"层(2026-11-06 引入, 治队头阻塞)

背景(实测, 线上 2026-11-06):
  `/insights/purchase?channel=jd` 一次返回 9,684 条 / **5.93MB**, 耗时 10s。
  其中 `actual_purchase > 0`(真正需要采购)的只有 **142 条 / 0.13MB** —— 冗余 45 倍;
  而前端首屏 `purchaseLimit` 默认只渲染 50 行。也就是说 99% 的字节是白传的。

为什么这会造成"页面加载不出"(不是连接池, 不是竞态):
  1. 5.93MB 响应体超过 TiDB 单条 entry 上限(6,291,456 B) → `cache_put` 抛 8025 →
     **这份结果永远进不了缓存**(线上 cache 表里根本没有 `purchase|` 键) → 每个请求都完整重算;
  2. 重算 + 序列化 + 6MB 出网在 EdgeOne 单实例上占满一个执行槽, 同一实例的其它请求排队
     (实测同一时段 summary 14s / aux 11s / stock-risk 23s, 而日志里 `连接池耗尽`/`PoolFull`/
     `Too many connections` 均为 0 —— 不是连接池问题)。
  3. 前端还要 `JSON.parse` 6MB 再 `slice(0, 50)`。

## 两版演进(第一版漏了什么, 必须记下来)

**v1(当日第一版, 错)**: 缓存里仍存**全量**, 只在**响应**按页切。
  → 出网字节确实降到 43KB(实测), 但 `cache_put` 存的仍是那 5.93MB 全量 →
  **依然抛 8025, 依然永远进不了缓存** → 每个请求还是完整重算, 耗时纹丝不动(实测 5~6s)。
  线上日志实证(13:29~13:30): `8025 entry too large` 与 `slow_request GET /insights/purchase`
  成对反复出现 —— 说明"只切响应"是治了症状没治因。
  教训: **要卡的是写进缓存的那一份, 不是发给前端的那一份。**

**v2(当前)**: 缓存键带上"签名 + 页号", **每页各自一条缓存**。
  · 单条 entry = 一页(page_size 条) → 天然远小于 6MB, 8025 消失, 缓存真正生效;
  · 全量"一次算完再切"的算法**完全不变** —— `_build_purchase` 一个字没动, 口径零漂移;
  · 翻页仍在同一份计算结果上切(同一次 builder 调用), 不重复建库查询;
  · `total` 永远是**收窄后**的总数(计数与列表分离, 见六条口径第 4 条)。

顺序铁律: **先筛后切**。先按 need_only / search 收窄, 再切片; 反过来会导致
"第 2 页只有 3 条"这类页码与总数对不上的错位。
"""


def _num(row, key):
    """数值收敛: None/''/非数字一律当 0(与前端 `(x||0)` 同义, 不改变判定)"""
    v = row.get(key)
    if v is None or v == "":
        return 0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def filter_rows(rows, need_only=0, search="", need_keys=None):
    """按 need_only / search 收窄(与原路由内联实现逐字等价)

    need_keys 默认 `("actual_purchase",)`(采购口径); 补货传
    `("suggested_qty", "b_suggested")` —— 任一 >0 即"确实需要补"。
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
    """切页。page<=0 或 page_size<=0 → 保持全量(兼容导出/内部调用方)

    返回 (items, total)。total 永远是**收窄后**的总数, 不是当前页条数 ——
    卡面计数与列表可以不等(口径 4), 但同一列表的 total 必须与 items 同源。
    """
    total = len(rows)
    if page and page_size and page > 0 and page_size > 0:
        start = (page - 1) * page_size
        return rows[start:start + page_size], total
    return rows, total


def shape(rows, need_only=0, search="", page=0, page_size=0, need_keys=None):
    """一步到位: 筛选 → 分页 → 返回 (items, total)

    :param need_keys: `need_only` 的判定字段名元组, 任一字段 >0 即保留。
        默认 `actual_purchase`(采购口径); 补货传 `("suggested_qty","b_suggested")`。
        显式传参而不是在各路由里各写一份 filter —— 免得两份筛选逻辑慢慢漂开。
    """
    # 2026-11-06 P1-4: builder 契约护栏 —— 只有 list 能进筛。
    #   采购曾在"商品库为空"分支 `return ok({...})`(dict), 于是这里迭代出的是 key 字符串,
    #   need_only 时对 str 调 `.get` → AttributeError → 500, 而且这个坏值会被写进
    #   analysis_cache, 300s 内所有请求反复复现。根因已在 purchase_calc 修掉(改回 `return []`),
    #   这里再挡一层: 契约违约时按空列表处理(前端语义就是"暂无需采购", 准确且不炸)。
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
    """分片缓存键: `<base>|<need_only>|<search>|<page>|<page_size>`

    search 参与键 → 换了关键字就是另一份缓存(不会串结果)。
    search 做长度上限, 防止超长串把键撑爆(cache 表 key 列有长度限制)。
    """
    return "%s|%d|%s|%d|%d" % (base, 1 if need_only else 0, str(search or "")[:64],
                               int(page or 0), int(page_size or 0))


def shape_cached(base, full_builder, cache_get, ttl, need_only=0, search="",
                 page=0, page_size=0, max_bytes=1024 * 1024,
                 need_keys=None, out_key="suggestions"):
    """**分片缓存**版: 每页各自一条缓存(单条远小于 6MB, 8025 不再发生)

    :param base        : 基础缓存键(渠道/模式/天数)
    :param full_builder: 产出**全量**行的 builder(算法与旧版一致, 不动)
    :param cache_get   : `routes.analysis_cache.cache_get`
    :param max_bytes   : 单条 entry 上限。一页 50 条约 40KB, 给 1MB 足够;
                         超了宁可这条不落缓存, 也不去撞 TiDB 的 6MB。
    :param need_keys   : `need_only` 判定字段(采购 `actual_purchase` / 补货两个字段)
    :param out_key     : 列表字段名 —— 采购用 `suggestions`, 补货用 `items`。
                         不改调用方契约(前端各页各读各的键), 只换外壳名。

    设计取舍: builder 仍是"全量一次算完再切"(不是 SQL 分页)。
    理由: 这两个算子里都有跨 SKU 的聚合/排序/关联, 改成 SQL 分页要重写整条
    计算链, 口径极易漂移; 而现在这条链**已经在 3~6s 内算完**, 真正的浪费是"每次重算"
    —— 分片缓存正好消掉它。等后续有需要再谈下推。
    """
    key = slice_key(base, need_only=need_only, search=search,
                    page=page, page_size=page_size)

    def _build():
        items, total = shape(full_builder(), need_only=need_only, search=search,
                             page=page, page_size=page_size, need_keys=need_keys)
        return {out_key: items, "total": total, "page": page, "page_size": page_size}

    return cache_get(key, ttl, _build, max_bytes=max_bytes)
