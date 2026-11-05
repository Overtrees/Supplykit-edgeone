#!/usr/bin/env python3
"""把已有的第二集群 `Supplykit` 清空并当成新库做全量适配。

2026-11-05: 原集群 RU 配额用尽, 启用 EdgeOne `supplykit-backed` 项目用的那套
独立凭据(user 与旧集群不同, 库名 Supplykit)。该库有 32 张遗留表 / 30 万订单,
**全部清空**后按当前代码版本重建: 建表 → 索引 → 迁移 → 基础配置 → 联动验证。

凭据来源: EdgeOne DescribePagesProjectEnvs(Production), 运行时现取, 不硬编码。
落盘: 写 .env.local(gitignore) 供后续脚本/部署使用; 审计留痕写 .artifacts/。

用法:
  python3 scripts/rebuild_supplykit.py --audit   # 只看遗留库清单, 不动任何数据
  python3 scripts/rebuild_supplykit.py           # 清空 + 全量重建 + 验证
"""
import json
import os
import sys
import time
import urllib.request

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://pages-api.cloud.tencent.com/v1"
BACKED_PROJECT = "makers-vltewg30sszj"   # supplykit-backed = 第二套凭据所在项目


def edgeone_token():
    # 优先用运行时传入的 token(附件给的新 token); 回落 .env 里的旧变量名
    t = os.environ.get("EDGEONE_API_TOKEN", "").strip()
    if t:
        return t
    for ln in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        ln = ln.strip()
        if ln.startswith("EDGEONE_PAGES_API_TOKEN="):
            return ln.split("=", 1)[1]
    return ""


def call(action, **kw):
    body = json.dumps({"Action": action, **kw}).encode()
    req = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + edgeone_token()})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if r.get("Code") != 0:
        raise RuntimeError("%s Code=%s %s" % (action, r.get("Code"), r.get("Message")))
    return (r.get("Data") or {}).get("Response") or {}


def target_env():
    """第二套凭据(Production 档), 只取需要的键"""
    want = ("TIDB_HOST", "TIDB_PORT", "TIDB_USER", "TIDB_PASSWORD",
            "TIDB_DB", "TIDB_SSL", "DB_BACKEND")
    resp = call("DescribePagesProjectEnvs", ProjectId=BACKED_PROJECT)
    out = {}
    for e in resp.get("EnvVars") or []:
        k = e.get("Key")
        if k in want and "Production" in (e.get("Env") or []):
            out[k] = str(e.get("Value") or "")
    missing = [k for k in want if not out.get(k) and k not in ("TIDB_SSL",)]
    if missing:
        raise RuntimeError("EdgeOne 未返回完整的 Production 凭据: %s" % missing)
    return out


def connect(env):
    return pymysql.connect(
        host=env["TIDB_HOST"], port=int(env.get("TIDB_PORT", "4000")),
        user=env["TIDB_USER"], password=env["TIDB_PASSWORD"],
        database=env["TIDB_DB"],
        ssl={"ca": None} if env.get("TIDB_SSL", "true").lower() != "false" else None,
        autocommit=True, cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4", connect_timeout=20, read_timeout=120, write_timeout=120,
    )


def list_tables(cur, db):
    cur.execute("SELECT table_name, table_rows FROM information_schema.tables "
                "WHERE table_schema=%s ORDER BY table_name", [db])
    return [(r["table_name"], int(r["table_rows"] or 0)) for r in cur.fetchall()]


def audit(env, cur):
    tables = list_tables(cur, env["TIDB_DB"])
    total = sum(n for _, n in tables)
    print("遗留库 %s: %d 张表 / 约 %d 行" % (env["TIDB_DB"], len(tables), total))
    for t, n in tables:
        print("   %-32s %8d" % (t, n))
    os.makedirs(os.path.join(ROOT, ".artifacts"), exist_ok=True)
    p = os.path.join(ROOT, ".artifacts", "supplykit_legacy_audit.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "db": env["TIDB_DB"], "tables": tables, "rows_approx": total},
                  f, ensure_ascii=False, indent=2)
    print("审计留痕 → %s" % p)
    return tables


