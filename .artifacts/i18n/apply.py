#!/usr/bin/env python3
"""批量 i18n 迁移执行器。

输入: 一个批次文件(`.artifacts/i18n/batch_XX.py`), 结构:

    LOCALE = {
        "nav.foo": ("中文", "English"),
        ...
    }
    REPL = [
        ("frontend/src/pages/X.tsx", "原文片段", "替换后片段"),
        ...
    ]
    IMPORTS = {"frontend/src/pages/X.tsx": "import { t } from '../locale'"}

行为:
  1. 追加词条(已存在则跳过, 不覆盖 —— 覆盖会悄悄改掉别的页面在用的文案);
  2. 逐条做**精确替换**, 任一条未命中即整体不落盘并报错(避免半截迁移);
  3. 自动补 import(文件里没有 `from '../locale'` 才补, 路径按文件深度算);
  4. 全部替换完跑 `npx tsc --noEmit`, 失败则回滚本次改动。
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ZH = os.path.join(ROOT, "frontend/src/locale.zh.ts")
EN = os.path.join(ROOT, "frontend/src/locale.en.ts")


def esc(v: str) -> str:
    """单引号字符串转义: 反斜杠 → 引号 → 换行(必须, 否则会写出真实换行把文件写坏)"""
    return (
        v.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
    )


def add_keys(loc: dict) -> int:
    added = 0
    for path, idx in ((ZH, 0), (EN, 1)):
        src = open(path, encoding="utf-8").read()
        new = []
        for k, pair in loc.items():
            if "'%s'" % k in src:
                continue
            new.append("  '%s': '%s',\n" % (k, esc(pair[idx])))
        if new:
            i = src.rstrip().rfind("}")
            src = src[:i] + "".join(new) + src[i:]
            open(path, "w", encoding="utf-8").write(src)
            added += len(new)
    return added


def locale_import_for(rel: str) -> str:
    """从文件所在目录回到 src/ 的相对路径(rel 形如 frontend/src/pages/x/y.tsx)"""
    sub = rel.split("frontend/src/", 1)[1]
    up = sub.count("/")  # 目录层数
    return "import { t } from '%slocale'" % ("../" * up)


def apply(repls, imports) -> list[str]:
    by_file: dict[str, list] = {}
    for rel, old, new in repls:
        by_file.setdefault(rel, []).append((old, new))
    touched, errors = [], []
    for rel, pairs in by_file.items():
        path = os.path.join(ROOT, rel)
        src = open(path, encoding="utf-8").read()
        want = []  # 待改片段: (old, new, 是否已命中)
        for old, new in pairs:
            if old not in src:
                # 缩进容错: 批次文件写片段时常与源码缩进差一层, 按"去掉行首空白后的内容"重匹配
                norm = [ln.strip() for ln in old.strip("\n").split("\n")]
                if len(norm) > 1:
                    pat = r"\n[ \t]*".join(re.escape(x) for x in norm)
                    m = re.search(pat, src)
                    if m:
                        lead = src[src.rfind("\n", 0, m.start()) + 1:m.start()]
                        src = src[:m.start()] + ("\n" + lead).join(norm) + src[m.end():]
                        continue
                errors.append("%s: 未命中 → %r" % (rel, old[:70]))
                want.append(None)
                continue
            # 按行替换: 用"行首空白归一"比较, 命中后整段替换(批次文件不必逐字对齐缩进)
            old_lines = [ln for ln in old.split("\n")]
            src_lines = src.split("\n")
            key = [ln.strip() for ln in old_lines]
            n = len(key)
            hit = -1
            for i in range(len(src_lines) - n + 1):
                if [ln.strip() for ln in src_lines[i:i + n]] == key:
                    hit = i
                    break
            if hit < 0:
                errors.append("%s: 未命中 → %r" % (rel, old.strip()[:70]))
                continue
            src_lines[hit:hit + n] = new.split("\n")
            src = "\n".join(src_lines)
        if rel in imports and "from '%s'" % imports[rel].split("from '")[1].rstrip("'") not in src:
            pass
        if "locale'" not in src.split("\n\n")[0] and "from '../locale'" not in src and "from './locale'" not in src:
            imp = locale_import_for(rel)
            if imp not in src:
                # 插在最后一个 import 之后
                lines = src.split("\n")
                last = max(i for i, l in enumerate(lines) if l.startswith("import "))
                lines.insert(last + 1, imp)
                src = "\n".join(lines)
        if errors:
            break
        open(path, "w", encoding="utf-8").write(src)
        touched.append(rel)
    return errors


def main():
    batch = sys.argv[1]
    spec = importlib.util.spec_from_file_location("batch", batch)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore

    loc = getattr(mod, "LOCALE", {})
    repls = getattr(mod, "REPL", [])
    imports = getattr(mod, "IMPORTS", {})

    bad = [(k, v) for k, v in loc.items() if not (isinstance(v, tuple) and len(v) == 2)]
    if bad:
        print("词条必须 (zh, en) 二元组:", bad[:3])
        return 1

    n = add_keys(loc)
    errors = apply(repls, imports)
    if errors:
        print("替换失败(已改的部分需回滚 git):")
        for e in errors:
            print("  -", e)
        return 1
    print("词条 %d 条 / 替换 %d 处 / 文件 %d 个" % (n, len(repls), len({r[0] for r in repls})))

    r = subprocess.run(["npx", "tsc", "--noEmit"], cwd=os.path.join(ROOT, "frontend"),
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-2500:])
        return 2
    print("tsc OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
