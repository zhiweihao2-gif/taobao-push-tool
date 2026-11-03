"""诊断「完成」按钮：为什么点了没反应。

截图显示主图已上传且选中（蓝色边框 + 勾），但弹窗没关。
「完成」按钮可能在 iframe 底部视口外。
"""
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
  const btns = [];
  document.querySelectorAll('button,[role=button],[class*="btn"]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 10) return;
    const cs = window.getComputedStyle(b);
    btns.push({
      text: clean(b.innerText).slice(0, 16),
      tag: b.tagName,
      cls: clean(b.className).slice(0, 80),
      disabled: b.disabled === true || b.getAttribute('aria-disabled') === 'true',
      visible: cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0',
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });
  // 选中的图
  const picked = [];
  document.querySelectorAll('[class*="pic_imgBox"],[class*="selected"],[class*="checked"]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 60) return;
    picked.push({ cls: clean(el.className).slice(0, 90), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return {
    viewport: [window.innerWidth, window.innerHeight],
    scroll: [window.scrollX, window.scrollY],
    docHeight: document.documentElement.scrollHeight,
    btns,
    picked: picked.slice(0, 8),
  };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)

        fr = None
        for f in page.frames:
            if "sucai-selector" in f.url:
                fr = f
                break
        if fr is None:
            print("[XX] iframe 不在")
            return 1

        info = fr.evaluate(_JS)
        print("iframe 视口=%s scroll=%s docHeight=%s" % (
            info["viewport"], info["scroll"], info["docHeight"]))
        print("\n按钮:")
        for b in info["btns"]:
            mark = ""
            if b["text"] in ("完成", "确定", "确认"):
                mark = "  <<<"
            print("  %-14r %-8s disabled=%-6s visible=%-6s %-42s %s%s" % (
                b["text"], b["tag"], b["disabled"], b["visible"],
                b["cls"][:40], b["rect"], mark))

        print("\n选中项:")
        for p in info["picked"]:
            print("  %-50s %s" % (p["cls"][:48], p["rect"]))

        # 滚动 iframe 到底
        print("\n[..] 滚动 iframe 到底部")
        try:
            fr.evaluate("() => { window.scrollTo(0, document.documentElement.scrollHeight); }")
            page.wait_for_timeout(1200)
        except Exception as exc:
            print("    失败: %s" % exc)

        print("[..] scrollIntoView 找「完成」")
        try:
            loc = fr.get_by_text("完成", exact=True).first
            loc.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(800)
            r = loc.bounding_box()
            print("    bounding_box: %s" % r)
            print("    disabled attr: %s" % loc.get_attribute("disabled"))
            print("    aria-disabled: %s" % loc.get_attribute("aria-disabled"))
            print("    class: %s" % (loc.get_attribute("class") or "")[:100])
        except Exception as exc:
            print("    失败: %s" % exc)

        info2 = fr.evaluate(_JS)
        print("\n滚动后 iframe scroll=%s" % info2["scroll"])
        for b in info2["btns"]:
            if b["text"] in ("完成", "确定", "确认"):
                print("  完成按钮: %s disabled=%s visible=%s" % (b["rect"], b["disabled"], b["visible"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "before_done.png"))
        print("\n截图: data/publish_poc/before_done.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
