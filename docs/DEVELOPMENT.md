# SupplyKit 开发规范

## 一、项目定位


SupplyKit 是**电商供应链数据清洗与补货决策看板**，定位为 ERP 与 Excel 之间的"中间层工具"——不做 ERP 的流程管理，也不替代 Excel 的灵活性。

### 核心原则
- 看板 + 补货决策为最主要核心
- 数据经过清洗、规则引擎、补货建议，最终输出决策
- 不替代 ERP 的核心流程管理
- 不与 Excel 竞争，而是互补——SupplyKit 做自动化，Excel 做灵活性

---

## 二、技术栈


| 层级 | 技术 | 版本 |
|---

## 三、项目结构


```
Supplykit/
├── frontend/                    # 前端
│   ├── src/
│   │   ├── App.tsx              # 主入口（391 行，从 1098 拆分）
│   │   ├── main.tsx             # 挂载点
│   │   ├── locale.ts            # 国际化翻译（150+ 键，中英双语）
│   │   ├── types.ts             # 全局类型定义
│   │   ├── theme.ts             # 主题配置（深色/浅色模式）
│   │   ├── version.ts           # 版本信息
│   │   ├── api/
│   │   │   └── client.ts        # API 客户端（缓存+在途去重+console.debug日志）
│   │   ├── store/
│   │   │   └── useAppStore.ts   # Zustand 全局状态
│   │   ├── pages/               # 10 个页面组件
│   │   ├── components/          # 通用组件
│   │   │   ├── Card.tsx          # 支持 borderRadius/valueColor 属性
│   │   │   ├── Chart.tsx         # 自动注入深色模式 tooltip/label 颜色
│   │   │   ├── Toast.tsx         # 玻璃态模糊背景
│   │   │   ├── Sidebar.tsx      # 页面内渲染（非 position:fixed overlay）
│   │   │   ├── ConfirmDialog.tsx
│   │   │   ├── EmptyState.tsx
│   │   │   ├── ErrorBoundary.tsx
│   │   │   ├── Icons.tsx
│   │   │   └── hammer/          # 锤子菜单组件（8 个）
│   │   └── styles.css           # 全局样式 + CSS 变量 + 玻璃态 + 横屏适配
│   ├── vite.config.js
│   └── package.json
│
├── backend/                     # 后端
│   ├── app/
│   │   ├── main.py              # API 请求日志中间件（>500ms 标 warning）
│   │   ├── core/
│   │   │   ├── database.py      # SQLite ORM + TableRef（DB_LOG 环境变量控制日志）
│   │   │   ├── dashboard_cache.py  # 看板内存缓存 15s
│   │   │   ├── replenishment_cache.py  # 补货建议持久化缓存 3min
│   │   │   ├── sales_utils.py   # 日销计算（三窗口 3σ 剔除 + 趋势加权）
│   │   │   ├── rules.py         # 规则引擎
│   │   │   ├── scheduler.py     # APScheduler 定时任务 + 磁盘自检/备份保留7个
│   │   │   ├── database.py     # SQLite ORM + 任务持久化 + 索引 + 渠道迁移
│   │   │   └── sales_utils.py  # 日销计算 + sku_to_channel 渠道推断
│   │   └── api/routes/          # 19 个路由模块
│   └── tests/                   # 80+ 个后端测试
│
└── docs/
    └── DEVELOPMENT.md
```

---

## 四、代码规范


### 4.1 TypeScript

- **组件 Props 接口**：`interface XxxProps { ... }`
- **避免 `any` 类型**：优先使用具体类型或泛型
- **Store 状态有接口定义**：`AppState` / `AppActions`
- **全文件覆盖 100%**：31 个 TS 文件均已添加类型定义
- 核心类型：`ColumnDef`、`WarehouseType`、`ToastItem`、`OrderItem`、`ChartProps` 等

### 4.2 React 组件

```tsx
export default function ComponentName({ prop1, prop2 }: { prop1: string; prop2?: number }) {
  // ...
}
```

- **函数组件**，不使用 class 组件（ErrorBoundary 除外）
- **默认导出**：`export default function Xxx()`

### 4.3 CSS 规范

**CSS 变量（魔法数字抽取）**
```css
--radius-sm: 12px;   --radius-md: 16px;   --radius-lg: 32px;   --radius-full: 99px;
--space-xs: 4px;     --space-sm: 10px;    --space-md: 12px;    --space-lg: 16px;    --space-xl: 20px;
--font-xs: 11px;     --font-sm: 12px;     --font-md: 14px;     --font-lg: 16px;
--h-btn: 30px;       --h-btn-lg: 36px;    --h-btn-xl: 48px;
```

**CSS 工具类（50+ 个）**

| 类别 | 类名 | 说明 |
|---

## 五、数据流规范


### 5.1 API 缓存

- **dashboard**：内存缓存 15s
- **补货建议**：持久化缓存 5min + 数据版本号
- **日销快照**：`daily_sales_snapshot` 表，每天凌晨 3:30 构建
- **在途去重**：同一请求未完成时复用

### 5.2 数据归档

- 订单超 90 天自动聚合为 `daily_stats` 行，删除原始订单
- 每天凌晨 1 点执行

---

## 六、部署规范


### 6.1 前端部署

```bash
git push origin main → Cloudflare Pages 自动构建
```

### 6.2 后端部署

