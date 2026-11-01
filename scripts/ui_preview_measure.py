"""实测预览页竖屏(390x844) overhead 分解 —— 给"调大视口"提供可压缩空间数据。只读。"""
from playwright.sync_api import sync_playwright

HOST = "https://supplykit.top"
CSV = "SKU,名称,备注\nUI-TEST-999,注入验证专用商品,x\n"

MEASURE = """() => {
  const r = s => { const e=document.querySelector(s); return e?e.getBoundingClientRect():null }
  const cs = el => el?getComputedStyle(el):null
  const page = document.querySelector('.clean-preview-page')
  // 向上回溯 5 层祖先, 记录每个的 padding-top/bottom 与高度, 定位 overhead 来源
  const chain = []
  let n = page
  for (let i=0;i<6 && n;i++){
    const s = cs(n)
    chain.push({tag:n.tagName, cls:(n.className||'').toString().slice(0,40),
      pt:s.paddingTop, pb:s.paddingBottom, h:Math.round(n.getBoundingClientRect().height),
      top:Math.round(n.getBoundingClientRect().top)})
    n = n.parentElement
  }
  const pr = r('.clean-preview-page'), sr=r('.clean-preview-scroll'), ar=r('.clean-preview-actions')
  // 预览页内部: 步骤条/标题(非scroll/actions的部分)
  return { vh: window.innerHeight,
    page: pr?{top:Math.round(pr.top),bottom:Math.round(pr.bottom),h:Math.round(pr.height)}:null,
    scroll: sr?{top:Math.round(sr.top),h:Math.round(sr.height)}:null,
    actions: ar?{top:Math.round(ar.top),h:Math.round(ar.height),bottom:Math.round(ar.bottom)}:null,
    chain }
}"""

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
    page.select_option("select >> nth=1", value="product"); page.wait_for_timeout(500)
    page.set_input_files("input[type='file']", {"name":"t.csv","mimeType":"text/csv","buffer":CSV.encode()})
    page.wait_for_timeout(2000)
    page.select_option(".map-row >> nth=0 >> select", value="sku")
    page.select_option(".map-row >> nth=1 >> select", value="product_name")
    page.select_option(".map-row >> nth=2 >> select", value="expiry_managed")
    page.wait_for_timeout(500)
    page.click("button:has-text('下一步 · 预览')", timeout=6000); page.wait_for_timeout(3500)
    m = page.evaluate(MEASURE)
    vh=m["vh"]
    print(f"视口 vh = {vh}")
    print(f"预览页 page: top={m['page']['top']} bottom={m['page']['bottom']} h={m['page']['h']}")
    print(f"  overhead_上 = {m['page']['top']}")
    print(f"  overhead_下 = {vh - m['page']['bottom']}  (应=card pb + container pb)")
    print(f"表格 scroll: h={m['scroll']['h']}")
    print(f"按钮区 actions: h={m['actions']['h']} bottom={m['actions']['bottom']}")
    print(f"  预览页内部头部(非scroll/actions) = {m['page']['h'] - m['scroll']['h'] - m['actions']['h']}")
    print("祖先链(padding分解):")
    for c in m["chain"]:
        print(f"  {c['tag']}.{c['cls']}  pt={c['pt']} pb={c['pb']} h={c['h']} top={c['top']}")
    b.close()
