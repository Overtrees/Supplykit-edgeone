from fastapi import APIRouter, Depends
from app.core.database import get_db
from app.core.response import ok, fail
from app.core.sales_utils import calc_sales, rolling_predict
from app.api.routes.replenishment import get_replenishment_suggestions
from datetime import datetime, timezone
import json, os, time

router = APIRouter(prefix="/api/insights", tags=["insights"])

# with-sales 结果缓存（30s TTL + 版本号）
_with_sales_cache = {}

# 滞销识别内存缓存（10s TTL，API 调用走缓存，scheduler 走实时）
_slow_cache = {}
_SLOW_CACHE_TTL = 10


@router.get('/ping')
def ping():
    return ok({"time": datetime.now(timezone.utc).isoformat()})

def detect_slow_moving_products(db=None, create_alerts=False):
    # API 调用（create_alerts=False）走 10s 内存缓存；scheduler 调用（create_alerts=True）走实时
    if not create_alerts:
        _now = time.time()
        _cached = _slow_cache.get('data')
        if _cached and _now - _cached['ts'] < _SLOW_CACHE_TTL:
            return _cached['data']
    from datetime import datetime, timedelta, timezone
    if db is None:
        from app.core.database import get_db
        db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    # 快照聚合：按 SKU 取最大日期，替代 orders 全表 GROUP BY
    from app.core.database import get_conn
    last_order = {}
    try:
        rows = get_conn().execute("SELECT sku, MAX(date) FROM daily_sales_snapshot WHERE date >= ? GROUP BY sku", (cutoff,)).fetchall()
        last_order = {str(r[0]): str(r[1] or '')[:10] for r in rows}
    except Exception as e:
        import logging; logging.warning(f"[slow-moving] snapshot agg: {e}")
    # 当天 orders 补充（快照不含今天）
    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        rows = get_conn().execute("SELECT sku, MAX(ordered_at) FROM orders WHERE ordered_at >= ? AND (deleted_at='') GROUP BY sku", (today,)).fetchall()
        for r in rows:
            if r[0] and str(r[1] or '')[:10] > last_order.get(str(r[0]), ''):
                last_order[str(r[0])] = str(r[1] or '')[:10]
    except Exception as e:
        import logging; logging.warning(f"[slow-moving] today orders: {e}")
    # 只加载需要的字段，避免全量 select("*") 导致 10 万 SKU 时 OOM
    products_map = {}
    try:
        from app.core.database import get_conn
        _conn = get_conn()
        for r in _conn.execute("SELECT sku, product_name, barcode, channel FROM products WHERE deleted_at=''").fetchall():
            products_map[str(r[0])] = {"sku": str(r[0]), "product_name": str(r[1] or ''), "barcode": str(r[2] or ''), "channel": str(r[3] or 'jd')}
    except Exception as e:
        import logging; logging.warning(f"[slow-moving] products: {e}")
    sku_barcode_map = {s: (p.get('barcode', '') or '') for s, p in products_map.items()}
    inventory_map = {}
    sku_wh = {}   # SKU → 库存最多仓的主体(滞销积压在哪类仓: 自有/B/C)
    try:
        for r in _conn.execute("SELECT sku, available_qty, product_name, channel, warehouse_type, warehouse FROM inventory").fetchall():
            _s = str(r[0])
            inventory_map[_s] = {"sku": _s, "available_qty": r[1], "product_name": str(r[2] or '') or _s, "channel": str(r[3] or 'jd')}
            _aq = r[1] or 0
            if _aq > 0 and (_s not in sku_wh or _aq > sku_wh[_s][1]):
                sku_wh[_s] = (str(r[4] or ''), _aq)   # 取有货、库存最多仓的主体(积压研判更有意义)
    except Exception as e:
        import logging; logging.warning(f"[slow-moving] inventory: {e}")
    # SKU → channel（优先 products 主表，回退 inventory）
    from app.core.sales_utils import sku_to_channel
    sku_channel_map = {s: (p.get('channel') or sku_to_channel(s, db) or 'jd') for s, p in products_map.items()}
    for s, i in inventory_map.items():
        if s not in sku_channel_map or not sku_channel_map[s]:
            sku_channel_map[s] = i.get('channel') or 'jd'
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive, 与strptime一致(修复slow_moving days恒999)
    result = []
    # 只遍历有库存的 SKU（无库存不需要滞销检测），避免 10 万+ SKU 全量遍历
    all_skus = set(inventory_map.keys())
    for sku in all_skus:
        p = products_map.get(sku)
        inv = inventory_map.get(sku)
        bc = sku_barcode_map.get(sku, '')
        key = f"{sku}|{bc}" if bc else sku
        last_date = last_order.get(key, "") or last_order.get(sku, "")
        days = 999
        if last_date:
            try: days = (now - datetime.strptime(last_date[:10], "%Y-%m-%d")).days
            except Exception as e: import logging; logging.warning(f"[slow-moving] parse date {last_date}: {e}")
        stock = int(inv.get("available_qty") or 0) if inv else 0
        if days > 30 and stock > 0:
            level = "滞销" if days > 60 else ("冷淡" if days > 30 else "正常")
            result.append({"sku": sku, "barcode": bc, "product_name": p["product_name"] if p else inv.get("product_name",sku) if inv else sku, "last_order_date": last_date[:10], "days_since_last": days, "stock": stock, "level": level, "channel": sku_channel_map.get(sku, 'jd'), "warehouse_type": (sku_wh.get(sku) or ('', 0))[0]})
            if create_alerts:
                ex = db.table("alerts").select("id").eq("alert_type","slow_moving").eq("related_sku",sku).eq("status","active").execute().data
                if not ex:
                    db.table("alerts").insert({"alert_type":"slow_moving", "title":f"滞销: {result[-1]['product_name']}", "description":f"{days} 天无销售，库存 {stock} 件", "severity":"warning", "source":"event_bus", "related_sku":sku, "status":"active", "channel": sku_channel_map.get(sku, 'jd'), "warehouse_type": (sku_wh.get(sku) or ('', 0))[0]}).execute()
    result.sort(key=lambda x: -x["days_since_last"])
    # API 调用写入缓存（scheduler 不写，避免覆盖实时数据）
    if not create_alerts:
        _slow_cache['data'] = {'data': ok(result), 'ts': time.time()}
    return ok(result)

