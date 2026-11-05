"""种子数据完整生成器 —— PA 原版 seed.py(790 行)逻辑完整移植(TiDB 原生)

规模与原版一致: jd 1000 SKU/1100 单每天 + other 1000 SKU/550 单每天 × 60 天 ≈ 10 万订单
含: 促销波峰(618 第5-20天/月末45-55天)、周末回落、滞销 3%+低动销 2% SKU、
金额 GMV 明细口径、8 仓库存(18% 低库存)、批次效期(4% 问题批次)、
当月出入库记录、库存月汇总、补货参数+内置规则、规则引擎告警、日销快照
"""
from routes.common import try_err, SALES_SQL
from core.alert_lifecycle import close as _close_alerts  # 2026-11-03 D8
import json
import random

def _rand_hm():
    """仿真时间: 电商活跃时段 8-22 点随机时分秒(秒级仿真, 替代全 0 点)"""
    return "%02d:%02d:%02d" % (random.randint(8, 22), random.randint(0, 59), random.randint(0, 59))

import time
from datetime import datetime, timedelta, timezone

from db import query, one, execute, executemany
from core.clock import now_cn

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

# ── 规模与窗口(2026-11-05 用户定案) ─────────────────────────────────────
# 每渠道 10000 SKU / 约 21 万单 / 90 天。集中在这里, 免得散落在各函数里对不上
# (历史上 `_seed_orders(today,...)` 写死 60 天, 而流水生成写死 90 天 —— 两套窗口,
#  导致"订单只有 60 天、流水 90 天", 周转与环比口径永远差一截)。
SKU_PER_CHANNEL = 10000
ORDER_DAYS = 90
# 单渠道每天基础单量: 90 天 × 1550 ≈ 13.95 万, 叠加促销日(×2~4, 25 天)与周末(×0.6~1.2)
#   → 实测 5 次抽样落在 20.4 万 ~ 21.6 万, 中位 21.1 万(用户口径 "21 万上下")。
#   注: 曾估 2333(按"90×2333≈21 万"直算), 但促销/周末系数是**在基数上再乘**的,
#   实测跑出 32 万 —— 用户给的 21 万是含促销波峰后的总量, 不是基数。基数必须反推。
ORDER_BASE_PER_DAY = 1550
BC2_RATIO = 0.01          # 多码 SKU 占比(每渠道约 100 个)



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


def _make_skus(sfx, count=1000, bc2_ratio=0.01):
    """SKU 生成器: SKU-{i:05d}{sfx}

    2026-11-05 规模调整(用户定案: 每渠道约 21 万单 / 10000 SKU / 90 天):
      · 编号位宽 4 → 5: 原 '%04d' 到 9999 就撞名, 10000 个 SKU 必须 5 位;
      · 多码样本从"前 3 个"改成**同渠道约 1%**(bc2_ratio): 固定 3 个在大规模下等于没有,
        多码链路(按码拆库存行/按码日销/补码规则)要靠足量样本才验得出来；
      · 第二码用 691 前缀而非 690, 保证不与主码段重叠。
    """
    r = []
    _n_bc2 = max(1, int(count * bc2_ratio))
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
        r.append({'sku': 'SKU-%05d%s' % (i, sfx), 'name': '%s%d' % (c, i), 'store': s, 'cat': c,
                  'price': p, 'box': random.choice([6, 12, 24]),
                  'barcode': '690%010d' % i, 'weight': round(random.uniform(5, 25), 1),
                  'volume': round(random.uniform(0.02, 0.12), 3),
                  'status': 'active', 'brand': _pick_brand(c),
                  # 2026-11-01 多码样本: 换包装/换供应商导致的条码变更 —— 没有这组数据,
                  #   所有"按 69码聚合"的修复都只能在单码上验证 = 等于没验证
                  # 2026-11-05: 固定前 3 个 → 按比例(同渠道约 1%, 10000 SKU 即约 100 个)
                  'bc2': ('691%010d' % i) if i <= _n_bc2 else ''})
    return r


def _bc_of(sk):
    """订单取码(2026-11-01 多码样本): 多码 SKU 按 35% 概率出第二码

    现实对应: 同一 SKU 换包装/换供应商后条码变更, 新码订单与旧码订单会并存一段时间。
    订单带哪个码就该进哪个码的日销切片 —— 这正是 sku_sales_stats 按码行的来源。
    """
    if not isinstance(sk, dict):
        return ""
    _b2 = sk.get('bc2') or ''
    if _b2 and random.random() < 0.35:
        return _b2
    return sk.get('barcode', '')


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
                              'brand': _brand, 'unit': '瓶',
                              # 2026-11-01 是否做效期管理: 家清/日化(home)不做, 食品/零食做
                              #   —— 让两种扣减口径(FEFO / 到货序 FIFO)都有真实数据可验证
                              'expiry_managed': 'no' if _g == 'home' else 'yes'})
    # 2026-11-05 走导入链路(用户定案): 商品档案 i 也是"导入文件"进来的一份数据。
    #   原来直接 INSERT ... ON DUPLICATE, 于是"映射了供应商编码但写入白名单漏了它"
    #   这类漏列问题在演示环境永远暴露不出来(§15.158 就是这么发现的)。
    from routes.seed_import import import_rows as _imp
    for ch in ('jd', 'other'):
        _prow = [p for p in prod_rows if p.get('channel') == ch]
        if _prow:
            _r = _imp('product', ch, _prow, label='商品档案')
            if _r.get('failed'):
                try_err('seed_fill', '商品档案导入 %s 有 %d 行未落库(首因=%s)'
                        % (ch, _r['failed'],
                           str((_r.get('errors') or [{}])[0].get('reason') or '')[:120]), None)
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
    return _seed_orders_range(today, skus_data, 0, ORDER_DAYS)


def _seed_orders_range(today, skus_data, day_from, day_to):
    """按天范围生成订单(day_from <= day_offset < day_to)并写入(异步分步执行用)

    随机池预生成(统计等价, 消除 18 万次 random 调用); 500/批写入(TiDB serverless 单条 INSERT 上限)"""
    global _DEMO_SLOW, _DEMO_LOW
    if day_from == 0:
        _DEMO_SLOW, _DEMO_LOW = set(), set()
    # 2026-10-29 修复跨步重复: 原 total 每步重置为 0 → 分步 seed 时 order_no 可能与前一步撞
    #   uq_orders_no_sku(实测 step2 报 1062)。改为带步偏移, 全局不重叠
    # 2026-11-05 偏移量 10 万 → 100 万: 单段(15 天)已约 3.5 万单, 双渠道 7 万;
    #   原 10 万偏移在多实例/重跑叠加时余量太薄, 而 order_no 唯一是硬约束(撞了就是整批 1062)
    total = int(day_from) * 1000000
    # 2026-11-05 修: 返回值此前是**全局累计序号 total**(含 day_from*1000000 的步偏移),
    #   不是本段新增行数 —— 水位对账第一次跑就把它抓了出来:
    #   第 2 段报"预期 15,072,115 / 实际 72,115"(差正好 1500 万 = 偏移量本身)。
    #   旧代码拿这个值当"生成了多少单"用(step summary 展示), 一直显示的是天文数字,
    #   只是没人核对过。这里显式累加本段实际 flush 的行数。
    _seg_rows = 0
    batch = []

    def _amt(q, price, disc_r, freight_r, sub_r):
        base = round(q * price, 2)
        disc = round(base * disc_r, 2)
        return {'total_amount': base, 'discount_amount': round(disc, 2),
                'freight_amount': freight_r, 'subsidy_amount': round(sub_r, 2), 'tax_amount': 0.0,
                'actual_amount': round(base - disc - sub_r + freight_r, 2)}

    def flush():
        nonlocal batch, _seg_rows
        if not batch:
            return
        # 2026-11-05 走导入链路(用户定案: 演示数据必须由导入代码路径自己产出):
        #   原先是 executemany("INSERT IGNORE INTO orders ...") —— 那等于绕过导入:
        #   不补 69码(档案有码/本行缺码的规则)、不做四维业务键对齐、不走批内聚合、
        #   不登记脏日重建、不失效分析缓存。演示库"看着对", 真实导入却可能错, 没人能提前发现。
        #   现在: 内存行 → 中文表头 CSV → _parse_table → _clean_rows → _write_rows(同一分派器)。
        #   batch 仍是 500 行一组(控制单条 INSERT 体积, 与导入侧 500/批同源)。
        from routes.seed_import import import_rows as _imp
        _r = _imp('order', ch_cur[0], batch, label='订单')
        _seg_rows += int(_r.get('rows') or 0)
        if _r.get('failed'):
            try_err('seed_fill', '订单导入链路 %s 段有 %d 行未落库(已留痕, 不中断): 首因=%s'
                    % (ch_cur[0], _r['failed'],
                       str((_r.get('errors') or [{}])[0].get('reason') or '')[:120]), None)
        batch = []

    ch_cur = ['jd']   # flush() 需要知道当前渠道(闭包外变量, 避免每批多传一个参数)

    for ch, label, skus, base in [('jd', 'jd', skus_data['jd'], ORDER_BASE_PER_DAY),
                                  ('other', 'other', skus_data['other'], ORDER_BASE_PER_DAY)]:
        ch_cur[0] = ch
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
                                  'sku': sk['sku'], 'barcode': _bc_of(sk), 'product_name': sk['name'], 'quantity': q,
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
                              'sku': sk['sku'], 'barcode': _bc_of(sk), 'product_name': sk['name'], 'quantity': q,
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
    # 返回**本段实际写入行数**(非全局序号; 历史调用方拿它当"生成了多少单"展示, 见上方注释)
    return _seg_rows


