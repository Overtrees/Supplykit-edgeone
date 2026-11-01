"""§15.99 注入式一次核验: 预览页必填警示条 + 竖屏首帧可见 + 横屏按钮在视口内。
只读预览 + 被硬拦的写入, 不产生任何数据残留。"""
from playwright.sync_api import sync_playwright
import json, sys

HOST = "https://supplykit.top"
CSV = "SKU,名称\nUI-TEST-999,注入验证专用商品\n"
FAILS = []
def check(name, cond, actual=None):
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else "  → %s" % (actual,)))
    if not cond: FAILS.append(name)

def geo(page):
    return page.evaluate("""() => {
      const sc = document.querySelector('.clean-preview-scroll')
      const act = document.querySelector('.clean-preview-actions')
      const bar = [...document.querySelectorAll('div')].find(d =>
        d.textContent && d.textContent.includes('未映射必填列'))
      const tb = document.querySelector('.clean-preview-scroll table, .clean-preview-scroll tbody')
      const r = e => e ? JSON.parse(JSON.stringify(e.getBoundingClientRect())) : null
      return { scroll: r(sc), actions: r(act), warnBar: r(bar), table: r(tb),
        scrollY: window.scrollY, vh: window.innerHeight, vw: window.innerWidth,
        docH: document.documentElement.scrollHeight }
    }""")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
    # 手机竖屏
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    toasts = []
    page.on("console", lambda m: None)
    page.goto(HOST, wait_until="domcontentloaded"); page.wait_for_timeout(2500)
    page.fill("input[placeholder*='用户名']", "admin")
    page.fill("input[placeholder*='密码']", "admin123")
    page.click("button:has-text('登录')"); page.wait_for_timeout(4500)
    try: page.click("button:has-text('Skip')", timeout=3000); page.wait_for_timeout(1200)
    except Exception: pass
    page.click(".menu-btn", timeout=5000); page.wait_for_timeout(800)
    page.get_by_text("数据清洗及导入", exact=False).first.click(timeout=5000)
    page.wait_for_timeout(2000)
    assert "选择导入类型" in page.inner_text("body"), "未进入清洗页"
    page.select_option("select >> nth=1", value="product")
    page.wait_for_timeout(600)
    # 监听 toast(react-toastify)
    page.evaluate("""() => {
      window.__toasts = []
      new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
        if (n.nodeType===1 && n.classList && (n.classList+'').includes('Toastify'))
          window.__toasts.push(n.innerText)
      }))).observe(document.body, {childList: true, subtree: true})
    }""")
    page.set_input_files("input[type='file']", {"name": "ui_test.csv", "mimeType": "text/csv",
                                                "buffer": CSV.encode("utf-8")})
    page.wait_for_timeout(2000)
    page.click("button:has-text('下一步 · 预览')", timeout=6000)
    page.wait_for_timeout(4500)
    g = geo(page)
    print("[竖屏 390x844]", json.dumps(g, ensure_ascii=False)[:400])
    toasts = page.evaluate("() => window.__toasts || []")
    print("toasts:", toasts)
    check("必填预警 toast(效期管理)", any("效期管理" in t for t in toasts), toasts)
    check("警示条已渲染且可见", g["warnBar"] and g["warnBar"]["height"] > 10, g["warnBar"])
    check("进预览回顶(不手动滚动即见内容)", g["scrollY"] == 0, g["scrollY"])
    check("表格首帧可见(高度>100)", g["table"] and g["table"]["height"] > 100, g["table"])
    check("页面不被撑高(docH ≈ vh)", g["docH"] <= g["vh"] * 1.25, g["docH"])
    check("写入按钮在视口内", g["actions"] and g["actions"]["bottom"] <= g["vh"] + 1, g["actions"])
    # 点写入: 必须被通用硬拦(无任何写请求)
    page.click("button:has-text('确认写入')", timeout=4000)
    page.wait_for_timeout(1500)
    toasts2 = page.evaluate("() => window.__toasts || []")
    check("写入被硬拦 toast", any("必须映射必填列" in t for t in toasts2[len(toasts):]), toasts2)
    # 横屏
    page.set_viewport_size({"width": 844, "height": 390})
    page.wait_for_timeout(1200)
    g2 = geo(page)
    print("[横屏 844x390]", json.dumps(g2, ensure_ascii=False)[:400])
    check("横屏表格可见(高度>80)", g2["table"] and g2["table"]["height"] > 80, g2["table"])
    check("横屏 docH ≈ vh(不被撑高→无 backdrop 采样遮挡)", g2["docH"] <= g2["vh"] * 1.3, g2["docH"])
    check("横屏按钮在视口内", g2["actions"] and g2["actions"]["bottom"] <= g2["vh"] + 1, g2["actions"])
    check("横屏仍在预览页(未误触发跳步)", g2["warnBar"] is not None)
    page.screenshot(path="/tmp/inj_portrait.png")
    print("\nALL PASS" if not FAILS else "\nFAILED: %s" % FAILS)
    sys.exit(1 if FAILS else 0)
