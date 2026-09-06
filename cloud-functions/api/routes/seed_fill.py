"""种子数据完整生成器 —— PA 原版 seed.py(790 行)逻辑完整移植(TiDB 原生)

规模与原版一致: jd 1000 SKU/1100 单每天 + other 1000 SKU/550 单每天 × 60 天 ≈ 10 万订单
含: 促销波峰(618 第5-20天/月末45-55天)、周末回落、滞销 3%+低动销 2% SKU、
金额 GMV 明细口径、8 仓库存(18% 低库存)、批次效期(4% 问题批次)、
当月出入库记录、库存月汇总、补货参数+内置规则、规则引擎告警、日销快照
"""
import json
import random
import time
from datetime import datetime, timedelta, timezone

from db import query, one, execute, executemany

BRANDS_FOOD = ['禾味', '山泉', '椒香', '酱乡', '醋乡', '味源', '禾田', '青禾', '禾风',
               '谷香', '醇味', '鲜禾', '禾记']
BRANDS_SNACK = ['薯乐', '果脆', '禾果', '咔脆', '香脆', '谷脆', '果乐', '脆脆', '禾零', '脆香']
BRANDS_HOME = ['净洁', '柔白', '净香', '洁舒', '柔洁', '净白', '舒洁', '洁净', '净舒', '白净', '净柔', '净力']
CATS = ['酱油', '酱料', '调味汁', '食用油', '醋', '料酒', '蚝油', '芝麻油', '辣椒酱', '拌面酱',
        '老抽', '生抽', '陈醋', '香醋', '白醋', '米醋', '花椒油', '藤椒油', '辣椒油', '芥末油',
        '番茄酱', '甜辣酱', '沙拉酱', '芝麻酱', '花生酱', '豆瓣酱', '豆豉', '腐乳', '糟卤', '鱼露',
        '咖喱块', '咖喱粉', '五香粉', '孜然粉', '花椒粉', '辣椒粉', '胡椒粉', '十三香', '卤料包', '炖肉料',
        '鸡精', '味精', '白糖', '冰糖', '红糖', '麦芽糖', '蜂蜜', '黄酒', '米酒',
        '薯片', '虾条', '爆米花', '坚果', '瓜子', '花生', '饼干', '威化', '巧克力', '糖果',
        '洗衣液', '洗洁精', '洗手液', '消毒液', '纸巾', '湿巾', '垃圾袋', '保鲜膜', '保鲜袋', '收纳盒']
STORES = ['自营旗舰店', '直营店', '调味品专营店', '食品旗舰店', '综合食品店']
WH = [('北京仓', 'platform'), ('上海仓', 'platform'), ('成都仓', 'platform'), ('武汉仓', 'platform'),
      ('沈阳仓', 'platform'), ('西安仓', 'platform'), ('郑州仓', 'platform'), ('集货仓', 'own'),
      ('三方仓', 'own'), ('B仓', 'platform_b')]
SUP = [
    ('SUP-001', '云味食品(演示)', '王小明', '010-80000001', 5),
    ('SUP-002', '谷香调味(演示)', '李小红', '010-80000002', 4),
    ('SUP-003', '椒香园食品(演示)', '赵大勇', '010-80000003', 5),
    ('SUP-004', '青禾食品(演示)', '孙晓梅', '010-80000004', 3),
    ('SUP-005', '禾味坊调味(演示)', '周建华', '010-80000005', 4),
    ('SUP-006', '鲜禾食品(演示)', '吴丽华', '010-80000006', 5),
    ('SUP-007', '醇味调味(演示)', '郑国栋', '010-80000007', 4),
    ('SUP-008', '禾田食品(演示)', '陈志强', '010-80000008', 3),
    ('SUP-009', '山泉食品(演示)', '林秀英', '010-80000009', 4),
    ('SUP-010', '净洁日化(演示)', '黄文博', '010-80000010', 5),
]

_DEMO_SLOW = set()
_DEMO_LOW = set()


def _cat_group(cat):
    if cat in CATS[50:60]:
        return 'snack'
    if cat in CATS[60:]:
        return 'home'
    return 'food'


def _brand_group(brand):
    if brand in BRANDS_SNACK:
        return 'snack'
    if brand in BRANDS_HOME:
        return 'home'
    return 'food'


