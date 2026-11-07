"""订单归档: 超期订单 → daily_stats(集合式, 按 id 区间分批, 每区间一个事务)

为什么重写(2026-11-06 S6/P0-11):
  原实现把**全部**超期订单 SELECT 进内存(含全字段), 然后**逐条** INSERT(一条一次往返),
  最后按 200 个 id 一批 DELETE。50 万行 = 50 万次往返, 单实例执行槽被占几十分钟,
  期间所有请求排队; 云函数 60s 上限一到就被切断 → "归档永远跑不完", 而它每天都会重来。

现在:
  · 每 N 万 id 一个区间, 区间内 1 条 `INSERT ... SELECT ... GROUP BY ... ON DUPLICATE`
    + 1 条 `DELETE`, 往返数 = 区间数(44 万行约 45 个区间), 内存只占聚合结果
  · INSERT 与 DELETE **同事务**: 要么都成要么都不成。重跑时已删订单不会被再 SELECT 到,
    因此天然幂等; 若"归档了但没删", 事务回滚, 不会出现重跑重复累加 daily_stats
  · 按 id 区间而不是按 ordered_at 游标: 订单会随时间**变成**超期, 用"上次扫到哪"的水位
    会漏掉 id 更小、之后才超期的订单。区间从 MIN(id) 起, 每天都能重新覆盖全部

判定式用 `ordered_at < cutoff`(而不是 `DATE(ordered_at) < cutoff`): 语义完全相同
(cutoff 是日期, 即当天 00:00), 但能让 `idx_orders_channel_ordered` 参与, 不把函数
包在列上。历史实现用 DATE() 包列, 每次都是全表扫。
"""
import os
from datetime import timedelta

from db import query, one, execute, transaction
from core.clock import now_cn
from routes.common import try_err

ARCHIVE_BATCH = int(os.environ.get("ARCHIVE_BATCH", "10000") or 10000)
# 保留期唯一真源: cron(写侧) 与 history(读侧切线) 必须读同一个值, 否则跨切线会重复/漏算。
#   可用环境变量覆盖(应急回滚到更长保留期, 不用改代码)。
ARCHIVE_KEEP_DAYS = int(os.environ.get("ARCHIVE_KEEP_DAYS", "180") or 180)

_INS_SQL = (
    "INSERT INTO daily_stats(`date`, channel, store, sku, order_status, gmv, order_count, quantity) "
    "SELECT DATE(ordered_at), COALESCE(channel,''), COALESCE(store,''), COALESCE(sku,''), "
    "       SUBSTR(COALESCE(order_status,''),1,10), "
    "       COALESCE(SUM(total_amount),0), COUNT(*), COALESCE(SUM(quantity),0) "
    "FROM orders WHERE id >= %s AND id <= %s AND ordered_at < %s "
    "  AND (deleted_at IS NULL OR deleted_at=''){ch} "
    "GROUP BY DATE(ordered_at), COALESCE(channel,''), COALESCE(store,''), COALESCE(sku,''), "
    "         SUBSTR(COALESCE(order_status,''),1,10) "
    "ON DUPLICATE KEY UPDATE gmv=gmv+VALUES(gmv), order_count=order_count+VALUES(order_count), "
    "quantity=quantity+VALUES(quantity)")

_DEL_SQL = ("DELETE FROM orders WHERE id >= %s AND id <= %s AND ordered_at < %s "
            "  AND (deleted_at IS NULL OR deleted_at=''){ch}")


def archive_orders(cutoff_days=ARCHIVE_KEEP_DAYS, channel=None, batch=None, dry_run=False):
    """把 `cutoff_days` 天前的订单归档进 daily_stats, 归档成功才删除 orders。

    2026-11-07 用户定案: 保留期 365 → **滚动半年**(180 天)。
      理由: 免费版 orders 441k 行/133MB 且持续膨胀, 订单页/导出的分页与 COUNT 都随行数
      线性涨; 半年前的明细已无运营价值, 而年度同比仍由 daily_stats(按天聚合)**完整保留**
      —— 归档只删明细, 不丢日汇总, history/yoy 口径不受影响。

    返回 {"archived": 删除行数, "stats_rows": 写入行数(插入+更新), "batches": 区间数,
          "cutoff": 切线日期, "aborted"?: 中止原因}
    dry_run=True 只统计各区间会归档多少行, 不写不删。
    """
    _b = max(int(batch or ARCHIVE_BATCH), 1000)
    _cut = (now_cn() - timedelta(days=int(cutoff_days))).strftime("%Y-%m-%d")
    _ch_sql = " AND channel=%s" if channel else ""
    _ch_arg = [channel] if channel else []

    _rng = one("SELECT MIN(id) AS lo, MAX(id) AS hi FROM orders "
               "WHERE ordered_at < %s AND (deleted_at IS NULL OR deleted_at='')" + _ch_sql,
               [_cut] + _ch_arg) or {}
    if not _rng.get("lo"):
        return {"archived": 0, "stats_rows": 0, "batches": 0, "cutoff": _cut}

    _lo, _hi = int(_rng["lo"]), int(_rng["hi"])
    # 区间是**闭区间** [a, z]: 用 `id > a` 会永远漏掉 MIN(id) 那一行。
    #   2026-11-06 线上实测: 6 条测试订单只归档了 5 条, 丢的正是 id 最小的那条
    #   (它是 MIN(id), 不满足 `id > MIN(id)`) —— 真实数据里就是"每天静默丢一单"。
    out = {"archived": 0, "stats_rows": 0, "batches": 0, "cutoff": _cut}
    _cur = _lo
    while _cur <= _hi:
        _a, _z = _cur, _cur + _b - 1
        _cur = _z + 1
        try:
            if dry_run:
                n = int((one("SELECT COUNT(*) AS c FROM orders WHERE id >= %s AND id <= %s "
                             "AND ordered_at < %s AND (deleted_at IS NULL OR deleted_at='')" + _ch_sql,
                             [_a, _z, _cut] + _ch_arg) or {}).get("c") or 0)
                out["archived"] += n
                out["batches"] += 1
                continue
            with transaction():
                _n_ins = execute(_INS_SQL.format(ch=_ch_sql), [_a, _z, _cut] + _ch_arg)
                _n_del = execute(_DEL_SQL.format(ch=_ch_sql), [_a, _z, _cut] + _ch_arg)
            out["stats_rows"] += int(_n_ins or 0)
            out["archived"] += int(_n_del or 0)
            out["batches"] += 1
        except Exception as _e:
            # 单区间失败 → 该区间事务已回滚(账单一致), 继续下一区间; 但把原因带回调用方。
            #   不中断整轮的理由: 区间之间彼此独立, 中止只会让后面的区间永远轮不到。
            try_err("cron", "归档区间 %s~%s 失败" % (_a, _z), _e)
            out.setdefault("failed_batches", 0)
            out["failed_batches"] += 1
            out.setdefault("error", str(_e)[:200])
    return out