```bash
cp backend/app/api/routes/file.py /tmp/file.py
curl -X POST -H "Authorization: Token $PYTHONANYWHERE_TOKEN" \
  -F "content=@/tmp/file.py" \
  "https://www.pythonanywhere.com/api/v0/user/Overtrees/files/path/home/Overtrees/Supplykit/backend/app/..."
curl -X POST -H "Authorization: Token $PYTHONANYWHERE_TOKEN" \
  "https://www.pythonanywhere.com/api/v0/user/Overtrees/webapps/overtrees.pythonanywhere.com/reload/"
```

### 6.3 冷启动保活

UptimeRobot 每 5 分钟 ping `https://overtrees.pythonanywhere.com/api/insights/ping`
### 6.4 CI/CD 自动部署

项目采用双通道 CI/CD：

**前端（Cloudflare Pages）**
- 推送 `main` 分支后自动触发构建
- 构建命令：`cd frontend && npm run build`
- 输出目录：`frontend/dist`
- 环境变量在 CF Pages 控制台配置：`VITE_SENTRY_DSN` / `VITE_SENTRY_ENV` / `SENTRY_AUTH_TOKEN` / `SENTRY_ORG` / `SENTRY_PROJECT` / `SENTRY_URL`
- 无需独立 GitHub Actions 工作流

**后端（GitHub Actions → PythonAnywhere）**
- 工作流文件：`.github/workflows/deploy-backend.yml`
- 触发条件：推送 `main` 且 `backend/**` 有变更
- 步骤：
  1. 遍历 `backend/app/` 下所有 `.py` 文件，curl 上传到 PA
  2. 429 限流自动重试（3 次，指数退避）
  3. 调用 PA API 重启 webapp（失败重试 5 次，间隔 30s）
  4. 健康检查轮询（最多 6 次，每次 20s）确认服务恢复
- GitHub Secrets 需配置：`PYTHONANYWHERE_TOKEN`

**Sentry Sourcemap 上传**
- 前端构建时通过 `@sentry/vite-plugin` 自动上传 sourcemaps
- EU 区必须设置 `SENTRY_URL=https://de.sentry.io/`
- 构建完成后自动删除本地 `.map` 文件（防源码泄露）


---

## 七、测试规范（严格标准）


### 7.0 核心原则：测试先于代码

```
改代码前 → 先写测试 → 确认测试失败 → 改代码 → 确认测试通过
```

所有功能修改、bug 修复、重构，必须遵循此流程。

### 7.1 测试覆盖要求

| 变更类型 | 必须覆盖的测试 | 最低要求 |
|---

## 八、提交前自动化检查（严格标准）


### 8.1 必须配置的自动化检查

以下检查必须在每次提交前自动运行，**不允许手动跳过**：

```bash
# 1. 括号匹配检查（防止 JSX 语法错误）
find src -name '*.tsx' -o -name '*.ts' | while read f; do
  node -e "const fs=require('fs');const s=fs.readFileSync('$f','utf8');const po=(s.match(/\(/g)||[]).length,pc=(s.match(/\)/g)||[]).length;const bo=(s.match(/\{/g)||[]).length,bc=(s.match(/\}/g)||[]).length;if(po!==pc||bo!==bc){console.log('❌ 括号不匹配: $f');process.exit(1)}" 2>/dev/null
done

# 2. import.meta.env 拼写检查
grep -rn "import.meta\.einv" src/ && echo "❌ import.meta.env 被误改" && exit 1

# 3. t() 引号包裹检查
grep -rn "'{t(\"" src/ && echo "❌ t() 在字符串中" && exit 1

# 4. height/borderRadius 不带 px 单位
grep -rn "height:[0-9]\+px\|borderRadius:[0-9]\+px" src/ --include='*.tsx' && echo "❌ 数字值带 px 单位" && exit 1

# 5. 重复导入检查
grep -rn "import.*from.*locale" src/ | sort | uniq -d && echo "❌ 重复导入" && exit 1

echo "✅ 全部检查通过"
```

### 8.2 建议配置的自动化检查

```bash
# 6. 前端测试
cd frontend && npm test || exit 1

# 7. 后端测试
cd backend && python -m pytest tests/ -v || exit 1

# 8. TypeScript 类型检查
cd frontend && npx tsc --noEmit || exit 1
```

### 8.3 提交前核对清单

```bash
# 一键执行全部检查
echo "=== 1. 括号匹配 ==="
find src -name '*.tsx' -o -name '*.ts' | while read f; do
  node -e "const fs=require('fs');const s=fs.readFileSync('$f','utf8');const po=(s.match(/\(/g)||[]).length,pc=(s.match(/\)/g)||[]).length;const bo=(s.match(/\{/g)||[]).length,bc=(s.match(/\}/g)||[]).length;if(po!==pc||bo!==bc)console.log('FAIL: $f')" 2>/dev/null
done

echo "=== 2. import.meta.env ==="
grep -rn "import.meta\.einv" src/ && echo "❌" || echo "✅"

echo "=== 3. t() 在字符串中 ==="
grep -rn "'{t(\"" src/ && echo "❌" || echo "✅"

echo "=== 4. height/borderRadius 单位 ==="
grep -rn "height:[0-9]\+px\|borderRadius:[0-9]\+px" src/ --include='*.tsx' && echo "❌" || echo "✅"

echo "=== 5. 重复导入 ==="
grep -rn "import.*from.*locale" src/ | sort | uniq -d && echo "❌" || echo "✅"

echo "=== 6. 未提交文件 ==="
git status --short
```

