# EdgeOne Makers pages-api 逆向参考(2026-09-05 实测)

> **重要**:pages-api.cloud.tencent.com/v1 的 Action **无公开官方文档**。腾讯云 EO Makers 官方文档(pages.edgeone.ai、cloud.tencent.cn)只有概念说明与 CLI 用法,**没有完整 REST API 参考**。以下内容来自 edgeone CLI 打包源码逆向 + 线上实测,是 Phase2 迁移的可复用资产。

## 1. 基本用法

```
POST https://pages-api.cloud.tencent.com/v1
Authorization: Bearer $EDGEONE_PAGES_API_TOKEN
Content-Type: application/json
Body: {"Action": "<ActionName>", ...参数}
```

- 响应结构:`{"Code":0, "Data":{"Response":{...}}}`;`Code!=0` 时 `Message` 说明错误
- Action 不存在 → `{"Code":107,"Message":"Action has not found."}`
- 认证 token:控制台 Settings → API Token(与 CLI 用的 `EDGEONE_PAGES_API_TOKEN` 相同)

## 2. 已实测 Action 清单

| Action | 关键参数 | 返回要点 | 用途 |
|---|---|---|---|
| `DescribePagesProjects` | `Filters:[{"Name":"Name","Values":["supplykit"]}], Offset, Limit` | `Projects[0]`: ProjectId / Name / Status / RootDir / OutputDir / BuildCmd / InstallCmd / Framework / Provider / RepoUrl / RepoBranch / **PresetDomain** / **EnvVars**(含掩码) / Deployment | 查项目 ID/域名/env |
| `ModifyPagesProjectEnvs` | `ProjectId, EnvVars:[{Key,Value}...]` | Code 0 | **改项目 env(部署时快照,不作用于运行实例,必须重新部署才生效)** |
| `DescribePagesDeployments` | `ProjectId, Offset, Limit, OrderBy, Order` | `Deployments[]`: DeploymentId / Status(Success/Process/Failed) / RepoCommitHash / RepoCommitMsg / **PreviewUrl** / ProjectUrl / MetaData(路由) / UsedInProd | 查构建状态/路由 |
| `DescribePagesEncipherToken` | **`Text`(域名文本,如 supplykit-qreqtomf.edgeone.cool)** | `{Token, Timestamp}` | **生成函数访问签名**(见 §3) |
| `ModifyPagesProject` | ProjectId + 修改字段 | Code 0 | 改项目配置(displayName 等) |
| `CreatePagesDeployment` | ProjectId, ViaMeta(Upload/Zip), Provider, TempBucketPath | — | 直传部署(仅 Upload 类型项目) |
| `DescribePagesCosTempToken` | — | Credentials(TmpSecretId/Key) | COS 临时凭证 |
| `CreateOrDescribeTokenByMCP` | — | — | MCP token |
| `DescribeUserInfo` | — | AppId/Uin/UserName | whoami |

## 3. 函数访问签名机制(关键,已破解)

Makers 函数生产域名直连 → **401 `X-EOP-MSG: eo_time missing`**(鉴权开启)。

正确调用流程(等价控制台 3 小时预览链接,可脚本化):
1. `DescribePagesEncipherToken` with `Text=<域名>`(PresetDomain 去掉 https://,如 supplykit-qreqtomf.edgeone.cool)→ 拿 `{Token, Timestamp}`
2. 请求 `https://<域名>/api/<路径>?eo_token=<Token>&eo_time=<Timestamp>` → **302 + Set-Cookie**(`eo_token` + `eo_time`, Max-Age=10800=3h, HttpOnly)
3. **带 cookie 访问无签名 URL** 即直达函数

- **Token 绑定域名**:Text 参数必须传域名,传 ProjectId 会得到 wrong token(实测踩坑)
- 302 后的 Location 是无签名的干净 URL——重定向会丢 cookie,必须手动带 cookie 两步走(curl -c/-b)

## 3.5 同源免签(浏览器会话, 2026-09-05 实测)
- 签名 URL 302 种 cookie 后, **浏览器同源 fetch('/api/*') 直达函数 200**——静态页面 + API 整个站点 3h 内全通
- curl 带 Origin/Referer 模拟仍 401——同源免签依赖真实浏览器会话(平台校验 Cookie 完整性)
- **含义**: 前端部署 Makers 同域名 + VITE_API_BASE_URL 同源, 预览/签名会话内可跑前端全功能一比一; 生产公开访问仍需自定义域名或确认免签配置

## 4. Action 发现途径(按可靠性排序)

1. **CLI 打包源码逆向**(最全):`/usr/local/lib/node_modules/edgeone/edgeone-dist/cli.js`(npm 全局安装后),grep `"Describe[A-Za-z]*"` / `action:"..."` 提取全部 Action;配合调用上下文理解参数
2. **控制台网络抓包**:浏览器 DevTools 打开 Makers 控制台操作,Network 面板能看到页面调的 pages-api 请求(需登录态)
3. **CLI 命令行为观察**:`edgeone makers deploy/env/link` 等命令背后就是这些 Action(CLI 源码里 `Sj({action:"..."})` 调用)
4. 官方文档仅概念/CLI:https://pages.edgeone.ai/zh/document/(日志分析/限制配额等) + cloud.tencent.com EO Makers 控制台

## 5. 踩坑记录

- `DescribePagesEncipherToken` 参数名是 `Text`(域名),**不是 ProjectId**——传错 → wrong token
- `ModifyPagesProjectEnvs` 只改项目配置,**运行中的函数实例 env 是部署时快照**,改完必须重新部署(Git 集成项目 = push 触发 webhook)
- 日志无公开 API:官方文档确认日志只在**控制台"日志分析"页**(按时间/状态/关键字,保留 24h);DescribePages*Logs 类 Action 均不存在(107)
- 入口文件检测:构建器要求行首 `app =`(/^app\s*=/m),`from x import app` 不满足
- 函数包 = 仅 cloud-functions/ 目录,独立模块(api/ 下的 .py)在运行时 import 可能失败 → **入口自包含单文件最稳**
