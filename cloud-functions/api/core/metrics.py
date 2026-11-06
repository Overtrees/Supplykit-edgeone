"""core/metrics.py —— 库存判定口径**单一真源**(2026-11-06 S3)

## 它回答三个问题

1. **这个桶卖多快**(`ds`): 维度由"判定对象"决定, **只有 69码 可降级**。
2. **该备多少**(`safety_line`): `ds × 周期`; 手工 `safety_qty > 0` 优先。
3. **够不够**(`judge`): 可用为 0 → 断货; 覆盖量 < 安全线 → 低库存。

## 为什么必须有它

同一句话在 5 个地方各写了一遍, 每处都"看起来对", 但放在同一张卡上就互相打脸:

| 消费方 | 它怎么取的 ds | 后果 |
|---|---|---|
| 看板健康分 `_health_index` | SKU 级 `__C__` 日销, 对**每一行**(含 own 仓、每个 69码) | 每个仓都拿去比"整盘"需求 → 逐仓分虚低 |
| 看板低库存 `_block_low` | 同上 | 每个仓的可用量都去比整盘安全线 → 越小的仓越"缺货" |
| 看板低库存 `_assemble` | 同上, 但过滤条件还不一样(avail>0) | 同一件事三个数 |
| 规则引擎 `rule_eval` | 已按维度(正确), 用 **ds28** | 卡片用 fused、告警用 ds28 → 同一 SKU 卡片说黄、告警说没事 |
| 采购/补货 | 各自一套 ds 变体 | 变体是有意的(削峰/shrink), 但**维度解析**又各写一遍 |

这不是"算错了一个数", 而是**同一个词在两处指两个东西**。所以本模块把
「桶怎么归并 / 日销取哪个维度 / 安全线多长 / 状态怎么分档」四件事各只写一次。

## 四条铁律

1. **判定用 ds28(近 28 天日均)**, 与告警规则同源; `fused`(三窗口趋势融合)只用于
   "断货风险/补货建议"这类**趋势问题**, 不参与安全线 —— 否则卡片和告警永远差一档。
2. **维度是判定对象, 不参与降级**。BC 盘就整盘算, 判单仓就整仓算 —— 换维是换问题,
   不是"少一点精度"。只有 69码 缺失时才回落到该维合计。
3. **绝不拿全国兜底**。该维无日销 → `ds=0` → 不触发(而不是"用全国的数凑一个")。
4. **停用 SKU 不进缺口**(`state=paused`), 库存照常呈现。判定每轮现查 `products.status`,
   重新启用会自动恢复 —— "动态识别"不是缓存一个开关。

## 关于 `include_transit`

"够不够"用**覆盖量 = 可用 + 在途**(`cover`), 与规则引擎的
`inv.available_qty + inv.in_transit_qty < inv.safety_qty` 同口径;
而"断货"只看**可用**(`avail <= 0`) —— 在途的货还没到, 不能算断货。
看板原来只用 `avail` 比安全线, 于是同一批 SKU 在告警列表里是低库存、在健康卡里是健康。
业务可用 `stockout.include_transit=0` 关掉(默认 1)。

## 关于"取哪几列"

`load()` 只取调用方点名的列。这不是微优化: 同一份 9,003 行, 12 列 1.44s / 6 列 0.45s
(线上实测, 见 `_q_inventory` 的注释) —— **传输量才是这里的成本**, 不是扫描。
单仓判定只要 `ds28` 一列, 就别把 7 列 12 万行搬回来。
"""
from core.clock import now_cn
from db import query
from routes.common import try_err

# ── 判定对象(桶)──────────────────────────────────────────────────
DIM_BC = "bc"     # BC 盘: platform + platform_b 合计 —— 一盘货, 一个单元
DIM_WH = "wh"     # 单仓: 一个仓位一个单元
DIM_OWN = "own"   # 自有仓(集货仓): 逐仓一个单元, 判的是"给下游备的货够不够"

