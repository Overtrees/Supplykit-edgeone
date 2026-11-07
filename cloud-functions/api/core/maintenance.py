"""
维护与请求的隔离层(2026-11-06)

## 为什么要有这个文件

看板 `/dashboard/summary` 的缓存 miss 分支里原来塞着四件与"回答用户问题"无关的事
(数据追平 / 每日维护 / 规则守卫 / 健康快照写入)。线上实测 `查询 1.21s + 组装 0.21s = 1.42s`
但接口 5.17s —— **73% 的响应时间不在业务计算里**。

这就是"性能优化做过很多轮、却反复退化"的机制:
优化的是**计算**(减少 SQL 往返、收缩投影列, 见 DEVELOPMENT §15.120),
而**维护量**会随功能增加重新长回请求路径。计算优化得再好, 也抵不住一条
`_rebuild_day_agg(90)` 随机插在用户的冷算上。**两笔账必须分开放。**

## 隔离原则(四条, 都不许破)

1. **请求路径零维护**: 取数 + 组装之外什么都不做。维护只"提个醒", 不做。
2. **单飞**: 同渠道同一时刻只有一个维护线程(进程内锁)。多实例由既有原子抢占兜
   (`maintenance_log` 主键 / 库存指纹 UPDATE ... WHERE), 不需要再加分布式锁。
3. **限频**: 进程内每渠道 N 秒最多起一次线程 —— 不给实例留常驻负担。
4. **失败不外溢**: 维护失败只落 `quality_logs`, 绝不影响本次响应, 也绝不回滚业务数据。

## 数据四维在这里怎么成立的(逐条, 不是口号)

把维护挪到后台,**必须先证明"晚几分钟"不会让用户看到错数**。三条分工不同:

| 事项 | 能不能异步 | 依据 |
|---|---|---|
| **实时性(写后可见)** | 不靠维护 | 靠 `invalidate("orders")` 域失效: 导入/删单 → 缓存打墓碑 → 下个请求必 miss 必重算。维护挪走不影响这条路 |
| **准确性(不读半成品)** | 可以 | `_drain_agg_dirty` 未清空时 `agg_dirty` 保持置位, 看板**降级直查**(慢但准)。半成品永远读不到 —— 见 `analysis_cache._drain_agg_dirty` 的注释 |
| **完整性(不丢天)** | 可以 | 维护本身幂等, 且 cron(05:05)/`continue_tasks`/写后 `_drain_agg_dirty` 都是**独立**触发源, 不依赖本文件。本文件只是"让它在有人访问时也早点跑" |
| **可靠性(失败有痕)** | 可以 | 每步独立 try + `try_err` 落 `quality_logs`; 前置的原子抢占标记在失败时回滚, 下次重试 |

**反例(不该异步的, 已经改回来)**: 健康快照 `health_snapshot` 的单行 upsert。
它是"今天此刻的健康分", daemon 线程被冻结就永久缺失当天数据, 且读侧会静默跳过 ——
所以它是**同步**的(见 `dashboard_health._sync_health_snapshot_async` 的说明)。
判据不是"快不快", 而是 **"丢这一次, 有没有别的地方能补回来"**: 能补 → 可异步; 不能 → 必须同步。

> ⚠️ 后台线程在 Makers 上可能随实例冻结而中断。这不是问题: 维护全是**幂等**的,
> 下次有人访问就再来一次; 而"这次没维护"最多让数据晚几分钟追平(维护本来就不是实时性来源,
> 实时性靠写操作后的 `invalidate` 域失效)。
"""

import threading
import time
import uuid

# 每渠道维护限频(秒)。60s 与中间件续跑同量级: 足够让维护及时跟上, 又不给实例留常驻负担。
_THROTTLE_S = 60

_last = {}          # channel -> 上次起线程的时间戳
_locks = {}         # channel -> Lock(单飞)
_guard = threading.Lock()


def _lock_for(channel):
    with _guard:
        lk = _locks.get(channel)
        if lk is None:
            lk = threading.Lock()
            _locks[channel] = lk
        return lk


