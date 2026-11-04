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

// 本地预览代理: 把 /api 转给线上后端, 规避"线上未开 CORS + 浏览器跨域"的限制。
// 仅本地验证用, 正式部署走同域, 不经过这里。
const PROXY = {
  '/api': {
    target: process.env.VITE_PROXY_TARGET || 'https://supplykit.top',
    changeOrigin: true,
    secure: true,
  },
}

export default defineConfig({
  define: { __APP_VERSION__: JSON.stringify(pkg.version || 'dev'), __BUILD_DATE__: JSON.stringify(new Date().toISOString().slice(0, 10)) },
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
  server: { port: 5173, proxy: PROXY },
  // 预览服务(preview)同样需要代理: 本地跑 dist 验证时, /api 要转给线上后端
  // (线上后端未开 CORS, 浏览器不能从 localhost 直连 supplykit.top)
  preview: { port: 4173, proxy: PROXY },
})