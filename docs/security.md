# 安全攻防清单（2026-10-13 审计——公开仓库角度）

> 公开仓库 + 公开演示账号(demo/demo123 只读)——攻击者可读实现对照打线上。以下为清单式审计结论与加固建议。

## 已确认（当前状态）
| 项 | 状态 | 验证 |
|---|---|---|
| 访客 demo 只读（写操作 403 拦截） | ✅ 代码实现（auth 中间件 user==demo → 403） | 代码审查 + 接口测试 |
| JWT_SECRET 环境变量必设（无 fallback 硬编码） | ✅ 代码 env 读取；fallback 已回退 | git 历史扫描未检出硬编码值 |
| CRON_SECRET 环境变量（无硬编码） | ✅ 代码 env 读取 | 代码审查 |
| 认证头 X-Auth-Token（网关不改写） | ✅ README 已去网关细节（信息最小化） | README 审查 |

## 加固建议（待办——非紧急）
| 项 | 现状 | 建议 | 优先级 |
|---|---|---|---|
| cron secret 传输 | query 参数 `?secret=`（URL 落日志风险） | ✅ 已完成：仅 X-Cron-Secret 头（弃 query） | 中→已办 |
| cron secret 比较 | `q == secret`（平文比较——timing） | ✅ 已完成：hmac.compare_digest | 中→已办 |
| cron 未配置 secret | `return True`（依赖平台签名 q-sign） | 保留（平台签名保护——生产建议必配 CRON_SECRET） | 低 |
| 密钥轮换 | 未设轮换机制 | 若曾疑似泄漏——立即轮换（env 更新即生效） | 低 |

## README 层面无法验证（仅清单——需人工/定期）
- 平台侧：EdgeOne 环境变量存储安全（JWT_SECRET/CRON_SECRET 值不被平台日志暴露）
- 历史泄漏：git 历史 blob 扫描受仓库 refs 异常限制——建议完整 clone 后定期 `git log -S` 复查

## 决策记录
- 2026-10-13：认证头统一 X-Auth-Token（网关改写 Authorization 根因）——细节见 docs/decisions/
