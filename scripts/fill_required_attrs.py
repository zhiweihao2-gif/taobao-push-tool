"""填「适配轴体类型」（下拉）+ 第二个「型号」（文本）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_type  # noqa: E402

_OPTIONS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('.next-overlay-wrapper.opened,[class*="menu"],[class*="popup"]').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 30 || r.y < 100) return;
    o.querySelectorAll('li,[role=option],div[class*="item"],div[class*="option"]').forEach(li => {
      const lr = li.getBoundingClientRect();
      if (lr.height < 14 || lr.height > 70 || lr.y < 100) return;
      const lt = clean(li.innerText);
      if (!lt || lt.length > 40) return;
      out.push({ t: lt, rect: [Math.round(lr.x), Math.round(lr.y), Math.round(lr.width), Math.round(lr.height)] });
    });
  });
  const seen = new Set();
  return out.filter(o => { const k = o.t + o.rect.join(","); if (seen.has(k)) return false; seen.add(k); return true; });
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)

        page.evaluate("() => window.scrollTo(0, 600)")
        page.wait_for_timeout(900)

        # 1) 第二个型号：idx=7 @ [427,793,252,34]
        print("=== 第二个型号（0/100，推荐 61键）===")
        try:
            human_type(page, '[data-dsh-idx="7"]', "61键")
            page.wait_for_timeout(600)
            v = page.locator('[data-dsh-idx="7"]').input_value()
            print("  回读: %r" % v)
        except Exception as exc:
            print("  [XX] %s" % str(exc).split("\n")[0][:90])

        # 2) 适配轴体类型：idx=6 是 ph='请选择' 的窄 input [1097,669,37,24]
        print("\n=== 适配轴体类型（下拉）===")
        for label, x, y in (("input中心", 1115, 681), ("右侧箭头", 1300, 681), ("label下方", 1140, 700)):
            print("  试 (%d,%d) %s" % (x, y, label))
            page.mouse.click(x, y)
            page.wait_for_timeout(1500)
            opts = page.evaluate(_OPTIONS)
            print("    选项 %d 个: %s" % (len(opts), [o["t"] for o in opts[:12]]))
            if opts:
                # 找 Cherry / 轴
                want = None
                for o in opts:
                    if any(k in o["t"] for k in ("Cherry", "樱桃", "通用", "其他", "机械")):
                        want = o
                        break
                if want:
                    cx = want["rect"][0] + want["rect"][2] // 2
                    cy = want["rect"][1] + want["rect"][3] // 2
                    page.mouse.click(cx, cy)
                    page.wait_for_timeout(1200)
                    print("    [OK] 选 %r" % want["t"])
                    break
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)

        page.wait_for_timeout(800)
        body = page.evaluate("() => document.body.innerText")
        print("\n「必填项」剩: %d 处" % body.count("必填项"))
        import re
        for m in re.finditer(r"必填项[^\n]{0,30}", body):
            print("  %r" % m.group(0))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "attrs_filled.png"), timeout_ms=20000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
