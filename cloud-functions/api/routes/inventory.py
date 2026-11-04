"""原生 inventory 路由(方案 B): 缺货清单 + 单条删除(契约与旧 backend 一致)

2026-11-02 删除可恢复: 用户定案「恢复删除 = 复原删除时的快照」→ 删除前整行进 inventory_archive
(保留原 id), 恢复 = 原样插回。归档表方案(而非 deleted_at 软删)的理由:
60 处读库存的 SQL 零改动、唯一键 uq_inventory_biz 不被"已删行"占位。
"""
from fastapi import APIRouter, Request

from db import query, one, execute, executemany
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["inventory"])

# 批量上限: 云函数 wall clock 120s 硬限, 恢复逐条插入 + 冲突预取, 500 条实测 < 6s;
# 超过则让用户分批(不静默截断——截断会让"选了 800 只恢复 500"变成静默丢数据)
_BATCH_MAX = 500


# inventory 全列(与 schema_create 的 inventory DDL 一一对应; 归档表同列序 + archived_at)
_INV_ALL = ("id, sku, product_name, store, warehouse, warehouse_type, available_qty, in_transit_qty, "
            "c_transit, safety_qty, safety_days, locked_qty, beginning_stock, month_inbound, "
            "month_outbound, turnover_days, weight, volume, barcode, channel, deleted_at, ext_json")
_INV_FIELDS = _INV_ALL.split(", ")


@router.get("/inventory/out-of-stock")
@traced
def out_of_stock(channel: str = "jd", wh: str = "own", limit: int = 0):
    """缺货清单(维度与看板健康卡一致): wh=own/platform/platform_b/bc
    bc = platform + platform_b 按 SKU 合计 <=0(与 dashboard aux bcOutOfStock 同口径)
    limit>0 时截断(健康卡数据量大时防大响应, 默认全量兼容)"""
    _lim = (" LIMIT %d" % limit) if limit > 0 else ""
    if wh == "bc":
        # bc = B仓+全国C仓按 SKU 合计(bbcc 一盘棋口径); warehouse 列给实际缺货仓集合
        # (前端弹窗/预览按数据 warehouse 列显示实际仓名, 非硬编码 "BC")
        # 2026-10-29 按 69码: BC 盘缺货按 (sku, 69码) 分组(原按 sku 合并, 两码库存互相抵消)
        rows = query(
            "SELECT sku, COALESCE(barcode,'') AS barcode, MAX(product_name) AS product_name, "
            "'bc' AS warehouse_type, SUM(available_qty) AS available_qty, "
            "GROUP_CONCAT(DISTINCT IF(available_qty<=0, warehouse, NULL) "
            "ORDER BY warehouse SEPARATOR ',') AS warehouse "
            "FROM inventory "
            "WHERE channel=%s AND warehouse_type IN ('platform','platform_b') "
            "GROUP BY sku, COALESCE(barcode,'') HAVING SUM(available_qty) <= 0 ORDER BY sku" + _lim, [channel])
        for _r in rows:
            _r["warehouse"] = str(_r.get("warehouse") or "") or "BC"
    else:
        from routes.common import INVENTORY_COLS as _IC
        rows = query(
            "SELECT " + _IC + " FROM inventory WHERE channel=%s AND warehouse_type=%s "
            "AND available_qty<=0 ORDER BY id DESC" + _lim, [channel, wh])
    return ok(rows)


@router.get("/inventory/sample")
def inventory_sample(channel: str = "jd"):
    """规则测试'用真实库存填充': 取该渠道一条真实库存样本(演示/调试真实数据下条件触发)"""
    return ok(one("SELECT sku, available_qty, safety_qty, in_transit_qty, warehouse_type, product_name "
                  "FROM inventory WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!='' "
                  "ORDER BY id LIMIT 1", [channel]) or {})


@router.delete("/inventory/{iid}")
@traced
def delete_inventory(iid: int):
    # 2026-11-02 删除可恢复: 快照与"行存在"合成一条 INSERT...SELECT(rowcount=0 即行不存在,
    #   且什么都没写), 再删原行 —— 比先 SELECT 再写少一次往返, 也不会出现"删了没存"
    _n = execute("INSERT INTO inventory_archive (" + _INV_ALL + ", archived_at) "
                 "SELECT " + _INV_ALL + ", NOW() FROM inventory WHERE id=%s", [iid])
    if not _n:
        return fail("库存行不存在: id=%s" % iid)
    execute("DELETE FROM inventory WHERE id=%s", [iid])
    from routes.analysis_cache import invalidate
    invalidate("inventory")
    return ok({"archived_id": iid})


