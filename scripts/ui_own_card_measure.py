"""自有仓健康大卡实测 —— 多设备断言结构/三类露头/更多入口/弹窗开关。只读。"""
from playwright.sync_api import sync_playwright

HOST = "https://supplykit.top"

MEASURE = """() => {
  const out = { cards: [], ownCard: null, rows: [], moreBtn: null }
  // 1) 定位所有 chart-row-3 里的大卡
  document.querySelectorAll('.chart-row-3 > .card').forEach(c => {
    const t = c.querySelector('.section-title')
    const b = c.getBoundingClientRect()
    out.cards.push({ title: (t ? t.textContent : '').trim().slice(0, 30),
                     w: Math.round(b.width), h: Math.round(b.height),
                     sh: c.scrollHeight, ch: c.clientHeight })
  })
  // 2) 自有仓卡
  let own = null
  document.querySelectorAll('.chart-row-3 > .card').forEach(c => {
    const t = c.querySelector('.section-title')
    if (t && t.textContent.indexOf('自有仓健康') === 0) own = c
  })
  if (!own) return out
  const b = own.getBoundingClientRect()
  out.ownCard = { title: own.querySelector('.section-title').textContent.trim(),
                  w: Math.round(b.width), h: Math.round(b.height),
                  sh: own.scrollHeight, ch: own.clientHeight,
                  overflow: own.scrollHeight > own.clientHeight + 1 }
  // 3) 明细行(padding 8px 0 + borderBottom)
  own.querySelectorAll(':scope > div').forEach(d => {
    const s = getComputedStyle(d)
    if (s.borderBottomWidth === '1px' && s.paddingTop === '8px' && s.borderBottomStyle === 'solid') {
      const pill = d.querySelector('.pill')
      const spans = d.querySelectorAll('span')
      out.rows.push({ pill: pill ? pill.textContent.trim() : '',
                      pillCls: pill ? pill.className.replace('pill ', '') : '',
                      text: (spans.length ? d.textContent : '').trim().slice(0, 48) })
    }
  })
  // 4) 底部「更多」
  const mb = own.querySelector('.pill.info')
  if (mb) {
    const r = mb.getBoundingClientRect()
    out.moreBtn = { text: mb.textContent.trim(),
                    fontSize: getComputedStyle(mb).fontSize,
                    h: Math.round(r.height),
                    w: Math.round(r.width) }
  }
  // 5) 整页是否横向溢出
  out.docOverflowX = document.documentElement.scrollWidth > window.innerWidth + 1
  return out
}"""

POPUP = """() => {
  const sheets = [...document.querySelectorAll('div')].filter(d => {
    const t = d.textContent || ''
    return t.indexOf('自有仓健康 · 共') === 0 || (t.indexOf('自有仓健康 · 共') > -1 && t.length < 60)
  })
  if (!sheets.length) return { open: false }
  const head = sheets[0]
  // 向上找弹窗容器(带 maxHeight 70vh 的那个)
  let box = head
  for (let i = 0; i < 6 && box.parentElement; i++) {
    box = box.parentElement
    if (getComputedStyle(box).maxHeight === '70vh' || getComputedStyle(box).overflowY === 'auto') break
  }
  const pills = [...box.querySelectorAll('.pill')].map(p => p.textContent.trim())
  const cnt = {}
  pills.forEach(x => { cnt[x] = (cnt[x] || 0) + 1 })
  return { open: true, title: head.textContent.trim(),
           rowCount: box.querySelectorAll('div[style*="7px 0"]').length || pills.length,
           pills: cnt }
}"""


DEVICES = [
    ("iPhone SE 竖", 375, 667, True),
    ("iPhone 14 竖", 390, 844, True),
    ("iPhone 14 横", 844, 390, True),
    ("iPad 竖", 768, 1024, True),
    ("iPad 横", 1024, 768, True),
    ("桌面", 1440, 900, False),
]


def login(b, w, h, mob):
    """每台设备独立登录 —— 复用 storage_state 会让看板停留在首屏骨架, 测量失效。"""
    ctx = b.new_context(viewport={"width": w, "height": h},
                        is_mobile=mob, has_touch=mob)
    pg = ctx.new_page()
    pg.goto(HOST, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.fill("input[placeholder*='用户名']", "admin")
    pg.fill("input[placeholder*='密码']", "admin123")
    pg.click("button:has-text('登录')")
    pg.wait_for_timeout(6000)
    for _ in range(2):
        try:
            pg.click("button:has-text('Skip')", timeout=2000)
            pg.wait_for_timeout(800)
        except Exception:
            pass
    # 等自有仓卡真正挂载
    for _ in range(20):
        if pg.evaluate("() => !!document.body.innerText.match(/自有仓健康/)"):
            break
        pg.wait_for_timeout(600)
    return ctx, pg


def run():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        for name, w, h, mob in DEVICES:
            ctx, page = login(b, w, h, mob)
            try:
                page.evaluate("() => window.scrollTo(0, 0)")
            except Exception:
                pass
            m = page.evaluate(MEASURE)
            print(f"\n===== {name}  {w}x{h} =====")
            if not m.get("ownCard"):
                print("  !! 未找到自有仓大卡; chart-row-3 卡片:", [c["title"] for c in m["cards"]])
                ctx.close()
                continue
            oc = m["ownCard"]
            print(f"  标题: {oc['title']}")
            print(f"  尺寸: {oc['w']}x{oc['h']}  scrollH={oc['sh']} clientH={oc['ch']}  溢出={oc['overflow']}")
            print(f"  同行大卡: {[c['title'] for c in m['cards']]}")
            print(f"  明细行({len(m['rows'])}):")
            for r in m["rows"]:
                print(f"    [{r['pill']}/{r['pillCls']}] {r['text']}")
            if m.get("moreBtn"):
                mb = m["moreBtn"]
                print(f"  更多入口: '{mb['text']}' font={mb['fontSize']} {mb['w']}x{mb['h']}")
            else:
                print("  更多入口: 无(总项数 <= 5 ?)")
            print(f"  页面横向溢出: {m['docOverflowX']}")
            ctx.close()

        # 弹窗验证(390x844)
        ctx, page = login(b, 390, 844, True)
        clicked = False
        for sel in ["text=更多", ".chart-row-3 >> text=自有仓健康"]:
            try:
                page.locator(sel).first.click(timeout=3000)
                clicked = True
                break
            except Exception:
                continue
        page.wait_for_timeout(1200)
        print("\n===== 弹窗(390x844) =====")
        print("  触发:", clicked)
        st = page.evaluate(POPUP)
        print("  ", st)
        # 关闭
        try:
            page.locator("text=关闭").last.click(timeout=3000)
            page.wait_for_timeout(800)
            print("  关闭后 open:", page.evaluate(POPUP).get("open"))
        except Exception as e:
            print("  关闭失败:", type(e).__name__)
        ctx.close()
        b.close()


if __name__ == "__main__":
    run()
