#!/usr/bin/env python3
"""seed 驱动(低 RU): 反复 GET /api/seed/fill/status 推进一步, 中间只查库看进度

为什么这样写:
  · 驱动必须由「一个请求 = 一段」推进, 每段 ≤90s 预算, 请求超时 100s < 网关 120s;
  · 每段之间**不额外调 status**(那一步只读、但仍然算一次函数调用), 只查任务行;
  · 库查只用一条 SELECT params, 不碰业务表(不烧业务 RU)。
"""
import os
import sys
import json
import time
import urllib.request

API = os.environ.get('SK_API', 'https://supplykit.top')
TASK = sys.argv[1]
REQ_TIMEOUT = int(os.environ.get('SK_REQ_TIMEOUT', '115'))

sys.path.insert(0, '/workspace/supplykit/cloud-functions/api')
os.chdir('/workspace/supplykit/cloud-functions/api')
from dotenv import load_dotenv  # noqa: E402

load_dotenv('/workspace/supplykit/.env')
from db import query as _q  # noqa: E402


def task_row():
    r = _q("SELECT status,params,updated_at,lease_until FROM sync_tasks WHERE task_id=%s", [TASK])
    return r[0] if r else None


def _token():
    """全局鉴权中间件只认 X-Auth-Token(网关会改写 Authorization 为 q-sign)。
    不带它每次推进请求都 401 —— 任务看着在跑, 实际一步没走。
    """
    t = os.environ.get('SK_TOKEN', '')
    if not t:
        try:
            t = open('/tmp/sk_token', encoding='utf-8').read().strip()
        except Exception:
            t = ''
    return t


def drive():
    url = '%s/api/seed/fill/status?task_id=%s&driver=script' % (API, TASK)
    req = urllib.request.Request(
        url, headers={'Accept': 'application/json', 'X-Auth-Token': _token()})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as f:
            body = json.loads(f.read().decode('utf-8'))
        return body, time.time() - t0, None
    except Exception as e:
        return None, time.time() - t0, str(e)[:160]


def main():
    n = 0
    while True:
        row = task_row()
        if not row:
            print('任务行不存在, 退出', flush=True)
            return 2
        st = row['status']
        if st in ('done', 'error', 'failed'):
            print('\n终态: %s' % st, flush=True)
            break
        body, el, err = drive()
        n += 1
        if err:
            print('req#%d %.1fs 失败(%s), 10s 后重试' % (n, el, err), flush=True)
            time.sleep(10)
            continue
        d = body.get('data') or body or {}
        print('req#%d %.1fs step=%s/%s status=%s last=%s'
              % (n, el, d.get('step'), d.get('total_step'), d.get('status'),
                 str(d.get('last') or d.get('part') or '')[:60]), flush=True)
        if d.get('status') in ('done', 'error'):
            break
        time.sleep(1)
    row = task_row()
    print(json.dumps({'status': row['status'], 'params': row['params']}, ensure_ascii=False)[:600],
          flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