---

## 九、代码审查（严格标准）


### 9.1 审查流程

```
提交 PR → 至少 1 人审查 → 通过 → 合并到 main
```

### 9.2 审查 checklist

| 审查项 | 必须通过 |
|---

## 十、开发经验总结（2026-08-21）


### 数据库
1. **WAL vs DELETE 模式**：WAL 读写并发（seed 填充写 12 万订单期间读不阻塞），DELETE 读写互斥（页面全卡）。PA 环境 WAL 有文件损坏风险，用启动自检 + .gz 备份自动恢复兜底。
2. **auto_vacuum=INCREMENTAL**：DELETE 后空间自动回收，不需要独占锁（规避 VACUUM 被锁问题）。
3. **VACUUM 阈值**：数据库真实大小 99MB，阈值设 80MB 太小会导致反复触发 VACUUM 锁死所有接口。应设 > 数据库真实大小（如 150MB）。
4. **线程池 `max_workers`**：`max_workers=2` 太小，1 个卡死任务就堵死。应设 4+，配合启动时清理 running 超 10 分钟的任务。
5. **`_seed_builtin_rules` 必须用 `get_conn()`**：直接 `sqlite3.connect` 无 `row_factory`，污染主线程连接导致 `dict(r)` 报错（"cannot convert dictionary update sequence element #0 to a sequence"）。
6. **压缩备份**：VACUUM INTO + gzip，备份体积减半，防止撑爆 PA 配额。

### ORM / Builder 模式
7. **`insert({...})` 必须调 `.execute()`**：`db.table().insert({...})` 只创建 Builder，不执行 INSERT。必须 `.execute()`（3 处变更历史 insert 缺 execute 导致从未写入）。

### 任务系统
8. **任务类型 `channel='all'`**：全局任务（seed/reset）不区分渠道，标记 `channel='all'`，查询时 `WHERE channel=? OR channel='all'`。
9. **任务卡片步骤可视化**：`/api/tasks` 返回 steps 字段（`result` 中解析），前端卡片显示步骤明细（✓ 完成/… 进行中/✗ 失败 + 耗时）。
10. **页面回前台即时刷新**：`visibilitychange` + `focus` 事件触发数据刷新，不等 setInterval（挂后台回来时立即看到最新进度）。

### 缓存
11. **缓存命中与 miss 返回格式必须一致**：后端缓存命中返回 `ok(cached['data'])` 统一格式；前端缓存命中也要解包 `{ok,data}`（与拦截器一致），否则 `Array.isArray` 判断失败。
12. **内存缓存注意保存时失效**：rules/replenishment-config 加 30s 内存缓存，创建/更新/删除时清空缓存。

### 前端
13. **模块级代码不能引用未导入的变量**：`TYPE_LABEL` 引用未 import 的 `IconUndo` → 模块加载时抛 ReferenceError → 整个 JS bundle 加载失败 → 页面空白（连登录页都不显示）。
14. **API 变量用 `import.meta.env`**：不要硬编码，保持与所有文件一致的环境变量配置。

### PA 环境
15. **免费版 512MB 配额 + 不稳定文件系统**：WAL 损坏、write error 是环境问题，非代码 bug。需要启动自检 + 自动恢复 + 压缩备份兜底。长期建议升级付费版或迁移轻量服务器。

---



## 十一、国际化规范


---

## 十二、版本控制


### 12.1 Commit 格式

```
<type>: <description>
feat: 新功能 | fix: Bug | refactor: 重构 | docs: 文档 | test: 测试 | style: 样式 | chore: 杂项
```

### 12.2 分支策略

- `main` 分支直接部署到生产环境
- 推送到 `main` 自动触发 Cloudflare Pages 构建

---

## 十三、提交前核对清单（手动备用）


> 自动化检查尚未完全实现时，手动执行以下命令作为替代。

### 12.1 JSX 内联样式常见错误

| 错误写法 | 正确写法 | 报错 |
|---

## 十四、常见问题


| 问题 | 原因 | 解决 |
|---

## 十五、2026-08-07 关键改进记录


### 14.1 性能优化（10 万单量级）
- 统一日销数据源：`load_daily_sales`（快照历史 + 当天 orders），消除重复计算
- SQL 级过滤 + 8 个索引，补货/滞销/进销存查询大幅提速
- `calc_sales_from_daily` 预计算日期列表，消除 8 万次 datetime 调用
- 告警批量处理：2000 次独立查询 → 3 次（executemany）
- 一键填充：12 分钟 → 2.5 分钟（订单 executemany 5.4x，规则引擎批量 1000x）
- with-sales 结果缓存 30s（版本号校验）

### 14.2 渠道隔离（jd/other 全链路）
- 告警/规则/供应商/已下单/出入库全部按 channel 隔离
- `evaluate()` 按 channel 过滤规则；`sku_to_channel()` 从 products 主表推断渠道
- 供应商 code 加渠道后缀，避免两渠道 upsert 互相覆盖
- 清洗页渠道标记与全局渠道联动 + UI 明确导入目标

### 14.3 可靠性（存储配额 + 任务持久化）
- **存储配额根治**：40 个每日备份（2-3GB）撑爆 512MB → 备份只保留 7 个 + 每日磁盘自检 + WAL checkpoint
- **任务状态持久化**：sync_tasks 表（task_id 列），跨重启可恢复 status/result/steps
- 健康检查加 integrity quick_check + WAL 监控
- 启动时自动 WAL checkpoint；seed 后 WAL checkpoint
- 数据归档惰性兜底（with-sales 请求时每天检查一次）

