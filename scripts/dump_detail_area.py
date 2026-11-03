"""dump 宝贝详情区的可用入口和编辑器结构。"""
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
  const out = { buttons: [], editables: [], iframes: [] };

  // 找「宝贝详情」容器
  let box = null;
  document.querySelectorAll('*').forEach(el => {
    if (box) return;
    const t = clean(el.innerText);
    if (t.startsWith('宝贝详情') && t.length < 120 && el.children.length <= 6) box = el;
  });

  const scope = box || document;
  scope.querySelectorAll('button,a,span[class*="btn"],[role=button]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) return;
    const t = clean(b.innerText);
    if (!t || t.length > 20) return;
    out.buttons.push({ text: t, tag: b.tagName, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  document.querySelectorAll('[contenteditable=true],textarea').forEach(e => {
    const r = e.getBoundingClientRect();
    out.editables.push({
      tag: e.tagName,
      cls: clean(e.className).slice(0, 60),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      visible: r.width > 20 && r.height > 10,
    });
  });

  document.querySelectorAll('iframe').forEach(f => {
    const r = f.getBoundingClientRect();
    out.iframes.push({ src: (f.src || '').slice(0, 90), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  out.boxText = box ? clean(box.innerText).slice(0, 300) : null;
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)
        print("URL: " + page.url[:80])

        # 滚到宝贝详情
        try:
            loc = page.get_by_text("宝贝详情", exact=False).first
            loc.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(1200)
            print("[OK] 已滚到宝贝详情")
        except Exception as exc:
            print("[!] 滚动失败: %s" % exc)

        info = page.evaluate(_JS)
        print("\n容器文本: %r" % info["boxText"])
        print("\n按钮 %d 个:" % len(info["buttons"]))
        for b in info["buttons"]:
            print("  %-10r %-6s %s" % (b["text"], b["tag"], b["rect"]))
        print("\n可编辑区 %d 个:" % len(info["editables"]))
        for e in info["editables"]:
            print("  %-10s vis=%-5s %-40s %s" % (e["tag"], e["visible"], e["cls"], e["rect"]))
        print("\niframe %d 个:" % len(info["iframes"]))
        for f in info["iframes"]:
            print("  %-60s %s" % (f["src"], f["rect"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "detail_area.png"))
        print("\n截图: data/publish_poc/detail_area.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
