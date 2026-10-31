"""种子数据完整生成器 —— PA 原版 seed.py(790 行)逻辑完整移植(TiDB 原生)

规模与原版一致: jd 1000 SKU/1100 单每天 + other 1000 SKU/550 单每天 × 60 天 ≈ 10 万订单
含: 促销波峰(618 第5-20天/月末45-55天)、周末回落、滞销 3%+低动销 2% SKU、
金额 GMV 明细口径、8 仓库存(18% 低库存)、批次效期(4% 问题批次)、
当月出入库记录、库存月汇总、补货参数+内置规则、规则引擎告警、日销快照
"""
from routes.common import try_err, SALES_SQL
import json
import random

def _rand_hm():
    """仿真时间: 电商活跃时段 8-22 点随机时分秒(秒级仿真, 替代全 0 点)"""
    return "%02d:%02d:%02d" % (random.randint(8, 22), random.randint(0, 59), random.randint(0, 59))

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
        for ch in ['jd', 'other']:  # 演示数据固定双渠道(seed 专用, 不随 channels 表扩展)
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
    # 2026-10-29 修复跨步重复: 原 total 每步重置为 0 → 分步 seed 时 order_no 可能与前一步撞
    #   uq_orders_no_sku(实测 step2 报 1062)。改为带步偏移(day_from × 10 万), 全局不重叠
    total = int(day_from) * 100000
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
                'data_source', 'barcode']
        # 2026-10-29 幂等写入: 曾用普通 INSERT → 任一键冲突则**整批 500 行失败**且任务中断
        #   改为 INSERT IGNORE: 冲突行跳过(seed 演示数据允许少量缺失), 任务可跑完
        executemany("INSERT IGNORE INTO orders(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
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
                                  'sku': sk['sku'], 'barcode': sk.get('barcode', ''), 'product_name': sk['name'], 'quantity': q,
                                  'unit_price': sk['price'], **_a,
                                  'order_status': random.choices(['已完成', '已发货'], [80, 20])[0],
                                  'ordered_at': dt.strftime('%Y-%m-%d ') + _rand_hm(),
                                  'paid_at': dt.strftime('%Y-%m-%d ') + _rand_hm(),
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
                # 2026-10-17 去重键兼容: order_no 全局唯一(原 total%100000 超10万轮回→撞 uq 键)
                # ——改为全渠道累计序号不取模(60天≈9.9万单, 7位够), 且保留渠道/天维度信息
                # 双保险: 序号 + 4 位随机(防多实例/旧代码导致的 total 重复)
                batch.append({'order_no': '%s-%s%03d-%08d%04d' % (label.upper(), ch, d, total,
                                                                   random.randint(0, 9999)),
                              'store': sk['store'], 'warehouse': c_whs[_pi % len(c_whs)],
                              'sku': sk['sku'], 'barcode': sk.get('barcode', ''), 'product_name': sk['name'], 'quantity': q,
                              'unit_price': sk['price'], **_a, 'order_status': st,
                              'ordered_at': dt.strftime('%Y-%m-%d ') + _rand_hm(),
                              'paid_at': paid_dt.strftime('%Y-%m-%d ') + _rand_hm(),
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
                inv.append({'sku': sk['sku'], 'barcode': sk.get('barcode', ''), 'product_name': sk['name'], 'warehouse': wh_name,
                            'warehouse_type': wt, 'available_qty': q,
                            'in_transit_qty': 0 if low else random.randint(0, 200),
                            'safety_qty': 0,  # 静态安全线已退役(2026-10-08): 全链路动态安全线 ds×周期
                            'channel': ch})
    for i in range(0, len(inv), 500):
        cols = ['sku', 'product_name', 'warehouse', 'warehouse_type', 'available_qty',
                'in_transit_qty', 'safety_qty', 'channel', 'barcode']
        executemany("INSERT IGNORE INTO inventory(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
                                                              ", ".join(["%s"] * len(cols))),
                    [tuple(x[c] for c in cols) for x in inv[i:i + 500]])
    return len(inv)


def _seed_batches():
    # 2026-10-29 按 69码: 批次从库存行取码(该 SKU+仓 唯一码时归属明确)
    bc_map = {}
    try:
        for _r in query("SELECT sku, warehouse, MAX(barcode) AS bc FROM inventory "
                        "WHERE COALESCE(barcode,'')!='' GROUP BY sku, warehouse"):
            bc_map[(str(_r.get('sku') or ''), str(_r.get('warehouse') or ''))] = str(_r.get('bc') or '')
    except Exception:
        bc_map = {}
    """批次效期: 按有货库存行 1~3 批, 4% 问题 SKU(过期/临近), 回写 products.best_before

    2026-10-29 幂等化: 原纯 INSERT —— 分步 seed(step6) 与一键 seed 都会调用, 重复执行即翻倍
    (实测 jd/platform 批次 4,894,006 vs 库存 2,445,069 = 精确 2.00 倍)
    现: 先按渠道清空再插入, 保证 Σ批次qty ≡ inventory.available_qty
    """
    from datetime import datetime as _dt
    try:
        execute("DELETE FROM batches WHERE channel IN ('jd','other')")
    except Exception as _e0:
        try_err('seed_fill', '批次清空失败(继续插入可能翻倍)', _e0)
    today = _dt.now(timezone.utc)
    rows = query("SELECT sku, warehouse, warehouse_type, channel, available_qty FROM inventory "  # no-barcode-ok: seed 平台仓批次按 sku×仓
                 "WHERE available_qty > 0 AND warehouse_type NOT IN ('own')")
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
            bdata.append({'sku': sku, 'barcode': str(bc_map.get((sku, wh), '') or ''),
                          'warehouse': wh, 'warehouse_type': wht, 'channel': ch,
                          'prod_date': prod.strftime('%Y-%m-%d'), 'exp_date': exp.strftime('%Y-%m-%d'),
                          'qty': bq})
            if sku not in best_map or exp < best_map[sku]:
                best_map[sku] = exp
    for i in range(0, len(bdata), 500):
        cols = ['sku', 'barcode', 'warehouse', 'warehouse_type', 'channel', 'prod_date', 'exp_date', 'qty']
        executemany("INSERT IGNORE INTO batches(%s) VALUES(%s)" % (", ".join("`%s`" % c for c in cols),
                                                            ", ".join(["%s"] * len(cols))),
                    [tuple(b[c] for c in cols) for b in bdata[i:i + 500]])
    for sku, exp in best_map.items():
        execute("UPDATE products SET best_before=%s WHERE sku=%s AND (best_before='' OR best_before IS NULL)",
                (exp.strftime('%Y-%m-%d'), sku))
    for sku in problem:
        if sku in best_map:
            execute("UPDATE products SET best_before=%s WHERE sku=%s",
                    (best_map[sku].strftime('%Y-%m-%d'), sku))
    # 2026-10-31 own 仓批次: 走生产算法(期初 + 入库 − 出库精确扣减), 与真实导入同一条链路。
    #   原实现按 available_qty 随机拆 → 与 _rebuild_own_batches 两套口径, seed 跑完自洽,
    #   但任何一次真实导入触发重建就被按"空日期流水"重算而塌缩(本次 65 项偏差的成因之一)。
    from routes.cleanse_write import _rebuild_own_batches as _rbb
    for _och in ('jd', 'other'):
        _osk = [str(x.get('sku') or '') for x in query(
            "SELECT DISTINCT sku FROM inventory WHERE channel=%s AND warehouse_type='own'", [_och])]  # no-barcode-ok: SKU 枚举
        _osk = [x for x in _osk if x]
        if _osk:
            _rbb(_och, _osk)
    # best_before 以批次为准(批次 = 期初 + 流水 − 出库, 唯一真源)
    #   2026-10-31: 单条 JOIN UPDATE —— 原逐条 UPDATE 是 2000 次往返(实测线上 200s),
    #   会撑爆 seed 单步 90s 上限; 单条实测 0.3s。
    try:
        execute("UPDATE products p JOIN ("
                "  SELECT sku, MIN(exp_date) AS e FROM batches "
                "  WHERE channel IN ('jd','other') AND COALESCE(exp_date,'')<>'' GROUP BY sku"
                ") b ON b.sku=p.sku SET p.best_before=b.e "
                "WHERE COALESCE(p.best_before,'')<>b.e")
    except Exception as _ebf:
        try_err('seed_fill', 'best_before 回写降级', _ebf)
    return len(bdata)


def _seed_records():
    """当月出入库记录(进销存页展示): 按批次池随机, INSERT IGNORE 防唯一冲突"""
    today = datetime.now(timezone.utc)
    # 2026-10-29 幂等: 先清空该渠道旧出入库记录(原纯 INSERT → 重复执行翻倍,
    #   实测 8061 条入库中 4029 组同 SKU+日期+仓 重复)
    try:
        execute("DELETE FROM inbound_records WHERE channel IN ('jd','other')")
        execute("DELETE FROM outbound_records WHERE channel IN ('jd','other')")
    except Exception as _edr:
        try_err('seed_fill', '出入库记录清理降级', _edr)

    # 2026-10-29 业务定案: 出入库只属于自有仓 → 批次池只取 warehouse_type='own'
    # (原取全部批次 → 生成出"沈阳仓/上海仓/B仓"的虚假出入库记录)
    _batch_pool = {}
    # 2026-10-29 按 69码: 批次池**从 inventory 取码**
    #   原因: seed 顺序为 库存(step5) → 流水(step6) → 批次(step7),
    #   流水生成时 batches 还不存在 → 原"从 batches 取"导致流水 barcode 全空
    # 2026-10-31 修复(批次对账偏差根因): 原 pd/ed 恒为空 → 入库流水 100% 无批次,
    #   批次重建的精确扣减全部落空, 只能靠"缩进 bug 少算"让总量看似接近。
    #   现: 每个 (SKU, 渠道, 仓) 先定 1~3 个批次(prod/exp), 入库按批次落笔,
    #   出库只从"该批次剩余量"里取 → Σ批次 = 期初 + Σ入 − Σ出 恒成立, 且不会卖超。
    for r in query("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, channel FROM inventory "
                   "WHERE warehouse_type='own' AND warehouse!=''"):
        _batch_pool.setdefault((str(r.get('sku') or ''), str(r.get('channel') or 'jd')), []).append(
            {'wh': str(r.get('warehouse') or ''), 'bc': str(r.get('bc') or '')})
    _pcat = {}
    for r in query("SELECT sku, category, channel FROM products"):
        _pcat[(str(r.get('sku') or ''), str(r.get('channel') or 'jd'))] = str(r.get('category') or '')
    _prob = set()
    for _s in sorted({str(x.get('sku') or '') for x in query(  # no-barcode-ok: seed 按 SKU 枚举
            "SELECT DISTINCT sku FROM inventory WHERE warehouse_type='own'")}):
        if _s and random.random() < 0.04:   # 4% 问题批次(过期/临期), 供效期预警演示
            _prob.add(_s)
    _food_kw = ['酱油', '酱', '醋', '油', '酒', '糖', '蜂', '咖', '粉',
                '薯', '坚果', '饼干', '巧克力', '糖果', '麻辣', '椒']
    in_rows, out_rows = [], []
    for sku, sk_name, ch in [(r.get('sku'), r.get('product_name'), r.get('channel'))
                             for r in query("SELECT sku, product_name, channel FROM products")]:
        _ck = (str(sku or ''), str(ch or 'jd'))
        _pool = _batch_pool.get(_ck, [])
        if not _pool:
            continue  # 无自有仓库存行 → 不造 warehouse='' 的脏流水
        # 90 天窗口: 原 max(today.day-1, 6) 让流水全挤在当月 → 周转/库龄/环比全部失真
        max_days = 90
        for _whm in _pool:
            _cat = _pcat.get(_ck, '')
            _shelf = (random.randint(150, 270) if any(k in _cat for k in _food_kw)
                      else random.randint(200, 365))
            _is_prob = str(sku) in _prob
            plan = []
            _used_ago = set()
            for _bi in range(random.randint(1, 3)):
                if _is_prob and _bi == 0 and random.random() < 0.5:
                    _ago = _shelf + random.randint(3, 15)                                   # 已过期
                elif _is_prob and _bi == 0:
                    _ago = random.randint(max(_shelf // 3 - 2, 5), max(_shelf // 3 + 2, 8))  # 临期
                else:
                    _ago = random.randint(2, max(_shelf // 3 - 6, 5))
                # 同 SKU 内生产日必须错开: 撞日会让两条入库共享同一批号,
                # 出库按批号匹配后可能"早于其中一笔入库"(实测 38 个 SKU / 28 条出库)
                for _retry in range(20):
                    if _ago not in _used_ago:
                        break
                    _ago = max(2, _ago - 1)
                _used_ago.add(_ago)
                _prod = today - timedelta(days=_ago)
                plan.append({'pd': _prod.strftime('%Y-%m-%d'),
                             'ed': (_prod + timedelta(days=_shelf)).strftime('%Y-%m-%d'),
                             'left': 0, 'day': 0, 'ago': _ago})
            for _b in plan:                     # 入库: 一批一笔, 日期必须 ≥ 生产日(否则出现"未生产先入库")
                _qty = random.randint(50, 500)
                _db = random.randint(0, min(max_days, _b['ago']))
                _b['left'] = _qty
                _b['day'] = _db
                in_rows.append({'sku': sku, 'product_name': sk_name, 'quantity': _qty,
                                'supplier': '供应商-%s' % str(sku or '')[-3:],
                                'inbound_date': (today - timedelta(days=_db)).strftime('%Y-%m-%d ') + _rand_hm(),
                                'channel': ch, 'prod_date': _b['pd'], 'exp_date': _b['ed'],
                                'warehouse': _whm['wh'], 'barcode': _whm['bc']})
            # 出库: 先定目标出库率(55%~70%), 再按 2~4 笔分摊到批次;
            # 单笔上限 80%(原 60%×最多2笔 → 出库率仅 12%, 库存虚高、补货建议失真)
            _total_in = sum(b['left'] for b in plan)
            _target = int(round(_total_in * random.uniform(0.55, 0.70)))
            _n_out = random.randint(2, 4)
            for _k in range(_n_out + 6):        # 允许补笔: 单批 80% 上限会挡住均分, 剩余量继续摊
                if _target <= 0:
                    break
                _avail = [b for b in plan if b['left'] > 0]
                if not _avail:
                    break
                _b = random.choice(_avail)
                # 出库不早于该批入库(入库又 ≥ 生产日), 整条因果链闭合
                _db = random.randint(0, min(_b['day'], max_days))
                _quota = max(1, _target // max(1, _n_out - min(_k, _n_out - 1)))
                _qty = min(_quota, max(1, int(_b['left'] * 0.8)), _target)
                if _qty <= 0:
                    break
                _b['left'] -= _qty
                _target -= _qty
                out_rows.append({'sku': sku, 'product_name': sk_name, 'quantity': _qty,
                                 'target_warehouse': 'B仓',
                                 'outbound_date': (today - timedelta(days=_db)).strftime('%Y-%m-%d ') + _rand_hm(),
                                 'channel': ch, 'prod_date': _b['pd'], 'exp_date': _b['ed'],
                                 'warehouse': _whm['wh'], 'barcode': _whm['bc']})
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
            except Exception as _e:
                    try_err('seed_fill', '静默降级', _e)
    # 2026-10-29 修复(恒等式 998 项偏差根因): 原此处 `beginning_stock = available_qty`
    #   会在重算(step7)之后再次覆盖期初 → available 被打回期初值, 流水不生效
    #   用户定案: 期初 = 库存导入值(_seed_inventory 已设), **任何后续步骤不得改写**
    #   · 删除 beginning_stock 覆盖
    #   · 删除 month_inbound 负数兜底(同属自动修正, 违反"以导入为准")
    # 2026-10-29 删除全部兜底(用户定案): 期初**以实际导入为准**, 不做任何反推或自动修正
    #   · 原"期初为负则用 month_inbound/outbound 反推" → 已删除(会产生与流水矛盾的数据)
    #   · 原"负期初归 0" → 一并删除(用户明确: 不要兜底; 负数应视为导入数据本身有问题,
    #     由每日对账告警暴露, 而非静默改写)


def _seed_config():
    """补货参数 + 内置规则(原版 4 条, 与实时评估引擎条件一致)"""
    execute("DELETE FROM replenishment_config")
    for ch in ['jd', 'other']:  # 演示数据固定双渠道(seed 专用, 不随 channels 表扩展)
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
         '{"left":"inv.available_qty + inv.in_transit_qty","op":"<","right":"inv.safety_qty"}',
         "low_stock", "低库存预警: {product_name}", "可售 {sellable} < 动态安全线 {safety}", "warning",
         None),
        ("超卖保护", "order.created",
         '{"left":"order.total_qty","op":">","right":"inv.available_qty"}',
         "oversell", "超卖告警: {sku}", "累计订单量超过可用库存", "error",
         None),
        # 濒临断货预警(参数载体): 断货卡计算参数(params 被 stock-risk 同源读取),
        # alert_enabled=0 不生成告警(断货卡已实时承载展示); 用户可在编辑页开启告警开关
        ("濒临断货预警", "scheduled.daily",
         '{"left":"inv.adj_dos","op":"<=","right":"params.lit"}',
         "stockout", "濒临断货: {product_name}", "可售天数 {adj_dos} ≤ 补货周期 {lit}", "error",
         None),
        # 库存健康监控(参数载体): 健康分档参数(params 被 health_index 同源读取), 不告警
        ("库存健康监控", "scheduled.daily",
         '{"left":"health.score","op":"<","right":"params.health_warning"}',
         "health", "健康度预警: {health_score} 分", "库存健康分跌破 {health_warning} 档位线", "warning",
         None),
        # 注: 内置规则演进 —— "紧急补货"(replenish)/"滞销识别"(slow_moving) 退役
        #     (采购&补货卡读建议接口 / 处置建议页承载滞销); stockout/health 保留为参数载体(不告警)
    ]
    for ch in ['jd', 'other']:  # 演示数据固定双渠道(seed 专用, 不随 channels 表扩展)
        for name, ev, cond, at, title, desc, sev, _p in rules:
            if at == "stockout":
                params = {"lit": 3 if ch == 'jd' else 10, "otif_min": 0.6, "ss_z": 1.65,
                          "accel_ratio": 1.3, "accel_min_qty": 10, "buffer_orange": 1.2,
                          "buffer_yellow": 1.0, "orange_slack_days": 1, "include_avail_zero": 1,
                          "log": 0, "alert_enabled": 0}
            elif at == "health":
                params = {"health_good": 85, "health_warning": 60, "log": 0, "alert_enabled": 0}
            else:
                params = None
            execute("INSERT INTO rules(name, event, condition_json, alert_type, alert_title, "
                    "alert_desc, severity, is_active, channel, params) VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s,%s)",
                    (name, ev, cond, at, title, desc, sev, ch,
                     json.dumps(params, ensure_ascii=False) if params else None))


def _seed_alerts():
    """规则引擎告警(仿原版 _seed_rules): 按库存行(SKU×仓)逐仓生成低库存/紧急补货 + 关闭已恢复

    逐仓粒度(与规则引擎 evaluate_many 去重 key 一致: alert_type+sku+warehouse+channel):
    传统多仓下每个仓的库存风险独立成条, 告警弹窗按 warehouse 列显示实际仓名
    """
    closed = 0
    for ch in ('jd', 'other'):
        cur = execute(
            "UPDATE alerts SET status='closed' WHERE source='rules_engine' AND channel=%s "
            "AND status='active' AND alert_type IN ('low_stock','replenish') "
            "AND related_sku IS NOT NULL AND related_sku != '' AND NOT EXISTS ("
            "SELECT 1 FROM inventory i WHERE i.channel=%s AND i.sku=alerts.related_sku "  # no-barcode-ok: 存在性判断(SELECT 1)
            "AND i.warehouse=alerts.warehouse AND i.available_qty < i.safety_qty)", (ch, ch))
        closed += int(cur or 0)
    existing = set()
    for r in query("SELECT alert_type, channel, related_sku, warehouse, source FROM alerts WHERE status='active'"):
        existing.add((r.get('alert_type'), r.get('channel') or 'jd', r.get('related_sku'),
                      r.get('warehouse') or '', r.get('source') or ''))
    inserts = []
    for ch in ['jd', 'other']:  # 演示数据固定双渠道(seed 专用, 不随 channels 表扩展)
        rows = query(
            "SELECT sku, warehouse, warehouse_type, MAX(product_name) AS name, "
            "MAX(available_qty) AS avail, MAX(in_transit_qty) AS transit, MAX(safety_qty) AS safety "
            "FROM inventory WHERE channel=%s GROUP BY sku, warehouse, warehouse_type", [ch])  # no-barcode-ok: seed 告警按 sku×仓生成(演示数据)
        for r in rows:
            sku, name = r.get('sku'), r.get('name') or r.get('sku')
            avail, safety = int(r.get('avail') or 0), int(r.get('safety') or 0)
            wh = str(r.get('warehouse') or '')
            # 仅低库存告警(avail < safety); "紧急补货"(replenish)已退役——
            # 看板采购&补货告警卡改读补货/采购建议接口(动态缺口), 与静态 30% 阈值告警 100% 重叠, 无独立信息价值
            if avail < safety and ('low_stock', ch, sku, wh, 'rules_engine') not in existing:
                existing.add(('low_stock', ch, sku, wh, 'rules_engine'))
                inserts.append(("low_stock", "低库存预警: %s" % name, "可售 %d < 动态安全线 %d" % (avail, safety),
                                "warning", ch, sku, r.get('warehouse_type') or '', wh))
    for i in range(0, len(inserts), 500):
        executemany("INSERT INTO alerts(alert_type, title, description, severity, source, channel, "
                    "related_sku, status, warehouse_type, warehouse) VALUES(%s,%s,%s,%s,'rules_engine',%s,%s,'active',%s,%s)",
                    inserts[i:i + 200])
    return len(inserts)


def _build_snapshot():
    """日销快照: 从订单聚合最近 90 天(幂等 upsert)"""
    start = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    # 2026-10-29 按 69码: 快照主键含 barcode, 写入必须带码(否则按码日销全空)
    execute(
        "INSERT INTO daily_sales_snapshot(date, channel, sku, barcode, warehouse, order_count) "
        "SELECT DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse, SUM(quantity) FROM orders "
        "WHERE order_status IN (" + SALES_SQL + ") "
        "AND (deleted_at IS NULL OR deleted_at='') AND ordered_at >= %s "
        "GROUP BY DATE(ordered_at), channel, sku, COALESCE(barcode,''), warehouse "
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
        # 2026-10-29 顺序修正: 先流水(原 step6 生成批次 → 早于流水, 批次基于旧库存)
        a, b = _seed_records()
        return 7, {'inbound': a, 'outbound': b}
    if step == 7:
        # 重算自有仓(期初+流水) → 再生成批次(保证 Σ批次 = available)
        try:
            from routes.cleanse_write import _recalc_own_inventory as _rc
            for _ch in ('jd', 'other'):
                _sk = [r.get('sku') for r in query("SELECT DISTINCT sku FROM inventory "  # no-barcode-ok: SKU 枚举
                                                   "WHERE channel=%s AND warehouse_type='own'", [_ch])]
                if _sk:
                    _rc(_ch, [x for x in _sk if x])
        except Exception as _e_s7:
            try_err('seed_fill', '分步重算降级', _e_s7)
        n = _seed_batches()
        return 8, {'batches': n}
    if step == 8:
        _sync_inv_month()
        _seed_config()
        return 9, {'config': 'ok'}
    if step == 9:
        n_alert = _seed_alerts()
        n_snap = _build_snapshot()
        # 2026-10-29 补: 日销物化重建(必须跟在快照之后)
        #   补货/采购/断货/健康全部读 sku_sales_stats; 原 seed 不重建 → 物化为空/残缺
        #   放在 step9 内(而非新增 step10): status 端点以 nxt>=10 判定完成, step10 永不执行
        _rb = {}
        try:
            from biz.sales import _rebuild_sku_sales_stats as _rss
            for _ch in ('jd', 'other'):
                _rb[_ch] = _rss(_ch, days=90)   # 2026-10-30 窗口统一 90
        except Exception as _erb:
            try_err('seed_fill', 'seed 物化重建降级', _erb)
            _rb = {'error': str(_erb)[:150]}
        return 10, {'alerts': n_alert, 'snapshot': n_snap, 'sales_stats': _rb}
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
    # 2026-10-29 顺序修正: 流水 → 重算 own available → 再生成批次(批次必须基于最终库存)
    #   原顺序(批次→流水→重算)会让批次基于重算前的旧库存, 与 available 不齐
    n_in, n_out = _seed_records()
    try:
        from routes.cleanse_write import _recalc_own_inventory as _rc
        for _ch in ('jd', 'other'):
            _sk = [r.get('sku') for r in query("SELECT DISTINCT sku FROM inventory "
                                               "WHERE channel=%s AND warehouse_type='own'", [_ch])]
            if _sk:
                _rc(_ch, [x for x in _sk if x])
    except Exception as _e_rec:
        try_err('seed_fill', '自有仓重算降级(seed)', _e_rec)
    n_batch = _seed_batches()
    _sync_inv_month()
    _seed_config()
    n_alert = _seed_alerts()
    n_snap = _build_snapshot()
    return {'orders': n_orders, 'products': len(n_prod), 'suppliers': len(n_sup),
            'inventory': n_inv, 'inventory_db': inv_counts, 'orders_db': orders_db, 'batches': n_batch,
            'inbound': n_in, 'outbound': n_out, 'alerts': n_alert, 'snapshot': n_snap,
            'elapsed': round(time.time() - started, 1)}