### 14.4 加载/骨架屏防卡死
- `loadReplen` 独立 seq（不再与外层 useEffect 共享 reqSeq）
- InventoryPage/DashboardPage 竞态丢弃时关闭 loading
- 前端任务轮询 not_found 容错（重试 3 次）
- 欢迎页"开始体验"与一键填充联动修复（task_id 存储 + requires_reset 处理）

### 14.5 规则引擎组合表达式
- 四则运算支持：可用+在途 / 安全线-可用 / 可用/日销（可撑天数）/ 订单数量×单价
- 定时任务注入日销，支持断货风险类规则
- 告警按 source 区分（replenishment_engine / rules_engine），口径统一不误关
- 规则引擎紧急补货考虑在途（可用+在途≤安全线才算真紧急）

### 14.7 APM 监控
- 内存聚合请求统计（总请求/平均耗时/错误率/慢接口 TOP10）
- 慢请求（>5s）持久化到 quality_logs
- 公开接口 `GET /api/monitor`

### 14.8 认证系统
- 纯标准库 JWT（HMAC-SHA256），零外部依赖
- users 表（username/password_hash/role），预留多用户
- 后端正中件强制鉴权，访客模式只读
- JWT SECRET 持久化到数据库（启动时自动生成/恢复）

### 14.8 代码质量
- 裸 except 全部处理：ALTER TABLE 精确捕获 OperationalError，业务路径带日志
- products.py 搜索 `|` 运算符 → `or_()` 方法
- Pydantic Schema 入参校验（9 个）
- 前端 localStorage 全部 try-catch（store 18 处 safeGet）

### 14.6 种子数据增强
- 12% SKU 全仓低库存（含自有仓，触发采购场景）
- 填充后立即构建日销快照
- seed 前检测已有数据（requires_reset 保护）
- 供应商 code 渠道后缀

---

## 十六、2026-08-22 关键改进记录


### 15.1 性能优化（PA 资源受限日 630s→36s）
- **batch_size 500→5000**：`_seed_orders` 每批 500→5000 条 commit，fsync 360 次→36 次
- **流式写入**：`_seed_orders` 边生成边 flush（5000/批），内存峰值 18 万→5000 条，防 OOM
- **快照 UPSERT 分批 5000/commit**：`build_daily_sales_snapshot` 16 万行单事务→33 小批，防单事务 commit 过慢/线程被杀
- **cache_size + temp_store**：seed 期间 PRAGMA cache_size=-64000 + temp_store=MEMORY
- 实测：生成订单 630.5s→36.0s，全流程约 1min21s

### 15.2 种子填充稳定性
- **并发保护 `_check_busy`**：seed/reset 提交时检测存活任务（25 分钟内有更新=活着），拒绝并发提交；卡死任务（超 25 分钟无更新）自动标记 error 放行新任务
- **get_tasks 卡死自愈**：running 超 30 分钟无更新自动标记 error
- **get_tasks 锁容错**：database is locked 时返回 `database_busy` 标记而非 500，前端继续轮询
- **reset 补全漏表**：`_do_reset` 表列表增加 `daily_sales_snapshot`/`daily_stats`/`inbound_records`/`outbound_records`

### 15.3 规则编辑页修复（3 个问题）
- **mode 列迁移**：线上 `rules` 表缺 `mode` 列（SQLite 静默忽略），`init_db` 加 `ALTER TABLE rules ADD COLUMN mode` 迁移
- **后端 CRUD 持久化**：`rules.py` 创建/更新规则 payload 加 `mode` 字段，`schemas.py` RuleCreate/RuleUpdate 加 `mode` 字段
- **其他渠道隐藏 BBCC**：补货模式选择器加 `filter(m => m.v !== 'bbcc' || globalChannel === 'jd')`
- **保存反馈**：`save()` 加 loading 状态 + toast 成功/失败提示 + 错误处理
- **缓存清除**：后端 `_rules_cache` 全部 CRUD 操作清缓存（create/update/delete/restore/permanent-delete），前端 `save` 后调 `clearCache()`
- **本地即时更新**：`save` 后直接 `setRules(prev => prev.map(...))` 更新 mode，不等 API 返回

### 15.4 启动加速
- **移除启动 `backup_db`**：后台线程 VACUUM INTO 在 GIL 下阻塞所有请求数分钟（health 30s+ timeout），scheduler 已有每日 02:00 备份，启动备份冗余
- PA reload 恢复 HTTP 200（不再 409 slow_startup）

### 15.5 前端体验优化
- **TaskPage 轮询 5s→3s**：步骤进度更及时
- **步骤进行中显示**：running 步骤显示"进行中"+spinner 文字，而非仅 spinner
- **删除死代码**：`SeedProgress.tsx`/`ExportProgress.tsx`（已迁移任务管理页）
- **TaskPage 错误友好化**：401 → "登录已失效"；库忙/网络异常 → "数据正在处理中/自动重试中"
- **TaskPage 移除 AbortController 15s 超时**：3s 轮询本身在重试，超时反导致 seed 运行时请求被掐断报"网络异常"

### 15.6 已知问题
- 规则保存后 `load()` 的冗余 API 调用可去掉（数据已写入后端），可简化为纯本地更新

