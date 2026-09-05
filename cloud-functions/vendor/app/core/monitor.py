"""轻量级 APM 监控 — 内存聚合请求统计，慢请求持久化告警"""
import time, threading, logging
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger("monitor")

# 内存统计（每分钟重置）
_stats = {
    'total': 0, 'errors': 0, 'slow': 0, 'total_time': 0.0,
    'paths': defaultdict(lambda: {'count': 0, 'total_time': 0.0, 'errors': 0}),
    'start_time': time.time(),
    'minute_start': time.time(),
}
_lock = threading.Lock()


def record(path: str, duration: float, status: int):
    """记录一次请求"""
    with _lock:
        _stats['total'] += 1
        _stats['total_time'] += duration
        if status >= 500:
            _stats['errors'] += 1
        if duration > 5:
            _stats['slow'] += 1
        p = _stats['paths'][path]
        p['count'] += 1
        p['total_time'] += duration
        if status >= 500:
            p['errors'] += 1
        # 慢请求持久化到 quality_logs（>5s）
        if duration > 5:
            try:
                from app.core.database import get_conn
                conn = get_conn()
                conn.execute(
                    "INSERT INTO quality_logs(log_type,level,message,details,source) VALUES(?,?,?,?,?)",
                    ("slow_api", "warning", f"慢请求: {path} ({duration:.1f}s)",
                     f"status={status}", "monitor")
                )
                conn.commit()
            except Exception:
                pass


def get_stats():
    """获取当前统计"""
    with _lock:
        now = time.time()
        uptime = now - _stats['start_time']
        minute_elapsed = now - _stats['minute_start']
        avg = round(_stats['total_time'] / _stats['total'], 2) if _stats['total'] else 0
        # 慢接口 TOP10
        slowest = sorted(_stats['paths'].items(), key=lambda x: -(x[1]['total_time'] / max(x[1]['count'], 1)))[:10]
        return {
            'uptime': round(uptime),
            'total_requests': _stats['total'],
            'avg_response_ms': round(avg * 1000),
            'error_count': _stats['errors'],
            'error_rate': round(_stats['errors'] / max(_stats['total'], 1) * 100, 2),
            'slow_count': _stats['slow'],
            'slowest_paths': [{
                'path': p, 'count': d['count'],
                'avg_ms': round(d['total_time'] / max(d['count'], 1) * 1000),
                'errors': d['errors'],
            } for p, d in slowest],
        }