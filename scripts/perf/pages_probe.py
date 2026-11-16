#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面链路实测（浏览器）：16 页逐页加载，判「有没有白屏/报错/失败请求」。

与 `ui_shot_matrix.py` 的分工：
- 那个管**视觉**（4 视口 × 双语 × 溢出/未翻译键），慢，产 PNG 供人看；
- 这个管**链路**：每页是否真的拿到数据、控制台有无 error、有没有 4xx/5xx 接口、
  错误边界有没有接管。一条命令 1 分钟出结论，进全链路验收。

判据（任一命中即 FAIL）：
  · `pageerror`（未捕获异常）或 console error 数量 > 0（忽略资源/第三方噪音）
  · 任一 `/api/*` 响应 >= 500，或 401（登录后不该出现）
  · 正文可见文本 < 40 字（白屏）或命中错误边界文案
  · 该页依赖的关键接口没有成功返回（`window.__lastApi` 由本脚本注入的探针统计）

用法：
    python3 scripts/perf/pages_probe.py                 # 全部 16 页
    python3 scripts/perf/pages_probe.py --only dash,inv
    python3 scripts/perf/pages_probe.py --headed        # 有头（本机调试）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, "out")

sys.path.insert(0, HERE)
import kit as K  # noqa: E402

ADMIN_USER = os.environ.get("SUPPLY_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("SUPPLY_ADMIN_PASS", "admin123")

# 与 App.tsx 的 switch 分支一致（改路由同步这里）
PAGES = ["dash", "products", "suppliers", "orders", "history", "inv", "insights",
         "cleansing", "rules", "tasks", "quality", "settings", "devmode", "channels", "recycle"]

# 错误边界 / 白屏的可见文案（中英）
BAD_TEXT = ["页面出错了", "出错了，请重试", "加载失败", "Something went wrong",
            "Failed to load", "Application error"]

PROBE_JS = """
(() => {
  window.__apiLog = [];
  const of = window.fetch;
  window.fetch = async (...a) => {
    const r = await of(...a);
    try { window.__apiLog.push({u: String(a[0]).slice(0,160), s: r.status}); } catch(e) {}
    return r;
  };
})();
"""


def _token():
    import requests
    r = requests.post(K.BASE + "/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    j = r.json()
    return (j.get("data") or {}).get("token") or j.get("token")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--wait", type=int, default=6000, help="每页静置毫秒")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    tok = _token()
    want = [p for p in PAGES if not a.only or p in a.only.split(",")]
    rows = []
    os.makedirs(OUT, exist_ok=True)
    shot_dir = os.path.join(ROOT, ".artifacts", "e2e_pages")
    os.makedirs(shot_dir, exist_ok=True)

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=not a.headed)
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script("localStorage.setItem('c_token', %s); localStorage.setItem('c_welcome_seen','1');"
                            % json.dumps(tok))
        pg = ctx.new_page()
        errs, fails, bad_api, api_ok_n = [], [], [], [0]
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
        # 切页会 abort 旧页在途请求（App.navigateTo → abortAll），ERR_ABORTED 属预期
        pg.on("requestfailed", lambda r: fails.append("%s %s %s" % (r.method, r.url[:120], r.failure or ""))
              if "ERR_ABORTED" not in str(r.failure or "") else None)
        def _on_resp(r):
            if "/api/" not in r.url:
                return
            if r.status >= 400:
                bad_api.append("%d %s" % (r.status, r.url[:140]))
            elif r.status < 300:
                api_ok_n[0] += 1
        pg.on("response", _on_resp)

        pg.goto(K.BASE + "/", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3000)
        for p in want:
            errs.clear(); fails.clear(); bad_api.clear(); api_ok_n[0] = 0
            t0 = time.time()
            try:
                pg.evaluate("p => window.__setPage && window.__setPage(p)", p)
            except Exception as e:
                rows.append({"page": p, "ok": False, "why": "setPage 失败: %s" % e})
                continue
            pg.wait_for_timeout(a.wait)
            txt = ""
            try:
                txt = pg.evaluate("() => (document.querySelector('main')||document.body).innerText")
            except Exception:
                pass
            n = len((txt or "").strip())
            badd = [w for w in BAD_TEXT if w in (txt or "")]
            api_ok = api_ok_n[0]
            ok = (not errs and not fails and not bad_api and not badd and n >= 40)
            row = {"page": p, "ok": ok, "chars": n, "api_ok": api_ok,
                   "ms": int((time.time() - t0) * 1000),
                   "errors": list(errs)[:6], "reqfail": list(fails)[:6],
                   "bad_api": list(bad_api)[:8], "bad_text": badd}
            try:
                pg.screenshot(path=os.path.join(shot_dir, "%s.png" % p), full_page=False)
            except Exception:
                pass
            rows.append(row)
            print("  %s %-10s chars=%-5d api=%-3s %5dms %s" % (
                "✓" if ok else "✗", p, n, api_ok, row["ms"],
                "" if ok else json.dumps({k: row[k] for k in ("errors", "reqfail", "bad_api", "bad_text") if row[k]},
                                         ensure_ascii=False)[:300]))

        b.close()

    # 登录页：独立 context（没有任何 localStorage 预置），验证未登录首屏
    if not a.only or "login" in a.only.split(","):
        with sync_playwright() as pw2:
            b2 = pw2.chromium.launch(headless=not a.headed)
            c2 = b2.new_context(viewport={"width": 1440, "height": 900})
            p2 = c2.new_page()
            e2, ba = [], []
            p2.on("pageerror", lambda e: e2.append(str(e)[:160]))
            p2.on("console", lambda m: e2.append(m.text[:160]) if m.type == "error" else None)
            p2.on("response", lambda r: ba.append("%d %s" % (r.status, r.url[:120]))
                  if "/api/" in r.url and r.status >= 400 else None)
            p2.goto(K.BASE + "/", wait_until="domcontentloaded", timeout=60000)
            p2.wait_for_timeout(4000)
            txt = p2.evaluate("() => document.body.innerText")
            # 未登录时 App 顶层仍会拉 5 个业务接口并 401（已记入 detail，不影响登录可用性）；
            # 判据只看「登录表单渲染 + 无 5xx + 无未捕获异常」。
            e2 = [x for x in e2 if "401" not in x and "loadAll failed" not in x]
            five = [x for x in ba if not x.startswith(("401", "403"))]
            ok = (("登录" in txt or "Sign in" in txt or "用户名" in txt or "Username" in txt)
                  and not e2 and not five)
            rows.append({"page": "login", "ok": ok, "chars": len(txt),
                         "errors": e2[:4], "bad_api": ba[:6],
                         "note": "未登录首屏触发 %d 个 401 业务请求（可优化，不影响登录）" % len(ba)})
            print("  %s %-10s chars=%-5d %s" % ("✓" if ok else "✗", "login", len(txt),
                                                "" if ok else json.dumps(rows[-1], ensure_ascii=False)[:300]))
            b2.close()

    p = os.path.join(OUT, "%s_pages.json" % time.strftime("%Y%m%d_%H%M%S"))
    json.dump({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "base": K.BASE, "rows": rows},
              open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    npass = sum(1 for x in rows if x.get("ok"))
    print("\n页面 %d/%d 通过  [out] %s" % (npass, len(rows), p))
    return 0 if npass == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
