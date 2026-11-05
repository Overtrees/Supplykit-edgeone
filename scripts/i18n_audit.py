#!/usr/bin/env python3
"""i18n 审计: 前端中文字面量清单 + 键集三向校验(红线 B 的可执行版本)。

用法:
    python3 scripts/i18n_audit.py              # 汇总 + 残留清单
    python3 scripts/i18n_audit.py --by-file    # 按文件汇总(默认)
    python3 scripts/i18n_audit.py --lines      # 逐条打印 file:line: 源码
    python3 scripts/i18n_audit.py --gate       # 只做门禁判定, 有残留则 exit 1

判据(与 docs/SKILL.md §11 红线 B 一致):
  1. 注释不算文案 —— 本仓库注释一律中文, 这是刻意的;
  2. locale.ts 本身容纳 zh/en 字典, 不参与"硬编码"判定;
  3. 三向校验: zh/en 键集合相等 / t() 引用的键都存在 / 字典里的键至少被引用一次(死键);
  4. 白名单: 形如 `l: '中文'` 的字段字典若已改为 `l: 'key'` + `t()` 则不再命中,
     确实必须保留中文的数据(如后端 error 前缀)在 ALLOW 里按 (文件, 行内容特征) 登记。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "src")
LOCALE_FILES = (os.path.join(SRC, "locale.zh.ts"), os.path.join(SRC, "locale.en.ts"))
# 词条表按「列 id / 字段名」动态取键(t('col.'+id)), 表体本身是数据不是界面文案
DICT_FILES = (os.path.join(SRC, "components", "colLabels.ts"),
              os.path.join(SRC, "pages", "cleansing", "fieldNames.ts"))
# 例外: 这些文件的中文是**数据**(客户表头别名 / 样本值), 不是界面文案。按行前缀白名单。
#   fields.ts 的 ALIAS / ALIAS_EXT 是"中文列名 → 字段"的识别字典, PRESET_EXAMPLE 是样例值 ——
#   翻译它们等于让识别失效。判定按"行是否以 `中文关键词:` 开头"粗略放行, 仍需 review。
DATA_PREFIX_FILES = {
    os.path.join(SRC, "pages", "cleansing", "fields.ts"): ("  '", "  "),
    # 状态值映射的「内置默认值」是**匹配用的数据**(导入时按原值归一化), 翻译即失效
    os.path.join(SRC, "pages", "cleansing", "ColMapSheet.tsx"): ("  { name: '", "  "),
    # 订单状态: 库里存导入原样中文, 这里只做 值→词条键 映射与关键词判档 —— 键不能翻
    os.path.join(SRC, "components", "statusLabels.ts"): ("  ", "  "),
    # CSV 模板的示例行: 下载给用户填的样例数据(华东C仓/B仓…), 不是界面文案
    os.path.join(SRC, "pages", "cleansing", "fields.ts"): ("  ", "  "),
    # 订单状态取值必须与库里一致(中文是数据), 展示走 statusLabels.orderStatusLabel()
    os.path.join(SRC, "components", "hammer", "configs.ts"): ("  ", "  "),
}

# 整文件放行: 静态兜底页(React 未挂载时渲染, 拿不到 i18n 上下文)
SKIP_FILES = {os.path.join(SRC, "main.tsx")}

# 允许为空串的键(其余空串一律视为"假翻译", 见 scan_empty_values)
EMPTY_OK = {
    # 'When A [then] or B' —— 英文里 then/or 之间不需要连接词, 中文「A 时 或 B」需要「时」
    ("en", "rfm.then"),
}

CJK = re.compile(r"[\u4e00-\u9fff]")
# 访问后端 JSON 的中文字段: x['日期'] / map(i => i['订单数']) —— 数据契约, 不属文案
BACKEND_KEY_RE = re.compile(r"\[\s*['\"][^'\"]*[\u4e00-\u9fff][^'\"]*['\"]\s*\]")

# 代码行特征(JSX/JS 语法符号) —— 缩进续行若含这些, 就不是纯注释文本, 仍需检查
CODE_HINT = re.compile(r"[<>{};=]|\breturn\b|\bconst\b|\bfunction\b")


# 允许保留中文的位置(注释 / 样式行内注释 / 明确的诊断文本)
ALLOW_SUBSTR = (
    "console.",          # 开发期日志
    "reportError(",      # 上报到 quality_logs 的诊断 detail(非用户提示)
    "new Error(",        # 内部错误对象, 展示层会套 t() 前缀
    "addDebug(",         # 规则页开发者调试面板的日志(诊断数据, 非界面文案)
    "reason: '",         # 传给后端的删除原因, 进流水/审计表, 属数据
)


def strip_trailing_comment(line: str) -> str:
    """去掉行尾 ``// 注释``(引号内的 // 不切) —— 本仓库注释一律中文, 不能当成文案。"""
    out, quote, i = [], None, 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote and (i == 0 or line[i - 1] != "\\"):
                quote = None
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def is_comment(stripped: str) -> bool:
    return (
        stripped.startswith("//")
        or stripped.startswith("*")
        or stripped.startswith("/*")
        or stripped.startswith("{/*")
        or stripped.startswith("*/")
    )


