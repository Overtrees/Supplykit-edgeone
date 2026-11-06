"""告警生命周期单一入口(2026-11-03 D8 根治)

问题不是"少了两列", 而是三个机制缺失:

1. **关闭无原因**: 11 个入口各自 `UPDATE alerts SET status='inactive'`, 事后无法回答
   "这条为什么消失了"。多入口竞争(孤儿清理 vs 规则停用 vs 恢复关闭)时无法定责。
2. **关了又开 = 插新行**: 写入侧是"先查 active 去重, 没有再 INSERT"。告警被关后再次命中,
   会插入一条全新行 —— 同一维度(渠道/SKU/69码/仓)在表里反复增殖, "抖动"既看不见也数不清。
3. **维度键散落**: 去重键在 core/rules.py 写一遍、关闭条件在各路由再写一遍, 少一维就误关
   (典型: 按 SKU 关掉所有仓的告警, 而实际只有 B 仓恢复)。

本模块把三件事钉成单一真源:
  · ALERT_DIM       维度键 —— 与库存/批次四维对账同构: 渠道 + SKU + 69码 + 仓库(+ 仓型)
  · close()         任何关闭都写 closed_reason / closed_at; 条件参数化, 不做字符串拼接
  · open_or_reopen() 写入前查同维度最近一行: 已关闭则**重开**(reopen_count+1), 而不是新增行

约定: reason 是稳定枚举(≤32 字符), 前端/排障/SQL 统计都依赖它, 不要写自由文本。
"""
from db import query, execute

# 维度键(与四维对账同构): 告警属于哪一"格"库存。warehouse_type 不参与身份 ——
# bc 合计行与 own 明细行可能同 SKU 同仓名, 但仓型是判定产物, 不是身份。
ALERT_DIM = ("alert_type", "related_sku", "warehouse", "channel", "barcode")

# 关闭原因枚举(新增必须同步 docs/DEVELOPMENT.md, 否则排障时又变成"猜")
REASONS = {
    "rule_unhit": "规则重评估未命中",
    "eval_unhit": "全量评估未命中",
    "ls_orphan_no_demand": "低库存孤儿(无订单且无在档)",
    "oversell_cleared": "超卖已解除",
    "orphan_rule_missing": "规则已删除/停用, 存量告警成孤儿",
    "legacy_sku_level": "旧 SKU 级告警迁移清理",
    "no_demand": "无未删订单且无在档",
    "rule_disabled": "规则被停用",
    "rule_deleted": "规则被删除",
    "rule_alert_off": "该规则的告警开关被关闭",
    "purchase_converted": "已转采购单(采购需求闭环)",
    "seed_reset": "种子数据重置",
    "manual": "人工关闭",
}


def _dim_where(dim):
    """维度 dict → (SQL 片段, 参数)。空值统一成 ''(表里 warehouse/barcode 默认 '' 而非 NULL)"""
    parts, params = [], []
    for k in ALERT_DIM:
        if k not in dim:
            continue
        parts.append("`%s`=%%s" % k)
        v = dim.get(k)
        params.append("" if v is None else str(v))
    return (" AND ".join(parts) if parts else "1=1"), params


def close(cond="1=1", params=None, reason="manual", status="inactive", limit=0):
    """关闭满足条件的 active 告警 —— **所有关闭入口必须走这里**

    cond/params: 参数化的 WHERE 片段(可含 NOT EXISTS 子查询; 不要用表别名, SET 侧不需要别名)
    limit: >0 时限制条数(按 id 升序), 用于"只关前 N 条"的批处理
    返回受影响行数。
    """
    if reason not in REASONS:
        reason = "manual"
    sql = ("UPDATE alerts SET status=%s, closed_reason=%s, closed_at=NOW() "
           "WHERE status='active' AND (" + cond + ")")
    if limit:
        sql += " ORDER BY id LIMIT %d" % int(limit)
    return execute(sql, [status, reason] + list(params or []))