### 15.9 生产环境操作底线红线（强制，2026-08-28 事故沉淀）
**一次 VACUUM + 误删依赖/备份 = 项目整日瘫痪（数据全丢）。**

| # | 红线 | 说明 |
|---|------|------|
| 1 | **禁止磁盘满时 VACUUM** | VACUUM 需双倍空间，磁盘满中断 → **db 清空（表全丢）**。VACUUM 前查 `page_count*page_size` vs 配额 |
| 2 | **禁止删除不确定用途文件**（`.whl`/依赖/数据） | APScheduler/python-multipart/pytz/six 是运行依赖——删了 webapp import 崩 |
| 3 | **禁止删除数据库备份** | 备份是最后防线，删了无法恢复 |
| 4 | **危险操作双确认** | VACUUM/DROP/清空：先查环境 + git commit + 备份 |
| 5 | **生产改动纪律** | 改前 commit；上传后验证 MD5；reload 后验证 health/接口 |
| 6 | **磁盘/依赖防护** | WAL checkpoint 每 6h + main.py 依赖自检 + 磁盘用量告警 |
| 7 | **PA 局限** | reload 409/超时可能实际成功（help 文档）——验证 health 而非返回码 |

### 15.8 数据优化原则（四维不可牺牲，2026-08-27 沉淀）

**性能优化的前提：数据实时性/准确性/完整性/可靠性四维不可牺牲**。为快缩范围/漏数据 = 违规。

| # | 原则 | 说明/教训 |
|---|------|----------|
| 1 | **计算范围不因快缩小** | 补货/滞销/stockRisk 必须算**全量相关 SKU**（products ∪ inventory 全集，含缺货/无库存的）。`skus` 过滤只能用覆盖全部相关方的集合——只算有库存的会漏"卖光最需补货"的 SKU |
| 2 | **等价重构** | 多查询合并单次扫描（`GROUP BY d,status,store` → Python 拆各维度）必须数学等价；改后测完整性（字段/条数/边界） |
| 3 | **缓存版本号校验** | 数据变更必须递增版本号（订单/库存/商品/清洗/配置/规则/采购订单全覆盖）；版本号读写 key 必须一致（曾写错 `_cache_version`/`_replen_version` → 缓存永不失效 15 分钟旧数据） |
| 4 | **增量修正替代全量重建** | 规则/删单等只影响看板部分字段 → 直接改缓存对应字段，替代 invalidate_dashboard 全量重建（14.6s→2.7s） |
| 5 | **强实时绕前端缓存** | 看板/数据变更页静默+进入刷新 `?t=Date.now()` 或 `clearCache(pattern)`——命中 30s 缓存拿旧数据 |
| 6 | **聚合接口字段含消费方所需** | stockOverview.items 缺 available_qty → 前端 filter 失败（缺货列表不显示） |
| 7 | **时区同侧** | `datetime.now(UTC)` 与 `strptime` 混用 TypeError 被吞 → 计算静默失败（days_zero 恒 999）。aware/naive 必须一致 |
| 8 | **deleted_at 判空** | `deleted_at IS NOT NULL` 匹配 `''`(active) — 条件须 `deleted_at != ''`（曾每天 04:30 删光订单） |
| 9 | **懒加载** | IntersectionObserver 仅状态变化回调（持续在视口不重复）— 用 onScroll 或表格增长移出视口（补货模式）；**不要 finally 重新 observe**（循环加载全部）；回调判断用 ref 门闩 |
| 10 | **PA 环境边界** | 慢磁盘/单 worker → 并发请求排队累加；分批 commit 卡死（一次性 DELETE）；reload 409（slow_startup）上传后须验证代码生效 |

### 15.7 大数据分页 + 滚动懒加载 + 显示交互（复用规范）

> 适用：进销存页（with-sales）、商品页（list_products）。大数据量（万级 SKU/记录）场景必须分页，禁止全量返回（几十 MB + 14s 计算 + 前端渲染卡死）。

#### 后端分页规范
1. **真分页**：先分页取主表当前页行 → **只计算当前页 SKU 的日销/周转/出入库**（关键：`load_daily_sales(skus=...)` 支持 SKU 过滤），禁止"全量算完再切片"
2. **返回结构统一**：`ok({"items": [...], "total": N, "page": p, "page_size": s})`；`page=0/page_size=0` 时保持全量返回（兼容非分页调用方）
3. **total 获取**：分页前 `count(*)` 查询（带同样过滤条件）
4. **缓存按页**：缓存 key 含 `p{page}` + 搜索词，版本号校验（`_replen_version`）不变
5. **搜索走后端**：`search` 参数后端 LIKE 过滤（`ilike sku/product_name`），前端搜索时重置第 1 页——**禁止前端只过滤已加载页**（大数据会漏匹配）
6. **批次/效期注入只当前页**：`WHERE sku IN (<当前页 SKU>)` 查询，不扫全表

