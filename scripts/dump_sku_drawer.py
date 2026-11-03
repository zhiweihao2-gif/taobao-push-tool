"""dump 已打开的 SKU 抽屉（sku-decouple-drawer）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { drawers: [], inputs: [], buttons: [], selects: [], iframes: [] };

  document.querySelectorAll('.next-overlay-wrapper.opened').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 80) return;
    out.drawers.push({
      cls: clean(o.className).slice(0, 60),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      text: clean(o.innerText).slice(0, 700),
    });

    o.querySelectorAll('input').forEach(i => {
      const ir = i.getBoundingClientRect();
      out.inputs.push({
        ph: i.placeholder || '',
        val: (i.value || '').slice(0, 20),
        cls: clean(i.className).slice(0, 40),
        rect: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)],
      });
    });

    o.querySelectorAll('button,a,[role=button],span[class*="btn"]').forEach(b => {
      const br = b.getBoundingClientRect();
      if (br.width < 20 || br.height < 10) return;
      const t = clean(b.innerText);
      if (!t || t.length > 24) return;
      out.buttons.push({ t, tag: b.tagName, cls: clean(b.className).slice(0, 40), rect: [Math.round(br.x), Math.round(br.y), Math.round(br.width), Math.round(br.height)] });
    });

    o.querySelectorAll('[class*="select"],[class*="Select"],[class*="tag"],[class*="Tag"]').forEach(s => {
      const sr = s.getBoundingClientRect();
      if (sr.width < 30 || sr.height < 15) return;
      const t = clean(s.innerText);
      if (!t || t.length > 30) return;
      out.selects.push({ t, cls: clean(s.className).slice(0, 40), rect: [Math.round(sr.x), Math.round(sr.y), Math.round(sr.width), Math.round(sr.height)] });
    });
  });

  document.querySelectorAll('iframe').forEach(f => {
    const r = f.getBoundingClientRect();
    if (r.width < 60) return;
    out.iframes.push({ src: (f.src||'').slice(0, 100), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)
        print("URL: " + page.url[:70])

        info = page.evaluate(_JS)
        print("\n抽屉 %d 个:" % len(info["drawers"]))
        for d in info["drawers"]:
            print("  %s  %s" % (d["rect"], d["cls"]))
            print("  文本: %r" % d["text"][:500])

        print("\n输入框 %d:" % len(info["inputs"]))
        for i in info["inputs"]:
            print("  ph=%-16r val=%-18r %-30s %s" % (i["ph"], i["val"], i["cls"], i["rect"]))

        print("\n按钮 %d:" % len(info["buttons"]))
        seen = set()
        for b in info["buttons"]:
            k = (b["t"], tuple(b["rect"]))
            if k in seen:
                continue
            seen.add(k)
            print("  %-16r %-6s %-30s %s" % (b["t"], b["tag"], b["cls"], b["rect"]))

        print("\n下拉/标签 %d:" % len(info["selects"]))
        seen = set()
        for d in info["selects"]:
            k = (d["t"], tuple(d["rect"]))
            if k in seen:
                continue
            seen.add(k)
            print("  %-24r %-30s %s" % (d["t"], d["cls"], d["rect"]))

        print("\niframe %d:" % len(info["iframes"]))
        for f in info["iframes"]:
            print("  %-60s %s" % (f["src"], f["rect"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_drawer.png"), timeout_ms=15000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