def _seed_inventory(skus_data, channel='', wh_group=''):
    """库存快照生成 + **按导入文件落库**

    channel / wh_group (2026-11-05 线上实测逼出来的): 两渠道一次导入实测 **362s** —— 这是
      整链最重的一步, 90s(平台建议) 与 120s(网关硬上限) 两条线一起踩穿。拆成
      「渠道 × 仓型」4 段后, 单段最大的是自有仓(约 1 万行 + 触发重算)与平台仓(约 7~8 万行),
      各自回到单请求能承受的量级。
      不传 = 两渠道两仓型全做(一键 seed / 运维口的老行为, 语义不变)。

    ⚠ 拆分的安全性来自**幂等**: 库存是快照, 同键全列覆盖(uq_inventory_biz), 分几段写、
      写几遍, 结果都只取决于最后写入的那份值。所以这里不需要"先清再写"。
    """
    inv = []
    _chs = (channel,) if channel else tuple(skus_data.keys())
    for ch in _chs:
        skus = skus_data.get(ch) or []
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
                # 2026-11-04 真实场景(用户定案): 期初 = **月初结存**, 量级应与当前可用接近 ——
                #   当月才过几天, 库存不会凭空变成 3 倍。原 20%~40% 让"期初 2 件、当月出 68 件"
                #   这种自相矛盾的行占到 15%(负库存), 不符合任何真实公司。
                #   低库存行按 q 折得更低(缺货本色), 但至少 1 件 —— 0 期初 + 0 入库 = 永远发不出货
                if wt == 'own':
                    _beg_r = random.uniform(0.60, 1.00) if low else random.uniform(0.75, 1.05)
                    beg = max(1, int(q * _beg_r))
                else:
                    beg = 0
                # 2026-11-01 多码: own 仓按 65/35 拆成两行(同 SKU 同仓两个 69码)
                #   inventory 本就按 (sku,barcode,warehouse,warehouse_type) 拆行 —— 拆行不是新概念,
                #   是让"同 SKU 多码"这一真实形态在 seed 里存在, 否则按码聚合的修复无从验证。
                #   拆分只发生在 own(出入库流水只属于自有仓), 平台仓仍单码。
                _lines = [{'bc': sk.get('barcode', ''), 'q': q, 'beg': beg}]
                if wt == 'own' and sk.get('bc2'):
                    _q1 = int(round(q * 0.65))
                    _b1 = int(round(beg * 0.65))
                    _lines = [{'bc': sk['barcode'], 'q': _q1, 'beg': _b1},
                              {'bc': sk['bc2'], 'q': q - _q1, 'beg': beg - _b1}]
                for _ln in _lines:
                    inv.append({'sku': sk['sku'], 'barcode': _ln['bc'], 'product_name': sk['name'],
                                'store': sk.get('store', ''),
                                'warehouse': wh_name,
                                'warehouse_type': wt, 'available_qty': _ln['q'],
                                'beginning_stock': _ln['beg'],
                                'in_transit_qty': 0 if low else random.randint(0, 200),
                                'safety_qty': 0,  # 静态安全线已退役(2026-10-08): 全链路动态安全线 ds×周期
                                'locked_qty': 0,
                                'c_transit': 0,
                                'month_inbound': 0,   # 由 _sync_inv_month 按流水回填
                                'month_outbound': 0,
                                'week_inbound': 0,    # 本周口径同样由流水回填(周一至今)
                                'week_outbound': 0,
                                'weight': sk.get('weight', 0),
                                'volume': sk.get('volume', 0),
                                'snapshot_qty': _ln['q'],  # 对账基准: 导入文件里的"当时可用"
                                'channel': ch})
    # 2026-11-05 走导入链路(用户定案): 库存快照按**仓型**拆成两份"导入文件" ——
    #   自有仓走 inventory 模板(带期初/本月/本周/快照), 平台仓(B 仓)走 inventory_b 模板
    #   (平台导出只有可用/在途, 没有期初概念)。这正是真实业务里两个不同的上传入口。
    #   走导入的直接收益: 69码归码规则、未归码拒写、期初实盘校准(core.ledger_adj)、
    #   自有仓可用重算与批次 FIFO 重建, 全部由链路自己完成 —— 不必在 seed 里各写一遍。
    from routes.seed_import import import_rows as _imp
    for ch in _chs:
        _own = [x for x in inv if x.get('channel') == ch and x.get('warehouse_type') == 'own']
        _pf = [x for x in inv if x.get('channel') == ch and x.get('warehouse_type') != 'own']
        # 只做该段要的仓型: 否则"拆段"只是把同一个大请求发四遍
        if wh_group == 'platform':
            _own = []
        elif wh_group == 'own':
            _pf = []
        if _own:
            # baseline=True(仅 seed): 无视实盘窗口, 按导入值建本期期初。
            #   不加这个, jd 已结过 10 月账 → 走「已建账分支」把期初挪成 snapshot_qty,
            #   而 apply_import 因"今天在窗口外"(实盘窗口=上月末~当月3号)直接 return,
            #   beginning_stock 无人写回 → 10,100 行期初全 0, 落账核对 1 万行不平。
            #   真实客户的入口(cleanse_write 的导入请求)不传 baseline, 行为不变。
            _r = _imp('inventory', ch, _own, label='自有仓库存', baseline=True)
            if _r.get('failed'):
                try_err('seed_fill', '自有仓库存导入 %s 有 %d 行未落库(首因=%s)'
                        % (ch, _r['failed'],
                           str((_r.get('errors') or [{}])[0].get('reason') or '')[:150]), None)
        if _pf:
            _r = _imp('inventory_b', ch, _pf, label='平台仓库存')
            if _r.get('failed'):
                try_err('seed_fill', '平台仓库存导入 %s 有 %d 行未落库(首因=%s)'
                        % (ch, _r['failed'],
                           str((_r.get('errors') or [{}])[0].get('reason') or '')[:150]), None)
    return len(inv)


