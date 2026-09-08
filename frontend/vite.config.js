import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { sentryVitePlugin } from '@sentry/vite-plugin'
import { readdirSync, unlinkSync, existsSync } from 'fs'

// sourcemap 清理：Sentry 上传完成后删除本地 .map（防源码泄露）
// 放在 sentryVitePlugin 之后注册，确保 closeBundle 上传先于删除执行
function cleanupSourcemaps() {
  return {
    name: 'cleanup-sourcemaps',
    apply: 'build',
    closeBundle() {
      try {
        const dir = 'dist/assets'
        if (existsSync(dir)) {
          const maps = readdirSync(dir).filter(f => f.endsWith('.map'))
          maps.forEach(f => unlinkSync(dir + '/' + f))
          if (maps.length > 0) console.log(`[cleanup-sourcemaps] 已删除 ${maps.length} 个 .map 文件`)
        }
      } catch (e) {
        console.warn('[cleanup-sourcemaps] 清理失败:', e)
      }
    }
  }
}

import pkg from './package.json'

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(pkg.version || 'dev') },
  plugins: [
    react(),
    // sourcemap 上传（有 SENTRY_AUTH_TOKEN 才生效，否则跳过）
    sentryVitePlugin({
      org: process.env.SENTRY_ORG || '',
      project: process.env.SENTRY_PROJECT || '',
      authToken: process.env.SENTRY_AUTH_TOKEN || '',
      url: process.env.SENTRY_URL || 'https://sentry.io/',  // EU 区 org 需设 https://de.sentry.io/
      disable: !process.env.SENTRY_AUTH_TOKEN,
    }),
    cleanupSourcemaps(),
  ],
  build: {
    sourcemap: true,
    chunkSizeWarningLimit: 1000,
    target: 'es2020'
  },
  esbuild: {
    minifyIdentifiers: false,
    minifySyntax: true
  },
  server: { port: 5173 },
})