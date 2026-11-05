"""迁移执行器: 读已应用集合 → 只跑未应用的 → 单步事务 + 登记 + 失败留待重试

**单步事务是核心**: `run()` 里的写与 `INSERT INTO schema_migrations` 同一事务提交。
- 中途异常 → 整步回滚 → 数据没改、也没登记 → 下次冷启动重试(正确);
- 若反过来(先执行后登记, 各自 autocommit) → 崩在中间就是"改了但没登记" → 重跑,
  改数据类步骤重跑可能把金额算两遍(archive 那次的教训)。

失败不阻断后续步骤: 后续大多不依赖它, 而阻断会让整个 schema 版本永远卡住、每次冷启动重跑全部步骤。
"""
import time

from db import query, execute, transaction

# 同一 step 失败这么多次后不再自动重跑(避免每次冷启动都撞同一个错、刷满日志)
MAX_ATTEMPTS = 5


def _ensure_table():
    execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id VARCHAR(64) PRIMARY KEY, "
            "applied_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6), "
            "note VARCHAR(255) DEFAULT '', "
            "status VARCHAR(16) NOT NULL DEFAULT 'applied', "
            "attempts INT NOT NULL DEFAULT 0)")


def _settle(step_id, note, status, attempt):
    """登记/见证: applied 或 failed 都落一行(唯一键 upsert)

    为什么要记 failed: 不记的话, 失败步下次会**再跑一遍** —— 而它可能已经改了一半数据
    (单步事务已回滚, 但外部副作用/幂等性差的 SQL 未必)。有了 failed 行, 重试时 runner
    先看它上次失败到第几次, `MAX_ATTEMPTS` 后转为人工介入, 不再无限重跑。
    """
    execute("INSERT INTO schema_migrations(id, note, status, attempts) VALUES(%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE note=VALUES(note), status=VALUES(status), "
            "attempts=attempts+1", [step_id, str(note or "")[:255], status, attempt])


def applied_ids():
    """已应用集合; 表不存在(首启)返回空集 —— 不 catch 其它异常, 免得把"连不上库"当成"没跑过" """
    try:
        rows = query("SELECT id FROM schema_migrations WHERE status='applied'")
    except Exception as e:
        if "doesn't exist" in str(e) or "1146" in str(e):
            return set()
        raise
    return {str(r.get("id") or "") for r in rows}


def failed_attempts():
    """{step_id: 已失败次数} —— 达到上限的步骤跳过并告警, 不再无限重跑"""
    try:
        rows = query("SELECT id, attempts FROM schema_migrations WHERE status='failed'")
    except Exception:
        return {}
    return {str(r.get("id") or ""): int(r.get("attempts") or 0) for r in rows}


def pending(reg=None):
    """未应用步骤列表(供 local_test / 运维只读查看)"""
    from migrations import registry
    done = applied_ids()
    return [s for s in (reg or registry()) if s["id"] not in done]


def run_pending(reg=None):
    """执行全部未应用步骤。返回 (applied, failed), 各自元素为 step id。

    不抛异常 —— 调用方(启动路径)只需要日志; 抛出去会让整个 app 启动失败。
    失败步登记 status='failed' + attempts; 达到 MAX_ATTEMPTS 后跳过(不无限重跑),
    由运维介入(修数据后用 `scripts/... ` 或手工把该行 status 改回 pending 再冷启动)。
    """
    from migrations import registry, load_run
    _ensure_table()
    done = applied_ids()
    fails = failed_attempts()
    applied, failed, skipped = [], [], []
    for step in (reg if reg is not None else registry()):
        sid = step["id"]
        if sid in done:
            continue
        if fails.get(sid, 0) >= MAX_ATTEMPTS:
            skipped.append(sid)
            _log('migration', '%s 已连续失败 %d 次, 跳过(需人工介入)' % (sid, fails[sid]), None)
            continue
        t0 = time.time()
        try:
            run = load_run(step)
            with transaction() as c:
                note = run()
                cur = c.cursor()
                try:
                    cur.execute("INSERT INTO schema_migrations(id, note, status, attempts) "
                                "VALUES(%s, %s, 'applied', 0) "
                                "ON DUPLICATE KEY UPDATE note=VALUES(note), status='applied'",
                                [sid, str(note or "")[:255]])
                finally:
                    cur.close()
            applied.append(sid)
            _log_ok('migration', '%s 完成 %.2fs: %s' % (sid, time.time() - t0, str(note or "")[:160]))
        except Exception as e:
            failed.append(sid)
            # 失败留见证(独立于回滚的事务): 否则下次会当成"从没跑过"。留见证失败也不能吞掉原因。
            try:
                _settle(sid, str(e)[:200], 'failed', fails.get(sid, 0) + 1)
            except Exception as e2:
                _log('migration', '%s 失败且见证写入也失败(下次将重试): %s' % (sid, e2), e2)
            _log('migration', '%s 失败(已回滚, 下次冷启动重试): %s' % (sid, str(e)[:200]), e)
    if skipped:
        _log('migration', '以下迁移达失败上限被跳过: %s' % ",".join(skipped), None)
    return applied, failed


def _log(src, what, exc):
    """延迟导入: 启动早期 routes.common 可能尚未就绪(与 schema_create 同做法)"""
    try:
        from routes.common import try_err
        try_err(src, what, exc)
    except Exception:
        pass


def _log_ok(src, what):
    """迁移/建表**成功**留痕(2026-11-05): 走 log_info(info 级)而不是 _log → try_err。

    迁移完成是正常态, 原来把空字符串当 exc 传给 try_err, 会拼出 ': str: ' 尾巴并
    落成 warning/quiet_error —— 典型的「非真异常」。真失败仍由 _log → try_err 留痕。
    """
    try:
        from routes.common import log_info
        log_info(src, what)
    except Exception:
        pass
