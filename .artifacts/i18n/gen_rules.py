#!/usr/bin/env python3
"""由 /tmp/rf.json 生成 rules/labels.tsx 的键词表 + 改写字典为存键。

字典改「存键」而不是「存文案」: 原 VARS/LF/OPS/WHS/MODES 是模块级常量, 存中文就锁死了语言;
改存键后由 t() 在渲染时解析, 切换语言立即生效(与 fields.ts / colLabels.ts 同一套做法)。
"""
import json
import re

SRC = "frontend/src/pages/rules/labels.tsx"
EN = {
    # VARS —— 模板变量中文名(规则列表里 {avail} 这类占位渲染成的人话)
    "商品名": "Product name", "SKU": "SKU", "可用量": "Available", "动态安全线": "Dynamic safety stock",
    "天数": "Days", "库存量": "Stock", "订单数": "Orders", "店铺": "Store", "仓库": "Warehouse",
    "库存健康分": "Health score", "健康预警档位": "Health warning level",
    "调整后可撑天数": "Adj. days of supply", "补货周期": "Replenishment lead time",
    "可售量": "Sellable", "自有仓可用": "Own WH available", "自有仓在途": "Own WH in transit",
    "采购在途": "PO in transit", "自有可发": "Own shippable",
    "下游日销": "Downstream daily sales", "下游在仓": "Downstream on hand",
    "调拨周期": "Transfer lead time", "下游要货量": "Downstream demand",
    "调拨缺口": "Transfer gap", "可撑天数": "Days of supply", "距过期天数": "Days to expiry",
    "缺口下限": "Gap floor", "积压天数阈值": "Overstock days threshold", "效期缓冲": "Expiry buffer",
    "B→C调拨天数": "B→C transfer days", "C仓缓冲天数": "C-warehouse buffer days",
    "自有仓→B仓时效": "Own WH → B lead time", "安全库存天数": "Safety stock days",
    "仓储费阈值": "Storage fee threshold", "周转考核红线": "Turnover limit",
    "前置期": "Lead time", "采购前置期": "Purchase lead time", "采购安全库存": "Purchase safety stock",
    "MOQ最小起订": "MOQ", "目标周转天数": "Target turnover days",
    "OTIF置信下限": "OTIF lower bound", "动态SS Z值": "Dynamic SS Z-score",
    "波动率σ样本天数": "Volatility σ sample days", "加速倍率阈值": "Acceleration ratio threshold",
    "加速样本量": "Acceleration sample size", "橙灯缓冲上限": "Orange buffer ceiling",
    "黄灯缓冲上限": "Yellow buffer ceiling", "橙灯天数余量": "Orange days margin",
    "已断纳入红灯": "Include out-of-stock as red", "健康档位线": "Health tier line",
    "审计日志": "Audit log", "告警开关": "Alert toggle",
    # LF —— 左侧字段下拉
    "可用库存": "Available stock", "安全库存": "Safety stock", "在途库存": "In-transit stock",
    "锁定库存": "Locked stock", "可用+在途": "Available + in transit",
    "可用+在途+锁定": "Available + in transit + locked",
    "可售(可用+在途)": "Sellable (available + in transit)",
    "在途(单列)": "In transit (single column)",
    "可售缺口(安全线−可售)": "Sellable gap (safety − sellable)",
    "可售/安全线(比例)": "Sellable / safety (ratio)",
    "动态安全线-可用(缺口)": "Dynamic safety − available (gap)",
    "可用/动态安全线(比例)": "Available / dynamic safety (ratio)",
    "日销(定时任务提供)": "Daily sales (from scheduled job)",
    "可撑天数(可用/日销)": "Days of supply (available / daily sales)",
    "距上次销售(天)": "Days since last sale", "库存量": "Stock", "仓库类型": "Warehouse type",
    "69码": "Barcode", "订单数量": "Order qty", "累计订单量": "Cumulative order qty",
    "最大单笔量": "Max single order", "最小单笔量": "Min single order",
    "本批行数": "Rows in batch", "出入库数量": "Flow qty", "出入库仓库": "Flow warehouse",
    "出入库日期": "Flow date", "入库数量": "Inbound qty", "出库数量": "Outbound qty",
    "批次剩余效期(天)": "Batch days to expiry", "供应商评分": "Supplier score",
    "周转天数": "Turnover days", "订单金额": "Order amount",
    "订单数量×单价": "Order qty × unit price", "单价": "Unit price",
    "可售天数 Adj-DOS(看板断货同源)": "Adj-DOS (same source as OOS board)",
    "缓冲比 Buffer(可用/动态安全线)": "Buffer ratio (available / dynamic safety)",
    "在途置信 OTIF(供应商分/5)": "In-transit confidence OTIF (supplier score / 5)",
    "动态安全线 SS(规则参数)": "Dynamic SS (rule param)",
    "加速倍率(当天 vs 前3天)": "Acceleration (today vs prior 3 days)",
    "库存健康分(渠道)": "Health score (channel)",
    "补货周期 L/T(规则参数)": "Lead time L/T (rule param)",
    "健康预警档位(规则参数)": "Health warning tier (rule param)",
    "— 自有仓 —": "— Own warehouse —", "自有仓可用": "Own WH available",
    "自有仓在途": "Own WH in transit", "采购在途(已发未到)": "PO in transit (shipped, not arrived)",
    "自有可发(在仓+在途+采购在途)": "Own shippable (on hand + transit + PO)",
    "下游日销(平台仓)": "Downstream daily sales (platform WH)",
    "下游在仓(平台仓合计)": "Downstream on hand (platform WH total)",
    "调拨周期(天, 与规则同源)": "Transfer lead time (days, shared with rules)",
    "下游本期要货量": "Downstream demand this period",
    "调拨缺口(要货 − 可发)": "Transfer gap (demand − shippable)",
    "自有可撑天数(可用/下游日销)": "Own days of supply (available / downstream daily)",
    # OPS
    "小于": "less than", "小于等于": "less than or equal", "大于": "greater than",
    "大于等于": "greater than or equal", "等于": "equals", "不等于": "not equal",
    # WHS
    "全部": "All", "B仓": "B warehouse", "BC合计(BBCC)": "B+C total (BBCC)",
    "C仓": "C warehouse", "自有仓": "Own warehouse",
    # MODES
    "BBCC": "BBCC", "传统多仓": "Traditional multi-warehouse",
    # EVENTS
    "库存变动": "Inventory changed", "订单创建": "Order created", "每日定时": "Daily schedule",
    "入库记录": "Inbound recorded", "出库记录": "Outbound recorded",
}

