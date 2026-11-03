# 安全清单（公开仓库 + 公开演示账号视角）

> 前提：仓库公开、演示账号公开（demo/demo123 只读）、域名公开。攻击者能读实现对照打线上。
> 本文只写**可验证的结论**：每条要么有代码/脚本/CI 守着，要么明确写"为什么做不到 + 什么时候重估"。
> 审计命令：`python3 scripts/audit_secrets.py`（含 git 全历史，CI 每次推送 + 每周一自动跑）。

---

## A. 已根治（有代码、有测试、有 CI 守着）

| 项 | 做法 | 守护 |
|---|---|---|
| 访客只读 | `auth_middleware`：`user == "demo"` 且方法为写 → 403 | `local_test` 覆盖；接口实测 |
| 硬编码密钥 | `audit_secrets.py` 扫源码（含脱敏输出） | CI `secrets` job |
| `.env` 入库 | `git ls-files` 检查 + `.gitignore` | CI `secrets` job |
| **git 历史残留** | 扫 ① 全部历史 blob ② commit message ③ reflog 脱分支提交 | CI `secrets` job（`fetch-depth: 0`）+ 每周定时 |
| cron 被恶意重复触发 | ① `_throttle` 进程内限频 ② `_claim` DB 当日抢占（多实例安全）③ **时间窗守卫**：窗口外零 SQL 直接 skipped ④ 失败 `_unclaim` 回滚 | `local_test` 5 条新用例（含"防漏接"静态断言） |
| 堆栈回传浏览器 | `@traced` 统一 200 + `ok:false`，堆栈只写 `quality_logs` | §15.110 |
| `except: pass` 吞异常 | 静默失败 = 事故源；一律 `try_err(src, what, exc)` | 代码评审 + §15.106 三例实证 |

### A1. git 历史扫描：曾经"只能人工"，现在不成立

旧结论是"历史 blob 扫描受仓库 refs 异常限制，建议人工 `git log -S` 复查"。**实测推翻**：仓库只有
几百个对象，全扫 3.4 秒。现在的做法：

```bash
python3 scripts/audit_secrets.py            # 默认含历史
python3 scripts/audit_secrets.py --no-history   # 只需工作区时
```

三个面都扫，且**输出脱敏**（只留前 4 位 + 位数）—— 审计报告若打印完整值，等于把泄露面
从 git 搬到 CI 日志、终端历史、对话记录里，比不扫更糟。这条是写脚本时踩出来的：
初版把 `.env` 与 `.edgeone/auth.json` 的真实值直接打到了终端，现已改为
① 跳过 `.env*` 与 `.edgeone/`（它们是**密钥本体**，由"是否入库"来管，不是硬编码）
② 命中值一律 `abcd***(40位)`。

**2026-11-03 实测结论**：

| 面 | 范围 | 结果 |
|---|---|---|
| 本地历史 | 51 提交 / 424 blob / 脱分支提交 0 | 无命中 |
| 远端可达历史 | 120 提交（最早 2026-10-30）commit message | 无命中 |
| 当前树 | `git ls-tree -r HEAD` 敏感文件名 | 无 |

**注意本地是浅克隆**（`.git/shallow` 存在）。`github.com:443` 在本沙箱不可达 → 无法 `--unshallow`，
所以"完整历史"由 CI 保证（`fetch-depth: 0` + 浅克隆直接报 error），本地扫描只能算快速预检。

### A2. cron：**鉴权**根治不了，但**危害**已经根治

这是本次最需要说清的一条。

**为什么鉴权根治不了（不是偷懒，是物理约束）**：

- EdgeOne schedules 的 schema 只有 `name/cron/timezone/method/path/payload` —— **没有 headers 字段**，
  平台不会带任何自定义头来；
- `payload` 不支持环境变量插值 —— 写死密钥等于把密钥放进配置文件；
- `edgeone.json` 在公开仓库 —— 写死 = 公开；
- 实测网关也不会为普通请求改写 `Authorization`，所以拿 `q-sign` 当判据也不成立。

**所以换了个问题**：既然认不出"谁在调"，就守"现在该不该跑"。四道锁叠加后，外部调用者的能力上限是：

| 攻击动作 | 结果 |
|---|---|
| 随便什么时候打 `/cron/archive` | `skipped`（窗口外），**一行 SQL 都不发**，零 RU |
| 在窗口内打 | 可能把任务**提前几分钟**触发，`_claim` 保证当日仍只执行一次 |
| 高频繁刷 | `_throttle` 挡；即使绕过，`_claim` 已占位 → 只返回 skipped |
| 打 `force=1` 强跑 | 需要正确 `CRON_SECRET`，否则降级为普通调用 |
| 打 `ping` / `continue-tasks` | 无害：前者是保活，后者是幂等续跑器（且被请求驱动直接调用，不走 HTTP） |