def drop_all(cur, tables):
    print("\n[1/5] 清空遗留表 ...")
    # 关掉外键检查再逐个 DROP(TiDB 无强制外键, 但保险)
    try:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
    except Exception:
        pass
    done, failed = 0, []
    for t, _ in tables:
        try:
            cur.execute("DROP TABLE IF EXISTS `%s`" % t)
            done += 1
        except Exception as e:
            failed.append("%s: %s" % (t, str(e)[:120]))
    try:
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
    except Exception:
        pass
    print("   DROP %d 张, 失败 %d" % (done, len(failed)))
    for f in failed:
        print("   ⚠", f)
    return not failed


def ensure_schema_via_code(env):
    """用当前代码版本的 schema 门禁全量建表/索引/迁移(单一真源)"""
    print("\n[2/5] ensure_schema() 全量建表 + 索引 + 迁移 ...")
    for k, v in env.items():
        os.environ[k] = v
    sys.path.insert(0, os.path.join(ROOT, "cloud-functions", "api"))
    # 清掉可能的旧连接缓存
    for m in list(sys.modules):
        if m in ("db", "schema_create", "migrations.runner") or m.startswith("routes."):
            del sys.modules[m]
    import schema_create
    schema_create.ensure_schema()
    from db import one
    row = one("SELECT version FROM schema_state WHERE name='main'")
    print("   schema_state.version =", row)
    return schema_create.SCHEMA_VERSION


def bootstrap(cur, env):
    """基础数据: 让空库具备全部业务功能(这些不是演示数据, 是运行必需配置)"""
    print("\n[3/5] 基础配置(channels / admin / 补货参数 / 内置规则) ...")
    # ① 渠道种子 —— create_all_tables 里那次在表建好之前执行会失败并被吞掉, 这里补一次
    cur.execute("INSERT IGNORE INTO channels(code,name,enabled,supports_b,default_mode,sort) "
                "VALUES('jd','京东渠道',1,1,'bbcc',1),('other','其他渠道',1,0,'traditional',2)")
    cur.execute("SELECT COUNT(*) c FROM channels")
    print("   channels =", cur.fetchone()["c"])
    # ② admin 账号(与线上同一套口令哈希)
    sys.path.insert(0, os.path.join(ROOT, "cloud-functions", "api"))
    from routes.common import hash_password
    cur.execute("INSERT IGNORE INTO users(username,password_hash,role) VALUES(%s,%s,%s)",
                ["admin", hash_password("admin123"), "admin"])
    cur.execute("SELECT COUNT(*) c FROM users")
    print("   users =", cur.fetchone()["c"])
    # ③ 补货参数 + 内置规则: 直接复用 seed_fill._seed_config(与线上同一份定义, 不维护副本)
    try:
        from routes.seed_fill import _seed_config
        _seed_config()
        cur.execute("SELECT COUNT(*) c FROM replenishment_config")
        n_cfg = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM rules")
        n_rule = cur.fetchone()["c"]
        print("   replenishment_config = %d, rules = %d" % (n_cfg, n_rule))
    except Exception as e:
        print("   ⚠ _seed_config 降级:", str(e)[:200])


