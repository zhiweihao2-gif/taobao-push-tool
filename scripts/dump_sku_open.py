"""点「+ 创建规格」后立刻 dump（面板可能是抽屉，几秒后会关）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_SNAP = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { overlays: [], iframes: [], inputs: [], buttons: [], bodyTail: '' };

  document.querySelectorAll('.next-overlay-wrapper.opened,.next-drawer,[class*="drawer"],[class*="Drawer"],[role=dialog]').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 60) return;
    out.overlays.push({
      cls: clean(o.className).slice(0, 70),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      text: clean(o.innerText).slice(0, 400),
    });
  });

  document.querySelectorAll('iframe').forEach(f => {
    const r = f.getBoundingClientRect();
    if (r.width < 60) return;
    out.iframes.push({ src: (f.src||'').slice(0, 110), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  document.querySelectorAll('input').forEach(i => {
    const r = i.getBoundingClientRect();
    if (r.width < 20 || r.y < 0 || r.y > 1000) return;
    out.inputs.push({ ph: i.placeholder || '', val: (i.value||'').slice(0,14), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  document.querySelectorAll('button,a,[role=button]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 20 || r.height < 12 || r.y < 0 || r.y > 1000) return;
    const t = clean(b.innerText);
    if (!t || t.length > 18) return;
    out.buttons.push({ t, tag: b.tagName, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  const body = clean(document.body.innerText);
  out.bodyTail = body.slice(-500);
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("URL: " + page.url[:70])

        btn = page.locator("button").filter(has_text="创建规格").first
        btn.scroll_into_view_if_needed(timeout=6000)
        page.wait_for_timeout(500)
        print("按钮 box: %s" % btn.bounding_box())

        print("\n[1] 点击")
        btn.click(timeout=8000)
        print("    [OK]")

        # 立刻连续抓 4 次，看面板怎么出现
        for i, wait in enumerate((600, 900, 1500, 2500)):
            page.wait_for_timeout(wait)
            info = page.evaluate(_SNAP)
            print("\n--- 第 %d 次快照（+%dms） ---" % (i + 1, wait))
            print("  浮层 %d:" % len(info["overlays"]))
            for o in info["overlays"]:
                print("    %s %s" % (o["rect"], o["cls"]))
                print("      %r" % o["text"][:240])
            print("  iframe %d:" % len(info["iframes"]))
            for f in info["iframes"]:
                print("    %-70s %s" % (f["src"], f["rect"]))
            print("  输入框 %d, 按钮 %d" % (len(info["inputs"]), len(info["buttons"])))
            for inp in info["inputs"][:10]:
                print("    ph=%-14r val=%-12r %s" % (inp["ph"], inp["val"], inp["rect"]))
            for b in info["buttons"][:12]:
                print("    btn %-14r %s" % (b["t"], b["rect"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_open.png"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