# ── 状态(互斥)───────────────────────────────────────────────────
ST_OUT = "out"        # 可用 <= 0 —— 断货(单列, 不混进低库存)
ST_LOW = "low"        # 覆盖量 < 安全线
ST_OK = "ok"          # 覆盖量 >= 安全线
ST_IDLE = "idle"      # 无日销(新品类/停售) → 安全线 0, 不判库存高低
ST_PAUSED = "paused"  # 商品停用 → 不进缺口

# 健康 = ok + idle(无销量不判不健康) —— 与改动前 `_dyn<=0 or avail>=_dyn` 等价
HEALTHY_STATES = (ST_OK, ST_IDLE)
RISK_STATES = (ST_LOW,)

# 兜底(渠道配置读不到时): jd 是 BC 一盘货链路, 其余按传统逐仓
_CHANNEL_MODE = {"jd": "bbcc"}


def mode_of(channel, mode=None):
    """渠道 → 补货模式。显式传入优先; 未传时查渠道配置(**不写死 jd**)

    原代码把默认值写死在函数签名里(`_health_index(channel, mode="bbcc")`),
    而 cron 每小时对**每个渠道**调 `_health_index(ch)` → other 用 jd 的 12 天周期算安全线,
    判出来的"健康"是假的。默认值不该由函数签名替业务回答。
    """
    if mode in ("bbcc", "traditional"):
        return str(mode)
    try:
        from routes.common import ch_default_mode
        return str(ch_default_mode(channel) or "traditional")
    except Exception:
        return _CHANNEL_MODE.get(str(channel or ""), "traditional")


def periods(cfg):
    """安全线周期(天)单一来源 —— 由 `replenishment_config` 计算

    三个消费方原来各写一遍同样的五行, 连"mode 传哪个"都各不相同。返回:
      bbcc        C→B→C 全周期 = b_to_c + c_safety + ship_to_b + safety_multiplier
      traditional 传统补货周期 = lead_time + safety_multiplier
      lead        供应商到仓的提前期(**只** lead_time) —— σ 安全库存的地平线
      purchase    采购周期 = purchase_lead + purchase_safety
    取整保持原样(`int(float(v))`): 历史阈值都是整天, 改 float 会动线上数。
    """
    cfg = cfg or {}

    def _m(key, mode, default):
        v = cfg.get("mode_%s_%s" % (mode, key))
        if v is None:
            v = cfg.get(key)
        try:
            return int(float(v if v is not None else default))
        except Exception:
            return int(default)

    return {
        "bbcc": (_m("b_to_c_days", "bbcc", 3) + _m("c_safety_days", "bbcc", 0)
                 + _m("ship_to_b_days", "bbcc", 0) + _m("safety_multiplier", "bbcc", 0)),
        # C→B 这一段(B 仓补货周期): 自有仓"该给下游备多少"用的就是它
        "b2c": _m("b_to_c_days", "bbcc", 3) + _m("c_safety_days", "bbcc", 0),
        "traditional": _m("lead_time_days", "traditional", 10) + _m("safety_multiplier", "traditional", 0),
        "lead": _m("lead_time_days", "traditional", 10),
        "purchase": _m("purchase_lead_days", "bbcc", 14) + _m("purchase_safety_days", "bbcc", 3),
    }


def period_of(kind, channel, mode=None, p=None):
    """桶类型 → 安全线周期(天)

    · 自有仓判的是"给下游备多少" → 采购周期
    · BC 盘 / 平台仓判的是"补到仓" → 渠道模式的补货周期(bbcc 全周期 / 传统周期)
    """
    p = p or periods({})
    if kind == DIM_OWN:
        return float(p.get("purchase") or 0)
    if mode_of(channel, mode) == "bbcc":
        return float(p.get("bbcc") or 0)
    return float(p.get("traditional") or 0)