@router.get("/inventory/archive")
@traced
def inventory_archive(channel: str = "jd", limit: int = 200):
    """已删除库存的快照列表(供恢复页/接口消费); limit<=0 全量"""
    _lim = (" LIMIT %d" % int(limit)) if limit and limit > 0 else ""
    rows = query("SELECT " + _INV_ALL + ", archived_at FROM inventory_archive "
                 "WHERE channel=%s ORDER BY archived_at DESC, id DESC" + _lim, [channel])
    return ok(rows)


@router.post("/inventory/{iid}/restore")
@traced
def restore_inventory(iid: int):
    """恢复删除: 把归档行原样插回(原 id + 删除时的数量), 归档行移除

    拦两种冲突(都不动原数据, 让用户先决策):
    ① 原id已被新行占用(自增一般不复用, 防御);
    ② 删除后同业务键已重新导入(唯一键 uq_inventory_biz 会撞) —— 恢复会插出重复业务键。
    """
    row = one("SELECT * FROM inventory_archive WHERE id=%s", [iid])
    if not row:
        return fail("归档行不存在: id=%s" % iid)
    _same_id = one("SELECT id FROM inventory WHERE id=%s", [iid])
    if _same_id:
        return fail("库存表已存在 id=%s(自增不应复用, 数据异常), 恢复中止" % iid)
    _dup = one("SELECT id FROM inventory WHERE sku=%s AND COALESCE(barcode,'')=%s "
               "AND warehouse=%s AND channel=%s AND warehouse_type=%s AND id<>%s",
               [row.get("sku"), row.get("barcode") or "", row.get("warehouse") or "",
                row.get("channel") or "", row.get("warehouse_type") or "own", iid])
    if _dup:
        return fail("该业务键(sku×69码×仓)已重新导入为新行 id=%s, 恢复会重复——请先处理新行"
                    % _dup.get("id"))
    execute("INSERT INTO inventory (" + _INV_ALL + ") VALUES ("
            + ",".join(["%s"] * len(_INV_FIELDS)) + ")",
            [row.get(_c) for _c in _INV_FIELDS])
    execute("DELETE FROM inventory_archive WHERE id=%s", [iid])
    from routes.analysis_cache import invalidate
    invalidate("inventory")
    return ok({"restored_id": iid})


def _biz_key(r):
    """库存业务键 uq_inventory_biz: (sku, 69码, 仓, 渠道, 仓型)

    空值归一成 '' —— 与单条 restore 的 COALESCE(barcode,'') 判定保持同一口径,
    否则 'NULL' 与 '' 会被当成两个键, 放过本该拦截的冲突。
    """
    return (r.get("sku") or "", r.get("barcode") or "", r.get("warehouse") or "",
            r.get("channel") or "", r.get("warehouse_type") or "own")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


