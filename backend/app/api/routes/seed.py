from fastapi import APIRouter
from app.core.database import get_db, get_conn, DB_PATH, submit_task, get_task, _task_lock, _task_results
from app.core.response import ok
from app.core.dashboard_cache import invalidate
from app.core.sales_utils import build_daily_sales_snapshot
from datetime import datetime, timedelta, timezone
import random, sqlite3, uuid, threading, os

router = APIRouter(prefix="/api/seed", tags=["seed"])

# 演示滞销 SKU(跨订单/库存函数共享): slow=真滞销(无单,小库存→yellow), low=低动销(观察级)
_DEMO_SLOW = set()
_DEMO_LOW = set()

# 品牌池（真实品牌, 按品类; 调味多为区域老字号, 零食/日化头部品牌）
# 品牌池(虚构演示品牌, 与真实品牌无关联——合规: 避免真实商家信息)
BRANDS_FOOD = ['禾味','山泉','椒香','酱乡','醋乡','味源','禾田','青禾','禾风','谷香','醇味','鲜禾','禾记']
BRANDS_SNACK = ['薯乐','果脆','禾果','咔脆','香脆','谷脆','果乐','脆脆','禾零','脆香']
BRANDS_HOME = ['净洁','柔白','净香','洁舒','柔洁','净白','舒洁','洁净','净舒','白净','净柔','净力']
def _cat_group(cat):
    if any(k in cat for k in ['薯片','虾条','爆米花','坚果','瓜子','花生','饼干','威化','巧克力','糖果']):
        return 'snack'
    if any(k in cat for k in ['洗衣','洗洁','洗手','消毒','纸巾','湿巾','垃圾袋','保鲜']):
        return 'home'
    return 'food'
def _brand_group(brand):
    if brand in BRANDS_SNACK: return 'snack'
    if brand in BRANDS_HOME: return 'home'
    return 'food'
def _pick_brand(cat):
    g = _cat_group(cat)
    pool = {'food': BRANDS_FOOD, 'snack': BRANDS_SNACK, 'home': BRANDS_HOME}[g]
    return random.choice(pool)

# 供应商→品牌映射: 每家 1~3 个真实品牌（按主营品类, 不重复, 不用序号名）
_SUP_BRANDS = None
def _get_sup_brands(n_sup=10):
    global _SUP_BRANDS
    if _SUP_BRANDS is None:
        _SUP_BRANDS = []
        for i in range(n_sup):
            # 主营品类轮换: 调味/零食/日化
            g = ['food','snack','home'][i % 3]
            pool = {'food': BRANDS_FOOD, 'snack': BRANDS_SNACK, 'home': BRANDS_HOME}[g]
            n = random.randint(1, 3)  # 1~3 个品牌
            _SUP_BRANDS.append(random.sample(pool, min(n, len(pool))))
    return _SUP_BRANDS


cat_names = ['酱油','酱料','调味汁' ,'食用油','醋','料酒','蚝油','芝麻油','辣椒酱','拌面酱',
             '老抽','生抽','陈醋','香醋','白醋','米醋','花椒油','藤椒油','辣椒油','芥末油',
             '番茄酱','甜辣酱','沙拉酱','芝麻酱','花生酱','豆瓣酱','豆豉','腐乳','糟卤','鱼露',
             '咖喱块','咖喱粉','五香粉','孜然粉','花椒粉','辣椒粉','胡椒粉','十三香','卤料包','炖肉料',
             '鸡精','味精','白糖','冰糖','红糖','麦芽糖','蜂蜜','料酒','黄酒','米酒',
             '薯片','虾条','爆米花','坚果','瓜子','花生','饼干','威化','巧克力','糖果',
             '洗衣液','洗洁精','洗手液','消毒液','纸巾','湿巾','垃圾袋','保鲜膜','保鲜袋','收纳盒']
store_names = ['自营旗舰店','直营店','调味品专营店','食品旗舰店','综合食品店']
WH = [('北京仓','platform'),('上海仓','platform'),('集货仓','own'),('成都仓','platform'),('武汉仓','platform'),('沈阳仓','platform'),('西安仓','platform'),('郑州仓','platform'),('三方仓','own'),('B仓','platform_b')]
# 销售仓子集: 订单(销售)只发生在 C 仓(platform), B仓(调拨)/自有仓(集货)不产生零售订单
SUP = [
    {'code':'SUP-001','name':'云味食品(演示)','contact':'王小明','phone':'010-80000001','score':5},
    {'code':'SUP-002','name':'谷香调味(演示)','contact':'李小红','phone':'010-80000002','score':4},
    {'code':'SUP-003','name':'椒香园食品(演示)','contact':'赵大勇','phone':'010-80000003','score':5},
    {'code':'SUP-004','name':'青禾食品(演示)','contact':'孙晓梅','phone':'010-80000004','score':3},
    {'code':'SUP-005','name':'禾味坊调味(演示)','contact':'周建华','phone':'010-80000005','score':4},
    {'code':'SUP-006','name':'鲜禾食品(演示)','contact':'吴丽华','phone':'010-80000006','score':5},
    {'code':'SUP-007','name':'醇味调味(演示)','contact':'郑国栋','phone':'010-80000007','score':4},
    {'code':'SUP-008','name':'禾田食品(演示)','contact':'陈志强','phone':'010-80000008','score':3},
    {'code':'SUP-009','name':'山泉食品(演示)','contact':'林秀英','phone':'010-80000009','score':4},
    {'code':'SUP-010','name':'净洁日化(演示)','contact':'黄文博','phone':'010-80000010','score':5},
]