# ── 当日评估占位(三态) ─────────────────────────────────────────────────────────
# maintenance_log(date+task) 的 details 同时承担"今天评估到哪了"的职责。三种取值:
#   ''                  待跑(从未跑 / 上次失败已回滚)
#   '#run#<fp>#<token>'  某实例正持有; created_at 超过 _RUN_STALE_MIN 仍未落地 = 该实例被
#                        平台冻结(收尾语句永远不执行), 允许别的实例抢回来重跑
#   '<fp>'               已完成, 依据为该指纹
# 为什么不能"开始前就写指纹"(2026-11-06 线上 5.5 小时空窗的根因): 写着写着实例被冻结,
# 指纹却已经是"最新依据", 之后每次请求都判定"今天已按此依据评估过"直接返回 —— 告警停在
# 11:37, 而库存/订单早已变了。
_RUN_MARK = "#run#"
_RUN_STALE_MIN = 20


def inventory_fingerprint():
    """库存指纹 = 行数 | 正可用行数 | 可用量总和(整数)

    为什么用业务量而不是时间戳: `inventory` 没有 updated_at 列, 而导入/改量/删行都会改变
    这三元组; ROUND 收敛避免浮点抖动导致反复重评估。
    :return: 指纹字符串; '' 表示查不到(调用方应放弃本轮 —— 空串会和"待跑"状态撞车)
    """
    try:
        from db import one
        r = one("SELECT COUNT(*) AS c, "
                "SUM(CASE WHEN available_qty>0 THEN 1 ELSE 0 END) AS p, "
                "IFNULL(ROUND(SUM(available_qty)),0) AS s FROM inventory") or {}
        return "%s|%s|%s" % (int(r.get("c") or 0), int(r.get("p") or 0), int(r.get("s") or 0))
    except Exception:
        return ""


def claim_rules(fp, task="daily_rules"):
    """当日评估原子抢占(三态 CAS)。返回 (是否抢到, 令牌)。

    抢到的语义是"由**你**执行本次评估", 令牌用于结束时结算 —— 只有还持有令牌的实例
    才能把状态改成"已完成", 避免把别人正在跑的那次覆盖掉。
    表异常时放行执行(与 `cron._claim` 同一取舍: 宁可多跑一次, 也不要护栏自己出错导致整天空转)。
    """
    if not fp:
        return False, ""
    token = "%s%s#%s" % (_RUN_MARK, fp, uuid.uuid4().hex[:8])
    try:
        from core.clock import now_cn
        from db import execute, one
        today = now_cn().strftime("%Y-%m-%d")
        row = one("SELECT IFNULL(details,'') AS d FROM maintenance_log "
                  "WHERE `date`=%s AND task=%s", [today, task])
        d = str((row or {}).get("d") or "")
        _CAS = ("UPDATE maintenance_log SET details=%s, created_at=NOW() "
                "WHERE `date`=%s AND task=%s AND IFNULL(details,'')=%s")
        if not row:
            got = execute("INSERT IGNORE INTO maintenance_log(`date`, task, details) "
                          "VALUES(%s, %s, %s)", [today, task, token])
        elif d == fp:
            return False, ""      # 今天已按当前依据评估完成
        elif d.startswith(_RUN_MARK):
            # 有人持着: 未超时让路; 超时说明它被冻结了, 抢回来重跑。
            # INTERVAL 的量必须字面量(PyMySQL 把 int 参数转字符串, %d 会在运行期炸)。
            got = execute(_CAS + " AND created_at < DATE_SUB(NOW(), INTERVAL "
                          + str(int(_RUN_STALE_MIN)) + " MINUTE)", [token, today, task, d])
        else:
            got = execute(_CAS, [token, today, task, d])   # '' 待跑 / 旧指纹(依据已变)
        return (bool(got), token if got else "")
    except Exception as _e:
        from routes.common import try_err
        try_err('maintenance', '评估占位抢占失败(放行执行)', _e)
        return True, token


