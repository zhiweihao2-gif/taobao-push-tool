"""诊断图片空间上传后的浮层状态。

上次报错：`<div>50.29K</div> from <div class="next-overlay-wrapper opened"> subtree intercepts pointer events`
说明上传后有进度浮层挡住图片卡片，且图片数没增加（144 张）。
"""
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
  const out = { overlays: [], cards: 0, texts: [] };
  document.querySelectorAll('.next-overlay-wrapper').forEach(o => {
    const r = o.getBoundingClientRect();
    const cls = clean(o.className);
    out.overlays.push({
      cls: cls.slice(0, 70),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      text: clean(o.innerText).slice(0, 160),
    });
  });
  out.cards = document.querySelectorAll('[class*="pic_imgBox"]').length;
  // 抓所有含「上传/进度/K」的短文本
  document.querySelectorAll('div,span').forEach(el => {
    if (el.children.length > 2) return;
    const t = clean(el.innerText);
    if (!t || t.length > 40) return;
    if (/上传|进度|\d+(\.\d+)?K|完成|失败/.test(t)) out.texts.push(t);
  });
  out.texts = [...new Set(out.texts)].slice(0, 20);
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)

        fr = None
        for f in page.frames:
            if "sucai-selector" in f.url:
                fr = f
                break
        if fr is None:
            print("[XX] 图片空间没开着。先点一个槽位。")
            page.locator(".image-empty").first.click()
            page.wait_for_timeout(3000)
            for f in page.frames:
                if "sucai-selector" in f.url:
                    fr = f
                    break
        if fr is None:
            print("[XX] 打不开")
            return 1

        print("iframe: " + fr.url[:100])
        info = fr.evaluate(_JS)
        print("\n图片卡片: %d" % info["cards"])
        print("\n浮层 %d 个:" % len(info["overlays"]))
        for o in info["overlays"]:
            print("  %s" % o["cls"])
            print("    rect=%s" % o["rect"])
            print("    text=%r" % o["text"])
        print("\n相关文本: %s" % info["texts"])

        # 主页面状态
        main = page.evaluate("""() => ({
          empties: document.querySelectorAll('.image-empty').length,
          imgs: document.querySelectorAll('.sell-component-simply-images img').length,
        })""")
        print("\n主页面: empties=%d imgs=%d" % (main["empties"], main["imgs"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "space_state.png"))
        print("\n截图: data/publish_poc/space_state.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
