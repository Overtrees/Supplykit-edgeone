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
import json


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


# ── v3: 全量分块持久化(2026-11-06 S2-2 审计 P0-17) ──────────────────────
#
# v2(每页一条)解决了 8025, 但留了一个结构性浪费: **换 page_size 或改 search 就全部 miss**。
# 而 builder 一次要 4~7s —— 用户拖一下"每页 100 条"、或者搜个关键字, 就要再等 7 秒。
# 分页键的爆炸面 = 页数 × page_size 档位 × 搜索词个数, 缓存条数随使用方式膨胀,
# 而它们背后是**同一份全量结果**。
#
# v3 把"全量结果"按字节切块持久化(meta + chunk0..N), 页级缓存变成它上面的一层薄壳:
#
#     请求 ──> 页级缓存(40KB, 命中即回)          ← 常态路径, 零反序列化成本
#                └─miss─> 全量分块(6MB, 4 块)     ← 只算一次, 任意页/搜索都能命中
#                            └─miss─> builder     ← 4~7s, 一天只有几次
#
# 为什么"全量一条缓存"不行(审计报告的建议): 实测 purchase 全量 6,222,602 B,
#   TiDB 单条上限 6,291,456 B, **余量只有 69KB**; 补货 6,835,645 B 已经超了。
#   一条缓存 = 立刻 8025 = 回到原点(这正是 v1 踩过的坑)。分块是唯一同时满足
#   "只算一次"和"不撞上限"的做法。
_CHUNK_VER = "v3"


def _chunk_meta_key(base):
    return "%s|%s|meta" % (base, _CHUNK_VER)


def _chunk_key(base, idx):
    return "%s|%s|c%d" % (base, _CHUNK_VER, idx)


def _row_bytes(row):
    """单行的字节估算。用 json 而不是 len(str(row)): 前者与实际落库形态同构,
    后者会把 dict 的 repr 当成传输体积(引号/空格/True 大小写都不同)。"""
    try:
        return len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 256   # 估不出来按一个保守值算, 只影响切块位置, 不影响正确性


def split_chunks(rows, chunk_bytes=1024 * 1024):
    """按**序列化后字节**切块(不是按行数)。

    按行数切是错的: 采购建议每行 643 B, 补货有 note_segs 数组的行能到 4KB ——
    同样 1600 行, 前者 1MB、后者 6MB。按字节切才能保证"单条永不接近上限"。
    """
    chunks, cur, size = [], [], 0
    for r in rows:
        n = _row_bytes(r)
        if cur and size + n > chunk_bytes:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur or not chunks:
        chunks.append(cur)
    return chunks


def full_get_slice(base, full_builder, ttl, start, end, chunk_bytes=1024 * 1024):
    """只取全量中的第 [start, end) 行。返回 (rows, total); 无法定位时返回 (None, 0)。

    为什么需要(2026-11-06, 翻页体验):
      分块方案读一页要把 7 块 6.2MB 全部读回再切 —— 线上实测翻一页 5.1s,
      只比重算(7~11s)好一点。而一页 50 行、**一块约 1500 行**,
      真正需要的只是其中一块。meta 里记下每块的起始行号, 就能只读覆盖目标页的那一块。

    只在"没有 need_only / 没有 search"时可用: 那两者会改变行集合与顺序,
    必须先过一遍全量才知道第 N 页是哪几行。调用方(shape_cached)负责判断。

    meta 里没有 ranges(旧版写入的) → (None, 0), 调用方回退全量路径。
    """
    from routes.analysis_cache import cache_peek, cache_peek_many
    hit, meta = cache_peek(_chunk_meta_key(base), ttl)
    if not (hit and isinstance(meta, dict)):
        return None, 0
    ranges = meta.get("ranges")
    total = int(meta.get("rows") or 0)
    if not isinstance(ranges, list) or not ranges or total <= 0:
        return None, 0
    if start < 0 or end <= start or end > total:
        return None, 0
    need = []
    for i, rg in enumerate(ranges):
        try:
            s, e = int(rg[0]), int(rg[1])
        except Exception:
            return None, 0
        if e <= start:
            continue
        if s >= end:
            break
        need.append(i)
    if not need:
        return None, 0
    got = cache_peek_many([_chunk_key(base, i) for i in need], ttl)
    if len(got) != len(need):
        return None, 0
    rows = []
    for i in need:
        part = got.get(_chunk_key(base, i))
        if not isinstance(part, list):
            return None, 0
        rows.extend(part)
    base_row = int(ranges[need[0]][0])
    off_s, off_e = start - base_row, end - base_row
    if off_s < 0 or off_e > len(rows):
        return None, 0
    return rows[off_s:off_e], total


