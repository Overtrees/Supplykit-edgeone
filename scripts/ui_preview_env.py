"""本地预览环境: 构建 dist + 起 vite preview(带 /api 代理)+ 关掉, 给 UI 脚本复用。

为什么需要单独一个模块 —— 踩过三个坑, 每个都会让脚本"看起来成功但全是废话":

1. **dist/sw.js 拦截 /api/**: 它在 fetch 失败时返回 `{ok:false,error:'offline'}` 假响应,
   无头浏览器里登录永远提示"用户名或密码错误"。必须 `service_workers='block'`。
2. **线上后端没开 CORS**: 从 `http://localhost` 直连 `https://supplykit.top/api/*` 会被浏览器拦
   (preflight 无 Access-Control-Allow-Origin)。必须走 vite 的 proxy, 见 vite.config.js 的 preview.proxy。
3. **登录后 onLogin 清 c_welcome_seen 弹引导页**: 不点掉它, 每页截图都是引导页, 而且不报错。

用法:
    from ui_preview_env import PreviewEnv
    with PreviewEnv() as env:          # 构建 + 起服务, 退出自动 kill
        page = env.new_page(posix_w=390, locale='zh')
        env.login(page)
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
DEFAULT_PORT = 4173

# 登录后的首访引导页"跳过"按钮文案(随语言变), 用来把覆盖层收掉
SKIP_TEXTS = ["跳过，直接进入", "Skip", "Skip and continue"]


class PreviewEnv:
    """构建前端 → 起 vite preview → 提供建页/登录/切页 的便捷方法。"""

    def __init__(self, port: int = DEFAULT_PORT, build: bool = True,
                 proxy_target: str | None = None):
        self.port = port
        self.build = build
        self.proxy_target = proxy_target or os.environ.get("VITE_PROXY_TARGET", "")
        self.proc: subprocess.Popen | None = None
        self._pw = None
        self.browser = None

    # ---- 生命周期 ----
    def __enter__(self):
        if self.build:
            env = dict(os.environ)
            env.pop("VITE_API_BASE_URL", None)   # 留空 → 走同源, 由 proxy 转发
            print("[preview] building dist ...")
            subprocess.run(["npx", "vite", "build"], cwd=FRONTEND, env=env,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        env = dict(os.environ)
        if self.proxy_target:
            env["VITE_PROXY_TARGET"] = self.proxy_target
        print(f"[preview] serving on :{self.port} ...")
        self.proc = subprocess.Popen(
            ["npx", "vite", "preview", "--port", str(self.port), "--strictPort"],
            cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        self._wait_up()
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        return self

    def __exit__(self, *exc):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()
            if self.proc:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()

    def _wait_up(self, timeout: float = 60):
        url = f"http://localhost:{self.port}/"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.5)
        raise RuntimeError("vite preview 没有起来, 检查端口被占用或构建失败")

    # ---- 页面 ----
    def new_page(self, posix_w: int = 390, height: int = 844, locale: str = "zh",
                 scale: int = 2):
        """建一个已注入 locale/welcome 标记、屏蔽 SW 的 context, 返回 page。

        locale: 'zh' | 'en' —— 写 localStorage['c_locale']; 同时设浏览器 locale,
        影响 navigator.language(不设会让 detectLocale 按 en-US 走)。
        """
        ctx = self.browser.new_context(
            viewport={"width": posix_w, "height": height},
            device_scale_factor=scale,
            locale="zh-CN" if locale == "zh" else "en-US",
            service_workers="block",   # 坑 1
        )
        page = ctx.new_page()
        page.add_init_script(
            "try{localStorage.setItem('c_locale','%s');"
            "localStorage.setItem('c_welcome_seen','1')}catch(e){}" % locale
        )
        return page

    def login(self, page, user: str = "admin", pwd: str = "admin123") -> bool:
        """登录并收掉首访引导页。返回是否真的进了主界面。"""
        page.goto(f"http://localhost:{self.port}/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        if page.locator('input[type="password"]').count() == 0:
            return True                                  # 已是登录态
        try:
            # 用户名框没有 type 属性, 用 placeholder 关键字兜底(随语言变)
            page.locator(
                'input[placeholder*="USERNAME" i], input[placeholder*="账号" i], input:not([type])'
            ).first.fill(user, timeout=8000)
            page.locator('input[type="password"]').first.fill(pwd)
            page.locator('input[type="password"]').first.press("Enter")
            page.wait_for_timeout(4000)
        except Exception as e:                            # noqa: BLE001
            print("  登录异常:", str(e)[:160])
        if page.locator('input[type="password"]').count() > 0:
            body = page.locator("body").inner_text()[:160].replace("\n", " | ")
            print("  登录未成功, 页面:", body)
            return False
        # 坑 3: onLogin 会清 c_welcome_seen 并弹引导页 —— 补回标记再点"跳过"
        page.evaluate(
            """(skips) => {
                localStorage.setItem('c_welcome_seen', '1')
                const el = [...document.querySelectorAll('button,a,div,span')].find(
                    e => skips.includes((e.textContent || '').trim()) && e.offsetParent !== null
                )
                if (el) el.click()
            }""",
            SKIP_TEXTS,
        )
        page.wait_for_timeout(1500)
        return True

    @staticmethod
    def goto(page, page_id: str, wait: int = 2200) -> bool:
        """切成某个页面(走 App 暴露的 window.__setPage), 并回读确认真的切过去了。"""
        ok = page.evaluate(
            """(pid) => {
                try { window.__setPage && window.__setPage(pid); return true } catch(e) { return false }
            }""",
            page_id,
        )
        if not ok:
            return False
        page.wait_for_timeout(wait)
        return page.evaluate('() => window.__page || ""') == page_id
