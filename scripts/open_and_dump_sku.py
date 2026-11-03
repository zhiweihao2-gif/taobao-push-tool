"""点开 SKU 抽屉并立刻 dump（一次跑完）。

抽屉类名：sku-decouple-drawer-footer / next-drawer-header
点击要用 force，因为抽屉开过一次后按钮会被遮。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { drawers: [], inputs: [], buttons: [], selects: [] };

  document.querySelectorAll('.next-overlay-wrapper.opened').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 80) return;
    out.drawers.push({
      cls: clean(o.className).slice(0, 60),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      text: clean(o.innerText).slice(0, 800),
    });

    o.querySelectorAll('input').forEach(i => {
      const ir = i.getBoundingClientRect();
      out.inputs.push({
        ph: i.placeholder || '',
        val: (i.value || '').slice(0, 20),
        rect: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)],
      });
    });

    o.querySelectorAll('button,a,[role=button],span[class*="btn"]').forEach(b => {
      const br = b.getBoundingClientRect();
      if (br.width < 20 || br.height < 10) return;
      const t = clean(b.innerText);
      if (!t || t.length > 24) return;
      out.buttons.push({ t, tag: b.tagName, cls: clean(b.className).slice(0, 36), rect: [Math.round(br.x), Math.round(br.y), Math.round(br.width), Math.round(br.height)] });
    });

    o.querySelectorAll('[class*="select"],[class*="tag"],[class*="Tag"],[class*="spec"]').forEach(s => {
      const sr = s.getBoundingClientRect();
      if (sr.width < 30 || sr.height < 15) return;
      const t = clean(s.innerText);
      if (!t || t.length > 30) return;
      out.selects.push({ t, cls: clean(s.className).slice(0, 36), rect: [Math.round(sr.x), Math.round(sr.y), Math.round(sr.width), Math.round(sr.height)] });
    });
  });
  return out;
}
"""


def dump(page, tag: str) -> dict:
    info = page.evaluate(_JS)
    print("\n=== %s ===" % tag)
    print("抽屉 %d:" % len(info["drawers"]))
    for d in info["drawers"]:
        print("  %s %s" % (d["rect"], d["cls"]))
        print("  %r" % d["text"][:600])
    print("输入框 %d:" % len(info["inputs"]))
    for i in info["inputs"]:
        print("  ph=%-18r val=%-16r %s" % (i["ph"], i["val"], i["rect"]))
    print("按钮 %d:" % len(info["buttons"]))
    seen = set()
    for b in info["buttons"]:
        k = (b["t"], tuple(b["rect"]))
        if k in seen:
            continue
        seen.add(k)
        print("  %-16r %-6s %-32s %s" % (b["t"], b["tag"], b["cls"], b["rect"]))
    print("标签 %d:" % len(info["selects"]))
    seen = set()
    for s in info["selects"]:
        k = (s["t"], tuple(s["rect"]))
        if k in seen:
            continue
        seen.add(k)
        print("  %-22r %-30s %s" % (s["t"], s["cls"], s["rect"]))
    return info


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)
        print("URL: " + page.url[:70])

        before = page.evaluate("() => document.querySelectorAll('.next-overlay-wrapper.opened').length")
        print("点击前浮层数: %d" % before)

        btn = page.locator("button").filter(has_text="创建规格").first
        btn.scroll_into_view_if_needed(timeout=6000)
        page.wait_for_timeout(500)
        print("按钮: %s" % btn.bounding_box())

        t0 = time.time()
        try:
            btn.click(timeout=6000)
            print("[OK] 正常点击")
        except Exception:
            btn.click(force=True, timeout=5000)
            print("[OK] force 点击")
        print("耗时 %.1fs" % (time.time() - t0))

        for wait, tag in ((1200, "1.2s"), (1500, "2.7s"), (2000, "4.7s")):
            page.wait_for_timeout(wait)
            info = dump(page, tag)
            if info["drawers"]:
                print("\n[!] 抽屉出现了，停在这里")
                break

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_drawer.png"), timeout_ms=15000)
        print("\n截图: data/publish_poc/sku_drawer.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
