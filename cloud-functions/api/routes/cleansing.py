"""原生清洗路由(方案 B): 文件识别/预览/执行/模板(6 类目标)

契约要点(CleansingPage 直连, 平铺响应——axios 拦截器对无 data 字段的 {ok,...} 保留原样):
- detect → {ok, columns:[{name}], total}(平铺, 不能 {ok,data} 包装否则前端 d.ok undefined)
- preview → {ok, preview:[{target字段}], total}(preview 为清洗后前 50 行)
- execute-async → {ok, task_id, success, failed, error?, message?}(同步执行完返回, 前端优先生成 success 直显)
- templates: GET 标准 {ok,data:[...]}; POST {name,doc_type,mapping} → {ok,data:{message}}
"""
import csv
import io
import json
import re
import time
from datetime import timedelta

from fastapi import APIRouter
from fastapi import Request
from fastapi import UploadFile
from fastapi import File
from fastapi import Form

from db import query, one, execute, executemany
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["cleansing"])

# 目标类型 → 表/仓库类型映射
_TARGET_WH = {"inventory": "own", "platform_inv": "platform", "inventory_b": "platform_b"}
_NUMS = {"number", "price", "quantity", "score", "safety_qty", "available_qty", "in_transit_qty",
         "month_inbound", "month_outbound", "beginning_stock", "turnover_days", "c_transit",
         "locked_qty", "weight", "volume", "box_qty", "total_amount", "unit_price",
         "freight_amount", "subsidy_amount", "tax_amount", "discount_amount", "actual_amount"}


def _read_file_bytes(upload: UploadFile):
    data = upload.file.read()
    name = (upload.filename or "upload.csv").lower()
    return data, name


def _parse_table(data: bytes, fname: str):
    """解析 CSV/XLSX → (headers, rows); rows 为 list[dict]"""
    if fname.endswith(".xlsx"):
        import openpyxl
        # 小文件普通模式(兼容非标准/无维度信息文件): read_only 下 max_row/max_column 可能误判 1×1
        # (实测: 采购订单明细导出(非图书).xlsx read_only 读到 1列0行, 普通模式 48列9行)
        # 大文件(>=10MB)仍用 read_only(标准文件可靠, 省内存)
        _rb = len(data) >= 10 * 1024 * 1024
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=_rb, data_only=True)
        except Exception:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        # 取第一个有数据的 sheet(active 可能是空壳/标题页)
        _ws = None
        for _s in wb.worksheets:
            if _s.max_row and _s.max_column:
                _ws = _s
                break
        if _ws is None:
            _ws = wb.active
        rows_iter = _ws.iter_rows(values_only=True)
        headers = None
        rows = []
        for r in rows_iter:
            if headers is None:
                headers = [str(c).strip() if c is not None else "" for c in r]
                continue
            rows.append(dict(zip(headers, ["" if c is None else c for c in r])))
        headers = [h for h in headers if h]
        return headers, rows
    # CSV: 自动探测编码/BOM/分隔符
    raw = data
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    try:
        # 分隔符探测扩展到多平台导出常见分隔: 逗号/制表/分号/竖线/中文逗号顿号
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|，、")
        sep = dialect.delimiter
    except Exception:
        sep = ","
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    # 单列/误判兜底: Sniffer 对竖线/中文逗号等常失效(实测), 手动探测候选分隔符
    # 判定: 候选分隔后表头行分裂 >1 列且前 5 行列数一致 → 采用
    if len(all_rows) <= 1 or not (all_rows and max((len(r) for r in all_rows[:10]), default=1) > 1):
        for _s in ("\t", ";", "|", "，", "、", ","):
            _rr = list(csv.reader(io.StringIO(text), delimiter=_s))
            if _rr and len(_rr[0]) > 1 and \
               all(len(r) == len(_rr[0]) for r in _rr[:5] if any(str(x).strip() for x in r)):
                sep, all_rows = _s, _rr
                break
    if not all_rows:
        return [], []
    headers = [h.strip() for h in all_rows[0] if h.strip()]
    rows = []
    for r in all_rows[1:]:
        if not any(str(c).strip() for c in r):
            continue
        rows.append(dict(zip(headers, r[:len(headers)])))
    return headers, rows


