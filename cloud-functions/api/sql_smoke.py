"""真实 SQL 冒烟(S3-4): 把回归实际产生的 SQL 拿去 TiDB `EXPLAIN`。

## 为什么需要

`local_test.py` 的 205 个用例全部 mock 掉 db、返回固定行 —— 断言的是"拿到这些行之后
逻辑对不对", 而**表名/列名/GROUP BY 一个都测不出**。§15.106 的三个 `NameError`、
以及线上 1054 类错误能活那么久, 根因就在这: 测试从没问过数据库"你这句 SQL 我认不认"。

## 为什么用 EXPLAIN 而不是连库跑

| 方式 | 副作用 | 能验证 |
|---|---|---|
| 直接执行 | 读数据(RU)、慢、可能写 | 全部 |
| 事务 + ROLLBACK | DML 可回滚, **DDL 隐式提交不可回滚** | 全 |
| **EXPLAIN** | **零**: 只做名称解析 + 计划, 不读行、不加锁、不执行 | 表/列/别名/GROUP BY/语法/函数 |

实测(TiDB Serverless, 2026-11-03):
- `EXPLAIN SELECT sku, no_such_col FROM inventory` → 1054 Unknown column ✓
- `EXPLAIN SELECT * FROM no_such_table` → 1146 Table doesn't exist ✓
- `EXPLAIN SELECT sku, channel, SUM(qty) FROM inventory GROUP BY sku` → 1055 ONLY_FULL_GROUP_BY ✓
- `EXPLAIN DELETE FROM inventory` → 前后 `COUNT(*)` 均 17006, **未删任何行** ✓
- `EXPLAIN INSERT / UPDATE / DELETE / WITH` 均支持(TiDB) ✓

所以可以对着**生产库**跑, 不需要测试库、不需要造数据。

## 怎么收集 SQL

`audit(fn)` 包住 mock 函数, 记录 `(sql, params, 调用点)`。关键约束:
**routes 里的 `from db import query` 在 import 时完成绑定**, 所以只需在 local_test 首次
import 路由之前包住那几个 mock; 之后再改 `db.query` 对已 import 的模块无效(这是 Python
名字绑定的语义, 不是偷懒)。见 local_test.py 里的 `_A(...)` 调用点。

## 零误报原则

门禁只有在"报出来的都是真错"时才有人看。因此:
- 参数个数不匹配(`%s` 数量对不上) → 先用 NULL 填充重试, 通过则记 ⚠ 不记失败;
- 只有**名称解析类**错误码(1054/1146/1052/1055/1064/1109/1051/1247/1210)才算 ✗;
- 其他(类型截断/超时/权限)一律 ⚠ 并原样打印, 供人判断。

## 用法

    python3 local_test.py                # 只跑回归, 不连库
    python3 local_test.py --sql-smoke    # 回归 + SQL 冒烟(需 TIDB_* 凭据)
    python3 sql_smoke.py --self-test     # 只验校验器自身(正负例, 不需要库)
"""
import functools
import os
import re
import sys
import traceback

_API_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(os.path.dirname(_API_DIR))
_ENV_FILE = os.path.join(_ROOT_DIR, ".env")

ENABLED = False
_SEEN = {}          # 规范化 SQL -> {sql, params, hits, where}
_STATS = {"calls": 0}
_MIN_SQL = 200      # 唯一 SQL 基线下限(2026-11-03 实测 388) —— 低于它说明收集失效, 不是"业务变少了"

# 名称解析类错误码 —— 这些一定是代码与 schema 不一致, 必须失败
_FATAL = {
    1054: "列不存在",
    1146: "表不存在",
    1051: "表不存在",
    1052: "列名歧义",
    1055: "GROUP BY 不完整(ONLY_FULL_GROUP_BY)",
    1064: "语法错误",
    1109: "字段列表里引用了不存在的表",
    1247: "引用了不支持的别名/表",
    1210: "参数类型/个数不正确",
}

_EXPLAINABLE = {"select", "with", "insert", "update", "delete", "replace"}
# 非查询语句: EXPLAIN 不支持, 也不在做"SQL 能否执行"的验证范围内
_SKIP_HEAD = {
    "show", "set", "desc", "describe", "create", "alter", "drop", "truncate",
    "optimize", "analyze", "begin", "start", "commit", "rollback", "use",
    "call", "explain", "grant", "revoke", "flush", "kill",
}

