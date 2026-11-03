# 0001: 认证头统一 X-Auth-Token

- 日期: 2026-10-13（2026-11-03 补"终态"与验证方法）
- 状态: **已定案（平台约束下的终态，不是待办）**

## 决策

token 传输头从 `Authorization: Bearer` 改为自定义 `X-Auth-Token`，并**删掉 Bearer 回退**。

## 背景

边缘网关将 `Authorization` 改写为 `q-sign`（平台签名）—— Bearer JWT 被拦截 → 登录接口放行不受影响，
所以表现为"能登录但所有业务接口 401"。这是本项目最难查的一次故障之一：登录成功会让人把怀疑方向
放在权限/角色上，而真正的原因是**请求头在到达应用前就被换了**。

## 权衡

| 方案 | 结果 |
|---|---|
| 标准 `Authorization: Bearer` | 被网关改写，401；且不同路径是否改写不一致 → "部分通部分不通"的随机现象 |
| 自定义 `X-Auth-Token` | 网关不接触 → 稳定；非标准，但收益明确 |
| 两个都读（兼容回退） | **最差**：让"是否 401"取决于该请求是否被改写，排障成本 > 兼容收益。已明确删除 |

结论：`X-Auth-Token`。暴露面更小（网关完全不看这个头），安全链不变（TLS + JWT 验签 + `JWT_SECRET` 必设）。

## 为什么说这是"终态"而不是"待办"

应用层无法改变网关对请求头的改写规则。只要该行为不变，Bearer 就不可能工作；加回退只会引入不确定性。

## 平台是否变了的验证方法（30 秒，怀疑时跑）

```bash
TOKEN=$(curl -s -X POST https://supplykit.top/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 期望 401 —— 说明网关仍在改写, 维持 X-Auth-Token
# 返回 200 —— 平台已不改写, 可以评估切回标准头(需同时改前端 client.ts 与所有裸 fetch)
curl -s -o /dev/null -w '%{http_code}\n' \
  https://supplykit.top/api/dashboard/summary?channel=jd \
  -H "Authorization: Bearer $TOKEN"
```

## 真正能"根治"的两条路（都不在应用层）

1. **平台侧**配置网关不改写 `Authorization`（需平台能力，当前无）
2. 改用 **Cookie** 承载 token（HttpOnly 顺带解决 XSS 读 token，但要引入 CSRF 防护 + 改前端存储，
   收益不抵改造成本）

现阶段两条都不做，维持自定义头。

## 影响

后端中间件（`index.py::auth_middleware`）/ 自验全链 + 前端 `api/client.ts` 与裸 fetch 全量改造；
直接 `fetch` 时必须手动带 `X-Auth-Token`（见 §15.52 直接 fetch 纪律）。