def full_get(base, full_builder, ttl, chunk_bytes=1024 * 1024):
    """取全量行(分块缓存; miss 才算)。返回 list。

    一致性: meta 在**所有 chunk 写完之后**才写 —— 反过来会出现"meta 说有 4 块,
    但第 3 块没写进去", 之后每次请求都拼不全 → 只能回退重算, 且永远修不好。
    这里宁可 meta 晚一步, 也不让它指向不存在的块。
    """
    from routes.analysis_cache import cache_get, cache_peek, cache_peek_many
    mk = _chunk_meta_key(base)
    hit, meta = cache_peek(mk, ttl)
    if hit and isinstance(meta, dict):
        n_chunks = int(meta.get("chunks") or 0)
        n_rows = int(meta.get("rows") or -1)
        if n_chunks > 0:
            # 一次 IN 查询把全部块取回 —— 逐块读实测 7 块要 4.5s(跨区域往返),
            #   批量后只有 1 次往返, 总字节不变。
            got = cache_peek_many([_chunk_key(base, i) for i in range(n_chunks)], ttl)
            if len(got) == n_chunks:
                rows = []
                for i in range(n_chunks):
                    part = got.get(_chunk_key(base, i))
                    if not isinstance(part, list):
                        rows = None
                        break
                    rows.extend(part)
                # 行数必须与 meta 对齐: 对不上说明块内容被换过(版本/口径变更),
                #   宁可重算也不返回半份数据 —— 半份会让 total 变小、分页看起来"少了"。
                if rows is not None and len(rows) == n_rows:
                    return rows
    rows = full_builder()
    if not isinstance(rows, list):
        rows = []
    chunks = split_chunks(rows, chunk_bytes)
    # 每块的起始行号 —— 供 full_get_slice 直接定位(翻页只读一块, 不是七块)
    ranges, _acc = [], 0
    for part in chunks:
        ranges.append([_acc, _acc + len(part)])
        _acc += len(part)
    for i, part in enumerate(chunks):
        # 每块留 64KB 余量: cache_get 的 max_bytes 比实际序列化体积略宽,
        # 免得因为"估算少了 3 个字节"整块被跳过(跳过就等于这份永远存不进去)
        cache_get(_chunk_key(base, i), ttl, lambda p=part: p, max_bytes=chunk_bytes + 65536)
    cache_get(mk, ttl, lambda: {"chunks": len(chunks), "rows": len(rows), "ranges": ranges},
              max_bytes=16384)
    return rows


def shape_cached(base, full_builder, cache_get, ttl, need_only=0, search="",
                 page=0, page_size=0, max_bytes=1024 * 1024,
                 need_keys=None, out_key="suggestions"):
    """两级缓存版: 页级(快) → 全量分块(只算一次) → builder(4~7s)

    :param base        : 基础缓存键(渠道/模式/天数)
    :param full_builder: 产出**全量**行的 builder(算法与旧版一致, 不动)
    :param cache_get   : `routes.analysis_cache.cache_get`
    :param max_bytes   : 页级单条上限。一页 50 条约 40KB, 给 1MB 足够;
                         超了宁可这条不落缓存, 也不去撞 TiDB 的 6MB。
    :param need_keys   : `need_only` 判定字段(采购 `actual_purchase` / 补货两个字段)
    :param out_key     : 列表字段名 —— 采购用 `suggestions`, 补货用 `items`。
                         不改调用方契约(前端各页各读各的键), 只换外壳名。

    顺序铁律: **先筛后切**。先按 need_only / search 收窄, 再切片; 反过来会导致
    "第 2 页只有 3 条"这类页码与总数对不上的错位。
    """
    from routes.analysis_cache import cache_peek

    sk = slice_key(base, need_only=need_only, search=search,
                   page=page, page_size=page_size)
    # ① 页级命中(常态路径): 只读一条 40KB, 不做任何反序列化再拼装
    hit, val = cache_peek(sk, ttl)
    if hit and isinstance(val, dict):
        return val

    # ② 页级 miss → 全量分块(命中则零计算) → 在内存里筛/切
    #
    # 2026-11-06 翻页快路: 只有"无 need_only、无 search、真的在分页"时, 第 N 页就是
    #   `全量[(N-1)*size : N*size]` —— 与行内容无关, 所以能按 meta 的行号区间只读一块。
    #   实测这一条把翻页从 5.1s 降到 ~1.5s(读 1MB 而不是 6.2MB)。
    #   其余情况(收窄/搜索/导出)必须过全量: 先筛再切是铁律, 不能按行号猜。
    if not need_only and not search and page > 0 and page_size > 0:
        _s = (page - 1) * page_size
        # base 的键里不含分页参数, full_get_slice 内部自己找块; 未命中会返回 (None, 0)
        _part, _total = full_get_slice(base, full_builder, ttl, _s, _s + page_size)
        if _part is not None and _total > 0:
            out = {out_key: _part, "total": _total, "page": page, "page_size": page_size}
            return cache_get(sk, ttl, lambda: out, max_bytes=max_bytes)

    rows = full_get(base, full_builder, ttl)
    items, total = shape(rows, need_only=need_only, search=search,
                         page=page, page_size=page_size, need_keys=need_keys)
    out = {out_key: items, "total": total, "page": page, "page_size": page_size}
    # ③ 回写页级(下次同参数直接命中; 超 max_bytes 就不落, 本次结果照常返回)
    return cache_get(sk, ttl, lambda: out, max_bytes=max_bytes)