def dim_of(kind, warehouse="", wt=""):
    """桶 → 查日销用的 `sku_sales_stats.warehouse` 维度标签

    · BC 盘读 `__C__`: B 仓是调拨中转, **不产生销售**(用户定案方案 B) ——
      读 `__BC__` 会让 B 仓将来出现杂单时把安全线抬高 → 断货漏判。
    · 单仓读自己; 自有仓读 `__C__` —— 它自己零销量, 要的是**下游**需求。
    · B 仓(platform_b)也读 `__C__`: 它是 C 仓的上游中转, 判它够不够是拿"下游要多少"比,
      不是拿它自己的销量(恒 0)。原实现读全渠道 ds, 结论方向对但维度名不对。
    """
    if kind in (DIM_BC, DIM_OWN) or str(wt or "") == "platform_b":
        from biz.sales import WH_C
        return WH_C
    return str(warehouse or "")


# ── 判定粒度 ─────────────────────────────────────────────────────
# 粒度 = "一个判定单元是什么", 必须与渠道的补货模式一致, 且**同一渠道只许一个**:
#   bbcc        SKU×69码 —— BC 是一盘货, 平台按码卖/按码对账; 两个码的库存相加而日销只取
#               一个, 单元数与数量都对不上(旧 P0-14 就是这么来的)
#   traditional SKU     —— 逐仓按 SKU 备货; 码只是同货不同条码, 拆开判会把同一个缺口报两次
# 可覆盖: replenishment_config 的 `mode_<mode>_grain` 优先, 其次 `grain`(sku / barcode)
GRAIN_SKU = "sku"
GRAIN_BARCODE = "barcode"
_CFG_TTL = 60
_CFG_CACHE = {}   # channel → (ts, {key: value})


def channel_cfg(channel, ttl=_CFG_TTL):
    """该渠道的 replenishment_config(进程内 60s)。`channel=''` 是全局默认, 渠道行覆盖它

    只给"必须自己回答"的判定用(粒度)。周期/参数仍由调用方把手里的 cfg 传进 periods(),
    这样一次请求里只读一份配置, 不会出现"前半段按旧值、后半段按新值"。
    """
    import time as _t
    _k = str(channel or "")
    _now = _t.time()
    _hit = _CFG_CACHE.get(_k)
    if _hit and _now - _hit[0] < ttl:
        return _hit[1]
    cfg = {}
    for _ch in (("",) if not _k else ("", _k)):
        try:
            for r in query("SELECT `key`, value FROM replenishment_config WHERE channel=%s", [_ch]):
                if r.get("key"):
                    cfg[str(r.get("key"))] = r.get("value")   # 渠道行覆盖全局默认
        except Exception as _e:
            try_err("metrics", "渠道配置读取降级(%s)" % (_ch or "默认"), _e)
    _CFG_CACHE[_k] = (_now, cfg)
    return cfg


def grain_of(channel, mode=None, cfg=None):
    """渠道 → 判定粒度(`barcode` / `sku`)。**唯一**的粒度决定处

    为什么不能各调用点自己写 `by_barcode=True/False`:
      同一个渠道, 规则引擎按码判、看板按 SKU 数, 两个数放在一张卡上必然对不上
      —— 这正是 S3 要消灭的"同一个词指两个东西"。
    默认跟随补货模式(bbcc→码 / 其他→SKU); 业务要改就写配置:
      `mode_bbcc_grain` / `mode_traditional_grain` / `grain`(取值 sku / barcode)
    """
    m = mode_of(channel, mode)
    _cfg = channel_cfg(channel) if cfg is None else (cfg or {})
    for _k in ("mode_%s_grain" % m, "grain"):
        v = str(_cfg.get(_k) or "").strip().lower()
        if v in (GRAIN_SKU, GRAIN_BARCODE):
            return v
    return GRAIN_BARCODE if m == "bbcc" else GRAIN_SKU


def invalidate_cfg(channel=""):
    """清配置缓存(配置保存后调用)"""
    if not channel:
        _CFG_CACHE.clear()
        return
    _CFG_CACHE.pop(str(channel), None)


# ── 日销序列(一次取回, 只取点名的列)────────────────────────────────
_SERIES_TTL = 30
_SERIES = {}   # (channel, dims, with_barcode, days, fields) → (ts, Series)