def _pick_brand(cat):
    g = _cat_group(cat)
    pool = {'food': BRANDS_FOOD, 'snack': BRANDS_SNACK, 'home': BRANDS_HOME}[g]
    return random.choice(pool)


def _make_skus(sfx, count=1000):
    """SKU 生成器(与原版 make_skus 一致): SKU-{i:04d}{sfx}"""
    r = []
    for i in range(1, count + 1):
        c = CATS[(i - 1) % len(CATS)]
        s = STORES[(i - 1) % len(STORES)]
        price_type = random.choices(['normal', 'low', 'high'], [80, 10, 10])[0]
        if price_type == 'low':
            p = round(random.uniform(1.9, 5.0), 1)
        elif price_type == 'high':
            p = round(random.uniform(100, 299), 1)
        else:
            p = round(random.uniform(5.8, 99.9), 1)
        r.append({'sku': 'SKU-%04d%s' % (i, sfx), 'name': '%s%d' % (c, i), 'store': s, 'cat': c,
                  'price': p, 'box': random.choice([6, 12, 24]),
                  'barcode': '690%010d' % i, 'weight': round(random.uniform(5, 25), 1),
                  'volume': round(random.uniform(0.02, 0.12), 3),
                  'status': 'active', 'brand': _pick_brand(c)})
    return r


