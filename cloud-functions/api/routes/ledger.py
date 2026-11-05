"""账期(月结)路由: 状态查询 / 结账 / 补结 / dry-run 预演

- GET  /ledger/status   当前账期状态 + 上期体检结果(前端顶部账期标识读它)
- GET  /ledger/preview  某账期 dry-run: 只算不写, 输出期初/入/出/期末与差异
- POST /ledger/close    结某账期(默认上个月); 体检不过 → 拒绝并留痕, 不结假账
- POST /ledger/reclose  补导后重结: 从某期逐月补结到上个月(期初自动级联)

口径见 core/ledger.py 顶部说明; 这里只做参数校验与结果整形。
"""
from fastapi import APIRouter

from db import query
from routes.common import ok, fail, traced, try_err
from core.ledger import (period_of, shift_period, period_bounds, period_days,
                         elapsed_days, state_get, close_check, close_period,
                         reclose, build_rows, current_period)

router = APIRouter(tags=["ledger"])

# 只有自有仓走账期; 平台仓/B仓以后台导出为准, 不参与结账
LEDGER_WAREHOUSES = ("own",)


def _p(params):
    """取账期入参, 缺省 = 上个月(自动结账的默认目标)"""
    v = str(params.get("period") or "").strip()
    if not v:
        return shift_period(period_of(), -1)
    return v if len(v) == 7 and v[4] == "-" else ""


@router.get("/ledger/status")
@traced
def ledger_status(channel: str = "jd"):
    """当前账期 + 上期可结账性。数据不齐时 ok=False + reason, 前端直接展示原因"""
    out = []
    for wt in LEDGER_WAREHOUSES:
        st = state_get(channel, wt)
        cur = current_period(channel, wt)
        target = shift_period(period_of(), -1)
        ready, why = (False, "") if not target else close_check(target, channel, wt)
        # 期初来源: carryover(上月结转) / import(窗口内实盘校准过) —— 前端顶部挂徽标,
        #   用户要能一眼看出"这期期初是算出来的还是盘出来的"(2026-11-05 期初双场景)。
        from core.ledger_adj import summary as _adj_sum
        _adj = _adj_sum(cur, channel, wt)
        out.append({
            "warehouse_type": wt, "period": cur,
            # status 描述的是 **cur 这个账期**: 只有当已结到 cur 时才是 closed,
            #   否则是进行中(open)。原样返回 st.status 会出现「period=2026-11 + closed」——
            #   那个 closed 指的是 2026-10, 读的人会以为 11 月已结(2026-11-05 实测踩到)。
            "status": (st.get("status") if st.get("period") == cur else "open"),
            "begin_date": st.get("begin_date"), "closed_period": st.get("period"),
            "reason": st.get("reason"), "closed_at": st.get("closed_at"),
            "days": elapsed_days(cur), "month_days": period_days(cur),
            "is_open": cur == period_of(),
            "target_period": target, "target_ready": ready, "target_reason": why,
            "begin_source": _adj.get("begin_source"), "begin_adj_rows": _adj.get("adj_rows"),
            "begin_adj_delta": _adj.get("adj_delta"), "begin_window": _adj.get("window"),
        })
    return ok({"channel": channel, "items": out})


@router.get("/ledger/preview")
@traced
def ledger_preview(channel: str = "jd", period: str = "", warehouse_type: str = "own"):
    """dry-run: 只算不写。结账前先看"结出来是多少、跟当前可用差多少" """
    p = period or shift_period(period_of(), -1)
    a, b = period_bounds(p)
    if not a:
        return fail("账期格式应为 YYYY-MM")
    try:
        data = build_rows(p, channel, warehouse_type)
    except Exception as e:
        try_err('ledger', '账期预演失败', e)
        return fail("预演失败: %s" % e)
    cur = query("SELECT COALESCE(SUM(available_qty),0) AS av FROM inventory "
                "WHERE channel=%s AND warehouse_type=%s "
                "AND (deleted_at IS NULL OR deleted_at='')",
                [channel, warehouse_type]) or [{}]
    avail = int((cur[0] or {}).get("av") or 0)
    return ok({"period": p, "start": a, "end": b, "rows": len(data["rows"]),
               "begin": data["beg"], "in": data["in"], "out": data["out"], "end": data["end"],
               "orphan": data["orphan"], "available_now": avail,
               "diff": data["end"] - avail, "days": period_days(p)})


@router.post("/ledger/close")
@traced
def ledger_close(params: dict = None):
    """结账(幂等: 同期重复结 = 覆盖, 永不累加)。force=1 跳过体检, 仅补结历史时使用"""
    params = params or {}
    p = _p(params)
    if not p:
        return fail("账期格式应为 YYYY-MM")
    wt = str(params.get("warehouse_type") or "own")
    if wt not in LEDGER_WAREHOUSES:
        return fail("只有自有仓走账期; 平台仓/B仓以后台导出为准")
    try:
        r = close_period(p, str(params.get("channel") or "jd"), wt,
                         force=bool(params.get("force")))
    except Exception as e:
        try_err('ledger', '结账失败', e)
        return fail("结账失败: %s" % e)
    if not r.get("ok"):
        return fail(r.get("reason") or "结账被拒绝")
    return ok(r)


@router.post("/ledger/reclose")
@traced
def ledger_reclose(params: dict = None):
    """补导后重结: 从 period 起逐月补结到上个月(当月不结), 期初自动级联"""
    params = params or {}
    p = _p(params)
    if not p:
        return fail("账期格式应为 YYYY-MM")
    try:
        out = reclose(str(params.get("channel") or "jd"),
                      str(params.get("warehouse_type") or "own"), p)
    except Exception as e:
        try_err('ledger', '补结失败', e)
        return fail("补结失败: %s" % e)
    return ok({"items": out, "ok_count": len([x for x in out if x.get("ok")])})
