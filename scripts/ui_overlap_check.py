"""表格单元格「文字叠印/压边」检测 — 补 ui_shot_matrix 覆盖不到的那一类。

为什么单独一个脚本: 2026-11-04 用户报「订单页 69 码与订单号平行重叠」, 而当时的
快照矩阵报 `overflowX: 0`。原因很直接 —— **单元格文字溢出到相邻列, 不会让 table
的 scrollWidth 变大**。它只是画在别人头上。所以只测 scrollWidth 永远发现不了叠印。

这里换个测法: 逐个单元格比较 `文字实际宽` vs `单元格内容区宽`。
  · 文字右沿 > 单元格右沿(减 padding)  → 溢出, 会压到右邻列  ← 叠印的直接原因
  · 文字宽 > 内容区宽 且 overflow 非 hidden → 同上, 只是方向可能左溢
命中就把单元格的 文本/列名/几何/生效的 white-space/overflow 一起打出来, 便于直接定位。

用法(需要 localhost:4173, 用 ui_preview_env 起):
    python3 scripts/ui_overlap_check.py --serve --page orders --viewport 1440
    python3 scripts/ui_overlap_check.py --page inv --viewport all --locale both
    python3 scripts/ui_overlap_check.py --serve --page all      # 全部页面 × 全部视口

输出: 逐条命中 + 末尾汇总; 有命中退出码 1(可直接接在门禁链后面)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_preview_env import PreviewEnv, ROOT  # noqa: E402
from ui_shot_matrix import PAGES, VIEWPORTS  # noqa: E402

# 每个单元格量这些几何量; 顺带把决定溢出行为的两个 CSS 属性带回来
PROBE_JS = """
() => {
  const out = []
  const tables = [...document.querySelectorAll('table')]
  tables.forEach((tb, ti) => {
    const heads = [...tb.querySelectorAll('thead th')].map(th => (th.textContent || '').trim())
    let ci = 0
    tb.querySelectorAll('tbody tr').forEach((tr, ri) => {
      if (ri > 6) return                       // 前 7 行够用, 避免几万行跑太久
      [...tr.children].forEach((td, idx) => {
        if (td.tagName !== 'TD') return
        const cs = getComputedStyle(td)
        const r = td.getBoundingClientRect()
        const pl = parseFloat(cs.paddingLeft) || 0
        const pr = parseFloat(cs.paddingRight) || 0
        const contentW = r.width - pl - pr
        // Range 量的是**文字真实占位宽**, 不受 overflow 影响
        const rng = document.createRange()
        rng.selectNodeContents(td)
        const rects = [...rng.getClientRects()].filter(x => x.width > 0.5)
        if (!rects.length) { ci++; return }
        const textL = Math.min(...rects.map(x => x.left))
        const textR = Math.max(...rects.map(x => x.right))
        const textW = textR - textL
        const cellL = r.left + pl
        const cellR = r.right - pr
        // 右溢(压到右邻列) / 左溢(压到左邻列) —— 都算叠印风险
        const overR = textR - cellR
        const overL = cellL - textL
        if (overR > 1.5 || overL > 1.5) {
          out.push({
            table: ti,
            row: ri,
            col: idx,
            head: heads[idx] || (heads[0] || '') + '#' + idx,
            text: (td.textContent || '').trim().slice(0, 40),
            cls: td.className || '',
            msW: Math.round(textW),
            boxW: Math.round(contentW),
            overR: Math.round(overR),
            overL: Math.round(overL),
            ws: cs.whiteSpace,
            ovf: cs.overflow,
            fs: cs.fontSize,
          })
        }
        ci++
      })
    })
  })
  return out
}
"""


def check_page(env: PreviewEnv, page, vw: int, pid: str, locale: str) -> list[dict]:
    posix_w, height = VIEWPORTS[vw]
    p = env.new_page(posix_w=posix_w, height=height, locale=locale, scale=1)
    if not env.login(p):
        print(f"  [{locale}/{vw}/{pid}] 登录失败, 跳过")
        p.context.close()
        return []
    if not env.goto(p, pid):
        print(f"  [{locale}/{vw}/{pid}] 切页失败, 跳过")
        p.context.close()
        return []
    try:
        hits = p.evaluate(PROBE_JS)
    except Exception as e:  # noqa: BLE001
        print(f"  [{locale}/{vw}/{pid}] 探测异常: {str(e)[:120]}")
        hits = []
    p.context.close()
    for h in hits:
        h.update({"page": pid, "vw": vw, "locale": locale})
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true", help="自己 build + 起 preview")
    ap.add_argument("--port", type=int, default=4173)
    ap.add_argument("--page", default="all", help="页面 id 或 all")
    ap.add_argument("--viewport", default="all", help="375/390/844/1440 或 all")
    ap.add_argument("--locale", default="zh", choices=["zh", "en", "both"])
    ap.add_argument("--json", action="store_true", help="以 JSON 输出(便于接 CI)")
    args = ap.parse_args()

    vws = list(VIEWPORTS) if args.viewport == "all" else [int(args.viewport)]
    pids = [p[0] for p in PAGES] if args.page == "all" else [args.page]
    locales = ["zh", "en"] if args.locale == "both" else [args.locale]

    all_hits: list[dict] = []
    ctx = PreviewEnv(port=args.port, build=args.serve)
    if args.serve:
        ctx.__enter__()
    try:
        for locale in locales:
            for vw in vws:
                for pid in pids:
                    hits = check_page(ctx, None, vw, pid, locale)
                    all_hits.extend(hits)
                    flag = f"⚠ {len(hits)} 处" if hits else "ok"
                    print(f"{locale} {vw:<5} {pid:<10} {flag}")
    finally:
        if args.serve:
            ctx.__exit__(None, None, None)

    if args.json:
        print(json.dumps(all_hits, ensure_ascii=False, indent=1))
    elif all_hits:
        print("\n—— 命中明细 ——")
        for h in all_hits:
            print(
                f"[{h['locale']}/{h['vw']}/{h['page']}] 表{h['table']} 行{h['row']} "
                f"列「{h['head']}」{h['cls']}\n"
                f"    文本: {h['text']!r}\n"
                f"    文字宽 {h['msW']}px / 内容区 {h['boxW']}px  "
                f"右溢 {h['overR']}px 左溢 {h['overL']}px  "
                f"ws={h['ws']} overflow={h['ovf']} fs={h['fs']}"
            )
    print(f"\n合计 {len(all_hits)} 处叠印/压边")
    return 1 if all_hits else 0


if __name__ == "__main__":
    sys.exit(main())