def _v(value, vtype):
    """按目标类型清洗单值"""
    if value is None:
        return ""
    s = str(value).strip()
    if vtype == "number":
        s = re.sub(r"[,\s元¥￥]", "", s)
        try:
            return float(s)
        except Exception:
            return 0.0
    if vtype == "date":
        m = re.match(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
        if m:
            return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
        return s[:10]
    return s


def _clean_rows(rows, mapping, custom_fields=None):
    """rows → 按 mapping {src:{target,type}} 清洗; 忽略无映射列; 空 target 跳过"""
    out = []
    for r in rows:
        item = {}
        for src, cfg in (mapping or {}).items():
            if src == "_meta" or not cfg or not cfg.get("target"):
                continue
            v = r.get(src)
            vtype = cfg.get("type") or "string"
            item[cfg["target"]] = _v(v, vtype)
        out.append(item)
    return out


def _after(now, days_ago):
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _detect_columns(fname, data):
    headers, _ = _parse_table(data, fname)
    return headers


# ── detect: 识别列名 ────────────────────────────────────────────────────
@router.post("/cleansing/detect")
@traced
async def cleansing_detect(file: UploadFile = File(...)):
    data, fname = _read_file_bytes(file)
    try:
        headers, rows = _parse_table(data, fname)
        return {"ok": True, "columns": [{"name": h} for h in headers], "total": len(rows)}
    except Exception as e:
        return {"ok": False, "error": "文件解析失败: %s" % str(e)[:200]}


# ── preview: 清洗预览(前 50 行) ─────────────────────────────────────────
@router.post("/cleansing/preview")
@traced
async def cleansing_preview(file: UploadFile = File(...), mapping: str = Form("{}"),
                            target: str = Form("order"), channel: str = Form("jd"),
                            conflict_mode: str = Form("sum")):
    data, fname = _read_file_bytes(file)
    try:
        _, rows = _parse_table(data, fname)
        mp = json.loads(mapping or "{}")
        cleaned = _clean_rows(rows, mp)
        # 零映射兜底(严谨性): 无任何有效值 → 明确提示而非静默返回 0 条
        if not any(any(str(v) not in ("", "None", "none") for v in it.values()) for it in cleaned):
            return {"ok": False, "error": "未配置有效列映射，请先在字段映射界面选择目标字段后再预览"}
        return {"ok": True, "preview": cleaned[:50], "total": len(cleaned),
                "target": target, "channel": channel, "conflict_mode": conflict_mode}
    except Exception as e:
        return {"ok": False, "error": "预览失败: %s" % str(e)[:200]}


# ── execute-async: 全量清洗写入 ─────────────────────────────────────────
@router.post("/cleansing/execute-async")
@traced
async def cleansing_execute(file: UploadFile = File(...), mapping: str = Form("{}"),
                            target: str = Form("order"), channel: str = Form("jd"),
                            conflict_mode: str = Form("sum")):
    """导入(接力式: POST 存源文件+建任务 → GET /cleansing/status 续跑分窗清洗写入,
    防大批量 120s 超时——与导出/seed 同模式; Makers 实例请求后可冻结, 不用纯后台线程)
    返回 {ok, task_id}"""
    data, fname = _read_file_bytes(file)
    task_id = "clean_%d_%04d" % (int(time.time()), int(time.time() * 1000) % 10000)
    try:
        # source 文件存 export_files(clean_ 前缀, 7 天清理覆盖)
        import base64 as _b64
        try:
            execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                    ("clean_" + task_id, data, channel))
        except Exception:
            execute("INSERT INTO export_files(filename, content, channel) VALUES(%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE content=VALUES(content)",
                    ("clean_" + task_id, "base64:" + _b64.b64encode(data).decode("ascii"), channel))
        params = {"fname": fname, "mp": mapping, "target": target, "channel": channel,
                  "conflict_mode": conflict_mode, "offset": 0, "success": 0, "failed": 0,
                  "total": 0}
        execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                "VALUES(%s,'cleansing','running',%s,'{}',%s)",
                (task_id, json.dumps(params, ensure_ascii=False), channel))
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        import traceback as _tb
        try:
            execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                    "VALUES(%s,'cleansing','error',%s,%s,%s)",
                    (task_id, '{}',
                     json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]},
                                ensure_ascii=False), channel))
        except Exception:
            pass
        return fail("导入启动失败: %s" % str(e)[:200])


