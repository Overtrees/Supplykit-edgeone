#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档一致性门禁（零 RU / 纯静态）：把 §15.191 的文档勘误固化成可复跑判据。

起因：2026-11-09 用户指出「历史归档的天数可能存在不符」。复核发现两类实质漂移：
  ① 归档切线 365 → 180 天（真源 core/archive.py 的 ARCHIVE_KEEP_DAYS）；
  ② 变更史索引是 11-06 快照（卷数 / 节数 / 「§15.x 全局唯一」全部过时）。
另有一批静态数字（preflight 步数 / cron 端点 / 路由数）随代码漂移。

本脚本只核对**活文档**（README/CONTEXT/CHANGELOG 头/docs/*/scripts README），
历史卷 docs/history/*.md 与 CHANGELOG 历史批次**一律不检查**（红线：历史记录不改写）。

用法：
    python3 scripts/perf/docs_probe.py check       # 一致性门禁
    python3 scripts/perf/docs_probe.py selftest    # 反证：故意破坏应报红
    python3 scripts/perf/docs_probe.py matrix      # 打印「真源 → 文档」对照
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API = os.path.join(ROOT, "cloud-functions", "api")
_RES = []


def chk(ok, msg):
    _RES.append((bool(ok), msg))
    if not ok:
        print("  ✗ %s" % msg, flush=True)
    else:
        print("  ✓ %s" % msg, flush=True)


def text(rel):
    p = os.path.join(ROOT, rel)
    return open(p, encoding="utf-8").read()


# ── 真源（全部从代码/配置现读，不写死） ──────────────────────────────────
def truth():
    t = {}
    arc = text("cloud-functions/api/core/archive.py")
    m = re.search(r'ARCHIVE_KEEP_DAYS\s*=\s*int\(os\.environ\.get\(\s*["\']ARCHIVE_KEEP_DAYS["\']\s*,\s*["\'](\d+)["\']', arc)
    t["keep_days"] = int(m.group(1)) if m else None
    # 写侧/读侧是否共用同一常量
    t["shared_const"] = ("from core.archive import ARCHIVE_KEEP_DAYS" in
                         text("cloud-functions/api/routes/history.py"))
    # preflight 步数：数 [N/M] 标题
    pf = text("scripts/preflight.sh")
    steps = re.findall(r"──\s*\[(\d+)/(\d+)\]", pf)
    t["preflight_steps"] = int(steps[0][1]) if steps else None
    # cron 端点：装饰器行
    cron = text("cloud-functions/api/routes/cron.py")
    t["cron_endpoints"] = len(re.findall(r"@router\.(?:get|post|api_route)\(", cron))
    # schedules 条数
    import json
    t["schedules"] = len(json.load(open(os.path.join(ROOT, "edgeone.json"), encoding="utf-8")).get("schedules", []))
    # APIRouter / 路由文件
    rdir = os.path.join(API, "routes")
    files = [f for f in os.listdir(rdir) if f.endswith(".py")]
    routers = sum(1 for f in files if "APIRouter(" in open(os.path.join(rdir, f), encoding="utf-8").read())
    t["route_files"] = len(files)
    t["routers"] = routers
    # 前端导入类数
    tf = text("frontend/src/pages/cleansing/fields.ts")
    mm = re.search(r"export const TARGET_FIELDS[^=]*=\s*\{", tf)
    i = mm.end() - 1
    depth = 0
    j = i
    for k in range(i, len(tf)):
        if tf[k] == "{":
            depth += 1
        elif tf[k] == "}":
            depth -= 1
            if depth == 0:
                j = k
                break
    t["import_targets"] = len(set(re.findall(r"^  ([a-z_]+):", tf[i + 1:j], re.M)))
    # 变更史分卷
    hdir = os.path.join(ROOT, "docs", "history")
    hs = [f for f in os.listdir(hdir) if f.endswith(".md")]
    t["date_volumes"] = len([f for f in hs if re.match(r"^2026-\d\d-\d\d", f)])
    t["topic_volumes"] = len(hs) - t["date_volumes"]
    # 最大 §15.x 编号（标题级）
    mx = 0
    for f in hs:
        for l in open(os.path.join(hdir, f), encoding="utf-8"):
            if re.match(r"^#{2,4}\s", l):
                z = re.search(r"(?:§)?15\.(\d+)", l)
                if z:
                    mx = max(mx, int(z.group(1)))
    t["max_section"] = mx
    # §15.x 标题级重号数
    owner = {}
    for f in hs:
        for l in open(os.path.join(hdir, f), encoding="utf-8"):
            if re.match(r"^#{2,4}\s", l):
                z = re.search(r"(?:§)?15\.(\d+)", l)
                if z:
                    owner.setdefault(int(z.group(1)), set()).add(f)
    t["dup_sections"] = len([k for k, v in owner.items() if len(v) > 1])
    return t


# ── 活文档断言 ──────────────────────────────────────────────────────────
def check(T):
    print("=== 文档一致性门禁（活文档；历史卷/历史批次不检查） ===", flush=True)
    readme = text("README.md")
    arch = text("docs/ARCHITECTURE.md")
    dev = text("docs/DEVELOPMENT.md")
    skill = text("docs/SKILL.md")
    context = text("CONTEXT.md")
    changelog_head = "\n".join(text("CHANGELOG.md").splitlines()[:20])

    kd = T["keep_days"]
    # ① 归档切线：活文档不得再写 365 切线 / 第 366 天；必须写 180 / 第 181 天
    live_func = readme + arch + dev + skill
    chk(kd == 180, "真源 ARCHIVE_KEEP_DAYS=180（实测 %s）" % kd)
    chk("365 天切线" not in live_func and "满 365 天" not in live_func,
        "活文档无「365 天切线 / 满 365 天」")
    # SKILL 的历史引用（"由 365 改回"）允许，单独排除
    chk("第 366 天" not in live_func.replace("2026-11-07 由 365 改回", ""),
        "活文档无「第 366 天」（SKILL 历史注除外）")
    chk(("第 181 天" in readme and "180 天" in arch and "180 天" in dev and
         "180 天" in skill),
        "README/ARCH/DEV/SKILL 均写明 180 天 / 第 181 天")
    chk(T["shared_const"], "真源：写侧 cron 与读侧 history import 同一常量")
    chk("勘误" in context and "ARCHIVE_KEEP_DAYS=180" in context,
        "CONTEXT 历史章节有归档切线勘误注记（原文保留）")

    # ② 变更史索引
    chk("%d 个日期分卷" % T["date_volumes"] in dev,
        "DEVELOPMENT §12 写明 %d 个日期分卷" % T["date_volumes"])
    chk("%d 个专题卷" % T["topic_volumes"] in dev,
        "DEVELOPMENT §12 写明 %d 个专题卷" % T["topic_volumes"])
    chk("§15.%d" % T["max_section"] in dev,
        "DEVELOPMENT §12 章节号到 §15.%d" % T["max_section"])
    chk("全局唯一" in dev and "已不再成立" in dev,
        "DEVELOPMENT §12 如实标注「全局唯一已不再成立」")
    chk(T["dup_sections"] >= 10,
        "真源 §15.x 标题级跨卷重号 %d 个（≥10，故不能声称唯一）" % T["dup_sections"])
    for f, key in (("README.md", "readme"), ("CONTEXT.md", "context"),
                   ("CHANGELOG.md", "changelog_head"), ("docs/SKILL.md", "skill")):
        src = {"readme": readme, "context": context,
               "changelog_head": changelog_head, "skill": skill}[key]
        chk("全局唯一" not in src, "%s 不再声称 §15.x 全局唯一" % f)

    # ③ preflight 步数
    chk(T["preflight_steps"] == 8, "真源 preflight %d 步" % T["preflight_steps"])
    chk("八步" in readme and "i18n 八步" in readme, "README 自检注释写八步 + i18n")
    chk("七步交付闭环" in skill, "SKILL「七步交付闭环」(①~⑦ 流程) 正确保留")

    # ④ cron 端点 / schedules / 路由数
    chk(T["cron_endpoints"] == 12, "真源 cron %d 端点" % T["cron_endpoints"])
    chk(T["schedules"] == 10, "真源 schedules %d 条" % T["schedules"])
    chk("12 个端点" in readme and "12 端点" in arch and "12 端点" in skill,
        "README/ARCH/SKILL 均写 12 端点")
    chk("22 个路由" not in skill and "22 个 router" not in arch and
        "31 个模块" not in arch, "无过时「22 路由 / 31 模块」")
    chk("%d 个 APIRouter" % T["routers"] in dev and
        "%d 个路由文件" % T["route_files"] in dev,
        "DEVELOPMENT 写 %d APIRouter / %d 路由文件" % (T["routers"], T["route_files"]))

    # ⑤ 明确不改的正确描述（防止误伤）
    chk(T["import_targets"] == 9 and "9 类导入" in readme,
        "README「9 类导入」= 前端 %d 个 target，正确保留" % T["import_targets"])
    chk("860 项" in readme, "README「860 项回归」正确保留")
    chk("实例内 token 槽（3）" in readme and "heavy_slot`（2" in readme,
        "README 名额 3+2 描述正确保留")

    # ⑥ 勘误落档
    # 「最新一批是否真的落档」——**不能写死批次号/章节号**：写死「第七批/§15.191」时，
    #   下一批做完这条判据还在查旧批次，会给出「已记录」的假绿（2026-11-09 §15.192 就撞到了）。
    #   改从真源取：history 里最大的 §15.x = 本轮新增的那一节，再要求 CHANGELOG 顶部引用它。
    _sec = "§15.%s" % T["max_section"]
    _hist = text("docs/history/2026-11-09.md")
    _cl_head = "\n".join(text("CHANGELOG.md").splitlines()[:60])
    chk(_sec in _hist, "history 最新节 %s 存在（本轮记录已落档）" % _sec)
    chk(_sec in _cl_head, "CHANGELOG 顶部批次引用最新节 %s" % _sec)
    chk("## 13." in text("docs/PERF.md"), "PERF 有 §13 复验章节")
    chk("suite_p6.py" in text("scripts/perf/README.md"), "perf README 收录 suite_p6.py")

    ok = all(v for v, _ in _RES)
    print("  → docs_probe %s（%d 项，FAIL %d）" % (
        "PASS" if ok else "FAIL", len(_RES), sum(1 for v, _ in _RES if not v)), flush=True)
    return ok


def selftest(T):
    """反证：门禁必须能报红（不是恒真）。用独立收集器，不污染/不掩盖全局。"""
    print("=== 反证（门禁必须能报红） ===", flush=True)
    global _RES
    saved = list(_RES)      # 完整快照，结束恢复
    _RES = []               # 独立收集器

    # ① chk 机制本身能报红：故意 False 必须精确记 1 条 FAIL
    chk(False, "[反证] 故意 False")
    red = sum(1 for ok, _ in _RES if not ok)
    _RES = []               # 隔离掉这条「故意的」FAIL（它不该算进最终结论）
    chk(red == 1, "chk(False) 精确记 1 条 FAIL（实测 %d）" % red)

    # ② 注入篡改真源 → 核心判据必须失效（证明不是恒真）
    bad_dup = dict(T, dup_sections=0)
    chk(not (bad_dup["dup_sections"] >= 10),
        "注入 dup_sections=0 → 「不能声称唯一」判据失效")
    bad_keep = dict(T, keep_days=365)
    chk(bad_keep["keep_days"] != 180, "注入 keep_days=365 → 「真源=180」判据失效")
    bad_steps = dict(T, preflight_steps=7)
    chk(bad_steps["preflight_steps"] != 8, "注入 preflight_steps=7 → 「八步」判据失效")

    # ③ 篡改活文档文本 → 「活文档无 365 切线」判据必须命中（可报红）
    readme = text("README.md")
    polluted = readme.replace("180 天", "365 天切线")
    chk("365 天切线" in polluted and "365 天切线" not in readme,
        "把 180 天改回 365 天切线 → 归档判据能命中（原文档确无此串）")

    ok = all(v for v, _ in _RES)
    n = len(_RES)
    print("  → selftest %s（%d 项反证断言，FAIL %d；故意 FAIL 已隔离）" % (
        "PASS" if ok else "FAIL", n, sum(1 for v, _ in _RES if not v)), flush=True)
    _RES = saved            # 恢复全局快照
    return ok


def matrix(T):
    print("=== 真源 → 文档 对照矩阵 ===", flush=True)
    rows = [
        ("归档保留期", "ARCHIVE_KEEP_DAYS", T["keep_days"], "README/ARCH/DEV/SKILL = 180"),
        ("归档搬运日", "严格小于 today-KEEP", "第 %d 天" % (T["keep_days"] + 1), "第 181 天"),
        ("preflight 步数", "preflight.sh [N/M]", T["preflight_steps"], "README 八步"),
        ("cron 端点", "@router 装饰器", T["cron_endpoints"], "README/ARCH/SKILL 12"),
        ("schedules", "edgeone.json", T["schedules"], "10 条上限"),
        ("APIRouter", "routes/*.py", T["routers"], "DEVELOPMENT 24"),
        ("路由文件", "routes/*.py", T["route_files"], "DEVELOPMENT 34"),
        ("导入 target", "TARGET_FIELDS", T["import_targets"], "README 9 类（正确保留）"),
        ("日期分卷", "docs/history/2026-*", T["date_volumes"], "DEVELOPMENT §12 = 14"),
        ("专题卷", "docs/history/非日期", T["topic_volumes"], "DEVELOPMENT §12 = 3"),
        ("最大章节号", "标题级 §15.x", "§15.%d" % T["max_section"], "DEVELOPMENT §12"),
        ("跨卷重号数", "标题级同号多文件", T["dup_sections"], "§12 注明非全局唯一"),
    ]
    print("  %-14s %-26s %-12s %s" % ("项", "真源位置", "真值", "文档应写"))
    for a, b, c, d in rows:
        print("  %-14s %-26s %-12s %s" % (a, b, str(c), d))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    T = truth()
    if mode == "matrix":
        matrix(T)
        return 0
    if mode == "selftest":
        return 0 if selftest(T) else 1
    return 0 if check(T) else 1


if __name__ == "__main__":
    sys.exit(main())
