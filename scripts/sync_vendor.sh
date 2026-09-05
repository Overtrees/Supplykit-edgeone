#!/bin/sh
# 同步 backend/app → cloud-functions/vendor/app(函数包用)
# 处理: 清理缓存/数据库/env 文件; vendor main.py 入口消除(防构建器检测为第二函数)
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

rm -rf "$ROOT/cloud-functions/vendor/app"
cp -r "$ROOT/backend/app" "$ROOT/cloud-functions/vendor/app"
find "$ROOT/cloud-functions/vendor" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$ROOT/cloud-functions/vendor" -name '*.db*' -delete 2>/dev/null || true
find "$ROOT/cloud-functions/vendor" -name '.env' -delete 2>/dev/null || true
find "$ROOT/cloud-functions/vendor" -name '.selfheal.log' -delete 2>/dev/null || true
rm -rf "$ROOT/cloud-functions/vendor/app/tmp"

# vendor 副本 main.py 的 app = FastAPI( 用 if True: 包裹
# (构建器入口检测 /^app\s*=/m 要求行首, 否则把 vendor/app/main.py 当第二函数→sys.path污染)
python3 - "$ROOT" <<'PYEOF'
import sys
p = sys.argv[1] + '/cloud-functions/vendor/app/main.py'
s = open(p, encoding='utf-8').read()
marker = 'app = FastAPI(title='
assert marker in s, 'marker not found in vendor main.py'
if not s.startswith('if True:'):
    s = s.replace(marker, 'if True:\n    app = FastAPI(title=', 1)
open(p, 'w', encoding='utf-8').write(s)
print('vendor main.py 入口已消除')
PYEOF

echo "vendor 同步完成: $(find "$ROOT/cloud-functions/vendor" -name '*.py' | wc -l) 个 py 文件"
