"""工程审计测试(test_audit.py): 防 2026-10-06/07 两类事故复发
A. SQL 形态: _status_cond() 禁止嵌 IN (%s)(双包裹 bug —— 品牌 GMV 曾用非支付状态)
B. 缓存 key 完整性: key 必须包含影响结果的函数参数(采购/补货曾缺 days 维度致跨窗口污染)
C. except pass 静默吞异常统计(纪律基线, 输出清单不改码)

用法: cd cloud-functions/api && python3 test_audit.py
"""
import os
import re
import sys

ROUTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "routes")
FAIL = 0
PASS = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s  %s" % (name, extra))


def route_files():
    return [f for f in sorted(os.listdir(ROUTES)) if f.endswith(".py")]


print("=" * 60)
print("A. SQL 形态审计(_status_cond 双包裹防线)")
# 所有文件源码
srcs = {}
for f in route_files():
    srcs[f] = open(os.path.join(ROUTES, f), encoding="utf-8").read()

# A1: 禁止 _status_cond() 嵌入 IN (%s) —— 展开后 IN (IN (...)) 布尔值列表
# (排除 _status_cond 函数自身的定义行: return "%s IN (%s)")
bad = []
for f, s in srcs.items():
    for m in re.finditer(r'IN\s*\(\s*%s\s*\)', s):
        seg = s[max(0, m.start() - 600):m.end()]
        # 排除定义自身
        if "def _status_cond" in seg or "def _paid_cond" in seg:
            continue
        if "_status_cond" in seg:
            ln = s[:m.start()].count("\n") + 1
            bad.append("%s:%d IN(%%s) 与 _status_cond 同段" % (f, ln))
check("无 IN (%s) 与 _status_cond 同段(双包裹)", not bad, "; ".join(bad[:5]))

# A2: _status_cond 展开形态正确(PAID 4 状态含申请退款)
from routes.dashboard import _status_cond
from routes.common import PAID_STATUSES
_check = False
try:
    _check = _status_cond() == "order_status IN ('待发货','已发货','已完成','申请退款')"
except Exception:
    _check = tuple(PAID_STATUSES) == ("待发货", "已发货", "已完成", "申请退款")
check("_status_cond 展开 = PAID 4 状态(含申请退款)", _check)

# A3: 所有 "IN (%s)" 使用处列示(人工核对: 只允许非状态条件)
ins_uses = []
for f, s in srcs.items():
    for i, line in enumerate(s.splitlines(), 1):
        if "IN (%s)" in line or "NOT IN (%s)" in line:
            ins_uses.append("%s:%d %s" % (f, i, line.strip()[:80]))
print("  IN/NOT IN (%s) 使用处 %d 个(核对无状态条件):" % (len(ins_uses), len(ins_uses)))
for u in ins_uses[:12]:
    print("    " + u)

print("=" * 60)
print("B. 缓存 key 完整性审计(维度参数全覆盖)")
# 每个 _cache_get 调用的 key 模板 → 应包含的维度参数(影响结果的函数参数)
EXPECT = [
    ("dash_summary|", ["channel", "start_date", "end_date", "mode"]),
    ("dash_aux|", ["channel", "mode"]),
    ("stock_risk|", ["channel", "full"]),  # key 现带 _CACHE_V 前缀(%s|stock_risk|...)
    ("accel|", ["channel", "ratio", "min_qty"]),
    ("purchase|", ["channel", "mode", "days"]),
    ("repl|", ["channel", "mode", "days", "source"]),
]
key_defs = []
for f, s in srcs.items():
    # 2026-11-06: 上一轮把 purchase/repl 的 `_key` 换成 `shape_cached(base, ...)` 分片缓存,
    #   键的**业务维度全在 base 里**(slice_key 只追加 need_only/search/page/page_size)。
    #   所以断言也要认 `_base` —— 否则 db 里真丢了 channel 没人拦, 而测试还显示"通过"。
    for m in re.finditer(r'(?:_key|_base)\s*=\s*"([^"]+)"\s*%\s*\(([^)]*)\)', s):
        key_defs.append((f, m.group(1), m.group(2)))
seen = set()
for f, tmpl, args in key_defs:
    n_ps = tmpl.count("%s")
    argnames = [a.strip().split("=")[0].strip() for a in args.split(",") if a.strip()]
    # 2026-11-02 S2-9: 版本段参数(_CV/_CACHE_V/...V)不是业务维度, 不计入 need 校验;
    #   原逻辑只剥离「模板以 %s| 开头」的前缀, 而 stock_risk 的 _CV 写在模板首位之外
    #   ("stock_risk|%s|%s|%s" % (_CV, channel, full)) → 误报少一个维度。
    _ver_args = [a for a in argnames if a in ("_CV", "_CACHE_V", "_VER", "_V")]
    _dim_args = [a for a in argnames if a not in _ver_args]
    for prefix, need in EXPECT:
        if prefix in tmpl:  # 容忍 _CACHE_V 前缀(%s|stock_risk|...)
            seen.add(prefix)
            missing = [p for p in need if p not in _dim_args]
            # 判据 = 「**至少**含这些维度」+ 占位符数==参数数(否则 % 会错位)。
            # 2026-11-09: 原为 `len(_dim_args) == len(need)`（恰好等于）。stock_risk 后来
            #   把 limit/offset/level 也放进了 key（翻页/筛选必须分片，否则第 1 页响应
            #   被第 3 页命中）——多一个真实维度只会让缓存更保守，不会串味，却把这条
            #   判据顶成红。改成子集判定：缺维度仍然报红，加维度不再误报。
            check("缓存 key %s 含 %s" % (prefix, ",".join(need)),
                  not missing and n_ps == len(argnames),
                  "模板=%s 参数=%s" % (tmpl, argnames))
