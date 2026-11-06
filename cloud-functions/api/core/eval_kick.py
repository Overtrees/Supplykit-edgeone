"""
评估联动任务的**事件驱动**推进(2026-11-06)

## 为什么需要这个文件

导入完成后会创建 `sync_tasks(task_id='<导入id>_eval', task_type='cleansing_eval')`,
把全量 SKU 写进 `params.sku_list`, 靠**分片续跑**逐个 SKU 评估(每片 ≤90s, 防网关 120s 掐断)。
问题在于:**谁去推它?**

原来只有三条路径, 全部都不是"导入完就自动跑":

| 路径 | 真实行为 |
|---|---|
| 前端 `taskWatch` 看守 | 只在**任务页/App 首次挂载**时 `adoptRunning(/tasks?limit=8)` 才接管。用户导完就切走或关页 → 没人推 |
| `taskWatch` 的 `kind` 判定 | `task_type==='seed' ? 'seed' : 'cleansing'` —— `cleansing_eval` 落到 `'cleansing'` 分支, 打的是 `/cleansing/status?task_id=<x>_eval`, 而那个端点只认 `task_type='cleansing'`, 查不到 `_eval` 行 → 每轮 `{ok:false,error:"任务不存在"}`。**看守看得见它、却永远推不动它** |
| `continue_tasks` 兜底 | 中间件每 60s / `/health` 保活 / 平台 05:05 cron 才跑; 且只处理 `status='running' LIMIT 20` |

后果(线上实证): 低库存告警的 `created_at` 不是导入时刻, 而是散落在 12:58 / 13:06 / 13:13
几个**互不相邻**的批次 —— 每次都是"有人碰了兜底"才补一批。用户感知就是
「评估要靠兜底或手工才会跑, 不是导入就联动」。

## 做法

不做新机制, 只把**已有**的后台消费范式(`rules._drain_eval_tasks`)复用过来:
导入 done 分支写完 `_eval` 任务后, `kick_eval_task()` 立刻起一个 daemon 线程本地推进完。
线程内直接调 `cleansing_eval_status(...)` —— 就是前端看守打的那个端点, **同一条代码路径**,
所以幂等/租约/分片/降级行为完全不变, 不存在"两套评估逻辑漂移"。

线程是**尽力而为**(Makers 实例请求后可能冻结), 所以:
  · 线程跑完 → 任务 done, 前端下次进场看到的就是 done, 不需要它参与;
  · 线程被冻结 → 任务仍 running, `continue_tasks` / 看守 / 手工照旧能接上(零回归)。
也就是说: 事件驱动是**主路径**, 兜底退化为真正的"保底", 而不是唯一路径。

红线 A: 单函数 <80 行, 逻辑独立成文件; 调用侧只留一行 `kick_eval_task(...)`。
"""

import threading
import time

# 单个评估任务在一个线程里最多推进多少片。每片 page_size 个 SKU。
#   超出的部分**不丢**: 线程退出时任务仍 running, 由 continue_tasks/看守接续。
#   设上限是为了不让一个超大导入把实例钉在这条线程上(其它请求还要用同进程)。
_MAX_SEGMENTS = 60

# 同时只允许一个评估推进线程(多次连续导入不堆线程)
_KICK_LOCK = threading.Lock()
_KICK_ON = {"v": False}


def kick_eval_task(task_id, target="", channel=""):
    """导入 done 后调用: 立即在后台把 `_eval` 任务推进到完成(不阻塞本次导入响应)

    :param task_id: **导入任务** id(`_eval` 后缀由本函数补)
    :param target : 导入类型(仅用于日志)
    :param channel: 渠道(仅用于日志)
    """
    if not task_id:
        return False
    with _KICK_LOCK:
        if _KICK_ON["v"]:
            # 已有推进线程在跑: 它退出前会自己扫一遍 pending 的 running 任务
            return False
        _KICK_ON["v"] = True
    try:
        threading.Thread(
            target=_drain_one, args=(str(task_id) + "_eval", target, channel), daemon=True
        ).start()
        return True
    except Exception:
        with _KICK_LOCK:
            _KICK_ON["v"] = False
        return False


def _drain_one(eval_task_id, target="", channel=""):
    """把一个 `_eval` 任务分片推到 done / 到本线程额度用完为止"""
    try:
        from routes.cleansing import cleansing_eval_status
        from db import one as _one

        page_size = 500
        for _i in range(_MAX_SEGMENTS):
            row = _one("SELECT status FROM sync_tasks WHERE task_id=%s", [eval_task_id])
            if not row:
                break
            st = str(row.get("status") or "")
            if st in ("done", "error"):
                break
            t0 = time.time()
            try:
                # driver=event: 任务卡上能区分"导入自动联动"与"前端看守/后台兜底"
                cleansing_eval_status(eval_task_id, page_size=page_size, driver="event")
            except Exception as _e:
                try:
                    from routes.common import try_err
                    try_err("cleansing", "评估联动事件推进降级[%s]" % eval_task_id, _e)
                except Exception:
                    pass
                break
            # 单片耗时异常短(抢不到租约) 就别硬转 —— 让兜底/看守接
            if time.time() - t0 < 0.2:
                break
    except Exception as _e:
        try:
            from routes.common import try_err
            try_err("cleansing", "评估联动事件推进异常[%s/%s]" % (target, eval_task_id), _e)
        except Exception:
            pass
    finally:
        with _KICK_LOCK:
            _KICK_ON["v"] = False
        # 退出窗口内又有新 `_eval` 任务在 running(用户连点了两次导入) → 接上
        try:
            from db import one as _one2
            nxt = _one2(
                "SELECT task_id FROM sync_tasks WHERE task_type='cleansing_eval' "
                "AND status='running' ORDER BY id DESC LIMIT 1"
            )
            if nxt and nxt.get("task_id") and nxt.get("task_id") != eval_task_id:
                kick_eval_task(str(nxt.get("task_id"))[:-5], target, channel)
        except Exception:
            pass
