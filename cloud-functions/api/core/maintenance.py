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

## 为什么不放到 Makers schedules

平台 schedules 实测**从未自动触发**(见 `_daily_rules_guard` 的注释), 且 `edgeone.json`
在公开仓库、payload 不支持环境变量插值 —— "让平台带密钥来"在物理上做不到。
所以维护只能由应用层自己找时机。**"找时机"不等于"在用户的请求里做"** —— 本文件就是把
这两件事拆开的那个点。

> ⚠️ 后台线程在 Makers 上可能随实例冻结而中断。这不是问题: 维护全是**幂等**的,
> 下次有人访问就再来一次; 而"这次没维护"最多让数据晚几分钟追平(维护本来就不是实时性来源,
> 实时性靠写操作后的 `invalidate` 域失效)。
"""

import threading
import time

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
    # ① 数据追平(orders → 快照 → 日聚合; 含 60 天窗口完整性自愈)
    try:
        from routes.dashboard import _track_data
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
