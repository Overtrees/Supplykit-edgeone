"""task 导出链路模块(2026-10-11 拆分——tasks.py 减负; 接力式导出: POST 建任务→status 续跑分片→download)
函数: create_export/export_status/_save_export_part/export_download/_build_export_rows/now_stamp
依赖: db/routes.common/export_files——独立
"""
from datetime import datetime, timedelta, timezone
import json
import time as _time
import io

from fastapi import APIRouter, Request, Response

from db import query, one, execute
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["task-export"])



def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

# 导出文件名中文映射(2026-10-25: 曾 exports_<英文type>_<channel>_<ts>——改中文+日期+时分秒)
_EXPORT_NAMES = {
    "replen": "补货建议", "purchase_suggestions": "采购建议", "purchase": "采购建议",
    "slow": "滞销处置建议", "orders": "订单明细", "inventory": "进销存明细",
}

def _batch_pct(prod, exp):
    """批次消耗% = (今天-生产日)/总效期(与前端 InventoryPage 批次展开同算法)"""
    try:
        from datetime import date as _date
        pd = _date.fromisoformat(str(prod)[:10])
        ed = _date.fromisoformat(str(exp)[:10])
        dp = max((ed - pd).days, 1)
        return max(0, min(100, round(((_date.today() - pd).days / dp) * 100)))
    except Exception:
        return 0

