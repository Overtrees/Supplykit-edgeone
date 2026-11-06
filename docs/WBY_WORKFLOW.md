# WorkBuddy(WBY) 专属开发工作流

> 定位: 在 WBY 沙箱里**干活时的环境差异与故障速查**。通用闭环/门禁只在 `docs/SKILL.md` §4 与
> `docs/DEVELOPMENT.md` 维护, 本文不重复（重复过, 已经漂移: 这里曾写着"回归 392 通过"）。
> 适用: 在 WBY 沙箱内维护 Supplykit(EdgeOne Makers, Provider=Github)。其他项目可复用第 1/3/7 章骨架。

---

## 1. 环境事实(一次确认, 长期有效)

| 项 | 值 | 验证方式 |
|---|---|---|
| 仓库 | `Overtrees/Supplykit-edgeone`, 默认分支 **feat/edgeone** | `git branch --show-current` |
| 平台 | EdgeOne Makers, 项目 `makers-8gstkvheqm2c` | `edgeone makers list` |
| 部署方式 | **Provider=Github → 只能 push 触发**, `makers deploy` 被拒 | 推送后 `deploy_status.py` |
| 线上 | `https://supplykit.top` | `curl /api/health` |
| 沙箱网络 | **`github.com:443` 不可达**(131s TLS 断); `api.github.com` 稳定 | 推送只能走 `push_api.py` |
| 凭据 | `.edgeone/auth.json`(`EDGEONE_PAGES_API_TOKEN` + `GITHUB_TOKEN`)、`.env`(运行变量) | `python3 scripts/env_check.py` 一次性体检 |
| 数据库 | TiDB Cloud Serverless, 变量在 `.env`(6×TIDB_* + DB_BACKEND + JWT_SECRET) | 只读连接测试 |

**禁止**: `edgeone makers env ls` —— 会明文回显 `TIDB_PASSWORD` / `JWT_SECRET`。需要值时读本地 `.env`。

---

## 2. 标准闭环 → 见 `docs/SKILL.md` §4「七步交付闭环」

闭环本身（改前 → 门禁 → 提交 → 推送 → 部署判据 → 线上验证 → 落文档）**只在一处维护**，
本文不再抄第二份。本沙箱特有的四条差异记在这里：

1. **推送只能走 `scripts/push_api.py`**：到 `github.com:443` 的 TLS 在 131s 断开，重试 3 次只是白等
   ~7 分钟，且 `git push | tail -2` 的退出码是 `tail` 的 0（会被当成成功）。`api.github.com` 稳定。
2. **部署判据是"逐文件一致"**，不是 `Commit == 本地 HEAD` —— API 建提交时 author/committer 时间戳
   不同，SHA 必然不同，这是预期不是失败。
3. **`deploy_status.py` 只需要 `EDGEONE_PAGES_API_TOKEN` + ProjectId**，不需要腾讯云 SecretId/SecretKey
   （这一点曾误判，已纠正）。构建 2~2.5 分钟；连不上先跑 `scripts/env_check.py`。
4. **门禁是 `bash scripts/preflight.sh` 八步**（回归 681 / SQL 冒烟 / lint / tsc / vitest / i18n），
   不是本文旧版列的三条手工命令 —— 手工命令只用于门禁没覆盖的临时脚本。

---


## 3. 数据操作工作流

### 3.1 重灌顺序(铁律, 顺序错必出偏差)

```
seed step6(生成流水) → _recalc_own_inventory(重算可用) → _rebuild_own_batches(重建批次) → best_before 回写
```

- 漏 `_recalc` 直接重建 → 实测 177 / 18 项偏差;
- **禁止两个重灌进程并发** —— 批次删除是整段 DELETE, 会互相清表;
- 进程异常退出后先 `pgrep` 确认无残留, 再重跑。

### 3.2 长任务

- 每 100 SKU 一批, 每批 `print(..., flush=True)`;
- 长任务输出写文件, **不要用 `tail -6` 收实时进度** —— 管道缓冲会让人误判"卡死"并杀掉重跑;
- 单函数超过 90s 就不适合放进 Makers 单步(见 `sync_tasks` 续跑机制)。

### 3.3 缓存

- 正常写路径由路由层每步 `invalidate_all()`;
- **直连数据库会绕过它** → 只能等 TTL 自愈:

| 接口 | TTL |
|---|---|
| orders 分页 | 5s |
| dashboard summary | 30s |
| dashboard aux / accel | 60s |
| purchase / replenishment | 300s |

---

## 4. 成本(RU)

- RU 在**存储/计算层**计量, 与接入方式(MySQL 直连/HTTP API/控制台)无关 —— 直连同样计费;
- 公网出口 **1KiB = 1RU**, 同区内网 4KiB = 1RU → 本地比线上贵 4 倍;
- 省 RU 靠**减少扫描**, 不靠换连接: 多指标合并成单次查询、避免相关子查询、大聚合走物化表;
- 只读验证优先用"单次多指标 SQL", 禁止循环里逐条查。