for prefix, need in EXPECT:
    if prefix not in seen:
        check("缓存 key %s 存在定义" % prefix, False, "未找到 _key 定义")
# 额外的 _cache_get 直传 key(非 _key 变量) 检查
extra_keys = []
for f, s in srcs.items():
    for m in re.finditer(r'_cache_get\(\s*"[^"]+"', s):
        extra_keys.append("%s: %s" % (f, m.group(0)[:60]))
check("无 _cache_get 直传字符串 key(均走 _key 变量)", not extra_keys, "; ".join(extra_keys[:5]))

# B3: 分片缓存的切片键必须拼满五段 —— 少一段就会出现"翻页拿到上一页"、
#     "搜索词不生效"、"need_only 串味" 这类不报错但结果错的静默故障。
try:
    _lp = open(os.path.join(os.path.dirname(ROUTES), "core", "list_page.py"), encoding="utf-8").read()
except Exception:
    _lp = ""
_m_slice = re.search(r"def slice_key\(([^)]*)\)\s*:(.*?)(?=\ndef |\Z)", _lp, re.S)
check("slice_key 定义存在", bool(_m_slice), "core/list_page.py 未找到 slice_key")
if _m_slice:
    # 只看 slice_key 函数体(到下一个顶层 def 为止): 必须拼够 5 段, 且四个切片维度全部参与。
    #   不解析表达式(会踩 int(bool(...)) / `or ""` 的括号), 只数占位符与出现 —— 锁结构不锁写法。
    _body = _m_slice.group(2)
    _params = [a.strip().split("=")[0].strip() for a in _m_slice.group(1).split(",") if a.strip()]
    _need = ["need_only", "search", "page", "page_size"]
    _miss_p = [p for p in _need if p not in _params]
    _miss_b = [p for p in _need if p not in _body]
    _n_ph = len(re.findall(r"%[sd]", _body))
    check("slice_key 五段齐全(%s)" % ",".join(_need),
          not _miss_p and not _miss_b and _n_ph >= 5,
          "参数缺=%s 拼装缺=%s 段数=%d" % (_miss_p, _miss_b, _n_ph))

print("=" * 60)
print("C. except pass 静默吞异常统计(纪律基线)")
silent = []
for f, s in srcs.items():
    for m in re.finditer(r'except[^:]*:\s*\n\s*pass\b', s):
        ln = s[:m.start()].count("\n") + 1
        silent.append("%s:%d" % (f, ln))
    # 兼容 try:/except Exception:\n  pass 变体
for f, s in srcs.items():
    for m in re.finditer(r'except[^:]*:\n\s*pass\s*\n', s):
        ln = s[:m.start()].count("\n") + 1
        if "%s:%d" % (f, ln) not in silent:
            silent.append("%s:%d" % (f, ln))
print("  except pass 共 %d 处(建议逐处改为自记日志+降级):" % len(silent))
for s in sorted(silent):
    print("    " + s)

print("=" * 60)
print("D. DATE_FORMAT/STR_TO_DATE %% 转义审计(pymysql 参数化防 M3 复发)")
# 补扫 index.py(迁移 SQL 在入口文件, 非 routes/)
if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.py")):
    srcs["index.py"] = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.py"), encoding="utf-8").read()
single_pct = []
for f, s in srcs.items():
    for m in re.finditer(r'(?:DATE_FORMAT|STR_TO_DATE|DATE_ADD|DATE_SUB)\([^,]+,\s*\'([^\']*)\'', s):
        fmt = m.group(1)
        if '%' in fmt and '%%' not in fmt:
            ln = s[:m.start()].count('\n') + 1
            single_pct.append("%s:%d %s" % (f, ln, fmt))
check("无 DATE_FORMAT 单 % 格式串(参数化 SQL 须 %%)", not single_pct, "; ".join(single_pct[:5]))
for u in single_pct[:10]:
    print("  单%处(人工核对, 无参数查询 %Y 合法):", u)
# index.py 迁移段已于 2026-11-02 S2-9 整体退役(见 docs §15.123), 原"迁移 SQL DATE_FORMAT
# 必须 %% 转义"检查会变成恒真空跑; 改为全库同口径(任何文件的 DATE_FORMAT 格式串都不得用单 %),
# 与上一条 D 段互补: 上一条查裸 %Y 字面量, 这条查 DATE_FORMAT 内格式串。
_mig_ok = True
for _f, _s in srcs.items():
    for m in re.finditer(r'DATE_FORMAT\([^,]+,\s*\'([^\']*)\'', _s):
        if "%" in m.group(1) and "%%" not in m.group(1):
            _mig_ok = False
check("全库 DATE_FORMAT 格式串无单 %% (M3 防复发; 迁移段退役后全库同口径)", _mig_ok)

print("=" * 60)
print("E. 读接口零副作用(2026-11-06 S2-1 审计 P0-10)")
# 事故: `_build_purchase` 里写 purchase_need 告警 → "
#   ① 告警更不更新取决于"有没有人打开采购页";
#   ② 缓存粒度改成一页一条后, 每翻一页重写一遍(线上实证同 SKU 13 条)。
# 现在: builder 只算不写, 写入收敛到 core/alert_jobs。以下三条守这条边界。

_api_dir = os.path.dirname(os.path.abspath(__file__))


