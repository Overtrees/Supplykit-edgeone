"""按「去缩进后整行/整块匹配」做替换的通用助手。

批次脚本反复因为缩进差一层而「未命中」——人工核对缩进既慢又易错。
本模块: 把待替换片段按行剥离前导空白, 在源文件里按同样的规整方式定位,
命中后按源文件实际的缩进重新缩进新片段, 从而与缩进无关。
"""
import re


def _norm(lines):
    return [l.strip() for l in lines if l.strip() != '']


def replace_block(src: str, old: str, new: str) -> tuple[str, bool]:
    """在 src 中找到 old(忽略缩进/空行差异), 用 new 替换。返回 (新文本, 是否命中)。"""
    old_n = _norm(old.split('\n'))
    if not old_n:
        return src, False
    src_lines = src.split('\n')
    # 源文件中忽略空行后的索引映射
    idx_map = [i for i, l in enumerate(src_lines) if l.strip() != '']
    flat = [src_lines[i].strip() for i in idx_map]

    n = len(old_n)
    hit = -1
    for start in range(len(flat) - n + 1):
        if flat[start:start + n] == old_n:
            # 要求首尾行内容唯一, 避免误命中注释里的同形文本
            hit = start
            break
    if hit < 0:
        return src, False

    first_i = idx_map[hit]
    last_i = idx_map[hit + n - 1]
    # 用首行原缩进作为基准
    base_indent = re.match(r'[ \t]*', src_lines[first_i]).group(0)
    # old 首行缩进
    old_indent = re.match(r'[ \t]*', old.split('\n')[0]).group(0)

    out = []
    for l in new.split('\n'):
        if not l.strip():
            out.append('')
            continue
        cur_indent = re.match(r'[ \t]*', l).group(0)
        rel = cur_indent[len(old_indent):] if cur_indent.startswith(old_indent) else cur_indent
        out.append(base_indent + rel + l.strip())

    src_lines[first_i:last_i + 1] = out
    return '\n'.join(src_lines), True


def apply_all(edits, root, verbose=True):
    """edits: [(相对路径, 原文, 新文)]。全部命中才算成功(由调用方决定是否落盘)。"""
    import os
    results = {}
    misses = []
    for rel, old, new in edits:
        p = os.path.join(root, rel)
        cur = results.get(rel)
        if cur is None:
            with open(p, encoding='utf-8') as f:
                cur = f.read()
        nxt, ok = replace_block(cur, old, new)
        if not ok:
            misses.append((rel, old.splitlines()[0].strip()[:70]))
            continue
        results[rel] = nxt
    if misses:
        if verbose:
            print(f'\n未命中 {len(misses)} 条:')
            for rel, x in misses:
                print(f'  {rel} :: {x}')
        return False, misses
    for rel, txt in results.items():
        with open(os.path.join(root, rel), 'w', encoding='utf-8') as f:
            f.write(txt)
    if verbose:
        print(f'\n全部 {len(edits)} 条替换完成 ({len(results)} 个文件)')
    return True, []
