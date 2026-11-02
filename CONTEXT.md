# CONTEXT.md — 新会话引导文件

> **给 AI 助手**: 用户在新对话中提到 supplykit / 供应链系统 / 看板 / 规则 / 导入等关键词时,
> **先读完本文件**, 再按需读 `docs/DEVELOPMENT.md` 对应章节。读完即可接续工作, 无需用户复述背景。

## 项目是什么

供应链管理系统(进销存 + 看板 + 规则引擎 + 数据清洗导入), 部署在 EdgeOne Makers:

- 线上地址: https://supplykit.top (登录 admin/admin123)
- 仓库: https://github.com/Overtrees/Supplykit-edgeone.git (分支 `feat/edgeone`)
- 本地路径: `/workspace/supplykit` (前端 `frontend/`, 云函数 `cloud-functions/api/`)

## 必读文档索引

| 主题 | 位置 |
|---|---|
| UI 设计规范(小卡/大卡/弹窗/图标/数字格式) | `docs/UI_DESIGN.md` |
| 开发经验与根因记录(按天追加) | `docs/DEVELOPMENT.md` §15.x |
| 2026-11-01 全天 56 提交总览(18 条问题→根因→验证) | `docs/DEVELOPMENT.md` §15.105 |
| 低库存卡明细丢失三层根因(最顽固 bug) | `docs/DEVELOPMENT.md` §15.104 第五节 |
| 自有仓为什么不能用低库存口径 | `docs/DEVELOPMENT.md` §15.102 |
| 库存导入职责边界(期初/在途 vs 批次) | `docs/DEVELOPMENT.md` §15.98 |
| 69 码不猜: 补码两道闸 | `docs/DEVELOPMENT.md` §15.97 |
| 定时任务(平台 schedules, 不是 GitHub Actions) | `docs/DEVELOPMENT.md` §15.101 |
| 门禁转绿 + 三处「try 吞 NameError」潜伏 bug | `docs/DEVELOPMENT.md` §15.106 |
| 低库存卡口径: bbcc=BC 合计 / traditional=逐仓(轮转只服务后者) | `docs/DEVELOPMENT.md` §15.109 |
| S0 止血: cron 执行次数护栏 / 连接池名额 / traceback 收口 / 前端泄漏 | `docs/DEVELOPMENT.md` §15.110 |
| 凭据来源 + 只能走 GitHub API 推送(附踩坑) | `docs/DEVELOPMENT.md` §15.107 |

## 关键口径(不可破坏的业务约定)

1. **自有仓(集货仓)不产生销售** — 低库存/断货口径在自有仓恒不触发;
   自有仓用供给侧三口径: 调拨缺口 `own_transfer_gap` / 积压 `own_overstock` / 效期 `own_expiry_risk`
2. **补货模式**: bbcc(B→C 一盘) / traditional(C 仓); 日销取值统一走 `biz.sales.pick_ds`, 绝不拿全国合计兜底
3. **告警是「SKU × 仓」粒度** — 同一 SKU 在多仓各一条; 卡面展示需去重, 弹窗展示全量
4. **计数与列表分离** — 卡面 `(N)` 用后端独立 COUNT(`alertCounts`), 列表可能截断, 二者天然不等
5. **库存月汇总** = 仓型×SKU×69码×仓 聚合, 先清零再覆盖
6. **效期管理三态** — `expiry_managed` 是/否: 是→FEFO 精确批次, 否→到货序 FIFO

## 工作流程铁律

- **换机先自检**: `python3 scripts/env_check.py` —— 缺 .env / EDGEONE token / push 凭据会一次性列出, 别等 push 才发现
- **先本地, 再线上**: 逻辑验证用 `python3 scripts/local_api.py`(本地直连同库), 推一次 2~3 分钟且云函数 120s 硬限
- **部署判据**: `scripts/deploy_status.py` 输出 `Commit == 本地 HEAD && Prod == yes && Status == Success` 才算部署完成;
  若本机走 `push_api.py`(见下), 本地 SHA 与远端不同 —— 改判「远端 HEAD 与本地 HEAD 逐文件一致」(脚本会自检)
- **先看数据源实际返回什么**(拦截响应看真实分布), 再决定改前端还是后端 — 不要拿前端兜底掩盖口径问题
- **本地回归**: `cd cloud-functions/api && python3 local_test.py` → 当前 205 通过/0 失败
- **多视口 UI 验证**: Playwright 实测, 参考 `scripts/ui_own_card_measure.py`; 视口 375/390/844/768/1024/1440
- **文档同步**: 每项改动在 `docs/DEVELOPMENT.md` 追加 §15.10x, 写根因 + 实测数据