def _finalize_import(target, channel, mp, cleaned_all, success, failed, elapsed):
    """接力导入最后一步: 库存联动 + 规则评估 + 汇总消息(与原 execute-async 同步版同逻辑)"""
    import time as _t
    adjusted = 0
    evaluated = 0
    # 库存联动(A3): inbound+ / outbound- / order(采购单+、销售-)
    try:
        if target in ("inbound", "outbound", "order"):
            from collections import defaultdict
            deltas = defaultdict(int)
            _wht = {}
            for c in cleaned_all:
                _sku = c.get("sku")
                if not _sku:
                    continue
                _qty = int(c.get("quantity") or 0)
                _wh = str(c.get("warehouse") or "")
                if target == "inbound":
                    _d, _wt = _qty, "own"
                elif target == "outbound":
                    _d, _wt = -_qty, "own"
                else:
                    _ds = ((mp or {}).get("_meta") or {}).get("data_source", "")
                    _d = _qty if _ds == "jd_po" else -_qty
                    _wt = "platform"
                if _d:
                    deltas[(_sku, _wh, _wt)] += _d
                    _wht[(_sku, _wh, _wt)] = _wt
            if deltas:
                adjusted, _ = _adjust_inventory(channel, {k: v for k, v in deltas.items()})
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    # 规则引擎评估(批量)
    try:
        from core.rules import evaluate_many, load_rules_for
        if target == "order":
            seen = set()
            skus = [c.get("sku") for c in cleaned_all
                    if c.get("sku") and not (c.get("sku") in seen or seen.add(c.get("sku")))]
            inv_map = {}
            if skus:
                for _i in range(0, len(skus), 200):
                    _batch = skus[_i:_i + 200]
                    _ph = ",".join(["%s"] * len(_batch))
                    for r in query("SELECT sku, MAX(available_qty) AS avail FROM inventory "
                                   "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph),
                                   [channel] + _batch):
                        inv_map[r.get("sku")] = int(r.get("avail") or 0)
            o_ctxs = []
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                oq = int(c.get("quantity") or 0)
                o_ctxs.append({"sku": sku, "channel": channel,
                               "order": {"quantity": oq}, "order_qty": oq,
                               "available_stock": inv_map.get(sku, 0),
                               "inv": {"available_qty": inv_map.get(sku, 0), "safety_qty": 0,
                                       "in_transit_qty": 0}})
            if o_ctxs:
                evaluate_many("order.created", o_ctxs, channel,
                              load_rules_for("order.created", channel))
                evaluated = len(o_ctxs)
        elif target in ("inventory", "platform_inv", "inventory_b"):
            i_ctxs = []
            for c in cleaned_all:
                sku = c.get("sku")
                if not sku:
                    continue
                i_ctxs.append({"sku": sku, "channel": channel,
                               "inv": {"available_qty": int(c.get("available_qty") or 0),
                                       "safety_qty": int(c.get("safety_qty") or 0),
                                       "in_transit_qty": int(c.get("in_transit_qty") or 0),
                                       "warehouse_type": c.get("warehouse_type", ""),
                                       "warehouse": c.get("warehouse", ""),
                                       "product_name": c.get("product_name") or sku}})
            if i_ctxs:
                evaluate_many("inventory.changed", i_ctxs, channel,
                              load_rules_for("inventory.changed", channel))
                evaluated = len(i_ctxs)
    except Exception as _ee:
        try:
            import traceback as _tb6
            from db import execute as _e5
            _e5("INSERT INTO quality_logs(log_type, level, message, details, source) "
                "VALUES('cleansing_eval','error',%s,%s,'cleansing')",
                ("规则评估失败(%s): %s" % (target, str(_ee)[:200]),
                 _tb6.format_exc(limit=20)[-1800:]))
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    # 汇总消息(顺序状态口径 + 混渠道提示)
    _msg = "成功 %d 条, 跳过 %d 条" % (success, failed)
    try:
        if target == "order":
            from collections import Counter as _C
            _sc = _C((c.get("order_status") or "未知") for c in cleaned_all)
            _ignore = {k: v for k, v in _sc.items() if k != "已完成"}
            if _ignore:
                _msg += " · 注意: %s 不计入销量池(仅已完成), 已发货/待确认/退款类不参与补货采购计算" % \
                        ("; ".join("%s×%d" % (k, v) for k, v in _ignore.items()))
        _chs = {c.get("channel") for c in cleaned_all if c.get("channel")}
        if _chs and channel not in _chs:
            _msg += " · 文件含其他渠道数据(%s), 已按当前渠道 %s 写入" % ("/".join(_chs), channel)
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    return adjusted, evaluated, _msg


