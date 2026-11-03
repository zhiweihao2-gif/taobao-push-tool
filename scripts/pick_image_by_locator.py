"""在图片空间里用 locator 点图片卡片（实时选择模式，没有「完成」按钮）。

发现：iframe 里根本没有「完成/确定」按钮，URL 带 realTimeSel=true，
说明点图就直接回填到发布页。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_pause  # noqa: E402

_MAIN_STATE = r"""
() => {
  const empties = document.querySelectorAll('.image-empty').length;
  const imgs = [...document.querySelectorAll('.sell-component-simply-images img')]
    .map(i => ({ src: (i.src||'').slice(-60), w: Math.round(i.getBoundingClientRect().width) }));
  const dialogs = [...document.querySelectorAll('[class*="media-popup"],[role=dialog]')]
    .filter(e => e.getBoundingClientRect().height > 100).length;
  return { empties, imgs, dialogs };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)
        print("URL: " + page.url[:90])

        before = page.evaluate(_MAIN_STATE)
        print("[0] 弹窗前: empties=%d imgs=%d dialogs=%d" % (
            before["empties"], len(before["imgs"]), before["dialogs"]))

        # 如果弹窗没开，点第一个槽位
        fr = None
        for f in page.frames:
            if "sucai-selector" in f.url:
                fr = f
                break
        if fr is None:
            print("[1] 点第一个 image-empty 打开图片空间")
            page.locator(".image-empty").first.click()
            page.wait_for_timeout(4000)
            for f in page.frames:
                if "sucai-selector" in f.url:
                    fr = f
                    break
        if fr is None:
            print("[XX] 打不开图片空间")
            return 1
        print("    iframe: " + fr.url[:80])

        print("\n[2] 点第一张图（刚上传的 main_ 图）")
        cards = fr.locator('[class*="pic_imgBox"]')
        n = cards.count()
        print("    卡片数: %d" % n)
        if n == 0:
            print("[XX] 没有图")
            return 1

        try:
            cards.first.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(400)
            cards.first.click(timeout=6000)
            print("    [OK] 已点击")
        except Exception as exc:
            print("    [XX] 点击失败: %s" % exc)
            return 1

        page.wait_for_timeout(3000)
        human_pause(0.8, 1.5)

        print("\n[3] 主页面状态")
        after = page.evaluate(_MAIN_STATE)
        print("    empties=%d imgs=%d dialogs=%d" % (
            after["empties"], len(after["imgs"]), after["dialogs"]))
        for i in after["imgs"]:
            print("      %s (w=%d)" % (i["src"], i["w"]))

        still = [f for f in page.frames if "sucai-selector" in f.url]
        print("    图片空间 iframe: %d 个" % len(still))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "picked_main.png"))
        print("\n截图: data/publish_poc/picked_main.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