def _fn_body(path, fn_name):
    """截取模块级函数的函数体文本(到下一个同级 def / 文件尾)"""
    src = open(path, encoding="utf-8").read()
    m = re.search(r"\ndef\s+" + re.escape(fn_name) + r"\s*\(", src)
    if not m:
        return ""
    rest = src[m.end():]
    nxt = re.search(r"\n(?:def |class |@)", rest)
    return rest[:nxt.start()] if nxt else rest


_pc = os.path.join(_api_dir, "routes", "purchase_calc.py")
_bp_body = _fn_body(_pc, "_build_purchase")
check("采购 builder 函数体存在(函数被改名会让本组断言空跑)",
      len(_bp_body) > 500, "len=%d" % len(_bp_body))
check("采购 builder 内不得出现写语句(INSERT/UPDATE/DELETE)",
      not re.search(r"INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM", _bp_body, re.I),
      "builder 里又出现写库语句了")
check("采购 builder 默认不写告警(with_alerts 默认 False)",
      "with_alerts=False" in _bp_body.split("\n")[0] or "with_alerts=False" in _bp_body[:200],
      "签名默认值被改成 True 会让读接口又带副作用")

# 读路由不得调用写侧 job —— 那等于把副作用换个函数名塞回读路径
_pr = open(os.path.join(_api_dir, "routes", "purchase.py"), encoding="utf-8").read()
check("采购读路由不调用 alert_jobs / sync_purchase_alerts",
      "alert_jobs" not in _pr and "sync_purchase_alerts" not in _pr, "routes/purchase.py")

# 触发源必须存在(四个写侧入口), 否则"零副作用"退化成"永不更新"
_trig = 0
for _rel in ("routes/cleansing.py", "routes/cron.py", "routes/suppliers.py"):
    _src = open(os.path.join(_api_dir, *_rel.split("/")), encoding="utf-8").read()
    if "kick_purchase_alerts" in _src:
        _trig += 1
check("采购告警的三个写侧触发源都在(导入完成/评估完成/参数保存)",
      _trig == 3, "只在 %d 个文件里找到" % _trig)

# ── F. 判定口径单一真源(2026-11-06 S3)────────────────────────────────────
# 这一组守的不是"某行代码写成什么样", 而是**同一件事只允许有一个定义**。
# 判据刻意选"副本数"(grep 计数), 因为口径漂移的形态永远是"又多了一份看起来对的实现"。
print("\nF. 判定口径单一真源(core/metrics.py)")
_met_p = os.path.join(_api_dir, "core", "metrics.py")
_met_src = open(_met_p, encoding="utf-8").read() if os.path.exists(_met_p) else ""
check("core/metrics.py 存在且导出完整 API(dim/safety/judge/units/counts/score)",
      all(("def %s(" % _fn) in _met_src for _fn in
          ("mode_of", "periods", "period_of", "dim_of", "safety_line", "judge",
           "judge_units", "units", "counts", "score", "load")),
      "缺函数会导致消费方自己补一份")

# F1: 周期公式只许有一份 —— 其余文件不得再从 cfg 里拼 b_to_c_days + ...
_dup = []
for _root, _dirs, _files in os.walk(_api_dir):
    if "__pycache__" in _root or "/.git" in _root:
        continue
    for _f in _files:
        if not _f.endswith(".py") or _f in ("local_test.py", "test_audit.py", "metrics.py"):
            continue
        _fp = os.path.join(_root, _f)
        _src = open(_fp, encoding="utf-8").read()
        # 只看"活的表达式": 去掉注释行与 seed 的参数清单(那是数据, 不是公式)
        if _f == "seed_fill.py":
            continue
        _live = "\n".join(_l for _l in _src.split("\n") if not _l.strip().startswith("#"))
        # "b_to_c_days" 与 "c_safety_days" 挨着出现 = 又拼了一遍 C 周期
        # 键名带引号才算"真的在读配置"(注释/文档串里不会带引号)
        if re.search(r"""['"]b_to_c_days['"][^\n]{0,150}['"]c_safety_days['"]""", _live):
            _dup.append(os.path.relpath(_fp, _api_dir))
check("周期公式只有 core/metrics.periods 一份(其余文件不再拼 b_to_c+c_safety)",
      not _dup, "重复实现: %s" % ",".join(_dup))

# F2: 健康分不得再读 SKU 级单维日销(get_sales_digest 的 {sku: fused})
_hh = open(os.path.join(_api_dir, "routes", "dashboard_health.py"), encoding="utf-8").read()
check("健康分不再用 get_sales_digest 的 SKU 级单维日销",
      "get_sales_digest" not in _hh and "metrics" in _hh,
      "那个 map 只有 __C__ 一个维度, 拿去判每个仓就是逐仓分虚低的根因")
check("健康分 mode 不再默认 bbcc(默认值由渠道配置回答)",
      'mode="bbcc"' not in _hh.split("def _health_index")[1][:300],
      "cron 每渠道调一次 → other 用 jd 的周期")
check("健康分粒度由 metrics.grain_of 决定(不再各写 by_barcode)",
      "grain_of" in _hh and "by_barcode" not in _hh,
      "同一渠道告警按码、看板按 SKU → 两个数放在一张卡上必然对不上")

# F3: 低库存计数不再自拼安全线
_dsh = open(os.path.join(_api_dir, "routes", "dashboard.py"), encoding="utf-8").read()
check("看板低库存两处计数都走 metrics(不再出现 _mcl/_mc2 自拼周期)",
      "_mcl(" not in _dsh and "_mc2(" not in _dsh and "_M.judge_units(" in _dsh,
      "自拼周期 = 第四份口径")

