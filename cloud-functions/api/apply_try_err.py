#!/usr/bin/env python3
"""except pass 批量治理: 静默吞异常 → try_err 自记日志(common.try_err 2026-09-11 已有)
模式: except Exception: pass(单/多行) → except Exception as _e: try_err("<mod>", "静默降级", _e)
保留: try_err 函数自身的 except pass(写日志失败自吞防递归)
"""
import os
import re
import sys

API = os.path.dirname(os.path.abspath(__file__))
ROUTES = os.path.join(API, "routes")

FILES = []
for f in sorted(os.listdir(ROUTES)):
    if f.endswith(".py"):
        FILES.append(os.path.join(ROUTES, f))
for f in ["analysis_cache.py", "auth.py", "cleansing.py", "cron.py", "seed_fill.py"]:
    p = os.path.join(API, f)
    if os.path.exists(p):
        FILES.append(p)

TOTAL = 0
for path in FILES:
    mod = os.path.basename(path)[:-3]
    s = open(path, encoding="utf-8").read()
    orig = s

    def inject(src, where):
        # from routes.common import X, Y → 追加 try_err
        m = re.search(r'from\s+routes\.common\s+import\s+[^\n]+', src)
        if m and "try_err" not in m.group(0):
            return src[:m.end()] + ", try_err" + src[m.end():]
        return src

    # 1) 多行: except Exception:\n<indent>pass
    def multi(m):
        ind = m.group(1)
        return "except Exception as _e:\n%s    try_err('%s', '静默降级', _e)" % (ind, mod)

    s2 = re.sub(r'except\s+Exception\s*:\s*\n(\s+)pass\b', multi, s)
    n1 = len(re.findall(r'except\s+Exception\s*:\s*\n(\s+)pass\b', orig))
    # 2) 单行: except Exception: pass
    def single(m):
        return "except Exception as _e: try_err('%s', '静默降级', _e)" % mod

    s3 = re.sub(r'except\s+Exception\s*:\s*pass\b', single, s2)
    n2 = len(re.findall(r'except\s+Exception\s*:\s*pass\b', s2)) - n1
    # 3) 注入 import(try_err 未引入且文件有 from routes.common import)
    if "try_err" not in s3 and "from routes.common import" in s3:
        s3 = inject(s3, mod)
    if s3 != orig:
        open(path, "w", encoding="utf-8").write(s3)
        TOTAL += n1 + n2
        print("%s: 替换 %d 处(多行 %d 单行 %d)" % (mod, n1 + n2, n1, n2))
print("共替换 %d 处" % TOTAL)
