"""sync_tasks 通用租约(令牌 + 心跳 + TTL) —— 四类任务统一(2026-11-06 S5/P0-6)

## 为什么必须有

`sync_tasks` 一张表装四类长跑任务(cleansing / cleansing_eval / export / seed)。
seed 在 2026-11-05 已经换了**租约心跳**(v10/v12: lease_until + lease_token,
见 task_seed.py), 而 cleansing / cleansing_eval / export 仍在用 `updated_at` 3 秒
窗口锁:

    UPDATE sync_tasks SET updated_at=NOW() WHERE task_id=%s
      AND updated_at < DATE_SUB(NOW(), INTERVAL 3 SECOND)

这个锁有三个结构性问题(审计 P0-6):

| 问题 | 后果 |
|---|---|
| 3s 窗口 = "上一拍刚跑完就又能跑" | 前端看守 + event 推进线程 + 60s 兜底同时轮询是常态 → 同一片被执行两遍; sum 模式下增量写入**双算**(线上实证单日精确 3100 = 1550×2) |
| 无归属令牌 | 上一任的续锁语句会替下一任"续命", 两个实例并发跑却看起来"锁正常" |
| sleep(4) 等锁边界 | 刚建完任务必须睡 4 秒才能同步推进(建任务把 updated_at 写成 NOW, 3s 内抢不到) |

本模块只做三件事, 列复用 v10/v12 已迁移的 `lease_until` / `lease_token`:

    tok, ok = acquire(task_id)        # 原子抢占(lease_until < NOW() 才给)
    stop = start_beat(task_id, tok)   # 后台线程每 BEAT 秒续租(带令牌归属校验)
    ... 长跑(单片可 >TTL, 心跳兜住) ...
    stop()                            # 退出必须停拍 —— 僵尸心跳会替下一任续租
    release(task_id, tok)             # 释放(或由终态 UPDATE 顺带清)

**调用纪律**: start_beat 之后, 函数所有退出路径(含 return / 异常)都必须走到 stop()。
推荐挂在函数已有的 try/except 的 `finally` 上(不需要重排缩进); 异常路径漏 stop 由
TTL 自愈(45s 后别人能抢), 但终态(done/error)更新应顺带清 lease 列。
"""
import threading
import time
import uuid

from db import execute
from routes.common import try_err

TTL = 45     # 租约时长: 覆盖单片最坏耗时靠心跳续; 进程冻结 45s 后别人可接管
BEAT = 15    # 心跳间隔(<= TTL/2, 保证两拍失败仍不过期)


def acquire(task_id, ttl=TTL):
    """原子抢占。返回 (token, ok) —— ok=False 表示别人持有(调用方直接返回 running)"""
    tok = uuid.uuid4().hex[:24] + str(int(time.time() * 1000) % 100000)
    try:
        ok = execute("UPDATE sync_tasks SET lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND), "
                     "lease_token=%s WHERE task_id=%s "
                     "AND (lease_until IS NULL OR lease_until < NOW())",
                     [int(ttl), tok, task_id])
    except Exception as _e:
        # 列缺失(未跑迁移) → 抢不到锁比静默并发安全; 留痕让运维能发现
        try_err("tasks", "租约抢占失败(lease 列缺失?), 按'他人持有'处理", _e)
        return "", False
    return (tok if ok else ""), bool(ok)


def release(task_id, token):
    """释放(只有持有者能清 —— 令牌不匹配说明已被接管, 不要动别人的租约)"""
    if not task_id or not token:
        return
    try:
        execute("UPDATE sync_tasks SET lease_until=NULL, lease_token='' "
                "WHERE task_id=%s AND lease_token=%s", [task_id, token])
    except Exception as _e:
        try_err("tasks", "租约释放失败(TTL 自愈)", _e)


def renew(task_id, token, ttl=TTL):
    """单次续租。False = 已被接管(持有者应立即停止本片工作)"""
    try:
        return execute("UPDATE sync_tasks SET lease_until=DATE_ADD(NOW(), INTERVAL %s SECOND) "
                       "WHERE task_id=%s AND lease_token=%s", [int(ttl), task_id, token])
    except Exception as _e:
        try_err("tasks", "租约续期失败(下一拍重试)", _e)
        return False


def start_beat(task_id, token, ttl=TTL, beat=BEAT):
    """后台心跳线程(每 beat 秒续一次租)。返回 stop() —— 调用方在退出路径必须调它

    为什么用线程: 段内是纯同步 SQL 长跑(窗口写入/重算/评估), 没有 await 点可插;
    线程是唯一能覆盖整个长查询期间的方案(与 task_seed._start_heartbeat 同款)。
    令牌归属: 续租语句带 lease_token —— 上一任的心跳永远续不到下一任的租。
    """
    _stop = {"v": False}

    def _beat():
        while not _stop["v"]:
            for _i in range(int(beat * 2)):          # 0.5s 粒度响应 stop
                if _stop["v"]:
                    return
                time.sleep(0.5)
            if _stop["v"]:
                return
            renew(task_id, token, ttl)

    _t = threading.Thread(target=_beat, daemon=True)
    _t.start()

    def _stop_hb():
        _stop["v"] = True
        try:
            _t.join(timeout=1.5)
        except Exception:
            pass
    return _stop_hb