# F4: cron 不得重复计算健康分, 且必须传渠道模式
_cr = open(os.path.join(_api_dir, "routes", "cron.py"), encoding="utf-8").read()
_hi_calls = len(re.findall(r"_h?i?x?\w*\(_ch\)", _cr.split("from routes.dashboard import _stock_risk, _health_index")[-1][:2500]))
check("cron 每渠道只算一次健康分, 且按渠道默认模式传入",
      "_health_index(_ch, _cdm(_ch))" in _cr and "_hix(" not in _cr,
      "重复调用=%d" % _hi_calls)

# F5: 规则引擎的量取"本单元", 不是 SKU 全仓汇总(否则 BC 可撑天数混入自有仓库存)
_re2 = open(os.path.join(_api_dir, "core", "rule_eval.py"), encoding="utf-8").read()
check("规则引擎按单元取量(BC=平台+B, 不再用 _agg 的 SKU 全仓合计)",
      "_M.judge(" in _re2 and '_st["avail"] + _st["transit"]' not in _re2,
      "P0-13: BC 盘可售里混着集货仓的货")

# ── G. 评估跑得完 + 挂起有痕(2026-11-06 S4)──────────────────────────────
# 判据来自线上实证: rule_eval(jd) 一条 running 停了 5 小时, other 每轮 140ms 却 0 命中。
# 根因是"一轮评估 55s、其中 35.7s 花在 560 次分批往返上" → 实例被掐断 → 收尾语句没执行。
print("\nG. 评估作业化(跑得完 / 挂起有痕)")
_re3 = open(os.path.join(_api_dir, "core", "rule_eval.py"), encoding="utf-8").read()
_osql_block = _re3.split("_osql = (", 1)[1].split(").replace", 1)[0] if "_osql = (" in _re3 else ""
check("超卖评估用一条 GROUP BY, 不再按 500/200 个 SKU 分批往返",
      'range(0, len(_skus_u), 500)' not in _re3 and 'range(0, len(_oskus), 200)' not in _re3
      and "FROM orders" in _osql_block and "GROUP BY sku" in _osql_block
      and _re3.count("query(_osql") == 1,
      "分批往返是 35.7s/55s 的来源")
check("超卖可用量口径不依赖别的规则要不要算变量(_agg 缺省时自己汇总)",
      "_agg = _agg or {}" in _re3 and "if _need_calc or _order_rules:" in _re3,
      "否则 available_stock 恒 0 → 每个有订单的 SKU 都判超卖")
_mt = open(os.path.join(_api_dir, "core", "maintenance.py"), encoding="utf-8").read()
check("挂起运行会被收尾成 timeout(不是永远 running)",
      "def close_stale_runs(" in _mt and "status='timeout'" in _mt,
      "收尾语句执行不到 = running 永久成立")
_csr_body = _mt.split("def close_stale_runs")[1][:1200]
check("收尾只动 pipeline_runs(观测表), 不 UPDATE sync_tasks 状态机",
      "UPDATE pipeline_runs" in _csr_body and "UPDATE sync_tasks" not in _csr_body,
      "改 sync_tasks 会让前端把失败当进行中")
_cr2 = open(os.path.join(_api_dir, "routes", "cron.py"), encoding="utf-8").read()
check("每日评估开始前先收尾挂起运行",
      _cr2.find("close_stale_runs") < _cr2.find("evaluate_stock_skus(_ch"),
      "顺序反了等于这轮还是被上一次的 running 误导")

# ── H. 超卖口径 + 缺货粒度(2026-11-06 用户定案)──────────────────────────
# 两件事:
#   ① 超卖判定量 = **未发货订单量**(待确认/待发货 + 近 N 天), 不是全历史累计销量。
#      后者只要卖过一阵就恒大于当前可用 → 每个 SKU 都超卖(other 实测 1,783 条)。
#   ② 缺货数与低库存数必须是**同一批单元**、同一粒度(按渠道补货模式):
#      bbcc → BC 盘 + 自有仓; traditional → 逐仓 + 自有仓; 且都只数销售仓。
print("\nH. 超卖口径 / 缺货粒度")
_met2 = open(os.path.join(_api_dir, "core", "metrics.py"), encoding="utf-8").read()
check("超卖口径只在 core/metrics 定义一次(状态清单 + 窗口 + SQL 片段)",
      "OPEN_ORDER_STATUSES" in _met2 and "def oversell_window" in _met2
      and "def oversell_sql_cond" in _met2,
      "散落定义必然出现'评估说超卖、关单说没超卖'")

# H1: 全仓不得再有"SUM(quantity) 直接当超卖判定量"
_bad = []
for _root, _dirs, _files in os.walk(_api_dir):
    if "__pycache__" in _root:
        continue
    for _f in _files:
        if not _f.endswith(".py") or _f in ("local_test.py", "test_audit.py"):
            continue
        _fp = os.path.join(_root, _f)
        _src = open(_fp, encoding="utf-8").read()
        for _m in re.finditer(r"SUM\(quantity\)\s+AS\s+(q|total_qty|oq)", _src):
            _seg = _src[max(0, _m.start() - 900):_m.end() + 900]
            # 只管**订单域**(出入库流水/账期里的 SUM(quantity) 是另一回事, 与超卖无关)
            if "FROM orders" not in _seg and "orders o" not in _seg:
                continue
            # 按订单 id 取"这一批订单的量"(恢复联动算单笔语义)不是历史累计, 跳过
            if "WHERE id IN" in _seg:
                continue
            # 订单域里, 没有"未发货"条件聚合 = 又把历史累计量当判定量
            if "order_status IN" not in _seg and "CASE WHEN" not in _seg:
                _bad.append("%s: %s" % (os.path.relpath(_fp, _api_dir), _m.group(0)))
check("没有'全历史累计销量'当超卖判定量的地方", not _bad, "; ".join(_bad[:4]))