#### 前端懒加载规范（对齐建议页）
1. **IntersectionObserver 底部哨兵**（非 onScroll 手算距离）：
```jsx
<div ref={function(el){
  if (el && !el._obs) {
    el._obs = new IntersectionObserver(function(entries){
      if (entries[0].isIntersecting && !loadingMore) loadXxx(page + 1)
    }, {rootMargin: '200px'})  // 提前 200px 预载
    el._obs.observe(el)
  }
}}><span className="btn btn-ghost">{loadingMore ? '加载中... ' : ''}({已加载}/{总数})</span></div>
```
2. **每次加载 100 条**，滚动到底自动 +1 页；竞态丢弃（`reqSeq.current` + `seq` 对比）
3. **条数/列显示**（页面标题或表头上方）：
```
已加载 {Math.min(loaded, total)}/{total} 条 · 显示 {visCols.length}/{COLS.length} 列{搜索 ? ` · "${关键词}"` : ''}
```
4. 全部加载完：`已加载全部 N 条`；搜索/渠道/主体切换时重置第 1 页
5. **api.get timeout 放宽**：大数据接口用 `{ timeout: 90000 }`（首次预热前可能慢）

#### 后端性能关键点
- `load_daily_sales(cutoff_days, db, sku_barcode_map=..., skus=[...])`：SKU 过滤下快照查询加 `AND sku IN (...)`，当天 orders 循环加 `if sku not in sku_set: continue`
- with-sales 缓存：300s TTL + `_replen_version` 校验（库存/订单/商品/清洗/seed 变更都会递增）

### 15.10 告警列表与健康卡（2026-08-28 治本修复）

**告警明细列表可见性由分组配额保证**，排序只决定组内顺序：
- `alerts.py` 按 low_stock / replenish / other 三组各取 limit 条（组内 id DESC）
- 勿用「limit*5 扩大窗口再 Python 排序」——补货告警 ≥ 窗口时低库存仍被挤空（8-28 18:35 报障教训）
- **计数一律独立 COUNT**（`alert_counts`），看板「(N 严重)」「还有 N 条」不得从截断列表 filter
- 缓存 key 含 limit + `_rules_version|_replen_version`

**规则引擎告警**（`_seed_rules` / `rebuild_rules`）：
- 去重 key 含 `channel+source`（对齐 `rules.py:_alert_dedup_key`）
- 关闭陈旧告警判据=该渠道该 SKU 无任何 inventory 行 avail<safety（与实时路径逐仓行判据一致，只关确实恢复的）
- rebuild_rules 递增三版本号：`_rules_version`+`_replen_version`+`_cache_version`

**库存健康卡**（京东主体）：
- **bc = platform(C仓) + platform_b(B仓) 总和**，不是单独 B 仓——SQL GROUP BY warehouse_type 后 Python 相加
- 自定义日期 summary 已 SQL 单次扫描聚合；trend GMV 只计已完成、订单数计全部；orders 必须按 channel 过滤

### 15.11 GMV 口径与健康卡/告警口径（2026-08-29 业务口径固化）

**GMV（业务铁律）**：
- 计入：待发货 / 已发货 / 已完成 / 申请退款（已支付=支付成功即计入）；**不含待确认(待付款)、空状态**
- 退款：计入总 GMV（支付流水），**净 GMV = 总 GMV − 退款金额**；漏斗(订单阶段分布)=全部状态（两卡不同业务口径，勿混）
- 金额：`total_amount − discount_amount + freight_amount + tax_amount`（平台补贴 subsidy 单独拆解，**实际回款 = 净GMV − subsidy**）；明细列由迁移 v18 提供，旧订单默认 0 平滑
- summary/periods/stores 必须带 `net_gmv / refund_amount / subsidy_amount / payout`；前端 GMV 卡 总/净/回款

**日销/补货/滞销**：快照构建、当天订单补充、删单增量修正**必须过滤已支付**（未付款算销量会高估补货/采购日销、误判滞销——曾每天把只有未付款单的 SKU 当有销售）

**告警仓库分布**：alerts 有 `warehouse_type`（迁移 v19）；规则引擎生成写**触发行**仓；存量回填与查询兜底用"最缺仓优先"(avail/safety 比值最小)；**勿用缺货 SKU 表 lookup 推算分布**（漏非缺货 SKU，曾 79% 低库存误算 C 仓）

**健康卡**（京东主体）：**bc = B+C 按 SKU 合计判断**（同一 SKU 在 B+C 可用/安全线先合计再判健康/偏低/缺货），own/平台/platform_b 维度保持行级；bc 缺货列表用合计缺货 SKU

### 15.12 补货模式口径 / 品牌GMV / 告警仓库维度（2026-08-30 固化）

**日销口径（补货模式二选一铁律）**：
- **BBCC 日销 = 全国 C 仓(warehouse_type='platform' 仓名)销量合计**——B 仓是调拨仓不产生零售、自有仓集货不计入；有空仓归属订单(快照归一'未知')才保守计入，own/B 仓销量绝不进 BBCC 需求
- **传统多仓日销 = 各 C 仓逐仓**，stockRisk C 仓维度用 SKU×仓 逐仓融合
- **渠道绝对隔离**：日销快照 + 当天订单补足(4处曾漏过滤) + 补货/采购全部 channel 过滤，jd/other 互不混入

**品牌 vs 店铺 GMV（多对多矩阵）**：店铺=store 归集所有订单(与品牌无关)；品牌=products.brand 跨店归集；dashboard_cache 返回 brands/period_brands(与 stores 同构含 net_gmv/payout)，前端店铺GMV卡 店铺|品牌 tab 切换。定位：查店铺 where store，查品牌 where 订单sku→products.brand join。