def _seed_batches(skus_data=None, channel='', wh_group=''):
    """批次效期: 按有货库存行 1~3 批, 4% 问题 SKU(过期/临近), 回写 products.best_before

    2026-10-29 幂等化: 原纯 INSERT —— 分步 seed(step6) 与一键 seed 都会调用, 重复执行即翻倍
    (实测 jd/platform 批次 4,894,006 vs 库存 2,445,069 = 精确 2.00 倍)
    现: 先按渠道清空再插入, 保证 Σ批次qty ≡ inventory.available_qty

    channel (2026-11-05 拆段): 只重建**该渠道**的批次。原实现一条 DELETE 清两渠道再全量重建,
      两渠道一起做实测 50s+; 拆开后每渠道 ≤22s。⚠ 清空范围必须与重建范围一致 ——
      若只清 jd 却重建两渠道,jd 会双份、other 会三份,这是本函数最怕的错。

    wh_group (2026-11-05 二次拆段): 'platform' = 只做平台仓/B 仓(非 own)批次;
      'own' = 只做自有仓批次。留空 = 两者都做(一键 seed 的老行为)。
      **实测数据量决定的**: jd 渠道 `platform` 仓 68,322 行 + `platform_b` 9,637 行,
      每行 1~3 批 ≈ 16.8 万批次, 单请求必爆 120s 网关(实测 504)。按仓型切开后
      单段约 8 万批次, 稳在 60s 内。仓储口径没有任何变化, 只是执行被分段。
    """
    _chs = (channel,) if channel else ('jd', 'other')
    _only_own = wh_group == 'own'
    _only_pf = wh_group == 'platform'
    # 2026-10-29 按 69码: 批次从库存行取码(该 SKU+仓 唯一码时归属明确)
    bc_map = {}
    try:
        for _r in query("SELECT sku, warehouse, MAX(barcode) AS bc FROM inventory "
                        "WHERE COALESCE(barcode,'')!='' AND channel IN (%s) GROUP BY sku, warehouse"
                        % ",".join(["%s"] * len(_chs)), list(_chs)):
            bc_map[(str(_r.get('sku') or ''), str(_r.get('warehouse') or ''))] = str(_r.get('bc') or '')
    except Exception:
        bc_map = {}
    from datetime import datetime as _dt
    # 清空范围: 与本次重建范围严格一致(渠道 × 仓型)。分三段写清楚, 不用拼接 trick ——
    #   这个 DELETE 一旦宽了就是"删了不重建"(数据丢失), 窄了就是"没删就插"(双份)。
    _ph = ",".join(["%s"] * len(_chs))
    if _only_own:
        _del_sql = "DELETE FROM batches WHERE channel IN (%s) AND warehouse_type='own'" % _ph
        _del_args = list(_chs)
    elif _only_pf:
        _del_sql = ("DELETE FROM batches WHERE channel IN (%s) "
                    "AND warehouse_type<>'own'" % _ph)
        _del_args = list(_chs)
    else:
        _del_sql = "DELETE FROM batches WHERE channel IN (%s)" % _ph
        _del_args = list(_chs)
    try:
        execute(_del_sql, _del_args)
    except Exception as _e0:
        try_err('seed_fill', '批次清空失败(继续插入可能翻倍)', _e0)
    today = now_cn()
    # ⚠ 2026-11-05 拆段配套: rows 必须与 DELETE 同范围。原实现无 channel 条件(两渠道一起重建
    #   没问题), 拆段后若不清不楚 —— 只清 jd 却按全表重建 → jd 双份、other 三份。
    # _only_own 段不产生平台仓批次 → 连查询都不发(这不是优化, 是"只做 own"的语义本身)
    if _only_own:
        rows = []
    else:
        _pf_cond = "AND warehouse_type<>'own' " if _only_pf else ""
        rows = query("SELECT sku, warehouse, warehouse_type, channel, available_qty FROM inventory "  # no-barcode-ok: seed 平台仓批次按 sku×仓
                     "WHERE available_qty > 0 " + _pf_cond +
                     "AND channel IN (%s)" % _ph, list(_chs))
    _pcat = {}
    _pem = {}
    for r in query("SELECT sku, category, channel, COALESCE(expiry_managed,'') AS expiry_managed "
                   "FROM products WHERE channel IN (%s)" % ",".join(["%s"] * len(_chs)), list(_chs)):
        _pcat[(str(r.get('sku')), str(r.get('channel') or 'jd'))] = str(r.get('category') or '')
        _pem[(str(r.get('sku')), str(r.get('channel') or 'jd'))] = str(r.get('expiry_managed') or '')
    problem = set()
    _all = sorted({(str(r.get('sku')), str(r.get('channel') or 'jd')) for r in rows})
    for idx, (s, c) in enumerate(_all):
        chk = random.random()
        if chk < 0.02:
            problem.add(s)
        elif chk < 0.04:
            problem.add(s)
    bdata = []
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
            _no_exp = _pem.get((sku, ch)) == 'no'
        bdata.append({'sku': sku, 'barcode': str(bc_map.get((sku, wh), '') or ''),
                      'warehouse': wh, 'warehouse_type': wht, 'channel': ch,
                      # 不做效期管理 → 批次无日期(效期预警/临期建议自动跳过)
                      'prod_date': '' if _no_exp else prod.strftime('%Y-%m-%d'),
                      'exp_date': '' if _no_exp else exp.strftime('%Y-%m-%d'),
                      'qty': bq})
    # 2026-11-05 批次落库也走导入写入层(不再是裸 INSERT IGNORE):
    #   批次是"Σ批次 ≡ 可用"的另一半, 它和库存/流水必须用同一套冲突语义才对得上。
    #   用 overwrite(同键全列覆盖)而不是 IGNORE —— 平台仓批次按 (sku,仓,仓型,渠道,生产/到期日)
    #   落, 重灌时希望结果是"文件即权威", 而不是"旧行留着、新行被忽略"。
    _conflict = ("`barcode`=VALUES(`barcode`), `qty`=VALUES(`qty`), "
                 "`prod_date`=VALUES(`prod_date`), `exp_date`=VALUES(`exp_date`)")
    if bdata:
        from routes.cleanse_write import _write_batch as _wb
        _bs, _bf, _be = _wb(
            "batches",
            ["sku", "barcode", "warehouse", "warehouse_type", "channel", "prod_date", "exp_date", "qty"],
            bdata, "overwrite", "sku", "warehouse", "prod_date", "exp_date",
            channel=(channel or "jd"), conflict_sql=_conflict)
        if _bf:
            try_err('seed_fill', '批次写入 %d 行被拒(首因=%s)'
                    % (_bf, str((_be or [{}])[0].get('reason') or '')[:120]), None)
    # 2026-11-01: 此处原有两轮逐条 UPDATE products(先填 best_before, 再强制覆盖 problem SKU),
    #   每个 SKU 一次往返 —— 2000 SKU 约 4000 次 RTT, 本地实测把 step7 拖到 8 分钟, 线上必爆
    #   120s 网关。该效果已被下方"批次为准"的单条 JOIN UPDATE 完全覆盖(条件更宽、口径更权威):
    #   批次 exp_date 就是这里生成的 exp, MIN(exp_date) 即本轮 best_map 的最小值。故删除。
    # 2026-10-31 own 仓批次: 走生产算法(期初 + 入库 − 出库精确扣减), 与真实导入同一条链路。
    #   原实现按 available_qty 随机拆 → 与 _rebuild_own_batches 两套口径, seed 跑完自洽,
    #   但任何一次真实导入触发重建就被按"空日期流水"重算而塌缩(本次 65 项偏差的成因之一)。
    # 2026-11-05 二次拆段: 只做 'platform' 组时跳过 own 批次(它自成一整段, 见 _seed_settle_own*)
    from routes.cleanse_write import _rebuild_own_batches as _rbb
    for _och in (() if _only_pf else _chs):
        # 2026-11-05: 有内存 SKU 集合就用它(省一次 3.1s 的 DISTINCT); 没有才查库(/seed/step 单跑)
        if skus_data and skus_data.get(_och):
            _osk = [str(x.get('sku') or '') for x in skus_data[_och]]
        else:
            _osk = [str(x.get('sku') or '') for x in query(
                "SELECT DISTINCT sku FROM inventory WHERE channel=%s AND warehouse_type='own'", [_och])]  # no-barcode-ok: SKU 枚举
        _osk = [x for x in _osk if x]
        if _osk:
            _rbb(_och, _osk)
    # best_before 以批次为准(批次 = 期初 + 流水 − 出库, 唯一真源)
    #   2026-10-31: 单条 JOIN UPDATE —— 原逐条 UPDATE 是 2000 次往返(实测线上 200s),
    #   会撑爆 seed 单步 90s 上限; 单条实测 0.3s。
    # 2026-11-05 二次拆段: **只在最后一段回写**。MIN(exp_date) 跨全部仓型聚合, 只做平台仓
    #   那一半时批次还没齐, 早写会把 best_before 写成偏晚的错值(临期预警整体推迟)。
    if _only_pf:
        return len(bdata)
    try:
        execute("UPDATE products p JOIN ("
                "  SELECT sku, MIN(exp_date) AS e FROM batches "
                "  WHERE channel IN (%s) AND COALESCE(exp_date,'')<>'' GROUP BY sku"
                ") b ON b.sku=p.sku SET p.best_before=b.e "
                "WHERE COALESCE(p.expiry_managed,'')<>'no' AND COALESCE(p.best_before,'')<>b.e"
                % ",".join(["%s"] * len(_chs)), list(_chs))
        # 不做效期管理的 SKU: 清空 best_before(否则旧值残留, 临期建议会误判)
        execute("UPDATE products SET best_before='' WHERE COALESCE(expiry_managed,'')='no' "
                "AND COALESCE(best_before,'')<>'' AND channel IN (%s)"
                % ",".join(["%s"] * len(_chs)), list(_chs))
    except Exception as _ebf:
        try_err('seed_fill', 'best_before 回写降级', _ebf)
    return len(bdata)


