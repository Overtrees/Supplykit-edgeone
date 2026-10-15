import csv, io, json, subprocess, os, time

# 生成 5000 单 CSV(买家已付款——blocked 写入/GMV 覆盖; sku 映射)
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(['订单编号', 'SKU', '店铺', '数量', '金额', '下单时间', '订单状态'])
for i in range(5000):
    w.writerow(['B%05d' % i, 'SKU-9999-%d' % (i % 50), '旗舰店', 1, 10.0, '2026-10-15', '买家已付款'])
csv_data = buf.getvalue().encode('utf-8')

AT = os.environ['AT']
mp = json.dumps({'订单编号': {'target': 'order_no', 'type': 'string'},
                 'SKU': {'target': 'sku', 'type': 'string'},
                 '数量': {'target': 'quantity', 'type': 'string'},
                 '订单状态': {'target': 'order_status', 'type': 'string'}})
import urllib.parse
url = 'https://supplykit.top/api/cleansing/execute-async?q_target=order&q_channel=jd&q_conflict=sum&q_mp=' + urllib.parse.quote(mp)
t0 = time.time()
r = subprocess.run(['curl', '-s', '-m', '300', '-X', 'POST', url,
                    '-H', 'X-Auth-Token: ' + AT,
                    '-H', 'Content-Type: application/octet-stream',
                    '--data-binary', csv_data], capture_output=True, text=True)
print('execute:', r.stdout[:120], '|', round(time.time() - t0, 1), 's')

# 立即看任务 params(steps/cur_step 是否写入)
tid = None
try:
    import re
    m = re.search(r'"task_id":\s*"([^"]+)"', r.stdout)
    if m: tid = m.group(1)
except Exception:
    pass
if tid:
    import urllib.request
    q = url.split('execute-async')[0]
    time.sleep(2)
    def diag(sql):
        p = subprocess.run(['curl', '-s', '-m', '30', '-X', 'POST', q + 'db/diag',
                            '-H', 'X-Auth-Token: ' + AT, '-H', 'Content-Type: application/json',
                            '-d', json.dumps({'sql': sql})], capture_output=True, text=True)
        try:
            return json.loads(p.stdout).get('data', {}).get('rows', [])
        except Exception:
            return []
    rows = diag("SELECT status, params, result FROM sync_tasks WHERE task_id='" + tid + "'")
    for row in rows:
        print('task:', tid)
        print('status:', row.get('status'))
        pj = json.loads(row.get('params') or '{}')
        print('steps:', json.dumps(pj.get('steps', []), ensure_ascii=False)[:300])
        print('cur_step:', json.dumps(pj.get('cur_step'), ensure_ascii=False))
        print('offset/total:', pj.get('offset'), '/', pj.get('total'))
        rj = json.loads(row.get('result') or '{}')
        print('result:', json.dumps(rj, ensure_ascii=False)[:200])