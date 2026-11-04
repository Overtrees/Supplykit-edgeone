"""统一业务时区 = 北京时间(UTC+8)

为什么必须有这一个模块:
- 云函数进程时区是 UTC, TiDB Serverless 会话时区默认也是 UTC;
- 而 BusinessKit 里所有业务日期(订单下单日、出入库流水日、月份边界、"距今天数")
  都是**北京时间**的自然日;
- 直接用 `datetime.now(timezone.utc)` 会在北京时间 00:00–08:00 之间把"今天"算成昨天、
  把"本月"算成上月 —— 月初尤其致命(1 号 00:00–08:00 的周转/月区间会整体落到上月)。

所以:**任何"今天/本月/距今几天"的判断都必须走这里**, 不要再直接 now(utc)。
SQL 侧由 db.py 连接时 `SET time_zone='+08:00'` 保证 NOW()/CURRENT_TIMESTAMP 同为北京时间。
"""
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))


def now_cn():
    """当前北京时间(aware datetime)"""
    return datetime.now(CN_TZ)


def today_cn():
    """当前北京时间日期字符串 YYYY-MM-DD"""
    return now_cn().strftime("%Y-%m-%d")


def month_bounds_cn(dt=None):
    """当前北京时间月份的自然月区间: (YYYY-MM-01, YYYY-MM-<月末>)

    月末按真实日历取(2 月 28/29, 30/31 天), 不用"今天", 也不用"流水最后一天"。
    """
    d = dt or now_cn()
    last = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return d.strftime("%Y-%m-01"), last.strftime("%Y-%m-%d")


def month_days_cn(dt=None):
    """当前北京时间月份的自然月天数(11 月 30 / 12 月 31 / 2 月 28-29)"""
    d = dt or now_cn()
    last = (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return last.day


def elapsed_days_cn(dt=None):
    """当前账期**已过**的自然日数(= 今天几号)

    周转的分母只统计"本月已发生"的出库, 分子必须用同一个窗。用整月天数乘上去,
    月初会把 4 天的销量当成 30 天用, 周转虚高数倍(2026-11-04 实测约 7 倍)。
    月末最后一天 day == 整月天数, 两者自然重合, 不需要特判。
    """
    return (dt or now_cn()).day


def days_since_cn(date_str):
    """date_str(YYYY-MM-DD, 业务日期) 距北京时间今天的自然日天数; 空/非法返回 None"""
    s = str(date_str or "")[:10]
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (now_cn().date() - d).days