def _build_export_rows(exp_type, mode, channel, wh_type=""):
    """按类型生成导出行(复用现有路由逻辑, 均为字典列表)"""
    if exp_type in ("replen", "purchase"):
        # 补货导出(采购建议不再走此——曾 purchase_suggestions 误用补货数据组装成采购样式)
        from routes.replenishment import get_replenishment_suggestions
        r = get_replenishment_suggestions(days=28, mode=mode, channel=channel)
        items = (r.get("data") or []) if isinstance(r, dict) else (r or [])
        # 补货导出: 转中文表头(曾直接 return items——接口英文字段作表头; 补周转列齐全)
        out = []
        for it in items:
            out.append({
                "SKU": it.get("sku", ""), "69码": it.get("barcode", ""),
                "商品名": it.get("product_name", ""), "品牌": it.get("brand", ""),
                "店铺": it.get("store", ""), "可用库存": it.get("available_qty", ""),
                "安全线": it.get("safety_qty", ""), "在途": it.get("in_transit_qty", ""),
                "C仓在途": it.get("c_transit", ""), "B仓在途": it.get("b_transit", ""),
                "B仓库存": it.get("b_stock", ""), "C仓库存": it.get("c_stock", ""),
                "B缺口": it.get("b_gap", ""), "日销": it.get("daily_sales", ""),
                "日销7": it.get("daily_sales_7", ""), "日销14": it.get("daily_sales_14", ""),
                "日销28": it.get("daily_sales_28", ""), "日销60": it.get("daily_sales_60", ""),
                "库存可撑天数": it.get("days_to_empty", ""),
                "C仓周转": it.get("c_turnover", ""), "B-C调拨周转": it.get("transit_turnover", ""),
                "当前综转": it.get("combined_turnover_current", it.get("combined_turnover", "")),
                "补后综转": it.get("after_turnover", ""),
                "建议补货量": it.get("suggested_qty", ""), "B建议补": it.get("b_suggested", ""),
                "备注": it.get("note", ""),
            })
        return out
    if exp_type == "purchase_suggestions":
        # 采购导出: 采购建议接口(供应商级参数/MOQ/actual_purchase——曾误用补货数据组装成采购样式)
        from routes.purchase import purchase_suggestions
        r = purchase_suggestions(channel=channel, mode=mode)
        dd = r.get("data") if isinstance(r, dict) else r
        items = dd.get("suggestions") if isinstance(dd, dict) else (dd or [])
        out = []
        for it in items:
            out.append({
                "SKU": it.get("sku", ""), "商品名": it.get("product_name", ""),
                "69码": it.get("barcode", ""), "品牌": it.get("brand", ""),
                "仓库": it.get("warehouse", ""), "供应商": it.get("supplier_code", ""),
                "日销(融合)": it.get("daily_sales", ""), "日销14": it.get("daily_sales_14", ""),
                "日销28": it.get("daily_sales_28", ""),
                "系统可用": it.get("sys_available", ""), "系统在途": it.get("sys_transit", ""),
                "系统总库存": it.get("sys_total", ""), "自有可用": it.get("own_available", ""),
                "平台可用": it.get("plat_available", ""),
                "系统总缺口": it.get("raw_suggested", ""), "建议采购量": it.get("purchase_qty", ""),
                "建议采购(含箱规)": it.get("actual_purchase", ""), "箱规": it.get("box_qty", ""),
                "补后周转": it.get("after_turnover", ""), "目标周转": it.get("target_turnover", ""),
                "安全线": it.get("safety_qty", ""), "备注": it.get("note", ""),
            })
        return out
        return items
    if exp_type == "slow":
        # 滞销导出: 与页面同源(disposal-suggestions——多因素 level/原因/建议; 曾用 slow_moving 基础7字段表头不全)
        from routes.purchase import disposal_suggestions
        r = disposal_suggestions(channel=channel)
        data = (r.get("data") or {}) if isinstance(r, dict) else (r or [])
        items = data.get("items") if isinstance(data, dict) else (data or [])
        out = []
        for it in items:
            out.append({
                "SKU": it.get("sku", ""), "商品名": it.get("product_name", ""),
                "品牌": it.get("brand", ""), "仓库": it.get("warehouse", ""),
                "仓库类型": it.get("warehouse_type", ""), "品类": it.get("category", ""),
                "库存": it.get("stock", ""), "未售天数": it.get("days_zero", ""),
                "日销": it.get("daily_sales", ""), "周转天数": it.get("turnover_days", ""),
                "资金占用(元)": it.get("fund_occupied", ""), "品类滞销线": it.get("cat_line", ""),
                "等级": it.get("level", ""), "原因": "; ".join(it.get("reason") or []),
                "处置建议": it.get("suggestion", ""),
                "临期至": str(it.get("best_before") or "")[:10] or "",
                "B仓超期": "是" if it.get("b_storage") else "",
            })
        return out
    if exp_type == "orders":
        # 订单导出(当前渠道, 最多 5 万行)
        from routes.orders import list_orders
        r = list_orders(channel=channel, page=1, page_size=50000)
        data = (r.get("data") or {}) if isinstance(r, dict) else (r or [])
        items = data.get("items") if isinstance(data, dict) else (data or [])
        out = []
        for o in items:
            out.append({
                "订单号": o.get("order_no", ""), "店铺": o.get("store", ""),
                "仓库": o.get("warehouse", ""), "SKU": o.get("sku", ""),
                "商品名": o.get("product_name", ""), "品牌": o.get("brand", ""),
                "69码": o.get("barcode", ""),
                "数量": o.get("quantity", ""), "单价": o.get("unit_price", ""),
                "金额": o.get("total_amount", ""), "状态": o.get("order_status", ""),
                "下单时间": str(o.get("ordered_at") or "")[:10],
                "支付时间": str(o.get("paid_at") or "")[:10],
                "平台": o.get("platform", ""), "渠道": o.get("channel", ""),
            })
        return out
    if exp_type == "inventory":
        # 进销存导出(当前渠道+仓型, 最多 5 万行)——含批次级明细(每批次一行, 主字段重复;
        # 曾只导出汇总行(batch_* 摘要)——用户要求"全量明细看多批次对应数量")
        from routes.insights import inventory_with_sales
        r = inventory_with_sales(wh_type=wh_type or "own", channel=channel,
                                 page=1, page_size=50000)
        data = (r.get("data") or {}) if isinstance(r, dict) else (r or [])
        items = data.get("items") if isinstance(data, dict) else (data or [])
        _bs = {}
        try:
            for _rb in query("SELECT sku, warehouse, prod_date, exp_date, qty FROM batches "
                             "WHERE channel=%s AND warehouse_type=%s ORDER BY exp_date, id",
                             [channel, wh_type or "own"]):
                _bs.setdefault(str(_rb.get("sku")) + "|" + str(_rb.get("warehouse")), []).append(_rb)
        except Exception:
            _bs = {}
        out = []
        _wh = wh_type or "own"
        # B-C 调拨在途是 B 仓(platform_b)维度列——own/platform 导出不含(对齐前端 INV_COLS 按仓型隔离)
        for it in items:
            _rbs = _bs.get(str(it.get("sku")) + "|" + str(it.get("warehouse"))) or [None]
            for _rb in _rbs:
                _pd = str((_rb or {}).get("prod_date") or "")[:10]
                _ed = str((_rb or {}).get("exp_date") or "")[:10]
                _pct = _batch_pct(_pd, _ed) if _rb else ""
                _st = ("已消耗过半" if _pct >= 67 else "消耗中" if _pct >= 40 else "正常") if _rb else ""
                _row = {
                    "SKU": it.get("sku", ""), "商品名": it.get("product_name", ""),
                    "仓库": it.get("warehouse", ""), "仓库类型": it.get("warehouse_type", ""),
                    # 2026-10-30 可用列对齐实际情况(用户定): 多批次行每行重复**计算后的可用**
                    #   (own=期初+入-出 重算值 / platform=导入快照), 不强制=批次数量;
                    #   批次数量由"批次数量"列单独呈现(每批次一行)
                    "可用": it.get("available_qty", ""),
                    "在途": it.get("in_transit_qty", ""),
                }
                if _wh == "platform_b":
                    _row["B-C调拨在途"] = it.get("c_transit", "")
                _row.update({
                    "安全线": it.get("safety_qty", ""), "单价": it.get("price", ""),
                    "品牌": it.get("brand", ""),
                    "生产日期(批次)": _pd or "",
                    "截止日期(批次)": _ed or "",
                    "批次数量": (_rb or {}).get("qty") if _rb else "",
                    "批次消耗%": _pct if _rb else "",
                    "批次效期状态": _st or "",
                })
                # 2026-10-30 仅自有仓导出进销概念列: 平台仓/B 仓无出入库流水 →
                #   期初/月入/月出/周转/可撑 恒空(周转由建议页承接), 导出不再输出空列
                if _wh == "own":
                    _row.update({
                        "日销": it.get("daily_sales", ""),
                        "周转天数": it.get("turnover_days", ""),
                        "库存可撑天数": it.get("days_to_empty", ""),
                        "当月入库": it.get("month_inbound", ""),
                        "当月出库": it.get("month_outbound", ""),
                        # 期初取真实导入值(方案A), 不是反推的本月期初
                        "期初": it.get("beginning_stock_raw", ""),
                    })
                out.append(_row)
        return out
    return None

