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
    ("stock_risk|", ["channel", "full"]),
    ("accel|", ["channel", "ratio", "min_qty"]),
    ("purchase|", ["channel", "mode", "days"]),
    ("repl|", ["channel", "mode", "days", "source"]),
]
key_defs = []
for f, s in srcs.items():
    for m in re.finditer(r'_key\s*=\s*"([^"]+)"\s*%\s*\(([^)]*)\)', s):
        key_defs.append((f, m.group(1), m.group(2)))
seen = set()
for f, tmpl, args in key_defs:
    n_ps = tmpl.count("%s")
    argnames = [a.strip().split("=")[0].strip() for a in args.split(",") if a.strip()]
    for prefix, need in EXPECT:
        if tmpl.startswith(prefix):
            seen.add(prefix)
            missing = [p for p in need if p not in argnames]
            check("缓存 key %s 含 %s" % (prefix, ",".join(need)),
                  not missing and n_ps == len(need),
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
# index.py 迁移 SQL(M2/M3)必须 %% 转义 —— M3 曾因 %Y 被 pymysql 当占位符从未执行
_idx = srcs.get("index.py", "")
_mig_ok = True
for m in re.finditer(r'DATE_FORMAT\([^,]+,\s*\'([^\']*)\'', _idx):
    if "%%" not in m.group(1):
        _mig_ok = False
check("index.py 迁移 SQL DATE_FORMAT 全部 %% 转义(M3 防复发)", _mig_ok)

print("\n审计结果: %d 通过, %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
