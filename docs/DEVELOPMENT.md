# SupplyKit 开发规范
> **UI/UX 设计规范已独立**: 小/中/大卡布局·间距·密度·动效·安全区·导航·弹窗·列表·表单·响应式 → 见 `docs/UI_DESIGN.md`(2026-10-09 分离)——本文档不再新增 UI 规则描述, 历史 UI 相关记录保留供追溯。新 UI 改动按 UI_DESIGN.md 验收清单执行。


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
- **项目定位**：生产环境（最后阶段测试，seed 占位）。切真实数据前按 §15.18 上线防护执行（异地备份/语法门禁/误删防护/恢复演练）
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
- **异地备份**：PA 本地备份（凌晨2点 .gz 已含自校验）→ GitHub Actions 每日拉取上传 Release（保留7份）。**异地=GitHub与PA完全隔离的平台**；恢复演练：下载 Release 备份→gzip 校验→integrity_check→抽样核对 orders 总数→记录日期/耗时/结果
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

### 15.20 前端切 Makers + 同源免签机制(2026-09-05)
- **Makers 域名全站签名保护**: edgeone.cool 静态+函数均 401(大陆 IP), 报错 "Global MLC excluded 检查网络"; 浏览器无签名无法访问
- **同源免签(关键实测)**: 签名 URL 302 种 cookie(eo_token+eo_time, Max-Age=10800)后, **浏览器同源 fetch('/api/*') 直达函数 200**; curl 带 Origin/Referer 模拟仍 401——必须是真实浏览器会话(平台可能校验 Cookie 完整性/UA)
- **前端切换**: frontend/.env `VITE_API_BASE_URL=<Makers 域名>`(同源, 页面域名==API 域名); 用完整域名而非空串(空串 fallback 到默认 PA)
- **契约红线**: **LoginPage 用原生 fetch**(不走 api client 拦截器)→ 期待平铺 {ok, token}; 其他页面走 axios(ok(data) 解包)。改动后端返回结构时, 检查每个消费方是 fetch 直连还是 api client
- **大陆出口访问 PA 被墙**: browser_use/大陆 IP 请求 pythonanywhere 会"无法连接到服务器"——区分网络问题 vs 代码问题
- **生产公开访问**: 3h 签名会话只够开发验证; 公开访问需自定义域名(大陆 ICP 备案, 备案对象=域名, 后端变量不在备案范围)或确认海外区免签

### 15.21 生产公开访问 + 免费额度四维 + 逐页对齐(2026-09-06)
- **免备案成立**: 项目 Area=overseas(不含大陆)→ 自定义域名免 ICP; Area 创建时固定, 换区=新建项目(API 改被静默忽略)
- **免签公开**: 自定义域名(CNAME pages.dnsoe6.com)不受 eo_token 签名保护, 前端/API 全通; **EO 站点与 Makers 域名托管互斥**(DNS CNAME 二选一, EO 站点接管会空白页)
- **TiDB serverless 硬限制(实测)**: ①单条多值 INSERT ~500 行上限(2000/批只落500) ②单条大 DELETE 全表内存取消(8176)→ 分批 LIMIT 10000 ③db.execute 返回 rowcount 而非 lastrowid ④批量写入动态分批 500(10MB 单事务)
- **Makers 异步分步**: 单请求 120s 上限 → sync_tasks 任务表驱动, status 轮询续跑(每步≤90s); seed 18.7万订单 9 请求完成
- **分析缓存四维保障**: TTL 只是兜底, **写操作 invalidate_all() 中央失效**保证导入/规则/参数变更后下个请求即最新(不牺牲实时性); 缓存 key 含 channel/mode, 分页搜索缓存后处理
- **RU 数据驱动**: EXPLAIN ANALYZE 实测(summary 420 RU/次全扫, FORCE INDEX 反 716 更差——GROUP BY DATE 聚合本质需全扫, 不加索引提示); 月 RU ~916万(18%), 控制台 Diagnosis/Top RU 复核
- **cron 官方配置**: cloudFunctions 必须运行时分组(python.maxDuration) + mainlandRegions/overseasRegions; schedules 与旧顶层结构共存会丢函数路由; **cron 最小间隔一天(*/5 实测不支持且致路由丢失)**
- **前端批量状态**: 规则/商品/滞销共用 prodBatch/prodSelIds(联动一致); setChannel 与 navigateTo 统一复位(渠道/页面隔离); 删除文件须同步删 import(version.ts 教训: 删了文件留 import 致构建失败)
- **部署频率纪律**: Makers 免费版单日构建有限 → 攒批提交再部署, 禁止每小改即 push(9-06 约 55 次触及上限)