def verify(cur, env, version):
    print("\n[4/5] 全链路联动验证 ...")
    tables = list_tables(cur, env["TIDB_DB"])
    names = {t for t, _ in tables}
    print("   表数 = %d" % len(names))
    # 关键表必须都在(覆盖所有业务模块)
    need = ["users", "channels", "orders", "inventory", "products", "suppliers",
            "batches", "inbound_records", "outbound_records", "alerts", "rules",
            "replenishment_config", "replenishment_config_history", "daily_sales_snapshot",
            "sku_sales_stats", "orders_day_agg", "inventory_month", "batch_opening",
            "ledger_state", "ledger_begin_adj", "sync_tasks", "export_files", "gmv_flows",
            "cleansing_templates", "cleansing_errors", "quality_logs", "analysis_cache",
            "schema_state", "schema_migrations", "seed_watermark", "data_version",
            "data_freshness", "health_snapshot", "purchase_orders", "disposal_records",
            "custom_fields", "events", "warehouse_registry", "maintenance_log",
            "log_archives", "pipeline_runs", "agg_dirty", "agg_dirty_days", "eval_pending"]
    miss = [t for t in need if t not in names]
    print("   关键表缺失 =", miss or "无")
    # 索引抽检
    cur.execute("SHOW INDEX FROM orders")
    oidx = {r["Key_name"] for r in cur.fetchall()}
    print("   orders 四维业务键 uq_orders_biz =", "uq_orders_biz" in oidx)
    # 版本
    cur.execute("SELECT version FROM schema_state WHERE name='main'")
    v = cur.fetchone()
    print("   schema_state.version = %s (期望 %s)" % ((v or {}).get("version"), version))
    # 各模块关键查询在空库下能跑通(不为 0 行, 但不报错 = 联动可用)
    probes = [
        "SELECT COUNT(*) c FROM channels",
        "SELECT COUNT(*) c FROM users",
        "SELECT COUNT(*) c FROM rules",
        "SELECT COUNT(*) c FROM replenishment_config",
        "SELECT COUNT(*) c FROM orders",
        "SELECT COUNT(*) c FROM inventory",
        "SELECT COUNT(*) c FROM orders_day_agg",
        "SELECT COUNT(*) c FROM sku_sales_stats",
        "SELECT COUNT(*) c FROM schema_migrations",
    ]
    errs = []
    for q in probes:
        try:
            cur.execute(q)
            cur.fetchone()
        except Exception as e:
            errs.append("%s → %s" % (q[:50], str(e)[:100]))
    print("   联动 SQL 探测失败 =", errs or "无")
    return not miss and not errs and int((v or {}).get("version") or 0) >= version


def write_env_local(env):
    """按附件模板把凭据写进 .env.local(gitignore, 不进仓库/资料库)"""
    lines = ["# 2026-11-05 切到第二集群 Supplykit; 由 scripts/rebuild_supplykit.py 生成",
             "# 本文件已被 .gitignore 忽略, 禁止提交"]
    for k in ("TIDB_HOST", "TIDB_PORT", "TIDB_USER", "TIDB_PASSWORD", "TIDB_DB",
              "TIDB_SSL", "DB_BACKEND"):
        if env.get(k):
            lines.append("%s=%s" % (k, env[k]))
    # 安全/部署类沿用 .env 现有值(不打印)
    for ln in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
        ln = ln.strip()
        if ln.startswith(("JWT_SECRET=", "CRON_SECRET=", "EDGEONE_PAGES_API_TOKEN=",
                          "GITHUB_TOKEN=")):
            lines.append(ln)
    p = os.path.join(ROOT, ".env.local")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n[5/5] 凭据已写入 %s (gitignore, 不进仓库)" % p)


def main():
    audit_only = "--audit" in sys.argv
    env = target_env()
    print("目标: %s / %s / db=%s" % (env["TIDB_HOST"][:28] + "...",
                                     env["TIDB_USER"][:10] + "...", env["TIDB_DB"]))
    conn = connect(env)
    cur = conn.cursor()
    tables = audit(env, cur)
    if audit_only:
        conn.close()
        return 0
    if not drop_all(cur, tables):
        print("DROP 有失败, 中止(不带着半清空状态继续)")
        conn.close()
        return 1
    # 确认真的空了
    left = list_tables(cur, env["TIDB_DB"])
    print("   剩余表 =", len(left), left[:5])
    if left:
        print("未清空, 中止")
        conn.close()
        return 1
    version = ensure_schema_via_code(env)
    bootstrap(cur, env)
    ok = verify(cur, env, version)
    write_env_local(env)
    conn.close()
    print("\n结果:", "全量适配完成" if ok else "有缺口, 见上方 ⚠")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
