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
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi import Request
from fastapi import UploadFile
from fastapi import File
from fastapi import Form

from db import query, one, execute, executemany
from routes.common import ok, fail, traced

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
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
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
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        sep = dialect.delimiter
    except Exception:
        sep = ","
    reader = csv.reader(io.StringIO(text), delimiter=sep)
    all_rows = list(reader)
    if not all_rows:
        return [], []
    headers = [h.strip() for h in all_rows[0]]
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
    data, fname = _read_file_bytes(file)
    task_id = "clean_%d_%04d" % (int(time.time()), int(time.time() * 1000) % 10000)
    started = time.time()
    try:
        _, rows = _parse_table(data, fname)
        mp = json.loads(mapping or "{}")
        cleaned = _clean_rows(rows, mp)
        if not cleaned:
            return {"ok": True, "task_id": task_id, "success": 0, "failed": 0,
                    "error": "", "message": "文件为空或无有效映射"}
        success, failed = _write_rows(target, channel, conflict_mode, cleaned)
        errs = 0
        elapsed = round(time.time() - started, 1)
        # 规则引擎评估: 订单导入→order.created(超卖), 库存导入→inventory.changed(低库存/紧急补货)
        evaluated = 0
        try:
            from core.rules import evaluate
            if target == "order":
                seen = set()
                skus = [c.get("sku") for c in cleaned
                        if c.get("sku") and not (c.get("sku") in seen or seen.add(c.get("sku")))][:300]
                inv_map = {}
                if skus:
                    ph = ",".join(["%s"] * len(skus))
                    for r in query("SELECT sku, MAX(available_qty) AS avail FROM inventory "
                                   "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % (channel, ph),
                                   [channel] + skus):
                        inv_map[r.get("sku")] = int(r.get("avail") or 0)
                for c in cleaned[:300]:
                    sku = c.get("sku")
                    if not sku:
                        continue
                    oq = int(c.get("quantity") or 0)
                    evaluate("order.created", {"sku": sku, "channel": channel,
                                               "order": {"quantity": oq},
                                               "order_qty": oq,
                                               "available_stock": inv_map.get(sku, 0)})
                    evaluated += 1
            elif target in ("inventory", "platform_inv", "inventory_b"):
                for c in cleaned[:300]:
                    sku = c.get("sku")
                    if not sku:
                        continue
                    evaluate("inventory.changed", {"sku": sku, "channel": channel,
                                                   "inv": {"available_qty": int(c.get("available_qty") or 0),
                                                           "safety_qty": int(c.get("safety_qty") or 0),
                                                           "in_transit_qty": int(c.get("in_transit_qty") or 0),
                                                           "warehouse_type": c.get("warehouse_type", ""),
                                                           "warehouse": c.get("warehouse", ""),
                                                           "product_name": c.get("product_name") or sku}})
                    evaluated += 1
        except Exception:
            pass
        try:
            execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                    "VALUES(%s,'cleansing','done','{}',%s,%s)",
                    (task_id, json.dumps({"result": {"target": target, "success": success,
                                                       "failed": failed, "elapsed": elapsed,
                                                       "rules_evaluated": evaluated}}, ensure_ascii=False), channel))
        except Exception:
            pass
        return {"ok": True, "task_id": task_id, "success": success, "failed": failed,
                "error": "", "message": "成功 %d 条, 跳过 %d 条" % (success, failed),
                "target": target, "rules_evaluated": evaluated}
    except Exception as e:
        try:
            execute("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                    "VALUES(%s,'cleansing','error','{}',%s,%s)",
                    (task_id, json.dumps({"error": str(e)[:400]}, ensure_ascii=False), channel))
        except Exception:
            pass
        return {"ok": False, "error": "清洗失败: %s" % str(e)[:200]}


# ── 任务状态查询(前端轮询 cleansing/task/{id}) ──────────────────────────
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
    except Exception:
        pass
    out = {"status": status}
    if status == "done":
        out["result"] = result.get("result") or result
    elif status == "error":
        out["error"] = result.get("error") or "清洗失败"
    return out


# ── 模板 CRUD ───────────────────────────────────────────────────────────
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
    except Exception:
        pass
    name = d.get("name") or ""
    doc_type = d.get("doc_type") or "order"
    if not name:
        return fail("缺少模板名称")
    mapping = d.get("mapping")
    if isinstance(mapping, dict):
        mapping = json.dumps(mapping, ensure_ascii=False)
    execute("INSERT INTO cleansing_templates(name, doc_type, mapping) VALUES(%s,%s,%s)",
            (name, doc_type, mapping or "{}"))
    return ok({"message": "模板已保存"})


# ── 写入逻辑(按目标类型) ────────────────────────────────────────────────
def _write_rows(target, channel, conflict_mode, cleaned):
    """按 target 分派写入; 返回 (success, failed)"""
    if target == "order":
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
        s, f = _write_batch("inbound_records",
                            ["sku", "product_name", "quantity", "supplier", "inbound_date",
                             "channel", "prod_date", "exp_date", "warehouse"],
                            cleaned, conflict_mode, "sku", "inbound_date")
        return s, f
    if target == "outbound":
        return _write_batch("outbound_records",
                            ["sku", "product_name", "quantity", "target_warehouse", "outbound_date",
                             "channel", "prod_date", "exp_date", "warehouse"],
                            cleaned, conflict_mode, "sku", "outbound_date")
    return 0, len(cleaned)


def _write_batch(table, allowed_cols, cleaned, conflict_mode, *key_cols):
    """批量写入: 仅保留存在的列; 冲突按 conflict_mode(sum 累加数量/else 覆盖)"""
    if not cleaned:
        return 0, 0
    keys = set(key_cols)
    success = failed = 0
    qty_cols = [c for c in ("quantity", "available_qty") if c in allowed_cols]
    sum_mode = conflict_mode == "sum"
    for i in range(0, len(cleaned), 200):
        chunk = cleaned[i:i + 200]
        rows = []
        for it in chunk:
            row = {}
            for c in allowed_cols:
                if c in it and it[c] is not None and str(it[c]) != "":
                    row[c] = it[c]
            if not row or not all(row.get(k) for k in keys if k in row):
                failed += 1
                continue
            rows.append(row)
        if not rows:
            continue
        cols = list(rows[0].keys())
        sql = "INSERT INTO `%s` (%s) VALUES (%s)" % (
            table, ", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols)))
        if sum_mode:
            upd = []
            params_extra = []
            for c in cols:
                if c in qty_cols:
                    upd.append("`%s` = `%s` + VALUES(`%s`)" % (c, c, c))
                elif c not in keys:
                    upd.append("`%s` = VALUES(`%s`)" % (c, c))
            if upd:
                sql += " ON DUPLICATE KEY UPDATE %s" % ", ".join(upd)
            try:
                executemany(sql, [tuple(r[c] for c in cols) for r in rows])
                success += len(rows)
            except Exception:
                # 分批降级: 单行插入, 冲突跳过
                for r in rows:
                    try:
                        execute(sql, [r[c] for c in cols])
                        success += 1
                    except Exception:
                        failed += 1
        else:
            sql = sql.replace("INSERT INTO", "INSERT IGNORE INTO")
            try:
                executemany(sql, [tuple(r[c] for c in cols) for r in rows])
                success += len(rows)
            except Exception:
                for r in rows:
                    try:
                        execute(sql, [r[c] for c in cols])
                        success += 1
                    except Exception:
                        failed += 1
    return success, failed