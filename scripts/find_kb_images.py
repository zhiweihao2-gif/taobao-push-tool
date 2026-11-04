"""打开图片空间，搜 main_0ddeb，看键盘的图在不在"""
import sys, time, json
sys.path.insert(0, ".")
from src.publish.browser_pub import BrowserSession

def log(m=""): print(m, flush=True)

with BrowserSession(reuse_url_contains="publish.htm") as s:
    page = s.page()
    page.set_viewport_size({"width": 1440, "height": 1000})
    try: page.keyboard.press("Escape")
    except Exception: pass
    time.sleep(0.5)

    # 精确点主图组内的空槽位
    r = page.evaluate(r"""() => {
      const g = document.querySelector("#struct-mainImagesGroup");
      if (!g) return {err:'no group'};
      g.scrollIntoView({block:'start'});
      const emps = [...g.querySelectorAll('.image-empty')];
      if (!emps.length) return {err:'no empty', filled: [...g.querySelectorAll('img')].length};
      const e = emps[0];
      e.scrollIntoView({block:'center'});
      const rr = e.getBoundingClientRect();
      return {ok:true, x: rr.x+rr.width/2, y: rr.y+rr.height/2, empties: emps.length};
    }""")
    log("空槽位: %s" % json.dumps(r, ensure_ascii=False))
    if not r.get("ok"):
        raise SystemExit(r.get("err","?"))

    page.mouse.click(r["x"], r["y"])
    time.sleep(3.5)

    fr = None
    for _ in range(20):
        c = [x for x in page.frames if "sucai-selector" in (x.url or "")]
        if c: fr = c[0]; break
        time.sleep(0.5)
    log("iframe: %s" % (fr.url[:90] if fr else "没出来"))
    if not fr:
        raise SystemExit("no iframe")

    time.sleep(1.5)
    info = fr.evaluate(r"""() => {
      const cards = [...document.querySelectorAll('[class*="pic_imgBox"]')];
      const sbs = [...document.querySelectorAll('input')].map(i => ({
        ph: i.placeholder, w: Math.round(i.getBoundingClientRect().width), vis: i.getBoundingClientRect().width > 0
      }));
      return {cards: cards.length, inputs: sbs,
              first5: cards.slice(0,5).map(c => (c.querySelector('img')||{}).src ? (c.querySelector('img').src).split('/').pop().slice(0,45) : null)};
    }""")
    log("图片空间: %s" % json.dumps(info, ensure_ascii=False)[:800])

    # 搜 main_0ddeb
    sb = fr.locator("input[placeholder='搜索图片名称']")
    log("搜索框 count: %d" % sb.count())
    if sb.count():
        sb.first.click(force=True)
        time.sleep(0.3)
        fr.page.keyboard.press("Control+A")
        fr.page.keyboard.press("Delete")
        for ch in "main_0ddeb":
            fr.page.keyboard.type(ch, delay=40)
        time.sleep(2.5)
        r2 = fr.evaluate(r"""() => {
          const cards = [...document.querySelectorAll('[class*="pic_imgBox"]')];
          return {n: cards.length, sample: cards.slice(0,6).map(c => (c.querySelector('img')||{}).src ? (c.querySelector('img').src).split('/').pop().slice(0,50) : null)};
        }""")
        log("搜索后: %s" % json.dumps(r2, ensure_ascii=False))
