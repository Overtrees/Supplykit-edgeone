"""采购建议模块 — 从 insights.py 拆出"""
from fastapi import APIRouter
from app.core.database import get_db
from app.core.response import ok
from datetime import datetime, timezone
import json, os, logging

logger = logging.getLogger("purchase")

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get('/purchase')
def get_purchase_suggestions(days: int = 28, mode: str = 'bbcc', channel: str = 'jd', search: str = '', db = get_db()):
    """采购建议：系统总库存视角，含目标周转控制"""
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    # 尝试读取缓存（与补货建议共享 _replen_version 版本号）
    from app.core.replenishment_cache import get_cached, set_cache as _set_cache
    _pkey = 'purchase_' + (mode or 'bbcc')  # 与补货建议(纯mode)key隔离, 且教采购bbcc/traditional区分
    _cached, _hit = get_cached(_pkey, channel, days, db)
    if _hit:
        return ok(_cached)

    raw = {r['key']: r['value'] for r in db.table("replenishment_config").select("*").eq("channel", channel).execute().data}
    purchase_lead_time = int(raw.get('purchase_lead_days', '0'))
    moq_default = int(raw.get('moq', '0'))
    purchase_safety_days = float(raw.get('purchase_safety_days', '0'))

    # 活动系数
    season_key = f'season_config_{mode}'
    sv = db.table('replenishment_config').select('*').eq('key', season_key).execute().data
    season_config = json.loads(sv[0]['value']) if sv and sv[0].get('value') else []
    active_factor = 1.0
    for s in season_config:
        if isinstance(s, dict) and s.get('enabled') and float(s.get('factor', 1.0)) > active_factor:
            active_factor = float(s['factor'])

    # 获取 barcode 映射，用于日销复合 key
    products_map = {p["sku"]: p for p in db.table("products").select("*").eq("deleted_at", "").execute().data}
    sku_barcode_map = {sku: p.get('barcode', '') or '' for sku, p in products_map.items()}

    # 统一数据源：快照(历史) + 当天orders(实时)
    from app.core.sales_utils import load_daily_sales, calc_sales_from_daily
    today = now.strftime('%Y-%m-%d')
    today_orders = [o for o in (db.table("orders").select("*").eq("channel", channel).gte("ordered_at", today).execute().data or []) if not (o.get("deleted_at") or "")]

    # 14+28 双窗口：从快照一次加载，分别算两个窗口
    daily_28 = load_daily_sales(28, db, sku_barcode_map=sku_barcode_map, channel=channel)
    sales_14 = calc_sales_from_daily(daily_28, 14, orders=today_orders, sku_barcode_map=sku_barcode_map)
    sales_28 = calc_sales_from_daily(daily_28, 28, orders=today_orders, sku_barcode_map=sku_barcode_map)

    def get_purchase_sales(sales_dict, sku):
        """按 sku 查询采购日销，优先用复合 key，降级为 sku"""
        bc = sku_barcode_map.get(sku, '')
        if bc:
            val = sales_dict.get(f"{sku}|{bc}")
            if val is not None: return val
        return sales_dict.get(sku, 0) or 0
    fused_sales = {}
    for sku in set(sku_barcode_map.keys()):
        s14 = get_purchase_sales(sales_14, sku); s28 = get_purchase_sales(sales_28, sku)
        if s14 > s28 * 1.15: w14, w28 = 0.55, 0.45
        elif s14 < s28 * 0.85: w14, w28 = 0.35, 0.65
        else: w14, w28 = 0.20, 0.80
        fused_sales[sku] = round(s14 * w14 + s28 * w28, 1)

    # 系统总库存
    inv_data = db.table("inventory").select("*").eq("channel", channel).execute().data
    stock_by_sku = {}; b_avail = {}
    for i in inv_data:
        s = i['sku']
        if s not in stock_by_sku:
            stock_by_sku[s] = {'available':0,'transit':0,'safety':0,'safety_days':0,
                               'own_avail':0,'own_transit':0,'plat_avail':0,'plat_transit':0,'b_transit':0,'own_warehouse':''}
            b_avail[s] = 0
        qty = int(i.get('available_qty',0) or 0); tty = int(i.get('in_transit_qty',0) or 0)
        wt = i.get('warehouse_type','platform')
        # B 仓（platform_b）仅京东 BBCC 模式参与链路：传统模式(京东)与其他渠道全部跳过，
        # 不计入系统总库存/在途/安全库存，也不返回 b_available（补货模式二选一，口径对齐）
        if wt == 'platform_b' and (channel != 'jd' or mode != 'bbcc'):
            continue
        stock_by_sku[s]['available'] += qty; stock_by_sku[s]['transit'] += tty
        stock_by_sku[s]['safety'] += int(i.get('safety_qty',0) or 0)
        sd = float(i.get('safety_days',0) or 0)
        if sd > stock_by_sku[s]['safety_days']: stock_by_sku[s]['safety_days'] = sd
        if wt == 'platform_b':
            b_avail[s] += qty
            stock_by_sku[s]['b_transit'] += tty  # 供应商→B仓在途(bbcc口径, 前端在途列明细显示)
        elif wt == 'own':
            stock_by_sku[s]['own_avail'] += qty; stock_by_sku[s]['own_transit'] += tty
            if not stock_by_sku[s]['own_warehouse']: stock_by_sku[s]['own_warehouse'] = i.get('warehouse','')
        else: stock_by_sku[s]['plat_avail'] += qty; stock_by_sku[s]['plat_transit'] += tty

    products = {p["sku"]: p for p in db.table("products").select("*").eq("deleted_at", "").execute().data}

    # 供应商特定参数缓存（前置期/安全天数/MOQ 按供应商独立）
    _sup_params = {}
    def _get_sup_param(sup_code, key, fallback):
        if not sup_code:
            return fallback
        if sup_code not in _sup_params:
            _sup_params[sup_code] = {}
        if key not in _sup_params[sup_code]:
            _sup_params[sup_code][key] = int(float(raw.get(f'{key}_{sup_code}', str(fallback))))
        return _sup_params[sup_code][key]

    result = []
    for sku, st in stock_by_sku.items():
        ds = round(fused_sales.get(sku, 0) * active_factor, 1)
        sys_total = st['available'] + st['transit']
        prod = products.get(sku, {})
        _sup = prod.get('supplier_code', '')
        # 按供应商读取前置期和安全天数（无供应商则用全局）
        _lead = _get_sup_param(_sup, 'purchase_lead_days', purchase_lead_time)
        _safe_days = st['safety_days'] if st['safety_days'] > 0 else _get_sup_param(_sup, 'purchase_safety_days', purchase_safety_days)
        safety_days = _safe_days
        eff_safety = round(ds * safety_days) if ds > 0 else 0
        purchase_qty = max(round(ds * _lead) + eff_safety - sys_total, 0) if ds > 0 else 0
        # MOQ 在供应商维度统一处理，不在此处单个 SKU 触发
        box_qty = int(prod.get('box_qty', 1) or 1)
        actual_purchase = (purchase_qty + box_qty - 1) // box_qty * box_qty if purchase_qty > 0 else 0
        days_to_empty = round(st['available'] / ds, 1) if ds > 0 else 999
        after_stock = st['own_avail'] + st['own_transit'] + actual_purchase
        after_turnover = round(after_stock / ds, 1) if ds > 0 else 999
        target_turn = int(raw.get('max_turnover_days', '0'))
        c_consume = round(ds * _lead) if ds > 0 else 0
        note = ""
        if purchase_qty > 0:
            note = f"消耗{c_consume}+安全{eff_safety} -库存{int(sys_total)} ={int(purchase_qty)}"
            if box_qty > 1:
                note += f" · 箱规{box_qty}件, 实购{actual_purchase}件（{actual_purchase//box_qty}箱）"
            if target_turn > 0:
                note += f" · 补后周转{after_turnover}天" + (f" > 目标{target_turn}天" if after_turnover > target_turn else f" < 目标{target_turn}天")

        result.append({
            'sku': sku, 'barcode': sku_barcode_map.get(sku, ''), 'product_name': prod.get('product_name', ''), 'brand': prod.get('brand', ''),
            'store': prod.get('store', ''), 'warehouse': st['own_warehouse'], 'category': prod.get('category', ''),
            'sys_available': st['available'], 'sys_transit': st['transit'], 'sys_total': sys_total,
            'own_available': st['own_avail'], 'own_transit': st['own_transit'], 'b_transit': st['b_transit'],
            'plat_available': st['plat_avail'], 'plat_transit': st['plat_transit'],
            'b_available': b_avail.get(sku, 0),
            'safety_qty': st['safety'], 'daily_sales': ds,
            'daily_sales_14': get_purchase_sales(sales_14, sku), 'daily_sales_28': get_purchase_sales(sales_28, sku),
            'supplier_code': prod.get('supplier_code', ''),
            'purchase_qty': purchase_qty, 'box_qty': box_qty, 'actual_purchase': actual_purchase,
            'after_stock': st['own_avail'] + purchase_qty, 'after_turnover': after_turnover,
            'target_turnover': target_turn,
            'days_to_empty': days_to_empty, 'note': note,
        })

    # 按供应商汇总 MOQ：同一供应商所有 SKU 的采购量合计 < 该供应商 MOQ 时触发提升
    _sup_groups = {}
    for _r in result:
        _sup = _r.get('supplier_code', '')
        if not _sup: continue
        if _sup not in _sup_groups:
            _sup_moq = int(raw.get(f'moq_{_sup}', str(moq_default)))
            _sup_groups[_sup] = {'moq': _sup_moq, 'total_raw': 0, 'skus': []}
        _sup_groups[_sup]['total_raw'] += _r['purchase_qty']
        _sup_groups[_sup]['skus'].append(_r)
    for _sup, _sg in _sup_groups.items():
        if _sg['total_raw'] > 0 and _sg['total_raw'] < _sg['moq']:
            _ratio = _sg['moq'] / _sg['total_raw']
            for _r in _sg['skus']:
                _old = _r['purchase_qty']
                _r['purchase_qty'] = max(round(_r['purchase_qty'] * _ratio), 0)
                if _r['purchase_qty'] > 0:
                    _box = _r.get('box_qty', 1) or 1
                    _r['actual_purchase'] = (_r['purchase_qty'] + _box - 1) // _box * _box
                # 按优先级显示 MOQ 提示
                _parts = []
                _parts.append(f"供应商{_sup}起订{_sg['moq']}件")
                _parts.append(f"该供应商总计{_sg['total_raw']}件不足起订量")
                _parts.append(f"按占比{_old}/{_sg['total_raw']}提升至{_r['purchase_qty']}件")
                _r['note'] = _r.get('note', '') + ' · ' + '，'.join(_parts)
    if search:
        _sq = search.lower()
        result = [r for r in result if _sq in str(r.get('sku','')).lower() or _sq in str(r.get('product_name','')).lower() or _sq in str(r.get('barcode','')).lower()]
    result.sort(key=lambda x: x['days_to_empty'])
    # 批量处理告警（避免单 SKU 逐条查询，2000 SKU 时减少 4000 次 DB 查询）
    try:
        existing = {r['related_sku'] for r in db.table("alerts").select("*").eq("alert_type","purchase_need").eq("status","active").execute().data or []}
        from app.core.database import get_conn
        conn = get_conn()
        for r in result:
            should_insert = r['purchase_qty'] > 0 and r['days_to_empty'] < 14 and r['sku'] not in existing
            if should_insert:
                try:
                    conn.execute("INSERT INTO alerts(alert_type,title,description,severity,source,related_sku,status,channel) VALUES(?,?,?,?,?,?,?,?)",
                        ("purchase_need", f"需采购: {r['product_name']}",
                         f"可用{r['available_qty']}件, 建议采购{r['purchase_qty']}件, 可撑{r['days_to_empty']}天",
                         "warning", "purchase_engine", r['sku'], "active", channel))
                except Exception as e: logger.warning(f"[purchase] insert alert: {e}")
            elif r['purchase_qty'] == 0 and r['sku'] in existing:
                try:
                    conn.execute("UPDATE alerts SET status='closed' WHERE alert_type='purchase_need' AND related_sku=? AND status='active'", (r['sku'],))
                except Exception as e: logger.warning(f"[purchase] close alert: {e}")
        conn.commit()
    except Exception as e:
        logger.warning(f"[purchase] batch alerts: {e}")
    # 写入缓存
    try:
        _set_cache(_pkey, channel, days, result, db)
    except Exception:
        pass
    return ok(result)


