"""找一个带主图视频的淘宝商品，用于验证视频采集。

用 Playwright 打开搜索页（真实浏览器，风控压力小），从商品卡片里找带视频标记的。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402

KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "连衣裙"
OUT = ROOT / "data" / "raw" / "taobao"

EXTRACT_JS = r"""
() => {
  const items = [];
  // 淘宝搜索页商品卡片，兼容新旧两版 class
  const cards = document.querySelectorAll(
    '[class*="Card--"], [class*="item--"], .item.J_MouserOnverReq, [data-nid]');
  const seen = new Set();
  cards.forEach(c => {
    let id = c.getAttribute('data-nid');
    if (!id) {
      const a = c.querySelector('a[href*="id="]');
      if (a) { const m = a.href.match(/[?&]id=(\d{6,})/); if (m) id = m[1]; }
    }
    if (!id || seen.has(id)) return;
    seen.add(id);
    // 是否带视频标记
    const html = c.outerHTML;
    const hasVideo = /video|Video|视频|play/i.test(html);
    const videoTag = !!c.querySelector('video, [class*="video"], [class*="Video"]');
    const title = (c.querySelector('[class*="title"], .row-2, .title') || {}).textContent || '';
    items.push({id, hasVideo: hasVideo || videoTag, videoTag,
                title: title.trim().slice(0, 40)});
  });
  return items;
}
"""


def main() -> int:
    cookies = SessionStore().load("taobao")
    if not cookies:
        print("× 没有登录态")
        return 1

    from playwright.sync_api import sync_playwright

    url = f"https://s.taobao.com/search?q={KEYWORD}"
    print(f"打开 {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="zh-CN", viewport={"width": 1600, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for k, v in cookies.items():
            try:
                ctx.add_cookies([{"name": k, "value": v,
                                  "domain": ".taobao.com", "path": "/"}])
            except Exception:
                pass
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        print(f"页面标题: {page.title()[:60]}")

        for _ in range(3):
            page.mouse.wheel(0, 1200)
            time.sleep(1.2)

        items = page.evaluate(EXTRACT_JS) or []
        browser.close()

    print(f"\n找到 {len(items)} 个商品卡片")
    video_items = [i for i in items if i["hasVideo"]]
    print(f"其中带视频标记的 {len(video_items)} 个：")
    for i in video_items[:10]:
        print(f"  {i['id']}  {'[video]' if i['videoTag'] else '[maybe]'}  {i['title']}")

    if not video_items:
        print("\n没找到带视频的，前 10 个普通商品：")
        for i in items[:10]:
            print(f"  {i['id']}  {i['title']}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"search_{KEYWORD}.json").write_text(
        json.dumps({"keyword": KEYWORD, "items": items}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
