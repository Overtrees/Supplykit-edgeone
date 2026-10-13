# 0001: 认证头统一 X-Auth-Token

- 日期: 2026-10-13
- 决策: token 传输头从 Authorization:Bearer 改为自定义 X-Auth-Token
- 背景: 边缘网关将 Authorization 改写为 q-sign(平台签名)——Bearer JWT 被拦截 → 全接口 401(login 放行不受影响——能登录但接口全拒)
- 权衡: 标准 Bearer(被网关拦截) vs 自定义头(网关不改写——可控稳定; 非标准但有收益)
- 结论: X-Auth-Token(网关不接触 token——暴露面更小; 安全链 TLS+JWT 验签+密钥必设不变)
- 影响: 后端中间件/自验全链 + 前端 api/裸 fetch 全量改; 直接 fetch 纪律(§15.52)