def _seed_records(channel='', flow_type=''):
    """当月出入库记录(进销存页展示): 按批次池随机, 走导入链路落库

    一段一文件(2026-11-05): 给了 channel + flow_type = 只处理这一份导入文件,
      只删/只写自己那张表那个渠道; 两者都不给 = 全量(一键 seed / 运维口 / 本地回归
      的老行为, 语义不变)。四种 (渠道, 类型) 组合互不重叠, 重跑任一段不会写重数据。

    幂等为什么靠「按渠道删本表」而不是 ON DUPLICATE: inbound_records / outbound_records
      **没有唯一键**(schema_create 只给了 idx_ib_sku_date), ON DUPLICATE 对它们不成立,
      重复导入必然翻倍。原实现无条件 DELETE 两渠道两张全表 —— 每次重跑都把线上数据
      清空, 页面读到中间态、聚合接口随之 38~40s 超时; 现改为只清自己那一片。
    """
    today = now_cn()

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
    for r in query("SELECT sku, COALESCE(barcode,'') AS bc, warehouse, channel, "
                   "COALESCE(beginning_stock,0) AS beg FROM inventory "
                   "WHERE warehouse_type='own' AND warehouse!=''"):
        _batch_pool.setdefault((str(r.get('sku') or ''), str(r.get('channel') or 'jd')), []).append(
            {'wh': str(r.get('warehouse') or ''), 'bc': str(r.get('bc') or ''),
             # 2026-11-04 期初参与出库约束: 真实场景「当月出库 ≤ 月初结存 + 当月入库」,
             #   原实现只按批次剩余量限制(全期口径) → 当月出库能超过期初, 出现期初 2 件出 68 件
             'beg': int(r.get('beg') or 0)})
    _pcat = {}
    for r in query("SELECT sku, category, channel FROM products"):
        _pcat[(str(r.get('sku') or ''), str(r.get('channel') or 'jd'))] = str(r.get('category') or '')
    _prob = set()
    for _s in sorted({str(x.get('sku') or '') for x in query(  # no-barcode-ok: seed 按 SKU 枚举
            "SELECT DISTINCT sku FROM inventory WHERE warehouse_type='own'")}):
        if _s and random.random() < 0.04:   # 4% 问题批次(过期/临期), 供效期预警演示
            _prob.add(_s)
    # 2026-11-04 滞销判定改按**真实订单量**, 不再依赖内存里的 _DEMO_SLOW:
    #   那两个集合由 _seed_orders_range 填充(step2/3), 而 /seed/step 单跑 step6 时是空集
    #   → 滞销 SKU 会被当成正常动销(流水入库 500 出库 450), 与"90 天没几单"的订单自相矛盾。
    #   订单量 ≤ 15 件/90天 = 滞销或低动销, 出库流水也该几乎不动。
    #
    # 2026-11-05 RU 改造: 这条 GROUP BY sku HAVING SUM(quantity)<=15 实测 **422.69 RU**
    #   (18 万行订单扫 + 分组)。它只为拿 970 个 SKU 的标记, 却按订单行数计价 ——
    #   21 万单时代更贵, 而 seed 一次重灌跑很多步, 单这一条就抵得上重算可用的全部开销。
    #   改为**内存判定**: 标记由 _seed_orders_range 生成时记入 _DEMO_SLOW;
    #   只有内存集合为空(/seed/step 单跑 step6)时才退回查库, 且按渠道分别查(走 channel 索引)。
    _slow_db = set(_DEMO_SLOW)
    if not _slow_db:
        for _sc in ('jd', 'other'):
            try:
                _slow_db |= {str(r.get('sku') or '') for r in query(
                    "SELECT sku FROM orders WHERE channel=%s AND data_source='seed' "
                    "AND (deleted_at IS NULL OR deleted_at='') "
                    "GROUP BY sku HAVING SUM(quantity) <= 15", [_sc])}
            except Exception as _esl:
                try_err('seed_fill', '滞销判定降级(用内存标记)', _esl)
    _food_kw = ['酱油', '酱', '醋', '油', '酒', '糖', '蜂', '咖', '粉',
                '薯', '坚果', '饼干', '巧克力', '糖果', '麻辣', '椒']
    in_rows, out_rows = [], []
    # 2026-11-01 按是否做效期管理分流: 'no' 的 SKU 流水不写 prod_date/exp_date,
    #   批次重建走"到货序 FIFO", 效期预警/临期建议自然跳过(无日期)
    for sku, sk_name, ch, _em in [(r.get('sku'), r.get('product_name'), r.get('channel'),
                                   str(r.get('expiry_managed') or 'yes'))
                                  for r in query("SELECT sku, product_name, channel, "
                                                 "COALESCE(expiry_managed,'') AS expiry_managed "
                                                 "FROM products")]:
        _ck = (str(sku or ''), str(ch or 'jd'))
        _pool = _batch_pool.get(_ck, [])
        if not _pool:
            continue  # 无自有仓库存行 → 不造 warehouse='' 的脏流水
        # 90 天窗口: 原 max(today.day-1, 6) 让流水全挤在当月 → 周转/库龄/环比全部失真
        max_days = 90
        _mtd_days = max(1, today.day)   # 当月已过天数: 日期偏移 < 该值即落在本月
        for _whm in _pool:
            # 2026-11-04 当月额度(逐库存行): 期初 + 当月已入 − 当月已出
            #   出库生成时用它做上限, 保证「当月出库 ≤ 可售」, 负库存只留 1% 超卖样本
            _beg = _whm['beg']
            _mtd_in, _mtd_out = 0, 0
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
                if _db < _mtd_days:             # 当月入库 → 计入当月可售额度
                    _mtd_in += _qty
                in_rows.append({'sku': sku, 'product_name': sk_name, 'quantity': _qty,
                                'supplier': '供应商-%s' % str(sku or '')[-3:],
                                'inbound_date': (today - timedelta(days=_db)).strftime('%Y-%m-%d ') + _rand_hm(),
                                'channel': ch, 'warehouse': _whm['wh'], 'barcode': _whm['bc'],
                                # 2026-11-01 流水补仓型(原为空串): _sync_inv_month 按真实仓型
                                #   聚合, 不再靠"UPDATE 写死 warehouse_type='own'"掩盖空值
                                'warehouse_type': 'own',
                                # 不做效期管理 → 不落批次日期(重建走 FIFO, 不产生批次告警)
                                'prod_date': '' if _em == 'no' else _b['pd'],
                                'exp_date': '' if _em == 'no' else _b['ed']})
            # 出库: 先按场景定出库率, 再按 4~8 笔分摊到批次;
            # 单笔上限 80%(原 60%×最多2笔 → 出库率仅 12%, 库存虚高、补货建议失真)
            _total_in = sum(b['left'] for b in plan)
            # 2026-11-04 出库率按场景(用户定案: 低库存/滞销/日常表现都要保留):
            #   · 滞销(3% SKU): 几乎不动销, 出库率 2%~10% → 页面"未出库/高库龄"
            #   · 低库存: 到货即卖光, 96%~100% → 可用贴着 0, 补货频繁
            #   · 日常销售: 80%~95%, 进销接近平衡 → 期末≈期初≈可用(库存平稳, 真实公司形态)
            #   原一律 55%~70% 会让 90 天净流入 300+, 期初与可用差出数倍, 月底一对账就穿帮
            if str(sku) in _slow_db:
                _out_rate = random.uniform(0.02, 0.10)
            elif _beg <= 10:
                _out_rate = random.uniform(0.96, 1.00)
            else:
                _out_rate = random.uniform(0.80, 0.95)
            _target = int(round(_total_in * _out_rate))
            # 2026-11-04 真实场景调整(用户定案):
            #   · 日常销售节奏: 出库笔数 3~6, 前 2 笔强制落在近 3 天 → 当月有出库,
            #     不再出现"60% 的 SKU 当月一笔没出"(那不是缺货, 是流水撒得太稀)
            #   · 不卖超: 当月出库 ≤ (期初 + 当月入库) × 95%, 超出部分顺延到上月
            #     → 期末为负从 15% 降到 ~1%(保留极少数超卖样本, 让"负库存异常"标记仍可验证)
            _n_out = random.randint(4, 8)
            _cap_mtd = _beg + _mtd_in        # 当月可售上限
            _oversell = random.random() < 0.01
            for _k in range(_n_out + 12):        # 允许补笔: 单批 80% 上限会挡住均分, 剩余量继续摊
                if _target <= 0:
                    break
                _avail = [b for b in plan if b['left'] > 0]
                if not _avail:
                    break
                _b = random.choice(_avail)
                # 出库不早于该批入库(入库又 ≥ 生产日), 整条因果链闭合
                _db = random.randint(0, min(_b['day'], max_days))
                if _k < 2 and _b['day'] >= 2:   # 前两笔: 近 3 天(日常销售)
                    _db = random.randint(0, min(2, _b['day']))
                _quota = max(1, _target // max(1, _n_out - min(_k, _n_out - 1)))
                _qty = min(_quota, max(1, int(_b['left'] * 0.8)), _target)
                if _db < _mtd_days and not _oversell:
                    _room = int((_cap_mtd - _mtd_out) * 0.95)
                    if _room <= 0:
                        if _b['day'] > _mtd_days:
                            _db = random.randint(_mtd_days, _b['day'])   # 顺延到上月
                        else:
                            break          # 当月无额度, 且这批也是当月货 → 本月不再出
                    else:
                        _qty = min(_qty, _room)
                if _qty <= 0:
                    break
                _b['left'] -= _qty
                _target -= _qty
                if _db < _mtd_days:
                    _mtd_out += _qty
                out_rows.append({'sku': sku, 'product_name': sk_name, 'quantity': _qty,
                                 'target_warehouse': 'B仓',
                                 'outbound_date': (today - timedelta(days=_db)).strftime('%Y-%m-%d ') + _rand_hm(),
                                 'channel': ch, 'warehouse': _whm['wh'], 'barcode': _whm['bc'],
                                 # 2026-11-01 同入库: 流水落真实仓型, 供 _sync_inv_month 按码聚合
                                 'warehouse_type': 'own',
                                 'prod_date': '' if _em == 'no' else _b['pd'],
                                 'exp_date': '' if _em == 'no' else _b['ed']})
    # 2026-11-05 走导入链路(用户定案): 出入库是自有仓台账的唯一来源, 也是最容易出问题的一环
    #   (仓型归属纠正、69码补齐、批次效期同步、月汇总回填、可用重算)。原实现是
    #   `INSERT IGNORE INTO inbound_records ...` —— 仓型、69码都靠"上游刚写好"假定成立,
    #   链路里那些纠正规则一次都没被走过。现在按渠道 × 类型发四份导入文件。
    #
    # 冲突模式用 sum(与导入页默认一致): 同 SKU+日期+69码 的行累加数量。
    #   这不是给 seed 开的特例 —— 真实客户把同一张入库单导两次, 语义就是"再入一次"。
    from routes.seed_import import import_rows as _imp
    _chs = (channel,) if channel else ('jd', 'other')
    _fts = (flow_type,) if flow_type else ('inbound', 'outbound')
    _stat = {}
    for ch in _chs:
        for _tgt in _fts:
            _tbl = 'inbound_records' if _tgt == 'inbound' else 'outbound_records'
            # 幂等: 只删「本次要导入的那张表 + 那个渠道」(另一张表/另一渠道的行不动)。
            #   四段各占一个 (渠道, 表) 组合, 互不重叠 → 任一段重跑都不会清空别的段。
            try:
                execute("DELETE FROM `%s` WHERE channel=%%s" % _tbl, [ch])
            except Exception as _edr:
                try_err('seed_fill', '出入库分片清理降级 %s/%s' % (_tbl, ch), _edr)
            _src = in_rows if _tgt == 'inbound' else out_rows
            _rows = [r for r in _src if str(r.get('channel') or '') == ch]
            if not _rows:
                _stat[_tgt + '_' + ch] = 0
                continue
            _r = _imp(_tgt, ch, _rows, label=_tgt)
            _stat[_tgt + '_' + ch] = _r.get('rows')
            if _r.get('failed'):
                try_err('seed_fill', '%s 导入 %s 有 %d 行未落库(首因=%s)'
                        % (_tgt, ch, _r['failed'],
                           str((_r.get('errors') or [{}])[0].get('reason') or '')[:150]), None)
    return sum(int(v or 0) for v in _stat.values())


def _sync_inv_month():
    """同步出入库记录到 inventory 月汇总(仓型×SKU×69码×仓×渠道 聚合)

    2026-11-01 三处修正(原实现只有 sku+warehouse+channel 三键, 且 UPDATE 缺 barcode):
      · 聚合键补 warehouse_type / barcode
        - 原 UPDATE ... WHERE sku=? AND warehouse=? AND channel=? AND warehouse_type='own'
          没有 barcode 条件 → 同一 SKU 同仓多码时(inventory 本就按码拆行)会把**同一份
          全量**写到每一行, 再经 insights.py:299 的 SUM() 汇总 → 当月入库/出库成倍放大
        - 原写死 warehouse_type='own' → 掩盖了 inbound/outbound_records.warehouse_type
          空串的缺口(seed 现已在 _seed_records 补写 'own')
      · 逐行 execute(约 4000 次 RTT, 实测 step8 1366s) → executemany 批量
      · 返回各列聚合组数, 供 /api/seed/step 运维口展示
    说明: 流水仓型为空串时匹配不到 inventory(不会写错值, 也不会兜底猜), 表现为月汇总
      不更新 —— 由 /api/seed/step 的前后对账把偏差显性暴露出来。
    """
    month_start = now_cn().strftime('%Y-%m-01')
    _stat = {}
    # 2026-11-01 清零(跨月必需): 原实现只 UPDATE「有流水聚合组的行」, 当月无流水的行会
    #   停留在上个月的月汇总值。实测 11-01: inventory.month_inbound 合计 98,725 vs 流水
    #   53,915 (1.83 倍), 157 行是 10 月残留。月汇总语义 =「本月流水合计」, 无流水即 0。
    #   注: 本函数仅 seed 调用(seed_step 8 / run_seed_fill); 生产环境客户导入的月度值
    #   (ERP 口径, cleanse_write 可写 month_* 列)不得被此函数覆盖 —— 导入路径不调这里。
    try:
        # 清零范围必须与下面的重算范围**完全一致**(channel IN ('jd','other')):
        #   清零更宽 → 该渠道被清 0 却永远不重算, 静默丢数据(seed 库现无其他渠道, 但不该靠运气)
        execute("UPDATE inventory SET month_inbound=0, month_outbound=0 "
                "WHERE channel IN ('jd','other')")
        _stat['cleared'] = "channel in (jd,other)"
    except Exception as _e:
        try_err('seed_fill', '月汇总清零', _e)
    for table, date_col, target_col in (('inbound_records', 'inbound_date', 'month_inbound'),
                                        ('outbound_records', 'outbound_date', 'month_outbound')):
        rows = query("SELECT warehouse_type, sku, COALESCE(barcode,'') AS bc, warehouse, channel, "
                     "SUM(quantity) AS q FROM `%s` "
                     "WHERE channel IN ('jd','other') AND `%s` >= %%s "
                     "GROUP BY warehouse_type, sku, COALESCE(barcode,''), warehouse, channel"
                     % (table, date_col), [month_start])
        _stat[target_col] = len(rows or [])
        if not rows:
            continue
        # 2026-11-05 线上事故修复(step22 单段 122s): 原 WHERE 写 `COALESCE(barcode,'')=%%s`,
        #   表达式让唯一键 uq_inventory_biz(sku,barcode,warehouse,channel,warehouse_type)
        #   只能用上 sku 前缀 —— EXPLAIN 从 Point_Get 退化成 IndexRangeScan + 逐行
        #   TableRowIDScan。inbound 7,869 + outbound 13,820 = 2.1 万组, 每组都要回表扫,
        #   单请求实测 122s 被网关 120s 硬切。
        #   改法: barcode 分两支 —— 有码走等值(命中完整唯一键, Point_Get);
        #   空码走 `barcode IS NULL OR barcode=''`(既有有码行不会被误改, 因为条件限定了空/无)。
        _n_bc = [r for r in rows if str(r.get('bc') or '')]
        _n_nul = [r for r in rows if not str(r.get('bc') or '')]
        if _n_bc:
            _args = [(int(r.get('q') or 0), str(r.get('sku') or ''), str(r.get('bc') or ''),
                      str(r.get('warehouse') or ''), str(r.get('channel') or 'jd'),
                      str(r.get('warehouse_type') or '')) for r in _n_bc]
            try:
                executemany("UPDATE inventory SET `%s`=%%s WHERE sku=%%s AND barcode=%%s "
                            "AND warehouse=%%s AND channel=%%s AND warehouse_type=%%s" % target_col, _args)
            except Exception as _e:
                try_err('seed_fill', '月汇总批量更新(有码)', _e)
        if _n_nul:
            _args2 = [(int(r.get('q') or 0), str(r.get('sku') or ''),
                       str(r.get('warehouse') or ''), str(r.get('channel') or 'jd'),
                       str(r.get('warehouse_type') or '')) for r in _n_nul]
            try:
                executemany("UPDATE inventory SET `%s`=%%s WHERE sku=%%s "
                            "AND (barcode IS NULL OR barcode='') "
                            "AND warehouse=%%s AND channel=%%s AND warehouse_type=%%s" % target_col, _args2)
            except Exception as _e:
                try_err('seed_fill', '月汇总批量更新(空码)', _e)
    # 2026-10-29 修复(恒等式 998 项偏差根因): 原此处 `beginning_stock = available_qty`
    #   会在重算(step7)之后再次覆盖期初 → available 被打回期初值, 流水不生效
    #   用户定案: 期初 = 库存导入值(_seed_inventory 已设), **任何后续步骤不得改写**
    #   · 删除 beginning_stock 覆盖
    #   · 删除 month_inbound 负数兜底(同属自动修正, 违反"以导入为准")
    # 2026-10-29 删除全部兜底(用户定案): 期初**以实际导入为准**, 不做任何反推或自动修正
    #   · 原"期初为负则用 month_inbound/outbound 反推" → 已删除(会产生与流水矛盾的数据)
    #   · 原"负期初归 0" → 一并删除(用户明确: 不要兜底; 负数应视为导入数据本身有问题,
    #     由每日对账告警暴露, 而非静默改写)
    return _stat
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
        # ── 2026-11-01 自有仓供给侧三条(可视化可配置; 看板「自有仓健康」卡同源) ──
        # 为什么不是低库存: 自有仓(集货仓)不产生销售 —— daily_sales_snapshot 只有平台仓行,
        #   逐仓日销 ds=0 → 动态安全线=0 → 低库存口径 `可售 < 0` 恒假, 结构性不可能触发(口径错配)。
        #   自有仓是供给侧, 该判的是「下游要货备不备得出 / 压得太多 / 会不会过期」。
        # 三条都带 inv.warehouse_type == 'own' 前置, 保证只在自有仓行求值
        #   (ctx 的 own.* 键也仅对 own 行注入, 双保险)。
        # ① 调拨覆盖不足(error): 一个调拨周期的要货量 > 自有可发(在仓+在途+采购在途)
        ("自有仓调拨覆盖不足", "inventory.changed",
         '{"left":"inv.warehouse_type","op":"==","right":"own",'
         '"and":{"left":"own.gap","op":">","right":"params.transfer_gap_min"}}',
         "own_transfer_gap", "调拨覆盖不足: {product_name}",
         "下游 {downstream_ds}/日×{b2c_days}天需 {need} 件, 自有可发 {can_ship} 件, 缺 {gap} 件", "error",
         "own_transfer_gap"),
        # ② 自有仓积压(warning): 自有可用 ÷ 下游日销 > 阈值天数
        ("自有仓积压", "scheduled.daily",
         '{"left":"inv.warehouse_type","op":"==","right":"own",'
         '"and":{"left":"own.cover_days","op":">","right":"params.own_overstock_days"}}',
         "own_overstock", "自有仓积压: {product_name}",
         "自有可用 {available} 件, 可撑 {cover_days} 天(阈值 {own_overstock_days} 天), 建议加速调拨",
         "warning", "own_overstock"),
        # ③ 效期风险(error): 最早到期批次 < 效期缓冲 → 会过期报废(直接损失, 可调拨消化)
        #   注意: 这里用 batch.days_to_expire(全仓最早批次), 与看板 own_health 的
        #   "按自有仓批次 + 消耗速度" 是两层 —— 看板更严(含"吃不完"判据), 规则作兜底粗筛。
        #   要更严可在规则页把阈值调大, 或叠加 own.cover_days 条件。
        ("自有仓效期风险", "scheduled.daily",
         '{"left":"inv.warehouse_type","op":"==","right":"own",'
         '"and":{"left":"batch.days_to_expire","op":"<","right":"params.own_expiry_buffer_days"}}',
         "own_expiry_risk", "效期风险: {product_name}",
         "最早期批次 {days_to_expire} 天后到期(负值=已过期; 缓冲 {own_expiry_buffer_days} 天), 需调拨消化或报损",
         "error", "own_expiry_risk"),
        # 注: 内置规则演进 —— "紧急补货"(replenish)/"滞销识别"(slow_moving) 退役
        #     (采购&补货卡读建议接口 / 处置建议页承载滞销); stockout/health 保留为参数载体(不告警)
    ]
    # 自有仓三条的默认参数(全部可在规则 tab 编辑 — params 即 ctx.params.* 的真源)
    _OWN_PARAMS = {
        "own_transfer_gap": {"transfer_gap_min": 1, "transfer_cover_mult": 1.0, "log": 0},
        "own_overstock": {"own_overstock_days": 90, "log": 0},
        "own_expiry_risk": {"own_expiry_buffer_days": 15, "log": 0},
    }
    for ch in ['jd', 'other']:  # 演示数据固定双渠道(seed 专用, 不随 channels 表扩展)
        for name, ev, cond, at, title, desc, sev, _p in rules:
            if at == "stockout":
                params = {"lit": 3 if ch == 'jd' else 10, "otif_min": 0.6, "ss_z": 1.65,
                          "accel_ratio": 1.3, "accel_min_qty": 10, "buffer_orange": 1.2,
                          "buffer_yellow": 1.0, "orange_slack_days": 1, "include_avail_zero": 1,
                          "log": 0, "alert_enabled": 0}
            elif at == "health":
                params = {"health_good": 85, "health_warning": 60, "log": 0, "alert_enabled": 0}
            elif at in _OWN_PARAMS:
                params = dict(_OWN_PARAMS[at])
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
        closed += int(_close_alerts(
            "source='rules_engine' AND channel=%s "
            "AND alert_type IN ('low_stock','replenish') "
            "AND related_sku IS NOT NULL AND related_sku != '' AND NOT EXISTS ("
            "SELECT 1 FROM inventory i WHERE i.channel=%s AND i.sku=alerts.related_sku "  # no-barcode-ok: 存在性判断(SELECT 1)
            "AND i.warehouse=alerts.warehouse AND i.available_qty < i.safety_qty)",
            [ch, ch], "seed_reset", status="closed") or 0)
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
    start = (now_cn() - timedelta(days=90)).strftime('%Y-%m-%d')
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
    jd_s = _make_skus('-J', SKU_PER_CHANNEL, BC2_RATIO)
    ot_s = _make_skus('-O', SKU_PER_CHANNEL, BC2_RATIO)
    return {'jd': jd_s, 'other': ot_s}


