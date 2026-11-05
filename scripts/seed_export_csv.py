#!/usr/bin/env python3
"""把 seed 成品数据按「导入的 7 种 target」原样拆成 7 个 CSV 文件。

2026-11-05 用户定案: **仅做拆文件这一步**, 数量 / 场景 / 字段一律不变。
  · 数据源 = 刚跑完 seed 的成品库(只读); 不做任何重新生成, 不改一行数据;
  · 表头 = routes/seed_import.py 的中文导出列名(与用户上传的导入文件同构);
  · 7 个 target 见 seed_import.py docstring:
      order / inventory / platform_inv / inventory_b / inbound / outbound / product
    其中 inventory / platform_inv / inventory_b 是同一张 inventory 表按仓型的三个
    不同上传入口 —— 这里按 warehouse_type 分流, 三份行数之和 = 库存表总行数。

输出: /workspace/supplykit_seed_csv/*.csv + _manifest.json
用法: python3 scripts/seed_export_csv.py
依赖: 仓库根 .env(只读源库凭据); 不写库、不消耗写 RU。
"""
import csv
import io
import json
import os
import sys
import time

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "/workspace/supplykit_seed_csv"
BATCH = 5000

# 让 routes.seed_import 的 spec() 可导入(单一真源, 不在本文件维护表头副本)
sys.path.insert(0, os.path.join(ROOT, "cloud-functions", "api"))
from routes.seed_import import spec  # noqa: E402

# 7 个 target → (源表, 额外 where, 表头规格 target)
#   platform_inv 在 _SPEC 里没有独立条目: 导入页 TARGET_FIELDS 里它与 inventory 共用
#   INV_FIELDS, 写入层 cleanse_write.py 也走同一分支 → 表头沿用 inventory 规格。
SPECS = [
    ("product", "products", "", "product"),
    ("order", "orders", "", "order"),
    ("inventory", "inventory", "warehouse_type='own'", "inventory"),
    ("platform_inv", "inventory", "warehouse_type='platform'", "inventory"),
    ("inventory_b", "inventory", "warehouse_type='platform_b'", "inventory_b"),
    ("inbound", "inbound_records", "", "inbound"),
    ("outbound", "outbound_records", "", "outbound"),
]


def load_env():
    env = {}
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def connect(env):
    return pymysql.connect(
        host=env["TIDB_HOST"], port=int(env.get("TIDB_PORT", "4000")),
        user=env["TIDB_USER"], password=env["TIDB_PASSWORD"],
        database=env.get("TIDB_DB", "supplykit"),
        ssl={"ca": None} if env.get("TIDB_SSL", "true").lower() != "false" else None,
        autocommit=True, cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4", connect_timeout=20, read_timeout=120,
    )


def cell(v):
    """与 seed_import.to_csv 完全一致: None 写空串, 其余 str()(datetime/Decimal 原样)"""
    return "" if v is None else str(v)


def export(conn, target, table, where, header_target):
    sp = spec(header_target)
    cols = sp["cols"]
    mp = sp["mp"]
    path = os.path.join(OUT, target + ".csv")
    cur = conn.cursor()
    cond = (" WHERE " + where) if where else ""
    cur.execute("SELECT COUNT(*) c FROM `%s`%s" % (table, cond))
    expected = int(cur.fetchone()["c"])
    written = 0
    last_id = 0
    t0 = time.time()
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        while True:
            sql = ("SELECT * FROM `%s` WHERE id > %%s%s ORDER BY id LIMIT %d"
                   % (table, (" AND " + where) if where else "", BATCH))
            cur.execute(sql, [last_id])
            rows = cur.fetchall()
            if not rows:
                break
            for r in rows:
                last_id = max(last_id, int(r.get("id") or 0))
                w.writerow([cell(r.get(str((mp.get(c) or {}).get("target") or "")))
                            for c in cols])
            written += len(rows)
            if written % 50000 < BATCH:
                print("  %-14s %7d / %7d  %.0fs" % (target, written, expected,
                                                    time.time() - t0), flush=True)
    size = os.path.getsize(path)
    ok = (written == expected)
    print("  %-14s %7d 行  %6.1f MB  %s  %.1fs"
          % (target, written, size / 1048576.0, "OK" if ok else "!! 行数不符", time.time() - t0),
          flush=True)
    return {"file": os.path.basename(path), "target": target, "source_table": table,
            "where": where, "header_target": header_target, "columns": cols,
            "rows": written, "expected": expected, "bytes": size, "match": ok}


def main():
    os.makedirs(OUT, exist_ok=True)
    env = load_env()
    conn = connect(env)
    print("源库: %s / %s (只读)" % (env.get("TIDB_DB"), env.get("TIDB_HOST", "")[:24] + "..."),
          flush=True)
    manifest = []
    for target, table, where, header_target in SPECS:
        manifest.append(export(conn, target, table, where, header_target))
    conn.close()
    total = sum(m["rows"] for m in manifest)
    with open(os.path.join(OUT, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "note": "仅按 7 种导入 target 拆分, 数量/场景/字段与 seed 成品库一致",
                   "total_rows": total, "files": manifest}, f,
                  ensure_ascii=False, indent=2)
    bad = [m["target"] for m in manifest if not m["match"]]
    print("合计 %d 行 → %s" % (total, OUT), flush=True)
    if bad:
        print("行数不符: %s" % ",".join(bad), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