def _seed_products_suppliers(skus_data):
    sup_brands = []
    for s_idx in range(10):
        g = ['food', 'snack', 'home'][s_idx % 3]
        pool = {'food': BRANDS_FOOD, 'snack': BRANDS_SNACK, 'home': BRANDS_HOME}[g]
        sup_brands.append(random.sample(pool, min(random.randint(1, 3), len(pool))))
    prod_rows = []
    for ch, skus in skus_data.items():
        for i, p in enumerate(skus):
            _sup_idx = i % 10
            _sup_code = 'SUP-%03d-%s' % (_sup_idx + 1, 'JD' if ch == 'jd' else 'OTHER')
            _blist = sup_brands[_sup_idx]
            _g = _cat_group(p.get('cat', ''))
            _matched = [b for b in _blist if _brand_group(b) == _g]
            _brand = _matched[(i // 10) % len(_matched)] if _matched else _pick_brand(p.get('cat', ''))
            prod_rows.append({'sku': p['sku'], 'product_name': p['name'], 'store': p['store'],
                              'category': p['cat'], 'price': p['price'], 'box_qty': p['box'],
                              'barcode': p['barcode'], 'weight': p['weight'], 'volume': p['volume'],
                              'status': p['status'], 'channel': ch, 'supplier_code': _sup_code,
                              'brand': _brand, 'unit': '瓶'})
    for i in range(0, len(prod_rows), 500):
        cols = list(prod_rows[0].keys())
        executemany("INSERT INTO products(%s) VALUES(%s) ON DUPLICATE KEY UPDATE product_name=VALUES(product_name), price=VALUES(price), brand=VALUES(brand), status='active'" %
                    (", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols))),
                    [tuple(p[c] for c in cols) for p in prod_rows[i:i + 500]])
    sup_rows = []
    for s_idx, (code, name, contact, phone, score) in enumerate(SUP):
        _blist = sup_brands[s_idx % 10]
        for ch in ['jd', 'other']:
            sup_rows.append({'supplier_code': '%s-%s' % (code, 'JD' if ch == 'jd' else 'OTHER'),
                             'supplier_name': name, 'contact_person': contact, 'contact_phone': phone,
                             'score': score, 'channel': ch, 'brand': '，'.join(_blist)})
    cols = list(sup_rows[0].keys())
    executemany("INSERT INTO suppliers(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
                                                          ", ".join(["%s"] * len(cols))),
                [tuple(s[c] for c in cols) for s in sup_rows])
    return prod_rows, sup_rows


def _seed_orders(today, skus_data):
    """全量订单(兼容): 等价于 _seed_orders_range(0, 60)"""
    return _seed_orders_range(today, skus_data, 0, 60)


def _seed_orders_range(today, skus_data, day_from, day_to):
    """按天范围生成订单(day_from <= day_offset < day_to)并写入(异步分步执行用)

    随机池预生成(统计等价, 消除 18 万次 random 调用); 500/批写入(TiDB serverless 单条 INSERT 上限)"""
    global _DEMO_SLOW, _DEMO_LOW
    if day_from == 0:
        _DEMO_SLOW, _DEMO_LOW = set(), set()
    total = 0
    batch = []

    def _amt(q, price, disc_r, freight_r, sub_r):
        base = round(q * price, 2)
        disc = round(base * disc_r, 2)
        return {'total_amount': base, 'discount_amount': round(disc, 2),
                'freight_amount': freight_r, 'subsidy_amount': round(sub_r, 2), 'tax_amount': 0.0,
                'actual_amount': round(base - disc - sub_r + freight_r, 2)}

    def flush():
        nonlocal batch
        if not batch:
            return
        cols = ['order_no', 'store', 'warehouse', 'sku', 'product_name', 'quantity', 'unit_price',
                'total_amount', 'discount_amount', 'freight_amount', 'subsidy_amount', 'tax_amount',
                'actual_amount', 'order_status', 'ordered_at', 'paid_at', 'channel', 'platform',
                'data_source']
        executemany("INSERT INTO orders(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
                                                           ", ".join(["%s"] * len(cols))),
                    [tuple(o[c] for c in cols) for o in batch])
        batch = []

    for ch, label, skus, base in [('jd', 'jd', skus_data['jd'], 1100),
                                  ('other', 'other', skus_data['other'], 550)]:
        promo = {'618': list(range(5, 20)), 'month_end': list(range(45, 55))}
        _n = len(skus)
        _shuffled = skus[:]
        random.shuffle(_shuffled)
        _slow_skus = set(x['sku'] for x in _shuffled[:_n * 3 // 100]) if _n >= 50 else set()
        _low_idx = set(x['sku'] for x in _shuffled[_n * 3 // 100:_n * 5 // 100]) if _n >= 50 else set()
        _DEMO_SLOW |= _slow_skus
        _DEMO_LOW |= _low_idx
        _normal_skus = [x for x in skus if x['sku'] not in _slow_skus and x['sku'] not in _low_idx]
        _sku_map = {x['sku']: x for x in skus}  # 原生优化: 避免低动销分支逐条线性扫描
        c_whs = [w for w, wt in WH if wt == 'platform']
        # 随机池预生成(统计等价, 消除 18 万次 random 调用 → 生成提速)
        pool = {
            'rand': [random.random() for _ in range(220000)],
            'qty': [random.randint(1, 8) for _ in range(220000)],
            'qty_promo': [random.randint(1, 20) for _ in range(220000)],
            'status': [random.choices(['已完成', '已发货', '待发货', '待确认', '申请退款'],
                                      [45, 18, 15, 10, 7])[0] for _ in range(220000)],
            'disc': [round(random.uniform(0.02, 0.10), 2) if random.random() < 0.5 else 0
                     for _ in range(220000)],
            'freight': [random.choice([0, 0, 0, 6, 8, 12]) for _ in range(220000)],
            'sub': [round(random.uniform(0.03, 0.12), 2) if random.random() < 0.3 else 0
                    for _ in range(220000)],
            'paid': [random.randint(1, 3) for _ in range(220000)],
            'sku': [random.choice(_normal_skus if _normal_skus else skus) for _ in range(220000)],
        }
        _pi = 0
        for d in range(day_from, day_to):
            dt = today - timedelta(days=d)
            is_promo = any(d in v for v in promo.values())
            cnt = int(base * random.uniform(2, 4)) if is_promo else \
                (int(base * random.uniform(0.6, 1.2)) if dt.weekday() >= 5 else base)
            # 低动销 SKU: 每 30 天 1 单(最后销售在 15-30 天前 → observe)
            if d % 30 == 18 or is_promo:
                for lsk in _low_idx:
                    sk = _sku_map.get(lsk)
                    if not sk:
                        continue
                    q = random.randint(1, 4)
                    _a = _amt(q, sk['price'], 0, 0, 0)
                    batch.append({'order_no': '%s-L%03d-%s' % (label.upper(), d, lsk[-3:]),
                                  'store': sk['store'], 'warehouse': random.choice(c_whs),
                                  'sku': sk['sku'], 'product_name': sk['name'], 'quantity': q,
                                  'unit_price': sk['price'], **_a,
                                  'order_status': random.choices(['已完成', '已发货'], [80, 20])[0],
                                  'ordered_at': dt.strftime('%Y-%m-%d'),
                                  'paid_at': dt.strftime('%Y-%m-%d'),
                                  'channel': ch, 'platform': '京东' if label == 'jd' else '天猫',
                                  'data_source': 'seed'})
                    total += 1
            for _ in range(cnt):
                sk = pool['sku'][_pi % len(pool['sku'])]
                q = pool['qty_promo'][_pi] if is_promo else pool['qty'][_pi]
                st = pool['status'][_pi]
                if pool['rand'][_pi] < 0.03:
                    st = '已退货'
                paid_dt = dt + timedelta(days=pool['paid'][_pi])
                _a = _amt(q, sk['price'], pool['disc'][_pi], pool['freight'][_pi], pool['sub'][_pi])
                batch.append({'order_no': '%s-%s%03d-%05d' % (label.upper(), ch, d, total % 100000),
                              'store': sk['store'], 'warehouse': c_whs[_pi % len(c_whs)],
                              'sku': sk['sku'], 'product_name': sk['name'], 'quantity': q,
                              'unit_price': sk['price'], **_a, 'order_status': st,
                              'ordered_at': dt.strftime('%Y-%m-%d'),
                              'paid_at': paid_dt.strftime('%Y-%m-%d'),
                              'channel': ch, 'platform': '京东' if label == 'jd' else '天猫',
                              'data_source': 'seed'})
                _pi += 1
                total += 1
                if len(batch) >= 500:
                    flush()
    flush()
    return total


def _seed_inventory(skus_data):
    inv = []
    for ch, skus in skus_data.items():
        for sk in skus:
            low = random.random() < 0.18
            seen_own = False  # 每个 SKU 内重置: WH 中 own 仓只保留一个(集货仓/三方仓)
            for wn, wt in WH:
                if wt == 'platform_b' and ch != 'jd':
                    continue
                if wt == 'own':
                    if seen_own:
                        continue
                    seen_own = True
                    wh_name = '集货仓' if ch == 'jd' else '三方仓'
                else:
                    wh_name = wn
                if sk['sku'] in _DEMO_SLOW:
                    q = random.randint(20, 60)
                elif sk['sku'] in _DEMO_LOW:
                    q = random.randint(30, 80)
                elif low and wt == 'platform':
                    q = random.randint(0, 5)
                elif low and wt == 'platform_b':
                    q = random.randint(0, 3)
                elif low and wt == 'own':
                    q = random.randint(0, 8)
                else:
                    q = random.randint(50, 800)
                inv.append({'sku': sk['sku'], 'product_name': sk['name'], 'warehouse': wh_name,
                            'warehouse_type': wt, 'available_qty': q,
                            'in_transit_qty': 0 if low else random.randint(0, 200),
                            'safety_qty': random.randint(30, 200),
                            'channel': ch, 'barcode': sk['barcode']})
    for i in range(0, len(inv), 500):
        cols = ['sku', 'product_name', 'warehouse', 'warehouse_type', 'available_qty',
                'in_transit_qty', 'safety_qty', 'channel', 'barcode']
        executemany("INSERT INTO inventory(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
                                                              ", ".join(["%s"] * len(cols))),
                    [tuple(x[c] for c in cols) for x in inv[i:i + 500]])
    return len(inv)


def _seed_batches():
    """批次效期: 按有货库存行 1~3 批, 4% 问题 SKU(过期/临近), 回写 products.best_before"""
    from datetime import datetime as _dt
    today = _dt.now(timezone.utc)
    rows = query("SELECT sku, warehouse, warehouse_type, channel, available_qty FROM inventory "
                 "WHERE available_qty > 0")
    _pcat = {}
    for r in query("SELECT sku, category, channel FROM products"):
        _pcat[(str(r.get('sku')), str(r.get('channel') or 'jd'))] = str(r.get('category') or '')
    problem = set()
    _all = sorted({(str(r.get('sku')), str(r.get('channel') or 'jd')) for r in rows})
    for idx, (s, c) in enumerate(_all):
        chk = random.random()
        if chk < 0.02:
            problem.add(s)
        elif chk < 0.04:
            problem.add(s)
    bdata = []
    best_map = {}
    for r in rows:
        sku, wh, wht, ch, qty = str(r.get('sku')), str(r.get('warehouse') or ''), \
            str(r.get('warehouse_type') or ''), str(r.get('channel') or 'jd'), int(r.get('available_qty') or 0)
        if qty <= 0:
            continue
        cat = _pcat.get((sku, ch), '')
        foodish = any(k in cat for k in ['酱油', '酱', '醋', '油', '酒', '糖', '蜂', '咖', '粉',
                                         '薯', '坚果', '饼干', '巧克力', '糖果', '麻辣', '椒']) if cat else False
        shelf = random.randint(150, 270) if foodish else random.randint(200, 365)
        is_prob = sku in problem
        n_batch = random.randint(1, 3)
        parts = [0.6, 0.3, 0.1][:n_batch]
        qty_left = qty
        for bi, ratio in enumerate(parts):
            bq = int(qty * ratio) if bi < n_batch - 1 else qty_left
            qty_left -= bq
            if bq <= 0:
                continue
            if is_prob and random.random() < 0.5:
                if random.random() < 0.5:
                    _ago = shelf + random.randint(3, 15)
                else:
                    _ago = random.randint(max(shelf // 3 - 2, 5), max(shelf // 3 + 2, 8))
            else:
                _ago = random.randint(2, max(shelf // 3 - 6, 5))
            prod = today - timedelta(days=_ago)
            exp = prod + timedelta(days=shelf)
            bdata.append({'sku': sku, 'warehouse': wh, 'warehouse_type': wht, 'channel': ch,
                          'prod_date': prod.strftime('%Y-%m-%d'), 'exp_date': exp.strftime('%Y-%m-%d'),
                          'qty': bq})
            if sku not in best_map or exp < best_map[sku]:
                best_map[sku] = exp
    for i in range(0, len(bdata), 500):
        cols = ['sku', 'warehouse', 'warehouse_type', 'channel', 'prod_date', 'exp_date', 'qty']
        executemany("INSERT INTO batches(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
                                                            ", ".join(["%s"] * len(cols))),
                    [tuple(b[c] for c in cols) for b in bdata[i:i + 500]])
    for sku, exp in best_map.items():
        execute("UPDATE products SET best_before=%s WHERE sku=%s AND (best_before='' OR best_before IS NULL)",
                (exp.strftime('%Y-%m-%d'), sku))
    for sku in problem:
        if sku in best_map:
            execute("UPDATE products SET best_before=%s WHERE sku=%s",
                    (best_map[sku].strftime('%Y-%m-%d'), sku))
    return len(bdata)


def _seed_records():
    """当月出入库记录(进销存页展示): 按批次池随机, INSERT IGNORE 防唯一冲突"""
    today = datetime.now(timezone.utc)
    _batch_pool = {}
    for r in query("SELECT sku, warehouse, prod_date, exp_date FROM batches"):
        _batch_pool.setdefault(str(r.get('sku')), []).append(
            {'wh': str(r.get('warehouse') or ''), 'pd': str(r.get('prod_date') or '')[:10],
             'ed': str(r.get('exp_date') or '')[:10]})
    in_rows, out_rows = [], []
    for sku, sk_name, ch in [(r.get('sku'), r.get('product_name'), r.get('channel'))
                             for r in query("SELECT sku, product_name, channel FROM products")]:
        _pool = _batch_pool.get(sku, [])
        max_days = max(today.day - 1, 6)
        used = set()
        for _ in range(random.randint(1, min(3, max_days + 1))):
            days_back = random.randint(0, max_days)
            while days_back in used:
                days_back = random.randint(0, max_days)
            used.add(days_back)
            _bp = _be = _wh = ''
            if _pool:
                _b = random.choice(_pool)
                _bp, _be, _wh = _b['pd'], _b['ed'], _b['wh']
            in_rows.append({'sku': sku, 'product_name': sk_name, 'quantity': random.randint(50, 500),
                            'supplier': '供应商-%s' % sku[-3:],
                            'inbound_date': (today - timedelta(days=days_back)).strftime('%Y-%m-%d'),
                            'channel': ch, 'prod_date': _bp, 'exp_date': _be, 'warehouse': _wh})
        used = set()
        for _ in range(random.randint(1, min(2, max_days + 1))):
            days_back = random.randint(0, max_days)
            while days_back in used:
                days_back = random.randint(0, max_days)
            used.add(days_back)
            _bp = _be = _wh = ''
            if _pool:
                _b = random.choice(_pool)
                _bp, _be, _wh = _b['pd'], _b['ed'], _b['wh']
            out_rows.append({'sku': sku, 'product_name': sk_name, 'quantity': random.randint(10, 100),
                             'target_warehouse': 'B仓',
                             'outbound_date': (today - timedelta(days=days_back)).strftime('%Y-%m-%d'),
                             'channel': ch, 'prod_date': _bp, 'exp_date': _be, 'warehouse': _wh})
    for table, rows, date_col in (('inbound_records', in_rows, 'inbound_date'),
                                  ('outbound_records', out_rows, 'outbound_date')):
        for i in range(0, len(rows), 500):
            cols = list(rows[0].keys())
            sql = "INSERT IGNORE INTO `%s`(%s) VALUES(%s)" % (
                table, ", ".join("`%s`" % c for c in cols), ", ".join(["%s"] * len(cols)))
            executemany(sql, [tuple(r[c] for c in cols) for r in rows[i:i + 500]])
    return len(in_rows), len(out_rows)


def _sync_inv_month():
    """同步出入库记录到 inventory 月汇总 + 期初库存反推(与原版一致)"""
    month_start = datetime.now(timezone.utc).strftime('%Y-%m-01')
    for table, date_col, target_col in (('inbound_records', 'inbound_date', 'month_inbound'),
                                        ('outbound_records', 'outbound_date', 'month_outbound')):
        rows = query("SELECT sku, warehouse, channel, SUM(quantity) AS q FROM `%s` "
                     "WHERE channel IN ('jd','other') AND `%s` >= %%s GROUP BY sku, warehouse, channel"
                     % (table, date_col), [month_start])
        for r in rows:
            try:
                execute("UPDATE inventory SET `%s` = %%s WHERE sku=%%s AND warehouse=%%s "
                        "AND channel=%%s AND warehouse_type='own'" % target_col,
                        (int(r.get('q') or 0), str(r.get('sku')), str(r.get('warehouse') or ''),
                         str(r.get('channel') or 'jd')))
            except Exception:
                pass
    # 期初 = 可用 - 入库 + 出库; 为负则调低入库使期初 >= 0
    execute("UPDATE inventory SET beginning_stock = available_qty - month_inbound + month_outbound "
            "WHERE channel IN ('jd','other') AND warehouse_type='own'")
    execute("UPDATE inventory SET month_inbound = available_qty + month_outbound "
            "WHERE channel IN ('jd','other') AND warehouse_type='own' AND beginning_stock < 0")
    execute("UPDATE inventory SET beginning_stock = available_qty - month_inbound + month_outbound "
            "WHERE channel IN ('jd','other') AND warehouse_type='own' AND beginning_stock < 0")


def _seed_config():
    """补货参数 + 内置规则(原版 4 条, 与实时评估引擎条件一致)"""
    execute("DELETE FROM replenishment_config")
    for ch in ['jd', 'other']:
        configs = [
            ('lead_time_days', '10'), ('safety_multiplier', '1.5'), ('max_turnover_days', '17'),
            ('turnover_warning_15', '15'), ('turnover_warning_90', '90'),
            ('purchase_lead_days', '14'), ('purchase_safety_days', '3'), ('moq', '50'),
            ('b_to_c_days', '3'), ('c_safety_days', '0'), ('active_factor', '1.0'),
            ('b_free_days', '15'), ('target_turnover', '15'),
            ('mode_bbcc_b_to_c_days', '3'), ('mode_bbcc_c_safety_days', '3'),
            ('mode_bbcc_safety_multiplier', '3'), ('mode_bbcc_ship_to_b_days', '3'),
            ('mode_bbcc_turnover_warning_15', '15'), ('mode_bbcc_turnover_warning_90', '90'),
            ('mode_traditional_lead_time_days', '6'), ('mode_traditional_safety_multiplier', '3'),
            ('mode_traditional_turnover_warning_90', '90'),
        ]
        for k, v in configs:
            execute("INSERT INTO replenishment_config(`key`, value, channel) VALUES(%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE value=VALUES(value)", (k, v, ch))
    execute("DELETE FROM rules")
    rules = [
        ("低库存预警", "inventory.changed",
         '{"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}',
         "low_stock", "低库存预警: {product_name}", "可用 {avail} < 安全线 {safety}", "warning"),
        ("紧急补货", "inventory.changed",
         '{"left":"inv.available_qty","op":"<=","right":"max(1,inv.safety_qty*0.3)"}',
         "replenish", "紧急补货: {product_name}", "可用 {avail}，低于安全线 30%", "error"),
        ("超卖保护", "order.created",
         '{"left":"order.quantity","op":">","right":"inv.available_qty"}',
         "oversell", "超卖告警: {sku}", "订单数量超过可用库存", "error"),
        ("滞销识别", "scheduled.daily",
         '{"left":"inv.days_since_last","op":">","right":"30"}',
         "slow_moving", "滞销: {product_name}", "{days} 天无销售", "warning"),
    ]
    for ch in ['jd', 'other']:
        for name, ev, cond, at, title, desc, sev in rules:
            execute("INSERT INTO rules(name, event, condition_json, alert_type, alert_title, "
                    "alert_desc, severity, is_active, channel) VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s)",
                    (name, ev, cond, at, title, desc, sev, ch))


def _seed_alerts():
    """规则引擎告警(仿原版 _seed_rules): SQL 聚合生成低库存/紧急补货 + 关闭已恢复"""
    closed = 0
    for ch in ('jd', 'other'):
        cur = execute(
            "UPDATE alerts SET status='closed' WHERE source='rules_engine' AND channel=%s "
            "AND status='active' AND alert_type IN ('low_stock','replenish') "
            "AND related_sku IS NOT NULL AND related_sku != '' AND NOT EXISTS ("
            "SELECT 1 FROM inventory i WHERE i.channel=%s AND i.sku=alerts.related_sku "
            "AND i.available_qty < i.safety_qty)", (ch, ch))
        closed += int(cur or 0)
    existing = set()
    for r in query("SELECT alert_type, channel, related_sku, source FROM alerts WHERE status='active'"):
        existing.add((r.get('alert_type'), r.get('channel') or 'jd', r.get('related_sku'),
                      r.get('source') or ''))
    sku_wh = {}
    for r in query("SELECT sku, warehouse_type, available_qty FROM inventory ORDER BY available_qty DESC"):
        if r.get('sku') and r.get('sku') not in sku_wh:
            sku_wh[r.get('sku')] = r.get('warehouse_type') or ''
    inserts = []
    for ch in ['jd', 'other']:
        rows = query(
            "SELECT sku, MAX(product_name) AS name, SUM(available_qty) AS avail, "
            "SUM(in_transit_qty) AS transit, SUM(safety_qty) AS safety "
            "FROM inventory WHERE channel=%s GROUP BY sku", [ch])
        for r in rows:
            sku, name = r.get('sku'), r.get('name') or r.get('sku')
            avail, transit, safety = int(r.get('avail') or 0), int(r.get('transit') or 0), int(r.get('safety') or 0)
            if avail < safety and ('low_stock', ch, sku, 'rules_engine') not in existing:
                existing.add(('low_stock', ch, sku, 'rules_engine'))
                inserts.append(("low_stock", "低库存预警: %s" % name, "可用 %d < 安全线 %d" % (avail, safety),
                                "warning", ch, sku, sku_wh.get(sku, '')))
            if avail <= max(1, int(safety * 0.3)) and (avail + transit) <= safety \
                    and ('replenish', ch, sku, 'rules_engine') not in existing:
                existing.add(('replenish', ch, sku, 'rules_engine'))
                inserts.append(("replenish", "紧急补货: %s" % name,
                                "可用 %d(<安全线30%%), 含在途 %d 仍不足安全线 %d" % (avail, avail + transit, safety),
                                "error", ch, sku, sku_wh.get(sku, '')))
    for i in range(0, len(inserts), 500):
        executemany("INSERT INTO alerts(alert_type, title, description, severity, source, channel, "
                    "related_sku, status, warehouse_type) VALUES(%s,%s,%s,%s,'rules_engine',%s,%s,'active',%s)",
                    inserts[i:i + 200])
    return len(inserts)


def _build_snapshot():
    """日销快照: 从订单聚合最近 90 天(幂等 upsert)"""
    start = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    execute(
        "INSERT INTO daily_sales_snapshot(date, channel, sku, warehouse, order_count) "
        "SELECT DATE(ordered_at), channel, sku, warehouse, SUM(quantity) FROM orders "
        "WHERE order_status IN ('待发货','已发货','已完成','申请退款') "
        "AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s "
        "GROUP BY DATE(ordered_at), channel, sku, warehouse "
        "ON DUPLICATE KEY UPDATE order_count=VALUES(order_count)", [start])
    r = one("SELECT COUNT(*) AS c FROM daily_sales_snapshot") or {}
    return int(r.get('c') or 0)


def prepare_skus():
    """生成 SKU(确定性: random.seed(42), 供 fill/status 各步复用一致)"""
    random.seed(42)
    jd_s = _make_skus('-J', 1000)
    ot_s = _make_skus('-O', 1000)
    return {'jd': jd_s, 'other': ot_s}


def seed_step(step, today, skus_data):
    """执行单个步骤(Makers 异步分步: 每步 ≤90s, 任务表驱动续跑); 返回 (next_step, part_summary)"""
    random.seed(42)  # 确定性: 各步/重复调用生成一致(防重跑数据漂移)
    if step == 0:
        n_prod, n_sup = _seed_products_suppliers(skus_data)
        return 1, {'products': len(n_prod), 'suppliers': len(n_sup)}
    if 1 <= step <= 4:
        day_from = (step - 1) * 15
        n = _seed_orders_range(today, skus_data, day_from, day_from + 15)
        return step + 1, {'orders_part_%d' % step: n}
    if step == 5:
        n = _seed_inventory(skus_data)
        return 6, {'inventory': n}
    if step == 6:
        n = _seed_batches()
        return 7, {'batches': n}
    if step == 7:
        a, b = _seed_records()
        return 8, {'inbound': a, 'outbound': b}
    if step == 8:
        _sync_inv_month()
        _seed_config()
        return 9, {'config': 'ok'}
    if step == 9:
        n_alert = _seed_alerts()
        n_snap = _build_snapshot()
        return 10, {'alerts': n_alert, 'snapshot': n_snap}
    return 10, {}


def run_seed_fill():
    """完整填充(PA 原版逻辑移植); 返回 summary dict"""
    started = time.time()
    random.seed(42)
    today = datetime.now(timezone.utc)
    jd_s = _make_skus('-J', 1000)
    ot_s = _make_skus('-O', 1000)
    skus_data = {'jd': jd_s, 'other': ot_s}
    n_prod, n_sup = _seed_products_suppliers(skus_data)
    n_orders = _seed_orders(today, skus_data)
    n_inv = _seed_inventory(skus_data)
    # 诊断: 落库行数按仓型/订单(排查生成与落库差异)
    inv_counts = {}
    for wt in ('own', 'platform', 'platform_b'):
        r = one("SELECT COUNT(*) AS c FROM inventory WHERE warehouse_type=%s", [wt]) or {}
        inv_counts[wt] = int(r.get('c') or 0)
    r = one("SELECT COUNT(*) AS c FROM orders") or {}
    orders_db = int(r.get('c') or 0)
    n_batch = _seed_batches()
    n_in, n_out = _seed_records()
    _sync_inv_month()
    _seed_config()
    n_alert = _seed_alerts()
    n_snap = _build_snapshot()
    return {'orders': n_orders, 'products': len(n_prod), 'suppliers': len(n_sup),
            'inventory': n_inv, 'inventory_db': inv_counts, 'orders_db': orders_db, 'batches': n_batch,
            'inbound': n_in, 'outbound': n_out, 'alerts': n_alert, 'snapshot': n_snap,
            'elapsed': round(time.time() - started, 1)}