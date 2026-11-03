"""填 SKU 行的三个下拉：SKU分类=单品，套装配列=100颗，图案=Ctrl。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_OPTIONS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('.next-overlay-wrapper.opened,[class*="menu"],[class*="popup"]').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 30 || r.y < 250) return;   // 排除顶部 tab
    o.querySelectorAll('li,[role=option],div[class*="item"],div[class*="option"]').forEach(li => {
      const lr = li.getBoundingClientRect();
      if (lr.height < 14 || lr.height > 70 || lr.y < 250) return;
      const lt = clean(li.innerText);
      if (!lt || lt.length > 40) return;
      out.push({ t: lt, rect: [Math.round(lr.x), Math.round(lr.y), Math.round(lr.width), Math.round(lr.height)] });
    });
  });
  const seen = new Set();
  return out.filter(o => {
    const k = o.t + o.rect.join(",");
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
"""

_TRIGGERS = [("SKU分类", 857, 656), ("套装配列", 1002, 656), ("图案", 1170, 656)]

WANT = {
    "SKU分类": "单品",
    "套装配列": "100颗",
    "图案": "Ctrl",
}


def pick(page, name: str, x: int, y: int, want: str) -> bool:
    page.mouse.click(x, y)
    page.wait_for_timeout(1500)
    opts = page.evaluate(_OPTIONS)
    print("  选项 %d 个" % len(opts))
    hit = None
    for o in opts:
        if o["t"].startswith(want):
            hit = o
            break
    if hit is None:
        print("  [XX] 没找到 %r，前 5 个: %s" % (want, [o["t"] for o in opts[:5]]))
        page.keyboard.press("Escape")
        return False
    cx = hit["rect"][0] + hit["rect"][2] // 2
    cy = hit["rect"][1] + hit["rect"][3] // 2
    page.mouse.click(cx, cy)
    page.wait_for_timeout(1200)
    print("  [OK] 选 %r @ (%d,%d)" % (hit["t"][:24], cx, cy))
    return True


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("URL: " + page.url[:70])

        for name, x, y in _TRIGGERS:
            print("\n=== %s -> %s ===" % (name, WANT[name]))
            try:
                pick(page, name, x, y, WANT[name])
            except Exception as exc:
                print("  [XX] %s" % str(exc).split("\n")[0][:80])
                page.keyboard.press("Escape")

        page.wait_for_timeout(1500)
        body = page.evaluate("() => document.body.innerText")
        # SKU 行文本
        import re
        m = re.search(r"黑色[^\n]{0,80}", body)
        print("\nSKU 行: %r" % (m.group(0) if m else "?"))
        print("还有「请选择」: %d 处" % body.count("请选择"))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_filled.png"), timeout_ms=15000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
