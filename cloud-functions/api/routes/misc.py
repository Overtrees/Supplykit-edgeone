"""原生辅助路由(方案 B): quality-logs / monitor"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter
from fastapi import Request

from db import query, one, execute
from routes.common import ok, fail, traced, try_err, SALES_SQL as _SALES_SQL

router = APIRouter(tags=["misc"])
from routes.debug import router as debug_router
router.include_router(debug_router)  # 2026-10-13 调试端点统一收口(debug.py)

# ── 日志收口分级白名单(2026-10-17 统一收敛: 曾 3 处重复定义漂移——export 多 detect_debug/business_error,
#    其余 2 处缺——且 5 个实际产生类型(cache_invalidate/cleansing_eval/config_history/migration/rule_action)
#    任何维度都看不到) ─────────────────────────────────────
_LOG_SCOPE_TYPES = {
    "user": ("risk_summary", "cron", "maint", "cleansing", "cleansing_eval", "task",
             "duplicate_order", "duplicate_sku", "format_error", "field_warning",
             "field_error", "mapping_info", "config_history", "rule_action"),
    "dev": ("api_error", "slow_request", "cache_error", "cache_invalidate", "quiet_error",
            "perf_debug", "detect_debug", "slow_alert", "frontend_error", "window_error",
            "unhandled_rejection", "component_error", "api_http_error", "business_error",
            "migration"),
}





@router.get("/quality-logs/export")
@traced
def export_quality_logs(date: str = "", scope: str = "", level: str = ""):
    """质量日志整日导出(Markdown, iPhone 日志分析式): 按天聚合全部日志成 md 文本, 便于留存/分享/离线分析
    级别分段 + 异常计数汇总 + 堆栈代码块; date 为空取今天(UTC); scope=user/dev/空 过滤类型; level 可选过滤"""
    _d, _count, _md = _export_md(date, scope, level)
    return ok({"date": _d, "count": _count, "scope": scope, "markdown": _md})


def _export_md(date="", scope="", level=""):
    """md 生成体(export/file 共用): 返回 (日期, 条数, markdown 文本)"""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _SCOPE_TYPES = _LOG_SCOPE_TYPES  # 2026-10-17 统一收敛(模块级常量)
    _d = date or _dt.now(_tz.utc).strftime("%Y-%m-%d")
    _next = (_dt.strptime(_d, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")
    where = " WHERE created_at >= %s AND created_at < %s"
    params = [_d + " 00:00:00", _next + " 00:00:00"]
    if scope in _SCOPE_TYPES:
        where += " AND log_type IN (%s)" % ",".join(["%s"] * len(_SCOPE_TYPES[scope]))
        params += list(_SCOPE_TYPES[scope])
    if level:
        where += " AND level=%s"
        params.append(level)
    rows = query("SELECT log_type, level, message, details, source, created_at FROM quality_logs"
                 + where + " ORDER BY id", params)

    from collections import Counter
    by_level = Counter((r.get("level") or "info") for r in rows)
    by_type = Counter((r.get("log_type") or "") for r in rows)
    lines = ["# 质量日志 · %s" % _d, ""]
    lines.append("> 共 %d 条 · 异常 %d / 警告 %d / 提示 %d"
                 % (len(rows), by_level.get("error", 0), by_level.get("warning", 0), by_level.get("info", 0)))
    lines.append("> 类型分布: " + " ".join("%s×%d" % (k, v) for k, v in by_type.most_common(8)))
    lines.append("")
    _LV = {"error": "异常", "warning": "警告", "info": "提示"}
    for _lvl in ("error", "warning", "info"):
        _seg = [r for r in rows if (r.get("level") or "info") == _lvl]
        if not _seg:
            continue
        lines.append("## %s (%d)" % (_LV.get(_lvl, _lvl), len(_seg)))
        for _r in _seg:
            _t = str(_r.get("created_at") or "")[:19].replace("T", " ")
            _ty = _r.get("log_type") or ""
            _src = _r.get("source") or ""
            lines.append("- `%s` **[%s]** `%s` (src=%s) — %s" % (_t, _lvl, _ty, _src, (_r.get("message") or "")[:200]))
            if _r.get("details"):
                lines.append("  ```")
                lines.append("  " + str(_r.get("details")).replace("\n", "\n  ")[:400])
                lines.append("  ```")
        lines.append("")
    return _d, len(rows), "\n".join(lines)


@router.get("/quality-logs/file")
@traced
def get_log_file(date: str = "", scope: str = ""):
    """日志文件(iPhone 式): 读 log_archives 归档(历史保留) → 无归档则实时生成并归档(懒生成, 任何日期点开即有)
    返回 {date, scope, count, markdown, archived}"""
    from datetime import datetime as _dt, timezone as _tz
    _d = date or _dt.now(_tz.utc).strftime("%Y-%m-%d")
    _arch = one("SELECT markdown, `count` FROM log_archives WHERE `date`=%s AND `scope`=%s", [_d, scope or "all"])
    if _arch and _arch.get("markdown"):
        # 2026-10-28 一致性修复: 归档是快照(旧), 明细可能已新增 —— 曾直接返回归档
        # → 列表显示 max(归档,明细)=842 但弹窗显示归档 664(不一致)
        # 修: 归档 count < 实时明细 count 时, 用实时内容覆盖(归档只在明细已清空时兜底)
        _live_cnt = 0
        try:
            _sc3 = (scope or "all")
            _sc_t = _sc3 if _sc3 in _LOG_SCOPE_TYPES else ""
            if _sc_t:
                _in3 = ",".join(["%s"] * len(_LOG_SCOPE_TYPES[_sc_t]))
                # ⚠️ 第二个占位符必须 %%；曾写 %s → 2 个占位符只给 1 个值 → TypeError
                # 被 except 吞 → _live_cnt 恒 0 → 永远走归档分支(修复形同虚设, 842 vs 664 复现)
                _r3 = one("SELECT COUNT(*) AS c FROM quality_logs WHERE log_type IN (%s) "
                          "AND DATE(created_at)=%%s" % _in3,
                          list(_LOG_SCOPE_TYPES[_sc_t]) + [_d])
            else:
                _r3 = one("SELECT COUNT(*) AS c FROM quality_logs WHERE DATE(created_at)=%s", [_d])
            _live_cnt = int((_r3 or {}).get("c") or 0)
        except Exception as _e:
            try_err('misc', '归档新鲜度检测降级', _e)
        if _live_cnt <= int(_arch.get("count") or 0):
            return ok({"date": _d, "scope": scope or "all", "count": _arch.get("count") or 0,
                       "markdown": _arch["markdown"], "archived": True})
        # 明细有新增 → 继续往下走实时生成, 并回写归档(下方 ON DUPLICATE 会更新)
    _d2, _cnt, _md = _export_md(_d, scope, "")
    try:
        execute("INSERT INTO log_archives(`date`, `scope`, markdown, `count`, updated_at) "
                "VALUES(%s,%s,%s,%s,NOW(6)) ON DUPLICATE KEY UPDATE markdown=VALUES(markdown), "
                "`count`=VALUES(`count`), updated_at=NOW(6)",
                [_d2, scope or "all", _md, _cnt])
    except Exception as _e:
        try_err('misc', '日志归档写入失败', _e)  # 归档失败不阻塞返回实时内容, 留痕
    return ok({"date": _d2, "scope": scope or "all", "count": _cnt, "markdown": _md, "archived": False})


@router.get("/quality-logs/files")
@traced
def list_log_files(days: int = 30, scope: str = ""):
    """日志文件列表(平铺): 归档表(历史保留) + 明细表实时日期(未归档标记 archived=False, 点击懒生成)
    每行 date/count/updated_at/archived —— 明细清理后归档文件仍保留(回溯)"""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _SCOPE_TYPES = _LOG_SCOPE_TYPES  # 2026-10-17 统一收敛(模块级常量, 防 3 处漂移)
    _sc = scope or "all"
    _cut = (_dt.now(_tz.utc) - _td(days=days)).strftime("%Y-%m-%d")
    # 1) 归档表(历史文件, 明细清理后仍在)
    arch = query("SELECT `date`, `count`, updated_at FROM log_archives WHERE scope=%s AND `date` >= %s "
                 "ORDER BY `date` DESC", [_sc, _cut])
    arch_map = {str(r.get("date") or "")[:10]: r for r in arch}
    # 2) 明细表近 N 天按日(未归档日期 → 实时生成标记)
    # 2026-10-16 修复: scope 空/未知/all 时 _SCOPE_TYPES.get 返回空元组 → IN () 语法错误(1064)
    # —— 降级为不过滤类型(全量明细), 语义=全部视图; user/dev 显式时仍按白名单
    _sc2 = _sc if _sc in _SCOPE_TYPES else ""
    # 注意 % 转义层级: 空分支不经过 Python % 预格式化 → 用单 %s(直接给 pymysql);
    # 有 scope 分支先 % _in 预格式化 → 用 %%s(% → %)
    _sql = ("SELECT DATE(created_at) AS d, COUNT(*) AS c FROM quality_logs "
            "WHERE created_at >= %s GROUP BY d ORDER BY d DESC")
    _params = [_cut + " 00:00:00"]
    if _sc2:
        _in = ",".join(["%s"] * len(_SCOPE_TYPES[_sc2]))
        _sql = ("SELECT DATE(created_at) AS d, COUNT(*) AS c FROM quality_logs "
                "WHERE log_type IN (%s) AND created_at >= %%s GROUP BY d ORDER BY d DESC") % _in
        _params = list(_SCOPE_TYPES[_sc2]) + [_cut + " 00:00:00"]
    _rows = query(_sql, _params)
    items = []
    seen = set()
    for r in _rows:
        _d = str(r.get("d") or "")[:10]
        _a = arch_map.get(_d)
        items.append({"date": _d,
                      # 2026-10-17 修复: 曾优先取归档 count(快照落后)——归档后新增日志不显示; 改 max(归档, 实时明细)=实际条数
                      "count": max(int((_a.get("count") if _a else 0) or 0), int(r.get("c") or 0)),
                      "updated_at": str(_a.get("updated_at") or "")[:19] if _a else "",
                      "archived": bool(_a)})
        seen.add(_d)
    # 3) 归档表有但明细已清(历史保留)——仍列出
    for _d2, _a2 in arch_map.items():
        if _d2 not in seen:
            items.append({"date": _d2, "count": _a2.get("count") or 0,
                          "updated_at": str(_a2.get("updated_at") or "")[:19], "archived": True})
    items.sort(key=lambda x: x["date"], reverse=True)
    return ok({"items": items[:days], "scope": _sc, "days": days})


@router.get("/quality-logs")
@traced
def list_quality_logs(channel: str = "", limit: int = 200, page: int = 0, page_size: int = 0, scope: str = ""):
    """质量日志: 默认返回最近 limit 条(兼容全局 loadAll 数组消费); 带 page/page_size 时分页 {items,total}
    scope: user=用户层(任务/维护/清洗/告警) / dev=开发者层(异常/慢请求/缓存/前端上报) / 空=全部"""
    # 日志收口分级: user=业务可理解 / dev=排查定位用(前后端统一, 2026-09-15)
    _SCOPE_TYPES = _LOG_SCOPE_TYPES  # 2026-10-17 统一收敛(模块级常量)
    where = ""
    params = []
    if scope in _SCOPE_TYPES:
        where = " WHERE log_type IN (%s)" % ",".join(["%s"] * len(_SCOPE_TYPES[scope]))
        params = list(_SCOPE_TYPES[scope])
    if page > 0 and page_size > 0:
        r = one("SELECT COUNT(*) AS c FROM quality_logs" + where, params) or {}
        total = int(r.get("c") or 0)
        rows = query("SELECT id, log_type, level, message, details, source, created_at FROM quality_logs "
                     + where + " ORDER BY id DESC LIMIT %s OFFSET %s", params + [page_size, (page - 1) * page_size])
        return ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    rows = query("SELECT id, log_type, level, message, details, source, created_at FROM quality_logs "
                 + where + " ORDER BY id DESC LIMIT %s", params + [limit])
    return ok(rows)


@router.post("/logs/frontend")
@traced
async def frontend_log(request: Request):
    """前端异常统一收口: JS 错误/未处理 Promise 拒绝/组件渲染错误/API HTTP 错误 → quality_logs(source=frontend)
    供开发者排查前端问题(用户层看不到); 限长防滥用"""
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('misc', '静默降级', _e)
    msg = (d.get("message") or "").strip()[:200]
    if not msg:
        return ok({})
    lt = (d.get("log_type") or "frontend_error")[:40]
    det = (d.get("details") or "")[:800]
    _url = (d.get("url") or "")[:200]
    try:
        execute("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES(%s,%s,%s,%s,'frontend')",
                (lt, d.get("level") or "error",
                 msg + (" @" + _url[-80:] if _url else ""), det))
    except Exception as _e:
            try_err('misc', '静默降级', _e)
    return ok({})


@router.get("/monitor")
@traced
def monitor():
    """APM(轻量实化, 2026-09-11 体检): quality_logs 实时聚合——错误/慢请求留痕即观测面
    (Makers 无状态请求环境, 无进程级 uptime/rps metrics; 慢路径与最新错误可下钻)"""
    from datetime import datetime, timezone as _tz
    err = one("SELECT COUNT(*) AS c FROM quality_logs WHERE level='error'") or {}
    slow = one("SELECT COUNT(*) AS c FROM quality_logs WHERE log_type='slow_request'") or {}
    today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
    err_today = one("SELECT COUNT(*) AS c FROM quality_logs "
                    "WHERE level='error' AND DATE(created_at)=%s", [today]) or {}
    slow_today = one("SELECT COUNT(*) AS c FROM quality_logs "
                     "WHERE log_type='slow_request' AND DATE(created_at)=%s", [today]) or {}
    slow_rows = query("SELECT message, created_at FROM quality_logs "
                      "WHERE log_type='slow_request' ORDER BY id DESC LIMIT 20") or []
    slowest = [{"path": (r.get("message") or "")[:120],
                "at": str(r.get("created_at") or "")[:19]} for r in slow_rows]
    latest_err = query("SELECT message, details, created_at FROM quality_logs "
                       "WHERE level='error' ORDER BY id DESC LIMIT 5") or []
    errs = [{"msg": (r.get("message") or "")[:150],
             "detail": (r.get("details") or "")[:200],
             "at": str(r.get("created_at") or "")[:19]} for r in latest_err]
    return ok({"totals": {"error": int(err.get("c") or 0), "slow": int(slow.get("c") or 0)},
               "today": {"error": int(err_today.get("c") or 0), "slow": int(slow_today.get("c") or 0)},
               "slowest_paths": slowest, "latest_errors": errs,
               "note": "无进程级 metrics(Makers 无状态), 以 quality_logs 留痕为观测面"})


@router.post("/db/diag")
@traced
async def db_diag(request: Request):
    """RU/执行计划测算(admin 专用, 只读): 免费额度数据驱动优化——EXPLAIN ANALYZE 拿真实扫描行数/耗时"""
    from routes.common import verify_token
    token = request.headers.get("X-Auth-Token", "")
    user = verify_token(token)
    if user != "admin":
        return fail("仅 admin 可用", 403)
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('misc', '静默降级', _e)
    sql = (d.get("sql") or "").strip()
    if not sql or len(sql) > 2000:
        return fail("sql 缺失或超长")
    if not sql.upper().lstrip().startswith(("EXPLAIN", "SELECT", "SHOW")):
        return fail("仅允许 EXPLAIN/SELECT/SHOW")
    import time as _t
    from db import query as _q
    t0 = _t.time()
    try:
        rows = _q(sql)
        return ok({"sql": sql[:200], "rows": rows[:40],
                   "elapsed_ms": round((_t.time() - t0) * 1000, 1)})
    except Exception as e:
        return fail("执行失败: %s" % str(e)[:200])


@router.post("/flow-records/batch")
@traced
async def flow_records_batch(request: Request):
    """入库/出库记录批量删除(2026-10-29): 两张表无 deleted_at(流水只增), 误导入无撤销手段
    body: {target: "inbound"|"outbound", ids: [int], reason?: str}
    安全: 上限 500 条/次(大范围删除需分批, 防误操作一次清空); 删除后同步批次(该 SKU 无入库则清 batches)
    """
    try:
        d = await request.json()
    except Exception:
        return fail("需 JSON body")
    target = str(d.get("target") or "")
    ids = [int(x) for x in (d.get("ids") or []) if str(x).isdigit()]
    if target not in ("inbound", "outbound"):
        return fail("target 必须为 inbound 或 outbound")
    if not ids:
        return fail("ids 不能为空")
    if len(ids) > 500:
        return fail("单次最多 500 条(请分批, 防误删)")
    from db import execute as _e, query as _q
    table = "inbound_records" if target == "inbound" else "outbound_records"
    _ph = ",".join(["%s"] * len(ids))
    try:
        _rows = _q("SELECT id, sku, quantity, channel FROM `%s` WHERE id IN (%s)" % (table, _ph), ids)
    except Exception as _e2:
        return fail("读取失败: %s" % str(_e2)[:120])
    if not _rows:
        return ok({"deleted": 0, "message": "无匹配记录"})
    _skus = sorted({str(r.get("sku") or "") for r in _rows if r.get("sku")})
    _ch = (_rows[0].get("channel") or "jd")
    # 2026-10-29 架构定案: 删除入/出库流水 → 自有仓必须重算(否则库存停留在旧值)
    # 由于改成"期初 + Σ流水"重算制, 删除后重算即自动回退(无需手工反向加减)
    try:
        _n = _e("DELETE FROM `%s` WHERE id IN (%s)" % (table, _ph), ids)
    except Exception as _e3:
        return fail("删除失败: %s" % str(_e3)[:120])
    # 自有仓重算(删除流水后库存随之回退; 幂等, 不依赖增量反向)
    try:
        if target in ("inbound", "outbound") and _skus:
            from routes.cleanse_write import _recalc_own_inventory as _rc
            _rc(_ch, _skus)
    except Exception as _erc:
        try_err('misc', '删除后自有仓重算降级', _erc)
    # 批次同步(仅入库): 该 SKU 已无入库记录 → 清理其 batches(避免效期统计幽灵)
    _b_cleaned = 0
    if target == "inbound" and _skus:
        try:
            for _sk in _skus:
                _left = _q("SELECT COUNT(*) AS c FROM inbound_records WHERE channel=%s AND sku=%s",
                           [_ch, _sk])
                if not (_left and int((_left[0] or {}).get("c") or 0)):
                    _b_cleaned += int(_e("DELETE FROM batches WHERE channel=%s AND sku=%s",
                                         [_ch, _sk]) or 0)
        except Exception as _e4:
            try_err('misc', '批次清理降级', _e4)
    # 审计留痕
    try:
        _e("INSERT INTO quality_logs(log_type, level, message, source) VALUES('rule_action','warning',%s,'api')",
           ("删除%s记录 %d 条(ids=%s%s)" % (
               "入库" if target == "inbound" else "出库", len(_rows), str(ids[:10]),
               (" reason=" + str(d.get("reason"))) if d.get("reason") else ""),))
    except Exception as _e5:
        try_err('misc', '删除审计写入降级', _e5)
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok({"deleted": len(_rows), "batch_cleaned": _b_cleaned,
               "message": "已删除 %d 条记录" % len(_rows)})


FLOW_RETENTION_DAYS = 180  # 出入库明细默认可见范围(天)


def _migrate_pk(table, cols):
    """主键原子迁移(影子表, 2026-10-29 根治)

    为什么不能原地改: TiDB 聚簇索引表的主键值直接编码进 Row Key, 主键=行的物理地址,
    `ALTER TABLE ... DROP PRIMARY KEY` 不被支持 → 只能换表。

    旧实现的问题: 「建新表 → DROP 旧表 → RENAME → 靠订单重算回填」——DROP 之后重算失败即空表;
    且对**已迁移**的表仍无条件全量 INSERT → 恒 1062(回填从未生效)。

    新实现(任一步失败都不动现有表): 建影子表 → 全列迁移 → 行数校验 → RENAME 原子切换 → 删旧表。
    返回状态字符串。
    """
    import re as _re
    _new = table + "__nb"
    _old = table + "__old"
    try:
        _idx = query("SHOW INDEX FROM `%s`" % table)
        _pk = [r.get("Column_name") for r in _idx if r.get("Key_name") == "PRIMARY"]
        if not [c for c in cols if c not in _pk]:
            return "already"
        _ddl = (query("SHOW CREATE TABLE `%s`" % table) or [{}])[0].get("Create Table") or ""
        if not _ddl:
            return "error: no ddl"
        _nddl = _re.sub(r"PRIMARY KEY \([^)]*\)",
                        "PRIMARY KEY (%s)" % ", ".join("`%s`" % c for c in cols), _ddl, count=1)
        _nddl = _nddl.replace("CREATE TABLE `%s`" % table, "CREATE TABLE `%s`" % _new, 1)
        _cs = ", ".join("`%s`" % r.get("Field") for r in query("SHOW COLUMNS FROM `%s`" % table))
        execute("DROP TABLE IF EXISTS `%s`" % _new)
        execute(_nddl)
        _moved = execute("INSERT INTO `%s` (%s) SELECT %s FROM `%s`" % (_new, _cs, _cs, table))
        _old_n = int((one("SELECT COUNT(*) AS c FROM `%s`" % table) or {}).get("c") or 0)
        _new_n = int((one("SELECT COUNT(*) AS c FROM `%s`" % _new) or {}).get("c") or 0)
        if _old_n != _new_n:
            raise RuntimeError("行数校验失败 old=%d new=%d" % (_old_n, _new_n))
        if _old_n == 0:
            raise RuntimeError("旧表为空, 迁移无意义(等数据落库后再迁移)")
        execute("DROP TABLE IF EXISTS `%s`" % _old)
        execute("RENAME TABLE `%s` TO `%s`, `%s` TO `%s`" % (table, _old, _new, table))
        execute("DROP TABLE `%s`" % _old)
        return "rebuilt(old_pk=%s, moved=%d)" % (",".join(_pk), int(_moved or _new_n))
    except Exception as e:
        try_err('misc', '主键迁移失败(%s)' % table, e)
        try:
            execute("DROP TABLE IF EXISTS `%s`" % _new)
        except Exception as _ec:
            try_err('misc', '影子表清理失败(%s)' % table, _ec)
        return "error: %s" % str(e)[:140]


# 代理主键契约: 表 → (业务唯一键列, 唯一索引名)
# 目标结构 = id 自增主键(聚簇, 顺序写) + 业务唯一键(含 69码)
SURROGATE_CONTRACT = {
    "daily_sales_snapshot": (["date", "channel", "sku", "barcode", "warehouse"], "uq_snapshot_biz"),
    "sku_sales_stats": (["channel", "sku", "barcode", "warehouse"], "uq_stats_biz"),
}


def _migrate_surrogate(table, biz_cols, uq_name):
    """业务主键 → 代理主键(**一次性根治**, 之后业务键变化只改唯一索引)

    为什么这是根治: TiDB 聚簇表主键=行物理地址, 不支持 DROP PRIMARY KEY。
    只要主键里含有业务列(barcode/warehouse), 业务粒度一变就必须整表重建。
    把业务列降级为 UNIQUE KEY 后:
      · 主键(id 自增)永不变化, 不会再触发"改主键"
      · 业务键增删 = ALTER TABLE DROP/ADD UNIQUE INDEX(在线 DDL, 无需重建)
      · ON DUPLICATE KEY 语义不变(唯一索引同样驱动 upsert)

    安全: 影子表 → 全列迁移 → 行数校验 → RENAME 原子切换 → 删旧表; 任一步失败不动现有表。
    """
    import re as _re
    _new = table + "__nb"
    _old = table + "__old"
    try:
        _idx = query("SHOW INDEX FROM `%s`" % table)
        _pk = [r.get("Column_name") for r in _idx if r.get("Key_name") == "PRIMARY"]
        _has_uq = any(str(r.get("Key_name") or "") == uq_name for r in _idx)
        if _pk == ["id"] and _has_uq:
            return "already(id + %s)" % uq_name
        _cols = [r.get("Field") for r in query("SHOW COLUMNS FROM `%s`" % table)]
        for _c in biz_cols:  # 旧表可能缺 barcode(补列自愈)
            if _c not in _cols:
                execute("ALTER TABLE `%s` ADD COLUMN `%s` VARCHAR(64) NOT NULL DEFAULT ''" % (table, _c))
                _cols.append(_c)
        _ddl = (query("SHOW CREATE TABLE `%s`" % table) or [{}])[0].get("Create Table") or ""
        if not _ddl:
            return "error: no ddl"
        # ① 原业务主键 → 唯一索引(含 clustered 注释一并替换, 避免残留)
        _nddl = _re.sub(r"PRIMARY KEY \([^)]*\)(?: /\*T!\[clustered_index\] \w+ \*/)?",
                        "UNIQUE KEY `%s` (%s)" % (uq_name, ", ".join("`%s`" % c for c in biz_cols)),
                        _ddl, count=1)
        # ② 末尾追加代理主键列(整数单列 → 聚簇, 写入顺序追加)
        _i = _nddl.rindex("\n) ENGINE")
        _nddl = _nddl[:_i] + ",\n  `id` BIGINT NOT NULL AUTO_INCREMENT,\n  PRIMARY KEY (`id`)" + _nddl[_i:]
        _nddl = _nddl.replace("CREATE TABLE `%s`" % table, "CREATE TABLE `%s`" % _new, 1)
        _cs = ", ".join("`%s`" % c for c in _cols)
        execute("DROP TABLE IF EXISTS `%s`" % _new)
        execute(_nddl)
        _moved = execute("INSERT INTO `%s` (%s) SELECT %s FROM `%s`" % (_new, _cs, _cs, table))
        _old_n = int((one("SELECT COUNT(*) AS c FROM `%s`" % table) or {}).get("c") or 0)
        _new_n = int((one("SELECT COUNT(*) AS c FROM `%s`" % _new) or {}).get("c") or 0)
        if _old_n != _new_n:
            raise RuntimeError("行数校验失败 old=%d new=%d" % (_old_n, _new_n))
        if _old_n == 0:
            raise RuntimeError("旧表为空, 迁移无意义(等数据落库后再迁移)")
        execute("DROP TABLE IF EXISTS `%s`" % _old)
        execute("RENAME TABLE `%s` TO `%s`, `%s` TO `%s`" % (table, _old, _new, table))
        execute("DROP TABLE `%s`" % _old)
        try:
            execute("INSERT IGNORE INTO migration_log(name) VALUES(%s)", ["pk_surrogate:%s" % table])
        except Exception as _eml:
            try_err('misc', '迁移登记降级(%s)' % table, _eml)
        return "rebuilt(surrogate id, moved=%d)" % int(_moved or _new_n)
    except Exception as e:
        try_err('misc', '代理主键迁移失败(%s)' % table, e)
        try:
            execute("DROP TABLE IF EXISTS `%s`" % _new)
        except Exception as _ec:
            try_err('misc', '影子表清理失败(%s)' % table, _ec)
        return "error: %s" % str(e)[:140]


@router.post("/maintenance/heal-own")
@traced
async def heal_own_inventory(request: Request):
    """自有仓数据自愈(2026-10-29, 幂等): 回填仓型 → 重算库存 → 重建批次

    场景: 架构定案(自有仓=重算制)后, 历史数据需一次性对齐
      · 出入库 warehouse_type 回填 'own'
      · inventory.available_qty = 期初 + Σ入库 − Σ出库
      · batches 按 FIFO 重建(Σ批次 ≡ available)
    只动自有仓维度, 平台仓/B仓(快照制)完全不动。
    """
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    channel = (d or {}).get("channel") or ""
    # surrogate=true → 执行「业务主键 → 代理主键」根治迁移(一次性; 默认不做, 避免隐式改表结构)
    _surrogate = bool((d or {}).get("surrogate"))
    # 2026-10-30 快照全量回填改为按需: 默认仅在快照为空时自动补(日常靠增量+对账);
    #   传 {"refill": true} 强制全量 90 天(运维修大缺口时用, 单次 13 万行写入)
    _force_refill = bool((d or {}).get("refill"))
    chs = [channel] if channel else ["jd", "other"]
    out = {}
    from routes.cleanse_write import (
        SELF_WAREHOUSE_TYPES as _SELF,
        _recalc_own_inventory as _recalc,
        _rebuild_own_batches as _rebuild,
    )
    _ph = ",".join(["%s"] * len(_SELF))
    _old_suffix = "_old_20261029"   # 此前 RENAME 遗留的兜底表后缀
    for ch in chs:
        try:
            # ① 回填仓型(历史出入库记录 warehouse_type 为空)
            _e1 = execute(
                "UPDATE inbound_records SET warehouse_type=%s WHERE channel=%s "
                "AND (warehouse_type IS NULL OR warehouse_type='')",
                [_SELF[0], ch])
            _e2 = execute(
                "UPDATE outbound_records SET warehouse_type=%s WHERE channel=%s "
                "AND (warehouse_type IS NULL OR warehouse_type='')",
                [_SELF[0], ch])
            # ② 重算库存 + ③ 重建批次(逐 SKU 分片控制耗时)
            _skus = [r.get("sku") for r in query(
                "SELECT DISTINCT sku FROM inventory WHERE channel=%s "  # no-barcode-ok: heal-own UPDATE 语句, 非 SELECT
                "AND warehouse_type IN (%s)" % ("%s", _ph), [ch] + list(_SELF))]
            _skus = [x for x in _skus if x]
            _n_inv = _recalc(ch, _skus) if _skus else 0
            _n_bat = _rebuild(ch, _skus) if _skus else 0
            out[ch] = {"bt": [_e1, _e2], "recalc": _n_inv, "batches": _n_bat, "skus": len(_skus)}
        except Exception as e:
            try_err('misc', '自有仓自愈降级(%s)' % ch, e)
            out[ch] = {"error": str(e)[:200]}
    # 2026-10-29 顺带: 日销主键迁移 + 物化重建(schema 只在模块加载时跑, 已运行实例不会重跑)
    try:
        from db import query as _q5, execute as _e5
        # TiDB 聚簇索引主键不可 DROP → **彻底重建**(不留旧表):
        #   日销/快照都是**派生数据**(可完全从订单重算) → 无需迁移旧数据, 直接 DROP 重建
        #   旧主键不含 barcode 会与按码行冲突, 只能重建; 留 _old 表属冗余包袱(用户定案: 根治)
        _pk_changes = {
            "daily_sales_snapshot": ["date", "channel", "sku", "barcode", "warehouse"],
            "sku_sales_stats": ["channel", "sku", "barcode", "warehouse"],
        }
        _mig = {}
        if _surrogate:
            for _tb, (_bcols, _uq) in SURROGATE_CONTRACT.items():
                _mig[_tb] = _migrate_surrogate(_tb, _bcols, _uq)
        else:
            for _tb, _cols in _pk_changes.items():
                _mig[_tb] = _migrate_pk(_tb, _cols)
        # 清理历史遗留的兜底表(用户定案: 不留 _old; 彻底重建后无需保留)
        _dropped = []
        try:
            for _r0 in _q5("SHOW TABLES"):
                _t0 = str(list(_r0.values())[0])
                if _old_suffix in _t0:
                    _e5("DROP TABLE IF EXISTS `%s`" % _t0)
                    _dropped.append(_t0)
        except Exception as _edr:
            try_err('misc', '遗留兜底表清理失败', _edr)
        out["_dropped_legacy_tables"] = _dropped
        # 快照回填(2026-10-29 根治): 90 天窗口 + 幂等 upsert + 统一销量池口径
        #   旧实现: 全量无窗口 + 无 ON DUPLICATE → 表非空时恒 1062(回填从未生效),
        #   且口径为 3 状态(与已完成口径差 +72%)。现与 cron._build_snapshot 完全同语义:
        #   只刷新最近 90 天, 不删任何历史行(归档期日销保留), 重复执行结果唯一。
        _refill = {}
        try:
            from db import execute as _e6
            # 2026-10-30 窗口统一 90: 物化重建 / 对账 / 回填三处一致;
            #   原 90 天会多写 30 天"物化根本不读"的历史行(白耗 RU)
            _start = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
            for _ch3 in chs:
                # 2026-10-30 条件化(原无条件全量 90 天回填): 该语句本意是"主键重建后表被清空"的补偿,
                #   改 ON DUPLICATE 后条件丢失 → 每次 heal-own 都写 13 万行(实测 12 次调用 = 12 次全量,
                #   RU 翻倍主因)。现仅在**该渠道快照为空**时回填; 平时由 _snapshot_step 增量 +
                #   _snapshot_reconcile 对账维护。
                _have = int(((_q5("SELECT COUNT(*) c FROM daily_sales_snapshot WHERE channel=%s",
                                  [_ch3]) or [{}])[0] or {}).get("c") or 0)
                if _have > 0 and not _force_refill:
                    _refill[_ch3] = "skipped(已有 %d 行, 需要全量请传 refill:true)" % _have
                    continue
                _e6("INSERT INTO daily_sales_snapshot(`date`, channel, sku, barcode, warehouse, order_count) "
                    "SELECT DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse, SUM(quantity) "
                    "FROM orders WHERE channel=%s AND order_status IN (" + _SALES_SQL + ") "
                    "AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s "
                    "GROUP BY DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse "
                    "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count)", [_ch3, _start])
                _cnt = int(((_q5("SELECT COUNT(*) c FROM daily_sales_snapshot WHERE channel=%s", [_ch3]) or [{}])[0] or {}).get("c") or 0)
                _refill[_ch3] = _cnt
        except Exception as _erf:
            try_err('misc', '快照全量重填充失败', _erf)
            _refill = {"error": str(_erf)[:160]}
        out["_snapshot_refill"] = _refill
        # 2026-10-30 快照残留自愈(软删订单后 upsert 不会删旧组合) —— 挂到运维接口, 可手动触发
        #   (每日也会由 _track_data 的 maintenance_log 抢占自动跑一次)
        _snap_rec = {}
        try:
            from routes.dashboard_maintain import _snapshot_reconcile as _sr
            for _ch4 in chs:
                _snap_rec[_ch4] = _sr(_ch4, limit_days=30)   # 运维入口一次最多修 30 天(日常自动跑仍 10 天)
        except Exception as _esr:
            try_err('misc', '快照残留自愈降级', _esr)
        out["_snapshot_reconcile"] = _snap_rec
        # 物化重建(生成按码行)
        _reb = {}
        try:
            from biz.sales import _rebuild_sku_sales_stats as _rss
            for _ch2 in chs:
                _reb[_ch2] = _rss(_ch2, days=90)
        except Exception as _erb:
            try_err('misc', '日销物化重建失败', _erb)
            _reb = {"error": str(_erb)[:160]}
        out["_pk_migration"] = _mig
        out["_stats_rebuild"] = _reb
    except Exception as _em:
        try_err('misc', '主键迁移/重建降级', _em)
    from routes.analysis_cache import invalidate_all
    invalidate_all()
    return ok(out)


@router.get("/flow-records/list")
@traced
def flow_records_list(target: str = "inbound", page: int = 1, page_size: int = 50,
                      date_from: str = "", date_to: str = "", sku: str = "",
                      channel: str = "jd", wt: str = "", days: int = 0):
    """出入库明细(2026-10-29): 进销存页锤子菜单 → 弹窗 tab 切换
    列: 日期 / SKU / 生产日期 / 截止日期 / 数量 / 备注(入库=供应商, 出库=目标仓)
    分页 SQL 下推(8061/6057 行, 索引 channel+date)"""
    from db import query as _q, one as _o
    if target not in ("inbound", "outbound"):
        return fail("target 必须为 inbound 或 outbound")
    table = "inbound_records" if target == "inbound" else "outbound_records"
    dcol = "inbound_date" if target == "inbound" else "outbound_date"
    # 2026-10-29 语义: 入库显示供应商 / 出库显示仓库(发生仓); 备注列单独返回
    ecol = "supplier" if target == "inbound" else "warehouse"
    pg = max(1, int(page or 1))
    ps = min(max(1, int(page_size or 50)), 200)
    where = ["channel=%s"]
    params = [channel or "jd"]
    if date_from:
        where.append("%s >= %%s" % dcol)
        params.append(date_from + " 00:00:00")
    if date_to:
        where.append("%s <= %%s" % dcol)
        params.append(date_to + " 23:59:59")
    if sku:
        where.append("sku LIKE %s")
        params.append("%" + sku + "%")
    # 2026-10-29 业务定案: 出入库只属于自有类仓型(白名单可扩展) → 默认过滤
    from routes.cleanse_write import SELF_WAREHOUSE_TYPES as _SELF
    if wt:
        where.append("warehouse_type = %s")
        params.append(wt)
    else:
        _ph_wt = ",".join(["%s"] * len(_SELF))
        where.append("(warehouse_type IN (%s) OR warehouse_type = '')" % _ph_wt)
        params.extend(list(_SELF))
    # 2026-10-29 可见保留范围: 默认最近 180 天(业务上出入库台账需追溯半年;
    # 更早记录仍在库, 传 days=0 可查全部)
    try:
        _keep_days = int(days or FLOW_RETENTION_DAYS)
    except Exception:
        _keep_days = FLOW_RETENTION_DAYS
    if _keep_days > 0:
        where.append("%s >= DATE_SUB(CURDATE(), INTERVAL %s DAY)" % (dcol, "%s"))
        params.append(_keep_days)
    w = " AND ".join(where)
    try:
        tot = _o("SELECT COUNT(*) AS c FROM `%s` WHERE %s" % (table, w), params) or {}
        rows = _q("SELECT id, sku, %s AS d, quantity, prod_date, exp_date, warehouse, "
                  "`%s` AS extra, COALESCE(remark,'') AS remark, "
                  "COALESCE(warehouse_type,'') AS wt "
                  "FROM `%s` WHERE %s ORDER BY %s DESC, id DESC LIMIT %%s OFFSET %%s"
                  % (dcol, ecol, table, w, dcol),
                  params + [ps, (pg - 1) * ps])
    except Exception as _e:
        return fail("查询失败: %s" % str(_e)[:150])
    items = [{"id": r.get("id"), "sku": r.get("sku"), "date": str(r.get("d") or "")[:19],
              "prod_date": str(r.get("prod_date") or "")[:10],
              "exp_date": str(r.get("exp_date") or "")[:10],
              "quantity": int(r.get("quantity") or 0),
              "warehouse": str(r.get("warehouse") or ""),
              "extra": str(r.get("extra") or ""),
              "remark": str(r.get("remark") or ""),
              "warehouse_type": str(r.get("wt") or "")} for r in rows]
    return ok({"items": items, "total": int((tot or {}).get("c") or 0),
               "page": pg, "page_size": ps, "target": target,
               "extra_label": "供应商" if target == "inbound" else "仓库"})


@router.get("/data-freshness")
@traced
def data_freshness(channel: str = "jd"):
    """数据新鲜度(2026-10-29): 各导入源最后导入时间 + 看板指标级推导

    背景: 所有数据源=ERP/平台导出快照, 停更时界面无感知(日销摊薄→建议失真)
    返回:
      sources: 各源 {doc_type,label,last_import_at,last_biz_date,days,level}
      staleLevel: 0 正常(≤7天) / 1 橙(8-30) / 2 红(>30)  ← 取最差
      metrics: 看板指标 → 依赖源的最旧状态
    """
    from datetime import datetime as _dt, timezone as _tz
    from db import query as _q
    _LBL = {"order": "订单", "inventory": "库存", "platform_inv": "平台库存",
            "inventory_b": "B仓库存", "inbound": "入库", "outbound": "出库",
            "product": "商品档案", "supplier": "供应商", "gmv_flow": "GMV回款"}
    _now = _dt.now(_tz.utc)
    rows = []
    try:
        # db.query 返回 tuple → 转 list(下方兜底需要 append; 曾 AttributeError: 'tuple' has no append)
        rows = list(_q("SELECT doc_type, last_import_at, last_biz_date FROM data_freshness WHERE channel=%s",
                       [channel or "jd"]) or [])
    except Exception as _e:
        try_err('misc', '新鲜度查询降级', _e)

    def _days(s):
        """天数差: 兼容 'YYYY-MM-DD HH:MM:SS' 与 'YYYY-MM-DDTHH:MM:SS'(MySQL MAX() 返回 T 分隔)"""
        if not s:
            return None
        _t = str(s)[:19].replace("T", " ")
        for _fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                _d = _dt.strptime(_t, _fmt).replace(tzinfo=_tz.utc)
                return max(int((_now - _d).total_seconds() // 86400), 0)
            except Exception:
                continue
        return None

    def _lvl(d):
        if d is None:
            return 0
        return 0 if d <= 7 else (1 if d <= 30 else 2)

    # 2026-10-29 兜底: data_freshness 无记录(新表/历史数据) → 从业务表 MAX(业务日期) 反推
    # 用**预置完整 SQL 字符串**(不做 % 格式化, 避免与 WHERE 内 %s 冲突)
    _seen = {str(r.get("doc_type") or "") for r in rows}
    _FB_SQL = {
        "order": ("SELECT MAX(ordered_at) AS m FROM orders "
                  "WHERE (deleted_at IS NULL OR deleted_at='') AND channel=%s"),
        "inbound": "SELECT MAX(inbound_date) AS m FROM inbound_records WHERE channel=%s",
        "outbound": "SELECT MAX(outbound_date) AS m FROM outbound_records WHERE channel=%s",
    }
    for _dtp, _sql in _FB_SQL.items():
        if _dtp in _seen:
            continue
        try:
            _r = _q(_sql, [channel or "jd"])
            _m = ""
            if _r:
                _m = str((_r[0] or {}).get("m") or "")[:19]
            if _m:
                rows.append({"doc_type": _dtp, "last_import_at": "", "last_biz_date": _m})
        except Exception as _fx:
            try_err('misc', '新鲜度兜底(%s)降级' % _dtp, _fx)

    src = {}
    for r in rows:
        _dtp = str(r.get("doc_type") or "")
        # 优先导入时间; 无则用业务日期(兜底反推) —— 两者都代表"数据截至"
        _d = _days(r.get("last_import_at")) if r.get("last_import_at") else _days(r.get("last_biz_date"))
        src[_dtp] = {"doc_type": _dtp, "label": _LBL.get(_dtp, _dtp),
                     "last_import_at": str(r.get("last_import_at") or "")[:19],
                     "last_biz_date": str(r.get("last_biz_date") or "")[:10],
                     "days": _d, "level": _lvl(_d)}
    # 缺记录 = 从未导入 → 视为未知(不报红, 避免新部署全红)
    def _worse(*keys):
        """取依赖源中最差(天数最大); 用于指标级推导"""
        best = None
        names = []
        for k in keys:
            s = src.get(k)
            if s and s.get("days") is not None:
                names.append(s["label"])
                if best is None or s["days"] > best["days"]:
                    best = s
        if not best:
            return {"days": None, "level": 0, "from": ""}
        return {"days": best["days"], "level": _lvl(best["days"]),
                "from": "/".join(names[:2])}

    metrics = [
        {"key": "gmv", "label": "上月 GMV", **_worse("order")},
        {"key": "pending", "label": "待处理告警", **_worse("order", "inventory")},
        {"key": "health", "label": "库存健康", **_worse("inventory", "order")},
        {"key": "risk", "label": "濒临断货", **_worse("inventory", "order")},
    ]
    lv = 0
    for s in src.values():
        lv = max(lv, s["level"])
    return ok({"channel": channel or "jd", "sources": list(src.values()),
               "metrics": metrics, "staleLevel": lv})
