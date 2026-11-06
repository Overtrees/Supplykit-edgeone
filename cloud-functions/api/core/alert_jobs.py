"""
告警同步的任务层(2026-11-06 S2-1 审计 P0-10)

## 为什么要有这个文件

`_build_purchase` 原来是"算采购建议 + 写 purchase_need 告警"二合一。于是:

  · **告警更不更新, 取决于有没有人打开采购页**。没人看 → 告警停在最后一次被浏览的
    时刻, 而看板的"采购&补货"卡显示的是接口实时算出来的数字 —— 两个东西不同步,
    用户看到的是"卡片说正常, 告警里还挂着一堆"。
  · 上一轮把缓存粒度改成一页一条之后,**每翻一页都重写一遍告警**
    (线上实证: 同一 SKU 13 条 purchase_need, 13 正好等于一次分页重算的次数)。
    计算被放大了 4~7 倍, 写库也被放大了同样的倍数。
  · 读接口带副作用之后就不敢让它被别人复用 —— 导出、内部对账、任何一次预取
    都会顺手写库, 而这些调用方根本不该承担"改告警状态"的责任。

## 做法

`_build_purchase` 只算不写(S2-1 已改), 写入收敛到本文件。触发源四个, 全部是**写侧**:

| 触发 | 为什么是这里 |
|---|---|
| 导入完成 | 库存变了 → 采购建议与"是否需采购"同时变 |
| 补货参数保存 | 提前期/MOQ/安全天数变了 → 建议量变 |
| 规则评估完成 | 评估会关闭一批告警, 采购需求要跟着重判 |
| cron 每日 | 兜底(前三个都跑不到时, 一天至少一次) |

## 为什么异步、以及异步之后四维怎么还成立

采购建议单渠道算一次 4~7s(9,684 行 + 逐 SKU 日销)。挂在导入响应里会直接顶到网关
120s 上限, 所以必须异步。

异步之后:
  · **实时性**: 采购告警不是"写后立刻要看到"的数据 —— 它由"库存+参数"推导, 而库存
    变化本身已在 `invalidate("purchases")` 里让采购页立刻重算。告警滞后几十秒不影响
    任何一次用户操作的正确性。
  · **准确性**: 同步用的是**同一次 builder 结果**(不是另算一份), 不存在"告警按 A 口径、
    页面按 B 口径"。
  · **完整性**: 四个触发源互相独立, 且 cron 每日兜底; 线程被冻结只影响这一次, 下次
    触发会全量重判(不是增量, 天生自愈)。
  · **可靠性**: 每渠道单飞 + 限频, 失败只落 `quality_logs`, 不影响业务写入。

## 红线

本文件**只能在写侧调用**。读接口(采购页/导出/看板)一律不许调 —— 那等于把副作用
又塞回读路径, 只是换了个函数名。`test_audit` 有静态断言守这条。
"""

import threading
import time

# 每渠道限频(秒): 采购建议重算 4~7s, 连续导入时不该反复重算。
#   120s 与"导入批次的自然间隔"同量级, 又远小于 cron 的日粒度。
_THROTTLE_S = 120

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


def _default_mode(channel):
    """渠道默认补货模式(bbcc/traditional)。算采购建议必须带 mode —— 它决定
    周期口径(B+C / lead+safety), 用错 mode 会让 purchase_qty 系统性偏大或偏小。"""
    try:
        from routes.common import ch_default_mode
        return ch_default_mode(channel)
    except Exception:
        return "bbcc" if channel == "jd" else "traditional"


def kick_purchase_alerts(channel="jd", mode="", days=28):
    """写侧调用: 只判断"要不要起后台线程", 立即返回(不阻塞、不抛)

    :return: True = 本次真的起了线程; False = 被限频或在跑(或起线程失败)
    """
    try:
        ch = channel or "jd"
        now = time.time()
        with _guard:
            prev = _last.get(ch) or 0
            if now - prev < _THROTTLE_S:
                return False
            _last[ch] = now
        lk = _lock_for(ch)
        if not lk.acquire(blocking=False):
            return False   # 已在跑(单飞)

        def _run():
            try:
                run_purchase_alerts(ch, mode, days)
            finally:
                lk.release()

        threading.Thread(target=_run, daemon=True).start()
        return True
    except Exception:
        return False


def run_purchase_alerts(channel="jd", mode="", days=28):
    """同步执行(供 cron 与测试直接调用): 算一次 → 同步告警。返回统计 dict。"""
    from routes.common import try_err, log_info
    ch = channel or "jd"
    md = mode or _default_mode(ch)
    try:
        from routes.purchase_calc import _build_purchase, sync_purchase_alerts
        # with_alerts=False 是**刻意的**: 这里拿结果自己同步, 不让 builder 顺手写 ——
        #   否则将来有人意外复用 builder, 副作用又回来了。
        rows = _build_purchase(ch, md, days, with_alerts=False) or []
        stat = sync_purchase_alerts(ch, rows)
        log_info("purchase", "采购告警同步[%s/%s] 建议%d条 opened=%d closed=%d"
                 % (ch, md, len(rows), stat.get("opened", 0), stat.get("closed", 0)))
        return stat
    except Exception as e:
        try_err("purchase", "采购告警同步失败[%s/%s]" % (ch, md), e)
        return {"opened": 0, "closed": 0, "error": str(e)[:120]}