@router.get('/export-purchase-suggestions')
def export_purchase_suggestions_excel(days: int = 28, mode: str = 'bbcc', channel: str = 'jd', db = get_db()):
    """导出采购建议为 Excel"""
    from openpyxl import Workbook
    from io import BytesIO
    from fastapi.responses import Response
    from urllib.parse import quote


    data = get_purchase_suggestions(days=days, mode=mode, channel=channel, db=db)
    suggestions = data.get("data") if isinstance(data, dict) and "data" in data else data

    wb = Workbook()
    ws = wb.active
    ws.title = "采购建议"
    headers = ["序号","SKU","69码","商品名称","仓库","系统总库存","系统可用","系统在途",
               "自有可用","自有在途","平台可用","平台在途","B仓可用",
               "日销(融合)","日销14","日销28","建议采购量","箱规","实购数量(含箱规取整)","补后周转","目标周转","可撑天数","采购时机","备注"]
    ws.append(headers)

    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    head_fill = PatternFill(start_color="1d4ed8", end_color="1d4ed8", fill_type="solid")
    head_font = Font(bold=True, color="ffffff", size=11)
    thin = Border(left=Side(style='thin',color='e2e8f0'), right=Side(style='thin',color='e2e8f0'),
                  top=Side(style='thin',color='e2e8f0'), bottom=Side(style='thin',color='e2e8f0'))
    for cell in ws[1]:
        cell.fill = head_fill; cell.font = head_font
        cell.alignment = Alignment(horizontal='center'); cell.border = thin

    for i, r in enumerate(suggestions, 1):
        timing = '建议' if r.get('purchase_qty',0) > 0 and (r.get('after_turnover',0) and (r.get('target_turnover',15) or 15) > 0 and r['after_turnover'] <= (r.get('target_turnover',15) or 15)) else '充足'
        if r.get('purchase_qty',0) <= 0: timing = '充足'
        ws.append([i, r["sku"], r.get("barcode","-"), r["product_name"], r["warehouse"], r["sys_total"], r["sys_available"], r["sys_transit"],
            r["own_available"], r["own_transit"], r["plat_available"], r["plat_transit"], r["b_available"],
            r["daily_sales"], r["daily_sales_14"], r["daily_sales_28"],
            r["purchase_qty"], r["box_qty"], r["actual_purchase"], r["after_turnover"], r["target_turnover"],
            r["days_to_empty"] if r["days_to_empty"] < 999 else "∞",
            timing, r["note"]])
        for cell in ws[ws.max_row]: cell.border = thin; cell.alignment = Alignment(horizontal='center')

    widths = [6,14,14,20,12,10,10,10,10,10,10,10,10,10,10,10,12,8,10,10,10,10,10,30]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1,i).column_letter].width = w

    ws2 = wb.create_sheet("汇总")
    ws2.append(["采购建议汇总"]); ws2.append(["生成时间", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
    ws2.append(["建议采购SKU数", len(suggestions)])
    ws2.append(["建议采购总量", sum(r["purchase_qty"] for r in suggestions)])
    ws2.merge_cells('A1:D1'); ws2['A1'].font = Font(bold=True, size=14)

    buf = BytesIO(); wb.save(buf); buf.seek(0)
    filename = f"采购建议_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})