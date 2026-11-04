"""备注单元格 emoji→SVG 图标回归检查(2026-11-04)。

背景: note_segs 结构化改造时, renderNoteSegs 直接吐 t() 文案、绕过了 shared.tsx 的
EMOJI_MAP 转换层, 界面上的 🔴/⚠️/📉/➡️ 退化成彩色原生 emoji。这里锁死判据:

  · 备注单元格 td.col-note 的 title 是后端 note 原文(含 emoji 标记, 导出/历史依赖, 不改)
  · 渲染内容里**不能**残留 emoji 字符, 且必须出现 <svg>
  · 英文界面渲染内容不含中文(词条生效)

因为备注数据来自真实 API, 本脚本同时能顺带验证登录/请求链路(client.ts 的 headers、
abort 等改动)没有把数据打空 —— withMarker==0 会显式报「无数据」。

覆盖范围: 双语 × 4 视口 × insights 页**默认(补货) tab**。InsightsPage 自身不渲染 tab
切换 UI(切换只由看板 onGoInsights 入口触发), 且 hammerInsightsTab 不落 localStorage,
所以脚本不假装能切 tab —— 补货/采购/滞销三 tab 的备注都走同一个 renderNote,
补货 100 行数据足以锁住渲染层。想单独看采购 tab: 从看板「采购建议」入口进。

用法(仓库根目录):
    python3 scripts/ui_note_icon_check.py                       # 双语 × 4 视口
    python3 scripts/ui_note_icon_check.py --locale zh --viewport 1440
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_preview_env import PreviewEnv  # noqa: E402

VIEWPORTS = {375: (375, 812), 390: (390, 844), 844: (844, 390), 1440: (1440, 900)}

COLLECT = r"""() => {
    const RE = /(🔴|🟠|🟡|⚠️|⚪|✅|📈|📉|➡️)/g;
    const cells = [...document.querySelectorAll('td.col-note')];
    const out = { total: cells.length, marked: 0, svg: 0, emojiLeft: [], cjk: [], sample: '' };
    for (const td of cells) {
        const title = td.getAttribute('title') || '';
        RE.lastIndex = 0;
        if (!RE.test(title)) continue;
        out.marked++;
        if (td.querySelector('svg')) out.svg++;
        const txt = td.innerText || '';
        RE.lastIndex = 0;
        const left = txt.match(RE);
        if (left) out.emojiLeft.push(...left);
        if (!out.sample) out.sample = txt.slice(0, 100);
        if (/[\u4e00-\u9fff]/.test(txt)) out.cjk.push(txt.slice(0, 40));
    }
    out.emojiLeft = [...new Set(out.emojiLeft)];
    out.cjk = [...new Set(out.cjk)].slice(0, 5);
    return out;
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", default="both", choices=["zh", "en", "both"])
    ap.add_argument("--viewport", default="all")
    args = ap.parse_args()

    locales = ["zh", "en"] if args.locale == "both" else [args.locale]
    vps = list(VIEWPORTS) if args.viewport == "all" else [int(args.viewport)]

    bad = []
    saw_data = 0
    with PreviewEnv() as env:
        for loc in locales:
            for vw in vps:
                w, h = VIEWPORTS[vw]
                page = env.new_page(posix_w=w, height=h, locale=loc)
                if not env.login(page):
                    bad.append(f"{loc} {vw} 登录失败")
                    page.context.close()
                    continue
                if not env.goto(page, "insights"):
                    bad.append(f"{loc} {vw} 切到洞察页失败")
                    page.context.close()
                    continue
                page.wait_for_timeout(2500)
                r = page.evaluate(COLLECT)
                saw_data += r["marked"]
                flags = []
                if r["marked"] and r["svg"] != r["marked"]:
                    flags.append(f"缺 SVG {r['marked'] - r['svg']}/{r['marked']}")
                if r["emojiLeft"]:
                    flags.append("残留 emoji " + "".join(r["emojiLeft"]))
                if loc == "en" and r["cjk"]:
                    flags.append("英文界面中文 " + str(r["cjk"][:2]))
                status = "⚠ " + " / ".join(flags) if flags else "ok"
                print(
                    f"{loc} {vw:>4} replen(默认) 备注{r['total']:>3} "
                    f"带标记{r['marked']:>3} svg{r['svg']:>3}  {status}"
                )
                if flags:
                    bad.append(f"{loc} {vw}: " + " / ".join(flags))
                if r["sample"]:
                    print(f"      样例: {r['sample']}")
                page.context.close()

    if saw_data == 0:
        bad.append("全程没拿到带 emoji 标记的备注数据 —— 检查 API/登录链路")
    print("\n结论:", "PASS" if not bad else "FAIL")
    for b in bad:
        print("  -", b)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