窗口按 `Asia/Shanghai` 显式计算（容器在新加坡，不能依赖 TZ），依据是**实测不是猜测**：

```
archive     配置 01:05 CST → 实跑 17:04:03 UTC = 01:04:03 CST
cleanup     03:05 → 19:04:02 UTC = 03:04:02 CST
freshness   03:10 → 19:09:00 UTC = 03:09:00 CST
snapshot    03:35 → 19:34:01 UTC = 03:34:01 CST
daily-rules 04:05 → 20:04:01 UTC = 04:04:01 CST
```

六个任务**一致地早 57~60 秒**（平台按分钟取整），所以窗口两端各留 ≥1.5 小时，不会误杀。

**残余风险**：窗口内被抢先触发 → 多一次正常执行（结果正确、时序略偏）。这个不打算再堵 ——
要堵只能给平台加 header 能力，超出应用层。

**什么时候重估**：若平台支持 schedules 带 header，立刻改强校验（`_authed` 已留好两条通道：
`X-Cron-Secret` 头、body `_k` 字段）。

---

## B. 平台约束下的终态（不是待办，是定案）

### B1. `X-Auth-Token` 而非标准 `Authorization`

**根因**：边缘网关把 `Authorization` 改写成 `q-sign` 后转发 → 应用层拿不到原 Bearer，表现为
"登录成功但所有接口 401"。这曾是最难查的一次故障（§15.13 / `docs/decisions/0001`）。

**定案**：只认 `X-Auth-Token`，并**删掉 Bearer 回退**。回退比不支持更危险 —— 会让"部分接口通、
部分不通"变成随机现象（取决于该路径是否经过改写），排障成本远高于收益。

**验证平台是否变了**（每次大版本或怀疑时跑一次，30 秒）：

```bash
TOKEN=$(curl -s -X POST https://supplykit.top/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
# 期望 401(网关仍改写); 若返回 200 → 平台已不改写, 可以评估切回标准头
curl -s -o /dev/null -w '%{http_code}\n' \
  https://supplykit.top/api/dashboard/summary?channel=jd -H "Authorization: Bearer $TOKEN"
```

**能真正"根治"的两条路**（都不在应用层）：① 平台侧配置网关不改写该头；② 改用 `Cookie` 承载
（HttpOnly 还顺带解决 XSS 读 token，但要改前端存储 + 防 CSRF，收益不抵成本）。**现阶段不做。**

### B2. 平台侧密钥存储

Makers 的 env 由平台托管，值是否被平台日志/工单系统暴露，应用层无法验证也无法控制。
这条**只能靠"少放 + 勤换 + 不主动外泄"**：

| 措施 | 做法 |
|---|---|
| 最小化 | env 只放运行必需的 12 个（`audit_secrets.py` 的 [4] 段自动列出，加新变量会被看到） |
| 不主动外泄 | 禁跑 `edgeone makers env ls`（明文回显 `TIDB_PASSWORD`/`JWT_SECRET`）；不在对话/commit 里贴值 |
| 轮换 | 按下表，或"疑似泄露立即执行" |
| 本地副本 | `.env` 是 600 + gitignored；`.edgeone/` 同理（且已被审计脚本跳过，不参与硬编码判定） |

**轮换操作（唯一仍需人工的动作，5 分钟）**：

1. TiDB 控制台 → Set Root Password → 新密码
2. Makers 控制台 → 项目 env → 改 `TIDB_PASSWORD`（**改完必须重新部署，env 是部署时快照**）
3. GitHub → Settings → Secrets → 同步（若 CI 用到）
4. 本地 `.env` 同步 + `python3 scripts/env_check.py` 验证连通
5. `JWT_SECRET` 轮换的副作用：所有已登录会话失效（用户需重登），挑低峰做

---

## C. 决策记录

| 日期 | 决策 | 依据 |
|---|---|---|
| 2026-10-13 | 认证头统一 `X-Auth-Token`，删 Bearer 回退 | 网关改写 `Authorization` → 全接口 401 |
| 2026-11-01 | cron 迁回平台 schedules，`_authed` 不做强校验 | schedules 无法注入 header；改为守执行次数 |
| 2026-11-02 | cron 加 `_throttle` + `_claim` + 失败回滚 | 路径公开 → 防重复执行/RU 打爆 |
| 2026-11-03 | **cron 加时间窗守卫** | 把"随时可触发"收敛到"每日窗口内 + 当日一次" |
| 2026-11-03 | **历史 blob 扫描进 CI（`fetch-depth: 0` + 每周定时）** | 实测 424 blob 只要 3.4s，"只能人工"不成立 |
