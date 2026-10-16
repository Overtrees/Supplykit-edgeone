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
from datetime import datetime, timedelta, timezone  # datetime 用于 status 超时标记(缺 datetime 曾 NameError——同步分支/轮询全崩)

from fastapi import APIRouter, Query
from fastapi import Request
from fastapi import UploadFile
from fastapi import File
from fastapi import Form

from db import query, one, execute, executemany
from routes.cleanse_write import _finalize_import, _adjust_inventory, _write_rows, _write_batch, _sync_batches
from routes.common import ok, fail, traced, try_err

router = APIRouter(tags=["cleansing"])

# 目标类型 → 表/仓库类型映射
_TARGET_WH = {"inventory": "own", "platform_inv": "platform", "inventory_b": "platform_b"}
_NUMS = {"number", "price", "quantity", "score", "safety_qty", "available_qty", "in_transit_qty",
         "month_inbound", "month_outbound", "beginning_stock", "turnover_days", "c_transit",
         "locked_qty", "weight", "volume", "box_qty", "total_amount", "unit_price",
         "freight_amount", "subsidy_amount", "tax_amount", "discount_amount", "actual_amount"}


def _mk_upload(data: bytes, filename: str = "file.csv"):
    """构造 UploadFile(raw body/ base64 路径——绕 iOS multipart body 空)"""
    import io
    from starlette.datastructures import UploadFile as _UF, Headers as _H
    # 魔数嗅探: xlsx(zip) PK 头 → filename 置 xlsx——否则退化 csv 当 CSV 解析乱码单列
    if data[:4] == b"PK\x03\x04":
        filename = "file.xlsx"
    return _UF(io.BytesIO(data), filename=filename,
               headers=_H({"content-type": "application/octet-stream"}), size=len(data))


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
    text = text.replace("\x00", "")  # NUL 清洗
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # 2026-10-14 换行归一(\r 老Mac——csv universal newline 防 'new-line character seen')
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
@router.post("/cleansing/driver")
@traced
def cleansing_driver(task_id: str = "", driver: str = "", channel: str = ""):
    """前端轮询驱动标记(2026-10-15): 前端轮询时写 params.driver=frontend——TaskCard 显示推进方式(前端轮询/后端推进)"""
    if task_id and driver in ("frontend", "backend"):
        try:
            execute("UPDATE sync_tasks SET params=JSON_SET(params, '$.driver', %s) WHERE task_id=%s", [driver, task_id])
        except Exception:
            try:
                _row = one("SELECT params FROM sync_tasks WHERE task_id=%s", [task_id])
                _pj = json.loads((_row or {}).get("params") or "{}")
                _pj["driver"] = driver
                execute("UPDATE sync_tasks SET params=%s WHERE task_id=%s", [json.dumps(_pj, ensure_ascii=False), task_id])
            except Exception as _e:
                    try_err('cleansing', 'driver 标记降级', _e)
    return ok({})

@router.post("/cleansing/detect")
@traced
async def cleansing_detect(request: Request, file: UploadFile = File(None)):
    if not file:
        _raw = await request.body()
        if _raw:
            file = _mk_upload(_raw, "file.xlsx")
    # 临时诊断(2026-10-14 422 file missing 定位): 记录请求表单/文件到达情况
    try:
        from db import execute as _ed
        _ed("INSERT INTO quality_logs(log_type, level, message, source) VALUES('perf_debug','info',%s,'cleansing')",
            ("detect 收到 file: %s, filename: %s, size: %s" % (
                bool(file), (file.filename or '')[:40], getattr(file, 'size', None))))
    except Exception:
        pass
    data, fname = _read_file_bytes(file)
    try:
        headers, rows = _parse_table(data, fname)
        return {"ok": True, "columns": [{"name": h} for h in headers], "total": len(rows)}
    except Exception as e:
        return {"ok": False, "error": "文件解析失败: %s" % str(e)[:200]}