@router.get("/cleansing/status")
@traced
def cleansing_status(task_id: str = "", page_size: int = 5000):
    """导入续跑(前端轮询触发): 每步读源文件 → 解析 → 窗口清洗写入(5000 行/步, ≤120s)
    全部完成 → 最后步全量重清洗做库存联动+规则评估+汇总 → done"""
    row = one("SELECT status, params, result FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"ok": False, "error": "任务不存在"}
    status = row.get("status") or ""
    if status in ("done", "error"):
        _ri = (json.loads(row.get("result") or "{}") or {}).get("result") or {}
        return {"ok": True, "task_id": task_id, "status": status,
                "success": _ri.get("success"), "failed": _ri.get("failed"),
                "message": _ri.get("message"), "error": _ri.get("error")}
    # 抢占锁(防并发续跑, 同 seed/导出)
    try:
        _locked = execute("UPDATE sync_tasks SET updated_at=NOW() WHERE task_id=%s "
                          "AND (updated_at IS NULL OR updated_at < DATE_SUB(NOW(), INTERVAL 3 SECOND))",
                          [task_id])
        if not _locked:
            return {"ok": True, "task_id": task_id, "status": "running"}
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    import time as _t
    started = _t.time()
    try:
        params = json.loads(row.get("params") or "{}")
        fname = params.get("fname") or "import.csv"
        mp = json.loads(params.get("mp") or "{}")
        target = params.get("target") or "order"
        channel = params.get("channel") or "jd"
        conflict_mode = params.get("conflict_mode") or "sum"
        offset = int(params.get("offset") or 0)
        success = int(params.get("success") or 0)
        failed = int(params.get("failed") or 0)
        # 读源文件
        fr = one("SELECT content FROM export_files WHERE filename=%s", ["clean_" + task_id])
        c = fr.get("content") if fr else None
        if isinstance(c, str):
            data = _b64.b64decode(c[7:]) if c.startswith("base64:") else c.encode("utf-8-sig")
        elif c is not None:
            data = bytes(c)
        else:
            raise Exception("源文件不存在")
        headers, rows = _parse_table(data, fname)
        total = len(rows)
        # 分窗清洗+写入
        window = rows[offset:offset + page_size]
        cleaned = _clean_rows(window, mp)
        if target == "order":
            try:
                from routes.suppliers import _norm_column_value as _ncol
                for x in cleaned:
                    if x.get("order_status"):
                        x["order_status"] = _ncol(channel, "order_status", str(x["order_status"]))
            except Exception as _e:
                    try_err('cleansing', '静默降级', _e)
        w_success, w_failed, err_details = _write_rows(target, channel, conflict_mode, cleaned)
        success += w_success
        failed += w_failed
        offset += len(window)
        done = offset >= total
        if done:
            # 最后步: 全量重清洗(联动/评估/汇总用)
            cleaned_all = _clean_rows(rows, mp)
            if target == "order":
                try:
                    from routes.suppliers import _norm_column_value as _ncol2
                    for x in cleaned_all:
                        if x.get("order_status"):
                            x["order_status"] = _ncol2(channel, "order_status", str(x["order_status"]))
                except Exception as _e:
                        try_err('cleansing', '静默降级', _e)
            elapsed = round(_t.time() - started, 1)
            adjusted, evaluated, _msg = _finalize_import(target, channel, mp, cleaned_all, success, failed, elapsed)
            execute("UPDATE sync_tasks SET status='done', result=%s, updated_at=NOW() WHERE task_id=%s",
                    (json.dumps({"result": {"target": target, "success": success, "failed": failed,
                                            "elapsed": elapsed, "rules_evaluated": evaluated,
                                            "inventory_adjusted": adjusted, "message": _msg}},
                                ensure_ascii=False), task_id))
            from routes.analysis_cache import invalidate_all
            invalidate_all()
            return {"ok": True, "task_id": task_id, "status": "done",
                    "success": success, "failed": failed, "message": _msg}
        # 未完 → 更新 offset/累积, running
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({**params, "offset": offset, "success": success, "failed": failed,
                             "total": total}, ensure_ascii=False), task_id))
        return {"ok": True, "task_id": task_id, "status": "running",
                "page": offset // page_size, "total": total}
    except Exception as e:
        import traceback as _tb
        execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]},
                            ensure_ascii=False), task_id))
        try_err("cleansing", "导入续跑失败", e)
        return {"ok": True, "task_id": task_id, "status": "error", "error": str(e)[:200]}


