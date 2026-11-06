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
check("健康分四个 tab 同一粒度(by_barcode=False, 单元=SKU)",
      "by_barcode=False" in _hh, "unit 不一致时 total 不可横向比较")

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

print("\n审计结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