**告警仓库主体**：
- 滞销/补货告警生成即写 warehouse_type(该SKU库存最多仓=B/C/自有)；存量回填靠迁移 v22(v23)
- alertCounts 返回 ls_warehouse(低库存)/slow_warehouse(滞销)/rp_warehouse(补货) 三组按 alert_type 分离 + by_warehouse 汇总——前端分布按补货模式聚合展示(BBCC→BC合计+自有, 传统→C+自有)
- **禁从截断列表 filter 出计数/分布**（列表是配额样本，曾 1164 显示成 list 200 分布失真）——一律用后端 counts

**快照自愈（PA 环境）**：启动时 + health snapshot_stale + APScheduler IntervalTrigger 每小时 freshness job 三重保障；**禁 while-True 守护线程**——PA 上会导致 app 整体 500（2026-08-30 实测回退）；CronTrigger 在 PA 不可靠（快照曾停 41 天）。

### 15.13 seed 数据仓名生成教训（2026-08-30）
`random.choice(WH)[0]`（WH 为 (仓名,类型) 元组列表，[0] 取仓名）若改成 `random.choice([w for w,wt in WH if wt=='platform'])[0]`——新表达式结果已是仓名字符串，末尾 `[0]` 会误取**首字符**（"成都仓"→"成"），订单 warehouse 变单字、快照/库存仓名不匹配、BBCC 全国C仓日销全 0。**改随机选择语义时必须去掉旧下标，并用快照 warehouse 分布诊断核对数据形态**（曾致一键重置填充后看板 bcTotal=0 全链路断）。

### 15.14 看板缓存 / BBCC链路 / 导出 / 任务自愈（2026-09-01）
- **看板缓存命中判定**：`get_cached_dashboard` 必须记录缓存构建时的 `ver` 并与 DB 当前版本比对，**禁止把 DB 版本值直接当布尔**（>0 即恒真 → 每请求强制重建，summary 曾 12s+）。排查性能先测"第2次请求是否应命中缓存秒回"
- **BBCC 缺口链路**：C缺口 = 日销×lead − C可用 − c_transit（B→C调拨在途）；**不减 in_transit（供应商→C，传统口径）**；B缺口 = C缺口 − (B可用+供应商→B在途)；B建议补 = B缺口 + 调拨期消耗(日销×(自有-b时间+安全天数)) 箱规取整；C建议补显示剩余缺口不提前取整
- **日销趋势**：28天对比基准用 daily_sales_60（后端 load 60 天，7/14/28 窗口值不变）；前端趋势图标注意 `(x||0)>y` 必须加括号（JS `>` 优先级高于 `||`，否则非零恒真全显上涨）
- **导出**：exports.py 必须按类型分发（补货页曾因缺 replen 类型被错误指向采购导出）；订单导出去 LIMIT 2000 保全量；进销存导出补 c_transit 列
- **任务自愈**：get_task 与列表接口都加陈旧检测（running/pending 超15min无更新 → 自动标记 error）——PA 重启线程被杀后任务曾无限"进行中"
- **PC 端**：使用组件必须先 import（建议页 ErrorRetry 未导入致整页崩溃）；tsc 可查出未定义标识符

### 15.15 搜索后端化 / 闭包门闩 / 断货卡 / 配额治本（2026-09-02~03）
- **分页搜索必须后端化**：补货/采购/滞销接口加 `search` 参数(SKU/商品名/69码过滤), 前端搜索变更重拉第1页——曾纯前端过滤已加载前100条致 SKU 搜不到; 无分页页(订单/商品/进销存/供应商)后端已有 search 或全量加载无此问题
- **IntersectionObserver 闭包旧 state 陷阱**：`el._observer` 判断只创建一次, 回调捕获创建时旧值(如 page=1)→每次触底反复请求同页→列表重复填充(用户滚3100条还在途0假象)。修复统一用 `xxxRef.current` 实时读最新值(函数式 `setX(prev=>prev+1)` 亦可)。排查过 InventoryPage/ProductPage/InsightsPage 三处
- **断货卡模式化**：传统= C+own 混合显示(不 split tab, 标签用真实仓名 x.warehouse), bbcc= BC 维度; 标题传统不带后缀; 弹窗与卡同步; 布局用 aspectRatio:1 正方形防拉长
- **WAL 配额事故**：PA 512MB 配额 + SQLite 高频写 → WAL 膨胀 → 写失败 → malformed(3次)。治本: health 每次自动 `wal_checkpoint(TRUNCATE)` 但**按需**(仅 WAL>15MB, 小WAL跳过零阻塞)+ threading.Lock 防并发; scheduler 15min; db 损坏自愈钩子必须在 `init_db()` 前(曾 init_db 连损坏库即崩全500)
- **PA reload 409**：PA 免费版 reload 返回 409/slow_startup 但实际已软重载——CI 部署只认 2xx 即可, 以 health 兜底验证(曾连续4commit误标失败)

### 15.16 seed 数据合规基线（2026-09-03）
- **种子数据必须全虚构**：品牌池(禾味/山泉/净洁等)/供应商(云味食品(演示)等)/联系人(王小明等)/电话(010-8000000x)/店铺名(自营旗舰店等)均不与真实商家关联——曾含真实品牌(海天/太太乐等)与真实公司名，存在数据纠纷风险
- 渠道名"京东/天猫"保留（系统主体渠道标签，非第三方商家）；B仓名中性化(京东B仓→B仓)
- 改 seed 后必须 reset+fill 生效；验证方法：products 全量扫描真实品牌名集合应 0 残留
- 前端展示的品牌/供应商/店铺均来自 seed，虚拟化后全链路无真实商家信息

