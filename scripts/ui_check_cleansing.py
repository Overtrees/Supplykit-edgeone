"""前端验证: 清洗页列缺失硬拦 + 映射面板智能建议范围

用 playwright 直接 set_input_files(隐藏 input 也能设), 监听网络请求判断"有没有真的发出去"。
"""
import sys, time, json

from playwright.sync_api import sync_playwright

HOST = "https://supplykit.top"
CSV_NOEXP = "/tmp/t_product_noexp.csv"   # 商品表, 没有"是否做效期管理"列
CSV_EXP = "/tmp/t_product_exp.csv"       # 商品表, 有效期列


def dump_nav(page):
    """打印导航菜单文本, 便于定位"""
    txt = page.inner_text("body")
    return txt


def login(page):
    page.goto(HOST, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    # 登录表单: 用户名/密码 + 提交(输入框靠 placeholder 定位, type 不一定是 text)
    try:
        page.fill("input[placeholder*='用户名']", "admin")
        page.fill("input[placeholder*='密码']", "admin123")
        page.click("button:has-text('登录')")
    except Exception as e:
        print("登录表单填充失败:", e)
    page.wait_for_timeout(4000)


def goto_cleansing(page):
    """跳过引导页 → 点"数据清洗导入" """
    for sel in ["button:has-text('Skip')", "text=Skip"]:
        try:
            page.click(sel, timeout=3000)
            page.wait_for_timeout(1500)
            break
        except Exception:
            continue
    for sel in ["text=数据清洗导入", "text=数据清洗及导入", "button:has-text('数据清洗')"]:
        try:
            page.click(sel, timeout=4000)
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            if "上传" in body or "选择文件" in body or "导入" in body:
                return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        reqs = []
        page.on("request", lambda r: reqs.append(r.url) if "/api/cleansing" in r.url else None)
        login(page)
        print("登录后 URL:", page.url)
        print("页面片段:", dump_nav(page)[:300].replace("\n", " | "))
        ok = goto_cleansing(page)
        print("进入清洗页:", ok, "| URL:", page.url)
        print("页面片段:", dump_nav(page)[:600].replace("\n", " | "))
        page.screenshot(path="/tmp/clean_page.png", full_page=False)
        print("截图: /tmp/clean_page.png")
        print("已捕获 cleansing 请求:", reqs)
        b.close()