# H2: 三个消费方都走同一份口径
_re4 = open(os.path.join(_api_dir, "core", "rule_eval.py"), encoding="utf-8").read()
check("评估/删单/关单三处共用 _open_qty_map 或 metrics.oversell_*",
      _re4.count("_open_qty_map(") >= 2 and "oversell_window" in _re4,
      "_open_qty_map 调用次数=%d" % _re4.count("_open_qty_map("))
_cw = open(os.path.join(_api_dir, "routes", "cleanse_write.py"), encoding="utf-8").read()
_cl = open(os.path.join(_api_dir, "routes", "cleansing.py"), encoding="utf-8").read()
check("导入即时评估也走 metrics 的口径(不再自己拼 SUM(quantity))",
      "oversell_sql_cond" in _cw and "oversell_sql_cond" in _cl,
      "导入时判出来的超卖与每日评估不一致 = 用户看到忽有忽无")

# H3: SKU 级告警(空仓型)必须能被自动关闭
check("关闭白名单允许空仓型(SKU 级告警), 否则 oversell 永远关不掉",
      'not str(a.get("wt") or "")' in _re4,
      "线上实证: jd 35 条 oversell 全 active, 其他类型都在正常流转")

# H4: 缺货与低库存同一批单元
_dsh2 = open(os.path.join(_api_dir, "routes", "dashboard.py"), encoding="utf-8").read()
# summary 走 `_M.roll(_t_sum, kinds=...)`（快照桶），aux 走 `_M.counts(_pr, ...)`（判定结果），
# 两条路都必须用同一组销售仓单元 BC+WH；任一侧换成单维或把自有仓算进来就报红。
_dsh2_ns = _dsh2.replace(" ", "")
check("缺货/低库存计数用同一批单元(kinds=BC/WH), 且两处(summary+aux)一致",
      "_M.roll(_t_sum,kinds=(_M.DIM_BC,_M.DIM_WH)" in _dsh2_ns
      and "_M.counts(_pr,(_M.DIM_BC,_M.DIM_WH))" in _dsh2_ns
      and "(_M.DIM_BC,_M.DIM_WH,_M.DIM_OWN)" not in _dsh2_ns,
      "两个数不同粒度时, 相加得到的 total 没有任何含义")
check("aux 不再单独 COUNT 库存行当缺货数",
      "COUNT(*) AS c FROM inventory WHERE channel=%s AND available_qty=0" not in _dsh2,
      "那数的是库存行(含自有仓、每个 69码), 与低库存的判定单元不同粒度")
check("自有仓不进缺货/低库存计数(它的缺口由 ownHealth 承载)",
      "自有仓(集货仓)的" in _dsh2 or "只数**销售仓**" in _dsh2,
      "集货仓 available=0 是常态, 算进缺货就是纯噪音")

# H5: 前端不再自己算低库存
_fe = os.path.join(_api_dir, "..", "..", "frontend", "src")
_hits = []
for _f in ("App.tsx", os.path.join("pages", "DashboardPage.tsx")):
    _fp = os.path.normpath(os.path.join(_fe, _f))
    if not os.path.exists(_fp):
        continue
    _src = open(_fp, encoding="utf-8").read()
    if "available_qty) < Number(x.safety_qty" in _src:
        _hits.append(_f)
check("前端不再用静态 safety_qty 自算低库存(第 6 份口径)",
      not _hits, "残留: %s" % ",".join(_hits))

# ── I. 粒度单一真源 + 旧字段删除(2026-11-06 第二轮定案)────────────────
# I1: units 调用一律传 grain(由 metrics.grain_of 决定), 禁止各调用点手写 by_barcode
import glob as _glob
_bad_bc = []
for _f in _glob.glob(os.path.join(_api_dir, "routes", "*.py")) + \
        [os.path.join(_api_dir, "core", "rule_eval.py")]:
    _src = open(_f, encoding="utf-8").read()
    if "by_barcode" in _src:
        _bad_bc.append(os.path.basename(_f))
check("判定粒度只由 metrics.grain_of 决定(调用点禁手写 by_barcode)",
      not _bad_bc, "残留: %s" % ",".join(_bad_bc))
_mm = open(os.path.join(_api_dir, "core", "metrics.py"), encoding="utf-8").read()
check("grain_of 存在且可配置(mode_<m>_grain / grain)",
      "def grain_of(" in _mm and "mode_%s_grain" in _mm and 'cfg.get(_k)' in _mm,
      "粒度必须唯一决定处, 且留业务覆盖口")
check("规则评估用 grain_of 并对粒度残留告警收尾",
      "grain_of" in _re4 and "_close_grain_mismatch" in _re4,
      "改粒度后旧键告警永远不被命中, 会被批量护栏挡住挂死在列表上")

# I2: 历史累计量字段已删(total_qty / lifetime_qty), 只留 order.open_qty
_rf = open(os.path.join(_api_dir, "core", "rule_fields.py"), encoding="utf-8").read()
check("字段表只有 order.open_qty, 不再有 total_qty/lifetime_qty 兼容别名",
      '"order.open_qty"' in _rf and "total_qty" not in _rf and "lifetime_qty" not in _rf,
      "留'同值兼容'会让人以为历史累计量是合法判定量")
_bad_qty = []
for _f in _glob.glob(os.path.join(_api_dir, "core", "*.py")) + \
        _glob.glob(os.path.join(_api_dir, "routes", "*.py")) + \
        [os.path.join(_api_dir, "core", "rule_sim.py")]:
    if "local_test" in _f or "test_audit" in _f:
        continue
    _src = open(_f, encoding="utf-8").read()
    if "total_qty" in _src or "lifetime_qty" in _src:
        _bad_qty.append(os.path.basename(_f))