_HEAD_RE = re.compile(r"^\s*(?:/\*.*?\*/|--[^\n]*\n|#[^\n]*\n|\s)*([A-Za-z_]+)", re.S)
_PLACEHOLDER_RE = re.compile(r"%[sd]")


# ── 收集阶段(测试跑的时候, 只记账不连库) ─────────────────────────────
def _norm(sql):
    """规范化去重键: 空白归一 + 小写。参数不同不重复验证(同一条 SQL 文本)"""
    return re.sub(r"\s+", " ", sql).strip().lower()


def _collapse(sql):
    return re.sub(r"\s+", " ", sql).strip()


def _callsite():
    """最后一个非本模块/非测试文件的栈帧 —— 报错要能指到"改哪个文件" """
    for fr in reversed(traceback.extract_stack()[:-2]):
        f = fr.filename.replace("\\", "/")
        if f.endswith("sql_smoke.py") or f.endswith("local_test.py"):
            continue
        try:
            rel = os.path.relpath(f, _API_DIR)
        except ValueError:
            rel = f
        return "%s:%d" % (rel, fr.lineno)
    return "?"


def _sample_params(p):
    """executemany 传的是 list[tuple] —— 取第一条做样本"""
    if isinstance(p, (list, tuple)):
        if p and isinstance(p[0], (list, tuple, dict)):
            return tuple(p[0].values()) if isinstance(p[0], dict) else tuple(p[0])
        return tuple(p)
    if isinstance(p, dict):
        return tuple(p.values())
    return ()


def collect(sql, params=None):
    if not ENABLED:
        return
    try:
        s = str(sql)
        if not s.strip():
            return
        _STATS["calls"] += 1
        key = _norm(s)
        hit = _SEEN.get(key)
        if hit is not None:
            hit["hits"] += 1
            return
        _SEEN[key] = {"sql": _collapse(s), "params": _sample_params(params),
                      "hits": 1, "where": _callsite()}
    except Exception:
        pass   # 审计自身的任何异常都不得改变测试行为


def audit(fn):
    """包装 mock 函数: 记账 → 原样转发。未启用时零开销(直接返回原函数)"""
    if not ENABLED:
        return fn

    @functools.wraps(fn)
    def wrapper(sql, *args, **kwargs):
        params = args[0] if args else kwargs.get("params")
        collect(sql, params)
        return fn(sql, *args, **kwargs)

    return wrapper


# ── 分类 ──────────────────────────────────────────────────────────
def explain_sql(sql):
    """返回可执行的验证语句, 或 None 表示不验证(DDL/SHOW/SET…)"""
    m = _HEAD_RE.match(sql or "")
    if not m:
        return None
    head = m.group(1).lower()
    if head in _EXPLAINABLE:
        return "EXPLAIN " + sql.strip().rstrip(";")
    return None


def is_skipped(sql):
    m = _HEAD_RE.match(sql or "")
    return bool(m) and m.group(1).lower() in _SKIP_HEAD


def classify_error(exc):
    """(级别, 错误码, 说明)。级别: fatal / warn"""
    code = None
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        code = args[0]
    if code is None:
        return "warn", None, str(exc)[:160]
    if code in _FATAL:
        return "fatal", code, "%s | %s" % (_FATAL[code], str(exc)[:140])
    return "warn", code, str(exc)[:160]