# ── preview: 清洗预览(前 50 行) ─────────────────────────────────────────
@router.post("/cleansing/preview")
@traced
async def cleansing_preview(request: Request, file: UploadFile = File(None),
                            q_target: str = Query(""), q_channel: str = Query(""), q_conflict: str = Query(""), q_mp: str = Query("")):
    # 新方式(2026-10-14 根治): raw body 直传(绕 iOS multipart body 空) + 参数走 query
    if not file:
        _raw = await request.body()
        if _raw:
            file = _mk_upload(_raw, "file.csv")
    target = q_target or "order"
    channel = q_channel or "jd"
    conflict_mode = q_conflict or "sum"
    mapping = q_mp or "{}"
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
async def cleansing_execute(request: Request, file: UploadFile = File(None),
                            q_target: str = Query(""), q_channel: str = Query(""), q_conflict: str = Query(""), q_mp: str = Query("")):
    if not file:
        _raw = await request.body()
        if _raw:
            file = _mk_upload(_raw, "file.csv")
    target = q_target or "order"
    channel = q_channel or "jd"
    conflict_mode = q_conflict or "sum"
    mapping = q_mp or "{}"
    import logging as _lg
    """导入(接力式: POST 存源文件+建任务 → GET /cleansing/status 续跑分窗清洗写入,
    防大批量 120s 超时——与导出/seed 同模式; Makers 实例请求后可冻结, 不用纯后台线程)
    返回 {ok, task_id}"""
    # 2026-10-14 防重: 同渠道已有进行中导入任务(误触/重复提交同批) → 拒绝
    try:
        _busy = one("SELECT COUNT(*) AS c FROM sync_tasks WHERE task_type='cleansing' "
                    "AND status='running' AND channel=%s AND updated_at > NOW() - INTERVAL 30 MINUTE", [channel]) or {}
        if int(_busy.get("c") or 0) > 0:
            return {"ok": False, "error": "已有进行中的导入任务，请等待完成后再提交（防止同批重复导入）"}
    except Exception:
        pass
    data, fname = _read_file_bytes(file)
    # 2026-10-15 重复提前检测: 同键已存在(全部重复——二次导入同表)→ 同步返回跳过计数(不建任务——秒级拦截——不等异步 4 分钟)
    try:
        from db import query as _qk
        _dr = _mk_upload(data, fname)
        _bdata, _bfname = _read_file_bytes(_dr)
        try:
            _bh, _brows = _parse_table(_bdata, _bfname)
            if target in ("order",) and _brows:
                # 2026-10-15 修复: 原始行无目标键(中文列名)——需清洗后取键(否则 _keys 空不拦截——重复仍建任务)
                _mapping = json.loads(mapping or "{}")
                _cb = _clean_rows(_brows, _mapping)
                _keys = [str(r.get("order_no") or "") + "|" + str(r.get("sku") or "") for r in _cb if r.get("order_no") and r.get("sku")]
                _uk = list(dict.fromkeys(_keys))
                _exists = 0
                for _i in range(0, len(_uk), 200):
                    _batch = _uk[_i:_i+200]
                    _pairs = [(b.split("|")[0], b.split("|")[1]) for b in _batch]
                    # 同键 EXISTS——分片查
                    _in_ph = ",".join(["%s"] * len(_batch))
                    _sql = ("SELECT COUNT(*) c FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') "
                            "AND CONCAT(order_no,'|',sku) IN (%s)" % ("%s", _in_ph))
                    _cnt = one(_sql, [channel] + _batch)
                    _exists += int((_cnt or {}).get("c") or 0)
                _force_new = request.query_params.get("force_new", "")
                if _keys and not _force_new:
                    _uk_n = len(set(_keys))
                    if _exists >= _uk_n:
                        # 全部已存在——重复导入——同步返回跳过(不建任务)
                        return {"ok": True, "task_id": "", "success": 0, "failed": len(_keys),
                                "total_rows": len(_brows), "duplicate": True,
                                "message": "文件已导入过（同键全部已存在），已跳过 %d 条" % len(_keys)}
                    if _exists > 0:
                        # 部分重叠(2026-10-16): 返回 overlap——前端弹窗"X 条已存在(新增 Y 条)——确认只写新增?"
                        return {"ok": True, "task_id": "", "success": 0, "failed": _exists,
                                "total_rows": len(_brows), "overlap_partial": True,
                                "overlap": _exists, "new": _uk_n - _exists,
                                "message": "%d 条已存在（新增 %d 条）——确认导入新增？" % (_exists, _uk_n - _exists)}
        except Exception:
            pass
    except Exception:
        pass
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
        # 同步分支(2026-10-14): 小行数(<1000)请求内完成秒级返回——不依赖轮询续跑; >=1000 走接力
        try:
            _, rows0 = _parse_table(data, fname)
            if len(rows0) < 1000:
                # 同步分支(2026-10-14 修复): 任务刚建锁未满足(updated_at<3s 返回 running)——sleep 3 等锁; done 才 success, running 返 task_id 前端轮询
                import time as _ts
                _ts.sleep(4)  # 锁需 updated_at < NOW-3s(建任务 3s 后仍边界 false——4s 稳)
                _sr = cleansing_status(task_id, page_size=5000)
                _sd = _sr.get("data") or _sr
                if _sr.get("ok") is not False and _sd.get("status") == "done":
                    return {"ok": True, "task_id": task_id,
                            "success": _sd.get("success", 0), "failed": _sd.get("failed", 0),
                            "total_rows": len(rows0)}
        except Exception:
            pass  # 解析/同步异常 → 走接力(由 status 续跑兜底)
        # 2026-10-15 异步返回 total_rows(前端显示已提交 N 条——非问号): len(rows0) 已解析可用
        _tr = 0
        try:
            _, _r0 = _parse_table(data, fname)
            _tr = len(_r0)
        except Exception:
            pass
        return {"ok": True, "task_id": task_id, "total_rows": _tr}
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