def _b64(code):
    import base64 as _b
    return _b.b64encode(code).decode("ascii")


@router.get("/cleansing/task/{task_id}")
@traced
def cleansing_task(task_id: str):
    row = one("SELECT status, result FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"status": "not_found"}
    status = row.get("status") or "pending"
    result = {}
    try:
        result = json.loads(row.get("result") or "{}")
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    out = {"status": status}
    if status == "done":
        out["result"] = result.get("result") or result
    elif status == "error":
        out["error"] = result.get("error") or "清洗失败"
    return out


# ── 模板 CRUD ───────────────────────────────────────────────────────────
@router.delete("/cleansing/templates/{tid}")
@traced
def cleansing_templates_delete(tid: int):
    """删除清洗模板(对齐 PA)"""
    execute("DELETE FROM cleansing_templates WHERE id=%s", [tid])
    return ok({"message": "已删除"})


@router.get("/cleansing/templates")
@traced
def cleansing_templates():
    rows = query("SELECT id, name, doc_type, mapping FROM cleansing_templates ORDER BY id DESC LIMIT 100")
    return ok(rows)


@router.post("/cleansing/templates")
@traced
async def cleansing_templates_save(request: Request):
    d = {}
    try:
        d = await request.json()
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    name = d.get("name") or ""
    doc_type = d.get("doc_type") or "order"
    if not name:
        return fail("缺少模板名称")
    mapping = d.get("mapping")
    if isinstance(mapping, dict):
        mapping = json.dumps(mapping, ensure_ascii=False)
    # 同名模板更新覆盖(对齐 PA: name 存在 → UPDATE; 否则 INSERT——防止同名重复)
    existing = one("SELECT id FROM cleansing_templates WHERE name=%s", [name])
    if existing:
        execute("UPDATE cleansing_templates SET mapping=%s, doc_type=%s, updated_at=NOW() WHERE id=%s",
                (mapping or "{}", doc_type, existing.get("id")))
    else:
        execute("INSERT INTO cleansing_templates(name, doc_type, mapping) VALUES(%s,%s,%s)",
                (name, doc_type, mapping or "{}"))
    return ok({"message": "模板已保存"})


# ── 写入逻辑(按目标类型) ────────────────────────────────────────────────
def _adjust_inventory(channel, deltas, evaluate_skus=None):
    """库存联动: {(sku, warehouse, warehouse_type): delta} → 批量更新库存(下限 0) + 规则批量评估(全量 SKU)

    完整性: 不限量 —— 大文件联动 SKU 全覆盖(原 300 上限曾致 >300 SKU 导入后规则评估缺失, 告警漏报);
    性能: evaluate_many 一次加载规则 + executemany 批量(原单条 evaluate 循环 N×查询)
    """
    if not deltas:
        return 0, []
    n = 0
    touched = []
    for (sku, wh, wht), delta in deltas.items():
        cur = one("SELECT id, available_qty FROM inventory WHERE sku=%s AND warehouse=%s AND channel=%s",
                  [sku, wh, channel])
        if cur:
            new_qty = max(0, int(cur.get("available_qty") or 0) + delta)
            execute("UPDATE inventory SET available_qty=%s WHERE id=%s", [new_qty, cur.get("id")])
        else:
            execute("INSERT INTO inventory(sku, warehouse, warehouse_type, available_qty, safety_qty, channel) "
                    "VALUES(%s,%s,%s,%s,10,%s)", [sku, wh or "", wht or "own", max(0, delta), channel])
        n += 1
        if sku not in touched:
            touched.append(sku)
    skus = touched
    try:
        from core.rules import evaluate_many, load_rules_for
        ctxs = []
        for sku in skus:
            inv = one("SELECT sku, warehouse, warehouse_type, product_name, available_qty, safety_qty, "
                      "in_transit_qty FROM inventory WHERE sku=%s AND channel=%s LIMIT 1", [sku, channel])
            if inv:
                ctxs.append({"sku": sku, "channel": channel, "inv": inv})
        if ctxs:
            evaluate_many("inventory.changed", ctxs, channel, load_rules_for("inventory.changed", channel))
    except Exception as _e:
            try_err('cleansing', '静默降级', _e)
    return n, skus


def _write_rows(target, channel, conflict_mode, cleaned):
    """按 target 分派写入; 返回 (success, failed)
    渠道归属: 导入数据归属导入渠道(除非 mapping 显式映射了 channel 列)——否则未映射时落默认 jd 污染跨渠道"""
    for it in cleaned:
        if "channel" not in it:
            it["channel"] = channel
    if target == "order":
        # 69 码自动查补(2026-09-15): 导入文件未映射/为空时从 products 按 sku 拉取(products.barcode 已全量填充)
        # —— 准确性(sku 1:1 唯一)/完整性(products 全有 barcode)/实时性(导入即补, 不再产生空 69 码)
        try:
            _miss = [r for r in cleaned if not (r.get("barcode") or "").strip()]
            if _miss:
                _skus = list({str(r.get("sku") or "").strip() for r in _miss if r.get("sku")})
                if _skus:
                    _bmap = {}
                    for _b in query("SELECT sku, barcode FROM products WHERE sku IN (%s) "
                                    "AND barcode IS NOT NULL AND barcode != ''"
                                    % ",".join(["%s"] * len(_skus)), _skus):
                        _bmap[str(_b.get("sku"))] = _b.get("barcode")
                    for r in cleaned:
                        _k = str(r.get("sku") or "").strip()
                        if not (r.get("barcode") or "").strip() and _bmap.get(_k):
                            r["barcode"] = _bmap[_k]
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
        return _write_batch("orders",
                            ["order_no", "store", "warehouse", "sku", "product_name", "barcode",
                             "quantity", "unit_price", "total_amount", "order_status",
                             "ordered_at", "paid_at", "platform", "channel", "data_source",
                             "freight_amount", "subsidy_amount", "tax_amount", "discount_amount",
                             "actual_amount", "supplier", "remark"],
                            cleaned, conflict_mode, "order_no", "sku")
    if target in ("inventory", "platform_inv", "inventory_b"):
        wt = _TARGET_WH[target]
        for it in cleaned:
            it["warehouse_type"] = wt
        return _write_batch("inventory",
                            ["sku", "product_name", "store", "warehouse", "warehouse_type",
                             "available_qty", "in_transit_qty", "safety_qty", "channel", "barcode",
                             "beginning_stock", "month_inbound", "month_outbound", "locked_qty",
                             "c_transit", "weight", "volume"],
                            cleaned, conflict_mode, "sku", "warehouse")
    if target == "product":
        return _write_batch("products",
                            ["sku", "product_name", "barcode", "store", "category", "price",
                             "box_qty", "unit", "status", "channel", "brand", "weight", "volume"],
                            cleaned, conflict_mode, "sku")
    if target == "supplier":
        return _write_batch("suppliers",
                            ["supplier_code", "supplier_name", "contact_person", "contact_phone",
                             "score", "status", "channel", "brand"],
                            cleaned, conflict_mode, "supplier_code")
    if target == "inbound":
        s, f, ed = _write_batch("inbound_records",
                            ["sku", "product_name", "quantity", "supplier", "inbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status"],
                            cleaned, conflict_mode, "sku", "inbound_date")
        # 批次效期同步(batches): 入库记录含 prod_date/exp_date(按映射列抓取) → 按 SKU×仓 维护
        # 更新方式联动导入冲突模式(conflict_mode): overwrite=删旧插新(文件即权威) / sum=同效期 qty 累加
        # (效期管控数据源: 进销存批次效期预警/临期处置建议)
        try:
            _br = [c for c in cleaned if (c.get("prod_date") or "") or (c.get("exp_date") or "")]
            if _br:
                _sync_batches(channel, _br, conflict_mode)
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
        return s, f, ed
    if target == "outbound":
        return _write_batch("outbound_records",
                            ["sku", "product_name", "quantity", "target_warehouse", "outbound_date",
                             "channel", "prod_date", "exp_date", "warehouse",
                             "barcode", "platform", "brand", "store", "category",
                             "price", "box_qty", "unit", "weight", "volume", "status"],
                            cleaned, conflict_mode, "sku", "outbound_date")
    return 0, len(cleaned), []


def _write_batch(table, allowed_cols, cleaned, conflict_mode, *key_cols):
    """批量写入: 仅保留存在的列; 冲突策略——
    inbound/outbound(sum/overwrite 前端适配面): sum=数量累加, overwrite=全列覆盖
    其他目标(order/inventory/product/supplier): 统一全列覆盖(对齐 PA ON CONFLICT DO UPDATE)"""
    if not cleaned:
        return 0, 0, []
    keys = set(key_cols)
    success = failed = 0
    err_details = []
    qty_cols = [c for c in ("quantity", "available_qty") if c in allowed_cols]
    # sum 累加仅限出入库记录(HammerCleansing 只在 inbound/outbound 暴露 sum/overwrite 选择)
    sum_mode = table in ("inbound_records", "outbound_records") and conflict_mode == "sum"
    for i in range(0, len(cleaned), 500):
        chunk = cleaned[i:i + 500]
        rows = []
        for it in chunk:
            row = {}
            ext = {}
            for c, v in it.items():
                if c == "_meta" or v is None or str(v) == "":
                    continue
                if c in allowed_cols:
                    row[c] = v
                else:
                    # 自定义列(用户动态新增, 非标准字段) → ext_json 合并存储
                    ext[c] = v
            if ext:
                row["ext_json"] = json.dumps(ext, ensure_ascii=False)
            # 严谨性: key 列必须存在且有值(原 `k in row` 跳过缺失列 → 无有效映射的导入
            # 也会写入空记录污染数据); 缺 key 的行判失败
            if not row or not all(k in row and row.get(k) for k in keys):
                failed += 1
                continue
            rows.append(row)
        if not rows:
            continue
        cols = list(rows[0].keys())
        sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (
            table, ", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols)))
        if sum_mode:
            # inbound/outbound + sum: 数量列累加, 其余列覆盖
            upd = []
            for c in cols:
                if c in qty_cols:
                    upd.append("`%s` = `%s` + VALUES(`%s`)" % (c, c, c))
                elif c not in keys and c not in ("id", "deleted_at"):
                    upd.append("`%s` = VALUES(`%s`)" % (c, c))
            if upd:
                sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(upd)
        else:
            # 统一全列覆盖(对齐 PA: 冲突即覆盖新值)
            upd = ", ".join("`%s`=VALUES(`%s`)" % (c, c) for c in cols if c not in ("id", "deleted_at"))
            if upd:
                sql += " ON DUPLICATE KEY UPDATE %s" % upd
        try:
            # 批级事务(2026-10-10): BEGIN/COMMIT 每批——失败回滚该批不留半写入(TiDB 单语句原子外跨行保护)
            execute("BEGIN")
            executemany(sql, [tuple(r[c] for c in cols) for r in rows])
            execute("COMMIT")
            success += len(rows)
        except Exception:
            # 批失败回滚 → 单行降级(逐行事务, 精确 success/failed 计数)
            try:
                execute("ROLLBACK")
            except Exception:
                pass
            for r in rows:
                try:
                    execute("BEGIN")
                    execute(sql, [r[c] for c in cols])
                    execute("COMMIT")
                    success += 1
                except Exception as e1:
                    try:
                        execute("ROLLBACK")
                    except Exception:
                        pass
                    failed += 1
                    if len(err_details) < 50:
                        err_details.append({"sku": str(r.get("sku") or r.get("supplier_code") or ""),
                                            "reason": str(e1)[:80], "row": str(r)[:200]})
    return success, failed, err_details


def _sync_batches(channel, rows, conflict_mode="overwrite"):
    """批次效期同步(入库记录导入含 prod_date/exp_date 列时): 按 SKU×仓 维护 batches
    (效期管控数据源: 进销存批次效期预警/临期处置建议)
    更新方式联动导入冲突模式:
    - overwrite(默认): 文件即权威 —— 删该 SKU×仓 旧批次, 插入文件批次
    - sum: 累加 —— 同 SKU×仓×效期(prod_date+exp_date)批次 qty 累加(多次入库同批次累加), 不同效期独立行"""
    from db import executemany as _em
    if not rows:
        return 0
    touched = set()
    ins = []
    for c in rows:
        sku = c.get("sku")
        if not sku:
            continue
        wh = str(c.get("warehouse") or "")
        wt = str(c.get("warehouse_type") or "")
        pd = str(c.get("prod_date") or "")[:10]
        ed = str(c.get("exp_date") or "")[:10]
        touched.add((sku, wh, wt))
        ins.append({"sku": sku, "warehouse": wh, "warehouse_type": wt, "channel": channel,
                    "prod_date": pd, "exp_date": ed,
                    "qty": int(c.get("available_qty") or c.get("quantity") or c.get("qty") or 0)})
    if conflict_mode == "sum":
        # 累加模式: 同 SKU×仓×效期批次 qty 累加(不同效期=不同批次独立行)
        for it in ins:
            try:
                exist = one("SELECT id, qty FROM batches WHERE sku=%s AND warehouse=%s "
                            "AND channel=%s AND prod_date=%s AND exp_date=%s",
                            [it["sku"], it["warehouse"], it["channel"],
                             it["prod_date"], it["exp_date"]])
                if exist:
                    execute("UPDATE batches SET qty=%s WHERE id=%s",
                            [int(exist.get("qty") or 0) + it["qty"], exist.get("id")])
                else:
                    execute("INSERT INTO batches(sku, warehouse, warehouse_type, channel, "
                            "prod_date, exp_date, qty) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                            [it["sku"], it["warehouse"], it["warehouse_type"], it["channel"],
                             it["prod_date"], it["exp_date"], it["qty"]])
            except Exception as _e:
                    try_err('cleansing', '静默降级', _e)
        return len(ins)
    for sku, wh, wt in touched:
        try:
            execute("DELETE FROM batches WHERE sku=%s AND warehouse=%s AND channel=%s",
                    [sku, wh, channel])
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    for i in range(0, len(ins), 200):
        chunk = ins[i:i + 200]
        cols = list(chunk[0].keys())
        try:
            _em("INSERT INTO batches(%s) VALUES(%s)" % (
                ", ".join("`%s`" % x for x in cols), ", ".join(["%s"] * len(cols))),
                [tuple(r[c] for c in cols) for r in chunk])
        except Exception as _e:
                try_err('cleansing', '静默降级', _e)
    return len(ins)