check("ctx 构造不再注入 total_qty/lifetime_qty(线上规则已核查无引用)",
      not _bad_qty, "残留: %s" % ",".join(_bad_qty))
_fe_rules = _glob.glob(os.path.join(_fe, "pages", "rules", "*.tsx"))
_bad_fe = []
for _f in _fe_rules:
    _src = open(_f, encoding="utf-8").read()
    if "order.total_qty" in _src or "order.lifetime_qty" in _src:
        _bad_fe.append(os.path.basename(_f))
check("前端规则页不再提供历史累计量字段",
      not _bad_fe, "残留: %s" % ",".join(_bad_fe))
check("前端默认超卖条件用未发货量",
      "left: 'order.open_qty'" in open(os.path.join(_fe, "pages", "rules", "EditForm.tsx"),
                                       encoding="utf-8").read(),
      "新建超卖规则的默认条件必须是现行口径")

# I3: cron force 的鉴权必须先于窗口检查(否则窗口外带 secret 也进不来 = 运维入口失效)
_cr = open(os.path.join(_api_dir, "routes", "cron.py"), encoding="utf-8").read()
# 2026-11-06 S7: 密钥校验收口到 _force_ok(request)(与 archive/cleanup 同一判据)
_i_auth = _cr.find("force = 1 if (force and _force_ok(request))")
_i_win = _cr.find('_off_window("daily_rules")')
check("daily-rules force 鉴权先于窗口检查, 且支持 channels/alert_types 定向重跑",
      0 < _i_auth < _i_win and "run_daily_rules(channels=_chs" in _cr,
      "原顺序: 窗口检查在前 → 窗口外 force 永远被 skip, 数据重建后无法补跑")

# I4: 合并不静默(2026-11-06 用户追问) —— grain=sku 吞的是行, 不能吞掉"哪个码没货"
_mm2 = open(os.path.join(_api_dir, "core", "metrics.py"), encoding="utf-8").read()
check("units 合并时记录码级明细(_merge_code), code_risk 复核存在",
      "_merge_code" in _mm2 and "def code_risk(" in _mm2 and "def code_detail_str(" in _mm2,
      "B1=0/B2=100 被 SKU 合计判 ok, 码级断货无声消失 = '多码被吞'")
check("看板对码级断货单列计数(code_out_count), 不混进互斥的 out/low",
      "code_risk(u)" in _dsh2 and "code_out_count" in _dsh2,
      "补充可见性; 判定口径不动, 否则又回到同渠道两种粒度")
check("规则 ctx 注入码级明细(inv.code_detail / 顶层 code_detail), 文案可定位到码",
      '"inv.code_detail"' in _rf and _re4.count("code_detail") >= 2,
      "多码 SKU 触发告警时用户要能看到各码可用量")

# ── J. S5 导入链路(2026-11-06): 租约统一 / O(n²) 消除 / 原子增量 / 清单出 params ──
_j_cl = open(os.path.join(_api_dir, "routes", "cleansing.py"), encoding="utf-8").read()
_j_ex = open(os.path.join(_api_dir, "routes", "task_export.py"), encoding="utf-8").read()
_j_sc = open(os.path.join(_api_dir, "schema_create.py"), encoding="utf-8").read()
check("四类任务锁统一: cleansing/eval/export 均用 task_lease(不再有 3s updated_at 窗口锁)",
      "INTERVAL 3 SECOND" not in _j_cl and "INTERVAL 3 SECOND" not in _j_ex
      and "_lease.acquire(" in _j_cl and "_lease.acquire(" in _j_ex,
      "3s 窗口挡不住看守+event+兜底三路轮询 → 同片执行两遍 → sum 双算(线上实证 3100=1550×2)")
check("租约心跳绑定退出路径(finally stop+release), 僵尸心跳不会替下一任续租",
      _j_cl.count("finally:") >= 2 and "_stop_beat()" in _j_cl
      and "_lease.release(task_id" in _j_cl, "start_beat 后必须保证 stop")
check("同步分支 sleep(4) 已删(新建任务 lease_until=NULL 即可抢)",
      "_ts.sleep(4)" not in _j_cl, "那 4 秒是所有小导入的固定延迟")
check("评估清单落 eval_queue(applied=完成标记+认领租约), params 不再塞 sku_list",
      '"eval_queue"' in _j_sc and "INSERT IGNORE INTO eval_queue(task_id, sku) " in _j_cl
      and '"sku_list": _ev_skus' not in _j_cl,
      "2 万 SKU ≈ 数百 KB params, 每次推进整份读改写(64KB 上限风险)")
check("认领=租约(claimed_at+TTL 可重认领), applied=完成标记(成功才置 1+token 归属)",
      "claimed_at < DATE_SUB(NOW(6), INTERVAL %d SECOND)" in _j_cl
      and "SET applied=1, applied_at=NOW(6) " in _j_cl
      and "AND claimed_token=%%s AND id IN" in _j_cl.replace("`", "")
      and "_claimed != len(_ids)" in _j_cl,
      "2026-11-06 线上验证实证: 领取即置位后实例冻结 → 兜底见未领行已空直接 done, "
      "冻结丢失的评估永久无法重放(兜底安全网被静默打穿)")
_smk = open(os.path.join(_api_dir, "sql_smoke.py"), encoding="utf-8").read()
_db = open(os.path.join(_api_dir, "db.py"), encoding="utf-8").read()
check("db 层前置守卫: 占位符/参数错配在发请求前报出带 SQL 的错误(4 次同类踩坑后固化)",
      "def _guard_fmt(" in _db and _db.count("_guard_fmt(sql, params)") >= 3,
      "PyMySQL 自己的报错不带 SQL, 线上只看到'任务卡住/评估静默失败'")
