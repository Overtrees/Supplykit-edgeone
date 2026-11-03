#!/usr/bin/env bash
# SupplyKit 本地质量门禁(preflight)
# 用途: push/部署前强制检查 —— 后端 3.10 语法 + pyflakes 静态分析 + local_test 回归; 前端 lint + 格式 + tsc + 单测
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
    --skip-sql-smoke) SKIP_SQL_SMOKE=1 ;;
  esac
done
SKIP_SQL_SMOKE="${SKIP_SQL_SMOKE:-0}"

echo "════════ SupplyKit preflight ════════"

if [ "$SKIP_BACK" = "0" ]; then
  echo "── [1/7] 后端 Python 3.10 语法门禁 ──"
  BAD=0
  for f in $(find "$API" -name "*.py"); do
    if ! python3 -c "import ast; ast.parse(open('$f').read(), feature_version=(3,10))" 2>"$API/../.syntax_err"; then
      echo "  ✗ 语法不兼容 3.10: $f"; cat "$API/../.syntax_err" | head -3; BAD=1
    fi
  done
  rm -f "$API/../.syntax_err"
  [ "$BAD" = "0" ] && echo "  ✓ 全部 .py 通过" || FAILS+=("后端语法")

  echo "── [2/7] pyflakes 静态分析(治本: try 吞 NameError = 功能从未生效) ──"
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

  echo "── [3/7] local_test 回归 ──"
  # 2026-11-03 S3-4: 有 TIDB 凭据时在同一个进程里顺带做真实 SQL 冒烟(EXPLAIN) ——
  #   不能"另跑一遍", 冒烟复用的正是这次运行收集到的 SQL。没有凭据时显式说明未验证,
  #   绝不静默当作通过(否则又是一条"不跑的测试")。
  _SMOKE=""
  if [ "$SKIP_SQL_SMOKE" = "1" ]; then
    echo "  ⚠ 已显式跳过真实 SQL 冒烟(表名/列名未验证)"
  elif grep -qs "^TIDB_HOST=." "$SK/.env" || [ -n "${TIDB_HOST:-}" ]; then
    _SMOKE="--sql-smoke"
    echo "  (检测到 TIDB 凭据 → 同时做真实 SQL 冒烟: EXPLAIN + schema 对账)"
  else
    echo "  ⚠ 无 TIDB 凭据: 真实 SQL 冒烟未执行(表名/列名未验证)"
  fi
  # 2026-11-02 修误判: 原用 "行内含『失败』" 判定, 而汇总行本身就是"205 通过, 0 失败"
  #   → 永远命中, preflight 恒红(实测每次都在这一步报错)。改为: 退出码 + 失败数>0 才算失败。
  _lt_out=$(cd "$API" && python3 local_test.py $_SMOKE 2>&1)
  _lt_rc=$?
  # 只显示"结论行" —— 回归汇总 + 冒烟的三行摘要, 其余 388 条 ✅ 不刷屏
  echo "$_lt_out" | grep -E "本地回归:|真实 SQL 冒烟|收集 [0-9]+ 次调用|校验 [0-9]+ 条|schema 对账|声明 [0-9]+ 张表|✓ 所有声明的|✗ |⚠ 未执行|未运行" | sed 's/^/  /'
  if [ "$_lt_rc" = "0" ] && echo "$_lt_out" | grep -qE "本地回归: [0-9]+ 通过, 0 失败"; then
    echo "  ✓ 回归通过"
  else
    echo "$_lt_out" | tail -6
    FAILS+=("local_test")
  fi
fi

if [ "$SKIP_FRONT" = "0" ]; then
  echo "── [4/7] 前端 ESLint(默认 --max-warnings 0, 无警告才通过) ──"
  echo "  ⚠ iSH 沙箱内存受限, 全量扫描可能假通过/崩溃——权威 lint 门禁以 GitHub Actions(CI) 为准"
  if (cd "$FRONT" && npm run lint 2>&1 | grep -vE "expose_wasm|^$|^>" | grep -E "error|warning|✖|problem" ); then
    FAILS+=("eslint")
  else
    echo "  ✓ lint 通过"
  fi

  echo "── [5/7] 前端 Prettier 格式检查 ──"
  if (cd "$FRONT" && npm run format:check 2>&1 | grep -vE "expose_wasm|^$|^>" | grep -iE "warn|error" ); then
    FAILS+=("prettier")
  else
    echo "  ✓ 格式合规"
  fi

  echo "── [6/7] TypeScript 类型检查(tsc --noEmit) ──"
  if (cd "$FRONT" && npx tsc --noEmit 2>&1 | grep -vE "expose_wasm" | grep -E "error TS"); then
    FAILS+=("tsc")
  else
    echo "  ✓ tsc 通过"
  fi

  echo "── [7/7] 前端单元测试(vitest) ──"
  # 2026-11-03 纳入门禁: 这套测试此前**从未被 preflight/CI 执行**, 于是攒下 12 个
  #   长期失败(Toast 假定时器 3 个 + 列定义数字过时 9 个)却无人知晓 —— 不跑的测试等于没有。
  _vt_out=$(cd "$FRONT" && npx vitest run 2>&1)
  _vt_rc=$?
  echo "$_vt_out" | grep -E "Test Files|Tests " | sed 's/^/  /'
  if [ "$_vt_rc" = "0" ]; then
    echo "  ✓ 单测通过"
  else
    echo "$_vt_out" | grep -E "FAIL|AssertionError|×" | head -8
    FAILS+=("vitest")
  fi
fi

echo "═══════════════════════════════════"
if [ "${#FAILS[@]}" -gt 0 ]; then
  echo "✗ preflight 失败: ${FAILS[*]} —— 修复后再 push"
  exit 1
fi
echo "✓ preflight 全部通过, 可 push"
# 密钥/历史泄露审计不占六步名额(要 3~4s, 且 CI 每次推送+每周一定时强制跑),
# 但改过配置/凭据/文档示例行时应当手动过一遍 —— 它扫的是全历史 blob, 不只是工作区
echo "  提示: python3 scripts/audit_secrets.py  (硬编码 / .env 跟踪 / git 全历史)"
exit 0