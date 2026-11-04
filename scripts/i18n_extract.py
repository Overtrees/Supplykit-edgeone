#!/usr/bin/env python3
"""把前端源码里的中文字面量抽成「待翻译清单」, 供人工/模型逐条过双语。

用法:
    python3 scripts/i18n_extract.py pages/InventoryPage.tsx     # 只抽一个文件
    python3 scripts/i18n_extract.py --all                       # 全量(输出到 .artifacts/i18n/)
    python3 scripts/i18n_extract.py components --summary        # 只出按文件的词条数

输出 TSV: 文件<TAB>行号<TAB>形态<TAB>原文
形态: jsx(JSX 文本节点) / str(字符串字面量) / tmpl(模板字符串里的中文) / attr(属性值)
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src")
CJK = re.compile(r"[\u4e00-\u9fff]")
# 单个中文字面量: '...' 或 "..." (不跨行, 不含转义引号)
STR_LIT = re.compile(r"'([^'\\\n]*(?:\\.[^'\\\n]*)*)'|\"([^\"\\\n]*(?:\\.[^\"\\\n]*)*)\"")


def is_comment(s: str) -> bool:
    st = s.strip()
    return st.startswith(("//", "*", "/*", "{/*", "*/"))


def strip_line_comment(line: str) -> str:
    """去掉行尾 // 注释(粗略: 引号内的 // 不切)"""
    out, quote = [], None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote and (i == 0 or line[i - 1] != "\\"):
                quote = None
        else:
            if ch in "'\"":
                quote = ch
                out.append(ch)
            elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def extract_file(path: str):
    rel = os.path.relpath(path, SRC)
    rows = []
    with open(path, encoding="utf-8") as fh:
        in_block = False
        for lineno, raw in enumerate(fh.read().split("\n"), 1):
            # 块注释(/** 与 {/*): 注释一律中文是本仓库刻意的, 不算待译文案
            _s = raw.strip()
            if in_block:
                if "*/" in _s:
                    in_block = False
                    raw = _s.split("*/", 1)[1]
                    if not raw.strip():
                        continue
                else:
                    continue
            elif _s.startswith(("/**", "{/*", "/*")) and "*/" not in _s:
                in_block = True
                continue
            if is_comment(raw):
                continue
            # 匹配用的数据行(导入别名/状态默认值)—— 翻译即失效, 不算待译文案
            if re.match(r"^\s*\{ name: '.*', group: '.*' \},?$", raw.strip()):
                continue
            line = strip_line_comment(raw)
            if not CJK.search(line):
                continue
            found = False
            for m in STR_LIT.finditer(line):
                text = m.group(1) if m.group(1) is not None else m.group(2)
                if not text or not CJK.search(text):
                    continue
                found = True
                rows.append((rel, lineno, "str", text))
            if not found:
                # JSX 文本节点等: 整行留证, 由人工判定
                rows.append((rel, lineno, "jsx", line.strip()))
    return rows


def iter_paths(args):
    for a in args:
        p = a if os.path.isabs(a) else os.path.join(SRC, a)
        if os.path.isdir(p):
            for root, _d, files in os.walk(p):
                for f in sorted(files):
                    if f.endswith((".ts", ".tsx")) and f not in ("locale.zh.ts", "locale.en.ts"):
                        if os.sep + "tests" + os.sep in os.path.join(root, f) + os.sep:
                            continue
                        yield os.path.join(root, f)
        elif p.endswith((".ts", ".tsx")):
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    paths = list(iter_paths(args.paths)) if not args.all else list(iter_paths(["."]))
    rows = []
    for p in paths:
        rows.extend(extract_file(p))

    if args.summary:
        by = {}
        for rel, _l, _k, _t in rows:
            by[rel] = by.get(rel, 0) + 1
        for rel, n in sorted(by.items(), key=lambda kv: -kv[1]):
            print(f"{n:>4}  {rel}")
        print(f"合计 {len(rows)} 条 / {len(by)} 文件")
        return 0

    for rel, lineno, kind, text in rows:
        print(f"{rel}\t{lineno}\t{kind}\t{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
