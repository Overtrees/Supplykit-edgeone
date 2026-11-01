"""运维端口(admin only, 2026-11-01): 一次性数据治理 —— 不进前端、不参与业务流程

为什么单独一个模块: 这类操作**不是** seed(不重建演示数据), 也**不是**业务导入(不来自客户文件),
是"代码口径变了之后, 把历史派生数据对齐到新口径"。混进 seed 会被误当成可随手重跑的动作。

当前提供:
  POST /ops/snapshot-barcode   快照 69码 归一: 空码行按 products.barcode 回填, 撞唯一键则合并 order_count
"""
from fastapi import APIRouter, Request

from db import query, one, transaction
from routes.common import ok, fail, traced, try_err

router = APIRouter(prefix="/ops", tags=["ops"])


# ── 快照 69码 归一的 SQL(按 date 分片, 每片一个事务) ─────────────────────────
# 幂等性说明: 三步必须同事务完成 —— 若"搬运"成功而"删除"失败, 重跑会把 order_count
#   再加一遍(双计)。事务保证要么三步全成、要么全回滚, 这是本函数唯一的正确性依赖。
_SQL_MOVE = (
    "INSERT INTO daily_sales_snapshot(date, channel, sku, barcode, warehouse, order_count) "
    "SELECT s.date, s.channel, s.sku, p.barcode, s.warehouse, s.order_count "
    "FROM daily_sales_snapshot s JOIN products p ON p.sku=s.sku "
    "WHERE COALESCE(s.barcode,'')='' AND COALESCE(p.barcode,'')<>'' "
    "  AND s.date >= %s AND s.date < %s"
)
# 撞唯一键(date,channel,sku,barcode,warehouse) → 累加, 不覆盖(销量是求和量, 覆盖会丢数)
# 必须限定目标表名: 裸写 order_count 在 INSERT...SELECT 下与来源表同名列歧义
#   → TiDB 1052 "Column 'order_count' in field list is ambiguous"(已 EXPLAIN 实测)
_SQL_MOVE_UPSERT = _SQL_MOVE + (" ON DUPLICATE KEY UPDATE "
                                "order_count = daily_sales_snapshot.order_count + VALUES(order_count)")
# 搬到码行之后, 把这些空码行删掉(否则下一步改码会撞唯一键)
_SQL_DROP_EMPTY = (
    "DELETE s FROM daily_sales_snapshot s JOIN products p ON p.sku=s.sku "
    "WHERE COALESCE(s.barcode,'')='' AND COALESCE(p.barcode,'')<>'' "
    "  AND s.date >= %s AND s.date < %s"
)


def _snapshot_bc_stat(channel=None):
    """迁移前后对比用的统计: 总行数 / 空码行 / 其中"档案有码可归"的行"""
    _w, _p = "", []
    if channel:
        _w, _p = " WHERE s.channel=%s", [channel]
    _w2 = (_w + " AND " if _w else " WHERE ") + "COALESCE(s.barcode,'')=''"
    r = one("SELECT COUNT(*) AS c, SUM(COALESCE(s.barcode,'')='') AS e "
            "FROM daily_sales_snapshot s" + _w, _p) or {}
    r2 = one("SELECT COUNT(*) AS c FROM daily_sales_snapshot s JOIN products p ON p.sku=s.sku"
             + _w2 + " AND COALESCE(p.barcode,'')<>''", _p) or {}
    return {"rows": int(r.get("c") or 0), "empty_barcode": int(r.get("e") or 0),
            "empty_fixable": int(r2.get("c") or 0)}


