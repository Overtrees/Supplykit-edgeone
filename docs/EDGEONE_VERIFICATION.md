# EdgeOne Makers 迁移验证报告（2026-09-05 实证）

> 本文档基于**线上构建日志 + 官方构建器源码**逐项验证，非猜测。
> 仓库：`Overtrees/Supplykit-edgeone`（feat/edgeone = 迁移版，main = PA 备份版）
> 项目：Makers `supplykit`（makers-vltewg30sszj，Provider: Github，Git 集成）

## 1. 验证结论总览

| 环节 | 结果 | 关键证据 |
|---|---|---|
| Git 集成部署 | ✅ | 推 feat/edgeone 自动构建部署（GitHub Webhook） |
| 构建配置 | ✅ | edgeone.json `build.command/cwd` + 项目 API 配置 |
| 前端 Vite 构建 | ✅ | 构建日志 `✓ 958 modules transformed` |
| **Python 函数检测** | ✅ | `Found 1 Python functions`（需行首 `app =` 入口标识） |
| 依赖安装 | ✅ | uv + CPython 3.10.21 + 27 包（需移除 supabase 死依赖） |
| 函数路由注册 | ✅ | MetaData `server-name: api-python` |
| **函数可调用** | ✅ | 海外节点 `/api/health` → 200 JSON |
| 大陆访问 | ⚠️ | 免费域名需 3 小时预览链接（平台规则） |
| **完整后端挂载** | ❌ | 函数包只含 cloud-functions/，backend/app 进不去（Phase 2 前置） |

## 2. 关键实证（踩坑记录，防再绕圈）

### 2.1 入口标识检测（最大坑）
官方构建器源码 `hasFunctionEntryPoint` 用正则检测入口：
```js
/^app\s*(?::\s*\S+\s*)?=\s*/m   // 行首 app = 才识别
/^class\s+(?:handler|Handler)\s*\(/
```
`from app.main import app` **不满足**（app 在行尾）→ `No Python functions found` → 纯静态部署。
修复：入口文件必须含行首 `app =`（如 `app = FastAPI()` 或 `app = _real_app`）。

### 2.2 构建命令执行目录
Makers 构建器从**项目 API 配置**读 BuildCmd/InstallCmd（非 edgeone.json）：
- `InstallCmd: cd frontend && npm install`（frontend 有 package.json）
- `BuildCmd: cd frontend && npm run build`
- RootDir 留空（仓库根），OutputDir: frontend/dist
- ⚠️ 错误示范：`cd frontend` 放 edgeone.json build.command 会让 Python 函数在 frontend/ 内找 cloud-functions → 找不到

### 2.3 函数包边界（架构性结论）
- 函数包 = **仅 cloud-functions/ 目录**（源码 `copyUserFiles()` 实证，includeFiles 对 Python builder 不生效）
- 复制的扩展名：`.py/.json/.yaml/.yml/.txt/.toml/.ini/.env`（排除 .sh/.db）
- **完整后端 backend/app 无法进入函数包** → `from app.main import app` 挂载失败
- 且后端依赖 SQLite 文件（无持久文件系统）→ **完整后端 = Phase 2（TiDB 数据层）前置**

### 2.4 依赖冲突
- `supabase==2.31.0` 要求 `pydantic>=2.11.7`，与 `pydantic==2.9.2` 冲突 → uv 解析失败
- 项目 0 处引用 supabase（死依赖）→ 从 cloud-functions/requirements.txt 移除

### 2.5 大陆访问 401
- 免费域名：大陆访问需**控制台预览链接（3 小时有效）**；海外直连
- 稳定大陆访问需自定义域名（大陆可用区需备案）

## 3. 当前可交付状态

- **函数通道已验证**：最小 FastAPI 入口（cloud-functions/api/index.py）海外 200
- **前端已部署**：Vite 产物正常
- **环境变量已配**：TIDB_HOST/PORT/USER/PASSWORD/DB/SSL（API 直设，CLI env set 需 link 不可靠）
- **CI 已通**：GitHub Actions 语法门禁（3.10）+ 部署 + env 设置

## 4. 下一步（Phase 2）

完整后端挂载需要：
1. **数据层 TiDB**（后端 SQLite → TiDB，无持久文件系统硬约束）
2. **cloud-functions 自包含**：后端代码进函数包（复制到 cloud-functions/ 或 build 时打包）
3. 任务系统外部化（schedules/外部 cron）+ WS→轮询
