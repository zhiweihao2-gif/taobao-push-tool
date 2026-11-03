"""dump 展开后的 SKU 规格编辑区。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { blocks: [], inputs: [], selects: [], buttons: [], iframes: [] };

  // 找「销售规格」到「购买须知」之间的容器
  let start = null;
  document.querySelectorAll('div,section').forEach(el => {
    if (start) return;
    const t = clean(el.innerText);
    if (t.startsWith('销售规格') && t.length < 900 && el.children.length < 40) start = el;
  });

  if (start) {
    out.blockText = clean(start.innerText).slice(0, 600);
    start.querySelectorAll('input').forEach(i => {
      const r = i.getBoundingClientRect();
      if (r.width < 20) return;
      out.inputs.push({ ph: i.placeholder || '', val: (i.value||'').slice(0,16), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
    });
    start.querySelectorAll('button,a,[role=button],span[class*="btn"]').forEach(b => {
      const r = b.getBoundingClientRect();
      if (r.width < 20 || r.height < 10) return;
      const t = clean(b.innerText);
      if (!t || t.length > 20) return;
      out.buttons.push({ t, tag: b.tagName, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
    });
    start.querySelectorAll('[class*="select"],[class*="Select"]').forEach(s => {
      const r = s.getBoundingClientRect();
      if (r.width < 60 || r.height < 20) return;
      const t = clean(s.innerText);
      if (!t || t.length > 30) return;
      out.selects.push({ t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
    });
  }

  document.querySelectorAll('iframe').forEach(f => {
    const r = f.getBoundingClientRect();
    if (r.width < 80) return;
    out.iframes.push({ src: (f.src||'').slice(0, 100), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("URL: " + page.url[:70])

        # 滚到销售规格
        try:
            page.get_by_text("销售规格", exact=True).first.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(1000)
        except Exception as exc:
            print("[!] 滚动: %s" % str(exc).split("\n")[0])

        info = page.evaluate(_JS)
        print("\n块文本: %r\n" % (info.get("blockText") or "")[:400])

        print("按钮 %d:" % len(info["buttons"]))
        for b in info["buttons"]:
            print("  %-16r %-6s %s" % (b["t"], b["tag"], b["rect"]))

        print("\n下拉 %d:" % len(info["selects"]))
        for d in info["selects"]:
            print("  %-28r %s" % (d["t"], d["rect"]))

        print("\n输入框 %d:" % len(info["inputs"]))
        for i in info["inputs"]:
            print("  ph=%-16r val=%-14r %s" % (i["ph"], i["val"], i["rect"]))

        print("\niframe %d:" % len(info["iframes"]))
        for f in info["iframes"]:
            print("  %-60s %s" % (f["src"], f["rect"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_area.png"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
