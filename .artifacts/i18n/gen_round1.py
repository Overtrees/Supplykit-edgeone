#!/usr/bin/env python3
"""本轮新增词条(导航/仓库/效期/订单状态/清洗/规则名) —— 追加进 locale.zh/en。"""
import os

ZH = {
    # 通用
    "common.n_records": "{n} 条记录",
    "common.back": "返回",
    "common.retry_hint": "网络异常或服务不可用",
    "common.refresh_page": "刷新页面",
    "common.retry_again_hint": "连续重试未恢复，建议刷新页面（重新连接服务）",
    "common.load_failed": "加载失败",
    "common.network": "网络波动",
    "common.processing": "处理中...",
    "common.ok": "正常",
    "common.none": "无",
    "common.append": "另有",
    "common.days": "天",
    "common.day_unit": "{n} 天",
    # 锤子菜单
    "hammer.pending": "功能待添加",
    "hammer.title": "操作",
    "hammer.close": "收起",
    # 仓库类型 / 效期档位 / 订单状态
    "inv.exp.ok": "正常",
    "inv.exp.ok_icon": "✓ 正常",
    "inv.exp.warn": "临近",
    "inv.exp.warn_icon": "⚠️ 临近",
    "inv.exp.no": "否",
    "inv.exp.no_icon": "✗ 否",
    "inv.exp.expired": "过期",
    "inv.exp.expired_icon": "⚫ 过期",
    "inv.exp.over_third": "已超1/3",
    "inv.exp.over_third_icon": "✗ 已超1/3",
    "inv.exp.expired_short": "已过期",
    "inv.use.half_icon": "⚠️ 已消耗过半",
    "inv.use.mid_icon": "🟡 消耗中",
    "inv.use.normal_icon": "🟢 正常消耗",
    "inv.use.fresh_icon": "⚪ 刚入库",
    "inv.act.slow": "动销放缓，促动销",
    "inv.act.normal": "正常销售",
    "inv.act.promo": "促销去库/调拨",
    "inv.act.clear": "尽快清仓/退供",
    "inv.act.scrap": "报废/退供应商",
    "oms.st.done": "已完成",
    "oms.st.to_ship": "待发货",
    "oms.st.shipped": "已发货",
    "oms.st.to_confirm": "待确认",
    "oms.st.refund": "申请退款",
    # 进销存页
    "inv.title": "进销存",
    "inv.loaded": "已加载 {a}/{b} 条 · 显示 {c}/{d} 列",
    "inv.refresh_now": "立即刷新",
    "inv.try_other_kw": "换个关键词试试",
    "inv.import_hint": "通过清洗导入库存数据",
    "inv.go_import": "去导入数据 →",
    "inv.batch_badge": "⤵ 批次",
    "inv.batch_loading": "批次加载中…",
    "inv.batch_failed": "批次加载失败，请收起后重新展开",
    "inv.total": "合计",
    "inv.all_loaded": "已加载全部 {n} 条",
    "inv.channel_other": "其他",
    "inv.channel_jd": "京东",
    "inv.load_fail_keep": "刷新失败，当前显示上次数据",
    "inv.delete_failed": "删除失败: {msg}",
    "inv.archived_miss": "已归档 {n} 条, {m} 条已不存在",
    "inv.archived_ok": "已归档 {n} 条 · 可在回收站恢复",
    "inv.not_found": "未找到 {sku}，可能不在当前仓库维度或已无库存",
    # 清洗
    "clean.mapping": "列映射",
    "clean.mapping_desc": "选择文件列对应的目标字段 · 未映射列导入时丢弃",
    "clean.suggest": "智能建议",
    "clean.required_suffix": "(必)",
    "clean.no_map": "-- 不映射 --",
    "clean.title": "数据清洗及导入",
}