## 常用命令

```bash
# 环境自检(换机/新会话第一条命令)
python3 scripts/env_check.py
# 推送(github.com:443 在本沙箱不可达, 走 API; 一次一个 squash 提交)
python3 scripts/push_api.py -m "fix(xxx): ..." 
# 部署状态
python3 scripts/deploy_status.py
# 本地直连测试
python3 scripts/local_api.py GET /health
# 前端构建
cd frontend && npx tsc --noEmit && npm run build
# 本地回归(注意目录)
cd cloud-functions/api && python3 local_test.py
# git 提交(消息含特殊字符时用文件)
cat > /tmp/msg.txt <<'EOF' ... EOF && git commit -F /tmp/msg.txt
```

## 已知遗留(截至 2026-11-02)

- ~~低库存卡 5 行全落郑州仓~~ **已修**: 卡面改「按仓轮转 + 轮内 SKU 去重」——第一轮每仓 1 条,
  轮内跳过已上卡的 SKU; 不满 5 条继续第二轮(3 个仓 = 3+2, 仍是 5 行)。
  **不要再做"每仓最多 1 条"的硬配额**(仓少时卡面塌成 3 行), **也不要只按仓轮转**
  (各仓头一条是同一个 SKU, 会变成同 SKU × 5 仓)。见 `docs/DEVELOPMENT.md` §15.108
- ~~bbcc 模式低库存卡空白(标题 160 条 / 卡面 0 行)~~ **已修(2026-11-02)**: bc 行 id 比 platform 行
  小 6 万, 混在 `ORDER BY id DESC LIMIT 300` 里被整体挤出窗口 → low_stock 拆成单独查询。
  别再"从截断样本推断全量"(11-01 就误判成"库里没有 bc 行")。见 `docs/DEVELOPMENT.md` §15.108 第三节
- ~~bbcc 卡面显示 C 仓(应为 BC 合计)~~ **已修(2026-11-02)**: `lowStockAlerts = fullAlerts || alertsList`
  这个"数据源优先级"把 aux 已按模式筛好的结果顶掉了; 仓型过滤上提到 lowStockAlerts 这一层,
  卡面与弹窗共用一份数据。bbcc 只有 `__BC__` 一个桶, **轮转退化为顺序取 5 条(无害无功)**,
  轮转真正服务 traditional 的 7 仓。见 §15.109
- 大卡弹窗为全量列表(自有仓 90 条全展示), `maxHeight:70vh` 滚动承接
- ~~cron 对公网开放~~ **已加固(2026-11-02)**: EdgeOne schedules 无 headers 字段 → **无法给平台调度注入凭据**,
  所以改守"执行次数"(`_throttle` 进程内限频 + `_claim` 当日抢占)。**archive 失败刻意不回滚**
  (逐条累加非幂等, 重跑会把金额算两遍), 其余可回滚。见 §15.110 第一节
- ~~连接池上限形同虚设~~ **已修(2026-11-02)**: `q.qsize()` 数的是空闲不是总数 → 并发 60 建 60 条;
  改 `BoundedSemaphore`, 压测 60 并发只建 10 条。见 §15.110 第二节
- ~~traceback 回传浏览器~~ **已修**: 主通道是 `traced` 装饰器(所有路由都套), 不是全局 handler。
  现在只回 request_id, 堆栈写 quality_logs。见 §15.110 第三节
- **`t=Date.now()` 不要简单删掉** —— 它保证数据实时性(用户明确要求)。改法是时间窗分桶
  `Math.floor(Date.now()/15000)`: 新鲜度与轮询周期等同, 同窗口内重复请求自动合并。见 §15.110 第五节
- 审计待办: S1(pipeline_runs/告警护栏/渠道去硬编码) → S2(on_data_changed + 增量重建) → S3(页面拆分)
- ~~lockfile 缺 esbuild 平台包~~ **已修(2026-11-02)**: `npm install --package-lock-only` 补齐 22 个
  `@esbuild/*` 可选包, 删除 0 / 版本漂移 0, `npm ci` 真实执行通过。见 §15.109 第三节

## 用户偏好

- 中文交流; 决策由用户拍板时, 选项要带推荐与理由
- 痛点出现时要求"彻底根治", 不接受前端兜底糊弄; 要敢报"我判断错了"并回滚
- UI 必须先完整读 `docs/UI_DESIGN.md` 再动手; 实测(浏览器量尺寸)优先于推断