async def create_export(request: Request):
    """导出任务(接力式: 建任务 → 前端轮询 /exports/status 续跑分页生成, 防大批量 120s 超时;
    Makers 请求后实例可能冻结, 不用纯后台线程) POST ?type&mode&channel&wh_type → {ok, task_id}"""
    from urllib.parse import parse_qs
    qs = parse_qs(request.url.query)
    exp_type = (qs.get("type") or ["replen"])[0]
    mode = (qs.get("mode") or [""])[0]
    channel = (qs.get("channel") or ["jd"])[0]
    wh_type = (qs.get("wh_type") or [""])[0]
    # 补货/采购导出必须显式传 mode(前端实际选择的当前补货模式)——曾默认 bbcc 兜底
    # 致传统模式导出变 BBCC(前端漏传时静默错误); 现在缺失显式报错, 无兜底
    if exp_type in ("replen", "purchase_suggestions", "purchase") and not mode:
        return fail("导出补货/采购需传 mode 参数(当前补货模式: bbcc / traditional)")
    if _build_export_rows(exp_type, mode, channel, wh_type) is None:
        return fail("未知导出类型: " + str(exp_type))
    from routes.tasks import _new_task_id  # 延迟(防循环: tasks→task_export→tasks)
    task_id = _new_task_id("exp")
    execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
            "VALUES(%s,'export','running',%s,'{}',%s)",
            (task_id, json.dumps({"type": exp_type, "mode": mode, "channel": channel,
                                  "wh_type": wh_type, "page": 0, "parts": [],
                                  "driver": "frontend", "start_ts": int(_time.time())},
                                 ensure_ascii=False), channel))
    return {"ok": True, "task_id": task_id}