def settle_rules(token, fp, task="daily_rules", ok=True):
    """结算: 成功 → 落指纹(下次同依据直接跳过); 失败 → 置 ''(下次请求可重跑)

    必须是条件更新(`AND details=<令牌>`): 跑的过程中令牌可能已被超时抢占者换掉,
    此时不该覆写 —— 否则会把"正在重跑"错标成"已完成"。
    """
    if not token:
        return 0
    try:
        from core.clock import now_cn
        from db import execute
        today = now_cn().strftime("%Y-%m-%d")
        if ok and fp:
            return int(execute(
                "UPDATE maintenance_log SET details=%s WHERE `date`=%s AND task=%s "
                "AND IFNULL(details,'')=%s", [fp, today, task, token]) or 0)
        return int(execute(
            "UPDATE maintenance_log SET details='' WHERE `date`=%s AND task=%s "
            "AND IFNULL(details,'')=%s", [today, task, token]) or 0)
    except Exception as _e:
        from routes.common import try_err
        try_err('maintenance', '评估占位结算失败', _e)
        return 0


EVAL_STALE_MIN = 10          # 评估任务/运行流水的新鲜度阈值(分钟), 跨模块唯一真源
_STALE_MIN = EVAL_STALE_MIN   # 兼容既有 close_stale_runs(minutes=...) 默认值


def eval_live_cond():
    """「评估任务仍在跑」的唯一判定(读侧文案 / 状态接口 / 写侧任务合并共用)

    2026-11-07 §15.158: 实例可被平台随时冻结, daemon 线程的 done/error 回写不保证执行。
    于是 `status='running'` 不等于「在跑」——必须带新鲜度: pending 或最近 _STALE_MIN
    分钟内才开始的 running 才算。超时 running 一律视为僵户, 由 close_stale_runs 留痕。

    为什么不放进各路由各自写一遍: 三处口径一旦漂移(比如一处放宽到 30 分钟), 就会出现
    「看板说在算、胶囊说算完了、保存规则却复用僵户 id」这类互相打架的状态。
    """
    return ("(status='pending' OR (status='running' AND "
            "started_at > DATE_SUB(NOW(), INTERVAL %d MINUTE)))" % EVAL_STALE_MIN)


def close_stale_runs(minutes=_STALE_MIN):
    """把挂住的 `running` 收尾成 `timeout` —— 否则"正在跑"会永远成立

    为什么必须有(2026-11-06 S4): `pipeline_runs` 的 running 是"开始时 INSERT 一行、
    结束时 UPDATE 那一行"表示的。实例被平台冻结/掐断时那条 UPDATE 永远不会执行 ——
    线上实证: `rule_eval(jd)` 11:37 那条至今仍是 running(5 小时), 于是:
      · /health 看着"有任务在跑", 而告警表已经几小时没更新(排障时会往错的方向查);
      · 任何"以 running 为单飞判据"的检查会永远成立 → 之后每次评估都被自己挡住。

    只做一件事: 超时留痕(status=timeout + error 说明)**不重跑、不动业务数据**。
    要不要重跑由调度决定 —— 收尾语句失败不能被当成"任务还在跑", 也不能顺手替它重跑。

    只动 `pipeline_runs`(纯观测表)。`sync_tasks` 有状态机与前端的进度轮询, 把它改 timeout
    会让前端把"失败"当"进行中"继续转圈, 那是另一个问题(S5 导入链路一起处理)。
    """
    out = {"runs": 0}
    try:
        from db import execute as _ex
        # INTERVAL 的量**不能**用占位符(MySQL/TiDB 只认字面量) —— 这里是自己算出来的整数,
        # 直接内联(不是拼接外部输入)。2026-11-06 被 SQL 冒烟抓到: 占位符写法运行期必 1064。
        _m = int(minutes)
        out["runs"] = int(_ex(
            "UPDATE pipeline_runs SET status='timeout', finished_at=NOW(), "
            "error=CONCAT('挂起超时: running 超过 %d 分钟(实例被冻结/掐断, 收尾语句未执行)') "
            "WHERE status='running' AND started_at < DATE_SUB(NOW(), INTERVAL %d MINUTE)" % (_m, _m)
        ) or 0)
        # 2026-11-07 §15.158: eval_tasks 是同一类「实例被冻结就没人收尾」的表, 但 S4 当时
        #   只收了 pipeline_runs(纯观测表)。线上实证: 13:21 那条 running 卡 3 小时 →
        #   aux.evaluating 恒 true → 看板「规则重算中」永久挂着; 且 _schedule_rule_eval 的
        #   合并语义会一直复用这个僵户 id, 之后保存规则再也不产生新评估。
        #   同阈值、同一次收尾、同样只留痕不重跑(要不要重跑由调度决定)。
        out["eval_tasks"] = int(_ex(
            "UPDATE eval_tasks SET status='timeout', finished_at=NOW(), "
            "error='stale running: instance frozen (no done/error writeback)' "
            "WHERE status='running' AND started_at < DATE_SUB(NOW(), INTERVAL %d MINUTE)" % _m
        ) or 0)
        if out["runs"]:
            try:
                _ex("INSERT INTO quality_logs(log_type, level, message, source) "
                    "VALUES('pipeline_stale','warning',%s,'maintenance')",
                    ("收尾 %d 条挂起运行(>%s 分钟): 实例被冻结/掐断时收尾语句不会执行"
                     % (out["runs"], minutes),))
            except Exception:
                pass
    except Exception as _e:
        from routes.common import try_err
        try_err('maintenance', '挂起运行收尾降级', _e)
    return out


