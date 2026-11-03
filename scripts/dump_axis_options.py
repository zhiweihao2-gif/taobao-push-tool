"""展开后 dump overlay 内每个选项的真实坐标（父容器高度 0，选项可能在视口外）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_LOCATE = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labels = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && clean(e.innerText) === '适配轴体类型');
  if (!labels.length) return { err: 'label not found' };
  let p = labels[0];
  for (let k = 0; k < 9 && p; k++) {
    const inp = p.querySelector && p.querySelector('input[role=combobox]');
    if (inp) {
      const sel = inp.closest('.next-select');
      if (!sel) { p = p.parentElement; continue; }
      sel.scrollIntoView({ block: 'center', behavior: 'instant' });
      const r = sel.getBoundingClientRect();
      return { rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
    }
    p = p.parentElement;
  }
  return { err: 'no select' };
}
"""

_DUMP = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const res = { overlay: null, leaves: [] };
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return res;
  const r = ov.getBoundingClientRect();
  res.overlay = {
    rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    style: (ov.getAttribute('style') || '').slice(0, 200),
    scrollY: window.scrollY,
  };
  ov.querySelectorAll('*').forEach(e => {
    if (e.children.length) return;
    const t = clean(e.innerText);
    if (!t || t.length > 30) return;
    const lr = e.getBoundingClientRect();
    if (lr.width < 10 || lr.height < 8) return;
    res.leaves.push({ t, rect: [Math.round(lr.x), Math.round(lr.y), Math.round(lr.width), Math.round(lr.height)], tag: e.tagName.toLowerCase() });
  });
  return res;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)

        info = page.evaluate(_LOCATE)
        print("定位: %s" % info)
        if info.get("err"):
            return 1
        page.wait_for_timeout(800)
        x, y, w, h = info["rect"]

        page.mouse.click(x + 8, y + h // 2)
        page.wait_for_timeout(2000)

        d = page.evaluate(_DUMP)
        print("\noverlay: %s" % d["overlay"])
        print("\n叶子 %d:" % len(d["leaves"]))
        for lf in d["leaves"]:
            print("  %-20r %-8s %s" % (lf["t"], lf["tag"], lf["rect"]))

        # 尝试点「通用」
        target = None
        for lf in d["leaves"]:
            if lf["t"] == "通用":
                target = lf
                break
        if target:
            tx = target["rect"][0] + target["rect"][2] // 2
            ty = target["rect"][1] + target["rect"][3] // 2
            print("\n点「通用」@ (%d,%d)" % (tx, ty))
            if 0 <= ty <= 1000:
                page.mouse.click(tx, ty)
            else:
                # 在视口外：用 locator 强制
                try:
                    page.get_by_text("通用", exact=True).last.click(force=True, timeout=6000)
                except Exception as exc:
                    print("  [XX] %s" % str(exc).split("\n")[0][:80])
            page.wait_for_timeout(1500)
            body = page.evaluate("() => document.body.innerText")
            import re
            print("  剩余必填: %s" % re.findall(r"必填项[^\n]{0,30}", body))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "axis_leaves.png"), timeout_ms=25000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
