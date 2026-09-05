"""Makers 入口 —— 最小版 + TiDB 连接验证端点"""
import os
from fastapi import FastAPI
import pymysql

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
