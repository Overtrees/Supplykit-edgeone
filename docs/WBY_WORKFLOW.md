# WorkBuddy(WBY) 专属开发工作流

> 定位: 把 `DEVELOPMENT.md §15` 的规范落成**可执行顺序**。规则本身以 §15 为准, 本文只定义"先做什么、门禁是什么、证据从哪来"。
> 适用: 在 WBY 沙箱内维护 Supplykit(EdgeOne Makers, Provider=Github)。其他项目可复用第 2/6 章骨架。

---

## 1. 环境事实(一次确认, 长期有效)

| 项 | 值 | 验证方式 |
|---|---|---|
| 仓库 | `Overtrees/Supplykit-edgeone`, 默认分支 **feat/edgeone** | `git branch --show-current` |
| 平台 | EdgeOne Makers, 项目 `makers-8gstkvheqm2c` | `edgeone makers list` |
| 部署方式 | **Provider=Github → 只能 git push**, `makers deploy` 被拒 | 推送后查部署记录 |
| 线上 | `https://supplykit.top` | `curl /api/health` |
| 沙箱网络 | `github.com` 大流量易超时; `api.github.com` 稳定 | 失败改用 `gh api` |
| 凭据 | `~/.git-credentials`、`~/.config/gh/hosts.yml`、`.env.local`(EDGEONE_API_TOKEN)、`.env`(运行变量) | 均为 600, 且 `.env` 已 gitignore |
| 数据库 | TiDB Cloud Serverless, 变量在 `.env`(6×TIDB_* + DB_BACKEND + JWT_SECRET) | 只读连接测试 |

**禁止**: `edgeone makers env ls` —— 会明文回显 `TIDB_PASSWORD` / `JWT_SECRET`。需要值时读本地 `.env`。

---

## 2. 标准闭环(六步, 不可跳)

### 2.1 改前

- 读 `DEVELOPMENT.md §15.8`(数据优化原则)与本项目既有定案(见 `CHANGELOG.md` 同日条目);
- `git status --short` 确认工作区干净, 避免把无关改动混进提交;
- **先把验收指标写成可量化的数字**(如"四维偏差 0 项""入库日期 100%"), 否则无法判断"改完了"。

### 2.2 本地验证三件套

```bash
# ① 语法门禁(线上 runtime 是 Python 3.10)
python3 -c "import ast;ast.parse(open('cloud-functions/api/routes/xxx.py').read(),feature_version=(3,10))"

# ② 全量回归
cd cloud-functions/api && python3 local_test.py   # 期望: 184 通过, 0 失败

# ③ 规模 mock(单 SKU + 200 SKU 各跑一轮)
python3 /tmp/verify_xxx.py
```

**为什么必须跑规模 mock**: 单 SKU 样本会让"批次/分组/分摊"类缺陷全部通过(每次只走一条分支)。

### 2.3 提交

- 约定式: `fix(scope): 主题` / `perf(scope)` / `feat(scope)`;
- body 写清 **根因 → 修法 → 验证**, 带实测数字(便于日后回溯"当时凭什么判断好了");
- 绝不提交 `.env` / `.edgeone/` / 任何密钥。

### 2.4 推送(网络不稳, 需重试)

```bash
cd /workspace/supplykit
for i in 1 2 3; do
  if timeout 100 git -c http.version=HTTP/1.1 push origin feat/edgeone; then break; fi
  sleep 3
done
gh api repos/Overtrees/Supplykit-edgeone/branches/feat/edgeone --jq '.commit.sha[0:7]'
```

- **坑**: `git push | tail -2` 的退出码是 `tail` 的 0, 会把失败当成功; 必须直接判断 `git push` 的退出码。
- **只认远端 SHA**: push 输出为空/TLS 报错都可能"其实已成功", 一律用 `gh api` 复核。

### 2.5 部署确认

```bash
TOKEN=$(grep -m1 '^EDGEONE_API_TOKEN=' .env.local | cut -d= -f2-)
curl -s -X POST https://pages-api.cloud.tencent.com/v1 \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"Action":"DescribePagesDeployments","ProjectId":"makers-8gstkvheqm2c","Offset":0,"Limit":1,"OrderBy":"CreatedOn","Order":"desc"}'
```

- 只认 **`Status=Success` 且 `RepoCommitHash` = 本地 SHA**;
- **不需要腾讯云 SecretId/SecretKey** —— 这一点曾误判, 已纠正;
- 构建耗时 2~4 分钟, 轮询间隔 15s。

### 2.6 线上验证

1. `curl https://supplykit.top/api/health` → 200(记录耗时);
2. 跑附录 A 的十项只读指标;
3. 若改动涉及写路径, 查 `quality_logs` 最近 2 小时是否出现新告警;
4. **部署成功 ≠ 功能生效** —— 分支未触发的路径(如"未填批次告警")必须说明"当前无数据触发", 不能声称已验证。

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

- [ ] `local_test.py` 184 通过 0 失败
- [ ] 语法门禁 3.10 通过
- [ ] 规模 mock(≥200 SKU)通过
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
| `GnuTLS recv error (-110)` | 沙箱到 github.com 连接被断 | 重试 ≤3 次 + `http.version=HTTP/1.1`; 仍失败改 `gh api` 提交 |
| push 显示成功但远端没变 | 管道退出码误导 / 实际失败 | 用 `gh api` 查远端 SHA |
| 长任务"卡住"不动 | `tail` 缓冲 / 连接挂起 | 查 `information_schema.processlist`; 改 `flush=True` 写文件 |
| 对账全量偏差 | 重灌顺序错(漏 recalc) / 两进程并发 | 按 3.1 顺序重跑, 先确认无残留进程 |
| seed 单步超时 | 逐条 UPDATE 往返太慢 | 改单条 `JOIN UPDATE`(200s → 0.3s) |
| 部署状态查不到 | 误以为需要 SecretKey | 用 Bearer + `DescribePagesDeployments` |
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
| `docs/DEVELOPMENT.md §15.8` | 数据优化原则十条(本文门禁的来源) |
| `docs/DEVELOPMENT.md §15.9` | 生产操作红线(改前 commit / 改后验证) |
| `docs/DEVELOPMENT.md §15.37` | 生产库排查方法论 |
| `docs/MAKERS_API.md` | pages-api 认证与 Action 清单 |
| `docs/ARCHITECTURE.md` | 免费额度 / RU 口径 / 数据流 |
| `docs/security.md` | 密钥与权限边界 |
| `CHANGELOG.md` | 定案与教训的原始记录(改前必读同日条目) |