d = json.load(open("/tmp/rf.json"))
src = open(SRC, encoding="utf-8").read()

# ── 1. 生成 LOCALE 段落(打印出来给批次文件用) ──
loc = []
def add(key, zh):
    en = EN.get(zh)
    if en is None:
        raise SystemExit("缺英文: %r" % zh)
    loc.append((key, zh, en))

for k, zh, key in d["VARS"]:
    add(key, zh)
for idx, (v, zh, _) in enumerate(d["LF"]):
    add("rf.lf.%d" % idx, zh)
for idx, (v, zh, _) in enumerate(d["OPS"]):
    add("rf.op.%s" % v.replace("<", "lt").replace(">", "gt").replace("=", "eq").replace("!", "n"), zh)
for idx, (v, zh, _) in enumerate(d["WHS"]):
    add("rf.wh.%s" % (v or "all"), zh)
for idx, (v, zh, _) in enumerate(d["MODES"]):
    add("rf.mode.%s" % (v or "all"), zh)

em = json.load(open("/tmp/rf.json"))  # noop
open("/tmp/rf_locale.txt", "w").write(repr(loc))
print("词条", len(loc))

# ── 2. 改写 labels.tsx 字典为存键 ──
# VARS
i = src.index("export const VARS = {")
j = src.index("\n}\n", i)
body = src[i:j]
lines = []
for line in body.split("\n"):
    m = re.match(r"^  (\w+): '([^']*)',$", line)
    if m:
        lines.append("  %s: 'rf.vars.%s'," % (m.group(1), m.group(1)))
    else:
        lines.append(line)
src = src[:i] + "\n".join(lines) + src[j:]

# LF / OPS / WHS / MODES: l 值换成键
def rewrite(name, kind, mkm):
    global src
    m = re.search(r"export const %s = \[\n(.*?)\n\]" % name, src, re.S)
    body = m.group(1)
    idx = [0]
    def rep(mt):
        v, zh = mt.group(2), mt.group(1)
        key = mkm(v, idx[0])
        idx[0] += 1
        if kind == "l":
            return "{ l: '%s', v: '%s' }" % (key, v)
        return "{ value: '%s', label: '%s' }" % (v, key)
    if kind == "l":
        nb = re.sub(r"\{ l: '([^']*)', v: '([^']*)' \}", rep, body)
    else:
        nb = re.sub(r"\{ value: '([^']*)', label: '([^']*)' \}", lambda mt: "{ value: '%s', label: '%s' }" % (mt.group(1), mkm(mt.group(1), idx.pop(0))), body)
    src = src[:m.start(1)] + nb + src[m.end(1):]

rewrite("LF", "l", lambda v, i: "rf.lf.%d" % i)
rewrite("OPS", "l", lambda v, i: "rf.op." + v.replace("<", "lt").replace(">", "gt").replace("=", "eq").replace("!", "n"))
rewrite("WHS", "l", lambda v, i: "rf.wh.%s" % (v or "all"))
rewrite("MODES", "l", lambda v, i: "rf.mode.%s" % (v or "all"))

# EVENTS: label 换键
m = re.search(r"export const EVENTS = \[\n(.*?)\n\]", src, re.S)
cnt = [0]
def rep_ev(mt):
    cnt[0] += 1
    return "{ value: '%s', label: 'rf.ev.%d' }" % (mt.group(1), cnt[0] - 1)
nb = re.sub(r"\{ value: '([^']*)', label: '([^']*)' \}", rep_ev, m.group(1))
src = src[:m.start(1)] + nb + src[m.end(1):]

open(SRC, "w", encoding="utf-8").write(src)
print("labels.tsx 已改写")