def iter_sources():
    for root, _dirs, files in os.walk(SRC):
        for name in sorted(files):
            if not name.endswith((".ts", ".tsx")):
                continue
            path = os.path.join(root, name)
            if any(os.path.abspath(path) == os.path.abspath(lf) for lf in LOCALE_FILES + DICT_FILES):
                continue
            if os.sep + "tests" + os.sep in path:
                continue
            if os.path.abspath(path) in {os.path.abspath(x) for x in SKIP_FILES}:
                continue
            yield path


def scan():
    hits = []
    for path in iter_sources():
        rel = os.path.relpath(path, SRC)
        with open(path, encoding="utf-8") as fh:
            fh_lines = fh.read().split("\n")
            in_block = False
            in_debug_obj = 0   # addDebug(... { ... }) 的多行对象深度
            for lineno, raw in enumerate(fh_lines, 1):
                # 块注释(/** ... */ 与 {/* ... */})逐行跟踪: 仓库注释一律中文, 不算文案
                _s = raw.strip()
                if in_block:
                    # 注释内出现中文引号/书名号不会终止块, 只有 `*/` 才终止
                    if "*/" in _s:
                        rest = _s.split("*/", 1)[1].strip()
                        in_block = False
                        # `*/` 同行后面还有代码时继续按代码行检查
                        if rest and CJK.search(rest) and not is_comment(rest):
                            hits.append((rel, lineno, rest))
                    continue
                if _s.startswith("/**") or _s.startswith("{/*"):
                    if "*/" not in _s:
                        in_block = True
                    continue
                # 块注释起点(可出现在行中, 如 style 里 `/* ...`), 本行无 `*/` 收尾 → 进入块
                if "/*" in _s and "*/" not in _s.split("/*", 1)[1]:
                    in_block = True
                    continue
                # 本行是块注释的收尾行(`... */`), 跳过; 若收尾后还有代码则继续检查
                if _s.endswith("*/"):
                    rest = _s.rsplit("*/", 1)[0]
                    if rest.strip().startswith("*") or not CODE_HINT.search(rest):
                        continue
                line = raw.strip()
                if not line or is_comment(line):
                    continue
                # 多行 addDebug(...) 的对象体: 里面是诊断键值, 不是界面文案
                if in_debug_obj:
                    in_debug_obj += line.count("{") - line.count("}")
                    continue
                # 单行 addDebug(...) 调用: 整段剥离后再看剩余代码, 避免连带吞掉后面的 JSX
                _m1 = re.search(r"addDebug\(\s*'[^']*'\s*,\s*\{", line)
                if _m1 and line.count("{") <= line.count("}"):
                    line = re.sub(r"addDebug\([^;]*?\)\s*;?", "", line, count=1).strip()
                    if not line or not CJK.search(line):
                        continue
                # 多行 addDebug 对象体: 只有对象确实未闭合时才进入诊断态
                _dbg = re.match(r"^addDebug\(.*\{\s*$", line)
                if _dbg and line.count("{") > line.count("}"):
                    in_debug_obj = line.count("{") - line.count("}")
                    continue
                line = strip_trailing_comment(line).strip()
                # 行内块注释 `a /* 说明 */ ,` —— 与行尾 // 同理, 不是文案
                line = re.sub(r"/\*.*?\*/", "", line).strip()
                if not line or not CJK.search(line):
                    continue
                if any(tok in line for tok in ALLOW_SUBSTR):
                    continue
                # 取后端 JSON 的中文字段名(数据契约, 不是界面文案)
                if re.match(r"^.*\w+\[['\"][^'\"]+['\"]\].*$", line) and "'" in line:
                    pass  # 落到下方常规判定, 由 BACKEND_KEY_RE 兜住
                if BACKEND_KEY_RE.search(line):
                    continue
                _dp = next((v for k, v in DATA_PREFIX_FILES.items()
                            if os.path.abspath(path) == os.path.abspath(k)), None)
                if _dp:
                    # 只放行数据行形态(别名表 `键: '值',` / 状态默认值 `{ name: '值', ... }`)
                    if re.match(r"^\s*('?[^:]+'?):\s*'.*',?$", line):
                        continue
                    if re.match(r"^\s*\{ name: '.*', group: '.*' \},?$", line):
                        continue
                    # CSV 示例行: `key: { sku: '...', warehouse: '华东C仓', ... },`
                    if re.match(r"^\s*[a-z_]+:\s*\{.*\},?$", line):
                        continue
                    # 键→词条 映射行 / 关键词判档行
                    if re.match(r"^\s*[^:]+:\s*'oms\.st\.", line):
                        continue
                    if "s.includes('" in line:
                        continue
                    # 数组常量里的枚举取值(如 ORDER_STATUSES)
                    if re.match(r"^\s*(export )?const [A-Z_]+ = \[", line):
                        continue
                # 取值比较: `if (v === '总订单') return t(...)` —— 左边是数据值(后端 funnel 桶名),
                # 右边才是要展示的文案; 把值翻掉会让判断失效。与文件无关, 全局生效。
                if re.match(r"^\s*if\s*\(.*?===?\s*'[^']*'\)\s*return\s+t\(", line):
                    continue
                # 纯 CSS 属性值里的中文只在注释里出现, 已在 is_comment 处理
                hits.append((rel, lineno, line))
    return hits