class Series(object):
    """某渠道的日销切片。`value()` 实现"只降码、不降维"的取值规则与降级顺序。"""
    __slots__ = ("rows", "channel", "fields")

    def __init__(self, channel, rows, fields=("ds28",)):
        self.channel = channel
        self.rows = rows            # {(sku, barcode, dim): {field: value}}
        self.fields = tuple(fields)

    def value(self, field, sku, barcode, dim, default=0.0):
        """①该维该码 ②该维 SKU 合计 ③default

        判 `is not None` 而非 truthy: 该码有记录但销量为 0 就是 0,
        不该被合计顶上来(否则"这个码不卖了"会被当成"没细分到码")。
        """
        if barcode:
            v = self._one(field, sku, barcode, dim)
            if v is not None:
                return v
        v = self._one(field, sku, "", dim)
        return default if v is None else v

    def _one(self, field, sku, barcode, dim):
        r = self.rows.get((str(sku), str(barcode or ""), str(dim)))
        if r is None:
            return None
        return r.get(field)

    def by_sku(self, field="ds28", dim=None):
        """{sku: value}(单维, SKU 合计行) —— 兼容老的 `{sku: ds}` 消费习惯"""
        if dim is None:
            from biz.sales import WH_C
            dim = WH_C
        out = {}
        for (sku, bc, d), r in self.rows.items():
            if bc == "" and d == dim:
                out[sku] = r.get(field) or 0.0
        return out

    def dims(self):
        return {d for (_s, _b, d) in self.rows}

    def sigma_by_sku(self, dim=None):
        """{sku: σ} —— 需求波动, 只认 SKU 合计行(逐码样本太短, σ 不可靠)"""
        if dim is None:
            from biz.sales import WH_C
            dim = WH_C
        out = {}
        for (sku, bc, d), r in self.rows.items():
            if bc == "" and d == dim:
                _v = r.get("sigma") or 0.0
                if _v > 0 and sku not in out:
                    out[sku] = _v
        return out

    @classmethod
    def from_sku_map(cls, channel, m, field="ds28"):
        """{sku: 值} → Series(全部挂在 `__C__` 维的 SKU 合计行上)

        只为兼容老调用方传进来的 `_ds_map`(测试也这么注)。生产路径一律走 `load()`,
        因为那种 map 丢掉了**维度**信息 —— 正是本模块要消灭的东西。
        """
        from biz.sales import WH_C
        rows = {}
        for k, v in (m or {}).items():
            try:
                rows[(str(k), "", WH_C)] = {field: float(v or 0)}
            except Exception:
                continue
        return cls(channel, rows, (field,))


def load(channel, dims=None, with_barcode=False, days=28, fields=("ds28",),
         ttl=_SERIES_TTL, fresh=False):
    """取该渠道日销切片(进程内 30s) —— **唯一**的日销读取入口

    dims         需要的维度标签集合(None = 全渠道)。按维度下推 SQL ——
                 看板 bbcc 只要 `__C__`(2 万行), 不必把 12 万行全拉回来。
    with_barcode 是否带按码行。默认 False(只取 SKU 合计行): 库存表里 9,900/10,000 个
                 SKU 只有一个 69码, 逐码判定与按 SKU 判定 99% 重合, 却要多搬一倍行。
                 规则引擎要按码判(**告警是 SKU×码×仓**), 那里显式传 True。
    fields       只取点名的列 —— 传输量是这里的唯一成本(见模块 docstring)。
    fresh        跳过进程内缓存(写后重算用)
    """
    import time as _t
    _dims = frozenset(str(d) for d in dims) if dims else None
    _flds = tuple(fields or ("ds28",))
    _key = (str(channel), _dims, bool(with_barcode), int(days), _flds)
    _now = _t.time()
    if not fresh:
        _hit = _SERIES.get(_key)
        if _hit and _now - _hit[0] < ttl:
            return _hit[1]
    _sel = ", ".join("`%s`" % f for f in _flds)
    _sql = ("SELECT sku, COALESCE(barcode,'') AS barcode, warehouse, " + _sel +
            " FROM sku_sales_stats WHERE channel=%s")
    _p = [channel]
    if _dims:
        _sql += " AND warehouse IN (" + ",".join(["%s"] * len(_dims)) + ")"
        _p += sorted(_dims)
    if not with_barcode:
        _sql += " AND COALESCE(barcode,'')=''"
    rows = {}
    try:
        for r in query(_sql, _p):
            _row = {}
            for f in _flds:
                try:
                    _row[f] = float(r.get(f) or 0)
                except Exception:
                    _row[f] = 0.0
            rows[(str(r.get("sku") or ""), str(r.get("barcode") or ""),
                  str(r.get("warehouse") or ""))] = _row
    except Exception as _e:
        try_err("metrics", "日销切片读取降级(%s)" % channel, _e)
        return Series(channel, {}, _flds)
    s = Series(channel, rows, _flds)
    _SERIES[_key] = (_now, s)
    return s


