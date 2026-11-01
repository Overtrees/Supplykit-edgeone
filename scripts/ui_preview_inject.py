"""§15.99.1 注入式一次核验(线上): 必填未映射=持久toast+不跳转; 补映射后跳转+提示消失;
竖屏 flex+svh 几何(回顶/不撑高/按钮在视口); 横屏回退 clamp。只读预览, 无写入。"""
from playwright.sync_api import sync_playwright
import json, sys

HOST = "https://supplykit.top"
CSV = "SKU,名称,备注\nUI-TEST-999,注入验证专用商品,x\n"
FAILS = []
def check(name, cond, actual=None):
    print(("  PASS " if cond else "  FAIL ") + name + ("" if cond else "  → %s" % (actual,)))
    if not cond: FAILS.append(name)

def geo(page):
    return page.evaluate("""() => {
      const r = s => { const e=document.querySelector(s); return e?JSON.parse(JSON.stringify(e.getBoundingClientRect())):null }
      return { vh: window.innerHeight, docH: document.documentElement.scrollHeight,
        page: r('.clean-preview-page'), scroll: r('.clean-preview-scroll'),
        actions: r('.clean-preview-actions'), scrollY: window.scrollY,
        text: document.body.innerText }
    }""")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
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
    page.wait_for_timeout(500)
    page.set_input_files("input[type='file']", {"name": "ui_test.csv", "mimeType": "text/csv",
                                                "buffer": CSV.encode("utf-8")})
    page.wait_for_timeout(2000)
    # 下拉必选后缀(模板同源口径)
    opts = page.eval_on_selector_all(".map-row >> nth=0 >> select >> option", "els=>els.map(e=>e.textContent)")
    check("下拉含「是否做效期管理(必)」", any("效期管理(必)" in o for o in opts), opts[:8])
    # 1) 未映射必填 → 点预览: 持久 toast + 不跳转
    page.click("button:has-text('下一步 · 预览')", timeout=6000)
    page.wait_for_timeout(2800)  # 预览请求返回后才弹 toast, 抓早了会漏
    g = geo(page)
    print("拦截toast全文片段:", [l for l in g["text"].split("\n") if "必填" in l])
    check("不跳转(仍在映射页)", g["scroll"] is None)
    check("持久toast出现", "未映射必填列" in g["text"] and "效期管理" in g["text"], g["text"][:200])
    page.wait_for_timeout(4000)  # 普通 toast 3s 已消失; 持久必须还在
    g2 = geo(page)
    check("toast 4s 后仍在(持久)", "未映射必填列" in g2["text"])
    # 2) 补映射 → 再点预览: 跳转 + 提示消失
    # 补齐必填映射: SKU→sku 名称→product_name 备注→expiry_managed(nth: 0渠道/1类型/2起映射行)
    page.select_option(".map-row >> nth=0 >> select", value="sku")
    page.select_option(".map-row >> nth=1 >> select", value="product_name")
    page.select_option(".map-row >> nth=2 >> select", value="expiry_managed")
    page.wait_for_timeout(500)
    page.click("button:has-text('下一步 · 预览')", timeout=6000)
    page.wait_for_timeout(4000)
    g3 = geo(page)
    check("跳转进入预览页", g3["scroll"] is not None)
    check("toast 已消失(跳转即清)", "未映射必填列" not in g3["text"])
    check("进预览回顶", g3["scrollY"] == 0, g3["scrollY"])
    check("竖屏页面不撑高(docH ≤ vh)", g3["docH"] <= g3["vh"] + 2, "%s vs %s" % (g3["docH"], g3["vh"]))
    check("竖屏写入按钮在视口内", g3["actions"] and g3["actions"]["bottom"] <= g3["vh"] + 1, g3["actions"])
    check("竖屏表格容器可见(高>120)", g3["scroll"] and g3["scroll"]["height"] > 120, g3["scroll"])
    page.screenshot(path="/tmp/inj2_port.png")
    # 3) 横屏: 回退 clamp 行为
    page.set_viewport_size({"width": 844, "height": 390}); page.wait_for_timeout(1200)
    g4 = geo(page)
    check("横屏表格容器限高生效(≤78dvh=304)", g4["scroll"] and g4["scroll"]["height"] <= 305, g4["scroll"])
    check("横屏页面不撑高(docH ≤ vh+60 旧文档流行为)", g4["docH"] <= g4["vh"] + 60, "%s vs %s" % (g4["docH"], g4["vh"]))
    page.screenshot(path="/tmp/inj2_land.png")
    b.close()
    print("\nALL PASS" if not FAILS else "\nFAILED: %s" % FAILS)
    sys.exit(1 if FAILS else 0)
