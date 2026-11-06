"""采购建议的"取数后再分页"层(2026-11-06 引入, 治队头阻塞)

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

改法: 缓存里仍存**全量**(保证"接着翻页"和导出口径一条不少), 只把**响应**按页切。
  · 切在服务端 = 出网字节降 45 倍(need_only) / 按页 50 条时降约 190 倍;
  · 缓存键一仍其旧 → 不改变任何业务口径, 不碰 `_build_purchase` 的算法;
  · `total` 用全量长度(计数与列表分离, 见六条口径第 4 条), 前端不受截断影响。

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


def filter_rows(rows, need_only=0, search=""):
    """按 need_only / search 收窄(与原路由内联实现逐字等价)"""
    out = rows
    if need_only:
        out = [r for r in out if _num(r, "actual_purchase") > 0]
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


def shape(rows, need_only=0, search="", page=0, page_size=0):
    """一步到位: 筛选 → 分页 → 返回 (items, total)"""
    return paginate(filter_rows(rows, need_only=need_only, search=search),
                    page=page, page_size=page_size)