check("冒烟含客户端格式化检查(PyMySQL %d 进参数位在发请求前就 TypeError, EXPLAIN 看不见)",
      "def client_fmt_err(" in _smk and "_fmt = client_fmt_err(" in _smk
      and "_preload_module_sqls" in _smk,
      "2026-11-06 连踩两次: 占位符少一个 + %d 进参数位, 两次都'冒烟全绿、线上全红'")
check("applied=0 但租约未到期时不得提前 done(别的驱动处理中)",
      "waiting_claim" in _j_cl and "_left" in _j_cl,
      "提前 done = 重演冻结丢评估")
check("导入分片真源 import_stage(一次解析+清洗落库), 分窗/eval 按 stage 直读",
      '"import_stage"' in _j_sc
      and "INSERT IGNORE INTO import_stage(task_id, row_no, sku, payload)" in _j_cl
      and "SELECT payload FROM import_stage WHERE task_id=%s " in _j_cl,
      "原'每窗重读 BLOB+重解析+重清洗整份'是 O(n²), 100 万行不可完成")
check("eval 分片不再解析源文件(_parse_table 已移出 eval 路径)",
      "_parse_table" not in _j_cl.split("def cleansing_eval_status")[1].split("def cleansing_templates")[0],
      "每片重读重解析是 O(n²) 的另一半")
check("_adjust_inventory 原子增量(相对 delta 下推 DB), 不再读-改-写绝对值覆盖",
      "GREATEST(0, available_qty+%s)" in open(os.path.join(_api_dir, "routes", "cleanse_write.py"),
                                             encoding="utf-8").read(),
      "并发分片后写覆盖前写(丢 delta) / 重试双算(错账纠正不了)")
_cw_src = open(os.path.join(_api_dir, "routes", "cleanse_write.py"), encoding="utf-8").read()
check("新行走 INSERT..ON DUPLICATE 增量(并发双建不 1062 不覆盖)",
      "ON DUPLICATE KEY UPDATE " in _cw_src
      and "GREATEST(0, available_qty+VALUES(available_qty))" in _cw_src,
      "uq_inventory_biz 五维键兜底并发建行")

# ── K. S6 容量与观测(2026-11-06)──────────────────────────────────────
_arc = open(os.path.join(_api_dir, "core", "archive.py"), encoding="utf-8").read()
_cr6 = open(os.path.join(_api_dir, "routes", "cron.py"), encoding="utf-8").read()
_sl6 = open(os.path.join(_api_dir, "biz", "sales.py"), encoding="utf-8").read()
_cm6 = open(os.path.join(_api_dir, "routes", "common.py"), encoding="utf-8").read()
_cl6 = open(os.path.join(_api_dir, "routes", "cleansing.py"), encoding="utf-8").read()

check("归档是集合式(INSERT...SELECT...GROUP BY), 不是逐条 INSERT",
      "INSERT INTO daily_stats" in _arc and "SELECT DATE(ordered_at)" in _arc
      and "GROUP BY" in _arc and "for o in old" not in _cr6,
      "逐条 INSERT: 50 万行 = 50 万次往返, 单实例执行槽被占几十分钟且云函数 60s 必被切断")
check("归档 INSERT 与 DELETE 同事务(归档成功才删, 重跑不重复累加)",
      "with transaction():" in _arc and "_INS_SQL" in _arc and "_DEL_SQL" in _arc,
      "若归档了但没删, 重跑同一区间会把 daily_stats 再加一遍")
check("归档按 id 区间分批(不是全量进内存), 切线段用 ordered_at 而非 DATE() 包列",
      "ordered_at < %s" in _arc and "DATE(ordered_at) < %s" not in _arc,
      "DATE() 包列 = 索引失效; 全量 SELECT 进内存 = 峰值内存随订单量线性涨")
check("重建按 SKU 分片、每片一个事务(DELETE 与 INSERT 原子)",
      "from db import transaction as _txn" in _sl6 and "with _txn():" in _sl6
      and "_groups.setdefault(str(r[\"sku\"])" in _sl6,
      "整渠道 DELETE 后分批 INSERT 有真空窗: 并发读会拿到空表/半量")
check("批次对账改 LEFT JOIN 聚合子查询(去相关子查询与裸 HAVING)",
      "LEFT JOIN (SELECT sku, COALESCE(barcode,'') AS bc" in _cr6
      and "(SELECT SUM(b.qty) FROM batches b" not in _cr6,
      "相关子查询对 inventory 每行跑一次 SUM(18 万批次)")
check("quality_logs 按级别保留(error 30 / warning 14 / 其余 3 天)+ 字节上限",
      "QUALITY_LOG_KEEP_BY_LEVEL" in _cm6 and "QUALITY_LOG_TARGET_BYTES" in _cm6,
      "一律 30 天时 info 级把配额占满, 真正的 error 反被硬上限截断删掉")
check("sql_smoke 有全表扫断言(过滤型查询在 >10 万行表上整表扫 = 红)",
      "def _fullscan_hits" in open(os.path.join(_api_dir, "sql_smoke.py"),
                                   encoding="utf-8").read()
      and "_FULLSCAN_ALLOW_TABLES" in open(os.path.join(_api_dir, "sql_smoke.py"),
                                           encoding="utf-8").read(),
      "P2-53: 加索引/改条件后回归挡住, 而不是等线上慢查询")