def _assert_available_balanced(chs):
    """落账核对(2026-11-05): 自有仓 可用 是否等于 期初 + Σ入库 − Σ出库

    为什么改成"只核对不重算": 可用重算这件事**已经由导入链路自己在做** —— 库存/出入库
    导入的写入分支会调 `_recalc_own_inventory` + `_rebuild_own_batches`。seed 再算一遍
    等于承认"链路没生效、得我自己补", 那正好把要验证的东西掩盖掉了。

    这里只读, 一条聚合 SQL 出「有差额的行数」和「最大差额」:
      有差额 = 导入链路的重算没覆盖到(或流水被拒写), 必须留痕而不是静默过关。
    """
    out = {}
    for ch in chs:
        try:
            _r = one(
                "SELECT COUNT(*) AS bad, COALESCE(MAX(ABS(d)),0) AS maxd FROM ("
                "  SELECT i.sku, i.warehouse, i.barcode,"
                "         i.available_qty - (COALESCE(i.beginning_stock,0)"
                "           + COALESCE(ib.q,0) - COALESCE(ob.q,0)) AS d"
                "  FROM inventory i"
                "  LEFT JOIN (SELECT sku, warehouse, COALESCE(barcode,'') AS bc, SUM(quantity) q"
                "             FROM inbound_records WHERE channel=%s"
                "             GROUP BY sku, warehouse, COALESCE(barcode,'')) ib"
                "    ON ib.sku=i.sku AND ib.warehouse=i.warehouse AND ib.bc=COALESCE(i.barcode,'')"
                "  LEFT JOIN (SELECT sku, warehouse, COALESCE(barcode,'') AS bc, SUM(quantity) q"
                "             FROM outbound_records WHERE channel=%s"
                "             GROUP BY sku, warehouse, COALESCE(barcode,'')) ob"
                "    ON ob.sku=i.sku AND ob.warehouse=i.warehouse AND ob.bc=COALESCE(i.barcode,'')"
                "  WHERE i.channel=%s AND i.warehouse_type='own'"
                "    AND (i.deleted_at IS NULL OR i.deleted_at='')"
                ") t WHERE d<>0" % ("%s", "%s", "%s"),
                [ch, ch, ch]) or {}
            _bad = int(_r.get("bad") or 0)
            out[ch] = {"bad_rows": _bad, "max_diff": float(_r.get("maxd") or 0)}
            if _bad:
                try_err("seed_fill",
                        "落账核对 %s: 自有仓有 %d 行 可用≠期初+Σ流水, 最大差 %.1f"
                        " —— 说明导入链路的重算没覆盖到这些行" % (ch, _bad, float(_r.get("maxd") or 0)),
                        None)
        except Exception as _e:
            out[ch] = "error: %s" % str(_e)[:100]
    return out