def invalidate(channel=""):
    """清进程内日销切片(写操作后调用; 不传 = 全清)"""
    if not channel:
        _SERIES.clear()
        return
    for k in list(_SERIES.keys()):
        if k[0] == str(channel):
            _SERIES.pop(k, None)


def dims_needed(inv_rows, channel, mode=None):
    """这批库存行需要哪几个日销维度 —— 单仓判定按仓取, BC/自有仓按 `__C__` 取

    少了维度 = 那些仓的 ds 恒 0 = 永远不触发(旧的"漏整个 other 渠道"就是这么来的);
    多了维度 = 白搬几万行。所以按实际出现的仓型算, 不写死。
    `inv_rows=None` → 自己查一次仓型枚举(8 行, 可忽略)。
    """
    from biz.sales import WH_C
    _m = mode_of(channel, mode)
    out = {WH_C}
    if inv_rows is None:
        try:
            inv_rows = query("SELECT DISTINCT warehouse, warehouse_type FROM inventory "
                             "WHERE channel=%s AND warehouse IS NOT NULL AND warehouse!=''",
                             [channel])
        except Exception as _e:
            try_err("metrics", "仓型枚举降级(%s)" % channel, _e)
            inv_rows = []
    for r in inv_rows or []:
        wt = str(r.get("warehouse_type") or "")
        w = str(r.get("warehouse") or "")
        if not w:
            continue
        if wt == "own":
            out.add(WH_C)          # 自有仓要的是下游需求
        elif wt == "platform_b" and _m != "bbcc":
            continue               # 传统模式 B 仓不参与判定
        else:
            out.add(w)             # 单仓判定: 该仓自己的日销
    return out