_cc6 = [ln.strip() for ln in _cl6.splitlines()
        if "CONCAT(" in ln and not ln.strip().startswith("#")]
check("查重不再用 CONCAT 包列(改行构造器, 可走索引)",
      not _cc6 and '["(%s,%s)"] * len(' in _cl6 and '["(%s,%s,%s)"] * len(' in _cl6,
      "CONCAT 把列包进函数 → 索引必然失效, 每次导入/打开导入页都整表扫; 残留 %s" % (_cc6[:2],))

# ── L. S7 安全面(2026-11-06)──────────────────────────────────────────
_mi7 = open(os.path.join(_api_dir, "routes", "misc.py"), encoding="utf-8").read()
_dg7 = open(os.path.join(_api_dir, "routes", "debug.py"), encoding="utf-8").read()
_ix7 = open(os.path.join(_api_dir, "index.py"), encoding="utf-8").read()
_au7 = open(os.path.join(_api_dir, "routes", "auth.py"), encoding="utf-8").read()
_cm7 = open(os.path.join(_api_dir, "routes", "common.py"), encoding="utf-8").read()

check("/debug/* 默认不注册路由(DEBUG_ENDPOINTS=1 才挂), 中间件同一开关",
      'DEBUG_ENDPOINTS' in _mi7 and 'DEBUG_ENDPOINTS' in _ix7,
      "匿名可读 JWT_SECRET 指纹/长度 = 把鉴权实现摊开给外部")
check("debug 端点不再回显 secret 长度/指纹(只回 secret_set 布尔)",
      "secret_len" not in _dg7 and '"fp"' not in _dg7,
      "长度/指纹是给爆破者的判定信号")
check("/auth/setup 需 SETUP_TOKEN(未配置时生产一律拒绝)",
      "SETUP_TOKEN" in _au7 and "compare_digest" in _au7
      and 'DB_BACKEND", "tidb"' in _au7,
      "本端点在中间件里放行 —— 不校验就是谁先调谁拿到 admin")
check("verify_token 用常数时间比较(防逐位爆破签名)",
      "hmac.compare_digest(str(parts[2]), expected)" in _cm7,
      "逐字节比较在第一个不同字符处返回, 时间差可做 timing attack")
check("/logs/frontend 有来源限频(匿名可写通道不能被刷爆)",
      "_FE_LIMIT" in _mi7 and "dropped" in _mi7,
      "分级保留后 info 只留 3 天, 刷日志就能把证据冲掉")
check("cron 窗口对带密钥的 force 放行(数据重建后能立刻补跑)",
      "def _force_ok(request)" in _cr and _cr.count("force_ok=_force_ok(request)") >= 8,
      "只有窗口内能跑 = 修完 bug 还得等到凌晨")

# ── M. S8 换键(2026-11-06, P1-21)────────────────────────────────────
_sc8 = open(os.path.join(_api_dir, "schema_create.py"), encoding="utf-8").read()
_cw8 = open(os.path.join(_api_dir, "routes", "cleanse_write.py"), encoding="utf-8").read()
check("products 唯一键是 (channel, sku), 不再是 sku",
      "UNIQUE KEY uq_products_biz (channel, sku)" in _sc8
      and "UNIQUE KEY uq_products_sku (sku)" not in _sc8,
      "旧键让两渠道同名 SKU 无法并存: 第二个渠道导入 1062 → 单行降级 → 用户看到'成功 0 条'")
check("换键迁移先建新键再删旧键, 且建键前把 channel 的 NULL/空归一",
      _sc8.index("ADD UNIQUE KEY uq_products_biz") < _sc8.index("DROP INDEX uq_products_sku")
      and "UPDATE products SET channel='jd' WHERE channel IS NULL OR channel=''" in _sc8,
      "先删后建有裸奔窗口(并发导入可写重); 多行 NULL 在唯一键里互不冲突 = 键没建成")
check("读 products 的效期查询按渠道(换键后同 SKU 两行会互相覆盖)",
      "WHERE sku IN (%s) AND (channel=%%s OR COALESCE(channel,'')='')" in _cw8,
      "只按 sku 查会拿到两渠道两行, _em_map[sku] 被后写覆盖 → 出库效期策略随机")

# ── N. 负库存口径(2026-11-06 用户定案: 保持负值)──────────────────────
# 重算 SQL 是"期初 + Σ入 − Σ出"的结果, 负值 = 账实不符的证据。夹 0 会让"出库 30/期初 0"
# 和"卖完了"在看板上一模一样 —— 谁也发现不了期初缺失。这条断言防"顺手夹 0"回流。
_cwn = open(os.path.join(_api_dir, "routes", "cleanse_write.py"), encoding="utf-8").read()
_recalc_body = _cwn.split("def _recalc_own_inventory")[1].split("def ")[0] \
    if "def _recalc_own_inventory" in _cwn else ""
_n_code = [ln for ln in _recalc_body.splitlines() if not ln.strip().startswith("#")]
_n_hit = [ln.strip()[:80] for ln in _n_code if "GREATEST(0" in ln or "max(0," in ln]
check("库存重算保留负值(不夹 0), 与增量扣减的 GREATEST(0,...) 是两件事",
      "去掉 `max(0, val)` 兜底" in _cwn and not _n_hit,
      "夹 0 = 把账实不符伪装成恰好卖完; 用户定案(2026-11-06)保持负值; 残留 %s" % (_n_hit[:2],))
check("前端对负值有明确标识(负库存异常), 不是静默显示",
      "inv.negative_stock" in open(os.path.join(_fe, "locale.zh.ts"), encoding="utf-8").read(),
      "保持负值的前提是它可见; 否则等于把异常藏起来")

print("\n审计结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