def locale_keys():
    """字典已拆成 locale.zh.ts / locale.en.ts (2026-11-04), 各自一份键集合。"""
    key_re = re.compile(r"^\s*'([^']+)':", re.M)
    out = []
    for lf in LOCALE_FILES:
        with open(lf, encoding="utf-8") as fh:
            out.append(set(key_re.findall(fh.read())))
    return out[0], out[1]


def locale_pairs():
    """键 → 文案, 用于核对 t(key, {n}) 的占位符是否落在文案里。"""
    pair_re = re.compile(r"^\s*'([^']+)':\s*'((?:[^'\\]|\\.)*)'", re.M)
    out = []
    for lf in LOCALE_FILES:
        with open(lf, encoding="utf-8") as fh:
            out.append(dict(pair_re.findall(fh.read())))
    return out[0], out[1]


# 词表化(字典存词条键, 渲染时 t(entry))的文件: 其字面量键也算「被引用」
# 否则 docTypeLabel/colLabel 这类查表解析出来的键会被误判成死键
TABLE_KEY_FILES = (
    os.path.join(SRC, "pages", "cleansing", "fields.ts"),   # DOC_TYPE_NAMES
    os.path.join(SRC, "colLabels.ts"),
    os.path.join(SRC, "fieldNames.ts"),
    os.path.join(SRC, "components", "Icons.tsx"),        # STATUS_LABEL
    os.path.join(SRC, "components", "hammer", "configs.ts"),
)


# 动态拼键的前缀: `t('col.' + id)` 这类无法静态求值。2026-11-05 之前只认字面量, 于是
#   这三组共 161 条活键被算成"死键"(占当时的 31%) —— 死键清单里混着三分之一的误报,
#   等于这条指标一开始就没人能信。前缀一旦确认是**命名空间**(该前缀下的键只可能通过拼接产生),
#   就不再逐条判死, 改为「该前缀整体由产键函数兜底」(见 scan_dynamic_prefix_health)。
DYNAMIC_PREFIXES = {
    "col": "components/colLabels.ts",          # colLabel(id) -> t('col.' + id)
    "fld": "pages/cleansing/fieldNames.ts",    # fldLabel(name) -> t('fld.' + name)
    "seg": "pages/insights/shared.tsx",        # 备注片段 k -> t('seg.' + k)
}