# ── 归桶(库存行 → 判定单元)────────────────────────────────────────
def units(inv_rows, merge_bc=False, with_bc=False, drop_platform_b=False,
          grain=GRAIN_BARCODE, status=None):
    """库存行 → 判定单元。**唯一**的归并规则。

    merge_bc          platform + platform_b 合并成 `DIM_BC`, 平台行从输出剔除(规则引擎用)
    with_bc           保留平台行, **另外**追加 BC 单元(健康卡四档要四个口径并存)
    drop_platform_b   传统模式下 B 仓不参与(它只是 jd 的中转仓, 不是判定对象)
    grain             单元粒度: `barcode`=(SKU,69码) / `sku`=按 SKU 合计。
                      由 `grain_of(channel, mode)` 决定(**不要**在各调用点手写 True/False,
                      否则同一渠道的告警与看板粒度不同)
    status            {sku: products.status}; 传了才标 `paused`(停用不进缺口)

    ⚠️ 与旧实现的差别(都是修 bug, 不是换口径):
      · BC 行的分子**只含 platform + platform_b**, 不再把 own 仓库存加进来(P0-13) ——
        旧 `_agg` 按 SKU 汇总所有仓型, 于是"BC 盘可售"里混着集货仓的货。
      · BC 单元按 (SKU, 69码) 建键, 不是按 SKU —— 旧实现两个码的库存相加、日销却取一个,
        单元数还按 SKU 算, 与 platform 的"按行"不同粒度, 两个数字不可比(P0-14(2))。
    """
    by_barcode = str(grain or GRAIN_BARCODE) == GRAIN_BARCODE
    out = []
    bc = {}

    def _key(sku, barcode, kind, wh):
        return (sku, barcode if by_barcode else "", kind if kind != DIM_WH else (DIM_WH, wh))

    _idx = {}

    def _emit(sku, barcode, kind, wh, wt, name, qty, tr, manual):
        k = _key(sku, barcode, kind, wh)
        u = _idx.get(k)
        if u is None:
            u = _idx[k] = {"sku": sku, "barcode": barcode, "kind": kind, "warehouse": wh,
                           "warehouse_type": wt, "name": name or "",
                           "avail": 0, "transit": 0, "manual_safety": 0}
            out.append(u)
        u["avail"] += qty
        u["transit"] += tr
        # 手工安全线取 max: 同 SKU 多行(不同仓/不同码)合并时, 任一行设了线就按最高的来,
        # 不能相加(那是把"每个仓都该备 N"错算成"总共备 N×仓数")
        if manual and manual > u["manual_safety"]:
            u["manual_safety"] = int(manual)
        if not u["name"]:
            u["name"] = name or ""

    for r in inv_rows or []:
        sku = str(r.get("sku") or "")
        if not sku:
            continue
        wt = str(r.get("warehouse_type") or "")
        if drop_platform_b and wt == "platform_b":
            continue
        _bc = str(r.get("barcode") or "")
        _q = int(r.get("available_qty") or 0)
        _t = int(r.get("in_transit_qty") or 0)
        _m = int(r.get("safety_qty") or 0)
        _n = str(r.get("product_name") or "")
        _w = str(r.get("warehouse") or "")
        if not _w:
            continue              # 空仓归属是脏数据(导入错行), 判它"缺货"只会制造噪音
        if wt in ("platform", "platform_b"):
            if merge_bc or with_bc:
                k = (sku, _bc if by_barcode else "")
                u = bc.get(k)
                if u is None:
                    u = bc[k] = {"sku": sku, "barcode": k[1], "kind": DIM_BC, "warehouse": "__BC__",
                                 "warehouse_type": "bc", "name": _n,
                                 "avail": 0, "transit": 0, "manual_safety": 0}
                u["avail"] += _q
                u["transit"] += _t
                if _m > u["manual_safety"]:
                    u["manual_safety"] = _m
                if not u["name"]:
                    u["name"] = _n
            if merge_bc:
                continue          # 平台行由 BC 行代表
        _emit(sku, _bc, DIM_OWN if wt == "own" else DIM_WH, _w, wt, _n, _q, _t, _m)
    out.extend(bc.values())
    if status:
        for u in out:
            if str(status.get(u["sku"]) or "active") != "active":
                u["paused"] = True
    return out


# ── 安全线 / 判定 ────────────────────────────────────────────────
def safety_line(ds, period, manual=0):
    """安全线 = 手工 safety_qty(>0 时优先) 否则 ds × 周期

    判 `> 0` 而不是"字段非空": 老数据里 `safety_qty` 默认 0 表示"没设过",
    0 是合法的"不设线", 不能当成"设了 0 天"。
    返回 (线, 来源) —— 来源进日志, 让"这条为什么报警"可回答。
    """
    try:
        m = float(manual or 0)
    except Exception:
        m = 0.0
    if m > 0:
        return m, "manual"
    d = float(ds or 0)
    p = float(period or 0)
    if d <= 0 or p <= 0:
        return 0.0, "none"
    return round(d * p, 1), "dynamic"


