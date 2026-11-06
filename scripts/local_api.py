#!/usr/bin/env python3
"""本地直连测试: 进程内 TestClient 打真实 app, 连 .env 指定的库, 不经网关、不等部署

用法:
    python3 scripts/local_api.py GET  /health
    python3 scripts/local_api.py POST /seed/step '{"from_step":7}'
    python3 scripts/local_api.py POST /ops/snapshot-barcode '{"dry_run":true}'
    python3 scripts/local_api.py GET  /products/list --raw     # 完整输出不截断
    python3 scripts/local_api.py GET '/cron/archive?force=1' --hdr X-Cron-Secret:$CRON_SECRET
        # --hdr K:V 可重复 —— cron 的 force 逃生口要真密钥(见 routes/cron._force_ok),
        #   否则窗口守卫会把它挡回"01:05 再来", 无法做定向补跑验证

为什么值得用:
  云函数 wall clock 120s 是硬限, 部署一次 2~2.5 分钟。本地进程内调用两者都没有 ——
  Python 层逻辑改完立刻能验, 部署只用来"确认", 不再用来"调试"。
  实测: app 冷加载约 40s(建表检查等), 之后单个请求毫秒级。

安全:
  连的是 .env 里 TIDB_DB 指向的库。当前该库为演示库(orders 全量 data_source='seed'),
  写操作仍按线上同样的守卫执行 —— /seed/step from_step=5 的 seed 库判定照旧生效。
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "cloud-functions", "api")


def load_env(path):
    """极简 .env 解析 —— 不引第三方依赖(本地可能没装 python-dotenv)"""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    method = sys.argv[1].upper()
    path = sys.argv[2]
    body = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else None
    raw = "--raw" in sys.argv
    extra_hdrs = {}
    for i, a in enumerate(sys.argv):
        if a == "--hdr" and i + 1 < len(sys.argv):
            k, _, v = sys.argv[i + 1].partition(":")
            extra_hdrs[k.strip()] = v.strip()

    load_env(os.path.join(ROOT, ".env"))
    os.environ.setdefault("JWT_SECRET", "local-test-secret")
    sys.path.insert(0, API)

    t0 = time.time()
    from fastapi.testclient import TestClient
    from index import app
    from routes.common import create_token

    token = create_token("admin")
    client = TestClient(app)
    headers = {"X-Auth-Token": token, "Authorization": "Bearer " + token}
    headers.update(extra_hdrs)

    kw = {"headers": headers, "timeout": 3600}   # 本地无 120s 网关, 给足时间
    if body:
        try:
            kw["json"] = json.loads(body)
        except json.JSONDecodeError:
            kw["content"] = body
    t1 = time.time()
    resp = client.request(method, path, **kw)
    dt = time.time() - t1

    print("[%s %s] %d  %.2fs  (app load %.1fs)" % (method, path, resp.status_code, dt, t1 - t0))
    text = resp.text
    if raw or len(text) <= 4000:
        print(text)
    else:
        try:
            d = resp.json()
            print(json.dumps(d, ensure_ascii=False)[:4000])
        except Exception:
            print(text[:4000])
        print("... (已截断, 加 --raw 看完整)")
    return 0 if resp.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
