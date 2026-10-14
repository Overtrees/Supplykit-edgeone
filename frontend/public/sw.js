const CACHE = 'supplykit-v2'
const PRECACHE = [
  '/',
  '/manifest.json',
  '/icon.svg',
]

// 安装时预缓存关键资源
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  )
})

// 激活时清理旧缓存
self.addEventListener('activate', e => {
  e.waitUntil(
    Promise.all([
      caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))),
      self.clients.claim(), // SPA 切页非导航——claim 让新 SW 立即接管(否则旧 SW 永驻——部署后旧代码一直跑)
    ])
  )
})
// 新 SW 接管后告知客户端刷新(部署向新代码)
self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting()
})

// 网络优先，缓存兜底
self.addEventListener('fetch', e => {
  // API 请求不做缓存，保证数据实时性
  if (e.request.url.includes('/api/')) {
    e.respondWith(fetch(e.request).catch(() => new Response(JSON.stringify({ok:false,error:'offline'}), {headers:{'Content-Type':'application/json'}})))
    return
  }
  // 静态资源：网络优先，离线时用缓存
  e.respondWith(
    fetch(e.request)
      .then(r => {
        const clone = r.clone()
        caches.open(CACHE).then(c => c.put(e.request, clone))
        return r
      })
      .catch(() => caches.match(e.request))
  )
})