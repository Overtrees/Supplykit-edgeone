"""原生 inventory 路由(方案 B): 缺货清单 + 单条删除(契约与旧 backend 一致)

2026-11-02 删除可恢复: 用户定案「恢复删除 = 复原删除时的快照」→ 删除前整行进 inventory_archive
(保留原 id), 恢复 = 原样插回。归档表方案(而非 deleted_at 软删)的理由:
60 处读库存的 SQL 零改动、唯一键 uq_inventory_biz 不被"已删行"占位。
"""
from fastapi import APIRouter

from db import query, one, execute
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["inventory"])

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
