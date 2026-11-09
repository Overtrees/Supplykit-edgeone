#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引导/修复管理员账号 —— 让空库具备"可登录"这一运行必需条件。

背景（2026-11-09）
------------------
线上 `admin` 登录不上，根因**不是密码错**，而是 `users` 表 **0 行**：
新集群只跑了 `schema_create.ensure_schema()`（建表 64 张），
`scripts/rebuild_supplykit.py` 的 `bootstrap()`（会 `INSERT IGNORE` admin）没跑，
而 `/auth/setup` 在未配 `SETUP_TOKEN` 的生产环境里**一律 403** ——
于是没有任何一条路径能创建账号，只剩硬编码的 `demo`（只读，写全 403）。

本脚本就是那条缺失的路径：**幂等**、**不依赖部署**、**不打印明文**。

用法
----
    python3 scripts/bootstrap_admin.py --check          # 只体检，不改数据（CI/门禁可用）
    python3 scripts/bootstrap_admin.py --create         # 有则跳过、无则创建
    python3 scripts/bootstrap_admin.py --reset          # 覆盖口令（轮换/找回）

口令来源（优先级）：`--password` > 环境变量 `ADMIN_PASSWORD` > 默认 `admin123`
（默认值是仓库既有约定：`CONTEXT.md` / `docs/SKILL.md` / `scripts/ru_diag.sh` /
`scripts/ui_*.py` / `smoke_test.py` 都以它为准，改默认值会同时打断这些脚本）。

退出码：0 正常 / 2 缺账号（--check） / 3 口令与预期不符（--check） / 4 参数或环境错误
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "cloud-functions", "api")

DEFAULT_USER = "admin"
DEFAULT_PWD = "admin123"
MIN_LEN = 6
WEAK = {"123456", "12345678", "password", "admin", "admin1", "111111", "qwerty"}


def _load_env():
    """仓库根 .env → 进程环境（与 scripts/perf/kit.py 同一份约定；已在环境里的不覆盖）。"""
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _db():
    sys.path.insert(0, API)
    import db  # noqa: E402
    return db


def _hash(pwd):
    sys.path.insert(0, API)
    from routes.common import hash_password, check_password  # noqa: E402
    return hash_password, check_password


def _state(db, user):
    return db.one("SELECT username, role, password_hash, created_at FROM users WHERE username=%s",
                  [user])


def check(db, user, pwd):
    row = _state(db, user)
    _hp, cp = _hash(pwd)
    if not row:
        print("✗ users 表里没有 %r —— 该账号不存在，任何口令都登不上" % user)
        return 2
    ok = cp(pwd, row.get("password_hash") or "")
    print("· 账号 %s / role=%s / created_at=%s / hash=%d 字符"
          % (row["username"], row.get("role"), row.get("created_at"),
             len(row.get("password_hash") or "")))
    print("%s 预期口令校验：%s" % ("✓" if ok else "✗", "匹配" if ok else "不匹配"))
    return 0 if ok else 3


def create(db, user, pwd, reset=False):
    hp, _cp = _hash(pwd)
    row = _state(db, user)
    if row and not reset:
        print("· 已存在 %s，跳过（要覆盖口令用 --reset）" % user)
        return check(db, user, pwd)
    if row:
        n = db.execute("UPDATE users SET password_hash=%s, role='admin' WHERE username=%s",
                       [hp(pwd), user])
        print("✓ 已重置 %s 的口令（role 归一为 admin），影响 %s 行" % (user, n))
    else:
        n = db.execute("INSERT INTO users(username, password_hash, role) VALUES(%s,%s,%s)",
                       [user, hp(pwd), "admin"])
        print("✓ 已创建 %s（role=admin），影响 %s 行" % (user, n))
    return check(db, user, pwd)


def main():
    ap = argparse.ArgumentParser(description="引导/修复管理员账号（幂等，空库可一键补齐）")
    ap.add_argument("--user", default=os.environ.get("ADMIN_USER", DEFAULT_USER))
    ap.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD", DEFAULT_PWD))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只体检，不写库（默认）")
    g.add_argument("--create", action="store_true", help="缺则创建，有则跳过")
    g.add_argument("--reset", action="store_true", help="覆盖口令（含 role 归一）")
    ap.add_argument("--allow-weak", action="store_true", help="允许弱口令（默认拒绝）")
    a = ap.parse_args()

    _load_env()
    if not (os.environ.get("TIDB_HOST") or os.environ.get("DB_HOST")):
        print("✗ 没有 DB 凭据（仓库根 .env 缺 TIDB_HOST）—— 无法直连，改用 --check 前先配 .env")
        return 4
    if not a.create and not a.reset and not a.check:
        a.check = True

    if (a.create or a.reset):
        if len(a.password) < MIN_LEN:
            print("✗ 口令至少 %d 位" % MIN_LEN)
            return 4
        if a.password.lower() in WEAK and not a.allow_weak:
            print("✗ 拒绝弱口令（%s）—— 确需使用请加 --allow-weak" % a.password)
            return 4

    db = _db()
    try:
        db.one("SELECT 1 AS ok")
    except Exception as e:
        print("✗ 连不上库：%s" % str(e)[:160])
        return 4

    # 口令只报"是不是默认约定值"，绝不回显明文
    print("· 目标账号=%s 口令=%s 库=%s"
          % (a.user, "默认约定值" if a.password == DEFAULT_PWD else "自定义(已提供)",
             os.environ.get("TIDB_DB") or "?"))

    if a.reset:
        return create(db, a.user, a.password, reset=True)
    if a.create:
        return create(db, a.user, a.password, reset=False)
    return check(db, a.user, a.password)


if __name__ == "__main__":
    sys.exit(main())
