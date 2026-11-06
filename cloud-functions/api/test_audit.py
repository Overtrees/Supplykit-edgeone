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
            check("缓存 key %s 含 %s" % (prefix, ",".join(need)),
                  not missing and n_ps == len(argnames) and len(_dim_args) == len(need),
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
check("超卖评估用一条 GROUP BY, 不再按 500/200 个 SKU 分批往返",
      'range(0, len(_skus_u), 500)' not in _re3 and 'range(0, len(_oskus), 200)' not in _re3
      and 'GROUP BY sku", [channel]' in _re3,
      "分批往返是 35.7s/55s 的来源")
check("超卖可用量口径不依赖别的规则要不要算变量(_agg 缺省时自己汇总)",
      'if _order_rules and not isinstance(_agg, dict):' in _re3,
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
check("缺货/低库存计数用同一批单元(kinds=BC/WH), 且两处(summary+aux)一致",
      _dsh2.count("_M.counts(_pr, (_M.DIM_BC, _M.DIM_WH))") == 1
      and _dsh2.count("_M.counts(_pairs_ls, (_M.DIM_BC, _M.DIM_WH))") == 1,
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
_i_auth = _cr.find("_force_ok = False")
_i_win = _cr.find("_off_window(\"daily_rules\")")
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

print("\n审计结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
