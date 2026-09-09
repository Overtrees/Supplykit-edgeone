#!/usr/bin/env bash
# SupplyKit 本地质量门禁(preflight)
# 用途: push/部署前强制检查 —— 后端 3.10 语法 + pyflakes 静态分析 + local_test 回归; 前端 lint + tsc
# 依赖: python3, pyflakes(apk add pyflakes); node, npm, eslint, typescript
# 用法: scripts/preflight.sh [--skip-frontend] [--skip-backend]
# 退出码: 0=全部通过(可 push)  1=存在失败(禁止 push)
set -u

SK="$(cd "$(dirname "$0")/.." && pwd)"
API="$SK/cloud-functions/api"
FRONT="$SK/frontend"
SKIP_FRONT=0
SKIP_BACK=0
FAILS=()

for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONT=1 ;;
    --skip-backend) SKIP_BACK=1 ;;
  esac
done

echo "════════ SupplyKit preflight ════════"

if [ "$SKIP_BACK" = "0" ]; then
  echo "── [1/3] 后端 Python 3.10 语法门禁 ──"
  BAD=0
  for f in $(find "$API" -name "*.py"); do
    if ! python3 -c "import ast; ast.parse(open('$f').read(), feature_version=(3,10))" 2>"$API/../.syntax_err"; then
      echo "  ✗ 语法不兼容 3.10: $f"; cat "$API/../.syntax_err" | head -3; BAD=1
    fi
  done
  rm -f "$API/../.syntax_err"
  [ "$BAD" = "0" ] && echo "  ✓ 全部 .py 通过" || FAILS+=("后端语法")

  echo "── [2/3] pyflakes 静态分析(治本: try 吞 NameError = 功能从未生效) ──"
  if command -v pyflakes >/dev/null 2>&1; then
    ERR=$(cd "$API" && pyflakes . 2>&1 | grep -vE "imported but unused|unable to detect undefined names" | grep -E "undefined name|redefin|used before assignment" || true)
    if [ -n "$ERR" ]; then
      echo "  ✗ pyflakes 发现问题:"; echo "$ERR" | head -10; FAILS+=("pyflakes")
    else
      echo "  ✓ 无 undefined name/重定义/先使用后定义"
    fi
  else
    echo "  ⚠ pyflakes 未安装(apk add pyflakes)——跳过"
  fi

  echo "── [3/6] local_test 回归 ──"
  (cd "$API" && python3 local_test.py 2>&1 | tail -2) | grep -E "通过|失败|FAIL" | while read -r line; do
    echo "  $line"
    case "$line" in
      *"失败"*) echo "PREFLIGHT_FAIL" > /tmp/preflight_back_fail ;;
    esac
  done
  if [ -f /tmp/preflight_back_fail ]; then rm -f /tmp/preflight_back_fail; FAILS+=("local_test"); else echo "  ✓ 回归通过"; fi
fi

if [ "$SKIP_FRONT" = "0" ]; then
  echo "── [4/6] 前端 ESLint(默认 --max-warnings 0, 无警告才通过) ──"
  echo "  ⚠ iSH 沙箱内存受限, 全量扫描可能假通过/崩溃——权威 lint 门禁以 GitHub Actions(CI) 为准"
  if (cd "$FRONT" && npm run lint 2>&1 | grep -vE "expose_wasm|^$|^>" | grep -E "error|warning|✖|problem" ); then
    FAILS+=("eslint")
  else
    echo "  ✓ lint 通过"
  fi

  echo "── [5/6] 前端 Prettier 格式检查 ──"
  if (cd "$FRONT" && npm run format:check 2>&1 | grep -vE "expose_wasm|^$|^>" | grep -iE "warn|error" ); then
    FAILS+=("prettier")
  else
    echo "  ✓ 格式合规"
  fi

  echo "── [6/6] TypeScript 类型检查(tsc --noEmit) ──"
  if (cd "$FRONT" && npx tsc --noEmit 2>&1 | grep -vE "expose_wasm" | grep -E "error TS"); then
    FAILS+=("tsc")
  else
    echo "  ✓ tsc 通过"
  fi
fi

echo "═══════════════════════════════════"
if [ "${#FAILS[@]}" -gt 0 ]; then
  echo "✗ preflight 失败: ${FAILS[*]} —— 修复后再 push"
  exit 1
fi
echo "✓ preflight 全部通过, 可 push"
exit 0