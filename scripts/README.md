# scripts/ 索引 —— 每一类验证用哪个脚本

> 目的: **不要再临时写一次性脚本**。这里没有的, 先找最接近的改参数;
> 确实没有的, 加进来并在本表登记。
>
> 通用约定: 全部在**仓库根目录**执行; 除注明外都只读, 不写线上库。

## 一、环境与部署

| 脚本 | 用途 |
|---|---|
| `env_check.py` | 换机/新会话第一条: 缺 .env、EDGEONE token、push 凭据一次性列出 |
| `deploy_status.py` | 部署判据: `Commit == HEAD && Prod == yes && Status == Success` |
| `push_api.py` | 推代码(github:443 在沙箱不可达, 走 API, 一次一个 squash 提交) |
| `smoke_check.py` | 线上冒烟: health JSON + 首页 #root |
| `audit_secrets.py` | 提交前查密钥/明文口令泄漏 |
| `preflight.sh` | 提交前一把梭, 八步: 3.10 语法 / pyflakes / 回归+SQL冒烟 / ESLint(含禁 any) / Prettier / tsc / vitest / i18n 门禁 |
| `i18n_audit.py` | 红线 B 可执行版: 中文硬编码残留 + zh/en 键集三向校验; `--gate` 进 preflight 与 CI, `--lines` 逐条定位 |
| `i18n_extract.py` | 从源码抽 `t()` 引用, 生成/补齐字典键(写文件前人工确认) |

## 二、后端逻辑(不部署就能验)

| 脚本 | 用途 |
|---|---|
| `local_api.py` | 进程内 TestClient 打真实 app, 连 .env 的库: `GET /health`、`POST /orders/...` —— 改完立刻验, 不等部署 |
| `test_inv_recalc.py` | 库存重算口径 |
| `test_inv_idempotent.py` | 库存批量操作幂等 |
| `test_barcode_q3.py` | 条码/两码合并 |
| `cloud-functions/api/local_test.py` | **主回归**: 860 条用例。必须在 `cloud-functions/api/` 目录跑 |
| `cloud-functions/api/sql_smoke.py` | 真实 SQL 冒烟(表名/列名 mock 测不出), `local_test.py --sql-smoke` 顺带跑 |

## 三、前端/界面

| 脚本 | 用途 |
|---|---|
| `ui_shot_matrix.py` | **4 视口 × 双语全页面快照**: 查横向溢出 / 未解析词条键 / 英文界面残留中文 |
| `ui_overlap_check.py` | **单元格叠印检测**: 逐格比「文字实际宽 vs 内容区宽」, 抓文字压到邻列的情况(快照测不出) |
| `ui_note_icon_check.py` | **备注 emoji→SVG 回归**: 双语 × 4 视口检查 `td.col-note` 渲染出 `<svg>` 且不残留 emoji 字符/中文 |
| `ui_own_card_measure.py` | 自有库存卡面实测 |
| `ui_preview_measure.py` | 清洗预览页 overhead 分解 |
| `ui_preview_inject.py` | 往预览页注入测试数据 |
| `ui_preview_rotate.py` | 横竖屏切换行为 |
| `ui_check_cleansing.py` | 清洗向导流程 |
| `ui_preview_env.py` | **公共底座**(非独立脚本): build + preview + 代理 + 登录, 给上面几个复用 |

### ui_shot_matrix.py 用法

```bash
# 让它自己 build 并起服务(最省事)
python3 scripts/ui_shot_matrix.py --serve --locale both --viewport all

# 已有服务时直接连(改一行代码只想看一页时用这个, 省掉 build)
cd frontend && npx vite preview --port 4173 &
python3 scripts/ui_shot_matrix.py --locale en --viewport 375 --page dash

# 只看异常不落盘
python3 scripts/ui_shot_matrix.py --locale both --viewport all --no-shot
```

三级排查口径: **溢出=0 / 未解析键=0** 是机器判据; 但**固定宽 + overflow:hidden 的静默裁切**
机器查不出来(溢出仍是 0), 所以关键页面必须人眼看截图 —— 例如待处理卡片被裁成
"Replenish 1…" 就是溢出 0 但内容没了。

### ui_overlap_check.py 用法

```bash
python3 scripts/ui_overlap_check.py --serve --page orders --viewport 1440
python3 scripts/ui_overlap_check.py --serve --page all --viewport all --locale both
```

**为什么必须单独一个脚本**: 单元格文字溢出到相邻列时, `table.scrollWidth` **不会变大**,
它只是画在别人头上 —— 2026-11-04「订单页 69 码与订单号平行重叠」正是被快照矩阵报成
`overflowX: 0` 漏掉的。这里的判据是 `文字 Range 右沿 > 单元格内容区右沿`(padding 内侧),
命中即叠印风险, 并打印 ws / overflow / fontSize 便于直接定位成因。有命中退出码 1, 可接门禁。

## 四、i18n

| 脚本 | 用途 |
|---|---|
| `i18n_audit.py --gate` | **门禁**: 中文残留 / zh-en 键集合相等 / 死键 / 变量遮蔽 t / 缺 col 词条 / 缺占位符 / 空串假翻译 |
| `i18n_extract.py` | 抽取残留到清单(排查用, 门禁用 audit) |

出问题时的常见顺序: `i18n_audit.py --gate` → `ui_shot_matrix.py` → 人眼看截图。

## 五、写新脚本的约定

1. 界面类脚本一律 `from ui_preview_env import PreviewEnv`, 不要在脚本里重写
   build/preview/代理/登录 —— 那三处坑(见模块 docstring)重写一次就多踩一次。
2. 视口与页面清单放脚本顶部常量, 与 `App.tsx` 的 `NAV`/`switch` 对齐。
3. 新增脚本必须在本文件登记一行, 否则下一个人还是找不到。

## 六、性能基准与压测

| 脚本 | 用途 |
|---|---|
| `perf/run.py` | **统一入口**: `hot`(快了多少) / `storm`(会不会停摆) / `cold`(重建分段) / `first`(真首建) / `noise`(测量可信度); 判据在文件头 `TH` |
| `perf/endpoints.py` | 页面→模块→接口→数据来源→机制的唯一真源; `run.py --list-md` 生成 `docs/PERF.md` 的表格 |
| `perf/smoke.py` | **只读冒烟**：DB 额度耗尽/接口异常时判定失败面（含 200+全 0 的静默空壳）；后端降级时打 `[DEGRADED]`（`X-App-Degraded` 头）；见 `docs/PERF.md` §8 |
| `perf/scenes.py` `perf/kit.py` | 场景实现 / 登录态与相位工具 |

详见 [`perf/README.md`](perf/README.md) 与 [`docs/PERF.md`](../docs/PERF.md)。
**注意**: 带写压测的 p50 不能用来判断"快了多少"（同配置轮间极差 22 倍），判延迟只用 `hot`。
