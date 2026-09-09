// SupplyKit EdgeOne Makers + TiDB + TypeScript 原生架构 ESLint 配置
// (2026-09-09 重构: 旧版为 PA+SQLite 环境遗留, 无 TS 解析器/React hooks 规则)
// 规则基调: 类型宽松期(strict:false)不阻塞开发, 重点守住:
//   - TS 语法正确解析(@typescript-eslint/parser)
//   - React hooks 心智负担(rules-of-hooks 必须, exhaustive-deps 显式化)
//   - 代码卫生(no-var / prefer-const / 未使用变量)
// 风格统一交给 Prettier(eslint-config-prettier 关闭冲突规则)
module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'unused-imports'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'prettier', // 关闭与 Prettier 冲突的格式规则(样式归 Prettier 管)
  ],
  rules: {
    // ── TypeScript 原生适配(宽松期: 不阻塞, 持续收敛) ──
    '@typescript-eslint/no-explicit-any': 'off',            // 历史大量 any, 逐步治理
    '@typescript-eslint/ban-ts-comment': 'off',             // @ts-ignore 兼容
    '@typescript-eslint/no-non-null-assertion': 'off',      // ! 断言兼容
    '@typescript-eslint/no-unused-vars': 'off',             // 由 unused-imports/no-unused-vars 接管(避免双报)
    '@typescript-eslint/no-empty-object-type': 'off',

    // ── React hooks(规则校验 + disable 注释显式化) ──
    'react-hooks/rules-of-hooks': 'error',                  // hooks 调用顺序(硬规则)
    // exhaustive-deps 关闭: 迁移代码大量非受控 effect, 强制会引入回归; 依赖完整性靠人工 review
    'react-hooks/exhaustive-deps': 'off',

    // ── 代码卫生 ──
    'no-var': 'error',                                      // 禁 var
    'prefer-const': 'warn',                                 // 未 reassign 的 let → const
    // allowEmptyCatch: 有意忽略的异常(如 localStorage 隐私模式读写)——空 if/循环仍报
    'no-empty': ['error', { allowEmptyCatch: true }],
    'unused-imports/no-unused-imports': 'error',            // 未用 import 自动删(--fix)
    // 变量未用检查关闭(分层): 存量冗余量大, 逐个治理风险>收益;
    // 未来 tsconfig strict 化时由 tsc noUnusedLocals 统一接管(本规则保留框架)
    'unused-imports/no-unused-vars': 'off',
    'no-console': ['warn', { allow: ['warn', 'error', 'info'] }],  // 禁 console.log/debug 残留
    'no-debugger': 'warn',
    'no-unexpected-multiline': 'error',
  },
  ignorePatterns: ['dist', 'node_modules', '*.js', '*.cjs', '*.css'],
}