def _assert_barcode_aligned():
    """seed 自检(2026-11-01): **可补码的 SKU** 必须无空码

    为什么非做不可: 补码规则(`cleanse_write._fill_barcode_from_products`)是**导入入口**逻辑,
    而 seed 直接写表、绕过了入口。没有这道自检, 这条规则在 seed 上等于从未被验证过 ——
    多码/缺码链路会一直"看起来正常", 直到真实客户导入才炸。

    **只查 expiry_managed='no'**(2026-11-01 二次收紧后): 补码唯一合法场景是"明确不做效期
    管理 + 无多码证据"。效期商品/未标注商品出现空码是**合法状态**(留空待人工确认, 不猜),
    拿它们当违约会误报, 逼着人去"补"从而把错账固化。

    不查"码不符": 多码 SKU 的第二码天然与档案单值不符, 那是合法数据。

    违规 → 抛错终止(seed 数据自身违约, 不该带病继续跑后续步骤);
    返回 {表: 空码条数}, 全 0 时原样返回供 step summary 展示。
    """
    out = {}
    for tbl in ("inbound_records", "outbound_records", "inventory"):
        try:
            _r = one("SELECT COUNT(*) AS c FROM `%s` r JOIN products p ON p.sku=r.sku "
                     "WHERE COALESCE(p.barcode,'')<>'' AND COALESCE(r.barcode,'')='' "
                     "AND COALESCE(p.expiry_managed,'')='no'" % tbl) or {}
            out[tbl] = int(_r.get("c") or 0)
        except Exception as _e:
            out[tbl] = "error: %s" % str(_e)[:80]
    _bad = {k: v for k, v in out.items() if isinstance(v, int) and v > 0}
    if _bad:
        raise ValueError("seed 69码自检失败: 档案有码但记录空码 %s —— 补齐 _seed_* 生成逻辑 "
                         "或确认 _fill_barcode_from_products 未被绕过" % _bad)
    return out


# ── seed 分区计划(2026-11-05 与 seed_step 同步调整) ─────────────────────
# 订单从 4 段 × 15 天扩到 6 段 × 15 天(90 天装满), 其余段号顺延。
# 调用方(task_seed)只把它当"下一个 step 号"用, 不依赖具体含义, 所以新增段不需改协议。
# 订单 6 → 9 段(2026-11-05 线上实测): 单段 15 天双渠道实测 71~148s, 第 4 段 148s 已越过平台
#   建议的 90s 且贴着 120s 网关。拆到 10 天/段后单段订单量降 1/3, 单请求回到 ~50s 量级。
_SEED_ORDER_SEGS = 9                                   # step 1..9 = 订单 9 段(90 天 ÷ 10 天)
# 库存按渠道 × 仓型拆 4 段(2026-11-05 线上实测): 两渠道一次导入 **362s**, 是整链最重的一步,
#   把 90s/120s 两条线一起踩穿。拆开后单段约 90s(自有仓)以内。
_SEED_INV_JD_PF = _SEED_ORDER_SEGS + 1                 # 10 库存 jd 平台/B 仓
_SEED_INV_JD_OWN = _SEED_INV_JD_PF + 1                 # 11 库存 jd 自有仓
_SEED_INV_OT_PF = _SEED_INV_JD_OWN + 1                 # 12 库存 other 平台/B 仓
_SEED_INV_OT_OWN = _SEED_INV_OT_PF + 1                 # 13 库存 other 自有仓
_SEED_INV = _SEED_INV_JD_PF                            # 兼容别名: 「库存起点」
_SEED_INV_SEGS = (
    (_SEED_INV_JD_PF,  'jd',    'platform'),
    (_SEED_INV_JD_OWN, 'jd',    'own'),
    (_SEED_INV_OT_PF,  'other', 'platform'),
    (_SEED_INV_OT_OWN, 'other', 'own'),
)
_SEED_FLOW = _SEED_INV_OT_OWN + 1                      # 14 流水起点
# ── 一段一文件协议(2026-11-05 线上事故逼出来的架构改造) ────────────────
# 事故链: step14 一个请求要 DELETE 两张流水表 + 内存生成全量行 + 按渠道×类型发 **4 份导入
#   文件**(每份走完整链路) → 实测 >120s 被网关硬切 → `lease_until=NULL` 那行没执行 →
#   600s 租约挂死 → 期间所有页面/轮询拿不到进度 → 租约一过期又被 3 个驱动抢到重跑,
#   从 DELETE 全表开始再超一次。用户看到的就是「卡在 14 几百秒」。
#
# 「再拆一段」只是把大请求挪个位置, 不解决机制问题。这次两条一起改:
#   ① **一个请求 = 一份导入文件 = 一个步骤窗口**: 流水按 (渠道 × 出/入类型) 拆 4 段,
#      每段只删/只写自己那张表那个渠道 → 幂等可重跑, 不再清空线上数据;
#   ② 租约 600s → 150s(见 task_seed), 被网关切断最多静止 2.5 分钟而非 10 分钟;
#      配合每段幂等, 租约过期后重跑同一段不会写重数据。
#
# 单段预算 90s(平台建议), 网关硬上限 120s 留 30s 余量。段粒度依据(不预拆到最细):
#   · 流水单份文件 = 原 step14 的 1/4(4 份一起 >120s, 单份 ≈30~60s)
#   · 落账 4 段(渠道 × 仓型)线上已实测各 ≤40s, 保持不动
#   · 尾段原 1 段同时跑 alerts+snapshot+两渠道物化, 历史实测 456s → 拆 4 段
# 兼容性: 前 13 段段号**完全不动** —— 那 13 段线上已实测完成且数据正确, 改动只会逼
#   用户重灌、白烧 RU。新协议从 step14 起生效, 已有任务 params.step=14 天然落在
#   流水第一段上, 无需迁移、不用重灌。
SEED_STEP_BUDGET_S = 90

# 流水 4 段 = 渠道(2) × 出/入类型(2)。一段 = 一份文件 = 一个步骤窗口。
_FLOW_SEGS = tuple(
    (_SEED_FLOW + i, ch, ft)
    for i, (ch, ft) in enumerate(
        (ch, ft) for ch in ('jd', 'other') for ft in ('inbound', 'outbound')
    )
)
_FLOW_NOS = tuple(x[0] for x in _FLOW_SEGS)