EN = {
    "common.n_records": "{n} records",
    "common.back": "Back",
    "common.retry_hint": "Network issue or service unavailable",
    "common.refresh_page": "Reload page",
    "common.retry_again_hint": "Still failing after retries — reload the page to reconnect",
    "common.load_failed": "Load failed",
    "common.network": "Network hiccup",
    "common.processing": "Processing...",
    "common.ok": "OK",
    "common.none": "None",
    "common.append": "Plus",
    "common.days": "days",
    "common.day_unit": "{n} d",
    "hammer.pending": "Coming soon",
    "hammer.title": "Actions",
    "hammer.close": "Close",
    "inv.exp.ok": "Normal",
    "inv.exp.ok_icon": "✓ Normal",
    "inv.exp.warn": "Near expiry",
    "inv.exp.warn_icon": "⚠️ Near expiry",
    "inv.exp.no": "Over",
    "inv.exp.no_icon": "✗ Over",
    "inv.exp.expired": "Expired",
    "inv.exp.expired_icon": "⚫ Expired",
    "inv.exp.over_third": "Over 1/3",
    "inv.exp.over_third_icon": "✗ Over 1/3",
    "inv.exp.expired_short": "Expired",
    "inv.use.half_icon": "⚠️ Over half used",
    "inv.use.mid_icon": "🟡 In use",
    "inv.use.normal_icon": "🟢 Normal",
    "inv.use.fresh_icon": "⚪ New batch",
    "inv.act.slow": "Slow moving — promote",
    "inv.act.normal": "Selling normally",
    "inv.act.promo": "Promote / transfer",
    "inv.act.clear": "Clear stock / return",
    "inv.act.scrap": "Scrap / return to supplier",
    "oms.st.done": "Completed",
    "oms.st.to_ship": "To ship",
    "oms.st.shipped": "Shipped",
    "oms.st.to_confirm": "To confirm",
    "oms.st.refund": "Refund requested",
    "inv.title": "Inventory",
    "inv.loaded": "Loaded {a}/{b} · {c}/{d} columns",
    "inv.refresh_now": "Refresh now",
    "inv.try_other_kw": "Try another keyword",
    "inv.import_hint": "Import stock data from Data Import",
    "inv.go_import": "Go to import →",
    "inv.batch_badge": "⤵ Batches",
    "inv.batch_loading": "Loading batches…",
    "inv.batch_failed": "Failed to load batches — collapse and expand to retry",
    "inv.total": "Total",
    "inv.all_loaded": "All {n} rows loaded",
    "inv.channel_other": "Other",
    "inv.channel_jd": "JD",
    "inv.load_fail_keep": "Refresh failed — showing last loaded data",
    "inv.delete_failed": "Delete failed: {msg}",
    "inv.archived_miss": "Archived {n}, {m} no longer exist",
    "inv.archived_ok": "Archived {n} · restore from the recycle bin",
    "inv.not_found": "{sku} not found — not in this warehouse view or out of stock",
    "clean.mapping": "Column mapping",
    "clean.mapping_desc": "Map file columns to target fields · unmapped columns are dropped",
    "clean.suggest": "Suggested",
    "clean.required_suffix": " (required)",
    "clean.no_map": "-- Not mapped --",
    "clean.title": "Data Import",
}


def emit(table):
    add = ""
    for k, v in table.items():
        vv = v.replace("'", "\\'")
        add += "  '%s': '%s',\n" % (k, vv)
    return add


for fname, table in (("locale.zh.ts", ZH), ("locale.en.ts", EN)):
    p = os.path.join("frontend/src", fname)
    s = open(p, encoding="utf-8").read()
    todo = {k: v for k, v in table.items() if "'%s'" % k not in s}
    if not todo:
        print("skip", fname)
        continue
    add = "  // ── 页面与状态标签(2026-11-04 全量 i18n 对齐)──\n" + emit(todo)
    idx = s.rstrip().rfind("}")
    s = s[:idx] + add + s[idx:]
    open(p, "w", encoding="utf-8").write(s)
    print(fname, "add", len(todo))