def migrate_snapshot_barcode(channel=None, dry_run=True, day_step=15):
    """快照 69码 归一(幂等): 空码行 → 档案码; 撞键则 order_count 相加后删空码行

    背景: daily_sales_snapshot 的唯一键含 barcode。历史上入口未补码时, 同一 SKU 同仓
    会同时存在"空码行"和"有码行"两套 —— 日销在某个时段读空码、某个时段读有码, 两边都不完整。
    入口补码(_fill_barcode_from_products)只管新数据, 存量必须迁移这一次。

    为什么按 date 分片: 单条 UPDATE 全表会长时间持锁, 且 TiDB 单事务过大易失败;
    每片 15 天 ≈ 数万行, 单事务可控, 失败只回滚该片。
    """
    before = _snapshot_bc_stat(channel)
    if dry_run:
        return {"dry_run": True, "before": before, "after": None,
                "note": "未写入; 传 dry_run=false 才执行"}
    # 覆盖快照的全部日期区间(按月边界切片, 避免依赖 MAX(date) 的时区歧义)
    _r = one("SELECT MIN(`date`) AS d0, MAX(`date`) AS d1 FROM daily_sales_snapshot"
             + (" WHERE channel=%s" if channel else ""), [channel] if channel else [])
    if not _r or not _r.get("d0"):
        return {"dry_run": False, "before": before, "after": before, "note": "快照为空"}
    from datetime import date as _d, timedelta as _td
    d0 = _r["d0"] if isinstance(_r["d0"], _d) else _d.fromisoformat(str(_r["d0"])[:10])
    d1 = _r["d1"] if isinstance(_r["d1"], _d) else _d.fromisoformat(str(_r["d1"])[:10])
    moved, dropped, slices = 0, 0, 0
    cur = d0
    while cur <= d1:
        nxt = cur + _td(days=day_step)
        try:
            with transaction() as c:
                _c = c.cursor()
                _c.execute(_SQL_MOVE_UPSERT, (cur.isoformat(), nxt.isoformat()))
                moved += max(_c.rowcount, 0)
                _c.execute(_SQL_DROP_EMPTY, (cur.isoformat(), nxt.isoformat()))
                dropped += max(_c.rowcount, 0)
                _c.close()
            slices += 1
        except Exception as _e:
            try_err('ops', '快照归码分片失败(%s~%s, 已回滚该片)' % (cur, nxt), _e)
        cur = nxt
    after = _snapshot_bc_stat(channel)
    return {"dry_run": False, "before": before, "after": after,
            "slices": slices, "upserted": moved, "empty_dropped": dropped,
            "hint": "物化表 sku_sales_stats 需重建后才能反映新归码口径(接口已自动触发)" if dropped or moved else "无需迁移"}


@router.post("/snapshot-barcode")
@traced
async def ops_snapshot_barcode(request: Request):
    """快照 69码 归一(admin 运维口) —— 不进前端, 不参与业务流程

    调用:
      curl -X POST <host>/api/ops/snapshot-barcode -H 'X-Auth-Token: <admin>' \\
           -d '{"dry_run": true}'                        # 先看影响面
      curl -X POST <host>/api/ops/snapshot-barcode -H 'X-Auth-Token: <admin>' \\
           -d '{"channel": "jd", "dry_run": false}'      # 再执行

    默认 dry_run=true: 这个接口会删行, 默认执行才是危险的。
    """
    from routes.common import verify_token
    if verify_token(request.headers.get("X-Auth-Token", "")) != "admin":
        return fail("仅 admin 可用", 403)
    try:
        try:
            d = await request.json()
        except Exception:
            d = {}   # 空 body / 非 JSON: 全部走默认(dry_run=true)
        _ch = (d.get("channel") or "").strip() or None
        _dry = d.get("dry_run")
        _dry = True if _dry is None else bool(_dry)
        res = migrate_snapshot_barcode(_ch, dry_run=_dry)
        if not _dry and res.get("upserted"):
            # 归码改变了日销的按码/合计分布 → 物化必须重建, 否则断货/补货仍读旧切片
            try:
                from biz.sales import _rebuild_sku_sales_stats
                for _c2 in ([_ch] if _ch else ["jd", "other"]):
                    res.setdefault("sales_stats", {})[_c2] = _rebuild_sku_sales_stats(_c2, days=90)
                # 2026-11-01 重建后必须失效聚合缓存: analysis_cache 存在 TiDB 表里、跨实例存活,
                #   只重建表不清缓存 → 接口在 TTL 内继续返回旧切片(实测: 物化已是 1.071,
                #   采购接口仍回 8.6 的合计值, 直到手动 invalidate_all 才对上)。
                from routes.analysis_cache import invalidate_all
                invalidate_all()
            except Exception as _e:
                res["sales_stats_error"] = str(_e)[:200]
        return ok(res)
    except Exception as e:
        return fail("快照归码失败: %s" % str(e)[:300])