# 落账 4 段 = 渠道 × 仓型。顺序有意义: 平台仓在前(量大), 自有仓在后(含可用重算与
#   best_before 回写, 必须最后, 因为它要读全部仓型的批次)。线上实测各 ≤40s, 保持原样。
_SETTLE_BASE = _FLOW_SEGS[-1][0] + 1                   # 18
_SETTLE_SEGS = (
    (_SETTLE_BASE,     'jd',    'platform'),
    (_SETTLE_BASE + 1, 'jd',    'own'),
    (_SETTLE_BASE + 2, 'other', 'platform'),
    (_SETTLE_BASE + 3, 'other', 'own'),
)
_SETTLE_NOS = tuple(x[0] for x in _SETTLE_SEGS)
_SEED_SETTLE_JD_PF = _SETTLE_SEGS[0][0]
_SEED_SETTLE = _SETTLE_SEGS[0][0]                      # 兼容别名: 落账起点
_SEED_SETTLE_OT_OWN = _SETTLE_SEGS[-1][0]              # 兼容别名: 最后一段(自检点)

_SEED_MONTH = _SETTLE_SEGS[-1][0] + 1                  # 22 月汇总 + 配置
# 尾段 4 段: 原一段同时跑 alerts + snapshot + 两渠道物化(历史实测 456s, 必然踩穿),
#   拆成 4 个独立单请求, 每个只做一件事。
_TAIL_SEGS = (
    (_SEED_MONTH + 1, 'alerts'),
    (_SEED_MONTH + 2, 'snapshot'),
    (_SEED_MONTH + 3, 'stats_jd'),
    (_SEED_MONTH + 4, 'stats_other'),
)
_SEED_TAIL = _TAIL_SEGS[0][0]                          # 兼容别名: 尾段起点
SEED_DONE = _TAIL_SEGS[-1][0] + 1                      # 27 = 完成


# ── 水位 / 回滚 / 行数对账(2026-11-05) ──────────────────────────────────
# 为什么需要: seed 一次全量 90 万行级订单 + 17 万行库存, 单步撞 90s 上限或网关 504 是常态。
#   旧做法下"跑到一半失败"只有一个出路 —— 整库 reset 重来(而 reset 也是几十万行 DELETE,
#   要 RU 也要时间, 且会把已经正确的步骤一并丢掉)。有了水位就能只回滚**这一轮新增的行**。
#
# 语义严格限定:
#   · hwm_before = 该表本轮写入前的 MAX(id)。回滚 = `DELETE WHERE id > hwm_before`
#     —— 只删本轮新增, 绝不碰历史行(哪怕历史行也是 seed 生成的)。
#   · 只对**插入型**目标有意义(orders/inventory/inbound/outbound/batches)。upsert 型目标
#     (products/suppliers/同键覆盖)无法用 id 水位回滚 —— 那类目标只做行数对账, 不登记回滚点。
#   · expected vs actual 不等 = 有行没落库(唯一键冲突被 IGNORE / 校验拒写)。
#     这个差在旧实现里是完全隐形的: 生成侧打印 "生成 170200 行", 库里实际 169800,
#     后续所有对账(Σ批次=可用 / 月汇总=流水)都在拿残缺底数算, 却"看起来跑完了"。
_WM_TARGETS = ('orders', 'inventory', 'inbound_records', 'outbound_records', 'batches')


def _wm_table_exists():
    try:
        one("SELECT 1 AS x FROM seed_watermark LIMIT 1")
        return True
    except Exception:
        return False


def wm_begin(run_id, step, targets, channel=''):
    """登记本轮写入前的 id 水位; 返回 {table: hwm_before}
    表不存在(老库未跑 ensure_schema)→ 返回空 dict, 调用方自动退化为"不登记, 不影响写入"
    """
    out = {}
    if not _wm_table_exists():
        return out
    for t in targets:
        if t not in _WM_TARGETS:
            continue  # 非插入型目标不登记(语义不成立, 见上方注释)
        try:
            _r = one("SELECT COALESCE(MAX(id),0) AS m FROM `%s`" % t) or {}
            _hwm = int(_r.get('m') or 0)
            execute("INSERT INTO seed_watermark(run_id, step, target, channel, hwm_before, status) "
                    "VALUES(%s,%s,%s,%s,%s,'writing') "
                    "ON DUPLICATE KEY UPDATE hwm_before=VALUES(hwm_before), expected=0, actual=0, "
                    "status='writing', started_at=NOW(), finished_at=NULL, note=''",
                    (run_id, int(step), t, channel, _hwm))
            out[t] = _hwm
        except Exception as _e:
            try_err('seed_fill', '水位登记降级 %s' % t, _e)
    return out


def wm_end(run_id, step, expected, channel=''):
    """回填本步实际落库行数, 与生成侧预期对账
    expected: {table: 生成行数}; 实际值按 `id > hwm_before` 精确计数(不受历史行干扰)
    """
    out = {}
    if not _wm_table_exists():
        return out
    for t, exp in (expected or {}).items():
        try:
            _r = one("SELECT hwm_before FROM seed_watermark "
                     "WHERE run_id=%s AND step=%s AND target=%s AND channel=%s",
                     (run_id, int(step), t, channel)) or {}
            _hwm = int(_r.get('hwm_before') or 0)
            # 计数用 `COUNT(*) WHERE id > hwm`(同一过滤条件, 精确) —— **不能用
            # MAX(id) - hwm**: INSERT IGNORE 撞键也会消耗 auto_increment, 两个数会差出一截,
            # 而且差得越多越像是"多写了"(实际是被 IGNORE 掉的空号)。
            _a = one("SELECT COUNT(*) AS c FROM `%s` WHERE id > %%s" % t, [_hwm]) or {}
            _act = int(_a.get('c') or 0)
            _exp = int(exp or 0)
            _status = 'ok' if _act >= _exp else 'short'
            execute("UPDATE seed_watermark SET expected=%s, actual=%s, status=%s, "
                    "finished_at=NOW(), note=%s "
                    "WHERE run_id=%s AND step=%s AND target=%s AND channel=%s",
                    (_exp, _act, _status,
                     '' if _act >= _exp else '落库少 %d 行(唯一键冲突或校验拒写)' % (_exp - _act),
                     run_id, int(step), t, channel))
            out[t] = {'expected': _exp, 'actual': _act, 'status': _status}
            # 少行**不抛错**: 可能是合法去重(如批次 INSERT IGNORE 撞唯一键),
            #   但要留痕 —— 旧实现在这里完全静默, 是"跑完了但数据少"的根源
            if _act < _exp:
                try_err('seed_fill', '水位对账少行 %s 步%d: 预期 %d 实际 %d'
                        % (t, int(step), _exp, _act), '')
        except Exception as _e:
            try_err('seed_fill', '水位回填降级 %s' % t, _e)
    return out


def wm_rollback(run_id, steps=None):
    """按水位回滚某轮 seed 的新增行(**只删 id > hwm_before**, 历史行不动)

    steps=None → 回滚该 run 全部已登记步骤。
    返回 {table: 删除行数} + 每步明细; 失败逐表留痕不中断(回滚是尽力而为, 但要能看见哪张表没删掉)。
    """
    out = {'deleted': {}, 'steps': [], 'error': ''}
    if not _wm_table_exists():
        out['error'] = 'seed_watermark 不存在(未跑 ensure_schema)'
        return out
    try:
        _sql = ("SELECT step, target, channel, hwm_before, expected, actual, status "
                "FROM seed_watermark WHERE run_id=%s")
        _p = [run_id]
        if steps:
            _sql += " AND step IN (%s)" % ",".join(["%s"] * len(steps))
            _p += [int(x) for x in steps]
        rows = query(_sql + " ORDER BY step, target", _p) or []
    except Exception as _e:
        out['error'] = str(_e)[:160]
        return out
    for r in rows:
        t = str(r.get('target') or '')
        _hwm = int(r.get('hwm_before') or 0)
        if t not in _WM_TARGETS:
            continue
        try:
            n = execute("DELETE FROM `%s` WHERE id > %%s" % t, [_hwm])
            out['deleted'][t] = out['deleted'].get(t, 0) + int(n or 0)
            out['steps'].append({'step': int(r.get('step') or 0), 'target': t,
                                 'hwm_before': _hwm, 'deleted': int(n or 0)})
        except Exception as _e:
            out['steps'].append({'step': int(r.get('step') or 0), 'target': t,
                                 'hwm_before': _hwm, 'error': str(_e)[:120]})
            try_err('seed_fill', '水位回滚失败 %s' % t, _e)
    return out


def wm_report(run_id):
    """该轮 seed 的水位与行数对账明细(运维口展示; 不含任何写操作)"""
    if not _wm_table_exists():
        return {'error': 'seed_watermark 不存在(未跑 ensure_schema)'}
    rows = query("SELECT step, target, channel, hwm_before, expected, actual, status, note, "
                 "started_at, finished_at FROM seed_watermark WHERE run_id=%s "
                 "ORDER BY step, target", [run_id]) or []
    _short = [r for r in rows if str(r.get('status') or '') == 'short']
    return {'run_id': run_id, 'steps': len(rows), 'short': len(_short), 'rows': rows}


