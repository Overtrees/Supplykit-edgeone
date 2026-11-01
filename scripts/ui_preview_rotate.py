"""复现: 清洗页预览 - 竖屏进入 / 不滚动直接横屏 的布局异常

只读前端行为(上传/预览不发 execute), 不打库。
输出每步的几何量: page scrollY, body 高度, header 位置, 预览容器高度与可见性。
"""
import json
import sys

from playwright.sync_api import sync_playwright

import os
HOST = os.environ.get("UI_HOST", "https://supplykit.top")
REMOTE = "https://supplykit.top"
CSV = "/tmp/t_inv_preview.csv"
N = 60


def make_csv():
    rows = ["SKU,仓库,69码,期初库存,在途,商品名称"]
    for i in range(N):
        rows.append("SKUP%03d,华东C仓,69000000%04d,%d,%d,预览测试商品%d" % (i, i, 10 + i, i % 5, i))
    open(CSV, "w", encoding="utf-8").write("\n".join(rows) + "\n")


def login(page):
    page.goto(HOST, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    page.fill("input[placeholder*='用户名']", "admin")
    page.fill("input[placeholder*='密码']", "admin123")
    page.click("button:has-text('登录')")
    page.wait_for_timeout(4000)


def goto_cleansing(page):
    for sel in ["button:has-text('Skip')", "text=Skip"]:
        try:
            page.click(sel, timeout=3000)
            page.wait_for_timeout(1500)
            break
        except Exception:
            continue
    for _ in range(3):
        try:
            page.click(".menu-btn", timeout=4000)
            page.wait_for_timeout(900)
            page.get_by_text("数据清洗及导入", exact=False).first.click(timeout=4000)
            page.wait_for_timeout(2500)
            if "选择导入类型" in page.inner_text("body"):
                return True
        except Exception as e:
            print("导航重试:", str(e)[:80])
    return False


def geo(page, tag):
    js = """() => {
      const sc = document.querySelector('.clean-preview-scroll');
      const ac = document.querySelector('.clean-preview-actions');
      const hd = document.querySelector('header');
      const tb = document.querySelector('.clean-preview-scroll table');
      const r = el => el ? (x => ({top: Math.round(x.top), bottom: Math.round(x.bottom),
                                   h: Math.round(x.height), w: Math.round(x.width)}))(el.getBoundingClientRect()) : null;
      return {
        vw: window.innerWidth, vh: window.innerHeight,
        visualVh: Math.round(window.visualViewport ? window.visualViewport.height : -1),
        scrollY: Math.round(window.scrollY),
        docH: Math.round(document.documentElement.scrollHeight),
        bodyH: Math.round(document.body.scrollHeight),
        scroll: r(sc), actions: r(ac), header: r(hd), table: r(tb),
        fits: document.documentElement.scrollHeight <= window.innerHeight + 1,
        actionsVisible: ac ? (ac.getBoundingClientRect().bottom <= window.innerHeight + 1) : null,
        tableRows: tb ? tb.querySelectorAll('tbody tr').length : 0,
        bodyText: document.body.innerText.slice(0, 60)
      };
    }"""
    d = page.evaluate(js)
    print("[%s] %s" % (tag, json.dumps(d, ensure_ascii=False)))
    return d


if __name__ == "__main__":
    make_csv()
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                            is_mobile=True, has_touch=True,
                            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        page = ctx.new_page()
        if "127.0.0.1" in HOST or "localhost" in HOST:
            def _fwd(route):
                try:
                    route.fulfill(response=route.fetch(url=route.request.url.replace(HOST, REMOTE)))
                except Exception as e:
                    print("proxy fail:", str(e)[:80])
                    route.continue_()
            page.route("**/api/**", _fwd)
        page.on("console", lambda m: None)
        login(page)
        ok = goto_cleansing(page)
        print("进入清洗页:", ok)
        geo(page, "清洗页-竖屏首屏")
        page.select_option("select >> nth=1", value="inventory")
        page.wait_for_timeout(300)
        page.set_input_files("input[type=file]", CSV)
        page.wait_for_timeout(6000)
        geo(page, "识别后-竖屏")
        try:
            page.click("button:has-text('下一步')", timeout=5000)
        except Exception as e:
            print("点下一步失败:", str(e)[:120])
            print(page.inner_text("body")[:400])
            sys.exit(3)
        page.wait_for_timeout(5000)
        geo(page, "预览-竖屏(未滚动)")
        page.screenshot(path="/tmp/prev_portrait.png")

        # 不滚动, 直接横屏
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_timeout(800)
        geo(page, "横屏 800ms")
        page.screenshot(path="/tmp/prev_land_800.png")
        page.wait_for_timeout(1500)
        geo(page, "横屏 2.3s")
        page.screenshot(path="/tmp/prev_land_2300.png")

        # 回竖屏
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(1200)
        geo(page, "回竖屏")
        page.screenshot(path="/tmp/prev_port_back.png")
        b.close()
    print("截图: /tmp/prev_portrait.png /tmp/prev_land_800.png /tmp/prev_land_2300.png")
