"""逐条替换: 命中即写, 未命中单独报告(不再「整批不落盘」, 避免一处失配全批失效)。"""
import os
import re


def replace_block(src: str, old: str, new: str) -> tuple[str, bool]:
    lines = src.split('\n')
    old_n = [l.strip() for l in old.split('\n') if l.strip()]
    if not old_n:
        return src, False
    # 先试精确匹配(快路径)
    if old in src:
        return src.replace(old, new, 1), True
    idx_map = [i for i, l in enumerate(lines) if l.strip()]
    flat = [lines[i].strip() for i in idx_map]
    n = len(old_n)
    hit = -1
    for s in range(len(flat) - n + 1):
        if flat[s:s + n] == old_n:
            hit = s
            break
    if hit < 0:
        return src, False
    first, last = idx_map[hit], idx_map[hit + n - 1]
    base = re.match(r'[ \t]*', lines[first]).group(0)
    old_ind = re.match(r'[ \t]*', old.split('\n')[0]).group(0)
    out = []
    for l in new.split('\n'):
        if not l.strip():
            out.append('')
            continue
        cur = re.match(r'[ \t]*', l).group(0)
        rel = cur[len(old_ind):] if cur.startswith(old_ind) else cur
        out.append(base + rel + l.strip())
    lines[first:last + 1] = out
    return '\n'.join(lines), True


def run(edits, root, verbose=True):
    ok_n, miss = 0, []
    cache = {}
    for rel, old, new in edits:
        p = os.path.join(root, rel)
        if rel not in cache:
            with open(p, encoding='utf-8') as f:
                cache[rel] = f.read()
        nxt, hit = replace_block(cache[rel], old, new)
        if hit:
            cache[rel] = nxt
            ok_n += 1
        else:
            miss.append((rel, old.splitlines()[0].strip()[:66]))
    for rel, txt in cache.items():
        with open(os.path.join(root, rel), 'w', encoding='utf-8') as f:
            f.write(txt)
    if verbose:
        print(f'命中 {ok_n}/{len(edits)}')
        for rel, x in miss:
            print(f'  未命中 {rel} :: {x}')
    return ok_n, miss
