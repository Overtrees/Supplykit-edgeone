"""4 视口 × 双语 UI 快照 — i18n 与布局回归的复用脚本。

做什么: 逐页在 375/390/844/1440 四个宽度下截图, 并采集三类异常:
  · 横向溢出(scrollWidth > clientWidth), 附带最宽的元凶选择器
  · 界面上出现**未解析的词条键**(如 `dash.title` 被原样显示)
  · 英文界面里残留中文(逐条列出来人工判定: 数据 or 漏翻)

只管"看得见的问题"。固定宽 + overflow:hidden 的**静默裁切**它发现不了(溢出会是 0),
所以关键页面仍要人眼看截图 —— 典型: 待处理卡片固定正方形, 英文 "Replenish 1…" 被硬裁。

用法:
    # 全量(需要 localhost:4173, 用 ui_preview_env 起)
    python3 scripts/ui_shot_matrix.py --locale both --viewport all

    # 只跑一页一类
    python3 scripts/ui_shot_matrix.py --locale en --viewport 375 --page dash

    # 自己起服务(会先 vite build), 不接手已有的
    python3 scripts/ui_shot_matrix.py --serve

    # 只查溢出/键, 不落盘 PNG
    python3 scripts/ui_shot_matrix.py --no-shot

输出: .artifacts/ui_shots/ 下 {locale}_{vw}_{page}.png + report.json(分批跑会自动合并)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_preview_env import PreviewEnv, DEFAULT_PORT, ROOT  # noqa: E402

OUT = os.path.join(ROOT, ".artifacts", "ui_shots")

VIEWPORTS = {
    375: (375, 812),    # iPhone SE / 小屏
    390: (390, 844),    # iPhone 14 竖屏(主基线)
    844: (844, 390),    # 手机横屏 / 小屏平板
    1440: (1440, 900),  # 桌面
}

# 页面 id 与 App.tsx 的 switch 分支一致(改路由记得同步这里)
PAGES = [
    ("dash", "看板"),
    ("products", "商品"),
    ("suppliers", "供应商"),
    ("orders", "订单"),
    ("history", "历史"),
    ("inv", "进销存"),
    ("insights", "洞察"),
    ("cleansing", "清洗"),
    ("rules", "规则"),
    ("tasks", "任务"),
    ("quality", "质量"),
    ("settings", "设置"),
    ("devmode", "开发模式"),
    ("channels", "渠道"),
    ("recycle", "回收站"),
]

COLLECT = """() => {
    const out = { overflowX: 0, offenders: [], keys: [], cjk: [] };
    const de = document.documentElement;
    out.overflowX = Math.max(0, de.scrollWidth - de.clientWidth);
    if (out.overflowX > 2) {
        document.querySelectorAll('*').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.right > de.clientWidth + 2 && r.width > 30) {
                const cls = (el.className && el.className.baseVal !== undefined)
                    ? el.className.baseVal : (el.className || '');
                out.offenders.push(
                    el.tagName.toLowerCase() + '.' + String(cls).slice(0, 60) +
                    ' w=' + Math.round(r.width) + ' right=' + Math.round(r.right)
                );
            }
        });
        out.offenders = out.offenders.slice(0, 6);
    }
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walk.nextNode())) {
        const s = (n.textContent || '').trim();
        if (!s) continue;
        const p = n.parentElement;
        if (!p) continue;
        const tag = p.tagName.toLowerCase();
        if (tag === 'script' || tag === 'style' || tag === 'noscript') continue;
        // 形如 dash.title / hx.batch —— 词条键被原样渲染出来了
        if (/^[a-z][a-z0-9_]{1,20}\\.[a-z][a-z0-9_.]{1,40}$/.test(s)) out.keys.push(s);
        if (/[\\u4e00-\\u9fff]/.test(s)) out.cjk.push(s.slice(0, 40));
    }
    out.keys = [...new Set(out.keys)].slice(0, 12);
    out.cjk = [...new Set(out.cjk)].slice(0, 12);
    return out;
}"""


def run(env, locales, vps, pages, shot=True):
    report = []
    for loc in locales:
        for vw in vps:
            w, h = VIEWPORTS[vw]
            page = env.new_page(posix_w=w, height=h, locale=loc)
            if not env.login(page):
                print(f"!! {loc} {vw} 登录失败, 跳过该视口")
                page.context.close()
                continue
            for pid, name in pages:
                if not env.goto(page, pid):
                    print(f"{loc} {vw:>4} {pid:<10} ✗ 切页失败(__setPage 未生效)")
                    continue
                page.wait_for_timeout(1200)
                iss = page.evaluate(COLLECT)
                fn = f"{loc}_{vw}_{pid}.png"
                if shot:
                    page.screenshot(path=os.path.join(OUT, fn), full_page=False)
                report.append({
                    "locale": loc, "viewport": vw, "page": pid, "name": name,
                    "file": fn if shot else None,
                    "overflowX": iss["overflowX"], "offenders": iss["offenders"],
                    "raw_keys": iss["keys"],
                    "cjk_in_en": iss["cjk"] if loc == "en" else [],
                })
                flag = []
                if iss["overflowX"] > 2:
                    flag.append(f"溢出{iss['overflowX']}px")
                if iss["keys"]:
                    flag.append(f"未解析键{len(iss['keys'])}")
                if loc == "en" and iss["cjk"]:
                    flag.append(f"英文界面中文{len(iss['cjk'])}")
                print(f"{loc} {vw:>4} {pid:<10} {'⚠ ' + ' / '.join(flag) if flag else 'ok'}")
            page.context.close()
    return report


def merge_report(rows):
    rp = os.path.join(OUT, "report.json")
    merged = {}
    if os.path.exists(rp):
        try:
            with open(rp, encoding="utf-8") as f:
                for row in json.load(f):
                    merged[(row["locale"], row["viewport"], row["page"])] = row
        except Exception:                                  # noqa: BLE001
            pass
    for row in rows:
        merged[(row["locale"], row["viewport"], row["page"])] = row
    allrows = sorted(merged.values(), key=lambda r: (r["locale"], r["viewport"], r["page"]))
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(allrows, f, ensure_ascii=False, indent=1)
    return rp, allrows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", default="both", choices=["zh", "en", "both"])
    ap.add_argument("--viewport", default="all")
    ap.add_argument("--page", default="all")
    ap.add_argument("--serve", action="store_true", help="自己起 preview(会先 build)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-shot", action="store_true", help="不落盘 PNG, 只查异常")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    locales = ["zh", "en"] if args.locale == "both" else [args.locale]
    vps = list(VIEWPORTS) if args.viewport == "all" else [int(args.viewport)]
    pages = PAGES if args.page == "all" else [p for p in PAGES if p[0] == args.page]

    if args.serve:
        with PreviewEnv(port=args.port) as env:
            rows = run(env, locales, vps, pages, shot=not args.no_shot)
    else:
        # 接手已起的 preview: 不 build, 只连浏览器
        class _Bare(PreviewEnv):
            def __enter__(self):
                from playwright.sync_api import sync_playwright
                self._pw = sync_playwright().start()
                self.browser = self._pw.chromium.launch()
                return self

            def __exit__(self, *exc):
                self.browser.close()
                self._pw.stop()

        with _Bare(port=args.port, build=False) as env:
            rows = run(env, locales, vps, pages, shot=not args.no_shot)

    rp, allrows = merge_report(rows)
    bad = [r for r in allrows if r["overflowX"] > 2 or r["raw_keys"] or r["cjk_in_en"]]
    print(f"\n累计 {len(allrows)} 条(本次 {len(rows)}), 有告警 {len(bad)} 条")
    for r in bad:
        flags = []
        if r["overflowX"] > 2:
            flags.append(f"溢出{r['overflowX']}px")
        if r["raw_keys"]:
            flags.append("键 " + ",".join(r["raw_keys"][:4]))
        if r["cjk_in_en"]:
            flags.append("中文 " + "|".join(r["cjk_in_en"][:3]))
        print(f"  ⚠ {r['locale']} {r['viewport']} {r['page']}: {' / '.join(flags)}")
    print("报告:", rp)


if __name__ == "__main__":
    main()