# 后端下发的词条键(task 进度步的 name_key), 前端 t(s.name_key) 渲染 —— 静态扫不到
BACKEND_I18N_KEY_RE = re.compile(r'"name_key"\s*:\s*"([a-z][a-z0-9_]*\.[a-z0-9_.]+)"')
BACKEND_DIR = os.path.join(ROOT, "cloud-functions", "api")


def backend_keys():
    """后端下发、前端按字符串渲染的词条键。扫 .py, 跳过 __pycache__/测试。"""
    out = set()
    for root, dirs, fs in os.walk(BACKEND_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in fs:
            if not f.endswith(".py"):
                continue
            with open(os.path.join(root, f), encoding="utf-8", errors="ignore") as fh:
                out |= set(BACKEND_I18N_KEY_RE.findall(fh.read()))
    return out


def scan_dynamic_prefix_health() -> list[tuple[str, str]]:
    """动态前缀的产键函数还在不在 —— 前缀文件删了/改名了, 该前缀下的键就真的全死了。

    这是「放过动态键」的代价: 不逐条判活, 就必须有另一种方式发现整体失效。
    """
    bad = []
    for pref, rel in DYNAMIC_PREFIXES.items():
        path = os.path.join(SRC, rel)
        if not os.path.exists(path):
            bad.append((pref, "产键文件不存在: " + rel))
            continue
        with open(path, encoding="utf-8") as fh:
            if not re.search(r"'%s\.'\s*\+" % pref, fh.read()):
                bad.append((pref, "文件里找不到 t('%s.' + ...) 拼接" % pref))
    return bad


def _all_source_texts():
    for path in iter_sources():
        with open(path, encoding="utf-8") as fh:
            yield fh.read()


def dead_keys(zh=None, used=None):
    """死键 = 字典里有、但引用不到、且不属于动态前缀命名空间的键。

    动态前缀(col./fld./seg.)是**命名空间**而非逐条引用: 该前缀下的键只可能由
    `t('col.' + id)` 这类拼接产生, 静态永远扫不到 —— 所以不逐条判死, 改由
    scan_dynamic_prefix_health() 盯住产键函数本身(文件删了/拼接没了 = 整片失效)。
    """
    zh = locale_keys()[0] if zh is None else zh
    used = used_keys() if used is None else used
    _dynp = tuple(x + "." for x in DYNAMIC_PREFIXES)
    # 兜底: 只要键以完整字面量在任何源文件出现过(对象键 / 三元 / 查表 / 注释), 就算有人引用。
    #   静态求值做不到全知 —— 与其逐条攻各种写法(元组/简写/三元/条件表达式), 不如承认
    #   "字面量存在即未死"; 死键统计的用途是**清理候选**, 漏报几条远好于误报一堆。
    texts = list(_all_source_texts())
    out = []
    for k in zh:
        if k in used or k.startswith(_dynp):
            continue
        if any(("'%s'" % k) in t or ('"%s"' % k) in t for t in texts):
            continue
        out.append(k)
    return sorted(out)


# 词表条目的键字段: { l: 'rf.lf.3', v: '...' } 渲染时 t(o.l) —— 键以字面量存, 但不在 t() 里。
# 2026-11-05 之前只白名单了几张已知表(TABLE_KEY_FILES), 于是 rf.* / task.type_* / oms.st.*
# 这些表里的键全被算成死键。改成按字段名通用识别, 表再增也不用维护白名单。
BARE_KEY_RE = re.compile(r"'([a-z][a-z0-9_]{0,20}(?:\.[a-z0-9_]{1,24}){1,3})'")
# 元组数组元素: ['lit', 'p.lit'] / ['x', 'oms.st.done'] —— 键前是逗号或 `[`
TUPLE_KEY_RE = re.compile(r"(?:\[|,)\s*'([a-z][a-z0-9_]{0,20}(?:\.[a-z0-9_]{1,24}){1,3})'\s*[,\]]")
# 对象字面量简写: const VARS = { lit: 'rf.vars.lit', ss_z: 'rf.vars.ss_z' }
#   渲染处是 Object.entries(VARS) -> t(v), 没有 l:/label: 这样的字段名可依
SHORTHAND_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*:\s*'([a-z][a-z0-9_]{0,20}(?:\.[a-z0-9_]{1,24}){1,3})'")
KEY_FIELD_RE = re.compile(
    r"\b(?:l|label|label_key|name_key|title_key|k|key|desc_key|msg_key)\s*:\s*'([a-z][a-z0-9_]*\.[a-z0-9_.]+)'")


def used_keys():
    used = set()
    call_re = re.compile(r"\bt\(\s*'([^']+)'")
    for path in iter_sources():
        with open(path, encoding="utf-8") as fh:
            used |= set(call_re.findall(fh.read()))
    # 词表文件里出现过的词条键一并视为引用
    lit_re = re.compile(r"'([a-z][a-z0-9_]*\.[a-z0-9_.]+)'")
    for path in TABLE_KEY_FILES:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            used |= set(lit_re.findall(fh.read()))
    # 全仓词表条目的键字段(不限于白名单文件)
    for path in iter_sources():
        with open(path, encoding="utf-8") as fh:
            used |= set(KEY_FIELD_RE.findall(fh.read()))
    # 裸元组表 [['rules', 'hr.tab_rules'], ...].map(([id, label]) => t(label))
    #   键作为数组元素出现。只认**元素开头**的那个字面量([, 'x.y'), 不认第二个:
    #   第二项往往是字段名({ v: 'flow.date' } / ['lit', 'params.lit']), 不是词条键。
    for path in iter_sources():
        with open(path, encoding="utf-8") as fh:
            _t = fh.read()
            used |= set(TUPLE_KEY_RE.findall(_t))
            used |= set(SHORTHAND_KEY_RE.findall(_t))
    # 后端下发的键(task.win / task.eval_seg …)
    used |= backend_keys()
    return used


# 变量名遮蔽翻译函数: `const t = setTimeout(...)` / `(t: any) =>` 之后, 同作用域的 t('x') 会炸。
# 这不是风格问题 —— 已实际踩过 3 次(relTime / load() / App 定时器), 一律在审计里拦。
SHADOW_RE = re.compile(r"\b(?:const|let|var)\s+t\s*=(?!=)")


def scan_shadow() -> list[tuple[str, int, str]]:
    out = []
    for path in iter_sources():
        rel = os.path.relpath(path, SRC)
        with open(path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh.read().split("\n"), 1):
                line = strip_trailing_comment(raw.strip())
                if SHADOW_RE.search(line):
                    out.append((rel, lineno, raw.strip()))
    return out


def scan_col_ids() -> list[str]:
    """列定义里的 id 必须都有 `col.*` 词条。

    列标签走 `colLabel(id)` → `t('col.' + id)`, 是动态拼键, 静态引用检查抓不到;
    漏词条时界面上会直接显示 'col.brand' 这种原始键(2026-11-04 4 视口验证时发现)。
    """
    cfg = os.path.join(SRC, "components", "hammer", "configs.ts")
    zh_path = os.path.join(SRC, "locale.zh.ts")
    id_re = re.compile(r"\{\s*id:\s*'([a-z][a-z0-9_]*)'")
    with open(cfg, encoding="utf-8") as fh:
        ids = list(dict.fromkeys(id_re.findall(fh.read())))
    with open(zh_path, encoding="utf-8") as fh:
        zh = set(re.findall(r"'col\.([a-z0-9_]+)'", fh.read()))
    return [i for i in ids if i not in zh]


def scan_placeholder_mismatch() -> list[tuple[str, str, str]]:
    """`t(key, {n})` 传了参数, 但词条里没有对应的 `{n}` —— 数字会被静默丢掉。

    实际踩过: 'dash.unit_wan_orders' 写成 '万单'(丢了 {n}), 看板图表上只剩"万单"没有数值。
    """
    zh_map, en_map = locale_pairs()
    out = []
    # 参数表只取到第一个 `}` —— 值本身可能带对象字面量({ ch: channelName({ code: ch }) }),
    # 否则会把内层 key(code) 也当成参数名, 误报
    call_re = re.compile(r"t\(\s*'([a-z][a-z0-9_.]+)'\s*,\s*\{([^{}]*)")
    for path in iter_sources():
        rel = os.path.relpath(path, SRC)
        with open(path, encoding="utf-8") as fh:
            for raw in fh.read().split("\n"):
                line = strip_trailing_comment(raw)
                for key, args in call_re.findall(line):
                    # 顶层形参名: `n:` / `count:` (排除 `? :` 三元与箭头函数里的 `x =>`)
                    names = re.findall(r"(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(?!:)", args)
                    if not names:
                        continue
                    for loc, mp in (("zh", zh_map), ("en", en_map)):
                        txt = mp.get(key)
                        if txt is None:
                            continue
                        lack = [n for n in names if ("{" + n + "}") not in txt]
                        if lack:
                            out.append((rel, key, f"{loc} 缺 {','.join(lack)}"))
    return out


def scan_empty_values() -> list[tuple[str, str, str]]:
    """字典里的空串 = 假翻译。

    `t()` 的回落是「当前语言 → zh → key」, en 写成 '' 不报错, 直接掉回中文 ——
    英文界面里因此出现「单 / 件」(2026-11-04 4 视口验证发现)。空串一律当缺陷报出来,
    只有「英文语法上本就不该有对应词」的键允许为空, 逐条登记在 EMPTY_OK 里并写明理由。
    """
    out = []
    zh_map, en_map = locale_pairs()
    for loc, mp in (("zh", zh_map), ("en", en_map)):
        for k, v in mp.items():
            if v.strip() == "" and (loc, k) not in EMPTY_OK:
                out.append((loc, k, "值为空串"))
    return out


def selftest() -> int:
    """判定自身正负例 —— 防止改坏扫描后"什么都查不出"仍然 PASS。

    2026-11-05 实际踩到过: 新加的后端 key 正则变量与既有的中文字段豁免正则**同名覆盖**,
    于是 `i['日期']` 这类豁免失效 → 冒出一个并不存在的"硬编码残留"。门禁红了,
    但红的原因是工具 bug 而不是代码问题 —— 与 §15.151 的 schema 对账假阳性同类。
    """
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else "  " + detail))
        ok = ok and cond

    # 1) 中文字段访问不算文案(豁免规则仍然有效)
    for line in ("data: periodTrend.map(i => i['日期']) || [],",
                 'const x = row["订单数"]'):
        chk("豁免被识别: " + line[:30], bool(BACKEND_KEY_RE.search(line)), "豁免规则失效")
    chk("中文字段访问被识别", bool(BACKEND_KEY_RE.search("i['日期']")))

    # 2) 真正的硬编码必须被抓到
    chk("JSX 文本被抓", CJK.search("<button>确定提交</button>") is not None)
    chk("中文注释不被抓", "注释" in "// 中文注释")

    # 3) 后端下发键: 正例抓得到, 反例(普通中文)抓不到
    got = BACKEND_I18N_KEY_RE.findall('{"name_key": "task.eval_seg", "p": {}}')
    chk("后端 name_key 正例", got == ["task.eval_seg"], str(got))
    chk("后端 name_key 反例", BACKEND_I18N_KEY_RE.findall('{"name": "评估"}') == [])

    # 4) 两个正则不是同一个对象(同名覆盖会导致上面第 1 组静默失效)
    chk("豁免正则 != 后端键正则", BACKEND_KEY_RE is not BACKEND_I18N_KEY_RE)

    # 5) 动态前缀的产键函数必须还在
    bad = scan_dynamic_prefix_health()
    chk("动态前缀 3/3 就位", not bad, str(bad))

    # 6) 动态前缀确实不在死键清单里
    zh, en = locale_keys()
    used = used_keys()
    # 判的是"死键清单里有没有它们"(主流程的过滤逻辑), 不是"字典里有没有" —— 后者永远为真
    _dynp = tuple(x + "." for x in DYNAMIC_PREFIXES)   # 不用 p: 上面 chk 的 detail 变量就叫 p
    ns_all = [x for x in zh if x.startswith(_dynp)]
    dead_now = dead_keys()
    leaked = [x for x in ns_all if x in dead_now]
    chk("动态前缀未混进死键", not leaked, str(leaked[:5]))
    chk("动态前缀确实有键(否则本条断言无意义)", len(ns_all) > 50, f"ns={len(ns_all)}")

    # 7) 词表键字段 / 元组数组元素 / 后端下发键 都要算引用
    for t, rx in (("{ l: 'rf.lf.3' }", KEY_FIELD_RE),
                  ("['lit', 'p.lit'],", TUPLE_KEY_RE),
                  ("'p.log'", BARE_KEY_RE),
                  ('{"name_key": "task.win"}', BACKEND_I18N_KEY_RE)):
        chk("键识别: " + t, rx.findall(t), "正则没抓到")
    chk("单字母前缀不被吞(p.lit)", "p.lit" in BARE_KEY_RE.findall("'p.lit'"))
    chk("同义键不误判(rules.new_btn != rules.new)",
        "rules.new" not in BARE_KEY_RE.findall("t('rules.new_btn')"))

    # 8) 键集合本身可解析(空集也算"解析成功", 所以要有下限)
    chk("字典规模合理", len(zh) > 500 and len(en) > 500, f"zh={len(zh)} en={len(en)}")

    print("自检:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", action="store_true", help="逐条打印残留")
    ap.add_argument("--gate", action="store_true", help="有残留则 exit 1")
    ap.add_argument("--selftest", action="store_true", help="判定规则的正负例自检(不进业务扫描)")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    zh, en = locale_keys()
    used = used_keys()

    print(f"字典: zh {len(zh)} 键 / en {len(en)} 键")
    only_zh, only_en = sorted(zh - en), sorted(en - zh)
    print(f"  键集合相等: {'✅' if not only_zh and not only_en else '❌'} "
          f"缺 en={only_zh[:8]} 缺 zh={only_en[:8]}")

    # 动态前缀(t('col.' + id) / t('fld.' + name) / t('seg.' + k) / t('seg.trend_x_' + x))
    # 由字典文件的键集合兜住, 不按字面量核对; 'seg.' 这类带尾点的键是拼接左半, 同样跳过
    _dyn_prefix = ("col.", "fld.", "seg.", "seg.trend_x_", "task.")
    # 缺键只认**真正的 t('字面量')** 调用: used_keys() 里还混着词表键字段(v: 'flow.date'
    #   这类是后端字段名), 拿它判缺键会冒出 40+ 条假缺失。
    t_lit = set()
    for _p in iter_sources():
        with open(_p, encoding="utf-8") as _fh:
            t_lit |= set(re.findall(r"\bt\(\s*'([^']+)'", _fh.read()))
    missing = sorted(k for k in t_lit if k not in zh and k not in _dyn_prefix
                     and not k.endswith("."))
    # 动态前缀命名空间不逐条判死(由 scan_dynamic_prefix_health 兜底),
    #   否则 col./fld./seg. 下 161 条活键全被算成死键
    dead = dead_keys(zh, used)
    print(f"  t() 引用但字典缺失: {len(missing)} {missing[:8]}")
    print(f"  字典有但未被引用(死键, 不含动态前缀): {len(dead)} {dead[:8]}")

    shadow = scan_shadow()
    print(f"  变量名遮蔽 t(): {len(shadow)} {[f'{r}:{n}' for r, n, _ in shadow][:4]}")

    no_col = scan_col_ids()
    print(f"  列 id 缺 col.* 词条: {len(no_col)} {no_col[:8]}")

    ph = scan_placeholder_mismatch()
    print(f"  传参词条缺占位符: {len(ph)} {[f'{k}({d})' for _r, k, d in ph][:4]}")

    empt = scan_empty_values()
    print(f"  空串词条(假翻译): {len(empt)} {[f'{l}:{k}' for l, k, _ in empt][:6]}")

    dyn_bad = scan_dynamic_prefix_health()
    print(f"  动态前缀产键函数: {len(DYNAMIC_PREFIXES) - len(dyn_bad)}/{len(DYNAMIC_PREFIXES)} 就位 "
          f"{[f'{p}: {why}' for p, why in dyn_bad][:3]}")

    hits = scan()
    by_file: dict[str, int] = {}
    for rel, _ln, _line in hits:
        by_file[rel] = by_file.get(rel, 0) + 1

    print(f"\n中文硬编码残留: {len(hits)} 行 / {len(by_file)} 个文件")
    if by_file:
        width = max(len(f) for f in by_file)
        for rel, cnt in sorted(by_file.items(), key=lambda kv: -kv[1]):
            print(f"  {cnt:>4}  {rel:<{width}}")
    if args.lines:
        print()
        for rel, ln, line in hits:
            print(f"  {rel}:{ln}: {line[:140]}")

    if args.gate:
        ok = (
            not only_zh
            and not only_en
            and not missing
            and not hits
            and not shadow
            and not no_col
            and not ph
            and not empt
            and not dyn_bad
        )
        print("\n门禁:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