### 15.17 生产环境定位 + DB 事故防绕圈铁律（2026-09-04）
- **项目定位**：生产环境（最后阶段测试，seed 占位）。切真实数据前必须执行 docs/PRODUCTION_CHECKLIST.md
- **本次事故链**：seed.py f-string 嵌套引号（3.12 合法/PA 3.11 SyntaxError）→ app 全 500 → 误删库 → JWT 失效 → init_db 静默 0 表
- **防绕圈铁律（知识沉淀）**：
  1. **语法错 > DB 损坏**：先查 `wsgi_probe` 确认 import 是否成功；SyntaxError 是 500 第一嫌疑
  2. **本地 3.12 会漏检 3.11**：部署前 `python3.11 -m py_compile` 全项目（或 CI 门禁）
  3. **勿删库**：PA 文件下载截断（196MB→12MB）是假象，勿据此判损坏；误删 = 数据风险
  4. **token 401 = JWT_SECRET 回退**：直接读 db `replenishment_config.jwt_secret` 签发，勿绕登录
  5. **自愈覆盖三态**：db 缺失 / db 过小 / db 损坏，都要走备份恢复
  6. **init_db 静默失败**：必须落盘日志，否则 0 表 + setup 500 无痕
  7. **快照判定容差**：快照到昨天即新鲜（今天订单实时补足），勿用当前日期对比
  8. **WAL 配额**：checkpoint 按需（>15MB）+ 互斥锁；删 WAL 是最后手段

### 15.18 异地备份 + 语法门禁 + GMV环比 + 碎片监控（2026-09-04）
- **异地备份**：PA 本地备份（凌晨2点 .gz 已含自校验）→ GitHub Actions 每日拉取上传 Release（保留7份）。**异地=GitHub与PA完全隔离的平台**；恢复演练步骤见 PRODUCTION_CHECKLIST §3.5
- **语法门禁**：deploy-backend.yml 在 Upload 前 `py_compile` 全项目，目标版本 = env `BACKEND_PY_VERSION`——**本地 3.12 会漏检 3.11 语法，门禁必须用目标版本**；迁移后端只改 env 一处，勿硬编码
- **GMV 环比**：periods 各维度含 prev_gmv/prev_orders（今日→昨日/本周→上周7天/本月→上月30天），前端标签随维度——忌"硬编码比较标签+用 trend 末两点当环比"
- **碎片监控**：健康检查加只读 freelist PRAGMA；`freelist>2000页` 提示手动 VACUUM——**勿做定时 VACUUM**（独占写锁+阻塞全请求，8-28 教训）
- **入口唯一**：WSGI 用 `app.main`（backend/app/main.py），根目录 main.py 是遗留冗余——迁移/备份时勿混淆

### 15.19 Makers 原生重构(方案B, 2026-09-05)——停 SQLite 适配, 数据层直写 TiDB 方言
**决策背景**: SQLite ORM + 方言适配器挂载 Makers 后, ORM 查询接口(products/orders/dashboard)秒级 500 函数崩溃——SQLite/TiDB 双层语义差异(占位符/日期类型/异步 DDL/序列化)+ Makers 环境坑(只读 FS/sys.path/root_path/env 快照)叠加, 每个接口都可能踩, 边际成本失控。**停适配, 按 Makers 官方规范原生重构**: 复用纯 Python 业务逻辑(三窗口日销/BBCC/口径), 数据层直写 TiDB 方言, 接口契约与前端零改动。

**Makers FastAPI 框架模式规范(实测)**
1. 路由**无 /api 前缀**(框架剥离 path 转发, root_path=/api)——旧 backend 的 /api/xxx 路由在 Makers 原生模式 404
2. 入口文件模块级行首 `app =`(构建器 /^app\s*=/m 检测)
3. 函数包运行时 **sys.path 只有函数根**——同目录模块(db.py/routes/*)必须 `sys.path.insert(0, os.path.dirname(__file__))`, 否则 ModuleNotFoundError(函数表现为 404/plain Not Found)
4. **只读文件系统**: TMPDIR/EXPORT_DIR 等模块级 mkdir 需可写探测回退 /tmp(否则 import 崩)
5. FastAPI 标量参数默认读 query——POST JSON body 需 `await request.json()` 手动解析
6. **TiDB DATE()/DictCursor 返回 datetime.date/datetime 对象**(非 str)——与字符串比较/拼接须 str(x)[:10]
7. 状态码 500 会被 Makers 转成"函数崩溃页"(HTML)——业务异常统一返回 200 + {ok:false, detail, tb}(@traced 装饰器)

**可复用资产**
- `cloud-functions/api/db.py`: 原生 pymysql DictCursor(query/one/execute/executemany/table CRUD, 反引号+%s)
- `cloud-functions/api/biz/sales.py`: 三窗口日销(3σ 剔除+近3天1.5倍加权+趋势权重表, 与旧版算法一致)
- `cloud-functions/api/local_test.py`: 21 项本地回归(mock db+TestClient)——**改代码先跑, Python 层 bug 部署前拦截**(TiDB 连接/CLI dev 在 iSH 均不可用)
- `scripts/gen_schema.py`: SQLite→TiDB DDL 转换器(索引并入 CREATE TABLE 根治异步 DDL 竞争)
- docs/MAKERS_API.md: pages-api 逆向参考(DescribePagesEncipherToken 签名机制等)
