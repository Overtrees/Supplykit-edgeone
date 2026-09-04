
from fastapi import APIRouter
from app.core.database import get_db
from app.core.response import ok, fail
from datetime import datetime, timedelta, timezone
from collections import defaultdict

router = APIRouter(prefix="/api/replenishment-config", tags=["replenishment"])

# 缓存（30s TTL，配置保存时 invalidate）
_cfg_cache = {}

@router.get("")
def get_config(mode: str = None, channel: str = 'jd', db=get_db()):
    import time
    key = f"{channel}:{mode or 'all'}"
    cached = _cfg_cache.get(key)
    if cached and time.time() - cached['ts'] < 180:
        return cached['data']
    rows = db.table("replenishment_config").select("*").eq("channel", channel).execute().data
    all_config = {r['key']: r['value'] for r in rows if not r['key'].startswith('_cache_replen_')}
    if mode:
        prefix = f'mode_{mode}_'
        result = ok({k[len(prefix):]: v for k, v in all_config.items() if k.startswith(prefix)})
    else:
        result = ok(all_config)
    _cfg_cache[key] = {'data': result, 'ts': time.time()}
    return result

@router.put("")
def update_config(data: dict, mode: str = '', channel: str = 'jd', db=get_db()):
    _cfg_cache.clear()
    try:
        from datetime import datetime, timezone
        v = db.table("replenishment_config").select("*").eq("key", "_cfg_version").execute().data
        nv = (int(v[0]["value"]) + 1) if v and v[0].get("value") else 1
        db.table("replenishment_config").upsert({"key": "_cfg_version", "value": str(nv), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except: pass  # 配置变更，清空缓存
    # 配置变更(含滞销参数 slow_cats_config) → 递增 _replen_version, 让滞销/补货缓存失效
    try:
        _rv = db.table("replenishment_config").select("*").eq("key", "_replen_version").execute().data
        _rnv = (int(_rv[0]["value"]) + 1) if _rv and _rv[0].get("value") else 1
        db.table("replenishment_config").upsert({"key": "_replen_version", "value": str(_rnv), "channel": "jd", "updated_at": datetime.now(timezone.utc).isoformat()}, conflict_col='key')
    except: pass
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    if mode:
        prefix = f'mode_{mode}_'
        for k, v in data.items():
            full_key = prefix + k
            existing = db.table("replenishment_config").select('value').eq('key', full_key).eq('channel', channel).execute().data
            old_val = existing[0]['value'] if existing else ''
            if str(old_val) != str(v):
                db.table("replenishment_config_history").insert({
                    'key': full_key, 'old_value': str(old_val), 'new_value': str(v),
                    'channel': channel, 'mode': mode, 'created_at': now
                }).execute()
            db.table("replenishment_config").upsert({"key": full_key, "value": str(v), "channel": channel, "updated_at": now}, conflict_col='key')
    else:
        for k, v in data.items():
            existing = db.table("replenishment_config").select('value').eq('key', k).eq('channel', channel).execute().data
            old_val = existing[0]['value'] if existing else ''
            if str(old_val) != str(v):
                db.table("replenishment_config_history").insert({
                    'key': k, 'old_value': str(old_val), 'new_value': str(v),
                    'channel': channel, 'mode': '', 'created_at': now
                }).execute()
            db.table("replenishment_config").upsert({"key": k, "value": str(v), "channel": channel, "updated_at": now}, conflict_col='key')
    return ok({'mode': mode, 'channel': channel})


@router.get('/history')
def get_config_history(channel: str = 'jd', mode: str = '', limit: int = 50, db=get_db()):
    query = db.table("replenishment_config_history").select('*').eq('channel', channel).order('created_at', desc=True).limit(limit)
    if mode:
        query = query.eq('mode', mode)
    return ok(query.execute().data)


@router.get('/seasons')
def get_seasons(mode: str = 'bbcc', channel: str = 'jd', db=get_db()):
    import json
    key = f'season_config_{mode}'
    val = db.table('replenishment_config').select('*').eq('key', key).eq('channel', channel).execute().data
    if val and val[0].get('value'):
        return json.loads(val[0]['value'])
    return ok([
        {'key':'618','name':'618','factor':1.5,'enabled':False},
        {'key':'1111','name':'双11','factor':1.8,'enabled':False},
        {'key':'cny','name':'年货节','factor':1.6,'enabled':False},
    ])

@router.put('/seasons')
def update_seasons(data: dict, mode: str = 'bbcc', channel: str = 'jd', db=get_db()):
    import json
    items = data.get('items', data.get('seasons', []))
    val = json.dumps(list(items), ensure_ascii=False)
    key = f'season_config_{mode}'
    existing = db.table("replenishment_config").select('value').eq('key', key).eq('channel', channel).execute().data
    old_val = existing[0]['value'] if existing else ''
    if old_val != val:
        db.table("replenishment_config_history").insert({
            'key': key, 'old_value': old_val, 'new_value': val,
            'channel': channel, 'mode': mode, 'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        }).execute()
    db.table("replenishment_config").upsert({"key": key, "value": val, "channel": channel, "updated_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}, conflict_col='key')
    return ok(items)
@router.get('/slow-cats')
def get_slow_cats(channel: str = 'jd', db=get_db()):
    """滞销品类配置（自定义条目: 名称/滞销线/临期线/品类名单/开关）"""
    import json
    val = db.table("replenishment_config").select('*').eq('key', 'slow_cats_config').eq('channel', channel).execute().data
    if val and val[0].get('value'):
        try:
            d = json.loads(val[0]['value'])
            if isinstance(d, list):
                return ok(d)
        except Exception:
            pass
    # 默认：食品 + 个护家清两类
    return ok([
        {'key': 'food', 'name': '食品', 'slow_days': 30, 'shelf_months': 3,
         'cats': '酱油,酱料,调味汁,食用油,醋,料酒,蚝油,芝麻油,辣椒酱,拌面酱,老抽,生抽,陈醋,香醋,白醋,米醋,花椒油,藤椒油,辣椒油,芥末油,番茄酱,甜辣酱,沙拉酱,芝麻酱,花生酱,豆瓣酱,豆豉,腐乳,糟卤,鱼露,咖喱块,咖喱粉,五香粉,孜然粉,花椒粉,辣椒粉,胡椒粉,十三香,卤料包,炖肉料,鸡精,味精,白糖,冰糖,红糖,麦芽糖,蜂蜜,黄酒,米酒,薯片,虾条,爆米花,坚果,瓜子,花生,饼干,威化,巧克力,糖果', 'enabled': True},
        {'key': 'home', 'name': '个护家清', 'slow_days': 60, 'shelf_months': 6,
         'cats': '洗衣液,洗洁精,洗手液,消毒液,纸巾,湿巾,垃圾袋,保鲜膜,保鲜袋,收纳盒', 'enabled': True},
    ])


@router.put('/slow-cats')
def update_slow_cats(data: dict, channel: str = 'jd', db=get_db()):
    import json
    items = data.get('items', [])
    val = json.dumps(list(items), ensure_ascii=False)
    existing = db.table("replenishment_config").select('value').eq('key', 'slow_cats_config').eq('channel', channel).execute().data
    old_val = existing[0]['value'] if existing else ''
    if old_val != val:
        db.table("replenishment_config_history").insert({
            'key': 'slow_cats_config', 'old_value': old_val, 'new_value': val,
            'channel': channel, 'mode': '', 'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        }).execute()
    db.table("replenishment_config").upsert({"key": "slow_cats_config", "value": val, "channel": channel, "updated_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}, conflict_col='key')
    return ok(items)


@router.get('/calculate')
def calculate(mode: str = 'bbcc', db=get_db()):
    prefix = f'mode_{mode}_'
    rows = db.table("replenishment_config").select("*").execute().data
    raw = {r['key']: r['value'] for r in rows}
    cfg = {}
    for k, v in raw.items():
        if k.startswith(prefix):
            cfg[k[len(prefix):]] = v
    lt = int(cfg.get('lead_time_days','10'))
    sm = float(cfg.get('safety_multiplier','1.0'))
    cutoff = (datetime.now(timezone.utc)-timedelta(days=30)).strftime('%Y-%m-%d')
    sku_s = defaultdict(int)
    for o in db.table("orders").select("*").execute().data:
        if o.get("deleted_at"): continue
        s = o.get('sku','')
        if s and str(o.get('ordered_at',''))[:10] >= cutoff:
            sku_s[s] += int(o.get('quantity',0) or 0)
    invs = db.table("inventory").select("*").execute().data
    sku_i = defaultdict(lambda: {'a':0,'t':0,'sf':0})
    for inv in invs:
        s = inv.get('sku','')
        if not s: continue
        sku_i[s]['a'] += int(inv.get('available_qty',0) or 0)
        sku_i[s]['t'] += int(inv.get('in_transit_qty',0) or 0)
        sku_i[s]['sf'] = max(sku_i[s]['sf'], int(inv.get('safety_qty',0) or 0))
    res = []
    for s,v in sku_i.items():
        d = round(sku_s.get(s,0)/30,1)
        sf = round(v['sf']*sm) if v['sf']>0 else round(d*(lt+2))
        sug = max(round(d*lt+sf-v['a']-v['t']),0)
        tot = v['a']+v['t']+sug
        td = round(tot/d,1) if d>0 else 999
        res.append({'sku':s,'daily':d,'stock':v['a'],'transit':v['t'],'safety':sf,'suggested':sug,'after':tot,'turnover':td})
    return ok({'config': cfg, 'items': sorted(res, key=lambda x: x['turnover'])})
