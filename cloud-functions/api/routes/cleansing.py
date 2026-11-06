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
from datetime import datetime, timedelta  # datetime 用于 status 超时标记(缺 datetime 曾 NameError——同步分支/轮询全崩)
from core.clock import now_cn   # 北京时间: updated_at 由 NOW()(+08:00 会话)写入, 比超时阈值必须同区

from fastapi import APIRouter, Query
from fastapi import Request
from fastapi import UploadFile
from fastapi import File
from fastapi import Form

from db import query, one, execute, executemany
from routes.cleanse_write import _adjust_inventory, _write_rows, _write_batch, _sync_batches
from routes.common import ok, fail, traced, try_err, type_ok as _type_ok

router = APIRouter(tags=["cleansing"])

# ── eval_queue 认领(2026-11-06 S5): SQL 提到模块级, 供 sql_smoke 直接实跑 ──────
#   为什么提到模块级: 这两句的失败模式是**客户端**的(PyMySQL 把 int 参数转成 str,
#   `INTERVAL %d` 一旦进参数位就 TypeError), EXPLAIN 发不到服务端, 只有真跑才暴露。
#   线上连踩两次(先 UPDATE 少一个占位, 再 SELECT 的 %d 进参数位), 故做成冒烟可引用。
_EVAL_CLAIM_TTL = 120   # 认领租约秒数: 冻结的认领者 2 分钟后可被重认领(评估幂等, 重放安全)
_EVAL_SEL_SQL = ("SELECT id, sku FROM eval_queue WHERE task_id=%%s AND applied=0 "
                 "AND (claimed_at IS NULL OR claimed_at < DATE_SUB(NOW(6), INTERVAL %d SECOND)) "
                 "ORDER BY id LIMIT %%s") % _EVAL_CLAIM_TTL
def _eval_done_sql(n):
    """完成标记 SQL(IN 列表按 n 展开)。做成函数而不是就地拼: 就地拼时极易只写
    `IN (%s)` 而把 ids 平铺进参数位 —— 占位符 3 个、参数 5 个 → "not all arguments
    converted"(2026-11-06 第三次同一类踩坑, 就地拼的代码看不到这个错)。"""
    return ("UPDATE eval_queue SET applied=1, applied_at=NOW(6) "
            "WHERE task_id=%%s AND claimed_token=%%s AND id IN (%s)"
            % ",".join(["%s"] * max(int(n), 1)))


_EVAL_CLAIM_SQL = ("UPDATE eval_queue SET claimed_at=NOW(6), claimed_token=%%s "
                   "WHERE task_id=%%s AND applied=0 "
                   "AND (claimed_at IS NULL OR claimed_at < DATE_SUB(NOW(6), INTERVAL %d SECOND)) "
                   "AND id IN (%%s)") % _EVAL_CLAIM_TTL

# 目标类型 → 表/仓库类型映射
from routes.common import TARGET_WH as _TARGET_WH  # 单一真源(2026-10-29)

# 2026-11-02 S2-1: 导入目标 → 缓存失效域。导入一次只写一类表, 没必要清全站缓存。
#   未登记目标(将来新增的导入类型) → 回落 "all", 宁可多清。
_TARGET_DOMAIN = {"order": "orders", "inventory": "inventory", "product": "products",
                  "supplier": "suppliers", "gmv_flow": "flows",
                  "inbound": "inventory", "outbound": "inventory"}

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
    # 2026-11-01 内容嗅探(与文件名解耦): 前端统一用 uploadRawBytes(octet-stream) 发原始字节,
    #   后端拿不到真实文件名, 只能给占位名。原来只按扩展名分支 → detect 的占位名是 "file.xlsx",
    #   用户上传 **CSV** 就被当 xlsx 打开, 直接 "文件解析失败: File is not a zip file"
    #   (实测: curl 与真实前端同样报错, 即 CSV 走这个接口从未成功过)。
    #   xlsx 本质是 zip, 前两字节固定为 "PK"; CSV 不会。按内容校正一次最可靠。
    if data[:2] == b"PK" and not str(fname).endswith(".xlsx"):
        fname = "upload.xlsx"
    if str(fname).endswith(".xlsx"):
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