@router.post("/inventory/batch")
@traced
async def inventory_batch(request: Request):
    """批量删除 / 批量恢复(归档快照).

    {action: "delete"|"restore", ids: [int]}

    与订单批量的**本质差异**(订单是 UPDATE deleted_at 一把梭, 这里不能照搬):
    ① 删除无冲突判定 → 可以整批 INSERT...SELECT + DELETE, 2 条 SQL 搞定;
    ② 恢复有业务键冲突(删除后同键又被导入) → **必须逐条判定**, 且要返回明细,
       让前端能说清"恢复了 3 条, 2 条被拦(原因)", 而不是整批炸掉或静默丢。
    ③ 冲突判定用**预取**而非逐条查: 先一次性取回候选 SKU 的现存行做内存比对,
       把 N 次往返压成常数次(否则 100 条要 200 次往返 ≈ 11s, 逼近 120s 硬限)。
    """
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
        try_err('inventory', 'batch 请求体解析失败', _e)
    action = (d.get("action") or "").strip()
    ids = []
    for _x in (d.get("ids") or []):
        try:
            ids.append(int(_x))
        except (TypeError, ValueError):
            pass
    ids = list(dict.fromkeys(ids))  # 去重, 保序
    if action not in ("delete", "restore") or not ids:
        return fail("参数无效: 需 action(delete|restore) 与 ids")
    if len(ids) > _BATCH_MAX:
        return fail("单次最多 %d 条(本次 %d), 请分批" % (_BATCH_MAX, len(ids)))

    if action == "delete":
        _archived, _missing = 0, []
        for _b in _chunks(ids, 200):
            _ph = ",".join(["%s"] * len(_b))
            # 先查存在行(区分"本来就不存在" vs "删成功"), 再归档再删 ——
            # 不能靠归档表回查: 历史归档行会让已删过的 id 被误判成本次删成功
            _live = [r["id"] for r in query("SELECT id FROM inventory WHERE id IN (%s)" % _ph, _b)]
            _missing += [i for i in _b if i not in set(_live)]
            if not _live:
                continue
            _ph2 = ",".join(["%s"] * len(_live))
            execute("INSERT INTO inventory_archive (" + _INV_ALL + ", archived_at) "
                    "SELECT " + _INV_ALL + ", NOW() FROM inventory WHERE id IN (%s)" % _ph2, _live)
            execute("DELETE FROM inventory WHERE id IN (%s)" % _ph2, _live)
            _archived += len(_live)
        if _archived:
            from routes.analysis_cache import invalidate
            invalidate("inventory")
        return ok({"deleted": _archived, "missing": _missing})

    # ── restore ──
    _rows = []
    for _b in _chunks(ids, 200):
        _ph = ",".join(["%s"] * len(_b))
        _rows += query("SELECT * FROM inventory_archive WHERE id IN (%s)" % _ph, _b)
    if not _rows:
        return fail("归档行不存在(可能已被恢复或清理)")

    _got = {r["id"] for r in _rows}
    _failed = [{"id": _i, "reason": "归档行不存在"} for _i in ids if _i not in _got]

    # 预取①: 原 id 是否已被占用
    _taken = set()
    for _b in _chunks([r["id"] for r in _rows], 500):
        _ph = ",".join(["%s"] * len(_b))
        _taken |= {r["id"] for r in query(
            "SELECT id FROM inventory WHERE id IN (%s)" % _ph, _b)}

    # 预取②: 业务键是否已重新导入 —— 只查候选 SKU, 不做全表扫
    _exist = set()
    _skus = list({(r.get("sku") or "") for r in _rows if r.get("sku")})
    for _b in _chunks(_skus, 500):
        _ph = ",".join(["%s"] * len(_b))
        for _r in query(
                "SELECT COALESCE(sku,'') AS sku, COALESCE(barcode,'') AS barcode, "
                "COALESCE(warehouse,'') AS warehouse, COALESCE(channel,'') AS channel, "
                "COALESCE(warehouse_type,'own') AS warehouse_type "
                "FROM inventory WHERE sku IN (%s)" % _ph, _b):
            _exist.add(_biz_key(_r))

    _ok_rows, _seen = [], set()
    for _r in _rows:
        _iid = _r.get("id")
        if _iid in _taken:
            _failed.append({"id": _iid, "reason": "库存表已存在同 id(数据异常)"})
            continue
        _k = _biz_key(_r)
        if _k in _exist:
            _failed.append({"id": _iid, "reason": "同业务键(sku×69码×仓)已重新导入, 恢复会重复"})
            continue
        if _k in _seen:  # 同批两条归档行互相撞键
            _failed.append({"id": _iid, "reason": "同批存在相同业务键, 仅恢复第一条"})
            continue
        _seen.add(_k)
        _ok_rows.append(_r)

    _restored = 0
    if _ok_rows:
        try:
            executemany(
                "INSERT INTO inventory (" + _INV_ALL + ") VALUES ("
                + ",".join(["%s"] * len(_INV_FIELDS)) + ")",
                [[_r.get(_c) for _c in _INV_FIELDS] for _r in _ok_rows])
            _restored = len(_ok_rows)
            for _b in _chunks([r["id"] for r in _ok_rows], 500):
                _ph = ",".join(["%s"] * len(_b))
                execute("DELETE FROM inventory_archive WHERE id IN (%s)" % _ph, _b)
        except Exception as _e:
            # 整批写入失败(唯一键/长度等) → 回退逐条, 让能恢复的恢复, 不整批回滚
            try_err('inventory', 'batch restore executemany 回退逐条', _e)
            _restored, _ok2 = 0, []
            for _r in _ok_rows:
                try:
                    execute("INSERT INTO inventory (" + _INV_ALL + ") VALUES ("
                            + ",".join(["%s"] * len(_INV_FIELDS)) + ")",
                            [_r.get(_c) for _c in _INV_FIELDS])
                    execute("DELETE FROM inventory_archive WHERE id=%s", [_r["id"]])
                    _restored += 1
                    _ok2.append(_r["id"])
                except Exception as _e2:
                    try_err('inventory', 'batch restore 单条 id=%s' % _r.get("id"), _e2)
                    _failed.append({"id": _r["id"], "reason": "写入失败(唯一键或数据异常)"})

    if _restored:
        from routes.analysis_cache import invalidate
        invalidate("inventory")
    return ok({"restored": _restored, "failed": _failed})
