"""Makers 入口 —— 最小版 + TiDB 连接验证端点 + Phase2 迁移工具"""
import os
from fastapi import FastAPI
import pymysql

from migrate import build as migrate_build, seed_small as migrate_seed, ru_test as migrate_ru, tables as migrate_tables

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "msg": "supplykit-edgeone"}


@app.get("/tidb-test")
def tidb_test():
    """验证 Makers 云函数 -> TiDB 链路: 认证/TLS/时区/建库/读写"""
    out = {}
    host = os.environ.get("TIDB_HOST")
    port = int(os.environ.get("TIDB_PORT", "4000"))
    user = os.environ.get("TIDB_USER")
    password = os.environ.get("TIDB_PASSWORD")
    db = os.environ.get("TIDB_DB", "supplykit")
    out["env"] = {"host": bool(host), "port": bool(port), "user": bool(user), "password": bool(password)}
    if not (host and user and password):
        out["error"] = "缺少 TIDB_HOST/TIDB_USER/TIDB_PASSWORD"
        return out
    try:
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password,
            ssl={"ca": None}, connect_timeout=10,
            read_timeout=20, write_timeout=20,
        )
        cur = conn.cursor()
        cur.execute("SELECT VERSION()")
        out["version"] = cur.fetchone()[0]
        cur.execute("SELECT @@system_time_zone, @@time_zone, NOW()")
        row = cur.fetchone()
        out["timezone"] = {"system": row[0], "session": row[1], "now": str(row[2])}
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db}`")
        cur.execute(f"USE `{db}`")
        cur.execute("CREATE TABLE IF NOT EXISTS _conn_test (id INT PRIMARY KEY, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("INSERT INTO _conn_test (id) VALUES (1) ON DUPLICATE KEY UPDATE ts=CURRENT_TIMESTAMP")
        conn.commit()
        cur.execute("SELECT id, ts FROM _conn_test")
        out["rw"] = str(cur.fetchone())
        cur.execute("DROP TABLE _conn_test")
        conn.commit()
        cur.close(); conn.close()
        out["status"] = "OK"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


@app.get("/")
def root():
    return {"ok": True}


# ─── Phase2 迁移工具端点(临时, 完成后删除) ─────────────────────────────

@app.get("/migrate/build")
def migrate_build_route():
    """建表 DDL(幂等)"""
    try:
        return migrate_build()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@app.get("/migrate/seed")
def migrate_seed_route(n_orders: int = 5000):
    """小批量虚拟数据(默认 5000 单)"""
    try:
        return migrate_seed(n_orders=n_orders)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@app.get("/migrate/ru-test")
def migrate_ru_route():
    """关键查询 EXPLAIN ANALYZE(RU 实测)"""
    try:
        return migrate_ru()
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}


@app.get("/migrate/tables")
def migrate_tables_route():
    """TiDB 表清单"""
    try:
        return {"tables": migrate_tables()}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}