@router.get("/cleansing/status")
@traced
def cleansing_status(task_id: str = "", page_size: int = 5000):
    """导入续跑(前端轮询触发): 每步读源文件 → 解析 → 窗口清洗写入(5000 行/步, ≤120s)
    全部完成 → 最后步全量重清洗做库存联动+规则评估+汇总 → done"""
    row = one("SELECT status, params, result, updated_at FROM sync_tasks WHERE task_id=%s", [task_id])
    if not row:
        return {"ok": False, "error": "任务不存在"}
    status = row.get("status") or ""
    if status == "running":
        # 2026-10-14 超时自愈: running 超过 30 分钟无更新(前端轮询停/实例冻结)——标记 error 防永久卡(释放防重拦截)
        ua = str(row.get("updated_at") or "")[:19]
        if ua and ua < (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"):
            execute("UPDATE sync_tasks SET status='error', result=%s WHERE task_id=%s",
                    [json.dumps({"result": {"error": "任务超时(30 分钟无更新)——已自动标记结束，请重新导入"}}), task_id])
            row["status"] = "error"
    if status in ("done", "error"):
        _ri = (json.loads(row.get("result") or "{}") or {}).get("result") or {}
        return {"ok": True, "task_id": task_id, "status": status,
                "success": _ri.get("success"), "failed": _ri.get("failed"),
                "message": _ri.get("message"), "error": _ri.get("error")}
    # 抢占锁(防并发续跑, 同 seed/导出)
    # 2026-10-16 锁 3s→150s: 窗口5(done 步 26s写+55s联动≈80s) > 锁3s——轮询重试抢锁重跑窗口5→唯一索引冲突→failed +1000 覆盖计数(库全写但计数 4000/1000 假象)——锁覆盖整窗+done——重试不抢锁返回 running 不重跑
    try:
        _locked = execute("UPDATE sync_tasks SET updated_at=NOW() WHERE task_id=%s "
                          "AND (updated_at IS NULL OR updated_at < DATE_SUB(NOW(), INTERVAL 150 SECOND))",
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
        # 读源文件(局部 import base64——覆盖模块级 def _b64(line 399)冲突——曾 _b64.b64decode attr error 任务 error)
        import base64 as _b64x
        fr = one("SELECT content FROM export_files WHERE filename=%s", ["clean_" + task_id])
        c = fr.get("content") if fr else None
        if isinstance(c, str):
            data = _b64x.b64decode(c[7:]) if c.startswith("base64:") else c.encode("utf-8-sig")
        elif c is not None:
            data = bytes(c)
        else:
            raise Exception("源文件不存在")
        headers, rows = _parse_table(data, fname)
        total = len(rows)
        # 分窗清洗+写入(2026-10-15 进度中: 窗口开始即设 cur_step——前端 TaskCard 实时显示进行中)
        _win_no = offset // page_size + 1
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({**params, "cur_step": {"name": "清洗写入窗口 %d/%d" % (_win_no, max((total // page_size) + 1, 1)), "started": int(_t.time())}}, ensure_ascii=False), task_id))
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
        # 2026-10-16 force_new 只写新增: 确认导入后过滤重叠行(unique idx 兜底防重复插入失败)
        if params.get("force_new") and target == "order" and cleaned:
            try:
                _fk = [str(r.get("order_no") or "") + "|" + str(r.get("sku") or "") for r in cleaned if r.get("order_no") and r.get("sku")]
                _fuk = list(dict.fromkeys(_fk))
                _fexists = set()
                for _fi in range(0, len(_fuk), 200):
                    _fb = _fuk[_fi:_fi+200]
                    _fp = ",".join(["%s"] * len(_fb))
                    _frow = query("SELECT CONCAT(order_no,'|',sku) k FROM orders WHERE channel=%s AND (deleted_at IS NULL OR deleted_at='') AND CONCAT(order_no,'|',sku) IN (%s)" % ("%s", _fp), [channel] + _fb)
                    for _r2 in _frow:
                        _fexists.add(_r2.get("k") or "")
                if _fexists:
                    cleaned = [r for r in cleaned if not (str(r.get("order_no") or "") + "|" + str(r.get("sku") or "") in _fexists)]
            except Exception:
                pass
        w_success, w_failed, err_details = _write_rows(target, channel, conflict_mode, cleaned)
        success += w_success
        failed += w_failed
        offset += len(window)
        # 2026-10-16 窗口写后计数留痕(message 含 task_id——唯一不禁频)——窗口5 failed 1000 定位
        try:
            from db import execute as _ew
            _ew("INSERT INTO quality_logs(log_type, level, message, source) VALUES('perf_debug','info',%s,'cleansing')",
                ("[w%d] %s 窗写 success=%d failed=%d offset=%d/%d" % (offset // page_size, task_id, w_success, w_failed, offset, total)))
        except Exception:
            pass
        done = offset >= total
        if done:
            # 最后步: 全量重清洗(联动/评估/汇总用)
            _st0 = _t.time()
            cleaned_all = _clean_rows(rows, mp)
            try_err('cleansing', 'done步 清洗耗时: %s' % round(_t.time() - _st0, 1))
            if target == "order":
                try:
                    from routes.suppliers import _norm_column_value as _ncol2
                    for x in cleaned_all:
                        if x.get("order_status"):
                            x["order_status"] = _ncol2(channel, "order_status", str(x["order_status"]))
                except Exception as _e:
                        try_err('cleansing', '静默降级', _e)
            elapsed = round(_t.time() - started, 1)
            _st1 = _t.time()
            adjusted, evaluated, _msg = _finalize_import(target, channel, mp, cleaned_all, success, failed, elapsed)
            try_err('cleansing', 'done步 finalize(联动+评估)耗时: %s' % round(_t.time() - _st1, 1))
            _steps_done = (params.get("steps") or []) + [{"name": "窗口 %d" % ((offset - len(window)) // page_size + 1), "elapsed": round(_t.time() - started, 1)}]
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW() WHERE task_id=%s",
                    (json.dumps({**params, "offset": offset, "success": success, "failed": failed,
                                 "total": total, "steps": _steps_done, "cur_step": None}, ensure_ascii=False),
                     json.dumps({"result": {"target": target, "success": success, "failed": failed,
                                            "elapsed": elapsed, "rules_evaluated": evaluated,
                                            "inventory_adjusted": adjusted, "message": _msg}},
                                ensure_ascii=False), task_id))
            from routes.analysis_cache import invalidate_all
            invalidate_all()
            return {"ok": True, "task_id": task_id, "status": "done",
                    "success": success, "failed": failed, "message": _msg}
        # 未完 → 更新 offset/累积, running(2026-10-15 每窗 steps 耗时 + cur_step 进度中——前端 TaskCard 可视化)
        _sq = round(_t.time() - started, 1)
        _steps = (params.get("steps") or []) + [{"name": "窗口 %d" % ((offset - len(window)) // page_size + 1), "elapsed": _sq}]
        _cur = {"name": "窗口 %d" % (offset // page_size + 1), "started": int(_t.time())}
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({**params, "offset": offset, "success": success, "failed": failed,
                             "total": total, "steps": _steps, "cur_step": _cur}, ensure_ascii=False), task_id))
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
    """删除清洗模板"""
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
    # 同名模板更新覆盖(name 存在 → UPDATE; 否则 INSERT——防同名重复)
    existing = one("SELECT id FROM cleansing_templates WHERE name=%s", [name])
    if existing:
        execute("UPDATE cleansing_templates SET mapping=%s, doc_type=%s, updated_at=NOW() WHERE id=%s",
                (mapping or "{}", doc_type, existing.get("id")))
    else:
        execute("INSERT INTO cleansing_templates(name, doc_type, mapping) VALUES(%s,%s,%s)",
                (name, doc_type, mapping or "{}"))
    return ok({"message": "模板已保存"})


# ── 写入逻辑(按目标类型) ────────────────────────────────────────────────