def close_by_ids(ids, reason="manual", status="inactive"):
    """按主键批量关闭(评估未命中场景: 命中集合已在内存里算好, 不必再拼维度条件)"""
    n = 0
    _ids = [int(x) for x in (ids or [])]
    for i in range(0, len(_ids), 200):
        b = _ids[i:i + 200]
        if not b:
            continue
        n += int(execute(
            "UPDATE alerts SET status=%s, closed_reason=%s, closed_at=NOW() "
            "WHERE status='active' AND id IN (%s)" % ("%s", "%s", ",".join(["%s"] * len(b))),
            [status, reason] + b) or 0)
    return n


def find_dim(dim, statuses=("active",)):
    """查同维度告警(最近一条优先)。返回 dict 或 None"""
    w, p = _dim_where(dim)
    _ph = ",".join(["%s"] * len(statuses))
    rows = query("SELECT id, status, reopen_count FROM alerts WHERE %s AND status IN (%s) "
                 "ORDER BY id DESC LIMIT 1" % (w, _ph), p + list(statuses))
    return rows[0] if rows else None


def open_or_reopen(dim, fields):
    """写入侧唯一入口: 单语句 upsert(2026-11-06 S2-4, 依赖 alerts.uq_alerts_dim)

    dim    : ALERT_DIM 的键值(alerts 表列名)
    fields : 其余列(title/description/severity/source/related_rule_id/warehouse_type...)
    返回 "opened"(新插入) / "reopened"(命中已有行) / "exists"(命中且一字未改)

    为什么从"先查后改"换成 upsert:
      · 原实现 `SELECT ... ORDER BY FIELD(status,'active') DESC LIMIT 1` 再决定
        INSERT/UPDATE —— 这是 check-then-act, 两个并发写入方各自读到"不存在"就各插一条。
        线上实证: purchase_need 同一 SKU 13 行, 全部 inactive(一次分页重算写一遍)。
      · 唯一键 uq_alerts_dim 把"同一维度只有一行"变成库约束, 并发怎么写都只有一行。
      · 同时消掉一次全表预载(评估路径原本还要另读一次 active 全量)。

    ⚠ 赋值顺序: `reopen_count` / `created_at` 必须在 `status='active'` **之前**。
      ON DUPLICATE KEY UPDATE 从左到右执行, 后面表达式读到的是已赋过值的列 ——
      反过来写的话 `IF(status<>'active',1,0)` 永远为 0, 重开次数再也不增长。

    为什么仍要重开而不是插新行:
      · 同一格库存的同一个问题只有一条"当前状态", 历史由 quality_logs 承载;
      · 不重开的话每次"恢复→再触发"都新增一行, 表按抖动次数膨胀, 且无法统计抖动频次。
    """
    cols = list(dim.keys()) + [k for k in fields.keys() if k in _WRITABLE]
    vals = [("" if dim[k] is None else str(dim[k])) for k in dim.keys()] + \
           [fields[k] for k in fields if k in _WRITABLE]
    sets = [
        # 这两行必须在 status 之前(见 docstring)
        "reopen_count=COALESCE(reopen_count,0)+IF(status<>'active',1,0)",
        "created_at=IF(status<>'active', NOW(), created_at)",
    ]
    sets += ["`%s`=VALUES(`%s`)" % (k, k) for k in fields.keys() if k in _WRITABLE]
    sets += ["closed_reason=''", "closed_at=NULL", "status='active'"]
    n = int(execute(
        "INSERT INTO alerts(%s) VALUES(%s) ON DUPLICATE KEY UPDATE %s" % (
            ", ".join("`%s`" % c for c in cols),
            ", ".join(["%s"] * len(cols)),
            ", ".join(sets)),
        vals) or 0)
    # ODKU 的 rowcount: 1=插入, 2=更新, 0=命中但值未变
    if n == 1:
        return "opened"
    return "reopened" if n >= 2 else "exists"


# 可写入列白名单 —— 防止调用方把 status/closed_* 混进 fields 绕过状态机
_WRITABLE = {"title", "description", "severity", "source", "related_rule_id",
             "warehouse_type", "related_order_no", "pushed"}