def judge(u, ds, p, channel, mode=None, include_transit=True, sigma=0.0, z=1.65,
          otif=1.0, accel=1.0):
    """一个单元 → 一个结论(所有视图共用这一个)

    返回 dict:
      ds/period/line/line_src  口径三元组(可解释)
      qty/transit/cover        量(cover = qty + transit; 断货只看 qty)
      state                    out / low / ok / idle / paused
      gap                      缺口 = line - cover(>0 才需要补)
      adj_dos                  可撑天数 = (avail + transit×otif) / ds
      buffer                   缓冲倍数 = qty / max(line, ss_dyn, 1)
      ss_dyn                   σ 安全库存 = z × σ × √提前期
    """
    qty = int(u.get("avail") or 0)
    tr = int(u.get("transit") or 0)
    cover = qty + tr if include_transit else qty
    per = period_of(u.get("kind"), channel, mode, p)
    line, src = safety_line(ds, per, u.get("manual_safety"))
    _lead = float((p or {}).get("lead") or 0) or 1.0
    ss = float(z) * float(sigma or 0) * (_lead ** 0.5)
    if qty <= 0:
        state = ST_OUT
    elif u.get("paused"):
        state = ST_PAUSED
    elif line <= 0:
        state = ST_IDLE
    elif cover < line:
        state = ST_LOW
    else:
        state = ST_OK
    return {
        "ds": round(float(ds or 0), 3), "period": per, "line": line, "line_src": src,
        "qty": qty, "transit": tr, "cover": cover, "state": state,
        "gap": round(max(line - cover, 0.0), 1),
        "adj_dos": round((qty + tr * float(otif or 1.0)) / float(ds), 2) if float(ds or 0) > 0 else 999.0,
        "buffer": round(qty / max(max(line, ss), 1.0), 2),
        "ss_dyn": round(ss, 1),
        "accel_rate": round(float(accel or 1.0), 2),
    }


def judge_units(us, series, channel, mode=None, metric="ds28", include_transit=True,
                z=1.65, p=None):
    """批量判定: units → [(unit, verdict)]。ds 维度按桶类型自动解析。"""
    p = p or periods({})
    out = []
    for u in us:
        dim = dim_of(u.get("kind"), u.get("warehouse"), u.get("warehouse_type"))
        ds = series.value(metric, u["sku"], u.get("barcode"), dim) if series is not None else 0.0
        out.append((u, judge(u, ds, p, channel, mode, include_transit=include_transit, z=z)))
    return out


def counts(pairs, kinds=None, wt=None):
    """[(unit, verdict)] → 分档计数。kinds 过滤桶类型, wt 过滤仓型(None = 全部)"""
    c = {"total": 0, "ok": 0, "low": 0, "out": 0, "idle": 0, "paused": 0}
    for u, v in pairs:
        if kinds and u.get("kind") not in kinds:
            continue
        if wt is not None and str(u.get("warehouse_type") or "") != str(wt):
            continue
        c["total"] += 1
        c[v["state"]] = c.get(v["state"], 0) + 1
    return c


def score(c, good=85.0, warning=60.0):
    """计数 → 健康分(健康率)。total=0 → score=None/level=empty(前端显示"—", 不给 100 分假象)

    口径: 健康 = ok + idle(无销量不判不健康); 停用(paused)不进分母 ——
    否则停掉一半 SKU 会让分数凭空掉一半, 而"停用"本来就不是库存问题。
    """
    total = int(c.get("total") or 0) - int(c.get("paused") or 0)
    if total <= 0:
        return {"score": None, "healthy": 0, "warning": 0, "out_of_stock": 0, "total": 0,
                "level": "empty"}
    healthy = int(c.get("ok") or 0) + int(c.get("idle") or 0)
    s = round(healthy / total * 100.0, 0)
    return {"score": s, "healthy": healthy, "warning": int(c.get("low") or 0),
            "out_of_stock": int(c.get("out") or 0), "total": total,
            "level": "good" if s >= good else ("warning" if s >= warning else "danger")}


# ── 自有仓供给侧(own_transfer_gap / own_overstock 规则用)───────────
def own_need(ds_down, b2c_days, cover_mult=1.0):
    """自有仓"一个调拨周期该备多少" = 下游日销 × 下游周期 × 覆盖倍数"""
    return float(ds_down or 0) * float(b2c_days or 0) * float(cover_mult or 1.0)