# ── detect: 识别列名 ────────────────────────────────────────────────────
@router.post("/cleansing/dup-check")
@traced
async def cleansing_dup_check(request: Request, file: UploadFile = File(None),
                              q_target: str = Query(""), q_channel: str = Query(""),
                              q_mp: str = Query("")):
    """2026-10-17 前端提前检测专用(职责: 前端提示/后端写入): 四维键优先(order_no|sku|barcode,
    空 barcode 落二键 order_no|sku)查库已存在——纯只读 SELECT 秒级, 不写入不建任务不拖导入性能
    返回 {duplicate:n(全重), overlap:n(部分), new:n}; 2026-11-02 起订单写入是 upsert,
    前端**只据此提示**(覆盖更新), 不再拦截 —— 详见 cleanse_write._write_batch ord_upsert"""
    try:
        _t = q_target or "order"
        if _t not in ("order", "gmv_flow"):
            return {"ok": True, "duplicate": 0, "overlap": 0, "new": 0}
        if not file:
            _raw = await request.body()
            if _raw:
                file = _mk_upload(_raw, "file.csv")
        data, fname = _read_file_bytes(file)
        _ch = q_channel or "jd"
        _, _rows = _parse_table(data, fname)
        if not _rows:
            return {"ok": True, "duplicate": 0, "overlap": 0, "new": 0}
        _mp = json.loads(q_mp or "{}")
        _cb = _clean_rows(_rows, _mp)
        if _t == "order":
            _keys = [
                (str(r.get("order_no") or "") + "|" + str(r.get("sku") or "")
                 + (("|" + str(r.get("barcode") or "").strip()) if str(r.get("barcode") or "").strip() else ""))
                for r in _cb if r.get("order_no") and r.get("sku")]
            _dcols = ("order_no", "sku", "barcode")
            _tbl, _soft = "orders", " AND (deleted_at IS NULL OR deleted_at='')"
        else:
            _keys = [str(r.get("platform") or "") + "|" + str(r.get("flow_no") or "") for r in _cb
                     if r.get("platform") and r.get("flow_no")]
            _dcols = ("platform", "flow_no")
            _tbl, _soft = "gmv_flows", ""
        _uk = list(dict.fromkeys(_keys))
        if not _uk:
            return {"ok": True, "duplicate": 0, "overlap": 0, "new": 0}
        _exists = 0
        # 2026-11-06 S6/P1-40: `CONCAT_WS(...) IN (...)` → 行构造器。CONCAT 把列包进函数,
        #   索引一定用不上(该端点每次打开导入页都被调, 等于每次整表扫)。
        #   三键(有 69码)/二键(无码)与建键处同一规则: 有码三键、空码降级二键。
        for _i in range(0, len(_uk), 200):
            _batch = _uk[_i:_i + 200]
            _cnt = {"c": 0}
            if len(_dcols) == 3:
                _t3 = [b.split("|", 2) for b in _batch if b.count("|") == 2]
                _t2 = [b.split("|", 1) for b in _batch if b.count("|") == 1]
            else:
                _t3, _t2 = [], [b.split("|", 1) for b in _batch]
            if _t3:
                _ph3 = ",".join(["(%s,%s,%s)"] * len(_t3))
                _q3 = ("SELECT COUNT(*) c FROM `%s` WHERE channel=%%s%s "
                       "AND (`%s`,`%s`,COALESCE(`%s`,'')) IN (%s)"
                       % (_tbl, _soft, _dcols[0], _dcols[1], _dcols[2], _ph3))
                _cnt["c"] += int((one(_q3, [_ch] + [v for _t in _t3 for v in _t]) or {}).get("c") or 0)
            if _t2:
                _ph2 = ",".join(["(%s,%s)"] * len(_t2))
                _q2 = ("SELECT COUNT(*) c FROM `%s` WHERE channel=%%s%s "
                       "AND (`%s`,`%s`) IN (%s)" % (_tbl, _soft, _dcols[0], _dcols[1], _ph2))
                _cnt["c"] += int((one(_q2, [_ch] + [v for _t in _t2 for v in _t]) or {}).get("c") or 0)
            _exists += int(_cnt.get("c") or 0)
        _n = len(set(_keys))
        return {"ok": True, "duplicate": 1 if _exists >= _n else 0,
                "overlap": _exists if 0 < _exists < _n else 0,
                "new": _n - _exists}
    except Exception as _e:
        from routes.common import try_err as _terr
        _terr('cleansing', 'dup-check 降级(放行由后端二键兜底)', _e)
        return {"ok": True, "duplicate": 0, "overlap": 0, "new": 0}

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
            file = _mk_upload(_raw, "file.csv")   # 占位名统一 csv: 真 xlsx 由 _parse_table 内容嗅探纠正
    # 临时诊断(2026-10-14 422 file missing 定位): 记录请求表单/文件到达情况
    # 2026-11-03 S3-4: 原来把 `"..." % (...)` 的**字符串**当 params 传 → pymysql 逐字符当参数
    #   序列, 报 "not all arguments converted", 被 except 吞掉 —— 这条诊断从未落过库。
    #   params 必须是 tuple(str,)。(同类错误在 dashboard_maintain._snapshot_step 有两处)
    try:
        from db import execute as _ed
        _ed("INSERT INTO quality_logs(log_type, level, message, source) VALUES('perf_debug','info',%s,'cleansing')",
            ("detect 收到 file: %s, filename: %s, size: %s" % (
                bool(file), (file.filename or '')[:40], getattr(file, 'size', None)),))
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
    try_err('cleansing', 'execute target=%s fname=%s' % (target, fname))
    # 2026-10-15 重复提前检测: 同键已存在(全部重复——二次导入同表)→ 同步返回跳过计数(不建任务——秒级拦截——不等异步 4 分钟)
    # 2026-10-16 泛化: order(键=order_no|sku) + gmv_flow(键=platform|flow_no) 对称拦截
    #   —— gmv_flow 此前无提前检测(仅 DB 唯一索引兜底, 二次导入全批 1062 失败无因)——用户实测重复导入痛点
    # 2026-10-17 职责边界(用户): 前端负责检测拦截(三键优先 order_no+sku+barcode / 二键兜底),
    #   后端只做二键写入检测(保持性能稳定, 不逐行算三键)
    try:
        from db import query as _qk
        _dr = _mk_upload(data, fname)
        _bdata, _bfname = _read_file_bytes(_dr)
        try:
            _bh, _brows = _parse_table(_bdata, _bfname)
            # 2026-10-29 补齐: 入库/出库 也要**写入前查库去重**
            #   (原只覆盖 order/gmv_flow —— 出入库纯 INSERT, 重复导入会产生重复行)
            #   三键: 69码有值 → 组合键+69码; 空 → 二键降级(与项目既有约定一致)
            #   出入库 sum 模式是"累加"语义, 不拦截(重复导入=再入库一次, 合法)
            # 2026-11-01 库存三类**移出查重**: inventory 是"此刻有多少"的快照, 不是流水。
            #   同键重导 = 更新数值(客户每月导新快照), 不是"重复行"。
            #   写入层已声明 inv_upsert = 同键全列覆盖(cleanse_write.py:961), 但查重挡在它前面 ——
            #   键存在就 return duplicate、根本不建任务 → 覆盖逻辑成了死代码,
            #   客户改完库存数量再导会被静默跳过, 库存永远停在首次导入的值(实测复现)。
            # 2026-11-02 S2-5 订单**移出拦截**: 写入层已改成四维键 upsert(重导幂等), 再在入口
            #   "键存在就 return duplicate、不建任务", 覆盖逻辑同样会变成死代码 —— 与 2026-11-01
            #   库存那次是同一类错误。重导同批的正确结果是"覆盖更新", 而不是"被跳过"。
            #   出入库 sum 模式是"累加"语义(重复导入=再入库一次, 合法), 也不拦截。
            #   gmv_flow 仍是流水且写入层无 upsert → 保留拦截。
            _dup_cfg = {"gmv_flow": ("gmv_flows", "platform", "flow_no", ""),
                        "inbound": ("inbound_records", "sku", "inbound_date", "barcode"),
                        "outbound": ("outbound_records", "sku", "outbound_date", "barcode")}
            # 出入库 sum 模式跳过拦截(累加语义)
            if target in ("inbound", "outbound") and conflict_mode == "sum":
                _dup_cfg = {}
            if target in _dup_cfg and _brows:
                # 2026-10-15 修复: 原始行无目标键(中文列名)——需清洗后取键(否则 _keys 空不拦截——重复仍建任务)
                _tbl_d, _k1_d, _k2_d, _k3_d = _dup_cfg[target]
                _mapping = json.loads(mapping or "{}")
                _cb = _clean_rows(_brows, _mapping)
                _keys = []
                for r in _cb:
                    _v1, _v2 = r.get(_k1_d), r.get(_k2_d)
                    if not (_v1 and _v2):
                        continue
                    _v3 = str(r.get(_k3_d) or "").strip() if _k3_d else ""
                    # 69码有值 → 三键; 空 → 二键降级
                    _keys.append("%s|%s|%s" % (_v1, _v2, _v3) if _v3 else "%s|%s" % (_v1, _v2))
                _uk = list(dict.fromkeys(_keys))
                _exists = 0
                for _i in range(0, len(_uk), 200):
                    _batch = _uk[_i:_i+200]
                    _soft = (" AND (deleted_at IS NULL OR deleted_at='')" if _tbl_d == "orders" else "")
                    # 2026-11-06 S6/P1-40: `CONCAT(k1,'|',k2) IN (...)` → 行构造器
                    #   `(k1,k2) IN ((..),(..))`。理由: CONCAT 把列包在函数里, 索引完全用不上
                    #   (每次导入的查重都是整表扫); 行构造器是标准的等值集合匹配, 能走
                    #   (k1,k2) 前缀索引。另: CONCAT 遇 NULL 返回 NULL, 键会静默消失 ——
                    #   行构造器里 NULL 同样不匹配, 但语义是显式的。
                    if _k3_d:
                        # 三键/二键混合: 69码 有值的用三键, 空值降级二键(与建键处同一规则)
                        _k3 = [b.split("|", 2) for b in _batch if b.count("|") == 2]
                        _k2 = [b.split("|", 1) for b in _batch if b.count("|") == 1]
                        _cnt_v = {"c": 0}
                        if _k3:
                            _ph3 = ",".join(["(%s,%s,%s)"] * len(_k3))
                            _sql3 = ("SELECT COUNT(*) c FROM `%s` WHERE channel=%%s%s "
                                     "AND (`%s`,`%s`,COALESCE(`%s`,'')) IN (%s)"
                                     % (_tbl_d, _soft, _k1_d, _k2_d, _k3_d, _ph3))
                            _c3 = one(_sql3, [channel] + [v for _t in _k3 for v in _t])
                            _cnt_v["c"] += int((_c3 or {}).get("c") or 0)
                        if _k2:
                            _ph2 = ",".join(["(%s,%s)"] * len(_k2))
                            _sql2 = ("SELECT COUNT(*) c FROM `%s` WHERE channel=%%s%s "
                                     "AND (`%s`,`%s`) IN (%s)" % (_tbl_d, _soft, _k1_d, _k2_d, _ph2))
                            _c2 = one(_sql2, [channel] + [v for _t in _k2 for v in _t])
                            _cnt_v["c"] += int((_c2 or {}).get("c") or 0)
                        _cnt = _cnt_v
                    else:
                        _ph_row = ",".join(["(%s,%s)"] * len(_batch))
                        _sql = ("SELECT COUNT(*) c FROM `%s` WHERE channel=%%s%s "
                                "AND (`%s`,`%s`) IN (%s)" % (_tbl_d, _soft, _k1_d, _k2_d, _ph_row))
                        _cnt = one(_sql, [channel] + [v for _b2 in _batch for v in _b2.split("|", 1)])
                    _exists += int((_cnt or {}).get("c") or 0)
                _force_new = request.query_params.get("force_new", "")
                if _keys and not _force_new:
                    _uk_n = len(set(_keys))
                    if _exists >= _uk_n:
                        # 全部已存在——重复导入——同步返回跳过(不建任务)
                        return {"ok": True, "task_id": "", "target": target, "success": 0, "failed": len(_keys),
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
                # 2026-11-06 S5/P0-6: sleep(4) 已删 —— 租约(lease_until=NULL)新建即可抢,
                #   不再需要"睡 4 秒等 3s 窗口打开"(那 4 秒是所有小导入的固定延迟)
                _sr = cleansing_status(task_id, page_size=5000)
                _sd = _sr.get("data") or _sr
                if _sr.get("ok") is not False and _sd.get("status") == "done":
                    return {"ok": True, "task_id": task_id, "target": target,
                            "success": _sd.get("success", 0), "failed": _sd.get("failed", 0),
                            "total_rows": len(rows0),
                            "failed_details": _sd.get("failed_details") or []}
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
def cleansing_status(task_id: str = "", page_size: int = 5000, driver: str = "frontend", probe: int = 0):
    """导入续跑(前端轮询触发): 每步读源文件 → 解析 → 窗口清洗写入(5000 行/步, ≤120s)
    全部完成 → 最后步全量重清洗做库存联动+规则评估+汇总 → done

    probe=1 → 纯读(2026-11-05 竞态根治): 只 SELECT 返回状态, 不写 driver / 不做超时自愈
      写操作 / 不抢锁 / 不续跑。App 层老轮询等「只看结果」的调用方一律走 probe,
      推进统一交给 taskWatch 看守(全仓单一驱动源)。原驱动链路一字未改。
    """
    # ① 纯读: 早于 mark_driver 与 30 分钟超时自愈(写操作), 保证零副作用
    if probe:
        if not task_id:
            return {"ok": True, "status": "not_found"}
        _p = one("SELECT status, params, result, created_at, updated_at FROM sync_tasks "
                 "WHERE task_id=%s", [task_id])
        if not _p:
            return {"ok": True, "status": "not_found"}
        _st = _p.get("status") or ""
        _d = {"ok": True, "task_id": task_id, "status": _st, "probe": True,
              "created_at": str(_p.get("created_at") or ""),
              "updated_at": str(_p.get("updated_at") or "")}
        try:
            _pj = json.loads(_p.get("params") or "{}") or {}
            _d["offset"] = int(_pj.get("offset") or 0)
            _d["total"] = int(_pj.get("total") or 0)
        except Exception:
            pass
        if _st in ("done", "error"):
            _ri = (json.loads(_p.get("result") or "{}") or {}).get("result") or {}
            _d.update({"success": _ri.get("success"), "failed": _ri.get("failed"),
                       "message": _ri.get("message"), "error": _ri.get("error"),
                       "failed_details": _ri.get("failed_details") or []})
        return _d
    # ② 驱动模式(taskWatch 看守): 保持原有语义
    # 2026-11-06 P0-1 防御纵深: **先校验类型再写任何东西**。
    #   原来 mark_driver 在 SELECT 之前、且不校验 task_type —— export/cleansing_eval 的 id
    #   打进来会被当清洗任务推进: 读不到 clean_<id> → 抛"源文件不存在" → 下面的 except
    #   执行 UPDATE status='error', 导出 3 秒内被判死。详见 common.type_ok 的 docstring。
    row = one("SELECT status, params, result, updated_at, task_type FROM sync_tasks "
              "WHERE task_id=%s", [task_id])
    if not row:
        return {"ok": False, "error": "任务不存在"}
    if not _type_ok(row, "cleansing"):
        return {"ok": True, "task_id": task_id, "status": "not_found"}
    from routes.common import mark_driver as _mkdrv
    _mkdrv(task_id, driver)
    status = row.get("status") or ""
    if status == "running":
        # 2026-10-14 超时自愈: running 超过 30 分钟无更新(前端轮询停/实例冻结)——标记 error 防永久卡(释放防重拦截)
        ua = str(row.get("updated_at") or "")[:19]
        if ua and ua < (now_cn() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"):
            execute("UPDATE sync_tasks SET status='error', result=%s WHERE task_id=%s",
                    [json.dumps({"result": {"error": "任务超时(30 分钟无更新)——已自动标记结束，请重新导入"}}), task_id])
            row["status"] = "error"
    if status in ("done", "error"):
        _ri = (json.loads(row.get("result") or "{}") or {}).get("result") or {}
        return {"ok": True, "task_id": task_id, "status": status,
                "success": _ri.get("success"), "failed": _ri.get("failed"),
                "message": _ri.get("message"), "error": _ri.get("error"),
                "failed_details": _ri.get("failed_details") or []}
    # 抢占锁(2026-11-06 S5/P0-6: 3s updated_at 窗口锁 → 通用租约(令牌+心跳+TTL))。
    #   窗间不持锁(每窗 acquire/release), done 步防重跑仍用 finalizing 标记。
    _stop_beat = None
    _lk = ""
    from core import task_lease as _lease
    _lk, _got = _lease.acquire(task_id)
    if not _got:
        return {"ok": True, "task_id": task_id, "status": "running"}
    _stop_beat = _lease.start_beat(task_id, _lk)
    import time as _t
    started = _t.time()
    try:
        params = json.loads(row.get("params") or "{}")
        fname = params.get("fname") or "import.csv"
        # 2026-10-16 done 步中(finalizing=1)→ 返回 running 不重跑(锁已释放但 done 步进行中——防重跑覆盖计数)
        # 超时恢复: finalizing>5分钟且无更新(updated_at 旧——done 步请求被 Makers 冻结/进程死——连接死 TiDB 自动回滚未落库)
        # → 清标记允许重跑 done 步(事务幂等——重跑干净——根治进程死永久卡)
        try:
            if params.get("finalizing"):
                _fs = one("SELECT updated_at FROM sync_tasks WHERE task_id=%s", [task_id])
                _fu = str((_fs or {}).get("updated_at") or "")[:19].replace("T", " ")
                if _fu and _fu < (now_cn() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"):
                    execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                            [json.dumps({**params, "finalizing": 0}, ensure_ascii=False), task_id])
                    try_err('cleansing', 'finalizing 超时恢复(>5min——done 步死连接已回滚——重跑)', '')
                else:
                    return {"ok": True, "task_id": task_id, "status": "running"}
        except Exception:
            pass
        mp = json.loads(params.get("mp") or "{}")
        target = params.get("target") or "order"
        channel = params.get("channel") or "jd"
        conflict_mode = params.get("conflict_mode") or "sum"
        offset = int(params.get("offset") or 0)
        success = int(params.get("success") or 0)
        failed = int(params.get("failed") or 0)
        # 2026-11-06 S5/P0-4: 导入分片真源 = import_stage 表。
        #   原"每窗重读 BLOB + 重解析整份 + 重清洗整份"是 O(n²) —— 100 万行不可完成
        #   (P0-4); 物化一次(O(n))后, 每窗按 row_no 直读本窗口, done 步全量复用同表。
        if not params.get("staged"):
            # ── 物化: 读 BLOB → 解析 → 清洗 → 落 stage(每批 1000 行 executemany) ──
            import base64 as _b64x
            fr = one("SELECT content FROM export_files WHERE filename=%s", ["clean_" + task_id])
            c = fr.get("content") if fr else None
            if isinstance(c, str):
                data = _b64x.b64decode(c[7:]) if c.startswith("base64:") else c.encode("utf-8")
            elif c is not None:
                data = bytes(c)
            else:
                raise Exception("源文件不存在")
            _headers, _rows = _parse_table(data, fname)
            _staged = _clean_rows(_rows, mp)
            if target == "order":
                # order_status 归一化在物化时做一次(分窗/done 步都不再做, 语义一致)
                try:
                    from routes.suppliers import _norm_column_value as _ncol0
                    for x in _staged:
                        if x.get("order_status"):
                            x["order_status"] = _ncol0(channel, "order_status", str(x["order_status"]))
                except Exception as _e0:
                    try_err('cleansing', '静默降级', _e0)
            _stage_rows = []
            for _sno, _sc in enumerate(_staged):
                _stage_rows.append((task_id, _sno, str(_sc.get("sku") or ""),
                                    json.dumps(_sc, ensure_ascii=False)))
            for _si in range(0, len(_stage_rows), 1000):
                executemany("INSERT IGNORE INTO import_stage(task_id, row_no, sku, payload) "
                            "VALUES(%s,%s,%s,%s)", _stage_rows[_si:_si + 1000])
            params = {**params, "staged": 1, "total": len(_rows),
                      "stage_rows": len(_stage_rows)}
            execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                    (json.dumps(params, ensure_ascii=False), task_id))
            try_err('cleansing', 'stage 物化完成: %d 行(%s)' % (len(_stage_rows), task_id), '')
        total = int(params.get("total") or 0)
        # 分窗写入(2026-10-15 进度中: 窗口开始即设 cur_step——前端 TaskCard 实时显示进行中)
        _win_no = offset // page_size + 1
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({**params, "cur_step": {"name": "清洗写入窗口 %d/%d" % (_win_no, max((total // page_size) + 1, 1)), "name_key": "task.win_clean", "p": {"n": _win_no, "m": max((total // page_size) + 1, 1)}, "started": int(_t.time())}}, ensure_ascii=False), task_id))
        # 本窗口直读 stage(不再解析/清洗 —— 物化时已做)
        cleaned = []
        for _sr2 in query("SELECT payload FROM import_stage WHERE task_id=%s "
                          "ORDER BY row_no LIMIT %s OFFSET %s",
                          [task_id, page_size, offset]):
            try:
                cleaned.append(json.loads(_sr2.get("payload") or "{}"))
            except Exception:
                continue
        window_len = len(cleaned)
        # 2026-10-16 force_new 只写新增: 确认导入后过滤重叠行(unique idx 兜底防重复插入失败)
        #   —— 泛化 2026-10-16: order(键=order_no|sku) + gmv_flow(键=platform|flow_no) 对称
        # 2026-10-17 职责边界(用户): 前端检测三键优先, 后端 force_new 保持二键(写入性能稳定)
        if params.get("force_new") and target in ("order", "gmv_flow") and cleaned:
            try:
                _fk_cfg = {"order": ("orders", "order_no", "sku"),
                           "gmv_flow": ("gmv_flows", "platform", "flow_no")}
                _ftbl, _fk1, _fk2 = _fk_cfg[target]
                _fkeys = [str(r.get(_fk1) or "") + "|" + str(r.get(_fk2) or "") for r in cleaned
                          if r.get(_fk1) and r.get(_fk2)]
                _fuk = list(dict.fromkeys(_fkeys))
                _fexists = set()
                for _fi in range(0, len(_fuk), 200):
                    _fb = _fuk[_fi:_fi+200]
                    _fsoft = (" AND (deleted_at IS NULL OR deleted_at='')" if _ftbl == "orders" else "")
                    # 2026-11-06 S6/P1-40: 同上, CONCAT 查重 → 行构造器(键在 Python 端拼,
                    #   不再让 DB 把两列拼成字符串; CONCAT 包列 = 索引必然失效)
                    _fp2 = ",".join(["(%s,%s)"] * len(_fb))
                    _frow = query("SELECT `%s` AS k1, `%s` AS k2 FROM `%s` WHERE channel=%%s%s "
                                  "AND (`%s`,`%s`) IN (%s)" % (_fk1, _fk2, _ftbl, _fsoft,
                                                               _fk1, _fk2, _fp2),
                                  [channel] + [v for _b3 in _fb for v in _b3.split("|", 1)])
                    for _r2 in _frow:
                        _fexists.add("%s|%s" % (_r2.get("k1") or "", _r2.get("k2") or ""))
                if _fexists:
                    cleaned = [r for r in cleaned if not (str(r.get(_fk1) or "") + "|" + str(r.get(_fk2) or "") in _fexists)]
            except Exception:
                pass
        w_success, w_failed, err_details = _write_rows(target, channel, conflict_mode, cleaned)
        success += w_success
        failed += w_failed
        # 2026-10-16 失败明细累积(跨窗口→done 回传前端 failed_details——曾窗口间丢弃无因可查)
        _fd = list(params.get("err_details") or [])
        for _e1 in (err_details or []):
            if len(_fd) < 50:
                _fd.append(_e1)
        offset += window_len
        # 2026-10-16 窗口写后计数留痕(message 含 task_id——唯一不禁频)——窗口5 failed 1000 定位
        try:
            from db import execute as _ew
            _ew("INSERT INTO quality_logs(log_type, level, message, source) VALUES('perf_debug','info',%s,'cleansing')",
                ("[w%d] %s 窗写 success=%d failed=%d offset=%d/%d" % (offset // page_size, task_id, w_success, w_failed, offset, total)))
        except Exception:
            pass
        done = offset >= total
        if done:
            # 2026-10-16 done 步前置 finalizing 标记(防重跑: 锁3s会释放——重试重跑窗口5→唯一索引冲突→failed 覆盖——标记后重试不重跑)
            try:
                from db import execute as _ef
                _ef("UPDATE sync_tasks SET params=%s WHERE task_id=%s",
                    [json.dumps({**params, "finalizing": 1, "success": success, "failed": failed, "offset": offset, "total": total}, ensure_ascii=False), task_id])
            except Exception:
                pass
            # 最后步: 全量行(stage 流式读, 2000/批 —— 不再"全量重清洗整份", P0-4 的另一半)
            _st0 = _t.time()
            cleaned_all = []
            _stage_off = 0
            while True:
                _srp = query("SELECT payload FROM import_stage WHERE task_id=%s "
                             "ORDER BY row_no LIMIT 2000 OFFSET %s", [task_id, _stage_off])
                if not _srp:
                    break
                for _sr3 in _srp:
                    try:
                        cleaned_all.append(json.loads(_sr3.get("payload") or "{}"))
                    except Exception:
                        continue
                _stage_off += len(_srp)
                if len(_srp) < 2000:
                    break
            # 2026-11-01 效期标记值校验(列必填由前端拦, 这里兜"填了但值不合法/为空"):
            #   _norm_expiry 只认 是/否(含同义词), 其余返回 '?' → _write_batch 留空且仅记质量日志。
            #   导入结果页看不到 = 运营以为标了、实际全走"按做效期处理+告警" → 在此显性回到 message。
            _exp_warn = ""
            if target == "product":
                try:
                    from routes.cleanse_write import _norm_expiry as _ne
                    _n_bad = sum(1 for _c in cleaned_all
                                 if _ne(_c.get("expiry_managed")) not in ("yes", "no"))
                    if _n_bad:
                        _exp_warn = (" · 注意: %d 条「是否做效期管理」为空或无法识别(仅接受 是/否), "
                                     "这些商品出入库将按「做效期」处理并告警" % _n_bad)
                except Exception as _eexp:
                    try_err('cleansing', '效期标记值校验降级', _eexp)
            # 2026-11-01 库存导入: 效期商品只有期初(无到期日批次) → 临期预警覆盖不到, 必须让人看见
            #   判定依据是商品页 expiry_managed(用户定案), 只看自有仓(平台仓/B仓是平台快照, 无批次概念)
            if target == "inventory":
                try:
                    from routes.cleanse_write import _expiry_batch_gap as _ebg
                    _gap = _ebg(channel)
                    if _gap:
                        _exp_warn += (" · 注意: 自有仓有 %d 行效期管理商品当前没有带到期日的批次, "
                                      "这部分库存不会进临期预警(批次只能由含生产/到期日的入库记录产生)"
                                      % _gap)
                except Exception as _eg:
                    try_err('cleansing', '效期批次缺口提示降级', _eg)
            # 2026-11-01 入库导入: 效期商品缺到期日 → 静默无批次
            #   (该提示原写在 _finalize_import 里, 但那个函数自 2026-10-16 拆分成
            #    cleansing_status + cleansing_eval_status 后**再未被调用** → 提示从未生效)
            if target == "inbound":
                try:
                    from routes.cleanse_write import _expiry_missing_count as _emc
                    _miss_n = _emc(channel, cleaned_all)
                    if _miss_n:
                        _exp_warn += (" · 注意: %d 行是效期管理商品但无到期日期, 未生成批次"
                                      "(临期预警/处置建议会缺这批货)" % _miss_n)
                except Exception as _ei:
                    try_err('cleansing', '入库效期缺失提示降级', _ei)
            try_err('cleansing', 'done步 stage 读取耗时: %s' % round(_t.time() - _st0, 1))
            # order_status 归一化已在物化时做过一次(S5/P0-4), done 步不再重复
            elapsed = round(_t.time() - started, 1)
            # 2026-10-16 分离: 导入 done(写 done 计数——联动评估由独立 cleansing_eval 任务续跑分片——根治 done 步 80s 请求被杀)
            _steps_done = (params.get("steps") or []) + [{"name": "窗口 %d" % ((offset - window_len) // page_size + 1), "name_key": "task.win", "p": {"n": (offset - window_len) // page_size + 1}, "elapsed": round(_t.time() - started, 1)}]
            execute("UPDATE sync_tasks SET status='done', params=%s, result=%s, updated_at=NOW(), "
                    "lease_until=NULL, lease_token='' WHERE task_id=%s",
                    (json.dumps({**params, "offset": offset, "success": success, "failed": failed,
                                 "total": total, "steps": _steps_done, "cur_step": None}, ensure_ascii=False),
                     json.dumps({"result": {"target": target, "success": success, "failed": failed,
                                            "elapsed": elapsed,
                                            "message": "成功 %d 条, 跳过 %d 条 · 评估联动已创建独立任务" % (success, failed) + _exp_warn,
                                            "failed_details": _fd if _fd else [],
                                            "eval_task": task_id + "_eval"}}, ensure_ascii=False), task_id))
            # 2026-10-16 分离: 创建评估联动任务(独立续跑——分片评估不超时——导入 done 后自动触发)
            try:
                # 2026-11-06 S5/P0-3: SKU 清单直接从 import_stage 派生进 eval_queue(一条 SQL,
                #   不再内存提取几百 KB 塞 params)。
                _ee2 = execute
                _ee2("INSERT IGNORE INTO eval_queue(task_id, sku) "
                     "SELECT DISTINCT %s, sku FROM import_stage "
                     "WHERE task_id=%s AND sku IS NOT NULL AND sku!=''",
                     [task_id + "_eval", task_id])
                _ev_cnt = int((one("SELECT COUNT(*) AS c FROM eval_queue WHERE task_id=%s",
                                   [task_id + "_eval"]) or {}).get("c") or 0)
                _eval_task = task_id + "_eval"
                # 2026-10-29 修复: 导入失败(success=0)时 sku_list 为空, 仍创建 running 任务
                # → 无分片可推进, 任务页永久显示"进行中 0%"(实测卡 64 分钟, 需保活/前端拾起)
                if not _ev_cnt:
                    _ee2("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                         "VALUES(%s,'cleansing_eval','done',%s,%s,%s) "
                         "ON DUPLICATE KEY UPDATE status='done', params=VALUES(params), updated_at=NOW()",
                         [_eval_task, json.dumps({"src_task": task_id, "target": target, "channel": channel,
                                                  "offset": 0}, ensure_ascii=False),
                          json.dumps({"result": {"target": target, "success": 0, "failed": 0,
                                                 "rules_evaluated": 0, "inventory_adjusted": 0,
                                                 "message": "无可评估 SKU(导入全部失败或映射无 SKU 列)"}},
                                     ensure_ascii=False), channel])
                else:
                    _ee2("INSERT INTO sync_tasks(task_id, task_type, status, params, result, channel) "
                         "VALUES(%s,'cleansing_eval','running',%s,'{}',%s) "
                         "ON DUPLICATE KEY UPDATE status='running', params=VALUES(params), updated_at=NOW()",
                         [_eval_task, json.dumps({"src_task": task_id, "target": target, "channel": channel,
                                                  "mp": mp, "fname": fname, "offset": 0, "success": 0, "failed": 0,
                                                  "total": _ev_cnt, "steps": [],
                                                  "cur_step": {"name": "评估联动准备", "name_key": "task.eval_prep", "started": int(_t.time())}}, ensure_ascii=False), channel])
            except Exception as _e4:
                try_err('cleansing', '创建评估任务降级', _e4)
            # 2026-11-06 事件驱动(用户口径: 清洗页导入数据就该自动联动评估):
            #   原来 `_eval` 任务建完就撒手 —— 只有前端看守(且看守把 cleansing_eval 当成
            #   cleansing 打错端点, 永远推不动) 和 60s 兜底会碰它。线上实证: 低库存告警的
            #   created_at 散落在 12:58/13:06/13:13 几个不相邻批次, 全是"有人碰兜底"才补的。
            #   现在建完立刻在后台线程把分片跑完(与看守/兜底**同一条代码路径**), 导入即联动。
            #   线程被冻结也不影响正确性: 任务仍 running, 兜底/看守/手工照旧能接上。
            if _ev_cnt:
                try:
                    from core.eval_kick import kick_eval_task as _kick_ev
                    _kick_ev(task_id, target, channel)
                except Exception as _ek:
                    try_err('cleansing', '评估联动事件推进启动降级', _ek)
            # 2026-11-06 S2-1: 导入写完库存 → 采购需求同步重判。
            #   与上面的评估 kick 是**两条独立链**: 评估管规则告警(低库存/超卖/效期),
            #   这里管采购需求(purchase_need)。原来后者挂在"打开采购页"上 ——
            #   导完不点采购页, 卡片上永远看不到新需求。
            try:
                from core.alert_jobs import kick_purchase_alerts as _kick_pa
                _kick_pa(channel)
            except Exception as _ekp:
                try_err('cleansing', '导入后采购告警同步启动降级', _ekp)
            # 2026-11-06 P1-6: 数据新鲜度写入。
            #   原来唯一的写入点在 `cleanse_write._finalize_import()` 里, 而那个函数自
            #   2026-10-16 拆分成 cleansing_status + cleansing_eval_status 之后**再无调用点**
            #   (函数头自己写着"已废弃/勿调用") —— 于是 data_freshness 永远是空表,
            #   `/data-freshness` 只能靠 misc.py 的兜底 SQL 反推, 而那三条兜底只覆盖
            #   order / inbound / outbound。inventory(补货与断货的直接依据)、platform_inv、
            #   inventory_b、product、supplier、gmv_flow 六类**从不出现**,"数据截至"对它们
            #   完全失明: 库存停更三周, 界面照样显示一切正常。
            #   放在 done 分支 = 只有真正写完才记账(中途失败不写, 免得谎报"刚导过")。
            #   rows_imported 用覆盖不用累加 —— 它的语义是"上一批导了多少行",
            #   累加会让重跑一次导入(幂等重导)变成双倍计数。
            try:
                from db import execute as _efr
                _bizd = ""
                for _c in (cleaned_all or []):
                    for _k in ("ordered_at", "inbound_date", "outbound_date", "date",
                               "arrived_time", "paid_at", "arrival_date"):
                        _v = str(_c.get(_k) or "")[:10]
                        if _v and _v > _bizd:
                            _bizd = _v
                _efr("INSERT INTO data_freshness(doc_type, channel, last_import_at, "
                     "last_biz_date, rows_imported) VALUES(%s,%s,NOW(),%s,%s) "
                     "ON DUPLICATE KEY UPDATE last_import_at=NOW(), "
                     "last_biz_date=VALUES(last_biz_date), rows_imported=VALUES(rows_imported)",
                     [target, channel, _bizd, success + failed])
            except Exception as _frx:
                try_err('cleansing', '新鲜度记录降级', _frx)
            from routes.analysis_cache import invalidate
            invalidate(_TARGET_DOMAIN.get(target, "all"))
            return {"ok": True, "task_id": task_id, "status": "done",
                    "success": success, "failed": failed,
                    "message": "成功 %d 条, 跳过 %d 条 · 评估联动已创建独立任务" % (success, failed) + _exp_warn,
                    "failed_details": _fd if _fd else []}
        # 未完 → 更新 offset/累积, running(2026-10-15 每窗 steps 耗时 + cur_step 进度中——前端 TaskCard 可视化)
        _sq = round(_t.time() - started, 1)
        _steps = (params.get("steps") or []) + [{"name": "窗口 %d" % ((offset - window_len) // page_size + 1), "name_key": "task.win", "p": {"n": (offset - window_len) // page_size + 1}, "elapsed": _sq}]
        _cur = {"name": "窗口 %d" % (offset // page_size + 1), "name_key": "task.win", "p": {"n": offset // page_size + 1}, "started": int(_t.time())}
        execute("UPDATE sync_tasks SET params=%s, updated_at=NOW() WHERE task_id=%s",
                (json.dumps({**params, "offset": offset, "success": success, "failed": failed,
                             "total": total, "steps": _steps, "cur_step": _cur}, ensure_ascii=False), task_id))
        return {"ok": True, "task_id": task_id, "status": "running",
                "page": offset // page_size, "total": total}
    except Exception as e:
        import traceback as _tb
        execute("UPDATE sync_tasks SET status='error', result=%s, updated_at=NOW(), "
                "lease_until=NULL, lease_token='' WHERE task_id=%s",
                (json.dumps({"error": str(e)[:400], "tb": _tb.format_exc()[-1200:]},
                            ensure_ascii=False), task_id))
        try_err("cleansing", "导入续跑失败", e)
        return {"ok": True, "task_id": task_id, "status": "error", "error": str(e)[:200]}
    finally:
        # 2026-11-06 S5/P0-6: 窗口结束/异常都停心跳+释放(窗间不持锁)
        if _stop_beat:
            _stop_beat()
        if _lk:
            _lease.release(task_id, _lk)


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
    rows = query("SELECT id, name, doc_type, mapping, cf_meta FROM cleansing_templates ORDER BY id DESC LIMIT 100")
    return ok(rows)


@router.get("/cleansing/eval-status")
@traced
def cleansing_eval_status(task_id: str = "", page_size: int = 1000, driver: str = "frontend", probe: int = 0):
    """2026-10-16 评估联动独立任务——分窗接力(每请求 ≤90s 安全——SKU 清单驱动——不重读源)
    读 sku_list → 处理 offset 起 page_size 个 SKU(评估+联动该片——幂等 existing 去重+事务联动) → offset 推进 → done

    probe=1 → 纯读(2026-11-05 竞态根治): 只 SELECT 返回进度快照, 不写 driver / 不抢锁 / 不推进。
      与 /seed/fill/status、/cleansing/status 三类任务口径保持一致。
    """
    import time as _t
    if probe:
        if not task_id:
            return {"ok": True, "status": "not_found"}
        _p = one("SELECT status, params, result FROM sync_tasks WHERE task_id=%s", [task_id])
        if not _p:
            return {"ok": True, "status": "not_found"}
        _st = _p.get("status") or ""
        _d = {"ok": True, "task_id": task_id, "status": _st, "probe": True}
        try:
            _pj = json.loads(_p.get("params") or "{}") or {}
            _d["offset"] = int(_pj.get("offset") or 0)
            _d["total"] = len(_pj.get("sku_list") or [])
            if not _d["total"]:
                # 2026-11-06 S5/P0-3: 新任务清单在 eval_queue, params 不再带 sku_list
                _d["total"] = int((one("SELECT COUNT(*) AS c FROM eval_queue WHERE task_id=%s",
                                       [task_id]) or {}).get("c") or 0)
        except Exception:
            pass
        if _st in ("done", "error"):
            _ri = (json.loads(_p.get("result") or "{}") or {}).get("result") or {}
            _d.update({"success": _ri.get("success"), "failed": _ri.get("failed"),
                       "error": _ri.get("error"),
                       "rules_evaluated": _ri.get("rules_evaluated"),
                       "inventory_adjusted": _ri.get("inventory_adjusted")})
        return _d
    # 2026-11-06 P0-1 防御纵深: 先判类型再写。
    #   反方向误杀更隐蔽也更危险: 若把 cleansing(导入)任务的 id 打到这里, params 里没有
    #   sku_list → `_batch = []` → 下面的"全部分片完成"分支直接 UPDATE status='done' ——
    #   一个只写了一半的导入会被标成"成功完成", 数据静默残缺。详见 common.type_ok。
    row = one("SELECT status, params, result, task_type FROM sync_tasks WHERE task_id=%s",
              [task_id])
    if not row:
        return {"ok": False, "error": "任务不存在"}
    if not _type_ok(row, "cleansing_eval"):
        return {"ok": True, "task_id": task_id, "status": "not_found"}
    from routes.common import mark_driver as _mkdrv
    _mkdrv(task_id, driver)
    status = row.get("status") or ""
    if status in ("done", "error"):
        _ri = (json.loads(row.get("result") or "{}") or {}).get("result") or {}
        return {"ok": True, "task_id": task_id, "status": status, "success": _ri.get("success"),
                "failed": _ri.get("failed"), "error": _ri.get("error"),
                "rules_evaluated": _ri.get("rules_evaluated"), "inventory_adjusted": _ri.get("inventory_adjusted")}
    params = json.loads(row.get("params") or "{}")
    channel = params.get("channel") or "jd"
    target = params.get("target") or "order"
    started = _t.time()
    # 2026-11-06 S5/P0-3: SKU 清单出 params, 落 eval_queue(行级, applied 标记即幂等键)。
    #   原 sku_list 全量塞 params JSON: 2 万 SKU ≈ 数百 KB, 每次 offset 推进都要整份
    #   读改写(64KB 上限风险 + params 变成"既当配置又当游标")。清单只写一次进表,
    #   params 里只剩 src_task/target/channel 这些**真配置**。
    _ev_skus = params.get("sku_list") or []      # 兼容旧任务(迁移期可能有 running 行)
    _ev_total = len(_ev_skus)
    _offset = int(params.get("offset") or 0)     # 进度游标(展示/旧路径取片; 新路径取片按 applied=0)
    if not _ev_skus:
        try:
            _ev_total = int((one("SELECT COUNT(*) AS c FROM eval_queue WHERE task_id=%s",
                                 [task_id]) or {}).get("c") or 0)
        except Exception:
            _ev_total = 0
    _stop_beat = None
    _lk = ""
    try:
        # 抢占锁(2026-11-06 S5/P0-6: 3s updated_at 窗口锁 → 通用租约(令牌+心跳+TTL)。
        #   3s 窗口挡不住"看守+event 线程+兜底"三路同时轮询, 同片执行两遍 → sum 双算)
        from core import task_lease as _lease
        _lk, _got = _lease.acquire(task_id)
        if not _got:
            return {"ok": True, "task_id": task_id, "status": "running"}
        _stop_beat = _lease.start_beat(task_id, _lk)
        if _ev_skus:
            _batch = _ev_skus[_offset:_offset + page_size]
            _ids = None
        else:
            # 新路径: 从 eval_queue 取未应用分片(applied=0)。applied 标记即幂等键 ——
            #   处理成功才置 1, 重试不会重放同一批 SKU。
            # 认领是**租约**不是完成标记(2026-11-06 线上验证实证): 原来"领取即置位
            #   applied=1"后实例被冻结 → 兜底接手时未领行已空 → 直接 done, 冻结丢失的
            #   评估**永久无法重放**(兜底安全网被静默打穿, 本次实测 result/steps/库存
            #   重算全部落空)。现在 applied=完成标记(处理成功才置 1, 见分片写尾),
            #   claimed_at+token=认领租约(超 _CLAIM_TTL 可被重认领; 评估本身幂等,
            #   重放不双算: 告警 upsert / 库存重算制)。
            _ids = []
            _batch = []
            _claim_tok = ""
            # TTL 已在模块级常量里 Python 级内联(PyMySQL 会把 int 参数转成 str,
            #   进 %d 参数位必 TypeError —— 见 _EVAL_SEL_SQL 上方说明)。
            for _qr in query(_EVAL_SEL_SQL, [task_id, page_size]):
                _ids.append(int(_qr.get("id") or 0))
                _batch.append(str(_qr.get("sku") or ""))
            if _ids:
                _ph8 = ",".join(["%s"] * len(_ids))
                _claim_tok = _lk or ("t%d" % int(_t.time() * 1000))
                # 占位符: token/task/IN 列表是 %s, TTL 已内联。四对四 —— 漏一个就是
                #   "TypeError: %d format"(本次实测)。
                _claimed = execute(_EVAL_CLAIM_SQL % ("%s", "%s", _ph8),
                                   [_claim_tok, task_id] + _ids)
                if _claimed != len(_ids):
                    # 被别的实例抢先认领(重试/并发), 本片跳过 —— 下一轮重选可认领的
                    _batch, _ids, _claim_tok = [], [], ""
        if not _batch:
            # applied=0 但认领租约未到期 = **别的驱动处理中**, 不是完成 —— 提前 done
            # 会重演"冻结丢评估"(_total 未清空时 result 还是空壳)。只有 applied 全 1 才算完。
            _left = int((one("SELECT COUNT(*) AS c FROM eval_queue WHERE task_id=%s AND applied=0",
                             [task_id]) or {}).get("c") or 0)
            if _left:
                return {"ok": True, "task_id": task_id, "status": "running", "waiting_claim": True}
            # 真完成 → done(累计 result)
            _rj = json.loads(row.get("result") or "{}") or {}
            _rri = _rj.get("result") or {}
            execute("UPDATE sync_tasks SET status='done', params=%s, updated_at=NOW(), "
                    "lease_until=NULL, lease_token='' WHERE task_id=%s",
                    (json.dumps({**params, "cur_step": None}, ensure_ascii=False), task_id))
            from routes.analysis_cache import invalidate
            invalidate("rules", "inventory")
            return {"ok": True, "task_id": task_id, "status": "done",
                    "rules_evaluated": _rri.get("rules_evaluated"), "inventory_adjusted": _rri.get("inventory_adjusted")}
        _total = _ev_total
        _seg_no = _offset // page_size + 1
        _seg_cnt = max((_total + page_size - 1) // page_size, 1)
        # 该片 SKU: 评估(order.created——库存上下文)+ 联动(销售扣减——deltas 只算该片)
        from db import query as _q5, execute as _e5, transaction as _txn5
        from core.rules import evaluate_many, load_rules_for
        from collections import defaultdict
        inv_map = {}
        for _i in range(0, len(_batch), 500):
            _ph = ",".join(["%s"] * len(_batch[_i:_i + 500]))
            # 2026-10-29 修正: MAX 取单仓最大值会低估超卖风险 → SUM 汇总该 SKU 全部仓
            for r in _q5("SELECT sku, SUM(available_qty) AS avail FROM inventory "
                         "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph),
                         [channel] + _batch[_i:_i + 500]):
                inv_map[r.get("sku")] = int(r.get("avail") or 0)
        # 从 stage 取该片 SKU 的行(2026-11-06 S5/P0-4: 不再"重读 BLOB+重解析+重清洗整份"
        #   —— 物化时清洗已做, 这里按 (task_id, sku) 索引直读本片)
        src_task = params.get("src_task") or ""
        _cleaned = []
        for _i4 in range(0, len(_batch), 500):
            _seg4 = _batch[_i4:_i4 + 500]
            _ph4 = ",".join(["%s"] * len(_seg4))
            # IN 列表必须插值: 只写 `IN (%s)` 而把 sku 平铺进参数位, 单 SKU 时恰好
            #   2 对 2 蒙对, 多 SKU 必 "not all arguments converted"(2026-11-06 实测)。
            for _sr4 in query("SELECT payload FROM import_stage WHERE task_id=%s AND sku IN (%s)"
                              % ("%s", _ph4), [src_task] + _seg4):
                try:
                    _cleaned.append(json.loads(_sr4.get("payload") or "{}"))
                except Exception:
                    continue
        # 该片 SKU 行级评估 ctx(与导入 done 步同口径——每行 ctx——order.created 规则行级判定)
        # 2026-10-29 超卖口径修复: 原 ctx 只给 order.quantity=本次导入量(单笔) →
        # 累计已超卖(如 1095 件 vs 库存 9)但单笔都小于库存时**永不报警**(系统性漏报)
        # → 补 order.open_qty = 该 SKU 未发货订单量(与每日评估/删单联动同一口径)
        _cum = {}
        # 2026-11-06 审计 P0-12: 原 `_batch[:500]` —— 本函数 page_size=1000, 每片后 500 个
        #   SKU 拿不到 open_qty, ctx 退化为"单行最大量", 未发货超卖(1095 vs 库存 9)判定失效。
        #   改为 500/批循环拼接(与上方 inv_map 同一写法), 覆盖率 100%。
        # 2026-11-06 口径收敛: 判定量 = **未发货订单量**(待确认/待发货 且 近 N 天),
        #   与每日全量评估/删单联动同一个取数口径(定义只在 core.metrics 一处)。
        from core import metrics as _M
        _ow_c = _M.oversell_sql_cond(*_M.oversell_window(_M.rule_params_of(channel, "oversell")))
        for _i2 in range(0, len(_batch), 500):
            _seg = _batch[_i2:_i2 + 500]
            _ph2 = ",".join(["%s"] * len(_seg))
            for _r in _q5("SELECT sku, SUM(CASE WHEN " + _ow_c + " THEN quantity ELSE 0 END) AS q "
                          "FROM orders WHERE channel=%s "
                          "AND (deleted_at IS NULL OR deleted_at='') AND sku IN (%s) "
                          "GROUP BY sku" % ("%s", _ph2), [channel] + _seg):
                _cum[_r.get("sku")] = int(_r.get("q") or 0)
        # 2026-10-29 SKU 级去重评估(数学等价): 原每行一 ctx, 同 SKU 多行重复评估
        #   等价性: "存在某行 quantity > X" ⟺ max_quantity > X; "< X" ⟺ min_quantity < X
        #   → 提供 max/min/open_qty/line_count 四个字段, 任何单调条件都可精确表达(语义不漂移)
        # 2026-10-29 顺序修正(实证漏报): 出入库必须**先重算再评估**
        #   原顺序: 取 inv_map → 评估 → 重算 → ctx.available_qty 是重算前旧值
        #   实证: 出库 800 后 own 仓 803→3, 但评估仍按 803 判定"库存充足" → 低库存不告警
        adjusted = 0
        # 2026-11-01 库存导入也纳入重算(用户业务定案: 自有仓库存导入只提供【期初 + 在途】,
        #   可用 = 期初 + Σ入库 − Σ出库 由系统算, **不采信文件里的可用值**)。
        #   原条件只有 inbound/outbound → 库存导入写完 beginning_stock 后 available 长期不动,
        #   要等下一次出入库导入才被修正; 期间库存页/看板/补货用的是旧可用值(静默错账)。
        #   平台仓/B仓不在白名单: 它们是快照制(导入即权威), 重算会覆盖平台数据。
        if str(params.get("target") or "") in ("inbound", "outbound", "inventory"):
            try:
                from routes.cleanse_write import (
                    _ensure_inventory_rows as _eir0,
                    _recalc_own_inventory as _rc0,
                    _rebuild_own_batches as _rb0,
                )
                if str(params.get("target") or "") in ("inbound", "outbound"):
                    _eir0(channel, _cleaned)      # 仓库以导入表格为准(自动建档)
                adjusted = _rc0(channel, list(_batch))
                _rb0(channel, list(_batch))
                inv_map = {}                       # 刷新: 评估 ctx 必须用重算后的库存
                for _i2 in range(0, len(_batch), 500):
                    _ph2b = ",".join(["%s"] * len(_batch[_i2:_i2 + 500]))
                    for _r2b in _q5("SELECT sku, SUM(available_qty) AS avail FROM inventory "
                                    "WHERE channel=%s AND sku IN (%s) GROUP BY sku" % ("%s", _ph2b),
                                    [channel] + _batch[_i2:_i2 + 500]):
                        inv_map[_r2b.get("sku")] = int(_r2b.get("avail") or 0)
            except Exception as _e5c:
                try_err('cleansing', '出入库前置重算降级', _e5c)
        _grp = {}
        for c in _cleaned:
            sku = str(c.get("sku") or "").strip()
            if sku not in set(_batch):
                continue
            _q = int(c.get("quantity") or 0)
            _g = _grp.setdefault(sku, {"max": _q, "min": _q, "n": 0, "amt": 0.0, "price": 0.0})
            _g["max"] = max(_g["max"], _q)
            _g["min"] = min(_g["min"], _q)
            _g["n"] += 1
            # 2026-10-29 规则页字段对齐: order.total_amount / order.unit_price 此前未注入(选了恒空)
            try:
                _g["amt"] += float(c.get("total_amount") or 0)
                if not _g["price"]:
                    _g["price"] = float(c.get("unit_price") or 0)
            except Exception:
                pass
        # 2026-10-29 入库/出库触发新事件(此前完全不评估 —— 到货/出库后库存骤降无法即时预警)
        # 约束(四维): 仅本次导入 SKU(不全量) + try/except 降级(不阻塞) + 异步任务内执行(不拖慢导入)
        _flow_evt = {"inbound": "inbound.created", "outbound": "outbound.created"}.get(
            str(params.get("target") or ""))
        flow_ctxs = []
        if _flow_evt:
            _fgrp = {}
            for _fc in _cleaned:
                _fsku = str(_fc.get("sku") or "").strip()
                if not _fsku or _fsku not in set(_batch):
                    continue
                _fq = int(_fc.get("quantity") or 0)
                _fg = _fgrp.setdefault(_fsku, {"qty": 0, "wh": "", "date": "", "exp": ""})
                _fg["qty"] += _fq
                _fg["wh"] = _fg["wh"] or str(_fc.get("warehouse") or "")
                _fg["date"] = _fg["date"] or str(
                    _fc.get("inbound_date") or _fc.get("outbound_date") or "")
                _fg["exp"] = _fg["exp"] or str(_fc.get("exp_date") or "")
            for _fs, _fg in _fgrp.items():
                flow_ctxs.append({"sku": _fs, "channel": channel,
                                  "flow": {"quantity": _fg["qty"], "warehouse": _fg["wh"],
                                           "date": _fg["date"]},
                                  "inbound": {"quantity": _fg["qty"], "warehouse": _fg["wh"],
                                              "date": _fg["date"]},
                                  "outbound": {"quantity": _fg["qty"], "warehouse": _fg["wh"],
                                               "date": _fg["date"]},
                                  "available_stock": inv_map.get(_fs, 0),
                                  "inv": {"available_qty": inv_map.get(_fs, 0),
                                          "safety_qty": 0, "in_transit_qty": 0}})
        o_ctxs = []
        for sku, _g in _grp.items():
            o_ctxs.append({"sku": sku, "channel": channel,
                           "order": {"quantity": _g["max"],
                                     "max_quantity": _g["max"],
                                     "min_quantity": _g["min"],
                                     "line_count": _g["n"],
                                     "open_qty": _cum.get(sku, _g["max"]),
                                     "total_amount": round(_g["amt"], 2),
                                     "unit_price": _g["price"]},
                           "order_qty": _cum.get(sku, _g["max"]),
                           "available_stock": inv_map.get(sku, 0),
                           "inv": {"available_qty": inv_map.get(sku, 0), "safety_qty": 0, "in_transit_qty": 0}})
        evaluated = 0
        if flow_ctxs and _flow_evt:
            try:
                _frules = load_rules_for(_flow_evt, channel)
                if _frules:
                    evaluate_many(_flow_evt, flow_ctxs, channel, _frules)
                    evaluated += len(flow_ctxs)
            except Exception as _fe:
                try_err('cleansing', '出入库评估降级', _fe)
        # 2026-10-29 联动补全: 出入库改变了自有仓库存 → 必须评估 inventory.changed
        #   (低库存规则绑定 inventory.changed; 原只触发 inbound/outbound.created
        #    → 出库后库存跌破安全线**不亮灯**, 实测 own 仓 803→3 无告警)
        # 复用 evaluate_stock_skus(skus=本批): 动态安全线/批次效期/供应商/加速全同源,
        #   且反向关闭收敛到本批 SKU(不会误关其他 SKU 的告警)
        if _flow_evt and _batch:
            try:
                from core.rule_eval import evaluate_stock_skus as _ess
                _ess(channel, skus=list(_batch))
                evaluated += len(_batch)
            except Exception as _ie:
                try_err('cleansing', '出入库→库存变更评估降级', _ie)
        if o_ctxs:
            evaluate_many("order.created", o_ctxs, channel, load_rules_for("order.created", channel))
            # 2026-11-06 审计 P1-34: 原 `=` 覆盖 —— 上面出入库两段已累加的 evaluated 被清零,
            #   进度条只显示订单规则数, "rules_evaluated" 统计口径偏小。改累加。
            evaluated += len(o_ctxs)
        # 2026-10-29: 重算已前移到评估之前(见上方 adjusted) —— 此处不再重复执行
        #   (原顺序导致 ctx.available_qty 用旧值, 且重算两次虽幂等但浪费)
        # order: 不做任何库存联动(平台库存导出已含销售影响, 再扣=重复计算)
        # 本片处理**成功后才置完成标记**(applied=1 + 时间戳)。带 claimed_token:
        #   只有认领者能置位, 防止并发下别人替我"宣布完成"。
        if _ids:
            execute(_eval_done_sql(len(_ids)), [task_id, _claim_tok] + _ids)
        # 推进 offset + 累积 result
        _new_off = _offset + len(_batch)
        _rj = json.loads(row.get("result") or "{}") or {}
        _rri = _rj.get("result") or {}
        _cum_rules = int(_rri.get("rules_evaluated") or 0) + evaluated
        _cum_inv = int(_rri.get("inventory_adjusted") or 0) + adjusted
        _steps = (params.get("steps") or []) + [{"name": "评估分片 %d/%d" % (_seg_no, _seg_cnt), "name_key": "task.eval_seg", "p": {"n": _seg_no, "m": _seg_cnt}, "elapsed": round(_t.time() - started, 1)}]
        _done = _new_off >= _total
        _status = "done" if _done else "running"
        execute("UPDATE sync_tasks SET status=%s, params=%s, result=%s, updated_at=NOW() WHERE task_id=%s",
                (_status,
                 json.dumps({**params, "offset": _new_off, "steps": _steps,
                             "cur_step": None if _done else {"name": "评估分片 %d/%d" % (_seg_no + 1, _seg_cnt), "name_key": "task.eval_seg", "p": {"n": _seg_no + 1, "m": _seg_cnt}, "started": int(_t.time())}}, ensure_ascii=False),
                 json.dumps({"result": {"target": target, "success": _total, "failed": 0,
                                        "rules_evaluated": _cum_rules, "inventory_adjusted": _cum_inv,
                                        "message": "评估 %d SKU, 联动 %d" % (_cum_rules, _cum_inv)}}, ensure_ascii=False), task_id))
        if _done:
            from routes.analysis_cache import invalidate
            invalidate("rules", "inventory")
        return {"ok": True, "task_id": task_id, "status": _status, "page": _seg_no,
                "rules_evaluated": _cum_rules, "inventory_adjusted": _cum_inv}
    except Exception as _e6:
        try_err('cleansing', '评估任务异常(可重试)', _e6)
        return {"ok": True, "task_id": task_id, "status": "running"}
    finally:
        # 2026-11-06 S5/P0-6: 所有退出路径停心跳+释放租约(僵尸心跳会替下一任续租)
        if _stop_beat:
            _stop_beat()
        if _lk:
            from core import task_lease as _lease_r
            _lease_r.release(task_id, _lk)


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
    # 2026-10-27 根治: 模板内嵌自定义字段定义(跨设备/清缓存可还原字段名)
    cf_meta = d.get("cf_meta") or []
    if isinstance(cf_meta, (list, dict)):
        cf_meta = json.dumps(cf_meta, ensure_ascii=False)
    # 同名模板更新覆盖(name + doc_type 同时存在 → UPDATE; 否则 INSERT——防同名重复)
    # 2026-10-27 隔离修复: 曾只按 name 查——不同导入类型同名模板互相覆盖(订单/库存同名=串类型)
    existing = one("SELECT id FROM cleansing_templates WHERE name=%s AND doc_type=%s",
                   [name, doc_type])
    if existing:
        execute("UPDATE cleansing_templates SET mapping=%s, doc_type=%s, cf_meta=%s, updated_at=NOW() "
                "WHERE id=%s",
                (mapping or "{}", doc_type, cf_meta or "[]", existing.get("id")))
    else:
        execute("INSERT INTO cleansing_templates(name, doc_type, mapping, cf_meta) "
                "VALUES(%s,%s,%s,%s)",
                (name, doc_type, mapping or "{}", cf_meta or "[]"))
    return ok({"message": "模板已保存"})


# ── 写入逻辑(按目标类型) ────────────────────────────────────────────────





