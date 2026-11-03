"""用坐标点 SKU 行的三个下拉，读选项。

get_by_text 点不动（命中内部文本节点，被遮挡），改用 mouse.click 中心点。
坐标：SKU分类 (857,656) / 套装配列 (1002,656) / 图案 (1170,656)
"""
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
  const out = { menus: [], options: [] };
  document.querySelectorAll('.next-overlay-wrapper.opened,.next-menu,.next-select-menu,[class*="menu"],[class*="popup"]').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 30) return;
    const t = clean(o.innerText);
    if (!t) return;
    out.menus.push({ rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], text: t.slice(0, 160) });
    o.querySelectorAll('li,[role=option],div[class*="item"],div[class*="option"]').forEach(li => {
      const lr = li.getBoundingClientRect();
      if (lr.height < 14 || lr.height > 70) return;
      const lt = clean(li.innerText);
      if (!lt || lt.length > 30) return;
      out.options.push({ t: lt, rect: [Math.round(lr.x), Math.round(lr.y), Math.round(lr.width), Math.round(lr.height)] });
    });
  });
  // 去重
  const seen = new Set();
  out.options = out.options.filter(o => {
    const k = o.t + o.rect.join(",");
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return out;
}
"""

TARGETS = [("SKU分类", 857, 656), ("套装配列", 1002, 656), ("图案", 1170, 656)]


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("URL: " + page.url[:70])

        for name, x, y in TARGETS:
            print("\n=== %s (%d,%d) ===" % (name, x, y))
            page.mouse.click(x, y)
            page.wait_for_timeout(1600)
            opt = page.evaluate(_OPTIONS)
            print("  菜单 %d:" % len(opt["menus"]))
            for m in opt["menus"][:3]:
                print("    %s %r" % (m["rect"], m["text"][:100]))
            print("  选项 %d:" % len(opt["options"]))
            for o in opt["options"][:24]:
                print("    %-22r %s" % (o["t"], o["rect"]))
            page.keyboard.press("Escape")
            page.wait_for_timeout(700)

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_dd3.png"), timeout_ms=15000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