def export_status(task_id: str = "", page_size: int = 10000, driver: str = "frontend"):
    from routes.common import mark_driver as _mkdrv
    _mkdrv(task_id, driver)
    """导出续跑(前端轮询触发, 每步查 page_size 行生成 CSV 分片存 export_files; 完成合并→done)
    返回 {ok, task_id, status, filename?, total?, error?}"""
    row = one("SELECT status, params, result FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"ok": False, "error": "任务不存在"}
    status = row.get("status") or ""
    if status in ("done", "error"):
        # result 结构 {"result": {...}} 双层——filename/error 在内层
        _ri = (json.loads(row.get("result") or "{}") or {}).get("result") or {}
        return {"ok": True, "task_id": task_id, "status": status,
                "filename": _ri.get("filename"), "name": _ri.get("name"),
                "parts": _ri.get("parts") or [], "error": _ri.get("error")}
    # 续跑一步(防并发: 原子抢占 updated_at 锁, 同 seed)
    try:
        _locked = execute("UPDATE sync_tasks SET updated_at=NOW() WHERE task_id=%s "
                          "AND (updated_at IS NULL OR updated_at < DATE_SUB(NOW(), INTERVAL 3 SECOND))",
                          [task_id])
        if not _locked:
            return {"ok": True, "task_id": task_id, "status": "running"}
    except Exception as _e:
            try_err('tasks', '静默降级', _e)
    try:
        import time as _ttx
        _t0x = _ttx.time()
        params = json.loads(row.get("params") or "{}")
        exp_type = params.get("type") or "replen"
        mode = params.get("mode") or "bbcc"
        channel = params.get("channel") or "jd"
        wh_type = params.get("wh_type") or ""
        page = int(params.get("page") or 0)
        parts = list(params.get("parts") or [])
        # 分批查询: 订单/库存大批量按 page 续跑; 其他类型一次全量(小)
        from routes.orders import list_orders
        if exp_type == "orders":
            r = list_orders(channel=channel, page=page + 1, page_size=page_size)
            data = (r.get("data") or {}) if isinstance(r, dict) else (r or {})
            items = data.get("items") if isinstance(data, dict) else (data or [])
            total = int(data.get("total") or 0) if isinstance(data, dict) else len(items)
            buf = io.StringIO()
            if items:
                out = []
                for o in items:
                    out.append({
                        "订单号": o.get("order_no", ""), "店铺": o.get("store", ""),
                        "仓库": o.get("warehouse", ""), "SKU": o.get("sku", ""),
                        "商品名": o.get("product_name", ""), "69码": o.get("barcode", ""),
                        "数量": o.get("quantity", ""), "单价": o.get("unit_price", ""),
                        "金额": o.get("total_amount", ""), "状态": o.get("order_status", ""),
                        "下单时间": str(o.get("ordered_at") or "")[:10],
                        "支付时间": str(o.get("paid_at") or "")[:10],
                        "平台": o.get("platform", ""), "渠道": o.get("channel", ""),
                    })
                import csv as _csv
                w = _csv.DictWriter(buf, fieldnames=list(out[0].keys()))
                if page == 0:
                    w.writeheader()
                w.writerows(out)
                part_name = "exp_part_%s_%d.csv" % (task_id, page)
                _save_export_part(part_name, buf.getvalue().encode("utf-8-sig"))
                parts.append(part_name)
            done = len(items) < page_size or page * page_size + len(items) >= total
        else:
            rows = _build_export_rows(exp_type, mode, channel, wh_type)
            # 非订单类型: 一次生成(小数据量)
            if exp_type in ("inventory",):
                buf = io.StringIO()
                if rows:
                    import csv as _csv
                    w = _csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
                part_name = "exp_part_%s_%d.csv" % (task_id, 0)
                _save_export_part(part_name, buf.getvalue().encode("utf-8-sig"))
                parts.append(part_name)
                done = True
            else:
                from openpyxl import Workbook
                from openpyxl.styles import Font
                bbuf = io.BytesIO()
                wb = Workbook()
                ws = wb.active
                ws.title = "导出"
                if rows:
                    headers = list(rows[0].keys())
                    ws.append(headers)
                    for c in ws[1]:
                        c.font = Font(bold=True)
                    for r in rows:
                        ws.append([r.get(k) for k in headers])
                wb.save(bbuf)
                part_name = "exp_part_%s_0.xlsx" % task_id
                _save_export_part(part_name, bbuf.getvalue())
                parts.append(part_name)
                done = True
        if done:
            # 分片不合并(2026-10-09: TiDB 单行 entry 上限 6MB——20MB CSV 单行存不下):
            # result 记分片列表, 下载时按 task 读分片合并返回
            realname = "%s_%s_%s.%s" % (_EXPORT_NAMES.get(exp_type, exp_type), channel, now_stamp(),
                                        "csv" if parts[0].endswith(".csv") else "xlsx")
            _r = {"filename": task_id, "name": realname, "type": exp_type, "parts": parts,
                  "rows": total if exp_type == "orders" else len(rows),
                  "success": total if exp_type == "orders" else len(rows), "failed": 0,
                  "elapsed": max(round(_ttx.time() - int(params.get("start_ts") or _ttx.time()), 1), 0.1)}
            _sqd = round(_ttx.time() - _t0x, 1)
            _sd = (params.get("steps") or []) + [{"name": "%s 窗%d" % (exp_type, page + 1), "elapsed": _sqd}]
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW() WHERE task_id=%s",
                    (json.dumps({**params, "page": page + 1, "parts": parts, "total": total if exp_type == "orders" else len(rows),
                                 "steps": _sd, "cur_step": None}, ensure_ascii=False),
                     json.dumps({"result": _r}, ensure_ascii=False), task_id))
            return {"ok": True, "task_id": task_id, "status": "done", "filename": task_id}
        _sqr = round(_ttx.time() - _t0x, 1)
        _sr = (params.get("steps") or []) + [{"name": "%s 窗%d" % (exp_type, page + 1), "elapsed": _sqr}]
        _curx = {"name": "%s 窗%d" % (exp_type, page + 2), "started": int(_ttx.time())}
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({**params, "page": page + 1, "parts": parts,
                             "total": total if exp_type == "orders" else len(rows),
                             "steps": _sr, "cur_step": _curx}, ensure_ascii=False), task_id))
        return {"ok": True, "task_id": task_id, "status": "running", "page": page + 1}
    except Exception as e:
        import traceback as _tb
        execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]}, ensure_ascii=False), task_id))
        return {"ok": True, "task_id": task_id, "status": "error", "error": str(e)[:200]}