def seed_step(step, today, skus_data, run_id=''):
    """执行单个步骤(Makers 异步分步: 每步 ≤90s, 任务表驱动续跑); 返回 (next_step, part_summary)

    run_id: 本轮 seed 的标识(任务 id)。给了就登记水位(可回滚 + 行数对账); 不给则纯生成。
      旧调用方(不带 run_id)行为完全不变 —— 水位是附加能力, 不是前置条件。
    """
    random.seed(42)  # 确定性: 各步/重复调用生成一致(防重跑数据漂移)
    if step == 0:
        n_prod, n_sup = _seed_products_suppliers(skus_data)
        return 1, {'products': len(n_prod), 'suppliers': len(n_sup)}
    if 1 <= step <= _SEED_ORDER_SEGS:
        _span = ORDER_DAYS // _SEED_ORDER_SEGS          # 10 天/段(90 ÷ 9)
        day_from = (step - 1) * _span
        if run_id:
            wm_begin(run_id, step, ('orders',), channel='jd,other')
        n = _seed_orders_range(today, skus_data, day_from, day_from + _span)
        if run_id:
            wm_end(run_id, step, {'orders': n}, channel='jd,other')
        return step + 1, {'orders_part_%d' % step: n}
    # 库存四段: 渠道 × 仓型(顺序与 _SETTLE_SEGS 一致: 平台仓在前, 自有仓在后 ——
    #   自有仓那次导入会触发可用重算 + 批次重建, 放在渠道末尾能少一次跨段等待)
    for _seg_no, _seg_ch, _seg_wg in _SEED_INV_SEGS:
        if step != _seg_no:
            continue
        if run_id:
            wm_begin(run_id, step, ('inventory',), channel=_seg_ch)
        n = _seed_inventory(skus_data, channel=_seg_ch, wh_group=_seg_wg)
        if run_id:
            wm_end(run_id, step, {'inventory': n}, channel=_seg_ch)
        return _seg_no + 1, {'inventory_%s_%s' % (_seg_ch, _seg_wg): n}
    # 流水 4 段: 每段只发 1 份导入文件, 只删/只写自己的 (渠道, 表) 组合。
    #   不登记水位: 水位语义是「插入型目标按 id 递增」, 而这里按 (渠道 × 表) DELETE 已经
    #   幂等, 多段写同一张表时水位回滚会误删别的段的行(见 wm_rollback 注释)。
    for _no, _ch, _ft in _FLOW_SEGS:
        if step != _no:
            continue
        # 一个请求 = 一份导入文件 = 一个步骤窗口(渠道 × 出/入类型)
        n = _seed_records(channel=_ch, flow_type=_ft)
        return _no + 1, {'flow_%s_%s' % (_ch, _ft): n}
    # 落账 4 段: 渠道 × 仓型, 线上已实测各 ≤40s, 段内 DELETE 与重建范围严格一致。
    for _no, _ch, _wg in _SETTLE_SEGS:
        if step != _no:
            continue
        _st = seed_settle(skus_data, channel=_ch, wh_group=_wg)
        # 最后一段做完 → 才做跨段自检: 此时两渠道 × 两仓型才全部落完
        if _no == _SETTLE_SEGS[-1][0]:
            try:
                _st = dict(_st or {})
                _st['barcode_aligned'] = _assert_barcode_aligned()
            except Exception as _ea:
                _st = dict(_st or {})
                _st['degraded'] = ((_st.get('degraded') or '') +
                                   '; selfcheck: %s' % str(_ea)[:100]).strip('; ')
        return _no + 1, _st
    if step == _SEED_MONTH:
        _ms = _sync_inv_month()
        _seed_config()
        return _SEED_MONTH + 1, {'config': 'ok', 'inv_month': _ms}
    # 尾段 4 段: 一个请求只做一件事, 不再把 alerts + snapshot + 两渠道物化塞进同一段
    for _no, _kind in _TAIL_SEGS:
        if step != _no:
            continue
        return _no + 1, {_kind: _seed_tail_piece(_kind)}
    return SEED_DONE, {}


def _seed_tail_piece(kind):
    """尾段单片: alerts / snapshot / stats_jd / stats_other —— 每段一个独立单请求

    历史教训: 原 step 把四件事塞进一个请求, 实测 456s(平台单请求上限 120s),
    运维口文档里明写「拒绝单跑」, 但 seed 主链路仍在跑 —— 等于留了一颗定时炸弹。
    """
    if kind == 'alerts':
        return _seed_alerts()
    if kind == 'snapshot':
        return _build_snapshot()
    try:
        from biz.sales import _rebuild_sku_sales_stats as _rss
        return _rss('jd' if kind == 'stats_jd' else 'other', days=ORDER_DAYS)
    except Exception as _erb:
        try_err('seed_fill', 'seed 物化重建降级 %s' % kind, _erb)
        return {'error': str(_erb)[:150]}


def seed_settle(skus_data=None, channel='', wh_group=''):
    """落账: 重算自有仓可用 → 重建批次 → 自检(2026-11-05 抽成独立函数, seed 与运维口共用)

    为什么单独成函数: 这段是"数据写完之后的账要平"的唯一实现 ——
      available = 期初 + Σ入库 − Σ出库, 且 Σ批次 ≡ available。
    原来它内联在 seed_step(7) 里, /api/seed/step 运维口又抄了一份, 两处各自演化
    (历史上就因此出现过"批次基于重算前旧库存"的顺序错误)。现在只有一个实现。

    skus_data: 传了就**用它枚举 SKU**(内存里现成的), 省掉一次 `SELECT DISTINCT sku
      FROM inventory` —— 实测 2 万行上这条要 3.1s, 而它拿到的集合与 skus_data 完全一致。

    channel (2026-11-05 拆段): 传单个渠道代码 → **只落这一个渠道**, 且跳过跨渠道自检。
      背景: 落账段两渠道实测 21.6s(重建批次) ×2 + 重算, 合计 50s+ 已是单请求里最重的一段;
      再加上前面几步累积, 90s 平台上限非常容易撞。拆成「jd」「other」两段后每段 ≤30s,
      单请求余量足够。不传 = 两渠道都做(一键 seed / 运维口的老行为, 语义不变)。

    wh_group (2026-11-05 二次拆段, 线上 504 逼出来的): 'platform' / 'own'。
      第一次拆段(按渠道)实测**不够** —— jd 单渠道仍 504(123s)。查下来 jd 有
      68,322 平台仓行 + 9,637 B 仓行, 每行 1~3 批 ≈ 16.8 万批次, 单请求必爆。
      于是再按仓型切一刀: 'platform' 段只做非 own 批次(+ 不算可用, 可用与仓型无关但
      放在 own 段一起做), 'own' 段只做自有仓批次(含 best_before 回写)。
      仓储口径完全不变, 只是把同一个重建过程分段执行。

    失败**不重抛**: 走到这一步库存/流水都已落库, 抛出去只会留下半张账;
    留痕 + 在返回里标 degraded, 由调用方决定要不要重跑这一步。
    """
    _chs = (channel,) if channel else ('jd', 'other')
    out = {'recalc_check': {}, 'batches': 0, 'barcode_aligned': {}, 'degraded': '',
           'channels': list(_chs), 'wh_group': wh_group or 'all'}
    # 可用重算与仓型无关(own 维度一条 SQL 全量), 只在不含 wh_group 或 wh_group='own' 时做,
    #   避免平台仓段白跑一遍(重算只写 own 行, 跑两次结果相同但白花 RU)
    #
    # 2026-11-05 用户定案: **这一步现在由导入链路自己做**。库存导入的 _write_rows 分支里
    #   对自有仓逐次调用 _recalc_own_inventory + _rebuild_own_batches(core/ledger 那条路径),
    #   和客户真实导入完全同一段代码。seed 再单独跑一遍就成了"两套账各自算一次",
    #   既白花 RU, 又掩盖了链路本身是否真的重算成功 —— 那正是要验证的东西。
    #   保留一个**只读核对**: 实际可用 与 期初+Σ流水 的差额, 有差就留痕(不静默)。
    if wh_group in ('', 'own'):
        try:
            out['recalc_check'] = _assert_available_balanced(_chs)
        except Exception as _e_s:
            out['degraded'] = 'recalc_check: %s' % str(_e_s)[:120]
            try_err('seed_fill', '落账-可用核对降级', _e_s)
    try:
        out['batches'] = _seed_batches(skus_data, channel=channel, wh_group=wh_group)
    except Exception as _e_b:
        out['degraded'] = (out['degraded'] + '; ' if out['degraded'] else '') + 'batches: %s' % str(_e_b)[:120]
        try_err('seed_fill', '落账-批次重建降级', _e_b)
    # 自检: 档案有码 → 流水/库存不得空码(违规抛错, 不带病进下一步)
    #   只有"两渠道 × 两仓型全落完"那一轮才有资格做: 否则另一段还没轮到,
    #   空码是"还没轮到"而不是"违规"。
    if not channel and not wh_group:
        out['barcode_aligned'] = _assert_barcode_aligned()
    return out


def run_seed_fill():
    """完整填充(PA 原版逻辑移植); 返回 summary dict"""
    started = time.time()
    random.seed(42)
    today = now_cn()
    # 2026-11-05 同步分步口径: 原这里写死 1000 SKU, 而 prepare_skus() 已是 SKU_PER_CHANNEL
    #   → 一键 seed 与分步 seed 生成的 SKU 集合不同(order_no 撞车/库存行数对不上)。
    #   两处必须同源, 所以直接调 prepare_skus()。
    skus_data = prepare_skus()
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
    n_flow = _seed_records()   # 全量模式返回本轮导入总行数(分片模式每段返回自己那片)
    # 2026-11-05 落账统一: 原此处内联了一份"重算 → 批次 → 自检", 与 seed_step 里那份各自演化
    #   → 抽出 seed_settle() 单一实现, 一键与分步走同一条路径
    _st = seed_settle(skus_data)
    n_batch = _st.get('batches', 0)
    _bc_chk = _st.get('barcode_aligned', {})
    _sync_inv_month()
    _seed_config()
    n_alert = _seed_alerts()
    n_snap = _build_snapshot()
    return {'orders': n_orders, 'products': len(n_prod), 'suppliers': len(n_sup),
            'inventory': n_inv, 'inventory_db': inv_counts, 'orders_db': orders_db, 'batches': n_batch,
            'flow_rows': n_flow, 'alerts': n_alert, 'snapshot': n_snap,
            'recalc': _st.get('recalc', 0), 'degraded': _st.get('degraded', ''),
            'barcode_aligned': _bc_chk,
            'elapsed': round(time.time() - started, 1)}