def make_skus(sfx, count=1000, shared=None):
    """生成 SKU 列表。shared: 前 N 个共享 SKU 的内容模板（字典列表），
    复制其商品字段但 SKU 字符串独立命名（带本渠道后缀），避免跨渠道同名互相覆盖。"""
    r = []
    for i in range(1, count + 1):
        c = cat_names[(i-1)%len(cat_names)]
        s = store_names[(i-1)%len(store_names)]
        price_type = random.choices(['normal','low','high'],[80,10,10])[0]
        if price_type == 'low': p = round(random.uniform(1.9, 5.0), 1)
        elif price_type == 'high': p = round(random.uniform(100, 299), 1)
        else: p = round(random.uniform(5.8, 99.9), 1)
        unit = '包' if c in ['薯片','虾条','爆米花','坚果','瓜子','花生','饼干','威化','巧克力','糖果','纸巾','湿巾','垃圾袋','保鲜膜','保鲜袋'] else ('瓶' if c in ['洗衣液','洗洁精','洗手液','消毒液'] else '瓶')
        sku = f'SKU-{i:04d}{sfx}'
        brand = _pick_brand(c)
        shared_src = shared[i-1] if shared and i <= len(shared) and shared[i-1] is not None else None
        if shared_src:
            # 共享 SKU：内容与另一个渠道相同，但命名独立（防 products upsert 互相覆盖）
            item = dict(shared_src)
            item['sku'] = sku
            item['store'] = s
            if not item.get('brand'): item['brand'] = _pick_brand(item.get('cat',''))
            r.append(item)
            continue
        r.append({'sku':sku,'name':f'{c}{i}','store':s,'cat':c,'price':p,'box':random.choice([6,12,24]),'unit':unit,'barcode':f'690{i:010d}','weight':round(random.uniform(5,25),1),'volume':round(random.uniform(0.02,0.12),3),'status':'active','brand':brand})
    return r

_current_task_id = None

def _check_busy(conn):
    """并发保护：返回正在进行的 seed/reset 任务（running/pending 且 25 分钟内有过更新 = 活着）。
    卡死任务（超 25 分钟无更新，线程被 PA 重启/OOM 杀）自动标记 error 并放行。"""
    try:
        rows = conn.execute(
            "SELECT task_id FROM sync_tasks WHERE task_type IN ('seed','reset') AND status IN ('running','pending') "
            "AND updated_at >= datetime('now','-25 minutes')").fetchall()
        if rows:
            return rows[0][0]
        # 卡死的 running 任务标记 error
        stale = conn.execute(
            "SELECT task_id FROM sync_tasks WHERE task_type IN ('seed','reset') AND status IN ('running','pending') "
            "AND updated_at < datetime('now','-25 minutes')").fetchall()
        for _s in stale:
            try:
                _payload = json.dumps({"error": "任务卡死超时，已自动标记失败（可能因服务器资源受限）"}, ensure_ascii=False)
                conn.execute("UPDATE sync_tasks SET status='error', result=?, updated_at=datetime('now') WHERE task_id=?",
                    (_payload, _s[0]))
                conn.commit()
            except Exception:
                pass
    except Exception:
        pass
    return None


@router.post("/fill")
def seed_fill():
    global _current_task_id
    # 检查是否已有数据（防止未重置直接填充导致 UNIQUE 冲突/数据混合）
    try:
        conn = get_conn()
        n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        has_data = n > 0
        if has_data:
            return ok({"requires_reset": True, "task_id": "", "message": "已有数据，请先一键重置"})
        # 并发保护：已有正在执行的 seed/reset 任务时拒绝新提交（防止并发抢锁互相拖死）
        busy = _check_busy(conn)
        if busy:
            return ok({"task_id": "", "message": f"已有任务进行中: {busy}，请等待完成"})
    except Exception:
        pass
    task_id = 'seed_fill_' + uuid.uuid4().hex[:8]
    _current_task_id = task_id
    submit_task(task_id, _seed_fill_async, task_type='seed', channel='all')
    return ok({"task_id": task_id, "message": "种子数据填充已开始"})

@router.get("/fill/status")
def seed_fill_status(task_id: str = 'seed_fill'):
    t = get_task(task_id)
    if not t: return ok({"status": "not_found"})
    r = {"status": t['status'], "steps": t.get('steps', [])}
    if t.get('error'): r['error'] = t['error']
    if t.get('result'): r['result'] = t['result']
    return ok(r)

def _run_step(step_name, fn, steps):
    """添加步骤到列表（先设为 running），执行 fn，完成后更新为 ok/error"""
    import time as _t
    steps.append({"name": step_name, "status": "running"})
    _update_steps(steps)
    start = _t.time()
    try:
        fn()
        steps[-1] = {"name": step_name, "status": "ok", "elapsed": round(_t.time() - start, 1)}
    except Exception as e:
        err = str(e)[:500]
        steps[-1] = {"name": step_name, "status": "error", "error": err, "elapsed": round(_t.time() - start, 1)}
        # 持久化异常到 quality_logs（可跨重启查看）
        try:
            conn = get_conn()
            conn.execute("INSERT INTO quality_logs(log_type,level,message,details,source) VALUES(?,?,?,?,?)",
                ("seed_step", "error", f"种子填充步骤失败: {step_name}", err, "seed_engine"))
            conn.commit()
        except Exception as le:
            pass
    _update_steps(steps)

