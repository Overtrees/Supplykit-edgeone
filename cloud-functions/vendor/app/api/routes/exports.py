"""异步导出 — 后台生成 Excel，持久化导出记录到文件，支持下载"""
import os, json, uuid
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.core.database import get_db

router = APIRouter(prefix="/api/exports", tags=["exports"])

# 导出文件目录
EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'exports')
# 只读环境(Makers 函数沙箱)回退系统临时目录
try:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    _probe = os.path.join(EXPORT_DIR, '.w_probe')
    with open(_probe, 'w') as _pf:
        _pf.write('ok')
    os.remove(_probe)
except Exception:
    EXPORT_DIR = '/tmp'
    try:
        os.makedirs(EXPORT_DIR, exist_ok=True)
    except Exception:
        pass


@router.post("")
def create_export_task(type: str = 'purchase', mode: str = 'bbcc', days: int = 28,
                       channel: str = 'jd', limit: int = 5000, wh_type: str = '', db=get_db()):
    """提交导出任务，后台异步生成 Excel"""
    from app.core.database import submit_task
    task_id = f"export_{uuid.uuid4().hex[:8]}"
    params = {"type": type, "mode": mode, "days": days, "channel": channel, "limit": limit, "wh_type": wh_type}

    def _run():
        try:
            from openpyxl import Workbook
            from app.api.routes.purchase import get_purchase_suggestions
            from app.api.routes.replenishment import get_replenishment_suggestions
            from app.api.routes.insights import detect_slow_moving_products
            wb = Workbook(); ws = wb.active
            if type == 'purchase_suggestions':
                data = get_purchase_suggestions(days=days, channel=channel, db=get_db())
                items = data.get("data") if isinstance(data, dict) else data
                ws.append(["序号","品牌","SKU","69码","商品名称","仓库","系统可用","系统在途",
                           "自有可用","自有在途","平台可用","平台在途","B仓可用",
                           "日销(融合)","日销14","日销28","建议采购量","箱规","实购数量(含箱规取整)","补后周转","目标周转","可撑天数","采购时机","备注"])
                for i, r in enumerate((items or []), 1):
                    timing = '建议' if r.get('purchase_qty',0) > 0 else '充足'
                    ws.append([i, r.get('brand',''), r.get('sku',''), r.get('barcode','-'), r.get('product_name',''), r.get('warehouse',''),
                        r.get('sys_available',0), r.get('sys_transit',0),
                        r.get('own_available',0), r.get('own_transit',0), r.get('plat_available',0), r.get('plat_transit',0), r.get('b_available',0),
                        r.get('daily_sales',0), r.get('daily_sales_14',0), r.get('daily_sales_28',0),
                        r.get('purchase_qty',0), r.get('box_qty',1), r.get('actual_purchase',0), r.get('after_turnover',0), r.get('target_turnover',15),
                        r.get('days_to_empty',0) if r.get('days_to_empty',999) < 999 else '∞', timing, r.get('note','')])
            elif type == 'purchase':
                data = get_purchase_suggestions(days=days, mode=mode, channel=channel, db=get_db())
                items = data.get("data") if isinstance(data, dict) else data
                ws.append(["序号","品牌","SKU","69码","商品名称","仓库","系统可用","系统在途",
                           "自有可用","自有在途","平台可用","平台在途","B仓可用",
                           "日销(融合)","日销14","日销28","建议采购量","箱规","实购数量(含箱规取整)","补后周转","目标周转","可撑天数","采购时机","备注"])
                for i, r in enumerate((items or []), 1):
                    timing = '建议' if r.get('suggested_qty',0) > 0 else '充足'
                    ws.append([i, r.get('brand',''), r.get('sku',''), r.get('barcode','-'), r.get('product_name',''), r.get('warehouse',''),
                        r.get('sys_available',0), r.get('sys_transit',0),
                        r.get('own_available',0), r.get('own_transit',0), r.get('plat_available',0), r.get('plat_transit',0), r.get('b_available',0),
                        r.get('daily_sales',0), r.get('daily_sales_14',0), r.get('daily_sales_28',0),
                        r.get('suggested_qty',0), r.get('box_qty',1), r.get('actual_purchase',0), r.get('after_turnover',0), r.get('target_turnover',15),
                        r.get('days_to_empty',0) if r.get('days_to_empty',999) < 999 else '∞', timing, r.get('note','')])
            elif type == 'slow':
                data = detect_slow_moving_products(db=get_db(), create_alerts=False)
                items = data.get("data") if isinstance(data, dict) else data
                ws.append(["SKU","69码","商品名称","最近下单","无销售天数","库存","级别","渠道"])
                for r in (items or []):
                    # 按渠道隔离
                    if r.get('channel','jd') != channel: continue
                    ws.append([r.get('sku',''), r.get('barcode',''), r.get('product_name',''), r.get('last_order_date',''), r.get('days_since_last',0), r.get('stock',0), r.get('level',''), r.get('channel','jd')])
            elif type == 'replen':
                # 补货建议导出(曾缺失该类型——补货页导出被错误指向采购建议)
                data = get_replenishment_suggestions(days=days, mode=mode, channel=channel, db=get_db())
                items = data.get("data") if isinstance(data, dict) else data
                if mode == 'bbcc':
                    ws.append(["序号","品牌","SKU","69码","商品","仓库","供应商-B仓在途","B仓可用库存","B仓周转","C仓总和可用","B-C调拨在途",
                               "C仓日销","近7日销","近14日销","近28日销","C缺口","C建议补","B缺口","B建议补","箱规",
                               "当前综转","补后综转","C仓周转","可撑天数","备注"])
                    for i, r in enumerate((items or []), 1):
                        ws.append([i, r.get('brand',''), r.get('sku',''), r.get('barcode','-'), r.get('product_name',''), r.get('warehouse','-'),
                            r.get('b_transit',0), r.get('b_stock',0), (round((r.get('b_stock',0) or 0)/(r.get('daily_sales',0) or 1),1) if r.get('daily_sales',0) and r.get('b_stock',0) else '-'),
                            r.get('c_stock',0), r.get('c_transit',0),
                            r.get('daily_sales',0), r.get('daily_sales_7',0), r.get('daily_sales_14',0), r.get('daily_sales_28',0),
                            r.get('raw_suggested',0), r.get('suggested_qty',0), r.get('b_gap',0), r.get('b_suggested',0), -1,
                            r.get('combined_turnover_current',0), r.get('combined_turnover',0), r.get('c_turnover',0) if r.get('c_turnover') is not None else '∞',
                            r.get('days_to_empty',0) if r.get('days_to_empty',999)<999 else '∞', r.get('note','')])
                else:
                    ws.append(["序号","品牌","SKU","69码","商品","仓库","现有","在途","日销(融合)","近7日销","近14日销","近28日销",
                               "安全库存","建议补","箱规","补后周转","可撑天数","备注"])
                    for i, r in enumerate((items or []), 1):
                        ws.append([i, r.get('brand',''), r.get('sku',''), r.get('barcode','-'), r.get('product_name',''), r.get('warehouse','-'),
                            r.get('available_qty',0), r.get('in_transit_qty',0), r.get('daily_sales',0), r.get('daily_sales_7',0), r.get('daily_sales_14',0), r.get('daily_sales_28',0),
                            r.get('safety_qty',0), r.get('suggested_qty',0), -1, r.get('after_turnover',0) if r.get('after_turnover') is not None else '∞',
                            r.get('days_to_empty',0) if r.get('days_to_empty',999)<999 else '∞', r.get('note','')])
            elif type == 'orders':
                from app.core.database import get_conn
                _conn = get_conn()
                _barcodes = {}
                try:
                    for _r in _conn.execute("SELECT sku, channel, barcode FROM products WHERE barcode!=''").fetchall():
                        _barcodes[(_r[0], _r[1])] = _r[2] or ''
                except Exception: pass
                _rows = _conn.execute("SELECT ordered_at,order_no,store,warehouse,product_name,sku,quantity,unit_price,total_amount,order_status,supplier,data_source,channel,paid_at FROM orders WHERE channel=? AND (deleted_at='') ORDER BY id DESC", (channel,)).fetchall()
                ws.append(["下单日期","订单号","店铺","仓库","商品","SKU","数量","单价","金额","状态","69码","入库日期","供应商","来源"])
                for r in _rows:
                    _bc = _barcodes.get((r[5], r[12]), '')
                    _paid = str(r[13] or '')[:10] if len(r) > 13 and r[13] else str(r[0] or '')[:10]
                    ws.append([str(r[0] or '')[:10], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], _bc, _paid, r[10], r[11]])
            elif type == 'inventory':
                from app.core.database import get_conn
                _conn = get_conn()
                _barcodes = {}
                try:
                    for _r in _conn.execute("SELECT sku, channel, barcode FROM products WHERE barcode!=''").fetchall():
                        _barcodes[(_r[0], _r[1])] = _r[2] or ''
                except Exception: pass
                _where = "channel=?"
                _params = [channel]
                if wh_type:
                    _where += " AND warehouse_type=?"
                    _params.append(wh_type)
                _sql = "SELECT sku,available_qty,in_transit_qty,c_transit,safety_qty,warehouse,warehouse_type,channel,product_name,beginning_stock,month_inbound,month_outbound,turnover_days FROM inventory WHERE " + _where
                _rows = _conn.execute(_sql, _params).fetchall()
                # 批次明细（按 sku+warehouse+channel 分组，供展开每批次一行）
                _bm = {}
                try:
                    for _br in _conn.execute("SELECT sku, warehouse, channel, prod_date, exp_date, qty FROM batches WHERE channel=?", (channel,)).fetchall():
                        _bm.setdefault((str(_br[0]), str(_br[1]), str(_br[2] or 'jd')), []).append((str(_br[3] or '')[:10], str(_br[4] or '')[:10], int(_br[5] or 0)))
                except Exception: pass
                from datetime import datetime as _dt, timedelta as _tz
                def _utcnow():
                    import datetime as _m
                    return _m.datetime.utcnow()
                _today = _utcnow()
                def _eff_status(prod, exp):
                    try:
                        p = _dt.strptime(prod, '%Y-%m-%d'); e = _dt.strptime(exp, '%Y-%m-%d')
                        total = (e - p).days
                        if total <= 0: return '无效'
                        consumed = (_today - p).days
                        if consumed >= total: return '已过期'
                        third = max(total // 3, 1)
                        if consumed >= third: return '✗否'
                        if consumed + 3 > third: return '⚠️临近'
                        return '✓正常'
                    except Exception: return ''
                ws.append(["SKU","69码","商品名称","仓库","类型","渠道","可用","在途","B-C调拨在途","安全线","期初库存","当月入库","当月出库","周转天数","生产日期","截止日期","批次数量","效期状态"])
                for r in _rows:
                    _bc = _barcodes.get((r[0], r[6]), '')
                    _td = round(r[11] or 0, 1) if (r[11] or 0) > 0 else None
                    _bk = (r[0], r[4], r[6])
                    _batches = _bm.get(_bk, [])
                    if not _batches:
                        ws.append([r[0], _bc, r[8], r[5], r[6], r[7], r[1], r[2], r[3], r[4], r[9], r[10], r[11], _td, '', '', '', ''])
                    else:
                        for _pb in _batches:
                            ws.append([r[0], _bc, r[8], r[5], r[6], r[7], r[1], r[2], r[3], r[4], r[9], r[10], r[11], _td, _pb[0], _pb[1], _pb[2], _eff_status(_pb[0], _pb[1])])
            filename = f"{type}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.xlsx"
            filepath = os.path.join(EXPORT_DIR, filename)
            wb.save(filepath)
            return {"filepath": filepath, "filename": filename, "size": os.path.getsize(filepath), "type": type}
        except Exception as e:
            import logging; logging.warning(f"[export] {type}: {e}")
            raise

    submit_task(task_id, _run, channel=channel, task_type='export')
    return {"ok": True, "task_id": task_id, "data": {"type": type, "channel": channel}}


@router.get("/download/{path:path}")
def download_export(path: str):
    """下载导出文件"""
    filepath = os.path.join(EXPORT_DIR, os.path.basename(path))
    if not os.path.exists(filepath):
        raise HTTPException(404, "导出文件不存在或已过期")
    return FileResponse(filepath, filename=os.path.basename(filepath),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")