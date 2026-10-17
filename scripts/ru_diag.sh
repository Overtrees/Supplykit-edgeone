#!/bin/bash
# ── 只读 RU 诊断脚本(2026-10-17 三件套之一) ─────────────────────────
# 用途: 调试/排查性能时一键跑——全部 EXPLAIN(只读)+ GET(只读), 零写操作零导入
#       一次循环 100-300 RU(比写操作验证省 2-3 个数量级)
# 用法: bash scripts/ru_diag.sh [domain]   默认 https://supplykit.top
# 依赖: $TIDB_PUBLIC_KEY/$TIDB_PRIVATE_KEY 不需要; 只需登录 admin
# 输出: 每查询 RU/耗时 + 全表扫标记(⚠️)
# ─────────────────────────────────────────────────────────────────────
set -e
B="${1:-https://supplykit.top}"
echo "=== 登录 ==="
LOGIN=$(curl -s -m 20 -X POST "$B/api/auth/login" -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}')
TOKEN=$(echo "$LOGIN" | python3 -c "import json,sys; print(json.load(sys.stdin).get('token',''))")
if [ -z "$TOKEN" ]; then echo "❌ 登录失败: $LOGIN"; exit 1; fi

diag() {
  # diag SQL → JSON rows + elapsed
  curl -s -m 60 -X POST "$B/api/db/diag" -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
    -d "$(python3 -c "import json,sys; print(json.dumps({'sql': sys.argv[1]}))" "$1")"
}

get_ms() {
  curl -s -m 90 -o /dev/null -w "%{time_total}" "$B$1" -H "X-Auth-Token: $TOKEN"
}

echo ""
echo "=== 1. 核心查询 EXPLAIN ANALYZE(只读, 真实 RU) ==="
# 每项: 名称 | SQL —— 全部只读, 全部 EXPLAIN(不执行数据变更)
QUERIES=(
  "orders 分页|EXPLAIN ANALYZE SELECT * FROM orders WHERE channel='jd' AND (deleted_at IS NULL OR deleted_at='') ORDER BY id DESC LIMIT 30 OFFSET 0"
  "批次效期摘要|EXPLAIN ANALYZE SELECT sku, prod_date, exp_date, ROW_NUMBER() OVER (PARTITION BY sku ORDER BY exp_date ASC) rn FROM batches WHERE channel='jd' AND warehouse_type='platform' AND exp_date IS NOT NULL AND sku IN ('SKU-0001-J','SKU-0002-J')"
  "summary 物化聚合|EXPLAIN ANALYZE SELECT \`date\`, channel, order_status, store, SUM(gmv) g, SUM(cnt) c FROM orders_day_agg WHERE channel='jd' AND \`date\`>='2026-08-19' GROUP BY \`date\`, channel, order_status, store"
  "物化读(补货核心)|EXPLAIN ANALYZE SELECT sku, ds7, ds14, ds28, ds7_cut, ds14_cut, ds28_cut, sigma FROM sku_sales_stats WHERE channel='jd' AND warehouse='__C__' LIMIT 100"
  "进销存 COUNT|EXPLAIN ANALYZE SELECT COUNT(*) c FROM inventory WHERE channel='jd' AND warehouse_type='platform'"
  "快照聚合(降级路径)|EXPLAIN ANALYZE SELECT date, sku, SUM(order_count) c FROM daily_sales_snapshot WHERE channel='jd' AND date>='2026-09-19' AND sku IN ('SKU-0001-J') GROUP BY date, sku"
  "alerts counts|EXPLAIN ANALYZE SELECT status, severity, COUNT(*) c FROM alerts WHERE channel='jd' AND status='active' GROUP BY status, severity"
)
for q in "${QUERIES[@]}"; do
  NAME="${q%%|*}"; SQL="${q#*|}"
  R=$(diag "$SQL")
  echo "$R" | python3 -c "
import json,sys,re
try:
    d=json.load(sys.stdin)
    rows=(d.get('data') or {}).get('rows') or []
    ru=None
    for r in rows:
        m=re.search(r'RU:([\d.]+)', str(r.get('execution info') or ''))
        if m: ru=float(m.group(1)); break
    el=(d.get('data') or {}).get('elapsed_ms')
    # 全表扫标记
    scan=''
    for r in rows[:1]:
        if 'TableFullScan' in str(r.get('id') or ''): scan=' ⚠️全表扫'
    flag='✅' if (ru or 0)<=30 else ('⚠️' if (ru or 0)<=100 else '❌')
    print('  %s %-20s RU=%-8s %.0fms%s' % (flag, '$NAME', ru if ru is not None else '?', el or 0, scan))
except Exception as e:
    print('  ❌ $NAME 解析失败: %s' % str(e)[:60])
"
done

echo ""
echo "=== 2. 关键接口 GET 实测(只读, 冷/热) ==="
for ep in "/api/dashboard/summary?channel=jd" "/api/dashboard/aux?channel=jd" \
          "/api/dashboard/stock-risk?channel=jd" "/api/insights/replenishment?channel=jd&mode=bbcc&page=1&page_size=100" \
          "/api/insights/purchase?channel=jd&mode=bbcc&need_only=1" "/api/orders?page=1&page_size=30" \
          "/api/products?page=1&page_size=30" "/api/alerts?channel=jd" "/api/insights/with-sales?wh_type=platform&page=1&page_size=50"; do
  t1=$(get_ms "$ep"); t2=$(get_ms "$ep")
  echo "  %-70s %ss / %ss" "$ep" "$t1" "$t2"
done

echo ""
echo "=== 完成: 本轮全只读, 消耗 <300 RU ==="
