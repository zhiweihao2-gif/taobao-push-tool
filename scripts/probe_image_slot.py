"""探测点击主图槽位会发生什么：系统文件选择器 / 图片空间 iframe / 自定义弹窗。

三种可能：
  A. 弹系统 file chooser → Playwright 的 expect_file_chooser 能捕获，直接 set_files
  B. 弹云端图片空间 iframe（sucai.wangpu.taobao.com）→ 要在 iframe 里选图
  C. 弹自定义 dialog → 看 dialog 里的内容
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_AFTER = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  return {
    url: location.href,
    fileInputs: [...document.querySelectorAll('input[type=file]')].map(e => ({
      cls: (e.className||'').toString().slice(0,60),
      accept: e.getAttribute('accept'),
      multiple: e.multiple,
      rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(e.getBoundingClientRect()),
    })),
    iframes: [...document.querySelectorAll('iframe')].map(f => ({
      src: (f.src || '').slice(0, 120),
      rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(f.getBoundingClientRect()),
    })),
    dialogs: [...document.querySelectorAll('[role=dialog],[class*="dialog"],[class*="modal"],[class*="overlay"],[class*="drawer"]')]
      .filter(e => { const r = e.getBoundingClientRect(); return r.width > 200 && r.height > 150; })
      .map(e => ({ cls: clean(e.className).slice(0,80), text: clean(e.innerText).slice(0, 200),
                   rect: (r => [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)])(e.getBoundingClientRect()) })),
    slots: document.querySelectorAll('.image-empty').length,
  };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)
        if "publish.htm" not in page.url:
            print("[XX] 不在发布页")
            return 1

        print("[1] 点击前")
        before = page.evaluate(_AFTER)
        print("    fileInputs=%d iframes=%d dialogs=%d slots=%d" % (
            len(before["fileInputs"]), len(before["iframes"]),
            len(before["dialogs"]), before["slots"]))

        print("[2] 点第一个 image-empty，同时监听 filechooser")
        empty = page.locator(".image-empty").first
        empty.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(500)

        got_chooser = False
        try:
            with page.expect_file_chooser(timeout=6000) as fc_info:
                empty.click(timeout=5000)
            fc = fc_info.value
            got_chooser = True
            print("    [OK] 捕获到 file chooser! multiple=%s" % fc.is_multiple())
            print("         先不设文件，只记录")
        except Exception as exc:
            print("    [..] 没有 file chooser: %s" % type(exc).__name__)

        page.wait_for_timeout(3000)
        print("[3] 点击后")
        after = page.evaluate(_AFTER)
        print(json.dumps(after, ensure_ascii=False, indent=2)[:3000])

        print("\n[4] Playwright 视角的 page.frames")
        for fr in page.frames:
            print("    %s  %s" % (fr.name or "(no name)", fr.url[:110]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "after_click_slot.png"))
        print("\n截图: data/publish_poc/after_click_slot.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