@router.get('/slow-moving')
def get_slow_moving_products(db = get_db()):
    return detect_slow_moving_products(db, create_alerts=False)


_disposal_cache = {}
_DISPOSAL_CACHE_TTL = 300


@router.get('/disposal-suggestions')
def get_disposal_suggestions(channel: str = 'jd', page: int = 0, page_size: int = 0, search: str = '', db = get_db()):
    """滞销处置建议（300s TTL 缓存全量 + 分页返回）

    缓存存全量 suggestions(低频计算35s, 二次命中快), 分页在缓存后切片。
    page/page_size: 传0返回全部。
    """
    import time as _t
    # 版本号校验：数据变更(_replen_version递增)即时失效 → 导入订单后滞销SKU
    # 立即重算降级/移出(闭环实时性)。无变更时缓存命中(快)。
    _key = f"disposal_{channel}"
    _ver = 0
    try:
        _v = db.table("replenishment_config").select("*").eq("key", "_replen_version").execute().data
        _ver = int(_v[0]["value"]) if _v and _v[0].get("value") else 0
    except Exception:
        pass
    _cached = _disposal_cache.get(_key)
    if _cached and _cached.get('ver') == _ver and _t.time() - _cached.get('ts', 0) < _DISPOSAL_CACHE_TTL:
        suggestions = _cached['data']
    else:
        _res = _get_disposal_suggestions_impl(channel, db)
        suggestions = _res.get('data') if isinstance(_res, dict) else (_res if isinstance(_res, list) else [])
        try:
            _disposal_cache[_key] = {'data': suggestions, 'ts': _t.time(), 'ver': _ver}
        except Exception:
            pass
    if search:
        _sq = search.lower()
        suggestions = [x for x in suggestions if _sq in str(x.get('sku','')).lower() or _sq in str(x.get('product_name','')).lower() or _sq in str(x.get('barcode','')).lower()]
    total = len(suggestions)
    if page > 0 and page_size > 0:
        items = suggestions[(page - 1) * page_size: page * page_size]
        return ok({"items": items, "total": total, "page": page, "page_size": page_size})
    return ok(suggestions)