### 15.22 告警体系重构 + 濒临断货时间线判定 + 前端即时性(2026-09-07)
- **卡片定位铁律**: 同体系告警必须检查子集重叠(low_stock vs replenish 实测 100% 重叠 → 静态阈值告警退役, 改读接口与建议页同源); 计数与列表必须同口径(counts 按补货模式过滤, 标题数字与弹窗列表一致)
- **预警判定哲学**: 静态安全线比较 → **供应链时间线**(Adj-DOS ≤ 补货周期 L/T); 在途按 OTIF 打折(预警保守) vs 补货/采购全额(执行实际)是刻意设计; 已断(avail=0)纳入红灯最高级
- **逐仓粒度铁律**: 传统多仓的补货/断货/告警必须 **SKU×仓**(一个 SKU 一个仓库一行, 与该仓日销/可撑天数独立); 规则引擎去重 key 必须含 warehouse; DB 表加列用 index.py 启动自愈 ALTER(幂等, 免手动迁移)
- **逻辑粒度双轨**: BBCC bc 合计覆盖 B 仓 → 前端零消耗的冗余 B 维度/字段可移除; 但补货建议 BBCC 的 B 仓链路(b_gap/b_suggested)是核心绝不能动 —— 移除前必须 grep 确认前端消费点
- **前端即时性三要素**: ①后端写操作 invalidate_all(中央失效注册表) ②**前端事件驱动**(rules-changed/insights-refresh)派发到所有消费点 —— 参数保存/下单取消这类"看起来不用刷"的操作也必须派发(实测漏派 → 建议页/采购卡 stale) ③30s 静默刷新兜底无事件路径
- **深浅链接定位**: 目标行在分页深处(100 条/页)getElementById 找不到 → 跳转 `setHammerSearch(sku)` 搜索定位(行必在当前结果)+ 唯一 id(hl-sku-warehouse); 搜索词用 sessionStorage 标记(loc_search)离开页面即清, 防污染产品/供应商等共享搜索页面; 定位失败给 toast 兜底(不要静默)
- **前端坑(本轮新沉淀)**: ①props interface 定义必须同步组件函数解构(漏解构 → 'Can't find variable' ReferenceError, 线上实测才暴露) ②内联箭头函数体加语句必须块体 `() => { a(); b() }`, 不能 `() => a(); b()`(语法错误) ③接口默认参数返回数组兼容旧消费者, 分页用可选 page/page_size 返回 {items,total}
- **规则引擎演进规范**: 内置规则随业务迁移退役(seed 不生成 + index.py 启动幂等退役 + daily-rules 孤儿清理关存量; 用户自定义规则不受影响); 新增 POST /rules/evaluate 供'立即运行'(规则改动即时评估不等 cron); 规则/告警去重 key 与业务粒度同步(2026-09-07 起含 warehouse)
- **性能(看板响应)**: 小时级聚合查询(_hourly_accel)加 60s 内部缓存(当天订单 1 小时内基本不变), invalidate_all 同步失效; 前端首屏拆流(重接口 stock-risk 后置, summary+aux 先渲染不阻塞骨架); **日销 60 天窗口勿轻易截断**(用户确认趋势/环比依赖, 改前必须确认)

### 15.23 规则页融合看板计算 + 审查加固 + 模式跟随(2026-09-08)
- **规则=业务计算引擎配置**: 不新增 tab, 规则编辑页融合断货/健康计算参数(存 rules.params, 卡片同源读取); 内置断货/健康规则 = **参数载体**(alert_enabled=0 不告警); 告警开关即时联动(关=清存量/开=评估生成, 四维闭环)
- **类型契约铁律**: 前端表单字符串 vs 引擎数字比较是**隐式契约**, 必须统一 str() 处理 —— 本轮 3 个 bug(alert_enabled '0' / params '3' / 空规则 return_hits) 全因此类; 前后端各写一半无人校验 = 必踩坑
- **规则 mode 语义**: mode 不再按 ctx 过滤(否则 ctx 无 mode 时指定规则永不评估), 改用于 params.lit fallback 参数选择(bbcc/trad 双线补货周期); 新建规则 mode 默认跟随当前补货模式, 保存防丢保险(f.mode 空用 editing.mode)
- **卡片 vs 规则告警重叠判定**: 规则告警若与系统级卡片同语义(断货/健康), 必须先评估数字/粒度/频率重叠 —— 重叠则退役告警保留参数载体, 避免两套数字困惑(同 replenish/滞销教训)
- **模式跟随全页清单**: 断货/低库存/健康/采购&补货/待处理卡 ✓; 进销存 B 仓维度/健康卡 healthTab 需按模式归一(platform_b 仅 jd+bbcc); 规则 mode/lit 双线
- **性能**: evaluate_many params 预解析(循环外一次) + 按事件检测计算变量 + 规则缓存复用 → 121s→3.2s; 参数载体规则 _is_alert_rule 过滤 → 快速路径
- **前端坑(新增)**: ①组件顶部 useEffect 引用后声明 const → TDZ(改用已解构 store 值) ②ErrorBoundary 加'回到看板'(完整重拉自愈) ③CSS 变量'利用收敛'优于删除(内联样式统一到变量/全局类)
- **实时性**: aux TTL 300→60s; health 版本指纹补 rules/suppliers/config(外部改库也触发前端 30s 轮询); 30s 静默刷新兜底无事件路径

### 15.24 晚间稳定性攻坚 + PA 清理 + 静态分析治本(2026-09-08)
- **静态分析铁律**: try/except 吞异常 = 功能"从未生效"的温床 —— 定期 pyflakes 全量扫描; 本轮 2 个真 bug(otif_min 定义在 otif_map 后 → NameError 被吞 → **OTIF 置信恒 1.0**; cleansing defaultdict 导入名错 → **库存联动从未生效**) 均为 try 块内 NameError 静默; 未使用 import/变量 + 前端孤儿文件同步清理
- **空白页三根因**: ①useToast 在 Provider 外 null(no-op 不崩) ②渲染层引用 useMemo 局部变量(ReferenceError) ③组件构建期缺实现(ToastAutoClear) → 构建失败。**任何 React 重构上线前必须本地 build + 线上冒烟**, 空白页=最高优先级
- **维护层(空态防线)**: index.html 内嵌'系统维护中'占位 + main.tsx 渲染异常兜底; 文案须与'加载中'区分(防误报); 改动若疑似致构建失败, 先回退恢复功能性修复再重做
- **任务轮询模式**: 统一静默刷新(clearCache+loadAll, 不整页 reload 消除闪烁) + visibilityState hidden 暂停轮询(回前台立即补查) + localStorage 标记驱动(任务提交自动感知)
- **code-split 教训**: 页面 lazy + React 18 UMD 构建环境未验证 → 一直加载中(React 未挂载) → 回退整页打包; **平台构建产物差异(UMD/CDN)下路由级 code-split 需先验证**
- **UI 冗余判定**: 显式刷新按钮在"写→invalidate_all→事件重拉秒级"链路下冗余(移除); 功能修复优先于新 UI 元素
- **跳转定位规范**: 一次性定位语义(loc_search/highlight)必须离开页面清理, 防污染共享搜索/残留兜底提示; 聚合维度跳转(断货 bc 行→C 仓多仓行)按 SKU 高亮全部行

### 15.25 前端质量门禁(TS 原生 lint) + 构建失败通知 + 密钥审计 + 深色适配(2026-09-09)
- **ESLint 重构为 TS 原生**(项目名 supplychain-v1-frontend → **supplykit-frontend**): 旧配置无 TS 解析器/React hooks 规则(PA 遗留); 现为 @typescript-eslint/parser + recommended + react-hooks(rules-of-hooks=error, exhaustive-deps=off 人工 review) + unused-imports(未用 import=error 自动删) + no-var/error + no-empty(allowEmptyCatch)/error + prefer-const/warn(CI --fix 自动) + console/debugger/warn; **unused 变量分层**: import 强制, 变量冗余量大治理风险>收益 → 关闭, 未来 strict 化由 tsc noUnusedLocals 接管
- **存量清理成果**: 162 errors(no-var 97/no-empty 62/hooks 3)+151 warnings → **0/0 全绿**(37 文件); no-empty 62 中主流是空 catch(allowEmptyCatch 语义化); var→let→const 脚本化; 未用 import 手动+脚本删; **3 处 rules-of-hooks 真 bug**(回调内 useToast()→组件顶部 toast 变量)
- **iSH 内存铁律(重要)**: iSH 沙箱内存不足 → eslint 全量扫描**不可靠**(std::bad_alloc 崩溃 或 **静默假通过**: 输出 0 问题 exit 0 但实际 160 err)——必须以 --format json 抽查戳穿; 对策: ①分目录/逐文件跑(单目录内存可控) ②**权威门禁放 CI(GitHub Actions)**, 本地 preflight 注明以 CI 为准 ③tsc --noEmit 做安全回归(删改后验证, 稳定快速)
- **构建失败通知链路**: ①scripts/preflight.sh 本地门禁(3.10 语法+pyflakes+local_test+lint+tsc) ②.github/workflows/preflight.yml push/PR 跑 lint/format/tsc + 后端回归, 失败自动 webhook 通知(GitHub Secrets: WEBHOOK_URL, 钉钉/企微 text) ③scripts/smoke_check.py 部署后冒烟(health JSON=函数路由存活/前端 #root/可选 token 业务接口, 失败 webhook)
- **密钥/配置审计**(scripts/audit_secrets.py): 硬编码密钥模式扫描(AWS/私钥/API key/password/token 等, 排除 node_modules/vendor/dist) + .env 跟踪审计 + .gitignore 覆盖检查 + 后端 os.environ 清单(部署必需 env 一览); **实测修复: frontend/.env 曾被 git 跟踪(含 Sentry DSN 等)→ git rm --cached + .gitignore 加固(.env/.env.local/dist/export_files)**
- **深色模式适配补漏**: styles.css 变量体系完善但 React 挂载前的静态占位硬编码浅色 → index.html #app-fallback 与 main.tsx 维护层改用 var(--bg/--text/--muted) + meta theme-color 加 dark media; 骨架屏 .skeleton 已有 dark 覆盖(#2C2C2E)

### 15.26 参数保存回退治本 + seed TiDB 首通 + 缓存/inflight 机制(2026-09-09)
- **自动脚本改代码铁律**: 脚本化重构(var→let→const/删未用变量)必须**浏览器级验证**——本轮 remove_var 把 `const [x,setX]=useState` 错改成 `const [[,setX]]=useState`(多套一层括号), 语法合法且 tsc/lint 全过, 但运行时对 useState 返回的 number 解构迭代 → 全站渲染崩溃("number is not iterable", 登录页都进不去)。**事故恢复链路**: 批量还原→推送→线上冒烟→浏览器确认
- **参数保存回退根因链(代码排查方法论)**: ①存储层: 生产库并存无前缀旧键与 mode 前缀新键(seed 旧版遗留) → 前端加载必须 mode 前缀优先+无前缀兜底(loadCfg 与 loadAll 同源解析) ②前端 state: loadSeasons 不得读本地 cfg 旧 JSON(保存后可能旧) → 总是 GET ③滞销参数页面重进丢(loadAll 不解析 slow_cats 等) → 补解析+tab 进入加载 ④**终极真凶 client.ts inflight 挂起**: 在途去重 promise 挂起(Makers 函数慢/冷启动, axios 30s 超时未到不清理) → 同 URL 后续请求全部复用挂起 promise → await 永不 resolve → setCfg 不执行 → UI 旧值; **重进页面=模块重建 inflight 清空 → 恢复**(与"退出重进才看到"吻合)。**排查证据闭环**: XHR hook 无请求 + console 无 [API] 日志 + 后端数据一直正确 → 定位为"请求层未发出/挂起"
- **修复双保险**: 低频参数加载 `&t=Date.now()` 新 key 绕过挂起 inflight(参数页即时性) + inflight 超 INFLIGHT_TTL(15s) 删除重发(治本, 防所有接口同病)
- **缓存分层原则**: 30s 缓存服务高频读接口(看板 RU 优化)保留; 写操作 invalidateCache 全清(秒级一致); 低频小数据接口完全绕缓存; inflight 是并发去重非缓存, 必须带挂起兜底
- **活动系数默认兜底模式**: 空库返回内置默认(enabled=false 默认关, 开启才纳入计算), 有自定义存储则以自定义为权威(自定义优先); 计算链路(_season_factor/采购建议)按 enabled 过滤
- **seed 演练价值**: 未实测的生成链路必然有坑 —— 空库填充首次 TiDB 跑通即暴露 only_full_group_by(非聚合列 SELECT 需包聚合); 演练是上线前必须项
- **无痕模式排查要点**: 无痕只清 HTTP 缓存, 不影响 axios 内存态(inflight/cache Map) —— 无痕下复现的"缓存类"问题优先查应用内存层而非浏览器缓存

### 15.27 Makers 多实例架构铁律 + 清洗导入 bug 链 + 销量池口径 + 批次效期(2026-09-09 下午-晚间)
- **Makers cloud-functions 请求模式铁律**: 无状态/每请求环境/多实例 → **模块级内存缓存命中率≈0**(实测 replen 3 连发全量重算 4s×3) + invalidate_all 只清当前实例(跨实例旧值)。**治本: 聚合缓存落 TiDB 共享表**(analysis_cache, 跨实例一致; DELETE 全表=全局失效; key 加口径版本前缀 c1 防部署跨实例存活命中旧口径; 异常自愈(删坏 key/建表/记日志)再降级)。诊断方法论: XHR hook 零请求 + console 零 [API] 日志 + 后端数据正确 = 请求层挂起/未发出
- **清洗导入沉睡 bug 链(实测必测铁律)**: ①SKU 查询 `% (channel, _ph)` Python 预格式化把 channel 值化 → pymysql 参数多 1 → **任何订单导入规则评估静默失败(evaluated 恒 0)** ②inbound `_write_batch` 3 元组解包 2 个 → **inbound 导入从未成功** ③300 上限致大文件告警漏报 ④key 列 `k in row` 宽松校验致空记录入库 ⑤o_ctxs 缺 inv.available_qty(超卖判定失效) ⑥评估异常静默吞。**教训: 未线上实测过的导入链路必然有沉睡 bug, 每次改动后必须真实链路验证**
- **销量池口径(用户决策)**: 采购补货日均实销只认"已完成"(钱货两清且离仓); GMV 展示口径 4 状态保留; 待发货锁定/ERP 联动暂不做(数据源均为导入, 未来平台 API 接入后纳入计划); 导入时非发货状态计数前置提示(不计入销量池)
- **批次效期可选链路**: 由入库记录(inbound)负责, 前端映射字段可选(prod_date/exp_date), 未映射/留空不强制不同步不误删旧批次; 同步=覆盖式(SKU×仓 删旧插新, 文件即权威); 消费方: 进销存批次效期预警/临期处置建议
- **未来计划**: 平台 API 导入订单/库存源数据 → 待发货锁定扣减(在仓可售=实物-锁定) + 关联 ERP 类联动

### 15.28 清洗导入全链路 + 设计系统收敛 + 图表坑(2026-09-10)
- **解析链路两坑**: ①xlsx read_only 对非标准文件(无默认样式/维度信息) max_row/column 误判 1×1 → 只读表头1列0行; 小文件普通模式+取第一个有数据 sheet ②CSV Sniffer 对竖线/中文逗号失效 → 手动候选分隔符探测+列数一致性校验。**教训: 上传解析必须用真实用户文件实测**(48列xlsx 暴露)
- **JSX 结构改动铁律**: ①CSS 变量在 JSX style 对象必须字符串包裹(`fontSize:'var(--x)'`, 裸 var() 报错) ②fixed 弹窗若父容器有 backdrop-filter/transform 会被捕获(包含块) → **createPortal(body) 是弹窗标准做法** ③批量正则替换后必须 tsc 稳定双次(iSH 内存抖动 bad_alloc 假通过; 曾推送 JSX 未闭合+TDZ 两次线上崩溃)
- **设计系统收敛(hue 方法论)**: 语义 token 优先于内联 hex(30+处 #ef4444 等 → --danger/--warning/--success, 深色自动正确); **业务分级色跨页同源**(断货三级/健康/滞销/效期/告警 severity 红色恒=紧急); 图表多系列色数组保留不 token 化; token 档位补全(圆角 8/24、字号 9~18)后组件收敛
- **echarts 坑**: ①bar label position right 超出 grid 被 clip → grid right 恒定留白+大数格式化('x.x万单') ②value 轴默认 max 自动取整导致不同量级 bar 占比随机(数据比例相同但视觉不同) → **xAxis max 显式固定**(总订单×1.15) 两渠道 bar 长度恒定一致
- **圆角档位收敛红线**: 收敛不是全并入统一值——**业务/层级专属档要保留**(看板小卡 26 是专属档, 曾误并入 24 属过度收敛已恢复); token 删除前 grep 全库(tsx+css) 零引用才删; 卡片内 absolute 伪元素(::before 高光)圆角需与本体同步(小卡 overflow:hidden 裁剪)
- **架构取舍(动态自定义列)**: ext_json 数据链路(表补列/清洗写入保全自定义字段/接口返回/custom-columns 配置)保留为基础设施; 前端动态列 UI 因**低频 + 一次性补列更划算**(30分钟 vs 常驻功能 3-4 倍成本)而回退 — 新增列需求走一次性补列流程
- **交互约定**: 模版管理/状态映射等页面工具入口放锤子菜单并按**步骤+导入类型**条件显示(hammerCleansingStep 同步); 底部弹窗统一看板'还有N条'同款; 触达热区 minHeight32; 动效 --motion-fast .15s
- **异步任务续跑原子抢占(08-10 晚间)**: seed 双轮询并发续跑冲突 → 后端 `seed_fill_status` 原子抢占锁(updated_at 3s 时间窗, 并发请求跳过续跑) + 前端**轮询源只保留一个**(移除 Settings 轮询改 App 事件联动 seed-done/seed-error)。**设计规范: 任何可续跑的异步任务(seed/clean/export)必须 状态机+时间窗原子抢占, 前端轮询单源**
- **BusyBox grep --include 陷阱(08-10 晚间)**: iSH BusyBox grep 不支持 `--include` → 误判 token 零引用 → 删 `--radius-card:24px` 后 17 处直角。**删 token/符号前必须验证 grep 工具能力**: BusyBox 用 `find dir -name '*.ext' | xargs grep` 或 python 扫描; 与 iSH 内存 bad_alloc 假通过同属"工具不可信, 以双重验证为准"
- **echarts 重绘方案实测回退(08-10 晚间)**: ResizeObserver+双rAF+notMerge 引入新问题(周期切换不动) → 回退 setOption+window resize; 容器首屏 0 宽问题由 Chart 组件内 ResizeObserver 触发重绘解决。**教训: 图表重绘方案以真实场景实测为准, 失败及时回退留档, 勿叠加多套机制**
- **SQL 预格式化 vs 参数化(08-10 晚间 d108fa70 / 09-11 _log 同源)**: `_compute_accel` IN 组 `% 预格式化只给 1 参数 → 其余 %s 无值(not enough arguments, 每次调用降级直算=全量重算); 全库仅此一处, 但两天后 _log 又踩同类(TypeError 被吞)。**规则: SQL 一律 %s 参数化 + params 元组, 杜绝 Python % 预拼 SQL 字符串(IN 组用 join 占位)**

### 15.29 Makers schedules 定时任务 3 bug 全修复 + 调度探针(2026-09-11)
> 背景: 质量日志无 cron 记录曾被误判"平台调度从未触发"→ 平台控制台"日志分析"页发现 `scfRequestId + statusCode=405` 实锤**调度一直在触发**。3 个 bug 全部线上实证修复。

- **Bug1 平台调度用 GET 触发(405 根因)**: 官方文档 edgeone-json 写 method **默认 POST**, 但平台实际以 **GET** 发起(SFC 定时触发器默认 GET) → 8 个 `@router.post` cron 路由全 FastAPI 405 静默失败, 路由逻辑从未执行。修复: 全部改 `@router.api_route(methods=["GET","POST"])`(72a8c4d9)。**教训: Makers schedules 触发方法与文档不符, cron 路由必须双方法; 平台请求级观测只有控制台日志分析(保留 24h, 无 API)**
- **Bug2 `_log` 格式化 TypeError 吞日志**: `execute("...VALUES('cron','%s',%s,%s)" % level, ...)` —— 3 个 %s 单值格式化 → TypeError 被 `except Exception: pass` 吞 → **所有 cron 日志(除直接 execute 的 risk_summary)从未写入**。修复: 参数化 + 异常自记(453f30e1)。**教训: quality_logs 无记录 ≠ 调度未触发; except pass 会掩盖日志链路自身故障, 日志写入必须可自证**
- **Bug3 同名任务改 cron 疑似不重建**: schedule-ping-test `35 12`→`30 13` 后 13:30 未触发(12:35 旧注册仍工作); 换新名 schedule-ping-2(13:55) 当日即触发(1560012)。**改 cron 建议换新任务名**
- **验证链**: 12:35 平台 405(旧 URL, 控制台日志) → 13:32 手动 405 对照(旧部署 URL 无 GET) → 13:39/13:45 手动 GET 200(新部署, 模拟平台调度方式) → 13:50 应用层兜底 daily-rules 首跑(dash 完成日志) → **13:54 平台自动触发 schedule-ping-2 成功写日志(全链路闭环)**
- **调度连通性可观测方案**: 保留 `schedule-ping-2`(每日 13:55 北京 → /api/cron/ping)为**常驻探针**, 任何调度问题通过 quality_logs 最新 ping 记录 5 分钟定位; 平台侧仍无 schedules 查询/日志 API
- **双保险运行**: 7 个正式任务(archive 1:05/cleanup-logs 3:05/freshness 3:10/snapshot 3:35/daily-rules 4:05/recycle 4:35/push-alerts 8:30 北京)现已随双方法修复自动执行; 应用层兜底(_daily_maintenance 快照自愈 + _daily_rules_guard 后台线程评估, maintenance_log 抢占)与平台 cron 双跑, 幂等可接受
- **应用层兜底设计**: summary 请求时 `INSERT IGNORE maintenance_log(date+task)` 抢占(多实例安全) → `threading.Thread` 后台执行 run_daily_rules(不阻塞响应) → 失败 DELETE 标记下次重试; run_daily_rules 抽为同步函数供路由与应用层共用

### 15.30 规则即时重算 + 全项目体检 P0/P1/P2 + UI 设计语言统一(2026-09-11 下午-晚间)
- **规则变更即时重算(替代'改规则等每日评估'24h 时滞)**: `_schedule_rule_eval()` —— eval_pending 表(task 主键)单行 `INSERT ON DUPLICATE` 打脏标记(多实例安全) → 后台 `threading.Thread` `DELETE` 抢占(rowcount=1 才评估, 串行) → `run_daily_rules()`(与每日任务同源同口径含孤儿清理) → **尾追循环**(评估完再查有无新标记)合并密集保存不叠加并发。触发点: create/update/restore/permanent-delete/batch(delete 除外——_close_alerts_for_rules 已即时关告警)。告警从 24h 时滞 → ~40s(看板 30s 静默刷新呈现)
- **体检索引结论(db/diag SHOW INDEX 实证)**: orders 12 索引冗余(idx_orders_ordered_at≈复合前缀/idx_orders_sku≈sku_ordered_at 前缀→写放大); **alerts 缺 channel 复合(分组配额查询扫描大); rules 无查询索引**。修复: `_INDEXES` 机制加 alerts(channel,status,created_at)+rules(channel,is_active); 新增 `_DROP_INDEXES` 幂等删 idx_orders_sku。**新建表/索引必须走 _INDEXES 机制, 不手改线上**
- **静默吞异常纪律(try_err)**: `_log` TypeError 前科(被 except pass 吞两天)→ 制度化 `common.try_err(src, what, exc)`: except 分支统一自记 quality_logs('quiet_error',warning), 不阻断降级流程但留痕可查。核心路径(evaluate 内 7 处: 告警批量写失败/补货周期/规则参数/OTIF/日销聚合/小时加速/健康分/供应商映射 + dashboard season/accel 2 处)先替换
- **monitor 实化**: Makers 无状态环境无进程级 metrics → 以 quality_logs 为观测面(totals/today/slowest_paths 最近20/latest_errors 最近5), 去掉恒 0 占位误导
- **create_rule 真实 id**: `db.execute_id`(lastrowid) 新增——create 类接口返回真实自增 id(告别 id:0 占位), 前端无需重新 load 拿 id
- **P2 单测**: `_grade_risk` 从 _stock_risk 抽为模块级纯函数(等价重构, 闭包薄壳转发闭包变量)→ 7 边界用例(red/orange/yellow/不入选 × 边界, 回归 91→98)。**核心算法(分级边界/评估精度)持续抽纯函数补单测**
- **EXPLAIN 慢查询诊断(2026-09-11)**: summary 60 天聚合 `TableFullScan 187K 行`(12.7万订单, 60 天窗口覆盖几乎全部数据 → 索引选择性差属正常, 慢=聚合固有成本)→ **物化日级汇总表**(orders_day_agg: GMV/单数/退款按 date+channel+status+store 预聚合)为专项; evaluate inventory SQL 侧正常(1.7万行), 36s 大头是 **Python 密集**(逐 SKU×规则解析 + _health_index 每次重算 + 全量日销聚合)→ 缓存 health_index/规则预编译为专项
- **quality_logs 膨胀治理**: id 达 159 万级(写删循环), 行数曾 63——daily_maintenance 增加"COUNT>500 → 删到 300"(与 cron cleanup-logs 同逻辑幂等, maintenance_log 抢占保证日级一次, **不依赖 platform cron 双保险**)
- **UI 设计语言/弹窗模板/fixed 回归铁律**: 已迁移至 `docs/UI_DESIGN.md`(八·弹窗体系/通用规范——纯 opacity 动效、createPortal、安全区 env)

### 15.31 看板提速物化表 + 日志收口 + 公共件体系(2026-10-06)
- **物化日级汇总表(orders_day_agg)模式(10s→0.77s)**: 高成本聚合(60 天 19 万行 GROUP BY)预聚到日级表(date/channel/status/store 主键)——**读取量降 52 倍**(3591 行)。**关键设计**: ①构建幂等(ON DUPLICATE 累加, 与业务 SQL 同口径 `_status_cond`) ②初始化 M0 抢注(migration_log)一次性 + 每日近3天增量 + MAX(date) 落后重建兜底 ③**强实时**: agg 覆盖 <昨天, **昨天+今天实时直查 orders**(补录/回填当日立即反映——四维实时性边界消除) ④**三层降级**: 近2天直查失败→仅 agg / agg 空→直查 60 天 / agg 路径整体异常→直查(每层 try 独立, 不阻塞看板)。**消费方统一行归一**(dict/tuple 行 zip 转 dict)防行形态差异
- **pymysql `%` 转义铁律**: 参数化 SQL 内 `DATE_FORMAT('%%Y-%%m-%%d')` 必须 `%%`——`%` 被当格式化占位符会 "not enough arguments"(曾致迁移 INSERT IGNORE 抢注成功但 UPDATE 从未执行=假成功)。**SQL 一律 %s 参数化 + params 元组, 杜绝 Python % 预拼 SQL**
- **聚合接口强实时规范**: 当日数据的聚合展示(看板/漏斗)直接用**源表实时查**而非物化表——物化表天然滞后, 近 N 天窗口直查是强实时标准做法(直查窗口 = 日单量级可接受)
- **前端异常统一收口(前后端一表)**: `POST /api/logs/frontend` → quality_logs(source=frontend); 全局捕获(error/unhandledrejection/ErrorBoundary/axios 5xx) + **限频去重**(同 type+message 5 分钟 1 条内存 Map); scope 分级(user 业务层/dev 排查层 log_type 白名单)——质量日志页=用户层, 开发者页=dev 层
- **事件联动 ref 转发铁律**: window 事件监听用 **ref 转发 + 单次绑定**(useEffect 空依赖绑一次, handler 恒调 `xxRef.current`)——**依赖 selected/state 重绑会出现新旧双 handler 并存, 一事件触发两次互相抵消**(回收页全选/取消第二次点击无反应根因)
- **ListGroup 公共件**: 行规范已迁移至 `docs/UI_DESIGN.md`(七·五 列表行规范)
- **custom 日期持久化 + 参数一致**: 自定义周期 start/end 必须持久化 localStorage(刷新恢复)+ **所有 summary 请求统一带 custom 参数**(loadAll/主 load/重试/静默刷新——两套请求参数不一致→响应交替覆盖→视图跳变)
- **健康柱分段敏感度(折中定稿)**: `base = 5 + v/100*13`(绝对基础, 分数语义) + `span>5` 时叠加 `(v-min)/span*7`(相对补偿, 大波动肉眼可见)——5 分内如实/超 5 分显著; 柱色=绝对档位(绿/橙/红)——绝对比例(0.18px/分不可辨)与纯相对拉伸(恒定时 4px 矮条)两版弱点互补
- **订单保留期决策**: archive 90→365 天——容量实证(information_schema 85MB/5GB 上限 1.7%, 365 天约 330MB-1.35GB 最坏 27%); RU 控制(agg/快照维护窗口保持 90 天不随保留期延长); 超期归档 daily_stats(SKU 日级汇总兜底)

### 15.32 三条 SQL 防线 + 工程审计自动化(2026-10-07)
- **双包裹 SQL bug 机制(品牌 GMV 负数根因)**: `SUM(IF(o.order_status IN (%s), ...))` % `_status_cond()` —— `_status_cond()` 返回**完整** `col IN (...)` → 展开成 `IN (IN (...))`(布尔值列表) → TiDB 只匹配非支付状态(待确认+已退货) → GMV 用非支付金额 → 净额可为负。**铁律: `_status_cond()` 只能作 `IF(%s,)` 条件, 禁止嵌 `IN (%s)`**——test_audit 静态扫描防复发
- **缓存 key 维度完整性铁律**: key 必须包含**函数签名全部影响结果的参数**(purchase/repl 曾缺 days → days 参数变化命中同 key 旧窗口缓存, other 采购建议被空结果污染 0 条 300s)。test_audit 断言 6 个 key( dash_summary/aux/stock_risk/accel/purchase/repl )的 %s 数与维度参数一致
- **DATE_FORMAT `%%` 转义审计**: 参数化 SQL 的 DATE_FORMAT/STR_TO_DATE 格式串必须 `%%`(M3 TypeError 防复发)——test_audit D 段扫描全项目 + index.py 迁移 SQL 断言
- **工程审计自动化三层**: ①`test_audit.py`(独立审计: SQL 双包裹/缓存 key/DATE_FORMAT/except pass 基线) ②`local_test.py` 内嵌审计断言 + MOQ 单测(105 项, 部署前置) ③`smoke_test.py` 部署后线上验证(16 项: 品牌 PAID 口径±2%/关键接口/无净负/迁移生效)
- **排查教训**: 本地 diag 手写"等价 SQL"自证会误判(双包裹 bug 被"正确单层 IN"掩盖 4.5M)——**必须用代码真实拼接的 SQL 执行验证**; 同 key 缓存污染判定 = 换参数强制重算对比

### 15.33 except pass 治理 + try_err 完整规范(2026-10-07)
- **116 处 `except Exception: pass` → `try_err(模块, '静默降级', e)`**(common.try_err 统一落点, 写 quality_logs quiet_error 失败自吞)——覆盖 routes/*+index+core/rules+auth/cleansing/cron/seed_fill
- **try_err 规范**: ①details 缺省带 traceback(limit 3)定位隐藏异常 ②**同 source 10s 窗口限频+计数合并**(窗口首条写库留痕 + 窗口结束补写 `×N 次` 计数条——防风暴刷屏且不丢异常规模, 每实例独立) ③**自身 except 必须 pass**(写日志失败防递归——治理脚本曾误替换成 try_err 自调用)
- **治理脚本教训**: 先替换后检查 `"try_err" not in s` 恒 False → 13 文件缺 try_err import(运行时 NameError 隐患)——**替换类脚本必须先注入 import 再替换, 且逐文件核对 import 链**
- **治理暴露真实 bug(M3 TypeError)**: Python `%` 格式化把 `%%Y`→`%Y` 后, pymysql 参数化(`params or ()` 空元组也触发格式化)把 `%Y` 当占位符 → **迁移从未成功**——修复: f-string 拼表名 + DATE_FORMAT 保留 `%%` 交 pymysql 转义 + execute 传空列表 `[]` 触发转义
- **启动迁移执行日志**: M1/M2/M3 成功留痕 quality_logs(migration 类型含影响行数)——M3 曾静默失败 2 个月无痕; quality-logs 接口 scope=user 白名单不含 migration(查迁移日志需全量不带 scope)

### 15.34 日志文件列表化架构(iPhone 日志分析式, 2026-10-07)
- **log_archives 表(date+scope 主键)**: 云端 SCF 无文件系统 → **用表模拟文件系统**(markdown 全文 + count + updated_at); 每日维护归档昨天(user/dev/all) + 清理 90 天前(保留周期 3 个月: 单日 md≤30KB, 90天×2scope≈5MB); **懒生成**: 读归档无记录 → 实时聚合(_export_md) → 写归档 → 返回(任何日期点开即有文件, 明细清理后文件仍在)
- **接口**: /quality-logs/files(归档表 + 明细实时日期合并, archived 标记) / quality-logs/file(预览+懒生成归档) / export(下载, 重构抽 _export_md 共用)
- **前端 LogFileList 公共组件**: 按天平铺(日期/N条)/点击行 → 底部弹窗预览 md/系统分享 `navigator.share({files})`(桌面 fallback 下载); 文件行**无更新时间列**(归档写入时间如 10-06 显示 10-07 易误读——只日期+条数)
- **嵌套弹窗裁剪根因 + portal 铁律**: 预览 sheet(fixed bottom)渲染在弹窗(overflowY:auto 容器)内 → **被容器 overflow 裁剪(渲染但看不到/点不到)**——底部弹窗一律 `createPortal(document.body)`(与 15.30 fixed 铁律一致)
- **file_edit 大段替换教训**: 替换路由文件大段代码时**装饰器行易被吞**(list_quality_logs 的 @router 被吞 → 接口 404 → 前端 catch 静默显示"0 条"而非报错)——**排查先查接口原始返回(Not Found), 替换后核对 @router 数量**

### 15.35 CI/Preflight 配置要点(2026-10-07)
- **workflow YAML 有效性**: MSG 多行字符串后 4 行缩进 0(block 提前结束) + name 含 `Secrets: WEBHOOK_URL`(冒号+空格=YAML mapping) → **GitHub 端 job 从未创建**(run 触发但 jobs/check-runs/logs 全空, 提交记录无 CI 状态)——**YAML 用 pyyaml 全量解析验证 + GitHub API state=active 确认**
- **workflow 文件变更不触发自身**: 修改 .github/workflows/*.yml 的 push 不触发该 workflow——用**空 commit(git commit --allow-empty)**强制触发验证
- **package-lock.json 必须 git 跟踪**: .gitignore 排除 → CI npm ci EUSAGE(CI 无 lock)——依赖锁定标准实践
- **隔离测试方法**: 最简 workflow(单 job echo)绿 → 框架 OK 定位到 job 定义层; logs API 404 = run 无 job(非权限——token 完整 /user 200)
- **Preflight 结构(可靠版)**: frontend(npm ci + lint + prettier --check + tsc) + backend(pip -r requirements + local_test + test_audit) + notify(`if: failure()` 内置函数——避免复杂 if 表达式解析风险; 需加 needs 才感知前序)

### 15.36 补货日销三机制算法(2026-10-07 下半场, 补货日销全 0 根因)
- **根因**: calc_sales_multi 3σ 按全窗口(含大片零)统计 → 60 天窗口多销售日但 28 天子窗口仅 1-2 个销售日时 σ 被零拉大 → threshold≈15×mean → 真实销售日被判离群全剔 → 补货日销 0(采购 n<3 短路 / 看板 fused 兜底 → 三消费方分化)
- **calc_sales_multi 重写**: ①3σ 非零日统计(nz_mean/threshold, 0 日不拉大 σ) ②sparse 参数 plain/shrink(采购看板求稳 / 补货证据收缩 ×nnz/3) ③统一摊薄语义 ws/win
- **smooth_promo_spikes(削峰)**: 近窗口单日峰值>5×历史基线(28-60天)→截断 2×基线; 无历史基线(新品)不削
- **活性门控**: 近 7/14 有销售×1 / 仅 28 天历史×0.5 / 全零 0
- **铁律**: 两分支语义不混——traditional 保持标准公式(ds×lead+safety−avail−transit, 静态 safety_qty), 只加需求门控 `_trad_suggested` 纯函数(ds<0.1 → 0, 安全线缺口不触发无需求补货); bbcc 保持 c_gap=ds×lead−avail−c_transit 结构
- **safety_qty 来源(静态)**: seed random.randint(30,200) / 清洗导入映射列 / 默认 0; 看板 SS_dyn 动态但仅断货卡
- **传统备注 _trad_note 纯函数(b8f67d8b)**: 对齐 bbcc 语境——0日销→无销量积压/⚪无销量; 需求低+低于安全线→说明暂不补原因; 周转红线(补后/当前综转>90→🔴/接近→⚠️); 优先级: 🔴已濒临前置→趋势→需补量→综转→无销量
- **口径确认**: 日销=仅已完成(销量池, 待发货走扣减池); GMV=PAID 4 状态——两口径分离
- **0.0x 显示**: round 1 位显示 0.0 非 bug——稀疏→建议 0→不送是人为决策(箱规+物流)

### 15.37 生产库排查方法论(2026-10-07 沉淀)
- **/db/diag 是唯一稳的库查询路径**: admin/admin123(演示系统 admin) → POST /db/diag {sql} 任意 SELECT/SHOW/EXPLAIN(白名单, admin-only)——本地 TIDB env 存在但沙箱出站抖动(偶尔连通), 不可依赖
- **临时诊断端点模式**: admin-only + 输出管道内部量(by_sku/c_whs/daily_c/multi 长度+样例) → 定位断裂点 → 排查后移除(线上 404 确认)
- **判断部署生效**: 按 quality-logs 时间窗过滤(新旧错误并存是常态); 特征端点(移除的诊断 404 = 新版本已上线)
- **分页假象**: 订单"全是 7.13" = 按 id DESC 排序 + 最新日约 1650 单占满前几页(种子 09-10 运行, 日期实为 07-13..09-10)
- **SQL 参数化 IN 占位符**: 自生成 ph 直接字符串拼接安全(非用户数据), channel 等真实参数走 %s 参数化; `% ph` 裸串 = 1 参数对多占位符 TypeError

### 15.38 前端 tsc 门禁修复要点(2026-10-07, 563→0)
- **store 循环推断崩塌**: 裸函数引用 + create() 匿名 initializer → T 落 {} → 具名化 `create(initAppStore)`(ReturnType 推导)
- **CSSProperties 字面量拓宽**: const 对象属性拓宽为 string → `: React.CSSProperties` 上下文标注
- **ASI 坑**: 替换 `e.target.style`→`(e.target as HTMLElement).style` 后语句以 ( 开头 → 上一行被续调用("Type 'void' has no call signatures" 级联)——语句起始括号表达式必须 `;` 前缀
- **tsc 语法错误跳过语义检查**: parse error 时看似 3 个错, 修完才暴露 104 个真错误——验证必须等完整 exit
- **any 红线**: 精确 interface + [k:string]:unknown 索引签名 + String()/Number() 边界收敛(API 动态数据用 Record<string,unknown>, 不造假严格)

### 15.39 首屏性能优化方法论(2026-10-08)
- **瓶颈定位**: 重置后 agg 残留旧数据 → summary 陈旧检测降级直查(60 天 12 万订单 GROUP BY 慢); 5 接口并发重算竞争 TiDB RU; 单接口 8-9s
- **优化链**: ①agg 物化重建入口(fill 钩子+admin maintain rebuild_agg) ②digest 共享缓存(进程内 30s, 三消费方收敛) ③warmup 预热(长 TTL 缓存) ④前端 loadAll 拆批(轻先行/重后置) ⑤旧值策略(首次清空/刷新保留)
- **四维保障铁律**: 缓存等价断言(与直算一致)/30s 与看板同频(实时不劣化)/全量 SKU(完整)/幂等+try_err 降级(可靠); 拆批数据最终一致
- **坑**: analysis_cache.register 已废弃(表缓存版本号失效)——进程内缓存用短 TTL 与看板同频, 不挂 register 钩子; diag SQL 含 % 被参数化(用 LEFT/全表拉取避开); key 保留字需反引号

### 15.40 动态安全线口径统一(safety_qty = ds×周期, 2026-10-08)
- 排查: 补货/采购响应 safety_qty 曾只显示 effective_safety(安全天数部分) vs 备注预警用全周期 → 口径分裂
- 统一: safety_qty = ds×对应周期(传统 前置+安全 / bbcc C+B 全周期 / 采购 采购周期, 供应商级优先)——与备注"只够卖X天<周期Y天"同口径
- 备注缺口链准确: B建议补N件(C缺口X→B缺口Y)——b_gap = c_gap − B仓覆盖

### 15.41 错误文案统一(errText, 2026-10-08)
- 网络层失败(axios 无 response)→"网络波动"; 服务端/业务(有 response)→"加载失败"; ErrorRetry 与 store 共用; props 精确 interface(any 红线)

### 15.42 Makers+TiDB 大批量异步规范(2026-10-09)
- **纯后台线程不可靠**: Makers 请求后实例可能冻结(skill 无生命周期保障)——任务表接力(seed/导出/导入同模式: POST 建任务 → status 轮询续跑分步)
- **TiDB 单行 entry 6MB + EdgeOne 响应体 6MB 双限制**: 大文件/大响应须分片(每片 ≤5MB 保险); LONGTEXT 解决列容量但单行 entry 仍限
- **分片模式下载**: 服务端不合并(单行/响应双限)——前端逐片 fetch+Blob concat+正式名保存
- **联动最后一步**: 接力任务最后步做全量重清洗(库存联动+规则评估+汇总)——与原同步版逻辑抽 _finalize_import 整块保留
- **status/download result 双层**: result={result:{...}}——解析统一内层

### 15.43 批量操作模式(2026-10-09 订单)
- 后端 POST /{resource}/batch {action,ids} + invalidate_all; 前端行勾选(Set)+选中高亮+锤子菜单按钮(事件 dispatch); 单条操作合并走批量链路(软删/恢复/永久删保留)

### 15.44 前端错误收口链路(2026-10-09 修复——此前从未生效)
- 链路: ErrorBoundary componentDidCatch → reportError → POST /api/logs/frontend → quality_logs(source=frontend)
- **坑1**: /logs/frontend 必须入鉴权白名单(否则 401 静默, 前端错误永远进不了日志——"看日志定位"前提)
- **坑2**: middleware path 含 /api 前缀——白名单用 endswith 匹配(== 不中)
- **坑3**: ErrorBoundary 重试须强制 children 重挂载(retryKey key)——setState({err:null}) 同实例重渲染仍错
- **坑4(TS)**: zustand 解构必须在所有使用前(useEffect 依赖数组渲染时求值——const 声明在后的 TDZ "Cannot access X before initialization")

### 15.45 CI 预防机制(2026-10-09 沉淀——连续多日 CI 红的治本)
- **推送前门禁(husky pre-push)**: tsc + eslint + prettier 本地全过才允许推送(源头拦截, CI 只兜底)——脚本 .husky/pre-push, prepare=husky 激活
- **TS strict 联动清单(interface 改动的 4 处必须同步)**: ①interface 声明 ②state 初始化 ③getDerivedStateFromError/工厂返回对象 ④使用处——retryKey 曾漏 ②③(TS2741 连续多次)
- **本地提交前自检**: git status 确认改动全提交; tsc 后台跑完确认 0 再推(勿推时 tsc 未完成——时序坑)
- **local_test 覆盖缺口**: agg 路径(orders_day_agg 有数据时)未覆盖——dashboard mode 类签名错误线上才暴露——新增场景补断言

### 15.46 数据追平基准 + 快照分批 + 导入写入(2026-10-10 沉淀)
- **物化落后检测基准 = 数据最新日(orders MAX)**——非"今天"：演示数据/休息日无新数据时不误判重建(10s 偶发根治); 真实导入→orders MAX 前移→即时追平; 通用场景
- **快照分段重建**: 每 miss 续段 10天/段(幂等 ON DUPLICATE)——12.6万行单次 INSERT 8-10s 拆分 ~1s/段; 段互斥等价
- **追平独立于 daily 维护条件**: 保持每 miss 无条件执行(放 builder 内非 _daily_maintenance——被 daily 抢占拦截教训)
- **TiDB 写入性能**: 大批事务(持锁慢)/小批事务(往返多 366s 最差)/**无事务 executemany 单语句原子**(TiDB INSERT 多值单语句原子=无部分写入)——1000/批最优; 半写入防护由单语句原子性承担

### 15.47 模块化红线：新增/改动以"后续维护方便"为主（2026-10-11 强制）
- **背景**：dashboard.py 1162 行、RulesPage 2971 行等大文件——今日拆维护/追平模块返工（一个个拆一个个测回归——费时费力）。**根因 = 此前新增逻辑堆积大文件**。
- **红线适用：项目全部文件**（后端所有 routes/*、core/*、biz/* + 前端所有 pages/*、components/*、store/*——不是仅 dashboard）
- **红线（新增功能/逻辑调整时）**：
  1. **先定位归属模块**（不是"加在最近处"）：按业务域放——维护/追平→`routes/dashboard_maintain.py`、stock-risk→拟独立模块、summary/aux→dashboard 主路由、规则引擎→`core/rules.py`、日销→`biz/sales.py`、补货/采购→`replenishment.py`/`purchase.py`、导入→`cleansing.py`+`cleanse_write.py`(拟)、列表/表格公共→`components/`、store 逻辑→`useAppStore.ts` 或拆分 store——**大文件（>800 行）新增逻辑优先独立模块/子函数文件**
  2. **子组件/子函数优先**：前端大页面（>1000 行）新增区块 → 拆子组件（`components/` 或页面内子组件文件）；后端路由函数膨胀 → 独立模块
  3. **函数粒度**：单函数 >150 行拆子函数（今日 TDZ/缩进类错误多来自长函数）
  4. **新增即放对位**：不积攒——新增逻辑当天放归属位置（避免文件膨胀后集中重构）
- **触发"必须拆"阈值**：后端路由文件 >1000 行 / 前端页面 >1500 行 / 单函数 >200 行——超阈值新增逻辑须独立文件
- **当前需按此管理的大文件**（新增大逻辑禁止再堆入）：
  - 前端：RulesPage(2971)/DashboardPage(2774)/InsightsPage(2079)/CleansingPage(1706)/InventoryPage(1036)
  - 后端：dashboard.py(1162——已拆维护组)/cleansing.py(745)/seed_fill.py(610)/core/rules.py(565)/purchase.py(535)/tasks.py(502)
  - 新增逻辑归属参考：看板区块→页面内子组件或新组件文件; 规则功能→core/rules.py 已有模块; 导入新类型→cleansing.py 已有分派; 通用工具→components/common/* 或 routes/common.py
- **拆分准则**（必须拆时）：①选风险最低独立组（无跨模块依赖优先）②re-export 保持外部调用不变 ③local_test 全量回归 150+ ④一次拆一步（勿打包拆）

### 15.48 拆分依赖随迁红线(2026-10-11 教训: health 移动漏带 _rule_params——NameError)
- 移动函数时**依赖必须随迁**：函数体引用的其他模块函数(如 _health_index 用 _rule_params)需 ①同迁 或 ②目标模块显式 import——移后 AST 扫描函数引用 + local_test 全量回归(150)验证
- 拆分后**先本地复现**(mock db 导入 + 调用主链)——勿直接部署(线上 NameError 才暴露)

### 15.49 token 认证头约定（2026-10-13 强约束——401 事故根治）
- **认证头统一用 `X-Auth-Token`**（非 Authorization）——Makers/边缘网关将 Authorization 改写为 q-sign（腾讯云签名）→ Bearer JWT 被拦截 → 全接口 401（login 放行不受影响——能登录但接口全拒）
- **禁止**：新增任何 `Authorization: Bearer` 发送/读取（后端中间件/路由自验/auth.py check+require_user/tasks_maintain/misc db_diag 已全部改 X-Auth-Token）
- **安全链**：TLS 传输 + 服务端 JWT 验签（HMAC-SHA256 + JWT_SECRET 必设——无 fallback 兜底）+ token 过期校验——X-Auth-Token 与 Bearer 同等级（且网关不接触 token——暴露面更小）
- 排查方法（先绕鉴权分层）：/debug 前缀放行——临时探测直调接口逻辑确认接口层 → 再定位鉴权/token 层（勿先抠 secret/签名）

### 15.50 调试端点统一收口(2026-10-13)
- **调试/探测端点统一进 `routes/debug.py`**（/debug/* 前缀——鉴权白名单放行）——禁止散落各路由
- **入口**：`GET /api/debug/list` 列出全部调试端点(路径/方法/说明)——排查先看这里
- 现有端点: jwt-check(verify 结果)/auth-sim(网关改写模拟)/jwt-fingerprint(实例 secret 一致性)
- 约定: 加调试端点带 @traced + docstring 说明用途; 排查完成保留(调试价值); 防敏感回显

### 15.51 调试口全清单（2026-10-13 统一收束——排查定位入口）
**路由级（/debug/* 统一收口到 debug.py——入口 /api/debug/list 列出）**
| 端点 | 用途 | 处置 |
|---|---|---|
| /debug/list | 调试端点清单 | 永久保留 |
| /debug/jwt-check | JWT 校验结果(401 排查) | 保留 |
| /debug/auth-sim | 网关改写 Authorization 模拟 | 保留 |
| /debug/jwt-fingerprint | 实例 JWT_SECRET 一致性 | 保留 |
| /db/diag | EXPLAIN/SELECT(admin 只读运维) | 保留(admin 专用) |

**日志级（quality_logs——排查保留）**
- perf_debug（summary/stock-risk 计时）/ slow_alert（>10s 慢请求告警）/ migration（启动迁移留痕）/ 快照续段

**前端级**
- console.debug（api/client 请求链路——排查接口用——保留 debug 级别不刷屏）
- window.__ 全局: __setPage/__page(导航/ErrorBoundary 功能) __seedMissCount/__cleanMissCount(填充/清洗计数调试) __hammerReplenMode(模式——功能)
- DevModePage(设置彩蛋——开发者页: 连接状态/环境/monitor/日志分析)

**约定**: 新增调试/临时代码统一登记(§15.50 路由集中) + 本清单补充; 生产禁敏感回显; 排查完成保留(有调试价值)

### 15.52 CI 0 容忍纪律(2026-10-13 沉淀——CI 红连续多轮)
- 本地 tsc 判 0 须确认进程完成(log 非空——空 log≠0); CI 为严格完整环境(完整 checkout/node_modules)——差异以 CI 为准
- 改后必跑: 前端 prettier --check 全绿 + tsc + eslint; 后端 local_test 150 + test_audit 11(《husky pre-push 已验)
- package.json 依赖变更须同步 package-lock(否则 npm ci 红); 全局 window 扩展写 global.d.ts(勿 (window as any))
- 审计修 bug 用精准(核对实际语义)——勿放宽/掩盖(startswith→in + 剥离前缀严格校验——非 n_ps>=宽容)
- **直接 fetch 纪律(2026-10-13 漏网教训——任务管理页报错)**: api client 拦截器只覆盖 api 实例——裸 fetch 必须手动 `'X-Auth-Token': token`(禁 Authorization)——新请求/页面统一走 api 实例或手动 X-Auth-Token; 上线前 grep 全项目 'Authorization' 确认 0 残留

### 15.53 订单状态映射与口径(2026-10-15)
- **两口径分离**：
  - **GMV**(PAID_STATUSES): 待发货/已发货/已完成/申请退款——已支付即计(宽)
  - **销量池**(SALES_STATUSES): 仅已完成——实销(窄)——补货/断货/日销分母
- **导入状态归一**(写入时): sale 档→已完成(销量池+GMV); blocked→内置归一标准(卖家已发货→已发货/等待出库→待发货/交易关闭→已取消 等——GMV 覆盖 PAID 含标准状态; 销量池只已完成分离); 未映射→保留原值
- **配置板**(映射界面锤子面板→表格列状态映射): column_value_map(渠道级——值→档位——覆盖内置默认; 兼容旧 order_status_map)——sale 档=入销量池(→已完成)/blocked=写订单不计销量池
- **订单写入 key**: order_no+sku——sku 必映射(前端校验拦截——缺失整批跳过——5000 单无明细教训)
- 表格实际值异于内置集 → 配置板补映射(值→sale/blocked)即可——订单写入由 sku 决定; 状态只影响销量池归属(blocked 也写订单)

### 15.54 任务推进机制(2026-10-15——挂后台结论)
- **Makers 免费版限制**: 实例请求后可冻结(后台线程不可靠) + 定时调度最小间隔 1 天——任务(导入/导出/seed)续跑依赖**轮询触发**(status 端点续跑一窗)
- **推进通道(可靠性排序)**:
  1. **页面停留轮询**(前端 setInterval——任务页/清洗页) — 可靠(前台续跑+卡片实时)
  2. **回前台 visibilitychange**(App 级——回前台调 continue-tasks) — 可靠(回前台即续跑)
  3. **请求驱动**(index 中间件——任意请求顺带续跑, 限频 60s) — ⚠️ 受限(限频+Makers 线程冻结/跨实例——实测不可靠)
  4. **每日 cron continue-tasks**(兜底——每天 05:05 续跑+清超时) — 兜底
- **真后台推进(不挂 app)**: 需**系统级定时**(iOS 快捷指令自动化——每 5 分钟请求 /api/cron/continue-tasks 或任意接口触发请求驱动)——Apple 快捷指令是唯一不依赖 app 进程的周期触发方式
- 大批量任务(5000 导入 ~2.3 分钟/121k 导出 ~13 窗)——页面停留或回前台等待完成; 离开页面 → 回前台继续(不丢——offset/steps 持久化)

### 15.55 iOS 快捷指令自动化——真后台任务推进(2026-10-15)
> 背景: Makers 免费版 schedule 最小间隔 1 天(构建器拒绝 */5——实证)——接力续跑由请求触发(schedule 限制不适用)——真后台高频推进需**系统级周期请求**。

**配置步骤(iOS 快捷指令自动化)**:
1. 快捷指令 App → 自动化 → 创建个人自动化
2. 触发条件: **时间** → 每 5 分钟(或可承受间隔——如 10 分钟/30 分钟——按任务量)
3. 添加操作: **获取 URL 内容**
   - URL: `https://supplykit.top/api/cron/continue-tasks?task=automation`
   - 方法: GET
   - 请求头: `X-Auth-Token: <admin token>`(用脚本化 token——或先手动登录取)
4. **运行前询问: 关闭**(后台静默执行)
5. 保存 → 自动化生效

**效果**: 系统级每 5 分钟触发 continue-tasks → 接力续跑所有 running 任务(导入/导出/seed——每窗盘活实例推进)——**不依赖 app 进程/页面驻留**——真后台持续推进。

**替代(更轻)**: 若不想暴露 token——自动化仅请求任意接口(如 /api/health)触发请求驱动续跑(限频 60s——5 分钟间隔足够)——但请求驱动线程在 Makers 不可靠(实测)——**推荐 continue-tasks 直调**(可靠)。

**注意**: token 安全——自动化内 token 存快捷指令(设备本地——不云端); 建议专用只读/低权 token 或定期轮换。

### 15.56 保活顺带续跑(2026-10-15——真后台推进最终方案)
- **快捷指令不适用**(iOS 只支持特定时间/小时——无每几分钟)——废弃
- **GitHub Actions cron 最小 5 分钟可做**——但**无条件每 5 分钟请求 = RU/调用配额浪费**(TiDB RU 有限/EdgeOne 配额)
- **最终方案**: UptimeRobot(已有 5 分钟保活 ping /api/health)→ **health 端点顺带检查** sync_tasks 有无 running(1 行轻查询——RU 极小)→ **有任务才调 continue_tasks**(续跑一窗——接力盘活)→ **无任务零开销**(仅保活本身)
- 效果: 有任务 → 每 5 分钟 UptimeRobot ping 触发续跑(真后台——不依赖 iOS/app); 无任务 → 零额外 RU

### 15.57 日志接口统一收束标准(2026-10-15——用户/开发者双维度)
**接口清单(调用方法)**
| 用途 | 接口 | 维度 |
|---|---|---|
| 质量日志(分页/筛选) | GET /api/quality-logs?page&page_size&scope&date&level | 用户(运营) |
| 日志导出(当日 Markdown) | GET /api/quality-logs/export?date&scope&level | 用户(运营) |
| 日志文件(按天) | GET /api/quality-logs/file?date&page | 开发者 |
| 日志文件列表 | GET /api/quality-logs/files | 开发者 |
| 前端异常上报 | POST /api/logs/frontend(log_type/message/details/level) | 前端统一收口 |
| 调试端点清单 | GET /api/debug/list | 开发者 |
| 慢请求告警 | quality_logs slow_request(>3s)/slow_alert(>10s) | 开发者 |

**日志类型白名单(scope 分组——§15.51)**
- **user(运营)**: risk_summary/cron/maint/cleansing/task/duplicate_order/duplicate_sku/format_error/field_warning/field_error/mapping_info/business_error/detect_debug
- **dev(开发者)**: api_error/slow_request/cache_error/quiet_error/frontend_error/window_error/unhandled_rejection/component_error/api_http_error/detect_debug/business_error

**收束标准(新增日志)**:
- 前端统一走 reportError(type, message, details, level)(client.ts)——5 分钟限频去重; 业务错误 ok:false(HTTP200) 记 business_error
- 后端统一 try_err(src, what, exc)——10s 同源限频; 慢请求中间件 slow_request; 422 handler 记 api_error(含 Content-Type/body 诊断)
- 新日志类型须登记白名单(scope 分组)——勿散落

### 15.58 except pass 红线(2026-10-15 强制)
- **禁止新增 `except: pass` / `except Exception: pass`**——静默吞异常=错误无痕(事故多源于此: 续跑 NameError/TypeError 被吞任务卡)
- 必须: except → try_err(src, what, exc) 留痕(至少 quiet_error); 已有 except pass 逐步清理(排查时发现即改)
- 例外: 前端 catch{} 静默(可选链防御——非错误路径)允许; 后端禁 pass

### 15.59 导入/评估分离 + 性能基线(2026-10-16)
- **两任务分离**: 导入(纯写加速——done) 自动创建 cleansing_eval 任务(sku_list 驱动分窗接力 1000/请求——每请求<90s——前端轮询/保活续跑)——根治 done 步长请求被 Makers 限时杀(233s 崩溃历史)
- **计数器防伪**: 窗口写与 done 步同请求时——防并发锁必须 > 窗时长(否则重试重跑窗→唯一索引冲突→failed 覆盖)——finalizing 标记 + done 步事务化 + finalizing 超时恢复(5min——进程死连接回滚)
- **写入性能(TiDB 官方依据)**: 每索引=1 KV 写放大——orders 11→9 索引/告警预载走复合索引(status,source); 批大小 1000(官方单语句多值——seed 注释 500 为经验)
- **评估/联动快**(对照: evaluate_many 非事务 4-10s; inv_map 200→500/片; 联动批量)——事务包裹评估是慢源(写放大)
- **经验**: 先读逻辑再动手(计数器 bug=没读锁代码加日志跑圈 N 轮); TiDB 行为查官方文档不猜

### 15.60 GMV 自然口径 + 回款矩阵(2026-10-16——v1.0 方案落地)
- **窗口口径**(仅 GMV 及联动——日销/补货/采购保持滚动): week=上周完整自然周/isoweekday 边界; month=上月完整自然月(day=1 推导月末)——GMV+trend+brands/stores/funnel 联动统一终点——边界用本地脚本验证(10-16 实证 10-05~11/09-01~30)
- **回款矩阵**(按到账时间归属): 仅月度/完整月可用——today/week/custom 前端弱化禁点+hover 提示; month 全亮
- **回款数据源**: gmv_flows 表(platform+flow_no 去重——表B 资金流水——清洗页「GMV 回款」类型导入); dashboard payout=真实流水优先(无数据→None+pending 前端"待接入资金流水"——算不准宁可不给)
- **日志收口**: perf_debug/slow_alert 入 dev 白名单(直插日志); execute 响应含 target 字段(部署验证/诊断)

### 15.61 GMV 回款导入链路根治 + 看板联动自然口径收口(2026-10-16 下午——线上 5 次 failed=15 实证闭环)
- **BOM 双重化根因(用户 5 次 failed=15 真因)**: 上传 bytes 存 TEXT 列→pymysql 读回 str 含 U+FEFF→续跑 `c.encode('utf-8-sig')` 再叠 BOM→`_parse_table` 只剥一个 BOM→首列名 `\ufeff平台` 与映射键失配→platform 全空整批 failed(preview 正常即因此); **修复: 读回分支 `encode('utf-8')`(不追加 BOM)**——写入 utf-8-sig 字节已含 BOM, TEXT 列读回已带 U+FEFF
- **导入写入防崩四件套**: ①数字列强清洗(amount/settled_amount 去 ¥元逗号→float 非法归 0——1366 整批失败根治; arrived_time 7 格式归一) ②必填 key 缺失留痕(err_details→前端 failed_details 区块——曾只 failed++ 无因) ③列并集+缺失补默认(数字0/文本空——tuple(r[c]) KeyError 整批崩) ④重复提前检测泛化 order+gmv_flow(键 platform|flow_no; order 保留软删排除)+force_new 对称
- **gmv_flows 唯一键补 channel**: `uq_platform_flow(platform,flow_no)` → `uq_platform_flow_ch(channel,platform,flow_no)`——跨渠道同 platform+flow_no 互阻 1062; 幂等迁移(cleanse_write 写前 SHOW INDEX 检查 ALTER; index.py DDL 同步)
- **回款数据源隔离(全局主体)**: 表列 channel + 写入强制注入 + 读取 WHERE channel + 重复检测 channel + 唯一键 channel——全链路渠道隔离
- **看板联动自然口径收口(前端/后端)**: 文本 近7天/近30天→上周/上月(locale 同步); 环比标签 较上周→较上上周/较上月→较上上月(窗口=完整自然周/月, 基准=上上周/上上月——无歧义); periodDays 跟随后端实际天数(自然月 28-31 曾写死 30); 环比跟随维度(总/净/回款分取 prev_gmv/prev_net_gmv/prev_payout——后端 periods 补字段, 无数据不显示); 日均回款去推算兜底(有就有没有就没有); 店铺卡 month 回款按 shop 真值/品牌卡=null(流水无品牌维度)/前端 _g 去 fallback; trend payout 月度真实流水按日覆盖(折线/微条与主值同源, 无流水日=0 不打洞)
- **接口修复**: quality-logs/files scope 空/all 1064→不过滤降级(注意 % 转义层级: 不经 Python 预格式化的分支用单 %s, 经 `% _in` 的分支用 %%s); 补货无分页裸调 413→过滤后截断 2000; reset 清单补 gmv_flows
- **写放大教训(06674741→064504ac revert)**: invalidate_all 35 调用点(规则/订单/供应商/商品/采购/清洗/seed 每步)×每次全表 300 天 agg 重建 = RU 爆/并发线程——**固定 3 天原状; 历史缺口(9月差146万)线上手动 rebuild_agg(90) 修复, 后续导入历史订单按需手动 rebuild 或另出有界方案**

### 15.62 协同验证-修复闭环工作流(2026-10-16 下午沉淀——GMV 回款链全程实证)
- **验证闭环顺序**(用户给验证清单 → 行为验证 → 根因定位 → 修复 → CI → 部署 → 行为复验):
  1. 生产行为验证: 登录(demo 只读/admin 写操作)→ 逐接口实测(HTTP 码+返回结构)——不依赖部署 API(iSH 沙箱 pages-api 经常 HTTP 000 连不上, 记忆实证)
  2. 根因定位: diag SQL 直查(admin 专用 SHOW/SELECT——唯一稳的库查询路径)逐日对比(orders 直查 vs agg 表)→ 定位缺口范围; 日志留痕(try_err/窗口计数)找时间线
  3. 本地单测复现: 同一数据本地 `_parse_table/_clean_rows/_write_rows` 跑一遍, 对比线上行为差异(preview 正常 vs execute 失败 → 定位读源文件路径分支)
  4. 修复 → 语法门禁 3.10 + local_test 153 → push edgeone(用户确认)→ CI(前端 tsc/prettier/eslint 全绿)
  5. 部署确认: 行为验证(新代码特征字段/行为)——不依赖 health version(那是数据版本非代码); CI 报红用 GitHub API 拉 run/jobs 详情定位(prettier 格式类错误本地 `npx prettier --check` 先跑)
- **"有就有没有就没有"铁律**(用户口径): 回款/净维度无真实数据不拿推算兜底(日均/图柱/趋势——主值"待接入资金流水"、图柱 null、趋势按日真值无流水日=0)——宁缺毋滥不误导
- **自然口径无歧义**: 窗口=完整自然周/月(上周/上月), 环比基准=上上周/上上月——前端标签必须区分(较上上周/较上上月), 不许同名(较上周)制造歧义
- **写放大审查**: 改任何"全局失效/重建"逻辑前, 先数调用点(rg invalidate_all)——35 处写操作 × 全表重建 = RU 爆; 修复用有界窗口或手动触发
- **改动纪律**: 改前端/后端前先读相关完整代码(用户强制); 提交粒度=一改一 commit 语义完整; CI 报红立刻修(不得跳过)

### 15.63 新集群迁移 + 日销物化表 + 四维验证(2026-10-17——配额耗尽应对, 全链路实测闭环)
- **TiDB serverless 配额耗尽(1105)**: 会限流所有请求(登录假 401/health degraded/summary 偶发 13s)——**Starter 免费额度按集群独立, 非按项目共享**(实证: 新建 supplykit-secondary 后 health db=ok, 不受旧集群配额影响)
- **建集群 API(serverless.tidbapi.com/v1beta1)**: Digest 认证 `--digest -u $TIDB_PUBLIC_KEY:$TIDB_PRIVATE_KEY`(public=用户名/private=密码, 非 Basic; api.tidbcloud.com 全 404); POST /clusters body 需 displayName(≥6字符不能过短)+ region.name 完整形态("regions/alicloud-ap-southeast-1" — 别用 regionId 短格式)+ clusterType=DEVELOPER + rootPassword(创建时设——v1beta1 无改密 API, 只能控制台 Set Root Password); host=endpoints.public.host, user=userPrefix.root
- **iSH 沙箱 DNS 劫持(198.19.0.3 合成 IP)**: pages-api.cloud.tencent.com 解析失败/HTTP 000 → **DoH(1.1.1.1/dns-query)拿真实 IP(CNAME eo.dnse5.com → 43.159.104.94)→ curl --resolve 域名:443:IP 直连**——固化为标准(09-06 同法, 本次又踩)
- **Makers env 配置**: 原项目 `ModifyPagesProjectEnvs` 直接改(非新建项目); **env 是部署时快照, 必须重新部署才生效**(改 PORT 判别实证)
- **db.py 自动建库**: 连接 Unknown database(1049)时无库连接 CREATE DATABASE 后重连(迁移到空集群自动引导)
- **schema_create.py 全表 DDL**(按现有代码数据逻辑重建, 非 PA 旧版): 列=INSERT/SELECT 权威提取; **建表幂等 CREATE IF NOT EXISTS + 幂等补列 SHOW COLUMNS 差异→ALTER ADD**(曾漏 inventory.safety_days → 1054 实证——CREATE IF NOT EXISTS 不改已建表, 必须补列自愈)+ 唯一索引兜底(orders uq_orders_no_sku——seed 订单号已修全局唯一不取模)
- **日销三窗口物化表 sku_sales_stats**(根治补货冷算 22s): 只存三窗口底料(ds7/14/28/sigma + *_cut 削峰+shrink 组 + nnz/last_sale)——**不固化 fused**(采购 14/28 自权重≠看板 rolling_predict 口径冲突教训, 消费方现场算); 行维度显式枚举 __C__(全国platform)/__BC__(+platform_b)/__ALL__(全仓)/逐仓(无 any 哨兵); invalidate 重建 **5min 限频**(写放大控制——35 调用点×全量曾 RU 爆); 消费方=补货 cut 行 / digest(规则/看板/健康/低库存)WH_ALL / stock_risk 三组
- **看板 3 卡空根因**: daily_rules 守卫在 seed 后物化/快照未就绪时空评估 → maintenance_log 占位毒化 → 永不重跑 → 手动 rules/evaluate 补跑即恢复(384 告警); 修=_daily_rules_guard 加**数据就绪检测**(快照有行才占位, 未就绪下次重试); 新集群 seed 后需手动触发一次 rules/evaluate
- **四维验证方法(可复用)**: ①准确性=接口 vs 同口径 SQL 直查逐数对比(summary gmv 54611336.61 逐分一致; periods.orders=PAID 口径 36356 vs SQL 全状态 41807——口径差异非 bug) ②物化准确性=物化行 vs 快照直算(SKU ds28 19.554 vs 直除 19.036, 差异=3σ+加权合理) ③端到端=补货建议手工核对(ds×lead−avail; SKU red 因 adj_dos 0.54<lit3) ④完整性=关联无幽灵(订单/库存 SKU 全在 products 档案; 物化 970=快照 970) ⑤实时性=写操作→invalidate→物化重建(updated_at 05:12→05:27 实证)
- **性能(seed 2000 SKU/17.8万单冷算实测)**: 补货 22-30s→1.78s / summary 13.4s→0.88s / stock-risk 16.3s→2.5s / 进销存 15.7s→1.46s(物化+SQL 下推, 非缓存热)
- **前后端口径核对(全一致)**: 商品 100/页 SQL下推 / 订单 30/页 / 进销存懒加载 100/页 SQL LIMIT(total 独立 COUNT 稳定) / 补货滚动 100/页物化 / 滞销处置 page=1×100 一次拉 / 供应商全量 20 条
- **日志收口统一**: _SCOPE_TYPES 3 处漂移→模块级 _LOG_SCOPE_TYPES; 补 5 缺口(cache_invalidate/migration=dev, cleansing_eval/config_history/rule_action=user——此前任何维度看不到)
- **轻量性能验证三件套(零/极低 RU, 不动数据——2026-10-17)**: ①计算层=本地构造 5 万 SKU dict 跑核心函数(5.32s 冷算 vs 283ms 物化读——纯 Python 零 RU) ②TiDB 层=EXPLAIN ANALYZE 抽样 5 个代表性查询拿真实 RU/扫描行数——**发现 orders 分页 TopN 全表扫 428 RU/次**(17.8万行, 百万订单线性涨; 加 idx_orders_ch_id(channel,id) 后 4 RU/13ms——107 倍降, 索引直达不随行数涨) ③Makers 层=连续 5 请求实测冷启动(0.63-0.75s 稳定, 实例池复用无断层) —— 本地模拟测不出执行计划/RU 与无状态特性, 线上抽样个省 6 个量级
- **RU 预算要点**: 物化读 4.1 RU / summary 10.8 / 进销存 COUNT 10.5 / 快照聚合 78 / orders 分页 4(修复后)——Starter 约 5M RU/月, 高频轮询接口必须索引直达防线性涨
- **三慢点收敛(stock-risk/purchase/aux, 2026-10-17)**: ①stock_risk 缓存永不落库·真因——端点用 `_loads`/`json.dumps` 但 dashboard.py 未导入 json → 写回抛 NameError → except 静默吞 → 每次全量冷算 2.5-14s(summary/aux cache_get 正常对照定位); 修=显式导入+try_err 留痕, 命中 0.55-0.7s(Makers 函数基础 ~0.6s 近下限) ②stock_risk 物化短路(fe02b366): 物化有数据直读 WH_ALL/WH_C/逐仓现场 rolling_predict, 跳过直算×3 ③purchase 物化读 14/28(0ea443bf): 采购自权重 fused 读物化 ds14/ds28 现场算——三接口 3.7-14.6s/3.1s/3s → 0.55-0.7s/1.4-1.7s/0.9-2.3s
- **排查方法(可复用)**: "接口每次都慢且缓存疑似失效"先查端点是否缺 import(裸写 REPLACE 路径 vs cache_get 路径差异); 慢请求持续+summary 对照正常 → 缓存链路断裂而非 SQL; EXPLAIN ANALYZE 点查 0.59 RU/10ms 证明缓存读本身快
- **全接口 RU 体检(2026-10-17, 22 项 EXPLAIN ANALYZE)**: 修复 2 个 100+ RU 隐患——orders 分页 428→4(idx_orders_ch_id <channel,id>, ORDER BY id DESC 曾 TopN 全表扫)、批次效期摘要 121.6→2.1(idx_batches_ch_wt_sku <channel,warehouse_type,sku>, insights.py:102 进销存批次摘曾全扫 26782 行); 剩余: 快照 60 天 165(仅物化失效降级路径, 低危不优)、inventory 全行 51(补货/断货必拉, 可接受)——**全高频链路口径 ≤51 RU, 与数据量解耦(索引直达), 百万订单免费额度可承受**
- **⚠️ 密码安全红线(新教训)**: commit message 写密码已 push → amend+force push 清除; 密码/secret 永不进 git/对话可见处

### 15.64 导入链路全达标 + 双渠道四维 + 只读验证栈落地(2026-10-17 晚——7 类导入实测闭环)
- **采购页无数据根因(fc1e886d)**: purchase_calc 物化短路漏 s14m/s28m——第 175 行展示字段(daily_sales_14/28)引用直算变量, 物化命中时 UnboundLocalError → 采购接口崩 → 采购页/看板采购卡双空; 修=物化行现场补 s14m/s28m(与直算同构); 采购 140 条恢复
- **看板补货卡被连累根因(46deabea)**: loadProc 用 Promise.all([replenishment,purchase])——purchase 崩→整体 reject→补货卡也空; 修=**allSettled**(多数据源卡片必须 allSettled——单源崩不拖累另一卡)
- **角标 200 误导(46deabea)**: store 拉 /api/quality-logs 无 scope→全量 200 截断含 dev 排查日志; 修=scope=user(真实用户业务日志); 角标/计数按用户维度而非全量
- **质量页文件 count 快照落后(28d98ef3)**: 已归档日期曾优先取归档 count(归档后新增日志不显示——10-17 归档 04:59 count=4, 05:27 新增 4 条仍显示 4); 修=max(归档, 实时明细)=实际条数
- **导入同键多行聚合(179f4463, 用户定义原则)**: 新导入数据内部同组合键多行→**写入前 Python 分派式聚合**(不依赖 DB 唯一键, 曾撞 uq 撞 1062 整批失败实测 success2/failed1)——订单(order_no+sku+69码三键, 69码空落二键)/库存(sku+warehouse+69码)/出入库(sku+日期+69码)/回款(platform+flow_no)=数量金额求和一条; 商品/供应商=同键覆盖末行档案(upsert); **前端 dup-check 查库(三键)与文件内聚合键规则同源, 不冲突**(dup-check 已 dict.fromkeys 去重, new/overlap 按唯一键算)
- **评估联动 sku_list 漏窗真因(dc347d73)**: done 分支曾从 cleaned(最后窗)提取 SKU——分窗接力只含末窗(异步 1100 行 sku_list=100 实测, 前面窗 SKU 漏评估); 改从 cleaned_all(全量重清洗)提取——修复后 sku_list=1100 全量, rules_evaluated=inventory_adjusted=1100
- **7 类导入 × 同步/异步双分支全达标(线上实测)**: order/platform_inv/product/supplier/inbound/outbound/gmv_flow 同步(<1000 请求内)+ 异步(1100 行接力 done)→ 落库验证求和/覆盖正确; 评估联动自动建任务→分窗续跑→done(TaskCard 窗口进度/联动计数可视化 86e107a1)
- **测试数据清理法(只清测试不动 seed)**: order_no 前缀识别(SYNC-O/BIG-O/EVAL-FIX/O-DUP/TL-EVAL 等)→ diag 分页收 id→ /orders/batch 软删(500/批)→ invalidate→孤儿 oversell 告警自动转 inactive(实测 100 条测试触发的全关, 剩余 66 条全部有活跃订单支撑=真实告警); **清理后必验: 告警 active 100% 有订单关联(无孤儿残留), seed 行数不变**
- **双渠道(jd/other)四维全链路验证(2026-10-17 晚)**: ①准确性=补货(rolling cut)/采购(14/28 自重)/断货(rolling plain)三口径 × 双渠道 6+ SKU 逐 SKU 与物化底料现场算一致; 看板 GMV jd 54611336.61/other 26434882.98 vs orders_day_agg **逐分一致** ②完整性=物化 __ALL__/__C__/__BC__ = 快照(jd 970/other 1000)全覆盖 ③可靠性=双渠道缓存 6 组齐全(summary/aux/repl/purchase/stock_risk/digest/accel)+ 降级路径保留 ④实时性=双渠道当天订单(755/474)直查补足 + 物化 10:27/10:28 写后重建 ⑤16 项 GET 全 OK
- **只读验证栈三件套(落地)**: ①scripts/ru_diag.sh——EXPLAIN+GET 一键跑(只读零写, 全接口 RU/耗时表, 一次 <300 RU) ②坑沉淀 local_test(现在 155 项, 每次线上坑→本地用例: 同键聚合/漏窗/BOM/% 转义/缺列 等先本地拦) ③EXPLAIN 定位执行层+GET 定位行为层——**反复调不达标用只读栈, 一次循环 100-300 RU 替代写操作验证(省 2-3 个数量级)**
- **小批量冒烟验证流程(替代反复大批量导入)**: 导入/联动写链路验证用 **3-10 行小文件 × 同步/异步各一次**(同步=<1000 行请求内, 异步=构造 >1000 行触发接力)——3 行与 5000 行走同一套代码路径(解析/清洗/去重/写入/评估联动), RU 差 500 倍(3-10 行 ≈50 RU vs 5000 行 ≈2.5 万 RU); 验完即软删测试行; **每日部署后冒烟一次 ≈ 几百 RU, 替代"验证月 60M / 调试日上千"的旧模式**
- **前端验证缺口(待补)**: CI 只跑 lint/prettier/tsc(静态), vitest 配了 CI 没跑, 无组件/UI 测试——153 是后端 local_test, 前端功能/UI 调整缺回归(后续补 vitest CI + 组件测试)

### 15.65 看板计数口径铁律(2026-10-18——四维核对+待处理卡方案A)
- **看板各卡计数职责划分(可溯源, 不混淆)**: 待处理卡=无独立卡的待处理项(其他告警+滞销); 低库存卡=alerts.low_stock(规则引擎产物); 采购补货卡=实时接口(补货建议+采购建议, **不在 alerts 表**); 断货卡=stock-risk BC; 健康卡=health_index
- **三卡集合不同的等式关系是设计**: 补货建议=实时计算非告警, 所以"待处理告警 = 补货采购卡+低库存卡"数学上不成立——不是 bug
- **待处理卡方案A(edbff66b)**: 大数字=pendingTotal=otherTotal+slowMovingTotal(只统计无独立卡项); 异常行=error 级告警(by_severity.error)非质量日志; **禁止 errCount(质量日志)与告警数跨域硬加**(曾 8+2202=2210 误导)与当"异常"
- **删单联动勿碰 low_stock(f00ee08c)**: close_orphan 通用重评估曾因 ctx safety_qty=0 硬编码 → available<0 恒假 → 全关 low_stock(删测试单涉 1100 seed SKU → 2093 条全 inactive); low_stock 孤儿已由 _ls_orphan 分支按"无单无档"处理, 通用重评估跳过 low_stock(安全线=库存维度, 删单不影响库存)

### 15.66 看板物化 + 导出口径铁律(2026-10-25——锁根除/数据驱动/残留自愈/导出口径)
- **cache_get 禁 FOR UPDATE 持锁重算(9004 锁超时根治)**: SELECT FOR UPDATE 持锁执行 builder(2-3s) >> TiDB lock_wait_timeout(默认 1s) → 并发请求 9004 降级重算 → 缓存永不命中 → 每次请求全量重算(summary 85-174s 实测); **读命中/miss 重算一律无锁 + REPLACE 幂等**(并发重复算可接受, 最终一致)——与 10-11 弃 stock-risk 悲观锁同源; 若 builder 慢(环境/数据), 持锁设计必 9004, 无"保留锁"可行方案
- **陈旧检测/追平必须数据驱动(orders MAX 基准, 非时间驱动)**: 曾 now-3 天判 agg 陈旧——数据停更 3 天后误判 → 每次直算 60 天(0.88→3-13s 退化); 基准=orders MAX(与 _track_data 追平同语义, §15.46 在陈旧检测的补全); **重建窗口=查询窗口(60 天)**, 写路径+读追平一致
- **agg 残留根治(ON DUPLICATE 只 upsert 现存组合)**: 软删使 (date,channel,status,store) 组合消失 → 旧组残留 cnt 多算(gmv 可能掩盖: 测试单 GMV=0 时 GMV 一致 cnt 不一致); **窗口完整性自愈** = 60 天窗口 orders 行数 vs agg cnt 不一致 → 逐日定位缺口(orders 独有/agg 残留双向 LEFT JOIN)→ **单日 DELETE+INSERT 小事务**(全窗口 DELETE 大事务持锁致并发 9004 废弃); 自愈限频 60s/渠道(每 miss 检测不拖请求); %s/%%s 转义再次应验(单日重建 INSERT 未转义→not enough arguments)
- **agg_dirty 脏标记**: 重建失败置位 → 陈旧检测降级直查(不误用缺数据 agg); 成功清位自愈——封死"补录历史+重建失败"双故障边界(COUNT 一致性检测覆盖不了"同键值修改", 标记兜)
- **M1 迁移一次性标记原则**: 源头已解决(导入自动查补)→ 补齐(或确认无空值)后才写 migration_log 标记(兼容"不抢注+续跑"原设计); 标记后实例启动零全表扫
- **导出口径铁律(2026-10-25 全面排查)**:
  - **数据源必须与页面同源**: 滞销导出曾用 slow_moving(基础 7 字段)而页面用 disposal-suggestions(富字段)——导出与页面展示脱节; **采购导出曾误用补货数据**(get_replenishment_suggestions 组装成采购样式, 缺供应商/MOQ/actual_purchase)——每类导出必须调对应业务接口
  - **表头中文 + 齐全**: 曾直接 return items(接口英文字段作表头); 补货补周转列/采购补库存箱规/订单补品牌/进销存补批次效期; 导出列应覆盖前端展示主要列
  - **模式跟随无兜底**: 补货/采购导出 mode 必传(前端传组件内实际模式, 含非 jd→traditional 强制); 禁 window 全局变量兜底(曾 ||'bbcc' 致传统导出变 bbcc)
  - **按主体/仓型隔离**: 进销存 B-C调拨在途列仅 platform_b 仓型含(own/platform 无——对齐前端 INV_COLS); other 渠道验证 SKU-O 隔离/口径跟随/表头齐全
  - **文件中文命名**: 中文映射+渠道+年月日_时分秒(补货建议_jd_20261025_143025.csv)
  - **导出任务卡片与导入同规格**: driver 标签(前端轮询 frontend/后台 continue-tasks backend)+ done 统计(success=行数/failed=0/elapsed=创建到完成耗时)
- **品牌映射优先级**: COALESCE(69码+channel 优先, sku+channel 兜底)子查询——69码精确匹配优先(sku+69码+channel), 失败兜底 sku+channel; 禁 barcode 单独 join(同商品跨渠道同码歧义)
- **默认值公共常量防漂移 + 用户配置优先**: 内置默认提为模块常量(如 _DEFAULT_SLOW_CATS——接口与计算同源); 库从未配置→返回默认, **用户保存后一律以用户配置为准**(含删空 []——默认只兜"从未配置")
- **冷算性能判定方法论**: 组装 Python 慢(1.3s 稳定/2.5 万次操作)而 SQL 查询 0.2s 正常 + 数据/代码全等 + 多独立路径同步慢 + health 基线波动 → **Makers 实例环境 Python 执行慢**(非应用代码); 判定前先分段计时拆解(builder 追平/维护/构建/同步), 勿归咎锁/缓存/自愈
### 15.67 玻璃进阶 + 去投影 + 数字降维(2026-10-26——概念稿映射/边框立体/渠道目标核实)
- **玻璃底色统一 0.4(light 白/dark 黑)**: 判断底材强度=概念稿参数(低 alpha 透明光感); 改动须 4 个 token 同步(--bg-thin/--bg-regular/--glass-bg × light/dark)
- **去投影立体配方**: 外阴影全去(--shadow-card/sheet/alert/control/glass-shadow= none 或纯 inset)——立体靠 **1px 边框(light 灰/dark 深灰两端可见)+ inset 内高光(顶白, dark 加底黑双内)**; 边框 0.5px→1px 全局统一(按钮/胶囊/弹层/浮层/Toast); 分隔线 border-bottom 保留 0.5px(非边框语义)
- **焦散光(::after 底部光带)仅浮层面板**: 小圆钮(<72px)上 3px 亮带在弧线上突兀——焦散=大表面底座光语义, 组件按尺寸取舍
- **SVG 折射(useGlassRefraction)**: feDisplacementMap per-元素 lens(边缘弯曲+色差), 注入 --glass-url 进 backdrop-filter——**渐进增强**(不支持 url() 环境静默跳过); 挂按钮+浮层面板, 看板卡片不用; 支持检测用 setProperty 非 getPropertyValue 直接测; svg defs 容器不存在时动态创建(隐藏零尺寸)
- **"渠道主体切换胶囊"目标核实铁律**: header 渠道切换=**.header-status**(包裹 select 的胶囊), 非 .hammer-segment(规则页/锤子 tab)——**改 UI 组件前先确认真实 DOM 元素与语义**, 曾误加 .hammer-segment 反光/折射/边框后回退
- **看板数字降维规范**: 金额 fmtBig(≥1亿 x.xx亿/≥10万 x.xx万 **2 位小数**/否则千分位), 计数 fmtInt(≥10万 整数万/否则千分位)——GMV 卡/日均/待处理/断货/低库存计数统一; 图表 tooltip/坐标轴保留完整值(悬浮看细节)### 15.68 登录 401 确认机制 + 乐观项替换(2026-10-26 晚间——无痕首登踢回/422 根治)
- **401 全局处理必须"先确认再踢"铁律**: 任何接口 401 不要直接清 token+跳登录页(曾"登录后并发+冷实例偶发 401 → 立即踢出 → 登录页重挂载清空输入", 无痕首登必现)——**client 401 仅置标记(may-expired), App 收标记调 /auth/check 确认真失效才踢**(10s 限频, check 网络失败不误判); 偶发 401(冷实例/部署窗口/并发)免疫, 真 token 失效仍防挂机重进卡界面
- **乐观更新必须能拿到真实 id / 失败回滚**: 列表项的日期/更新类操作 require 后端真实行 id——乐观插入项先带临时标识(__tmp), POST 成功用后端返回行(含 id)替换, 失败移除乐观项+回滚 keys, GET 失败清空明细防残留; 后端 create 类接口**返回真实新行**(ONE 查询)前端替换——杜绝"乐观无 id 项 → PUT /undefined 422"(08:45 日志实锤)
- **登录体验**: fetch 必须带超时(AbortController, 原生 fetch 无超时—弱网挂起 30s+ 无反馈)+ loading guard 防重复(按钮 disabled 但 Enter onKeyDown 不检查——doLogin 内自检)
- **日志排查流**: 先分类计数(perf/quiet/slow/api_http/business/api_error)→ 逐条看非巡检异常 → 特征(422 undefined/id 缺失/网络)定位真 bug → 其余(401 时段现象/弱网 timeout/冷算 slow/offline)归已知边界并说明依据### 15.70 页面快照静默更新 + 请求生命周期(2026-10-27——快速切页无感/卡骨架根治)
- **页面快照(module 级, 非全局 store——防跨页污染)**: 高频只读页(建议/进销存)module `_snap` 存上次全量, 进入 `useState(() => _snap.x || [])` 先显旧; 加载完成后 `useEffect` 监听 state 写回快照
- **骨架条件统一铁律**: `loading && 数据空` 才骨架(曾 `loading||!dataLoaded` 每次进必闪——即使 store/快照有旧值); 全站 6 处同规格
- **写事件清快照(四维关键)**: 导入/删除/启停等写操作事件(rules-changed/insights-refresh/orders-changed)→ **立即清快照, 宁骨架不看旧**——快照绝不越过写路径(操作后仍显旧=误导, 违反准确性); 与 invalidate+事件联动同链路
- **abortScope(scope)**: api 支持 config.scope + AbortController; 页面卸载 cleanup 取消该页全部在途(axios.isCancel 静默不上报, inflight 移出)——切回不复用旧 promise(曾复用旧页面挂起 promise 等 30-60s 超时卡骨架), 旧请求真正取消 RU 不白跑
- **in-flight 复用加等待上限**: 对复用 promise `Promise.race([旧, 剩余TTL超时])`, 超时自动发起新请求替换(旧作废 __dead)——兜底漏调 abortScope 的路径
- **订单页/看板**: 数据走 store(zustand 持久)即天然快照, 只需同规格骨架条件
- **四维**: 准确=后台全量收敛最新; 实时=写事件清快照+重拉+30s 兜底; 完整=全量替换; 可靠=拉新失败保留快照(瞬时旧), 写后失败显错误

### 15.71 订单页缓存 + 短 TTL 共享表缓存范式(2026-10-27)
- **短 TTL 共享表缓存(5s)**: orders 接口 cache_get(key 含全参数)——热态 0.7s / 冷(失效后首拉)1.06s(载体 0.6s+COUNT 442RU); 原先冷 2.3-7.1s
- **适用条件**: 只读高频列表 + 写操作 invalidate_all 已覆盖(导入/删单即时失效→实时性不损); COUNT 全渠道行数 442RU 是冷算固有(11.8 万行索引范围扫), 物化总数需写失效联动, 收益 0.3s 暂不做
- **diag 参数化 `%` 陷阱再次应验**: `key LIKE 'orders%'` 被 diag 参数化转义误报 0 行——用 `LEFT(key,6)` 前缀比较真验
- **索引**: products 加 (barcode,channel)——品牌子查询(双层 Apply)per-row 提速; SKU 已唯一索引

### 15.72 玻璃内外光学定稿(2026-10-27 42px 版同构——去外投/内化体积/焦散椭圆)
- **去外投核心**: "体积感从外部投射改为内部绘制"——外投影全 none, `--inset-glass`(上亮下暗 inset)承担厚度; 分离感归边框(浅色玻璃灰边/白边=浅底隐身)
- **焦散迭代教训**: 横带(mask left/right+height)→ 圆上平行线; 弧带 ellipse→ 散化/越圆溢出; **定稿=椭圆光斑** inset:0 + radial-gradient(ellipse 50% 3px 圆钮 / 85% 3px 浮层 at 50%100%)——**椭圆自然衰减无直线边, 圆角裁弧, 底部由 border-radius 裁切**; overflow:hidden 裁越出圆部分(非算弦)
- **焦散层叠校准**: bottom 0 贴底覆盖内部暗边(盖倒数第二层), 反光环 1px 弱接触不抢层次; 亮度 --caustic-a 0.5 → light calc(×2)=1.0 / dark 0.5
- **弃容器单位(cqh/cqw)**: 等比缩放(4.2cqh 随高度变厚)破坏跨尺寸一致(72px 与 42px 不同家族); size 容器令内容宽元素(胶囊)塌缩——改"厚度锁定(可见 2px 或 ry3px 固定)+跨度随宽+亮度统一"解耦哲学
- **UI 验收**: 玻璃件边框/inset/反光/焦散四层齐; 浅色看灰边分离; dark 看边框 0.16

### 15.73 summary 两阶段加载与实例阻塞根治(2026-10-28)
- **中间件同步写库阻塞事件循环(最隐蔽的传染源)**: `slow_log_middleware` 内 `_e("INSERT INTO quality_logs ...")` 是同步调用, 直接阻塞 async 事件循环 → 一次 12s 的 summary 组装后, **同实例所有后续请求(含慢日志自身的写库)串行排队** → 实证 07:40:48 / 07:53:48 前端 orders/alerts/quality-logs **同秒三个 30s 超时**, 而同一实例下一次请求立即恢复(1.14s)。修: `await asyncio.to_thread(_write_slow)` —— DB 写卸载线程, 数据写入语义完全不变
- **summary 拆分的正确姿势 = 同缓存键派生多视野**: 不要拆成两个独立接口各自算(会跨时刻不一致)。做法: `dashboard_summary(part="main"|"charts"|"")`, 两份视野**均取自同一次 `_cache_get(_key)` 结果**, 只做顶层键裁剪(main=summary/health_index/periods; charts=trend/stores/brands/period_*/funnel) → **四维一致性天然成立, 无需快照机制**。实测 main+charts 合并 vs 全量 **逐键逐值一致**(gmv 42539605.73 三处相同)
- **首屏两阶段 + 合并式写入**: `part=main` 先渲染卡片(组装轻 ~2s), `part=charts` 并行拉取后**合并进 dashboard(`{...prev, ...charts}`)**, 不能整体替换——否则静默刷新时图表闪空
- **超时分级**: 重接口(summary/stock-risk) 35s(实测峰值 13.9s + 余量, 60s 过长伤响应性); 轻接口保持 30s。**只放宽不加严**——加严会误杀正常慢请求
- **部署窗口冷启动 ≠ 性能问题**: 15:58/16:25 两次 35s 超时, 但**同一秒后端 perf_debug 记录「查询 0.19s 组装 1.14s」正常** —— 时间线吻合部署(15:56 ba8a92f7 / 16:14 7a261d8f) → Makers 实例重启/唤醒耗时**不计入函数内计时**, 前端干等超时。**判定方法: 前端超时时刻 vs 后端 perf_debug 时刻对照** —— 后端已算完 = 平台层问题, 不是代码
- 加固: summary/charts 失败静默重试 1 次(1.2s + `t=` 破缓存), 正常时零额外请求

### 15.74 rules.mode 从"只存不读"到真正生效(2026-10-28)
- **原状**: `rules` 表有 `mode` 列(全部/bbcc/traditional), 前端可编辑保存, 但 **`load_rules_for(event, channel)` 只按 event+channel+is_active 过滤, 评估链路全程不读 mode** → 所有选项实际等价于"全部", 切传统多仓规则不跟随
- **修**: `load_rules_for(event, channel, mode=None)`, SQL 加 `(mode IS NULL OR mode='' OR mode=%s)` —— 空串=全部模式通用; 指定=仅该模式。`evaluate_stock_skus(channel, limit, mode)` 贯通, `/rules/evaluate` 接收前端 `hammerReplenMode`。**`mode=None` 不过滤**保向后兼容(导入链路 order.created 不变)
- **前端 mode 空值被回退 bug**: `mode: f.mode || (editing && editing.mode) || ''` —— **`''` 表示「全部」是合法值, 但 falsy 被 `||` 回退成旧值 bbcc** → 用户改回"全部"保存后几秒又变 bbcc。修: `f.mode ?? ''`(仅 undefined/null 才回退)。**通用教训: 语义化的空值不要用 `||` 兜底**
- **oversell 跨事件误关**: `evaluate_stock_skus` 只评估 `inventory.changed`/`scheduled.daily`, 但"恢复自动关闭"用 hits 集合判定未命中 → 加 `_evaluated_ats` 白名单(仅本次实际评估规则的 alert_type), 不跨事件关闭其他来源告警

### 15.75 清洗页 UX 重构踩坑集(2026-10-28~29, 46 commits)
- **`.step-indicator`/`.step` 曾完全无 CSS 定义** —— tsx 里天天用, 样式表零定义(裸露无样式)。**排查方法: 类名在 tsx 出现但在 css 零命中 = 缺失定义**(顺手脚本化检查)
- **表格高度 = dvh 减固定值, 横屏必崩**: `calc(100dvh - 340px)` 竖屏(844)得 504px 正常, **横屏(390)得 50px 表格不可见**。正解: `@media (max-height: 500px)` 单独给 `60dvh`, 加 `clamp(240px, calc(...), 100dvh)` 兜底 dvh 首帧异常
- **CSS `max-height` 与 `min-height` 并存时 max 优先** —— 写过 `maxHeight: max(220px, calc(...))` + `minHeight` 会互相压制, 结果仍是 50px。要么用 clamp 单值, 要么分媒体查询
- **iOS 地址栏伸缩触发 `resize`, 会毁掉滚动位置**: 为修"横条滞留"加了 `window.addEventListener('resize', () => el.scrollTop = 0)` —— **用户每滑一下页面, iOS 地址栏收起 → resize → scrollTop 归零 → 内容看起来"没出现"**。正解: 只监听 `orientationchange` + **用宽高比判断是否真实横竖切换**(尺寸微变不处理), 且只清 `scrollLeft`(横条)不动 `scrollTop`(阅读位置)
- **不要为轻微视觉问题改平台原生滚动行为**: 隐藏 `::-webkit-scrollbar{display:none}` + 移除 `WebkitOverflowScrolling:touch` → **iOS 滚动容器首帧不绘制内容**(必须手动触摸触发)。代价远大于收益(横条滞留 1s)。已全部回退
- **CSS 语法错误会静默废掉整段样式**: `.tpl-section-title` 漏一个 `}`, 导致其后所有规则(含焦散/反光/弹窗胶囊圆角)在浏览器**整段丢弃** —— 表现为"焦散没了、胶囊变正方形、高度不一致"。**每次改 CSS 必跑 `npx prettier --check`**(能捕获 Unclosed block)
- **`<option>` 不能内嵌 SVG** —— 图标选项改两态 pill 分段控件
- **模板弹窗曾双列数显示**: 标题行「已加载 N/M 条 · 显示 x/y 列」+ 表格容器内 `common.showing/common.columns` 旧提示 → 重复。排查时**全局 grep 同类文案**而非只看标题区

### 15.76 日志归档一致性: `%` 转义陷阱再犯(2026-10-29 凌晨)
- **现象**: `files` 列表显示 842 条, 但点击文件弹窗只有 664 条
- **第一层根因**: `files` 用 `max(归档count, 明细count)`, 而 `file` 接口**优先返回归档 markdown**(旧快照) → 修: 归档落后时改用实时生成并回写归档
- **第二层根因(修复形同虚设)**: 修复代码里写了
  ```python
  one("SELECT COUNT(*) AS c FROM quality_logs WHERE log_type IN (%s) AND DATE(created_at)=%s" % _in3, ...)
  ```
  **两个 `%s` 占位符只给 `_in3` 一个值 → `TypeError: not enough arguments for format string` → 被 `except` 吞掉 → `_live_cnt` 恒 0 → 永远走归档分支**
  修: 第二个占位符写 `%%s`(预格式化后保留给 pymysql)
- **判定方法**: 修了没效果时, **先手工复现格式化/构造逻辑**(`python -c "..."` 单跑那行), 别急着怀疑部署。本 skill 已多次记录 `%` 转义, 带预格式化的 SQL 一律 `%%s`

### 15.77 看板首帧骨架(2026-10-29 凌晨)
- `const [chLoading, setChLoading] = useState(false)` → 首帧渲染**空卡**(dashboard 为 null 但 loading=false), useEffect 跑起来才置 true → 用户看到"骨架闪一下/没有骨架"
- 修: `useState(() => !useAppStore.getState().dashboard)` —— **构造期就按"有无旧值"决定**, 首帧直接正确
- 同理适用于任何"loading 初始值"——首帧渲染前应已知道该不该 loading

### 15.78 库存数据源分离: 自有仓重算制 / 平台仓快照制 / 订单不碰库存(2026-10-29 架构定案)

**业务前提(用户明确)**: 所有数据源 = ERP 与平台后台**导出的历史快照**, 非 API 实时事件流。
由此推导: 任何"导入后扣库存"都是拿历史记录修改历史快照 = 重复计算。

| 仓型 | 权威来源 | 说明 |
|---|---|---|
| own | **重算制**: `available = beginning_stock + Σ入库 − Σ出库` | 出入库流水驱动, 支持批次精准管理 |
| platform / platform_b | **快照制**: 库存导入直接生效 | 平台后台导出已含销售影响/在途/调拨 |
| 订单 | **不碰任何库存** | 纯数据源(日销/GMV/规则) |

**为什么重算制而非增量制**(用户场景决定):

| 场景 | 增量制 | 重算制 |
|---|---|---|
| 实时录入(每天导) | ✓ | ✓ |
| **滞后补录**(补一周前) | ❌ 顺序错 | ✅ 自动正确 |
| **重复导入** | ❌ 翻倍 | ✅ 结果不变 |
| 对账 | ❌ 无法解释差额 | ✅ 恒等式恒成立 |

**铁律**: 期初 = 库存导入值, **任何后续步骤不得改写**(曾 `_sync_inv_month` 用 `beginning_stock = available_qty` 覆盖
→ 重算结果被打回 → 恒等式 998 项偏差)。负数期初视为**导入数据问题**, 由每日对账暴露, 不自动修正。

### 15.79 69码四维绑定规范(2026-10-29, 15 处消费方统一)

**绑定键**: `仓库类型 + SKU + 69码 + 仓库名`
**匹配规则**: **有值精准匹配; 任一方为空 → 降级(少掉 69码 比较)**

```sql
-- 标准写法(库存重算/批次对账通用)
AND (COALESCE(i.barcode,'')='' OR COALESCE(r.barcode,'')=''
     OR COALESCE(r.barcode,'')=COALESCE(i.barcode,''))
```

**覆盖范围(缺一不可)**:
- 数据层: 库存重算 / 批次重建 / 批次对账 / 自动建档 / 日销快照 / 日销物化
- 决策层: 补货 / 采购 / 滞销 / 断货(BC·OWN·C) / 规则 ctx / 进销存 / 健康度 / 临期处置

**踩坑清单**:
1. **日销表主键必须含 barcode** —— 否则 `ON DUPLICATE` 把同 SKU 不同码的行互相覆盖
2. **TiDB 聚簇索引不可 DROP PRIMARY KEY** → 用 `SHOW CREATE TABLE` 取 DDL 改写主键重建表
3. **派生数据重建不留旧表**(日销/快照可从订单完全重算), 不留 `_old` 兜底包袱
4. **批次对账必须四维逐一比对** —— 按 SKU 汇总会让不同码的偏差互相抵消(A 多 100 / B 少 100 → 汇总差 0)
5. **出库扣减不猜**: 未填批次(生产/截止日期)时**不扣任何批次** + 告警, 不做 FIFO 兜底

### 15.80 seed 分步生成铁律(6 轮返工沉淀)

**顺序约束**(违反任一即数据错):
```
库存(step5, 设期初) → 流水(step6) → 重算+批次(step7) → 月汇总(step8) → 告警+快照+物化(step9)
```
1. **流水生成时批次还不存在** → 批次池必须从 **inventory** 取码, 不能从 batches
2. **月汇总不得改写期初/available**(只更新 month_inbound/outbound)
3. **物化重建必须在快照之后**, 且并入 step9(端点以 `nxt>=10` 判完成, 新增 step10 永不执行)
4. **写入全用 `INSERT IGNORE`**: 普通 INSERT 时任一键冲突 → **整批 500 行失败 + 任务中断**
5. **order_no 需步偏移 + 随机后缀**: `total` 每步重置会跨步撞 `uq_orders_no_sku`
6. **幂等**: `_seed_records`/`_seed_batches` 开头先 DELETE 该渠道旧数据(纯 INSERT 重复执行翻倍)

### 15.81 重置完整性检查表(2026-10-29 全排查)

`_TABLES_RESET` 必须覆盖**所有派生表**, 漏一张就出现"重置后仍有数据":

| 必清(易漏) | 后果 |
|---|---|
| **sku_sales_stats** | 补货/采购/断货/健康的数据源 → 重置后卡片仍有数据 |
| data_freshness | 新鲜度圆点显示旧时间 |
| export_files / eval_pending / agg_dirty | 缓存与脏标记残留 |
| daily_sales_snapshot / batches / orders_day_agg | 派生数据 |

**保留**: `users`(账号) / `migration_log`(迁移记录) —— 非业务数据。

**前端侧**: 仅清 store 不够 —— 页面内 state(procList 等)需通过 `pageVersion` 递增 + 事件广播触发重拉。

### 15.82 Makers 多实例陷阱(2026-10-29)

**现象**: 代码已修复并部署, 但请求仍报旧错误。

**原因**: Makers 多实例 —— 请求被**尚未回收的旧实例**处理(旧代码)。

**应对**:
1. 修完后等 **5 分钟以上**(部署 + 实例回收)再验证
2. 关键写入改成**幂等**(INSERT IGNORE / ON DUPLICATE), 即使打到旧实例也不中断
3. 部署状态用 `DescribePagesDeployments` API 确认, 不要假设立即可用

### 15.83 SQL 拼接三大陷阱(本轮反复踩)

1. **`%` 优先级高于 `+`**: `'A' + cond + 'B' % args` 实际只格式化 `'B'` → 写成 `('A'+cond+'B') % args`
2. **占位符数量**: 给子句加过滤后, 外层 `%` 的参数个数要同步(本轮 4→5 漏改导致 heal 全失败)
3. **`LIKE '%中文%'` 在 diag 中失效** → 排查日志必须按 `source` 过滤, 不能靠中文 message 匹配

### 15.84 低库存口径: 两端必须同口径同维度(2026-10-30 根治)

**症状**: 跑一次 `/rules/evaluate` 后 jd low_stock 711 → 3157、other 2980, 9 个仓各 326–379 条。

**根因**: 判定链两端口径错配 —— 左「单仓 `available_qty`」vs 右「全国 `ds × 周期`」, 差 7–8 倍,
每个仓都必然报。这类错误**不报错、不告警**, 只有人工核对才能发现。

**正确口径**（维度是既定业务对象, 不是可降级层级）:

| 模式 | 左（可售, 含在途） | 右（需求） | 粒度 |
|---|---|---|---|
| bbcc | C可用+C在途+B可用+B在途 | `ds × 全周期` | SKU×69码 一条（`__BC__`） |
| traditional | 逐仓 可用+在途 | 该仓 `ds × (lead_time+safety_mult)` | SKU×69码×仓 |
| own（两模式） | 逐仓 可用+在途 | 该仓 `ds × (采购前置+安全)` | SKU×69码×仓 |

**铁律**:
1. 告警卡与建议页必须**同源同口径**（§15.22 卡片定位铁律的延伸）——本次就是"建议页用 BC 合计、告警用单仓"
2. **降级只降码, 不换维度**: 该维无数据 → 返回 0(不触发), **绝不拿全国/全国C合计兜底**
   （拿别处销量判本仓 → 可撑天数被放大或压低, 误报漏报都由此来）
3. `traditional` 的 `platform_b` 不参与（与采购 `purchase_calc:127` 同口径: B 仓仅 jd+bbcc 链路）

**实测收敛**: jd 3157 → 504（own 364 + `__BC__` 140, B 仓单独 0）, other 2980 → 1051。

### 15.85 "SELECT 漏列 + 输出兜底" 双重掩盖(2026-10-30 两个真 bug)

**现象**: 补货/断货的 69码 细分一直不生效, 但接口输出 `barcode` 字段一直有值。

**根因链**:
1. `_inventory()` / `stock_risk` 的 SELECT **没有 `barcode` 列**
2. 主循环 `_bc = r.get("barcode") or ""` → 恒空 → 分组键退化为 `(sku,'')` → 两码合并
3. 输出行 `"barcode": _bc or prod.get("barcode","")` → **用商品档案兜底**, 界面永远"有码"

**判定法**（写进排查习惯）:
- 输出字段存在**兜底赋值**（`x or prod.get(...)` / `x or 默认值`）时, **不能以"输出有值"证明链路生效**
- 必须看 ①SELECT 列清单 ②分组键 ③中间变量是否为空
- 这类问题的验证只能靠**多码数据**（同 SKU 两个 69码）——单码数据下两条码合并与正确结果完全一致, 永远测不出来

**修复**: 两处 SELECT 补 `COALESCE(barcode,'') AS barcode`。

### 15.86 库存列单一真源 + 运行时自检(机制层根治)

漏列会反复出现 → 从机制上堵:

1. **`routes/common.INVENTORY_COLS`** —— 库存表列单一真源（barcode/warehouse/在途/锁定/期初全含）,
   补货/断货/看板/健康/规则评估 5 处核心查询统一 `"SELECT " + INVENTORY_COLS + " FROM inventory"`,
   不再各写一份列清单
2. **`routes/common.check_cols(rows, cols, src, what)`** —— 数据入口运行时自检:
   首行缺列即 `try_err` 留痕（不抛异常）。已接入补货/断货/看板三入口
3. **放弃文本扫描门禁**: 试过在 local_test 里扫 `FROM inventory` 是否含 barcode —— 误报 6→12→6 处
   一直在漂（聚合/存在性/UPDATE 全被误判），**文本门禁不可靠, 运行时自检才精准**
4. 确实不需要码的查询（`SELECT 1`/`COUNT`/`DISTINCT`/`MAX(id)`/UPDATE）统一标注
   `# no-barcode-ok: 理由`，人工 review 时一眼分辨"不需要"与"忘了"

### 15.87 异步任务续跑与 driver 标签(2026-10-30)

- **请求驱动续跑条件恒假**: `request.url.path.startswith("/api/")` —— Makers 转发已剥离 `root_path(/api)`,
  实际 path 是 `/dashboard/summary` → 条件永假 → **续跑从未触发**。
  实证: `cleansing_eval` 任务 created 后 10 分钟 `updated_at` 不变, 只能等每日 05:05 平台 cron。
  **判定法**: 同一份 path 也用于 slow_request 日志——日志里 `GET /dashboard/summary`（无 /api 前缀）就是证据
- **driver 标签**: `common.mark_driver(task_id, driver)` 在四个 status 函数入口统一打标
  （前端轮询默认 frontend / `continue_tasks` 显式 backend）。此前只有 cleansing 有专用接口,
  seed/eval/export **从未写 driver** → 任务卡标签永不出现
- **标签只在 `status==='running'` 渲染**: 历史任务看不到推进方式（设计如此, 要展示需放宽前端条件）

### 15.88 数据版本指纹: 计数版 + 表指纹双轨(2026-10-30)

- 原 `/health version` 由各表 `MAX(id)` 拼接 → **只认新增, 不认更新**（告警关闭/库存重算/参数保存全无感）
- 另有一个隐蔽 bug: `MAX(id) FROM replenishment_config` —— 该表主键是 `(key, channel)` **没有 id 列**,
  查询抛错被 `except` 吞 → **version 恒 "0"** → 前端"版本变化 → clearCache+loadAll"链路完全失效
- 修复: 新增 `data_version` 计数表, 在 **`invalidate_all()`（所有写操作必经的中央失效点）** 递增;
  `version = "<计数>|<表指纹>"` —— 计数覆盖 UPDATE, 指纹覆盖绕过应用层直接改库
- 成本: 写操作 +1 次 UPDATE; health 读 +1 次 SELECT（高频轮询下约占月 RU 1.7%）

### 15.89 快照残留自愈: 软删订单后快照不会自动回滚(2026-10-30)

- 快照是"按 (date,sku,码,仓) 组合 upsert" → **订单软删后对应组合在快照里仍在** →
  物化/补货仍读到已删订单的日销（实测: 删单后 SKU 在物化里残留）
- `orders_day_agg` 早有窗口自愈(§15.66), **快照侧缺失** → 补 `dashboard_maintain._snapshot_reconcile`:
  按天比对（快照行数/合计 vs 订单聚合）→ 不一致的天 **DELETE + 单日 INSERT 重建**
  - 单日小事务（防大窗口 DELETE 造成 9004 锁超时）
  - 单次最多修 10 天, 余下下次续跑
  - 每日一次（`maintenance_log` 抢占）+ 后台线程, 不拖 summary 响应

### 15.90 性能: 先实测再决定, 别猜(2026-10-30 自我纠偏)

**背景**: `/rules/evaluate` 曾 25.5s, 直觉判断"下推 SQL 能提速" → 实测后否定。

**实测三件套**（本地零 RU + diag 只读）:

| 层 | 实测 | 占比 |
|---|---|---|
| SQL（6 段关键查询） | 合计 ≈1.36s | **5%** |
| Python 解析（json.loads×2.7万 / dict 合并 / 表达式解析） | ≈0.8–1.0s | **4%** |
| 其余（ctx 构造、规则匹配、订单评估） | ≈23s | 91% |

**结论**: 下推 SQL 顶多优化 5%, 方向错误。真正的收益来自**粒度收敛**:
BC 合计后 ctx 由 9000 → 2002, 评估自然从 25.5s 降到 **2–3s**（分段埋点证实最大段 `calc_load=0.9s`）。

**沉淀**:
1. 本地能证的不要上线试（`python3 -c` 直接跑关键函数/key 逻辑）
2. 必须线上验的用只读栈（diag + GET），批量导入只用于写链路冒烟
3. 埋点只用于"本地无法复现的平台层问题"（冷启动/多实例），不是首选
4. **先查部署版本再验行为**: `DescribePagesDeployments` 看 `RepoCommitHash` + `UsedInProd` ——
   前端静态资源先可访问、函数实例要等 `Status=Success` 且 `UsedInProd=true` 才切换，
   "看到新文案"≠"后端代码已生效"

### 15.91 前端跳转定位三铁律 + 两条渲染链 + 缓存假象(2026-10-30)

本轮 4 个"看起来是后端坏了"的前端问题, 沉淀为可复用排查顺序。

**一、跳转定位铁律(低库存/断货卡 → 进销存 高亮)**

症状: 点卡片跳进销存, 立刻弹「未找到 xxx, 可能不在当前仓库维度」, 几秒后该 SKU 才以高亮出现。

根因: 跳转瞬间 `inventory` 仍是**上一次的列表**(新的 with-sales 请求未回), 定位 effect 只看
`inventory.length > 0` 就判定并 `locateCheckedRef = true`(锁定不复查) → 误报。

三条铁律:
1. **加载中不判定** —— `if (loading) return;`
2. **数据必须已包含本次搜索词**(证明是跳转后的新数据) —— `inventory.some(x => x.sku.includes(s))`
3. **真找不到才提示** —— 提示只在"数据已就位且确实没有"时出现; 找到元素才滚动高亮, 找不到不弹
   (原逻辑是"找不到就弹", 必然在旧数据上误报)

**二、App 有两条渲染链, 新页面别插错**

- 主内容区: `switch (page) { case 'inv': return wrap(<InventoryPage/>); ... default: return null }`
- 锤子菜单: `page === 'inv' ? <HammerInventory/> : ...`(页面内快捷面板)

把新页面插进锤子链 → 主内容区 `<main class="container">` 为空(用户看到整页空白)。
判定法: `document.querySelector('main.container').innerHTML.length === 0` 即未挂载。

**三、`apiGet` 已解包, 别再写 `r.data.data`**

```js
// client.ts
return { data: 'data' in d ? d.data : d, status: resp.status }
```

`r.data` **就是**后端 `data` 字段。写 `r.data.data` 恒 `undefined` → 列表永远空。
业务失败(`ok:false`)由 apiGet **throw**, 统一用 `e.message` 提示, 不要在 `.then` 里判 `r.data.ok`。

**四、前端"改了没生效"先排缓存假象**

实证: 硬编码查 `assets/index-DlKxxDYf.js` 返回 **2544 字节**(EdgeOne 404 页), 而首页实际引用
`assets/index-CQOHMQEP.js`; 浏览器内存缓存仍跑旧 bundle → 新页面/新文案全都不出现,
看起来像"部署没生效/代码没作用"。

排查顺序:
1. `document.querySelectorAll('script[src]')` 拿当前页真实加载的 JS
2. `curl -o /dev/null -w '%{http_code} %{size_download}'` 确认该文件 200 且大小合理(应 ~1.7MB)
3. 首页 `curl https://站点/ | rg -o 'assets/[^"]+\.js'` 对比是否同一个文件
4. 不一致 → 浏览器在用旧 HTML/旧 JS → 换 cache-busting URL 强制刷新再验

**五、跳转入入口盘点(避免遗漏同类问题)**

| 入口 | 目标 | 定位方式 | 是否存在竞态 |
|---|---|---|---|
| 低库存卡 / 断货卡 / 告警弹窗 → | 进销存 `inv` | 前端定位(DOM 滚动高亮) | **有(本节铁律已修)** |
| 采购卡 / 补货卡 → | 采购/补货 `insights` | 后端搜索(搜索词随请求下发) | 无 |

商品/供应商/订单页用**前端过滤** `hammerSearch`; 目前没有带搜索词的跳转入口,
若将来新增, 必须同样"数据到位后再判定"。

**六、构建脚本 `;` vs `&&` 的陷阱(2026-10-30)**

`frontend/package.json` 的 build 原为 `npm install ... 2>/dev/null; vite build` —— **分号**串联,
`vite build` 失败时脚本退出码仍是 0 → Makers 判定成功 → 部署**上次遗留的 dist**(新页面没进包,
表现为"代码明明改了但线上没变")。

改成 `rm -rf dist && npm install ... && vite build` 后, 暴露真问题: 云端 `vite build` 在
`rendering chunks` 阶段失败 → 构建失败 → **全站 404**(静态资源与函数一起下线)。已 revert 回 `;`。

**当前状态**: 构建失败对我们是不可见的。排查新页面"没上线"时:
1. 先确认 `script[src]` 指向的 JS 是否 200 且大小合理(正常 ~1.7MB; 若返回 ~2.5KB 是 EdgeOne 404 页)
2. bundle 里搜新功能的关键字符串(如 `copy_from`)判断是否真进去了
3. 都没进去 → 说明构建失败被吞, 需单独排查云端构建(降级方案: `--minify false` 降低内存峰值, 或拆 chunk)

### 15.92 异步拉取三件套：同步中计数 / 重试留痕 / 配置不缓存(2026-10-30, **强制要求**)

任何页面/组件发起**数据拉取**(api.get / fetch / api.post 后轮询)都按下表接入; 缺项视为实现不合格。

#### 一、必须接入「同步中 / 加载中」(syncing 计数)

```tsx
useAppStore.getState().setSyncing(1)      // 请求开始 +1
try {
  const r = await api.get('/api/xxx')
  ...
} finally {
  useAppStore.getState().setSyncing(-1)   // **唯一出口** finally 归零(return/throw/abort 全覆盖)
}
```

- 胶囊文案由 `syncOld` 决定: **有旧值 →「同步中」/ 首载 →「加载中」**(`App.tsx` syncBadge)
- **重试期间只 ±1 一次**: 把重试写成 for 循环包在同一个 try/finally 内;
  若逐次 `+1/-1`(如 setTimeout 递归), 胶囊会闪一下归零再亮 → 用户以为已完成
- 禁止裸 `fetch(...)` 绕过该机制(2026-10-30 channels 拉取即此错: 用户完全看不到进度)

#### 二、失败必须重试 + 留痕

```tsx
for (let attempt = 0; attempt <= 2; attempt += 1) {
  try { ...; return } catch (e) {
    if (attempt >= 2) { reportError('api_http_error', 'xxx 加载失败', '', 'warning'); return }
    await new Promise(r => setTimeout(r, 1500 * (attempt + 1)))   // 1.5s / 3s 退避
  }
}
```

- **禁止 `catch(() => {})` 静默**(本轮同类错误: 渠道列表失败后无限使用兜底值, 无人发现)
- 失败降级要有可见感: 保留旧数据 + 顶部提示(参考 InventoryPage: `loadErr && inventory.length > 0` 非阻塞提示)

#### 三、配置类数据禁止 localStorage 缓存

| 数据类型 | 缓存策略 | 理由 |
|---|---|---|
| **配置**(channels / rules / 补货参数 / 列配置) | **不缓存**(每次挂载拉接口) | 禁用/改名/参数调整需**即时生效**; 缓存会让老客户端长期用过时配置(实时性缺口) |
| 业务数据(订单/库存/日销) | 走后端表缓存(analysis_cache, TTL + invalidate_all) | 见 §15.27; 前端只管 30s 静默刷新 |
| 纯 UI 偏好(列显示/排序/主题) | localStorage 可缓存 | 与业务数据无关, 滞后无风险 |

- 首屏正确性**靠兜底文案保证**(如 channels 兜底写 `'京东渠道'`), **不要用缓存换**
- 兜底值必须与后端默认值一致: 兜底写 `'京东'`、接口返回 `'京东渠道'` → 就会出现
  "文案有概率变短"(首屏兜底, 拉到后变长)

#### 四、接入检查清单(PR 自检)

1. [ ] 请求开始 `setSyncing(1)`, `finally` 里 `setSyncing(-1)`(唯一出口)
2. [ ] 重试循环包在同一 try/finally 内, 全程只 ±1
3. [ ] 失败 ≥2 次后 `reportError(...)` 留痕, 不静默
4. [ ] 有旧数据时失败 → 保留列表 + 非阻塞提示, 不白屏
5. [ ] 配置类数据没有 localStorage 缓存
6. [ ] 首屏兜底值与后端默认值一致(文案/模式/枚举)
### 15.93 进销存口径体系 + 导出下载坑(2026-10-30 晚间)

#### 一、进销存两列的分工(own 维度)

| 列 | 公式 | 方向 | 数据源 |
|---|---|---|---|
| **在库周转** | 期间天数 × (真实期初 + 当月入库) / 当月出库 | 回溯(实际周转效率) | inventory.beginning_stock + inbound/outbound 聚合 |
| **库存可撑天数** | 可用 / 日销 | 前瞻(还能撑几天) | inventory.available_qty + 日销 |

- **own 日销 = 全国 C 仓销量**(自有仓不销售, 消耗速度 = 平台整体销售): 取值链
  `__C__ 该码 → __C__ 合计 → 0`, 不跨维; platform/platform_b 行日销仍取逐仓
- **周转出库为 0 → 无定义(None → 前端显示 —)**: platform 仓无出入库流水,
  周转恒 None, 该概念由建议页承接; 仅 own 维度展示周转/可撑/期初/月入月出
- **底部总计 = 该维度所有 SKU**(后端 summary, 60s 表缓存): 不是已加载行之和;
  平均周转与行同口径加权(期间天数 × Σ(期初+月入)/Σ月出); 可撑合计仍按已加载行加权
  (全量日销需 +64 RU, 性价比低, 暂不做)

#### 二、导出下载三坑(每坑都导致下载文件损坏/失败)

1. **分片合并丢 base64 前缀** → 全片字符串拼接后无前缀 → 下游按 utf-8 编码 → base64 乱码
   修: 逐片 `b64decode(c[7:])` 成 bytes 再拼
2. **Content-Disposition 中文文件名** → HTTP 头必须 latin-1 可编码 → UnicodeEncodeError → 500
   修: 非 ASCII 时 `filename*=UTF-8''%(quote(name))` + ASCII fallback
3. **双 BOM** → 存储文本已带 BOM 又 `encode('utf-8-sig')` → Excel 首列乱码
   修: 仅在缺失时补 BOM(保证恰好一个)

- **前端下载必须检查 `r.ok`**: fetch→blob→a.download 前端自命名, 500 时下载到 HTML 错误页
  却"看着成功"(文件名正确、内容损坏) —— 这是静默损坏, 比报错更隐蔽
- **导出"可用"列 = 真实可用**(每行重复, own=重算值/platform=导入快照), 批次数量看「批次数量」列
- **期初列 = 真实导入值**(beginning_stock_raw), 不是反推的本月期初

#### 三、channels 加载(配置类数据规范, 详见 §15.92)

- 兜底文案必须与后端默认值一致(『京东』vs『京东渠道』会出现"文案有概率变短")
- 配置类数据禁 localStorage 缓存(try/catch 全环绕); 首屏正确性靠兜底, 不靠缓存
- 重试期间 syncing 单次计数(逐次 ±1 会造成胶囊闪烁) → for 循环包同一 try/finally