def kick(channel="jd"):
    """请求路径调用: 只判断"要不要起后台线程", 立即返回(不阻塞、不抛)

    :return: True 表示本次真的起了线程(便于测试/诊断); False 表示被限频或已在跑
    """
    try:
        now = time.time()
        with _guard:
            prev = _last.get(channel) or 0
            if now - prev < _THROTTLE_S:
                return False
            _last[channel] = now
        lk = _lock_for(channel)
        if not lk.acquire(blocking=False):
            return False   # 已在跑(单飞)

        def _run():
            try:
                run(channel)
            finally:
                lk.release()

        threading.Thread(target=_run, daemon=True).start()
        return True
    except Exception:
        # 连起线程都失败 → 不动业务, 下次访问再来
        return False


def run(channel="jd"):
    """真正的维护体(幂等)。顺序有意如此: 先追平数据, 再谈规则/快照。

    每一步独立 try: 前一步失败不许连坐后一步(维护是尽力而为)。
    """
    from routes.common import try_err
    # ⓿ 先把挂住的 running 收尾 —— 它是纯观测表, 但"永远 running"会让排障与单飞判断走错方向
    try:
        close_stale_runs()
    except Exception as _e:
        try_err('maintenance', '挂起运行收尾降级', _e)
    # ① 数据追平(orders → 快照 → 日聚合; 含 60 天窗口完整性自愈)
    #    ⚠️ 导入路径必须是 dashboard_maintain —— 2026-11-06 拆分时 _track_data 从
    #    dashboard.py 迁到了 dashboard_maintain.py, 这里原本还指向旧模块, 结果是
    #    ImportError 被 except 吞成一条 quiet_error: 维护线程每 60s 起一次、每次都
    #    在第一步就失败 → "请求路径已清空"看起来成立, 实际追平从未执行。
    #    这类错误只有在线上日志里才看得见(单测 import 的是函数名, 不校验来源)。
    try:
        from routes.dashboard_maintain import _track_data
        _track_data(channel, None)
    except Exception as _e:
        try_err('dashboard', '后台维护-数据追平降级', _e)
    # ② 每日维护(digest 预填 / 日志治理 / 导出清理 / 日志归档) —— 内部已按天原子抢占
    try:
        from routes.dashboard import _daily_maintenance
        _daily_maintenance(channel)
    except Exception as _e:
        try_err('dashboard', '后台维护-每日例程降级', _e)
    # ③ 规则守卫(库存指纹变化才真的评估; 内部自己起线程)
    try:
        from routes.dashboard import _daily_rules_guard
        _daily_rules_guard()
    except Exception as _e:
        try_err('dashboard', '后台维护-规则守卫降级', _e)
