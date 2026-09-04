# TiDB 迁移白皮书（2026-09-02）

## 1. 背景与目标

**触发**：supplykit.db 二次 malformed 事故（9-2）——SQLite 单文件 + PA 512MB 配额组合：
- 配额满 → SQLite 写失败 → `database disk image is malformed` → app 全 500
- 修复：恢复钩子前置 + 省配额自愈（已上线，50s 自动恢复）

**目标**：根治"磁盘写满毁库"（最痛故障源），DB 独立于 PA 文件配额。

## 2. 数据规模

| 表 | 行数 | 日增 |
|---|---|---|
| orders | 185,420 | ~3,500（60 天累计） |
| inventory | 17,000 | 灌入型 |
| daily_sales_snapshot | 138,574 | 日快照 |
| alerts | ~数千 active | 规则/补货引擎生成 |
| products/suppliers/batches 等 | 千级 | 低频 |

## 3. 代码审计结果（迁移成本量化）

### 数据访问层（自定义 ORM）
- `db.table()` 调用：**214 处**（26 文件）
- 原生 `conn.execute()`：**322 处**
- 自定义 ORM：TableRef/QueryBuilder/InsertBuilder/UpdateBuilder，内置 SQLite 专有冲突子句

### SQLite 专有语法（需适配 MySQL/TiDB）

| 语法 | 次数 | TiDB 适配 |
|---|---|---|
| `PRAGMA`（busy_timeout/journal_mode/wal_checkpoint/quick_check） | 54 | **剔除**（TiDB 自动管理）；quick_check 自愈需换成 SELECT 探活 |
| `strftime` | 63 | `DATE_FORMAT` |
| `datetime('now')` | 47 | `NOW()` / `CURRENT_TIMESTAMP` |
| `AUTOINCREMENT` | 28 | `AUTO_INCREMENT`（TiDB 支持） |
| `INSERT OR IGNORE` | 19 | `INSERT IGNORE`（MySQL 兼容） |
| `INSERT OR REPLACE` | 12 | `REPLACE INTO` |
| `substr(` | 7 | `SUBSTRING(` |
| `ON CONFLICT(...) DO UPDATE` | 5 | `ON DUPLICATE KEY UPDATE` |
| `||` 拼接 | 若干 | `CONCAT()` |

**文件集中度**（高到低）：database.py(55) > seed.py(23) > scheduler.py(15) > main.py(13) > sales_utils.py(12) > health.py(12)

### 工期估算
- SQL 审计清单 + 适配：2-3 人日
- ORM 抽象层改写（214 处兼容双后端）：10-15 人日
- 数据迁移演练（全量+增量）：2-3 人日
- 双库并行验证 + 切流量 + 回归：5-7 人日
- **合计：20-40 人日**

## 4. 四维评估

| 维度 | 评估 |
|---|---|
| 准确性 | ✅ MVCC/分布式事务，写失败不毁库；❌ 迁移期 SQL 语义偏差需逐条验证（strftime/upsert 为高发点） |
| 完整性 | ✅ 多副本 + 独立存储；❌ 迁移工具需演练（mydumper/DM），117 测试+全页面回归做护栏 |
| 实时性 | ⚠️ **PA→TiDB Cloud 跨公网 RTT +50-100ms/查询**：看板 cold 路径可能 1.8s→2.5-4s（缓存命中不受影响）；并行化/连接池可缓解 |
| 可靠性 | ✅ 根治配额写满（主要收益）；❌ 新增外部 SaaS 依赖（SLA/网络抖动） |

## 5. 决策建议

**推荐顺序**：
1. ✅ 保持 SQLite + 自愈（已上线）：稳运营基线
2. ✅ 配额监控（已上线 69%）：写满前预警
3. 订单归档 90 天窗口（待自然滚动）：db 控在 ~205MB
4. **TiDB 迁入**：接受"响应性微降换根治"再启动；迁移前完成 SQL 审计清单 + 数据演练

**方案对比**：TiKV/TiDB 自建（3 节点成本↑）> TiDB Cloud Serverless（建议，按量计费）> 维持 SQLite+归档（中短期够用）

**风险预案**：迁移期间保留 SQLite 为 read 后备；双写双读窗口 2-4 周；回滚点 = 全量快照。

## 6. 免费版（Starter）官方口径验证补充（2026-09-04）

**免费配额**（官方定价详情页实测）：每实例每月 **5 GiB 行存 + 5 GiB 列存 + 5000 万 RU**；每组织最多 5 个免费实例（合计 25 GiB + 2.5 亿 RU/月）。

**存储**：按**逻辑容量**计费（$0.2/GiB-月），多副本含在价格内，非物理 3 倍计费——当前线上 125MB，5 GiB 余量 ~40 倍，**存储不是瓶颈**。

**RU 是硬约束**：读 64KiB payload=1RU / 8 read req=1RU、写 2KiB=1RU、SQL CPU 3ms=1RU、**公网出口 1KiB=1RU**（PA→TiDB 跨公网返回数据直接吃 RU；`EXPLAIN ANALYZE` 不含出口 RU，易低估）。免费版单查询内存上限 256 MiB。

**配额耗尽行为（比预想严重）**：免费实例任一配额超限 → **立即拒绝新连接 + 存量连接限流**，直到设消费上限或下月重置——不是"响应微降"而是**服务拒连**。

**粗算**：看板 summary 全量重建（扫 18.5 万行）单次 ~1-3K RU，3min TTL 下日耗接近 167 万 RU/天上限（5000 万/月）；补货全 SKU 扫描同量级。**免费 RU 下定期全量重建有耗尽风险**。

**时区**：TiDB `time_zone` 默认 SYSTEM（托管实例通常 UTC），与 SQLite `datetime('now')`=UTC 语义一致；建实例后 `SELECT @@system_time_zone` 实测确认。代码 6 处 UTC 均为 Python 层，不受影响。

**休眠**：现行官方文档**未见自动休眠条款**（旧 Serverless 曾有 5min idle sleep）；仅公网连接 idle timeout 断连。唤醒延迟不可断言，建实例后实测。

**代码审计修正**（相对 §3）：PRAGMA 59 处（原 54）、`||` 拼接 **0 处**（原"若干"不实）、strftime 64、INSERT OR 35、ON CONFLICT 5、AUTOINCREMENT 0。