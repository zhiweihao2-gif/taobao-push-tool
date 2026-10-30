"""更宽松地找带视频的淘宝商品：首页推荐 + 搜索页 + 类目页，全试一遍。"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402

OUT = ROOT / "data" / "raw" / "taobao"

SOURCES = [
    ("home", "https://www.taobao.com/"),
    ("search", "https://s.taobao.com/search?q=%E8%BF%9E%E8%A1%A3%E8%A3%99"),
    ("search2", "https://s.taobao.com/search?q=%E6%89%8B%E6%9C%BA"),
    ("cart", "https://cart.taobao.com/cart.htm"),
]

GRAB_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  // 任何指向商品详情的链接
  document.querySelectorAll('a[href*="item.taobao.com"], a[href*="detail.tmall.com"]').forEach(a => {
    const m = a.href.match(/[?&]id=(\d{6,})/);
    if (!m || seen.has(m[1])) return;
    seen.add(m[1]);
    const card = a.closest('[class*="Card"], [class*="item"], li, div');
    const html = card ? card.outerHTML : a.outerHTML;
    out.push({
      id: m[1],
      hasVideoMark: /video|Video|视频|play-btn|player/i.test(html),
      hasVideoTag: !!(card && card.querySelector('video')),
      text: (a.textContent || '').trim().slice(0, 40)
    });
  });
  return out;
}
"""


def main() -> int:
    cookies = SessionStore().load("taobao")
    if not cookies:
        print("× 没有登录态")
        return 1

    from playwright.sync_api import sync_playwright

    all_items: dict[str, dict] = {}

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

        for label, url in SOURCES:
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(4)
                for _ in range(3):
                    page.mouse.wheel(0, 1200)
                    time.sleep(1.0)
                items = page.evaluate(GRAB_JS) or []
                print(f"[{label}] {page.title()[:40]!r} → {len(items)} 个商品链接")
                for it in items:
                    it["source"] = label
                    all_items.setdefault(it["id"], it)
            except Exception as e:
                print(f"[{label}] ERR {type(e).__name__}: {str(e)[:80]}")
            finally:
                page.close()

        browser.close()

    items = list(all_items.values())
    print(f"\n去重后共 {len(items)} 个商品")
    marked = [i for i in items if i["hasVideoMark"] or i["hasVideoTag"]]
    print(f"疑似带视频的 {len(marked)} 个：")
    for i in marked[:15]:
        print(f"  {i['id']}  tag={i['hasVideoTag']}  [{i['source']}]  {i['text'][:30]}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "video_candidates.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已保存 data/raw/taobao/video_candidates.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
