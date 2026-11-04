#!/usr/bin/env python3
"""fields.ts 一次性迁移: 把 `l: '中文'` 改成 `l: '字段名'`(字段名即 i18n 键后缀, 前缀 fld.)。

理由: 字段字典同源驱动 4 处 UI(映射下拉 / 智能建议 / 下载模板表头 / 模板弹窗),
标签一旦写死中文, 切 EN 时这 4 处全是中文列名; 但字段又有 100+ 条, 逐条塞进 t() 会让
字典膨胀且难维护 —— 于是收敛为「l 存字段名, UI 侧 u(f) 出双语」。
"""
import re

PATH = "frontend/src/pages/cleansing/fields.ts"
src = open(PATH, encoding="utf-8").read()

# 同一字段名在不同导入类型下标签不同时, 以「更具体」的写法保留(用别名后缀区分)
# 例: warehouse 在入库表叫「仓库名」→ 仍用 warehouse, 英文同 Warehouse(差异无信息量)
lines = src.split("\n")
out = []
changed = 0
for line in lines:
    m = re.search(r"\{ t: '([a-z_0-9]+)', l: '([^']*)', tp: '([a-z]+)' \}", line)
    if m:
        key, label, tp = m.groups()
        new = "{ t: '%s', l: '%s', tp: '%s' }" % (key, key, tp)
        line = line.replace(m.group(0), new)
        changed += 1
    out.append(line)

src = "\n".join(out)

# DOC_TYPE_NAMES: 值改键后缀, UI 侧 dt(t) 出双语
for zh_key, suffix in [
    ("'订单'", "'order'"), ("'库存'", "'inventory'"), ("'平台库存'", "'platform_inv'"),
    ("'B仓库存'", "'inventory_b'"), ("'商品'", "'product'"), ("'供应商'", "'supplier'"),
    ("'入库'", "'inbound'"), ("'出库'", "'outbound'"), ("'GMV回款'", "'gmv_flow'"),
]:
    src = src.replace(": %s,\n" % zh_key, ": %s,\n" % suffix)

open(PATH, "w", encoding="utf-8").write(src)
print("字段行改写:", changed)