def _get_disposal_suggestions_impl(channel: str = 'jd', db = get_db()):

    """滞销品自动处置建议（精简版）— SKU×仓库粒度

    核心: 卖不动(零销售超品类滞销线) + 成本压力(临期/B仓超免费期)
    品类配置: 规则页「滞销参数」自定义条目(名称/滞销线/临期线/品类名单开关), 仿活动系数
    等级: black紧急(临期) > red处置(滞销+B仓超期或高占用) > yellow滞销(超过滞销线)
    B仓超期按整月计费口径展示(超期天数+预估计费月数, 费率待定)
    """
    from datetime import datetime, timedelta, timezone
    from app.core.database import get_conn
    from app.core.sales_utils import load_daily_sales, calc_sales_from_daily
    import json
    conn = get_conn()
    today = datetime.now(timezone.utc).replace(tzinfo=None)  # naive, 与strptime一致(修复days_zero恒999+black不触发)

    # ── 品类配置（自定义条目列表）──
    def load_json(key, default):
        try:
            r = conn.execute("SELECT value FROM replenishment_config WHERE key=? AND channel=?", (key, channel)).fetchone()
            if r and r[0]:
                d = json.loads(r[0])
                if isinstance(d, list): return d
        except Exception:
            pass
        return default
    cat_cfg = load_json('slow_cats_config', [])
    if not cat_cfg:
        cat_cfg = [{'name':'食品','slow_days':30,'shelf_months':3,'cats':'酱油,酱料,调味汁,食用油,醋,料酒,蚝油,芝麻油,辣椒酱,拌面酱,老抽,生抽,陈醋,香醋,白醋,米醋,花椒油,藤椒油,辣椒油,芥末油,番茄酱,甜辣酱,沙拉酱,芝麻酱,花生酱,豆瓣酱,豆豉,腐乳,糟卤,鱼露,咖喱块,咖喱粉,五香粉,孜然粉,花椒粉,辣椒粉,胡椒粉,十三香,卤料包,炖肉料,鸡精,味精,白糖,冰糖,红糖,麦芽糖,蜂蜜,黄酒,米酒,薯片,虾条,爆米花,坚果,瓜子,花生,饼干,威化,巧克力,糖果','enabled':True},
                   {'name':'个护家清','slow_days':60,'shelf_months':6,'cats':'洗衣液,洗洁精,洗手液,消毒液,纸巾,湿巾,垃圾袋,保鲜膜,保鲜袋,收纳盒','enabled':True}]
    b_free = 15
    try:
        r = conn.execute("SELECT value FROM replenishment_config WHERE key='b_free_days' AND channel=?", (channel,)).fetchone()
        if r and r[0]: b_free = int(r[0])
    except Exception:
        pass
    # 资金占用阈值(配置化, 默认1万——yellow升级red的成本压力线)
    fund_threshold = 10000
    try:
        r = conn.execute("SELECT value FROM replenishment_config WHERE key='slow_fund_threshold' AND channel=?", (channel,)).fetchone()
        if r and r[0]: fund_threshold = int(r[0])
    except Exception:
        pass

    # 商品信息
    products = {}
    try:
        for r in conn.execute("SELECT sku, product_name, price, volume, category, best_before, channel, brand FROM products WHERE (deleted_at='') AND channel=?", (channel,)).fetchall():
            products[str(r[0])] = {"name": str(r[1] or ''), "price": float(r[2] or 0), "volume": float(r[3] or 0),
                                   "category": str(r[4] or ''), "best_before": str(r[5] or '')[:10], "brand": str(r[6] or '')}
    except Exception as e:
        import logging; logging.warning(f"[disposal] products: {e}")
    # 日销（28天）+ 近90天销售（最后销售日/动销参考）
    try:
        sales_28 = calc_sales_from_daily(load_daily_sales(28, db, channel=channel), 28)
    except Exception as e:
        import logging; logging.warning(f"[disposal] sales: {e}")
        sales_28 = {}
    # SQL 聚合取每SKU最后销售日(替代全量12万行Python遍历, 套用detect_slow_moving已有聚合方案)
    sale_90 = {}
    try:
        cutoff90 = (today - timedelta(days=90)).strftime('%Y-%m-%d')
        for r in conn.execute("SELECT sku, MAX(ordered_at) FROM orders WHERE channel=? AND ordered_at>=? AND (deleted_at='') GROUP BY sku", (channel, cutoff90)).fetchall():
            if r[0]: sale_90[str(r[0])] = str(r[1] or '')[:10]
    except Exception as e:
        import logging; logging.warning(f"[disposal] sale90: {e}")
    # 最后销售日
    last_order = {}
    try:
        for r in conn.execute("SELECT sku, MAX(date) FROM daily_sales_snapshot WHERE channel=? GROUP BY sku", (channel,)).fetchall():
            last_order[str(r[0])] = str(r[1] or '')[:10]
    except Exception as e:
        import logging; logging.warning(f"[disposal] snap date: {e}")
    for sk, mx in sale_90.items():
        if mx and mx > last_order.get(sk, ''):
            last_order[sk] = mx
    # B 仓入库批次
    b_arrival = {}
    try:
        for r in conn.execute("SELECT sku, arrival_date FROM purchase_orders WHERE channel=? AND arrival_date != ''", (channel,)).fetchall():
            if r[1]:
                try:
                    b_arrival.setdefault(str(r[0]), datetime.strptime(str(r[1])[:10], "%Y-%m-%d"))
                except Exception: pass
    except Exception as e:
        import logging; logging.warning(f"[disposal] po: {e}")
    # 已处置（30天去重）
    disposed = {}
    try:
        cutoff30 = (today - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        for r in conn.execute("SELECT sku, warehouse, action FROM disposal_records WHERE channel=? AND created_at >= ?", (channel, cutoff30)).fetchall():
            disposed[(str(r[0]), str(r[1]))] = str(r[2] or '')
    except Exception as e:
        import logging; logging.warning(f"[disposal] disposed: {e}")
    # 伪滞销线索（近14天有补货建议）
    pseudo = set()
    try:
        c14 = (today - timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')
        for r in conn.execute("SELECT DISTINCT related_sku FROM alerts WHERE channel=? AND alert_type IN ('replenish','rewrite') AND created_at>=?", (channel, c14)).fetchall():
            if r[0]: pseudo.add(str(r[0]))
    except Exception as e:
        import logging; logging.warning(f"[disposal] pseudo: {e}")

    suggestions = []
    LEVEL = {'black': 0, 'red': 1, 'yellow': 2, 'observe': 3}
    SUG = {'black': '临期紧急处理(促销/退供)', 'red': '退货供应商/清仓甩卖', 'yellow': '补货降量/持续跟踪', 'observe': '继续观察/补货降量'}
    for r in conn.execute("SELECT sku, warehouse, warehouse_type, available_qty FROM inventory WHERE channel=?", (channel,)).fetchall():
        sku, wh, wht = str(r[0]), str(r[1] or ''), str(r[2] or '')
        avail = int(r[3] or 0)
        if avail <= 0:
            continue
        p = products.get(sku)
        if not p:
            continue
        # 品类归类：匹配启用的配置条目（cats 包含 category）
        match = None
        cat = p['category']
        for c in cat_cfg:
            if c.get('enabled') is False: continue
            ck = str(c.get('cats') or '')
            if any(word.strip() and word.strip() in cat for word in ck.split(',')):
                match = c
                break
        slow_days = int(match.get('slow_days', 30)) if match else 30
        shelf_m = int(match.get('shelf_months', 3)) if match else 3
        cat_name = match.get('name', '未归类') if match else '未归类(默认食品线)'
        # 滞销天数
        days_zero = 999
        ld = last_order.get(sku, '')
        if ld:
            try:
                days_zero = (today - datetime.strptime(ld[:10], "%Y-%m-%d")).days
                days_zero = max(days_zero, 0)
            except Exception: pass
        daily = 0.0
        try: daily = float(sales_28.get(sku, 0) or 0)
        except Exception: pass
        turnover = round(avail / daily, 1) if daily > 0 else 999.0
        fund = round(avail * p['price'], 0)
        reason = []
        level = None
        b_storage = None
        # ① 临期风险（black 紧急）
        bb = p['best_before']
        shelf_days = shelf_m * 30
        if bb:
            try:
                dd = (datetime.strptime(bb[:10], "%Y-%m-%d") - today).days
                if dd <= shelf_days:
                    level = 'black'
                    if dd >= 0:
                        reason.append(f"距保质期{dd}天(<{shelf_m}月临期线)")
                    else:
                        reason.append(f"已过期{-dd}天")
            except Exception: pass
        # ② B 仓超免费期（仓储费成本, 按整月计费口径）
        if wht == 'platform_b' and channel == 'jd':
            days_stored = 0
            if sku in b_arrival:
                days_stored = max((today - b_arrival[sku]).days, 0)
            if days_stored > b_free:
                over = days_stored - b_free
                months = max((over + 29) // 30, 1)
                vol_m3 = round(avail * p['volume'], 3)
                b_storage = {"days_stored": days_stored, "free_days": b_free, "volume_m3": vol_m3, "over_days": over, "billed_months": months}
                reason.append(f"B仓在库{days_stored}天超免费期{over}天(约{months}计费月, 费率待定)")
        # ③ 滞销主判据（观察线 = 滞销线一半，对齐旧"冷淡"30天观察阶段）
        # 观察线: 条目可配 observe_days, 留空自动 = 滞销线一半(下限15)
        try:
            observe_days = int(str(match.get('observe_days') or '').strip()) if match and match.get('observe_days') not in (None, '', 0) else max(slow_days // 2, 15)
            observe_days = max(observe_days, 1)
        except Exception:
            observe_days = max(slow_days // 2, 15)
        if days_zero >= slow_days:
            if level is None:
                level = 'yellow'
            reason.append(f"{cat_name}: {days_zero}天未销售(超{slow_days}天线)")
        elif days_zero >= observe_days:
            if level is None:
                level = 'observe'
            reason.append(f"{cat_name}: {days_zero}天未销售(接近{slow_days}天线, 建议观察)")
        else:
            if level is None and b_storage:
                level = 'yellow'
            if level is None:
                continue  # 没过线且无临期/B仓 → 正常
        # ④ 升级: 滞销 + B仓超期 或 高资金占用 → red 处置
        if level == 'yellow' and (b_storage or fund >= fund_threshold):
            level = 'red'
            reason.append('有成本压力(仓储费或占用¥' + str(fund) + '), 建议尽快处置')
        # 伪滞销提示（不降级, 仅提示核实）
        if sku in pseudo and level in ('red',):
            reason.append('近14天有补货建议, 疑似缺货伪滞销, 建议先核实库存')
        suggestions.append({
            "sku": sku, "product_name": p['name'], "channel": channel,
            "warehouse": wh, "warehouse_type": wht, "category": cat,
            "stock": avail, "turnover_days": turnover, "fund_occupied": fund,
            "daily_sales": round(daily, 1), "days_zero": days_zero,
            "cat_line": cat_name, "slow_days": slow_days,
            "level": level, "reason": reason, "suggestion": SUG.get(level, ''),
            "b_storage": b_storage, "best_before": bb, "brand": p.get('brand', ''),
            "disposed": (sku, wh) in disposed, "disposed_action": disposed.get((sku, wh), ''),
        })
    suggestions.sort(key=lambda x: (LEVEL.get(x['level'], 9), -x['days_zero']))
    return ok(suggestions)


@router.get('/export-slow-moving')
def export_slow_moving_excel(channel: str = 'jd', db = get_db()):
    """导出滞销预警为 Excel"""
    from openpyxl import Workbook
    from io import BytesIO
    from fastapi.responses import Response
    from urllib.parse import quote

    result = get_slow_moving_products(db)
    # 按渠道过滤
    import json
    data = result.get("data") if isinstance(result, dict) and "data" in result else (result if isinstance(result, list) else [])
    if channel != 'all':
        products = set()
        try:
            from app.core.database import get_conn as _gconn
            for _r in _gconn().execute("SELECT sku FROM products WHERE channel=? AND (deleted_at IS NULL OR deleted_at='')", (channel,)).fetchall():
                products.add(_r[0])
        except Exception:
            products = set()
        data = [x for x in data if x['sku'] in products]
    slow = [x for x in data if x.get('level') != '正常']

    wb = Workbook()
    ws = wb.active
    ws.title = "滞销预警"
    headers = ["SKU","商品","最近下单","天数","库存","级别"]
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    hf = PatternFill(start_color="1d4ed8", end_color="1d4ed8", fill_type="solid")
    hfn = Font(bold=True,color="ffffff",size=11)
    thin = Border(left=Side(style='thin',color='e2e8f0'),right=Side(style='thin',color='e2e8f0'),top=Side(style='thin',color='e2e8f0'),bottom=Side(style='thin',color='e2e8f0'))
    ws.append(headers)
    for c in ws[1]: c.fill=hf; c.font=hfn; c.alignment=Alignment(horizontal='center'); c.border=thin
    for r in slow:
        ws.append([r.get('sku',''),r.get('product_name',''),r.get('last_order_date',''),r.get('days_since_last',0),r.get('stock',0),r.get('level','')])
        for c in ws[ws.max_row]: c.border=thin; c.alignment=Alignment(horizontal='center')
    buf = BytesIO(); wb.save(buf); buf.seek(0)
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":f"attachment; filename*=UTF-8''slow_moving_{datetime.now(timezone.utc).strftime('%Y%m%d')}.xlsx"})


@router.get('/summary')
def get_insight_summary(db = get_db()):
    inv = []
    try:
        from app.core.database import get_conn as _gconn
        inv = [{"sku": r[0], "available_qty": r[1], "safety_qty": r[2] or 0}
               for r in _gconn().execute("SELECT sku, available_qty, safety_qty FROM inventory").fetchall()]
    except Exception:
        inv = []
    total = len(inv)
    low_stock = len([x for x in inv if int(x.get("available_qty") or 0) < int(x.get("safety_qty") or 0)])
    out_of_stock = len([x for x in inv if int(x.get("available_qty") or 0) == 0])

    replen_raw = get_replenishment_suggestions(db=db)
    replen = replen_raw.get("data") if isinstance(replen_raw, dict) and "data" in replen_raw else replen_raw
    urgent = len([x for x in replen if x.get("suggested_qty", 0) > 0]) if isinstance(replen, list) else 0

    slow = get_slow_moving_products(db)
    slow_list = slow.get("data") if isinstance(slow, dict) and "data" in slow else (slow if isinstance(slow, list) else [])
    slow_count = len([x for x in slow_list if x.get("level") == "滞销"])
    cold_count = len([x for x in slow_list if x.get("level") == "冷淡"])

    return ok({
        "total_products": total,
        "low_stock": low_stock,
        "out_of_stock": out_of_stock,
        "urgent_replenish": urgent,
        "suggestions_count": len(replen) if isinstance(replen, list) else 0,
        "slow_moving": slow_count,
        "cold_count": cold_count,
    })


@router.get('/trend-analysis')
def trend_analysis(days: int = 30, channel: str = 'jd', db = get_db()):
    """趋势分析：日/周/月维度聚合"""
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    orders = []
    inventory = []
    try:
        from app.core.database import get_conn as _gconn
        _c = _gconn()
        orders = [{"ordered_at": r[0], "total_amount": r[1], "product_name": r[2]}
                  for r in _c.execute("SELECT ordered_at, total_amount, product_name FROM orders WHERE channel=? AND ordered_at>=? AND (deleted_at='') AND order_status IN ('待发货','已发货','已完成','申请退款')", (channel, cutoff)).fetchall()]
        inventory = [{"available_qty": r[0], "safety_qty": r[1]}
                     for r in _c.execute("SELECT available_qty, safety_qty FROM inventory WHERE channel=?", (channel,)).fetchall()]
    except Exception:
        orders, inventory = [], []

    daily = defaultdict(lambda: {'gmv': 0, 'orders': 0})
    cat_count = defaultdict(int)
    for o in orders:
        date = (o.get('ordered_at') or '')[:10]
        daily[date]['gmv'] += float(o.get('total_amount') or 0)
        daily[date]['orders'] += 1
        cat = o.get('product_name', '未知')[:4]
        cat_count[cat] += 1

    trend = [{'date': d, **v} for d, v in sorted(daily.items())[-days:]]
    cat_pie = [{'name': k, 'value': v} for k, v in sorted(cat_count.items(), key=lambda x: -x[1])[:10]]
    inv_status = {
        'normal': sum(1 for i in inventory if int(i.get('available_qty') or 0) >= int(i.get('safety_qty') or 0)),
        'low': sum(1 for i in inventory if 0 < int(i.get('available_qty') or 0) < int(i.get('safety_qty') or 0)),
        'out': sum(1 for i in inventory if int(i.get('available_qty') or 0) <= 0),
    }
    return {'daily': trend, 'categories': cat_pie, 'inventory_health': inv_status,
            'total_gmv': sum(d['gmv'] for d in trend), 'total_orders': sum(d['orders'] for d in trend)}

@router.get('/anomaly-tracking')
def anomaly_tracking(db = get_db()):
    """异常追踪：告警 + 质量日志汇总"""
    alerts = db.table("alerts").select("*").order("id", desc=True).limit(100).execute().data or []
    quality = db.table("quality_logs").select("*").order("id", desc=True).limit(100).execute().data or []
    events = db.table("events").select("*").order("id", desc=True).limit(100).execute().data or []
    return {
        'alerts': alerts,
        'quality_logs': quality,
        'events': events,
        'summary': {
            'alert_count': len(alerts),
            'active_alerts': sum(1 for a in alerts if a.get('status') == 'active'),
            'error_count': sum(1 for q in quality if q.get('level') == 'error'),
            'event_count': len(events),
        }
    }

@router.post('/sync-from-orders')
def sync_inventory_from_orders(db = get_db(), limit: int = 200):
    """根据最近订单自动调整库存（异步调用）"""
    orders = [o for o in db.table("orders").select("*").order("id", desc=True).limit(limit).execute().data if not (o.get("deleted_at") or "")]
    count = 0
    for o in orders:
        try:
            auto_adjust_inventory(o, 'cleansing', db)
            count += 1
        except Exception:
            pass
    return {'ok': True, 'synced': count, 'scanned': len(orders)}


def auto_adjust_inventory(order_data: dict, order_type: str, db):
    sku = order_data.get("sku", "")
    qty = int(float(order_data.get("quantity", 0)))
    if not sku or qty <= 0:
        return

    inv_list = db.table("inventory").select("*").eq("sku", sku).execute().data
    if inv_list:
        inv = inv_list[0]
        avail = int(inv.get("available_qty") or 0)
        if order_type in ("jd_purchase", "cleansing_purchase"):
            new_avail = avail + qty
            db.table("inventory").update({"available_qty": new_avail}).eq("id", inv["id"]).execute()
            inv["available_qty"] = new_avail
        elif order_type in ("sales", "jd_sales", "cleansing"):
            new_avail = max(0, avail - qty)
            db.table("inventory").update({"available_qty": new_avail}).eq("id", inv["id"]).execute()
            inv["available_qty"] = new_avail
        else:
            return
        # Emit inventory.changed so alert/event handlers fire
        try:
            from app.core.events import bus
            bus.emit('inventory.changed', {
                'inventory': inv,
                'action': 'auto_adjust',
                'quantity': qty,
                'order_type': order_type,
            })
        except Exception:
            pass
    else:
        db.table("inventory").insert({
            "sku": sku,
            "product_name": order_data.get("product_name", ""),
            "store": order_data.get("store", ""),
            "available_qty": qty if order_type in ("jd_purchase", "cleansing_purchase") else 0,
            "locked_qty": 0,
            "in_transit_qty": 0,
            "safety_qty": 10,
        }).execute()
@router.get('/with-sales')
def inventory_with_sales(wh_type: str = 'own', channel: str = 'jd', page: int = 0, page_size: int = 0, search: str = '', db = get_db()):
    """库存列表 + 日销 + 在库周转 + 当月出入库
    wh_type: own=自有仓, platform=平台仓(C仓), platform_b=B仓
    page/page_size: 翻页参数，传 0 返回全部
    """
    # 结果缓存 300s（_replen_version 校验：库存/订单/商品变更自动失效）
    import time as _t
    _cache_key = f"{wh_type}|{channel}|p{page}|s{search}"
    _now_ts = _t.time()
    try:
        # 读 _replen_version（库存/订单/商品变更都递增它）
        _vrow = db.table("replenishment_config").select("*").eq("key", "_replen_version").execute().data
        _ver = int(_vrow[0]["value"]) if _vrow and _vrow[0].get("value") else 0
    except Exception:
        _ver = 0
    _cached = _with_sales_cache.get(_cache_key)
    if _cached and _cached.get('ver') == _ver and _now_ts - _cached.get('ts', 0) < 300:
        return ok(_cached['data'])
    # 惰性归档：每天最多检查一次是否有超期订单需归档（不依赖凌晨任务）
    try:
        _arc = db.table("replenishment_config").select("*").eq("key", "_last_archive_check").execute().data
        _last_arc = _arc[0]['value'] if _arc else ''
        from datetime import timedelta as _td, timezone
        if _last_arc != (datetime.now(timezone.utc) - _td(days=1)).strftime('%Y-%m-%d'):
            # 惰性归档(带保护: daily_stats写入失败不删orders)
            from app.core.scheduler import _task_archive_orders
            try:
                _task_archive_orders()
            except Exception as _ae:
                import logging; logging.warning(f"[with-sales] lazy archive: {_ae}")
            db.table("replenishment_config").upsert({"key": "_last_archive_check", "value": datetime.now(timezone.utc).strftime('%Y-%m-%d'), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except Exception:
        pass
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    cur_month = now.strftime('%Y-%m')
    # 日销已从快照读取，orders 全量加载已移除（死代码，改用快照）
    # 出入库记录只取当月
    month_start = now.replace(day=1).strftime('%Y-%m-%d')
    # 分页：先取当前页 inventory（真分页——只算当前页 SKU 的日销/周转）
    _pg_total = 0
    _inv_q = db.table("inventory").select("*").eq("warehouse_type", wh_type).eq("channel", channel)
    if search:
        _like = f"%{search}%"
        _s1 = db.table("inventory").select("*").eq("warehouse_type", wh_type).eq("channel", channel).ilike("sku", _like)
        _s2 = db.table("inventory").select("*").eq("warehouse_type", wh_type).eq("channel", channel).ilike("product_name", _like)
        _inv_q = _s1.or_(_s2)
    if page > 0 and page_size > 0:
        _cnt_q = db.table("inventory").select("count(*)").eq("warehouse_type", wh_type).eq("channel", channel)
        if search:
            _cnt_q = _cnt_q.ilike("sku", f"%{search}%")
        _cnt = _cnt_q.execute()
        _pg_total = _cnt.count if hasattr(_cnt, 'count') else len(_cnt.data or [])
        inv = _inv_q.order("id", desc=True).limit(page_size).offset((page - 1) * page_size).execute().data or []
    else:
        inv = _inv_q.execute().data or []
    _pg_skus = set(str(i.get('sku','')) for i in inv)
    _is_pg = page > 0 and page_size > 0
    _pg_in = list(_pg_skus) if _is_pg else []
    month_end = now.strftime('%Y-%m-%d')
    inbound_month = {}
    outbound_month = {}
    try:
        from app.core.database import get_conn as _gconn2
        _ic = _gconn2()
        if _is_pg and _pg_in:
            _in_rows = _ic.execute("SELECT sku, SUM(quantity) FROM inbound_records WHERE inbound_date>=? AND channel=? AND sku IN (%s) GROUP BY sku" % ','.join(['?']*len(_pg_in)), [month_start, channel] + _pg_in).fetchall()
            _out_rows = _ic.execute("SELECT sku, SUM(quantity) FROM outbound_records WHERE outbound_date>=? AND channel=? AND sku IN (%s) GROUP BY sku" % ','.join(['?']*len(_pg_in)), [month_start, channel] + _pg_in).fetchall()
        else:
            _in_rows = _ic.execute("SELECT sku, SUM(quantity) FROM inbound_records WHERE inbound_date>=? AND channel=? GROUP BY sku", (month_start, channel)).fetchall()
            _out_rows = _ic.execute("SELECT sku, SUM(quantity) FROM outbound_records WHERE outbound_date>=? AND channel=? GROUP BY sku", (month_start, channel)).fetchall()
        for r in _in_rows:
            inbound_month[r[0]] = int(r[1] or 0)
        for r in _out_rows:
            outbound_month[r[0]] = int(r[1] or 0)
    except Exception as _e2:
        import logging; logging.warning(f"[with-sales] in/out agg: {_e2}")
    sales_28 = {}
    products_for_barcode = {}
    try:
        from app.core.database import get_conn as _gconn
        _psql = "SELECT sku, barcode, price, brand FROM products WHERE (deleted_at IS NULL OR deleted_at='')"
        _pp = None
        if _is_pg and _pg_in:
            _psql += " AND sku IN (%s)" % ','.join(['?']*len(_pg_in))
            _pp = _pg_in
        for _r in _gconn().execute(_psql, _pp).fetchall():
            products_for_barcode[_r[0]] = {"sku": _r[0], "barcode": _r[1] or '', "price": _r[2] or 0, "brand": _r[3] or ''}
    except Exception:
        products_for_barcode = {}
    # 从快照聚合 28 天日销（替代 orders 全表遍历）
    from app.core.sales_utils import load_daily_sales, calc_sales_multi, rolling_predict
    daily_28 = load_daily_sales(28, db, sku_barcode_map={s: (p.get('barcode','') or '') for s,p in products_for_barcode.items()}, skus=_pg_in if (_is_pg and _pg_in) else None)
    for key, daily in daily_28.items():
        sales_28[key] = sum(daily.values())
    # 融合日销（一次遍历算 7/14/28 三窗口 + 趋势加权，用于周转天数计算）
    _multi = calc_sales_multi(daily_28, windows=[7, 14, 28])
    _s7, _s14, _s28 = _multi[7], _multi[14], _multi[28]
    _fused = {}
    for _sk in set(list(_s7.keys()) + list(_s14.keys()) + list(_s28.keys())):
        _fused[_sk] = rolling_predict(_s7.get(_sk, 0), _s14.get(_sk, 0), _s28.get(_sk, 0))
    result = []
    # B→C调拨在途列: 读 inventory.c_transit 真实列(该 SKU 的 B→C 调拨在途总量, 按 SKU 聚合)
    # 曾误用 C 仓 in_transit_qty 之和(那是供应商→C, 与 BBCC 调拨在途口径不符, 造成进销存/建议页不同源)
    c_transit = {}
    try:
        _all_wh = db.table("inventory").select("*").execute().data or []
        for _row in _all_wh:
            _s = _row.get('sku', '')
            _v = int(_row.get('c_transit') or 0)
            if _v > 0:
                c_transit[_s] = c_transit.get(_s, 0) + _v
    except Exception as e:
        import logging; logging.warning(f"[inv] c_transit agg: {e}")
    for i in inv:
        sku = i['sku']
        bc = (products_for_barcode.get(sku) or {}).get('barcode', '')
        sales_key = f"{sku}|{bc}" if bc else sku
        ds = round(sales_28.get(sales_key, 0), 1)
        avail = int(i.get('available_qty',0) or 0)
        begin = avail - inbound_month.get(sku, 0) + outbound_month.get(sku, 0)
        # 单价：从 products 主表联表获取
        price = 0
        _p = products_for_barcode.get(sku) or {}
        try: price = float(_p.get('price') or 0)
        except Exception: price = 0
        # 周转天数 = 可用库存 / 融合日销（三窗口 3σ 剔除 + 趋势加权，比简单平均更精准）
        fused_ds = _fused.get(sales_key, 0) or _fused.get(sku, 0)
        turnover_days = round(avail / fused_ds, 1) if fused_ds > 0 else None
        result.append({
            'id': i['id'],
            'sku': sku,
            'barcode': bc,
            'brand': (_p.get('brand') or ''),
            'product_name': i.get('product_name',''),
            'price': price,
            'store': i.get('store',''),
            'warehouse': i.get('warehouse',''),
            'warehouse_type': i.get('warehouse_type','platform'),
            'channel': i.get('channel', 'jd'),
            'available_qty': avail,
            'in_transit_qty': int(i.get('in_transit_qty',0) or 0),
            'c_transit': c_transit.get(sku, 0) if wh_type == 'platform_b' else 0,
            'daily_sales': ds,
            'month_inbound': inbound_month.get(sku, int(i.get('month_inbound',0) or 0)),
            'month_outbound': outbound_month.get(sku, int(i.get('month_outbound',0) or 0)),
            'beginning_stock': int(i.get('beginning_stock',0) or 0) or begin,
            'month_start': month_start,
            'month_end': month_end,
            'turnover_days': turnover_days,
        })
    # 注入批次摘要（最早过期批次生产/截止/效期状态/总效期，按主体隔离）
    try:
        from app.api.routes.inventory import _get_batch_summary
        _bs_map = _get_batch_summary(channel, wh_type)
        for _item in result:
            _bk = (_item.get('sku',''), _item.get('channel','jd'))
            _b = _bs_map.get(_bk)
            if _b:
                _item['batch_prod_date'] = _b[0]
                _item['batch_exp_date'] = _b[1]
                _item['batch_status'] = _b[2]
                _item['batch_pct'] = _b[3]
                _item['batch_days'] = _b[5]
                _item['batch_count'] = _b[6]
            else:
                _item['batch_prod_date'] = _item['batch_exp_date'] = _item['batch_status'] = ''
                _item['batch_pct'] = 0
                _item['batch_days'] = 0
                _item['batch_count'] = 0
    except Exception as _e:
        import logging; logging.warning(f"[with-sales] batch inject: {_e}")
    total = _pg_total if (_pg_total > 0) else len(result)
    if page > 0 and page_size > 0:
        return ok({"items": result, "total": total, "page": page, "page_size": page_size})
    # 写缓存（30s TTL + 版本号）
    try:
        _with_sales_cache[_cache_key] = {'data': result, 'ts': _t.time(), 'ver': _ver}
    except Exception:
        pass
    if page > 0 and page_size > 0:
        result = result[(page - 1) * page_size: page * page_size]
        return ok({"items": result, "total": total, "page": page, "page_size": page_size})
    return ok(result)