def _save_export_part(filename, content):
    """存导出分片(与正式文件同列, base64 兜底)"""
    try:
        execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,'jd') "
                "ON DUPLICATE KEY UPDATE content=VALUES(content)", (filename, content))
    except Exception:
        import base64 as _b64
        execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,'jd') "
                "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                (filename, "base64:" + _b64.b64encode(content).decode("ascii")))

def export_download(filename: str):
    import base64 as _b64   # 2026-10-30 函数级导入(分片合并/单文件解码都用)
    row = one("SELECT content FROM export_files WHERE filename=%s", [filename])
    if not row:
        # 分片模式: filename 为任务虚拟名(实际分片在 sync_tasks.result.parts) → 读分片合并
        try:
            tr = one("SELECT result FROM sync_tasks WHERE task_id=%s", [filename])
            res = json.loads(tr.get("result") or "{}") if tr else {}
            res = res.get("result") or {}  # 双层结构 {result:{parts,name,...}}
            parts = res.get("parts") or []
            if parts:
                merged = b""
                for p in parts:
                    rp = one("SELECT content FROM export_files WHERE filename=%s", [p])
                    c = rp.get("content") if rp else None
                    if isinstance(c, str):
                        if c.startswith("base64:"):
                            # 2026-10-30 修复: 原直接把 c[7:] (仍是 base64 文本) 拼进 merged →
                            #   合并后无 "base64:" 前缀 → 下游按 UTF-8 编码 → 下载文件是 base64 乱码。
                            #   这里逐片解码成 bytes 再拼
                            try:
                                merged += _b64.b64decode(c[7:])
                            except Exception as _eb:
                                try_err('tasks', '分片解码降级(%s)' % p, _eb)
                        else:
                            merged += c.encode("utf-8-sig")
                    elif c is not None:
                        merged += bytes(c)
                filename = res.get("name") or filename  # 下载文件名用正式名(分片模式 filename=task_id)
                row = {"content": merged}
        except Exception as _e:
                try_err('tasks', '分片下载降级', _e)
    if row is None:
        return fail("导出文件不存在", 404)
    content = row.get("content")
    if isinstance(content, str):
        if content.startswith("base64:"):
            import base64 as _b64
            data = _b64.b64decode(content[7:])
        else:
            data = content.encode("utf-8-sig")  # 兼容 TEXT 列旧数据, 中文 Excel 打开不乱码
    elif content is None:
        data = b""
    else:
        data = bytes(content)
    if filename.endswith(".xlsx"):
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        media = "text/csv; charset=utf-8"
    # 2026-10-30 修复下载 500: HTTP 响应头必须 latin-1 可编码, 中文文件名(如"进销存明细_*.csv")
    #   直接放 filename= 会抛 UnicodeEncodeError → EdgeOne 500。改用 RFC 5987:
    #   ASCII 名放 filename=, 中文名放 filename*=UTF-8''(URL 编码)
    from urllib.parse import quote as _qfn
    _fn_ascii = filename if filename.isascii() else "export.csv"
    _cd = "attachment; filename=%s" % _fn_ascii
    if not filename.isascii():
        _cd += "; filename*=UTF-8''" + _qfn(filename, safe="")
    return Response(content=data, media_type=media, headers={"Content-Disposition": _cd})

# 导出端点注册(2026-10-11: 函数拆分至本模块——router 挂端点, tasks.py include)
router.post("/exports")(create_export)
router.get("/exports/status")(export_status)
router.get("/exports/download/{filename}")(export_download)
