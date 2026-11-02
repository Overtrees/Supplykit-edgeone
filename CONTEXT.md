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
- **部署判据**: `scripts/deploy_status.py` 输出 `Commit == 本地 HEAD && Prod == yes && Status == Success` 才算部署完成
- **先看数据源实际返回什么**(拦截响应看真实分布), 再决定改前端还是后端 — 不要拿前端兜底掩盖口径问题
- **本地回归**: `cd cloud-functions/api && python3 local_test.py` → 当前 205 通过/0 失败
- **多视口 UI 验证**: Playwright 实测, 参考 `scripts/ui_own_card_measure.py`; 视口 375/390/844/768/1024/1440
- **文档同步**: 每项改动在 `docs/DEVELOPMENT.md` 追加 §15.10x, 写根因 + 实测数据

## 常用命令

```bash
# 环境自检(换机/新会话第一条命令)
python3 scripts/env_check.py
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

## 已知遗留(截至 2026-11-01)

- 低库存卡 5 行当前全落郑州仓(接口 id DESC + 最新告警同仓), 用户接受现状;
  如需多仓覆盖可改"每仓最多 1 条"配额
- 大卡弹窗为全量列表(自有仓 90 条全展示), `maxHeight:70vh` 滚动承接

## 用户偏好

- 中文交流; 决策由用户拍板时, 选项要带推荐与理由
- 痛点出现时要求"彻底根治", 不接受前端兜底糊弄; 要敢报"我判断错了"并回滚
- UI 必须先完整读 `docs/UI_DESIGN.md` 再动手; 实测(浏览器量尺寸)优先于推断