def _seed_fill_async():
    global _current_task_id
    db = get_db()
    today = datetime.now(timezone.utc)
    conn = get_conn()
    steps = []

    # 提高写入速度：synchronous=OFF 跳过 fsync(PA慢磁盘4000次commit被放大→曾355s) + 页缓存 + 内存临时表
    # 注: 连接级设置仅影响本线程, 请求线程保持 NORMAL 安全; 填充结束恢复 NORMAL
    try:
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-64000")
        conn.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass

    # 先统一生成 SKU，确保各步骤数据一致
    # 共享 SKU：jd 前 200 个作为内容模板传入（make_skus 会复制字段但独立命名）
    jd_s = make_skus('-J', 1000)
    shared_skus = jd_s[:200]
    ot_s = make_skus('-O', 1000, shared=shared_skus + [None] * 800)
    skus_data = {'jd': jd_s, 'other': ot_s}

    # 步骤1: 清空旧数据（临时切 DELETE 模式避免 WAL 膨胀，orders 分批删除防 I/O error）
    def _clear_all():
        conn = get_conn()
        # 先切回 WAL 模式（之前可能因配额满降级为 DELETE，DELETE 下批量写入极慢）
        try: conn.execute("PRAGMA journal_mode=WAL")
        except Exception: pass
        # 临时切到 DELETE journal 模式（清空期间避免 WAL 文件膨胀导致 disk I/O error）
        try: conn.execute("PRAGMA journal_mode=DELETE")
        except Exception: pass
        for t in ['inventory','products','suppliers','alerts','quality_logs','events','purchase_orders','replenishment_config_history','cleansing_templates','custom_fields','daily_sales_snapshot','daily_stats','inbound_records','outbound_records','rules','replenishment_config','batches']:
            try: conn.execute(f'DELETE FROM "{t}"')
            except Exception: pass
        # 立即恢复 jwt_secret（replenishment_config 被清空后，若此时 PA 重启会生成新密钥导致旧 token 全失效 401）
        try:
            _secret = os.getenv("JWT_SECRET", "")
            if _secret:
                conn.execute("INSERT OR REPLACE INTO replenishment_config(key,value,channel) VALUES('jwt_secret',?,'jd')", (_secret,))
        except Exception: pass
        # orders 大表分批删除（每批 5000 行 commit，避免单事务过大）
        try:
            while True:
                cur = conn.execute("DELETE FROM orders WHERE id IN (SELECT id FROM orders LIMIT 5000)")
                conn.commit()
                if cur.rowcount == 0: break
        except Exception as e:
            # 分批删除失败则 DROP 重建
            try:
                conn.execute("DROP TABLE IF EXISTS orders")
                conn.execute("""CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_no TEXT NOT NULL, store TEXT DEFAULT '', warehouse TEXT DEFAULT '',
                    sku TEXT DEFAULT '', product_name TEXT DEFAULT '', quantity INTEGER DEFAULT 0,
                    unit_price REAL DEFAULT 0, total_amount REAL DEFAULT 0, data_source TEXT DEFAULT '',
                    order_status TEXT DEFAULT '', ordered_at TEXT DEFAULT '', platform TEXT DEFAULT '',
                    supplier TEXT DEFAULT '', remark TEXT DEFAULT '', parent_order_no TEXT DEFAULT '',
                    raw_data TEXT DEFAULT '', source TEXT DEFAULT '', owner_id TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')),
                    channel TEXT DEFAULT 'jd', paid_at TEXT DEFAULT '', barcode TEXT DEFAULT '')""")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_no_sku ON orders(order_no, sku)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_ordered_at ON orders(ordered_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_sku ON orders(sku)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_ch_status ON orders(channel, order_status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_sku_ordered_at ON orders(sku, ordered_at, channel)")
            except Exception as e2:
                pass
        conn.commit()
        # 恢复 WAL 模式
        try: conn.execute("PRAGMA journal_mode=WAL")
        except Exception: pass
    _run_step('清空旧数据', _clear_all, steps)

    # 步骤2: 写入商品/供应商
    _run_step('写入商品/供应商', lambda: _seed_products_suppliers(db, skus_data), steps)

    # 步骤3: 生成订单
    _run_step('生成订单', lambda: _seed_orders(db, today, skus_data), steps)

    # 步骤4: 生成库存
    _run_step('生成库存', lambda: _seed_inventory(db, skus_data), steps)

    # 步骤5: 生成当月出入库记录（进销存页展示）
    _run_step('生成批次效期', lambda: _seed_batches(db, skus_data), steps)
    _run_step('生成出入库记录', lambda: _seed_records(db, skus_data), steps)

    # 步骤6: 写入补货参数和规则
    _run_step('写入补货参数/规则', lambda: _seed_config(db, conn), steps)

    # 步骤7: 触发规则引擎
    _run_step('触发规则引擎', lambda: _seed_rules(db, skus_data), steps)

    # 步骤8: 构建日销快照（新数据实时纳入日销计算，不等次日凌晨）
    _run_step('构建日销快照', lambda: build_daily_sales_snapshot(db), steps)

    # 刷新缓存
    try:
        invalidate()
        from app.core.replenishment_cache import invalidate_cache
        invalidate_cache(db)
    except Exception as _e: import logging; logging.warning(f'[seed] invalidate cache: {_e}')
    # 预热看板缓存（避免首次请求超时）
    try:
        import requests
        base = os.getenv("API_BASE_URL", "https://overtrees.pythonanywhere.com")
        for ch in ['jd','other']:
            requests.get(f"{base}/api/dashboard/summary?channel={ch}", timeout=120)
    except Exception as _e: import logging; logging.warning(f'[seed] warmup: {_e}')

    # 持久化填充结果 summary（成功/失败，跨重启可查）
    try:
        ok_steps = [s for s in steps if s.get('status') == 'ok']
        err_steps = [s for s in steps if s.get('status') == 'error']
        conn = get_conn()
        conn.execute("INSERT INTO quality_logs(log_type,level,message,details,source) VALUES(?,?,?,?,?)",
            ("seed_result",
             "success" if not err_steps else "error",
             f"种子填充{'完成' if not err_steps else '部分失败'}: {len(ok_steps)}/{len(steps)} 步成功",
             f"步骤: {[s['name'] for s in err_steps]} 失败: {[s.get('error','')[:100] for s in err_steps]}",
             "seed_engine"))
        # checkpoint 防膨胀（填充产生大量写入，合并 WAL 到主库释放空间）
        try:
            conn.execute("PRAGMA synchronous=NORMAL")  # 恢复正常模式保证请求线程安全
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        conn.commit()
        # 广播填充完成 → 前端自动刷新所有数据
        try:
            from app.api.routes.ws import broadcast_sync
            broadcast_sync("data.updated")
            # seed 填充后补货/进销存缓存失效（_replen_version 递增）
            try:
                from app.core.replenishment_cache import invalidate_cache
                invalidate_cache(get_db())
            except Exception:
                pass
            # seed 重建了规则/供应商 → 清规则页缓存（避免最长 180s 显示旧数据）
            try:
                from app.api.routes.rules import _rules_cache
                _rules_cache.clear()
                from app.api.routes.suppliers import _suppliers_cache
                _suppliers_cache.clear()
            except Exception:
                pass
        except Exception:
            pass
    except Exception as e:
        pass

    return {"steps": steps}


def _update_steps(steps):
    global _current_task_id
    if _current_task_id:
        with _task_lock:
            t = _task_results.get(_current_task_id)
            if t: t['steps'] = list(steps)
        # 持久化 steps 到数据库（跨重启可恢复进度）
        try:
            from app.core.database import update_task
            update_task(_current_task_id, steps=list(steps))
        except Exception as e:
            import logging; logging.error(f"[seed] 步骤进度持久化失败 {_current_task_id}: {e}")

def _seed_products_suppliers(db, skus_data):
    sup_brands = _get_sup_brands(10)
    conn = get_conn()
    _prod_rows = []
    for skus,ch in [(skus_data['jd'],'jd'),(skus_data['other'],'other')]:
        for i, p in enumerate(skus):
            # 按顺序分配供应商（10 家供应商轮流覆盖 SKU）
            _sup_idx = i % 10
            _sup_code = f"SUP-{_sup_idx+1:03d}-{ch.upper()}"
            # SKU 品牌: 优选该供应商品牌中与 SKU 品类匹配的; 无匹配用品类池兜底
            _blist = sup_brands[_sup_idx]
            _g = _cat_group(p.get('cat',''))
            _matched = [b for b in _blist if _brand_group(b) == _g]
            if _matched:
                _brand = _matched[(i // 10) % len(_matched)]
            else:
                _brand = _pick_brand(p.get('cat',''))
            _prod_rows.append({'sku':p['sku'],'product_name':p['name'],'store':p['store'],'category':p['cat'],'price':p['price'],'box_qty':p['box'],'barcode':p['barcode'],'weight':p['weight'],'volume':p['volume'],'status':p['status'],'channel':ch,'supplier_code':_sup_code,'brand':_brand})
    # 批量 INSERT OR REPLACE(替代逐条 upsert 4000次commit——PA慢磁盘 fsync 放大致 355s)
    if _prod_rows:
        _cols = list(_prod_rows[0].keys())
        conn.executemany(
            "INSERT OR REPLACE INTO products(" + ', '.join('"%s"' % c for c in _cols) + ") VALUES(" + ', '.join('?' for _ in _cols) + ")",
            [[r.get(c) for c in _cols] for r in _prod_rows])
        conn.commit()
    _sup_rows = []
    for s_idx, s in enumerate(SUP):
        _blist = sup_brands[s_idx % 10]
        for ch in ['jd','other']:
            _sc = f"{s['code']}-{ch.upper()}"
            _brands = '，'.join(_blist)
            _sup_rows.append({'supplier_code':_sc,'supplier_name':s['name'],'contact_person':s['contact'],'contact_phone':s['phone'],'score':s['score'],'channel':ch,'brand':_brands})
    if _sup_rows:
        _scols = list(_sup_rows[0].keys())
        conn.executemany(
            "INSERT OR REPLACE INTO suppliers(" + ', '.join('"%s"' % c for c in _scols) + ") VALUES(" + ', '.join('?' for _ in _scols) + ")",
            [[r.get(c) for c in _scols] for r in _sup_rows])
        conn.commit()

def _seed_orders(db, today, skus_data):
    jd_s, ot_s = skus_data['jd'], skus_data['other']
    conn = get_conn()
    cols = ['order_no','store','warehouse','sku','product_name','quantity','unit_price','total_amount',
            'order_status','ordered_at','paid_at','channel','platform',
            'freight_amount','subsidy_amount','tax_amount','discount_amount','actual_amount']
    batch_size = 5000
    total = 0
    batch = []
    def _amt(q, price):
        """GMV 金额明细: 部分单带运费/满减/平台补贴, GMV = total - discount + freight + tax"""
        base = round(q * price, 2)
        disc = round(base * random.uniform(0.02, 0.10), 2) if random.random() < 0.5 else 0
        freight = random.choice([0, 0, 0, 6, 8, 12])
        subsidy = round(base * random.uniform(0.03, 0.12), 2) if random.random() < 0.3 else 0
        tax = 0.0
        return {'total_amount': base, 'discount_amount': round(disc, 2), 'freight_amount': freight,
                'subsidy_amount': round(subsidy, 2), 'tax_amount': tax,
                'actual_amount': round(base - disc - subsidy + freight + tax, 2)}
    def flush():
        nonlocal batch
        if not batch: return
        conn.executemany(
            f"INSERT INTO orders({','.join(cols)}) VALUES({','.join(['?']*len(cols))})",
            [[o.get(c) for c in cols] for o in batch]
        )
        conn.commit()
        batch = []
    for ch,label,skus,base in [('jd','jd',jd_s,1100),('other','other',ot_s,550)]:
        promo = {'618':list(range(5,20)),'月末':list(range(45,55))}
        # 滞销场景: 3% SKU 完全不出单(真滞销) + 2% SKU 每30天出1单(低动销/观察级)
        _n = len(skus)
        # 滞销/低动销 SKU 随机挑选(3%+2%), 每次填充不同 SKU 演示
        _shuffled = skus[:]
        random.shuffle(_shuffled)
        _slow_skus = set(x['sku'] for x in _shuffled[:_n*3//100]) if _n >= 50 else set()
        _low_idx = {x['sku'] for x in _shuffled[_n*3//100:_n*5//100]} if _n >= 50 else set()
        global _DEMO_SLOW, _DEMO_LOW
        _DEMO_SLOW |= _slow_skus
        _DEMO_LOW |= _low_idx
        _normal_skus = [x for x in skus if x['sku'] not in _slow_skus and x['sku'] not in _low_idx]
        for d in range(60):
            dt = today - timedelta(days=d)
            is_promo = any(d in v for v in promo.values())
            cnt = int(base * random.uniform(2,4)) if is_promo else (int(base * random.uniform(0.6,1.2)) if dt.weekday()>=5 else base)
            # 低动销 SKU: 每 30 天出 1 单, 偏移 18 天(最后销售在 15-30 天前 → observe 观察级)
            if d % 30 == 18 or is_promo:
                for lsk in _low_idx:
                    sk = next((x for x in skus if x['sku']==lsk), None)
                    if not sk: continue
                    q = random.randint(1,4)
                    st = random.choices(['已完成','已发货'],[80,20])[0]
                    _a = _amt(q, sk['price'])
                    batch.append({'order_no':f'{label.upper()}-L{d:03d}-{lsk[-3:]}','store':sk['store'],'warehouse':random.choice([w for w,wt in WH if wt=='platform']),'sku':sk['sku'],'product_name':sk['name'],'quantity':q,'unit_price':sk['price'],**_a,'order_status':st,'ordered_at':dt.strftime('%Y-%m-%d'),'paid_at':dt.strftime('%Y-%m-%d'),'channel':ch,'platform':'京东' if label=='jd' else '天猫'})
                    total += 1
            for _ in range(cnt):
                sk = random.choice(_normal_skus if _normal_skus else skus)
                q = random.randint(1,20) if is_promo else random.randint(1,8)
                st = random.choices(['已完成','已发货','待发货','待确认','申请退款'],[45,18,15,10,7])[0]
                if random.random() < 0.03: st = '已退货'
                paid_dt = dt + timedelta(days=random.randint(1,3))
                _a = _amt(q, sk['price'])
                batch.append({'order_no':f'{label.upper()}-{ch}{d:03d}-{total:03d}','store':sk['store'],'warehouse':random.choice([w for w,wt in WH if wt=='platform']),'sku':sk['sku'],'product_name':sk['name'],'quantity':q,'unit_price':sk['price'],**_a,'order_status':st,'ordered_at':dt.strftime('%Y-%m-%d'),'paid_at':paid_dt.strftime('%Y-%m-%d'),'channel':ch,'platform':'京东' if label=='jd' else '天猫'})
                total += 1
                if len(batch) >= batch_size:
                    flush()
    flush()
    return total

def _seed_inventory(db, skus_data):
    jd_s, ot_s = skus_data["jd"], skus_data["other"]
    inv = []
    jd_s, ot_s = skus_data['jd'], skus_data['other']
    for skus in [jd_s,ot_s]:
        for sk in skus:
            # 18% SKU 全仓低库存（模拟需补货/采购场景），其余正常
            low = random.random() < 0.18
            seen_own = False
            for wn,wt in WH:
                if wt == 'platform_b' and skus is not jd_s:
                    # B 仓是京东主体 BBCC 专属，其他渠道不生成 B 仓库存
                    continue
                if wt == 'own':
                    # WH 里有两个 own 仓（集货仓/三方仓），只保留一个避免重复行
                    if seen_own: continue
                    seen_own = True
                    wh_name = '集货仓' if skus is jd_s else '三方仓'
                else: wh_name = wn
                if sk['sku'] in _DEMO_SLOW:
                    q = random.randint(20, 60)     # 真滞销SKU小库存(占用<1万) → yellow
                elif sk['sku'] in _DEMO_LOW:
                    q = random.randint(30, 80)     # 低动销小库存 → observe
                elif low and wt == 'platform':
                    q = random.randint(0, 5)       # C 仓极低库存(7仓汇总0-35<需求65) → 触发补货
                elif low and wt == 'platform_b':
                    q = random.randint(0, 3)       # B 仓也低
                elif low and wt == 'own':
                    q = random.randint(0, 8)       # 自有仓也低 → 系统总库存低，触发采购
                else:
                    q = random.randint(50, 800)
                inv.append({'sku':sk['sku'],'product_name':sk['name'],'warehouse':wh_name,'warehouse_type':wt,'available_qty':q,'in_transit_qty':0 if low else random.randint(0,200),'safety_qty':random.randint(30,200),'channel':'jd' if skus is jd_s else 'other'})
    db.table("inventory").insert(inv).execute()

def _seed_batches(db, skus_data):
    """生成批次效期数据（SKU×仓库 1~3 批，16% 批次临期/受损用于演示预警）

    批次: prod_date(生产) + exp_date(截止) = prod + 总效期(随机)
    总效期: 按品类 60~240 天（食品短/家清长）
    16% 批次剩余效期 < 1/3(临近/否档)  → 预警演示
    同时回写 products.best_before = 该 SKU 最早批次截止日(风险最高)
    """
    from datetime import timedelta as _td, timezone
    today = datetime.now(timezone.utc)
    conn = get_conn()
    rows = conn.execute("SELECT sku, warehouse, warehouse_type, channel, available_qty FROM inventory WHERE available_qty > 0").fetchall()
    bdata = []
    _pcat = {}
    try:
        for r in conn.execute("SELECT sku, category, channel FROM products").fetchall():
            _pcat[(str(r[0]), str(r[2] or 'jd'))] = str(r[1] or '')
    except Exception: pass
    best_map = {}
    problem_skus = set()
    _all_skus = sorted({(str(r[0]), str(r[3] or 'jd')) for r in rows})
    for idx, (s, c) in enumerate(_all_skus):
        chk = random.random()
        if chk < 0.02:      # 2% SKU 带"过期"批次演示 black
            problem_skus.add(s)
        elif chk < 0.04:    # 2% SKU 带"接近1/3"批次演示 warn
            problem_skus.add(s)
    for r in rows:
        sku, wh, wht, ch, qty = str(r[0]), str(r[1] or ''), str(r[2] or ''), str(r[3] or 'jd'), int(r[4] or 0)
        if qty <= 0: continue
        cat = _pcat.get((sku, ch), '')
        foodish = any(k in cat for k in ['酱油','酱','醋','油','酒','糖','蜂','咖','粉','薯','坚果','饼干','巧克力','糖果','麻辣','椒'] ) if cat else False
        shelf = random.randint(150, 270) if foodish else random.randint(200, 365)
        is_prob = sku in problem_skus
        n_batch = random.randint(1, 3)
        parts = [0.6, 0.3, 0.1][:n_batch]
        qty_left = qty
        for bi, ratio in enumerate(parts):
            bq = int(qty * ratio) if bi < n_batch - 1 else qty_left
            qty_left -= bq
            if bq <= 0: continue
            if is_prob and random.random() < 0.5:
                # 问题 SKU 的批次: 一半过期 / 一半接近1/3
                if random.random() < 0.5:
                    _ago = shelf + random.randint(3, 15)   # 真过期
                else:
                    _ago = random.randint(max(shelf // 3 - 2, 5), max(shelf // 3 + 2, 8))  # 接近1/3
            else:
                _ago = random.randint(2, max(shelf // 3 - 6, 5))   # 正常
            prod = today - _td(days=_ago)
            exp = prod + _td(days=shelf)
            bdata.append({'sku': sku, 'warehouse': wh, 'warehouse_type': wht, 'channel': ch,
                          'prod_date': prod.strftime('%Y-%m-%d'), 'exp_date': exp.strftime('%Y-%m-%d'), 'qty': bq})
            # 记录 SKU 最早截止
            if sku not in best_map or exp < best_map[sku]:
                best_map[sku] = exp
    if bdata:
        conn.executemany("INSERT INTO batches(sku, warehouse, warehouse_type, channel, prod_date, exp_date, qty) VALUES(?,?,?,?,?,?,?)",
                         [[b['sku'], b['warehouse'], b['warehouse_type'], b['channel'], b['prod_date'], b['exp_date'], b['qty']] for b in bdata])
    # 回写 products.best_before = SKU 最早批次截止日
    for sku, exp in best_map.items():
        try:
            conn.execute("UPDATE products SET best_before=? WHERE sku=? AND (best_before='' OR best_before IS NULL)", (exp.strftime('%Y-%m-%d'), sku))
        except Exception: pass
    # problem SKU(过期/临期批次)强制回写 best_before → 滞销建议 black 级
    for sku in problem_skus:
        if sku in best_map:
            try:
                conn.execute("UPDATE products SET best_before=? WHERE sku=?", (best_map[sku].strftime('%Y-%m-%d'), sku))
            except Exception: pass
    conn.commit()
    return len(bdata)


def _seed_rules(db, skus_data):
    """触发规则引擎：SQL 级聚合 + 条件筛选，批量生成告警
    （替代 Python 全量遍历，10 万 SKU 时从 O(百万行 Python) → SQL 聚合 + 仅筛选符合条件的 SKU）"""
    conn = get_conn()
    # 先关闭陈旧告警：该渠道下该 SKU **所有仓行都不再低于安全线** → 告警失效。
    # 判据与实时事件路径 rules.py:evaluate 一致（逐仓行 available_qty < safety_qty），
    # 比本函数的跨仓 SUM 建告警判据更宽松 → 只会关掉「确实已恢复」的告警，不会误关仍在预警的。
    # 对照 replenishment.py 有 status='closed' 收尾，rules 路径此前缺失 → 补货后历史告警仍 active。
    closed_rows = 0
    for ch in ('jd', 'other'):
        cur = conn.execute("""
            UPDATE alerts SET status='closed'
            WHERE source='rules_engine' AND channel=? AND status='active'
              AND alert_type IN ('low_stock','replenish')
              AND related_sku IS NOT NULL AND related_sku != ''
              AND NOT EXISTS (
                  SELECT 1 FROM inventory i
                  WHERE i.channel=? AND i.sku = alerts.related_sku
                    AND i.available_qty < i.safety_qty
              )
        """, (ch, ch))
        closed_rows += cur.rowcount or 0
    conn.commit()
    # 批量查现有活跃告警，避免重复。
    # key 含 channel + source，对齐 rules.py:_alert_dedup_key 口径——此前只用
    # (alert_type, related_sku)，other 渠道已有同 SKU 告警时会把 jd 的告警抑制掉（漏报）。
    existing = set()
    for r in conn.execute(
        "SELECT alert_type, channel, related_sku, source FROM alerts WHERE status='active'"
    ).fetchall():
        existing.add((r[0], r[1] or 'jd', r[2], r[3] or ''))
    inserts = []
    # SKU → 库存最多仓主体(积压研判/看板 B/C/自有 分布一致): 生成告警带 warehouse_type
    sku_wh = {}
    for r in conn.execute(
        "SELECT sku, warehouse_type, available_qty FROM inventory ORDER BY available_qty DESC").fetchall():
        if r[0] not in sku_wh:
            sku_wh[r[0]] = r[1] or ''
    # 按渠道独立判断(修复: GROUP BY sku 无 channel 过滤——jd+other 同SKU跨渠道汇总
    # 导致 avail 叠加不满足 HAVING, rules_engine 低库存告警 0 条)
    for ch in ['jd', 'other']:
        inv_rows = conn.execute("""
            SELECT sku, MAX(product_name) as name, 
                   SUM(available_qty) as avail, SUM(in_transit_qty) as transit, SUM(safety_qty) as safety
            FROM inventory 
            WHERE channel=?
            GROUP BY sku
            HAVING SUM(available_qty) < SUM(safety_qty)
                OR (SUM(available_qty) <= MAX(1, SUM(safety_qty)*0.3) 
                    AND SUM(available_qty)+SUM(in_transit_qty) <= SUM(safety_qty))
        """, (ch,)).fetchall()
        for r in inv_rows:
            sku, name = r[0], r[1] or r[0]
            avail, transit, safety = int(r[2] or 0), int(r[3] or 0), int(r[4] or 0)
            if avail < safety and (('low_stock', ch, sku, 'rules_engine') not in existing):
                existing.add(('low_stock', ch, sku, 'rules_engine'))
                inserts.append(("low_stock", f"低库存预警: {name}",
                                f"可用 {avail} < 安全线 {safety}", "warning", ch, sku, sku_wh.get(sku, '')))
            if avail <= max(1, safety * 0.3) and (avail + transit) <= safety and (('replenish', ch, sku, 'rules_engine') not in existing):
                existing.add(('replenish', ch, sku, 'rules_engine'))
                inserts.append(("replenish", f"紧急补货: {name}",
                                f"可用 {avail}（<安全线30%），含在途 {avail+transit} 仍不足安全线 {safety}", "error", ch, sku, sku_wh.get(sku, '')))
    if inserts:
        conn.executemany(
            "INSERT INTO alerts(alert_type,title,description,severity,source,channel,related_sku,status,warehouse_type) VALUES(?,?,?,?,?,?,?,?,?)",
            [(t, ti, de, se, "rules_engine", ch, sk, "active", wt) for (t, ti, de, se, ch, sk, wt) in inserts]
        )
    conn.commit()

def _seed_records(db, skus_data):
    """生成当月出入库记录（供进销存页展示）"""
    from datetime import datetime, timedelta, timezone
    import random
    conn = get_conn()
    today = datetime.now(timezone.utc)
    _batch_pool = {}
    try:
        for _r in conn.execute("SELECT sku, warehouse, prod_date, exp_date FROM batches").fetchall():
            _pool = _batch_pool.setdefault(str(_r[0]), [])
            _pool.append({'wh': str(_r[1] or ''), 'pd': str(_r[2] or '')[:10], 'ed': str(_r[3] or '')[:10]})
    except Exception: pass
    for skus, ch in [(skus_data['jd'],'jd'), (skus_data['other'],'other')]:
        for sk in skus:
            _pool = _batch_pool.get(sk['sku'], [])
            max_days = max(today.day - 1, 6)
            used_dates = set()
            in_cnt = random.randint(1, min(3, max_days + 1))
            for _ in range(in_cnt):
                days_back = random.randint(0, max_days)
                while days_back in used_dates: days_back = random.randint(0, max_days)
                used_dates.add(days_back)
                try:
                    _bp = ''; _be = ''; _wh = ''
                    if _pool:
                        _b = random.choice(_pool)
                        _bp = _b['pd']; _be = _b['ed']; _wh = _b['wh']
                    conn.execute("INSERT OR IGNORE INTO inbound_records(sku,product_name,quantity,supplier,inbound_date,channel,prod_date,exp_date,warehouse) VALUES(?,?,?,?,?,?,?,?,?)",
                        (sk['sku'], sk['name'], random.randint(50, 500), f"供应商-{sk['sku'][-3:]}",
                         (today - timedelta(days=days_back)).strftime('%Y-%m-%d'), ch, _bp, _be, _wh))
                except Exception: pass
            used_dates = set()
            out_cnt = random.randint(1, min(2, max_days + 1))
            for _ in range(out_cnt):
                days_back = random.randint(0, max_days)
                while days_back in used_dates: days_back = random.randint(0, max_days)
                used_dates.add(days_back)
                try:
                    _bp = ''; _be = ''; _wh = ''
                    if _pool:
                        _b = random.choice(_pool)
                        _bp = _b['pd']; _be = _b['ed']; _wh = _b['wh']
                    conn.execute("INSERT OR IGNORE INTO outbound_records(sku,product_name,quantity,target_warehouse,outbound_date,channel,prod_date,exp_date,warehouse) VALUES(?,?,?,?,?,?,?,?,?)",
                        (sk['sku'], sk['name'], random.randint(10, 100), "B仓",
                         (today - timedelta(days=days_back)).strftime('%Y-%m-%d'), ch, _bp, _be, _wh))
                except Exception: pass
    conn.commit()

def _sync_inv_month(conn):
    """同步出入库记录到 inventory 月汇总(month_inbound/outbound)，确保汇总态=各批次之和"""
    from datetime import datetime as _dt, timezone
    _month_start = _dt.utcnow().strftime('%Y-%m-01')
    try:
        rows = conn.execute("SELECT sku, warehouse, channel, SUM(quantity) FROM inbound_records WHERE channel IN ('jd','other') AND inbound_date >= ? GROUP BY sku, warehouse, channel", (_month_start,)).fetchall()
        for r in rows:
            try:
                conn.execute("UPDATE inventory SET month_inbound = ? WHERE sku=? AND warehouse=? AND channel=? AND warehouse_type='own'",
                    (int(r[2] or 0), str(r[0]), str(r[1] or ''), str(r[2] or '')))
            except Exception: pass
        rows2 = conn.execute("SELECT sku, warehouse, channel, SUM(quantity) FROM outbound_records WHERE channel IN ('jd','other') AND outbound_date >= ? GROUP BY sku, warehouse, channel", (_month_start,)).fetchall()
        for r in rows2:
            try:
                conn.execute("UPDATE inventory SET month_outbound = ? WHERE sku=? AND warehouse=? AND channel=? AND warehouse_type='own'",
                    (int(r[2] or 0), str(r[0]), str(r[1] or ''), str(r[2] or '')))
            except Exception: pass
        conn.commit()
    except Exception as e:
        import logging; logging.warning(f"[seed] sync inv month: {e}")
    # 同步期初库存: 用公式反推(期初=可用-入库+出库), 若为负则调低入库使期初>=0
    try:
        conn.executescript("""
            UPDATE inventory SET beginning_stock = available_qty - month_inbound + month_outbound WHERE channel IN ('jd','other') AND warehouse_type='own';
            UPDATE inventory SET month_inbound = available_qty + month_outbound WHERE channel IN ('jd','other') AND warehouse_type='own' AND beginning_stock < 0;
            UPDATE inventory SET beginning_stock = available_qty - month_inbound + month_outbound WHERE channel IN ('jd','other') AND warehouse_type='own' AND beginning_stock < 0;
        """)
        conn.commit()
    except Exception as _e:
        import logging; logging.warning(f"[seed] sync begin: {_e}")

def _seed_config(db, conn):
    conn.execute("DELETE FROM replenishment_config")
    # 恢复 jwt_secret（防止清空后重启导致 token 失效）
    try:
        _secret = os.getenv("JWT_SECRET", "")
        if _secret:
            conn.execute("INSERT OR REPLACE INTO replenishment_config(key,value,channel) VALUES('jwt_secret',?,'jd')", (_secret,))
    except Exception:
        pass
    for ch in ['jd','other']:
        configs = [
            ('lead_time_days', '10'), ('safety_multiplier', '1.5'), ('max_turnover_days', '17'),
            ('turnover_warning_15', '15'), ('turnover_warning_90', '90'),
            ('purchase_lead_days', '14'), ('purchase_safety_days', '3'), ('moq', '50'),
            ('b_to_c_days', '3'), ('c_safety_days', '0'), ('active_factor', '1.0'), ('b_free_days', '15'),
            ('target_turnover', '15'), ('_cache_version', '0'),
            # 模式参数（BBCC/传统独立配置）
            ('mode_bbcc_b_to_c_days', '3'), ('mode_bbcc_c_safety_days', '3'),
            ('mode_bbcc_safety_multiplier', '3'), ('mode_bbcc_ship_to_b_days', '3'),
            ('mode_bbcc_turnover_warning_15', '15'), ('mode_bbcc_turnover_warning_90', '90'),
            ('mode_traditional_lead_time_days', '6'), ('mode_traditional_safety_multiplier', '3'),
            ('mode_traditional_turnover_warning_90', '90'),
        ]
        for k,v in configs:
            conn.execute("INSERT OR REPLACE INTO replenishment_config(key,value,channel) VALUES(?,?,?)", (k,v,ch))
    conn.execute("DELETE FROM rules")
    rules = [
        ("低库存预警", "inventory.changed", '{"left":"inv.available_qty","op":"<","right":"inv.safety_qty"}', "low_stock", "低库存预警: {product_name}", "可用 {avail} < 安全线 {safety}", "warning", 1),
        ("紧急补货", "inventory.changed", '{"left":"inv.available_qty","op":"<=","right":"max(1,inv.safety_qty*0.3)"}', "replenish", "紧急补货: {product_name}", "可用 {avail}，低于安全线 30%", "error", 1),
        ("超卖保护", "order.created", '{"left":"order.quantity","op":">","right":"inv.available_qty"}', "oversell", "超卖告警: {sku}", "订单数量超过可用库存", "error", 1),
        ("滞销识别", "scheduled.daily", '{"left":"inv.days_since_last","op":">","right":"30"}', "slow_moving", "滞销: {product_name}", "{days} 天无销售", "warning", 1),
    ]
    for ch in ['jd','other']:
        for r in rules:
            conn.execute("INSERT INTO rules(name,event,condition_json,alert_type,alert_title,alert_desc,severity,is_active,channel) VALUES(?,?,?,?,?,?,?,?,?)", r + (ch,))
    conn.commit()

@router.post("/reset")
def seed_reset(db=get_db()):
    import uuid
    # 并发保护：已有正在执行的 seed/reset 任务时拒绝新提交
    try:
        conn = get_conn()
        busy = _check_busy(conn)
        if busy:
            return ok({"task_id": "", "message": f"已有任务进行中: {busy}，请等待完成"})
    except Exception:
        pass
    task_id = 'reset_' + uuid.uuid4().hex[:8]
    def _do_reset():
        conn = get_conn()
        # 保留历史 journal_mode=DELETE 切换(历史为 reset 提速/防 WAL 卡引入, 不轻易移除)
        # 配合一次性 DELETE(1 commit 替代分批5000×36次commit——后者是PA卡死根因)
        try: conn.execute("PRAGMA journal_mode=DELETE")
        except Exception: pass
        for t in ['orders','inventory','products','suppliers','alerts','quality_logs','events','purchase_orders','replenishment_config_history','cleansing_templates','custom_fields','daily_sales_snapshot','daily_stats','inbound_records','outbound_records','replenishment_config','rules','batches']:
            try:
                if t == 'orders':
                    # 一次性 DELETE(1次commit)——分批5000×36次commit在PA慢磁盘每次fsync是reset慢/卡死主因
                    # 本地实测: 分批5000=37s, 一次性=27s; PA慢磁盘1次vs36次fsync差距更大
                    conn.execute("DELETE FROM orders")
                    conn.commit()
                else:
                    conn.execute(f'DELETE FROM "{t}"')
                    conn.commit()
            except Exception as _e: import logging; logging.warning(f'[seed] reset {t}: {_e}')
        try:
            _secret = os.getenv("JWT_SECRET", "")
            if _secret:
                conn.execute("INSERT OR REPLACE INTO replenishment_config(key,value,channel) VALUES('jwt_secret',?,'jd')", (_secret,))
        except Exception as _e: pass
        conn.commit()
        invalidate()
        from app.core.replenishment_cache import invalidate_cache
        try: invalidate_cache(db)
        except Exception: pass
        from app.core.database import _seed_builtin_rules
        try: _seed_builtin_rules()
        except Exception: pass
    submit_task(task_id, _do_reset, task_type='reset', channel='all')
    return ok({"task_id": task_id, "reset": True})