def own_gap(need, can_ship):
    """自有仓缺口 = 应备 − 可发(在仓+在途)。>0 即调拨覆盖不足"""
    return float(need or 0) - float(can_ship or 0)


def status_map(channel):
    """{sku: products.status} —— 停用判定**现查**, 不缓存(否则"重新启用"要等缓存过期才恢复)"""
    out = {}
    try:
        for r in query("SELECT sku, status FROM products WHERE channel=%s "
                       "AND (deleted_at IS NULL OR deleted_at='')", [channel]):
            out[str(r.get("sku") or "")] = str(r.get("status") or "active")
    except Exception as _e:
        try_err("metrics", "商品状态读取降级(%s)" % channel, _e)
    return out


def include_transit_default():
    """在途是否计入覆盖量 —— 默认 True(与告警规则同口径), 可由 stockout 规则参数关闭"""
    try:
        from routes.dashboard_stock_risk import _rule_params
        v = (_rule_params("stockout") or {}).get("include_transit", 1)
        return str(v) not in ("0", "false", "False", "")
    except Exception:
        return True


# ── 超卖口径 ────────────────────────────────────────────────────────────────
# 超卖 = "卖出去还没发、而仓里不够发"。判定量**不是**历史累计销量:
#   只要卖过一阵, 累计量必然大于当前可用 → 每个 SKU 都超卖。
#   线上实测(other): 全历史 1,783 / 未发货 1,441 / 未发货且近 14 天 390 / 近 7 天 57。
# 只算未发货状态(已发货的货在 ERP 里早扣过库存了, 再算一次就是双计),
# 且只算近 N 天下单的 —— 挂三个月没发的单是异常单, 它的库存早被别的单吃掉或已补回,
# 拿它判"现在发不出货"是错的。
OPEN_ORDER_STATUSES = ("待确认", "待发货")
OVERSELL_DAYS = 14


def oversell_window(params=None):
    """→ (天数, 未发货状态元组)。rules.params.oversell_days 优先, 默认 14

    三个消费方共用这一份: 评估(ctx 构造) / 删单联动重评 / 关单联动重评 ——
    原来三处各写一遍 `SUM(quantity)`, 于是"评估说超卖、关单说没超卖", 告警永远关不掉。
    """
    d = OVERSELL_DAYS
    try:
        v = (params or {}).get("oversell_days")
        if v is not None:
            d = max(1, min(int(float(v)), 365))
    except Exception:
        pass
    return d, OPEN_ORDER_STATUSES


def rule_params_of(channel, alert_type):
    """某渠道某类规则的 params(JSON → dict)。读不到 → {} (调用方按默认口径走)

    存在的意义: 口径的**参数**也必须有单一入口。原来 `_rule_params("stockout")` 不带渠道,
    于是 any 渠道都读第一条 → other 的窗口/阈值跟 jd 一样。
    """
    try:
        from routes.dashboard_stock_risk import _rule_params
        return _rule_params(alert_type, channel) or {}
    except Exception:
        return {}


def oversell_sql_cond(days, statuses=None, now=None):
    """→ SQL 片段: 未发货 + 近 N 天。调用方直接 AND 进 WHERE

    ⚠️ 时间基准用**北京时区**的 now(订单时间也是北京时间), 不能用 `NOW()`
    —— TiDB 的 NOW() 是 UTC, 差 8 小时会把"刚下单 8 小时内"的单漏在窗外。
    """
    from datetime import timedelta
    st = tuple(statuses or OPEN_ORDER_STATUSES)
    _in = ",".join("'" + str(s).replace("'", "") + "'" for s in st)
    if now is None:
        try:
            now = now_cn()          # 文件顶部已 import(core/clock)
        except Exception:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
    cut = (now - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
    return "order_status IN (%s) AND ordered_at >= '%s'" % (_in, cut)