---

## 5. 安全

| 风险 | 做法 |
|---|---|
| 密钥明文回显 | 不跑 `env ls`; 不 `cat .env` 到对话 |
| 密钥入库 | `.env` 在 `.gitignore`; 提交前 `git status` 复核 |
| 会话泄漏 | 一旦出现明文, 清理会话记录并提示轮换 |
| 生产写操作 | 先只读确认影响面 + 记录回滚点(本次改动前的批次行数/Σ) |

---

## 6. 交付前检查清单

- [ ] `local_test.py` 405 通过 0 失败
- [ ] 语法门禁 3.10 通过
- [ ] 规模 mock(≥200 SKU)通过
- [ ] **红线 A**: 新逻辑是否落独立文件? 页面/路由是否只做编排? 改动文件有没有继续变胖(见 `SKILL.md` §11)
- [ ] **红线 B**: 改动文件里的中文字面量是否只剩注释? 新增 i18n key 是否 zh+en 双份?
- [ ] **四维**: 准确 / 实时 / 完整 / 可靠 —— 每项有实测证据, 不能只写"应该没问题"
- [ ] **§15.8 十条**: 相关的逐条过(尤其计算范围未缩小、等价重构、缓存版本号)
- [ ] **数据四维**: 键是否含 仓型 + SKU + 69码 + 仓名; 空码是否按"任一方为空则降级"处理
- [ ] 部署 `Success` 且 SHA 匹配
- [ ] health 200
- [ ] 十项只读指标通过
- [ ] `CHANGELOG.md` 已记录(含实测数字与"未验证项")

---

## 7. 故障速查

| 现象 | 根因 | 动作 |
|---|---|---|
| `Failed to connect github.com port 443` / `GnuTLS recv error` | 沙箱到 github.com 不可达(131s 断) | 不要重试, 直接 `python3 scripts/push_api.py` |
| push_api 报错或远端 HEAD 未变 | 凭据缺 `repo contents:write` / 网络抖动 | `python3 scripts/env_check.py` 看 PUSH 项; 脚本可重跑(按内容 diff, 幂等) |
| 长任务"卡住"不动 | `tail` 缓冲 / 连接挂起 | 查 `information_schema.processlist`; 改 `flush=True` 写文件 |
| 对账全量偏差 | 重灌顺序错(漏 recalc) / 两进程并发 | 按 3.1 顺序重跑, 先确认无残留进程 |
| seed 单步超时 | 逐条 UPDATE 往返太慢 | 改单条 `JOIN UPDATE`(200s → 0.3s) |
| 部署状态查不到 | 误以为需要 SecretKey / token 失效 | `python3 scripts/env_check.py` 看 DEPLOY 项; 或 `scripts/deploy_status.py` |
| 改完线上数字没变 | 缓存 TTL 未过 / 未 invalidate | 等 TTL 或触发一次写操作 |
| 配置类数据滞后 | localStorage 缓存 | 配置不缓存(§15.92) |

---

## 附录 A 十项只读指标

> `pymysql` 下 `DATE_FORMAT('%Y-%m-%d')` 的单 `%` 会被当占位符, 必须写 `%%` 或用 `CURDATE()`。

1. 四维对账偏差项数(按 `仓型+SKU+69码+仓名` 逐一, `ABS(可用−Σ批次)>1`)
2. `Σ批次` vs `inventory.available_qty`(分渠道)
3. `inbound_records` / `outbound_records` 缺 `prod_date` 条数
4. 入库日期 < 生产日期 条数
5. 出库日期 < 该批入库日期 条数
6. 卖超 SKU 数(Σ出库 > Σ入库)
7. 负库存 / 负批次行数
8. 出库率(Σ出库 / Σ入库, 分渠道)
9. 流水日期跨度(判断是否挤在单月)
10. `best_before` 与批次 `MIN(exp_date)` 不一致条数

---

## 附录 B 相关文档

| 文档 | 用途 |
|---|---|
| `docs/DEVELOPMENT.md` | 现行规范册(写法/门禁/数据流) |
| `docs/SKILL.md` §4 | 七步交付闭环(唯一真源) |
| `docs/history/` | §15.8 数据十条 / §15.9 生产红线 / §15.37 排查方法论 |
| `docs/MAKERS_API.md` | pages-api 认证与 Action 清单 |
| `docs/ARCHITECTURE.md` | 免费额度 / RU 口径 / 数据流 |
| `docs/security.md` | 密钥与权限边界 |
| `CHANGELOG.md` (当月) + `docs/history/` | 定案与教训的原始记录(改前必读同日条目) |