# ── 校验阶段 ──────────────────────────────────────────────────────
def _load_env():
    # CI 直接用 secrets 注入环境变量, 没有 .env 文件 —— 已注入就以注入值为准
    if os.environ.get("TIDB_HOST"):
        return True
    if not os.path.exists(_ENV_FILE):
        return False
    for line in open(_ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return bool(os.environ.get("TIDB_HOST"))


def _connect():
    import pymysql
    return pymysql.connect(
        host=os.environ.get("TIDB_HOST"),
        port=int(os.environ.get("TIDB_PORT", "4000")),
        user=os.environ.get("TIDB_USER"),
        password=os.environ.get("TIDB_PASSWORD"),
        database=os.environ.get("TIDB_DB") or "supplykit",
        ssl={"ca": None} if os.environ.get("TIDB_SSL", "true").lower() != "false" else None,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
        connect_timeout=15,
        read_timeout=15,
    )


def _run(cur, sql, params):
    """执行验证语句。参数对不上时用 NULL 填充重试(避免把"mock 参数形状"误报成 SQL 错)"""
    try:
        cur.execute(sql, params or ())
    except Exception as e:
        msg = str(e).lower()
        if "format string" in msg or "not enough arguments" in msg or "arguments" in msg:
            cur.execute(_PLACEHOLDER_RE.sub("NULL", sql))
            return True   # param_warn
        raise
    return False


_CORE_TABLES = ("inventory", "orders", "rules")


def probe_db(conn):
    """目标库指纹: 核心表必须都在。

    为什么需要: 凭据指向另一个集群/空库时, 260 条 SQL 会**一起**报 1146 —— 那不是 260 个
    缺陷, 是一个环境错。先回答"这是不是本项目的库", 才谈得上逐条诊断。
    (2026-11-03 CI 实际踩到: secrets 里的 TIDB_* 指向一个没有 channels/schema_state 的库。)
    """
    ph = ",".join(["%s"] * len(_CORE_TABLES))
    cur = conn.cursor()
    try:
        cur.execute("SELECT DATABASE() AS db")
        db = str((cur.fetchone() or {}).get("db") or "")
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name IN (%s)" % ph, _CORE_TABLES)
        n = int((cur.fetchone() or {}).get("n") or 0)
    finally:
        cur.close()
    return db, n


def verify(verbose=False):
    """连库逐条 EXPLAIN。返回 (fail_list, warn_list, skipped, total)"""
    if not _SEEN:
        return ([("no-collect", "未收集到任何 SQL —— 审计包装未生效", "")], [], 0, 0)
    # 覆盖率下限: 2026-11-03 基线 388 条唯一 SQL。腰斩只可能是"包装被拆"或"大量分支没跑到",
    # 两种都让门禁失去意义 —— 宁可红, 不可假绿。
    if len(_SEEN) < _MIN_SQL:
        return ([("cov", "唯一 SQL 仅 %d 条, 低于基线下限 %d —— 收集可能失效" % (len(_SEEN), _MIN_SQL), "")],
                [], 0, len(_SEEN))
    if not _load_env():
        return None   # 调用方负责报"无凭据未执行"
    try:
        conn = _connect()
    except Exception as e:
        return ([("connect", "连接失败: %s" % str(e)[:120], "")], [], 0, len(_SEEN))

    _dbname, _ncore = probe_db(conn)
    if _ncore < len(_CORE_TABLES):
        try:
            conn.close()
        except Exception:
            pass
        return ([("db", "目标库 `%s` 缺核心表 %d/%d(%s) —— 凭据指向的可能不是本项目的库, 或迁移未跑"
                  % (_dbname, _ncore, len(_CORE_TABLES), "/".join(_CORE_TABLES)), "")],
                [], 0, len(_SEEN))

    failed, warned, skipped, ok = [], [], 0, 0
    cur = conn.cursor()
    try:
        for item in _SEEN.values():
            stmt = explain_sql(item["sql"])
            if stmt is None:
                if is_skipped(item["sql"]):
                    skipped += 1
                continue
            try:
                if _run(cur, stmt, item["params"]):
                    warned.append((None, "参数个数不匹配(已用 NULL 填充验证通过)", item))
                else:
                    ok += 1
                try:
                    cur.fetchall()
                except Exception:
                    pass
            except Exception as e:
                level, code, why = classify_error(e)
                row = (code if code is not None else "?", why, item)
                (failed if level == "fatal" else warned).append(row)
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
    return (failed, warned, skipped, len(_SEEN) - skipped)


def report(fail_n_before=0):
    """打印结果并返回"本次新增失败数" """
    calls = _STATS["calls"]
    total = len(_SEEN)
    files = {}
    for it in _SEEN.values():
        f = it["where"].rsplit(":", 1)[0]
        files[f] = files.get(f, 0) + 1
    print("\n── 真实 SQL 冒烟(EXPLAIN on TiDB) ──")
    print("  收集 %d 次调用 → 唯一 SQL %d 条, 覆盖 %d 个后端模块" % (calls, total, len(files)))
    res = verify()
    if res is None:
        print("  ⚠ 未执行: 缺 TIDB_* 凭据(不是通过 —— 只是没验)")
        return 1 if os.environ.get("SQL_SMOKE_REQUIRED") == "1" else 0
    failed, warned, skipped, checked = res

    def _detail(item):
        """前置失败(连接/覆盖率/库指纹)没有 _SEEN 条目, 三元组第三项是空串哨兵;
        只有逐条 SQL 失败才带 dict。旧实现无条件 item["where"] → 前置失败时
        崩成 TypeError, 把「配额耗尽 / 凭据指向错库」这类明确诊断淹没在 traceback 里,
        门禁反而说不清为什么红。统一成 (where, sql) 二元组, 缺失就留空。"""
        if isinstance(item, dict):
            return item.get("where", ""), item.get("sql", "")
        return "", ""

    print("  校验 %d 条(跳过非查询 %d 条) → ✓ %d / ✗ %d / ⚠ %d"
          % (checked, skipped, max(0, checked - len(failed) - len(warned)), len(failed), len(warned)))
    if skipped:
        heads = {}
        for item in _SEEN.values():
            if explain_sql(item["sql"]) is None:
                m = _HEAD_RE.match(item["sql"])
                k = (m.group(1).lower() if m else "?")
                heads[k] = heads.get(k, 0) + 1
        print("    跳过明细: " + ", ".join("%s×%d" % (k, v) for k, v in sorted(heads.items())))
        print("    (DDL/SHOW/SET 不在 EXPLAIN 能力范围内 —— 结构一致性由 schema 对账覆盖, 见 --schema-diff)")
    for code, why, item in failed:
        print("  ✗ [%s] %s" % (code, why))
        _w, _s = _detail(item)
        if _w:
            print("      %s" % _w)
        if _s:
            print("      %s" % (_s[:200] + ("…" if len(_s) > 200 else "")))
    for code, why, item in warned[:10]:
        print("  ⚠ [%s] %s" % (code, why))
        _w, _s = _detail(item)
        if _w:
            print("      %s" % _w)
        if _s:
            print("      %s" % (_s[:160] + ("…" if len(_s) > 160 else "")))
    if len(warned) > 10:
        print("  ⚠ 另有 %d 条告警未展开" % (len(warned) - 10))

    # 结构对账: DDL 跳过 125 条不代表"没问题", 只是 EXPLAIN 管不着 —— 换一种方式验
    if any(c == "db" for c, _, _ in failed):
        print("\n── schema 对账 ──\n  跳过: 目标库指纹不符(先修凭据, 逐条报错没有意义)")
        return len(failed)
    try:
        _c2 = _connect()
        try:
            n_schema = report_schema(_c2)
        finally:
            _c2.close()
    except Exception as e:
        print("\n── schema 对账 ──\n  ⚠ 未执行: %s" % str(e)[:120])
        n_schema = 0
    return len(failed) + n_schema


# ── 结构对账(DDL 无法 EXPLAIN, 但"声明了却没建"是同一类事故) ─────────
_CONSTRAINT_HEADS = {"primary", "unique", "key", "index", "constraint",
                     "fulltext", "spatial", "foreign", "check"}
# 列定义里紧跟列名的第一个词一定是类型。用它区分「列名恰好叫 key」与「KEY idx_x (...)」:
#   约束行的关键字后面跟的是**索引名或括号**, 列定义后面跟的是**类型关键字**。
#   2026-11-05 修: 原来只按首 token 判约束, 于是 replenishment_config /
#   replenishment_config_history / analysis_cache 三张表的 `key` 列被当成索引行跳过 ——
#   方向是"声明了却不校验"(真缺列会漏报), 表面则表现为对账报"库里多出 3 列"的假阳性。
_TYPE_HEADS = {"varchar", "char", "tinytext", "text", "mediumtext", "longtext",
               "tinyint", "smallint", "mediumint", "int", "integer", "bigint",
               "decimal", "numeric", "float", "double", "real",
               "date", "datetime", "timestamp", "time", "year",
               "json", "boolean", "bool", "enum", "set", "bit",
               "blob", "tinyblob", "mediumblob", "longblob",
               "binary", "varbinary", "geometry", "point"}


def parse_columns(ddl):
    """从 CREATE TABLE 提取列名: 顶层逗号切分, 跳过约束/索引行。

    为什么不用正则直接扫 `\\w+ TYPE`: 会误抓 `KEY idx (col)` 里的列, 也抓不到
    无类型修饰的列。按括号深度切分是唯一稳的办法。
    """
    m = re.search(r"\(", ddl or "")
    if not m:
        return []
    body = ddl[m.end():]
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break          # 走到表定义的闭合括号
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    cols = []
    for p in parts:
        tok = re.match(r"\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", p)
        if not tok:
            continue
        name = tok.group(1)
        if name.lower() in _CONSTRAINT_HEADS:
            # 关键字后面跟类型 = 这是一列(列名与约束关键字同名, 如 `key`/`index`);
            # 跟索引名或 `(` = 真的是约束/索引行, 跳过。
            _nxt = re.match(r"\s*`?([A-Za-z_][A-Za-z0-9_]*)`?", p[tok.end():])
            if not _nxt or _nxt.group(1).lower() not in _TYPE_HEADS:
                continue
        cols.append(name)
    return cols


def _declared():
    """代码里声明的 (表 -> 列集合) 与 (索引集合)"""
    import schema_create as sc
    tables = {}
    for name, ddl in sc._TABLES.items():
        tables[name] = set(parse_columns(ddl))
    for t, adds in getattr(sc, "_COLUMN_ADDS", {}).items():
        tables.setdefault(t, set())
        for item in adds:
            col = item[0] if isinstance(item, (list, tuple)) else item
            tables[t].add(col)
    idx = set()
    for lst in ("_INDEXES", "_ADD_INDEXES"):
        for row in getattr(sc, lst, []) or []:
            idx.add((row[0], row[1]))
    return tables, idx


def schema_diff(conn):
    """返回 (missing_cols, extra_cols, missing_idx, n_tables, n_cols)"""
    tables, idx = _declared()
    cur = conn.cursor()
    try:
        cur.execute("SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.columns "
                    "WHERE TABLE_SCHEMA = DATABASE()")
        real = {}
        for r in cur.fetchall():
            real.setdefault(str(r["t"]), set()).add(str(r["c"]))
        cur.execute("SELECT DISTINCT TABLE_NAME AS t, INDEX_NAME AS i FROM information_schema.statistics "
                    "WHERE TABLE_SCHEMA = DATABASE()")
        real_idx = {(str(r["t"]), str(r["i"])) for r in cur.fetchall()}
    finally:
        cur.close()

    missing_cols, extra_cols = [], []
    for t, cols in tables.items():
        if t not in real:
            missing_cols.append((t, "<整表不存在>"))
            continue
        for c in sorted(cols - real[t]):
            missing_cols.append((t, c))
        # 库里多出来的列只提示, 不判错(历史遗留列不影响新代码执行)
        for c in sorted(real[t] - cols):
            extra_cols.append((t, c))
    missing_idx = [(n, t) for (n, t) in sorted(idx) if (t, n) not in real_idx]
    return missing_cols, extra_cols, missing_idx, len(tables), sum(len(v) for v in tables.values())


def report_schema(conn):
    db, n = probe_db(conn)
    if n < len(_CORE_TABLES):
        print("\n── schema 对账 ──")
        print("  ✗ 目标库 `%s` 缺核心表 %d/%d(%s) —— 不是本项目的库, 对账无意义"
              % (db, n, len(_CORE_TABLES), "/".join(_CORE_TABLES)))
        return 1
    missing_cols, extra_cols, missing_idx, nt, nc = schema_diff(conn)
    print("\n── schema 对账(代码声明 vs 真实库) ──")
    print("  声明 %d 张表 / %d 列 / %d 个索引" % (nt, nc, len(_declared()[1])))
    if missing_cols:
        print("  ✗ 声明了但库里没有 %d 项(迁移没跑 / 列名写错):" % len(missing_cols))
        for t, c in missing_cols[:15]:
            print("      %s.%s" % (t, c))
        if len(missing_cols) > 15:
            print("      … 另 %d 项" % (len(missing_cols) - 15))
    else:
        print("  ✓ 所有声明的列都存在")
    if missing_idx:
        print("  ✗ 声明了但库里没有的索引 %d 个:" % len(missing_idx))
        for n, t in missing_idx[:10]:
            print("      %s (%s)" % (n, t))
    else:
        print("  ✓ 所有声明的索引都存在")
    if extra_cols:
        print("  ⚠ 库里多出 %d 列(代码未声明, 不影响执行): %s"
              % (len(extra_cols), ", ".join("%s.%s" % x for x in extra_cols[:8])))
    return len(missing_cols) + len(missing_idx)


# ── 自检(锁住校验器能力: 改代码后不能悄悄变成"什么都验不出") ─────────
def _self_test():
    bad = 0

    def ck(name, cond, extra=""):
        nonlocal bad
        print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else "  " + str(extra)))
        if not cond:
            bad += 1

    ck("SELECT → 加 EXPLAIN", explain_sql("SELECT 1") == "EXPLAIN SELECT 1")
    ck("小写/前导空白", explain_sql("  select * from t") == "EXPLAIN select * from t")
    ck("注释前缀可识别", explain_sql("/* c */ SELECT 1") == "EXPLAIN /* c */ SELECT 1")
    ck("WITH 可 EXPLAIN", (explain_sql("WITH x AS (SELECT 1) SELECT * FROM x") or "").startswith("EXPLAIN WITH"))
    ck("INSERT/UPDATE/DELETE 可 EXPLAIN",
       all((explain_sql(s) or "").startswith("EXPLAIN") for s in
           ["INSERT INTO t (a) VALUES (%s)", "UPDATE t SET a=1", "DELETE FROM t"]))
    ck("SHOW 不验", explain_sql("SHOW TABLES") is None and is_skipped("SHOW TABLES"))
    ck("CREATE/ALTER 不验", explain_sql("CREATE TABLE t (a INT)") is None)
    ck("去尾分号", explain_sql("SELECT 1;") == "EXPLAIN SELECT 1")
    ck("空白归一", _norm("SELECT  a\n FROM   t") == "select a from t")

    ck("1054 判致命", classify_error(type("E", (Exception,), {"args": (1054, "x")})())[:2] == ("fatal", 1054))
    ck("1146 判致命", classify_error(type("E", (Exception,), {"args": (1146, "x")})())[0] == "fatal")
    ck("1055 判致命", classify_error(type("E", (Exception,), {"args": (1055, "x")})())[0] == "fatal")
    ck("1064 判致命", classify_error(type("E", (Exception,), {"args": (1064, "x")})())[0] == "fatal")
    ck("1366(截断)只告警", classify_error(type("E", (Exception,), {"args": (1366, "x")})())[0] == "warn")
    ck("非数字码只告警", classify_error(ValueError("boom"))[0] == "warn")

    ck("占位符 NULL 填充", _PLACEHOLDER_RE.sub("NULL", "a=%s AND b=%d") == "a=NULL AND b=NULL")
    ck("%% 不被误伤", _PLACEHOLDER_RE.sub("NULL", "LIKE '%%x'") == "LIKE '%%x'")
    ck("executemany 取样", _sample_params([(1, 2), (3, 4)]) == (1, 2))
    ck("空参数", _sample_params(None) == ())

    # 列名解析: 这是 schema 对账的唯一入口, 解析错了会"假绿"
    _ddl = ("CREATE TABLE IF NOT EXISTS t (id BIGINT AUTO_INCREMENT PRIMARY KEY, "
            "`sku` VARCHAR(64) NOT NULL, amt DECIMAL(12,2) DEFAULT 0, "
            "UNIQUE KEY uk_t (sku), KEY idx_a (amt, sku))")
    ck("列名解析(DECIMAL 里的逗号不切断)", parse_columns(_ddl) == ["id", "sku", "amt"],
       parse_columns(_ddl))
    ck("约束行不当列", "unique" not in [c.lower() for c in parse_columns(_ddl)])
    ck("单列无约束也能解析", parse_columns("CREATE TABLE t (a INT)") == ["a"])
    ck("空 DDL 不炸", parse_columns("") == [])
    print("SQL 冒烟自检: %d 失败" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    if "--schema-diff" in sys.argv:
        if not _load_env():
            print("⚠ 缺 TIDB_* 凭据, 未执行")
            sys.exit(0)
        _cc = _connect()
        try:
            sys.exit(1 if report_schema(_cc) else 0)
        finally:
            _cc.close()
    print("用法: python3 local_test.py --sql-smoke  或  python3 sql_smoke.py --self-test|--schema-diff")
