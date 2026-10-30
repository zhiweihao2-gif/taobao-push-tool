"""Playwright 渲染详情页 → 按真实尺寸筛出详情图。

改进点：
1. 等商品标题渲染完成再抓（避免抓到骨架）
2. 在浏览器里读 naturalWidth/naturalHeight，按宽度筛（详情图通常 750 宽）
3. 兼容淘宝新版的 CSS Modules 哈希类名（desc--xxxx）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402

OUT = ROOT / "data" / "raw" / "taobao"

EXTRACT_JS = r"""
() => {
  const norm = (u) => {
    if (!u) return '';
    if (u.startsWith('//')) u = 'https:' + u;
    if (!/alicdn\.com/.test(u)) return '';
    if (u.startsWith('data:')) return '';
    return u.replace(/\.(jpg|jpeg|png|webp)_[^/]*$/i, '.$1');
  };
  const pick = (img) => norm(
    img.getAttribute('data-ks-lazyload') ||
    img.getAttribute('data-src') ||
    img.getAttribute('data-lazyload') ||
    img.currentSrc || img.src);

  const rows = [];
  const seen = new Set();
  document.querySelectorAll('img').forEach(img => {
    const url = pick(img);
    if (!url || seen.has(url)) return;
    seen.add(url);
    // 是否位于详情容器内
    const inDesc = !!img.closest(
      '[class*="desc--"], #description, #J_DivItemDesc, #J_Detail, .detail-content, [class*="detail-desc"]');
    rows.push({
      url,
      w: img.naturalWidth || 0,
      h: img.naturalHeight || 0,
      inDesc,
      cls: (img.closest('[class*="desc--"]') || {}).className || ''
    });
  });
  return rows;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("item_id")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--min-width", type=int, default=600)
    args = ap.parse_args()

    item_id = args.item_id
    cookies = SessionStore().load("taobao")
    if not cookies:
        print("× 没有登录态，先双击 1-登录淘宝.bat")
        return 1

    from playwright.sync_api import sync_playwright

    url = f"https://item.taobao.com/item.htm?id={item_id}"
    print(f"打开 {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        context = browser.new_context(
            locale="zh-CN", viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        for k, v in cookies.items():
            try:
                context.add_cookies([{"name": k, "value": v,
                                      "domain": ".taobao.com", "path": "/"}])
            except Exception:
                pass

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 等标题出现（最多 20s）
        try:
            page.wait_for_function(
                "() => document.title && document.title !== '商品详情' "
                "&& document.title.length > 6", timeout=20000)
        except Exception:
            print("  ! 标题未在 20s 内就绪，继续尝试")
        time.sleep(2)
        print(f"页面标题: {page.title()[:70]}")

        # 慢速滚动，触发所有懒加载
        for _ in range(12):
            page.mouse.wheel(0, 1200)
            time.sleep(0.7)
        time.sleep(3)

        rows = page.evaluate(EXTRACT_JS) or []
        browser.close()

    print(f"\nDOM 中 alicdn 图 {len(rows)} 张")
    desc = [r for r in rows if r["w"] >= args.min_width]
    print(f"宽度 >= {args.min_width}px 的 {len(desc)} 张：")
    for r in sorted(desc, key=lambda x: -x["w"]):
        tag = "详情区" if r["inDesc"] else "页面其他"
        print(f"  {r['w']:>5}x{r['h']:<5} [{tag}] {r['url'][:95]}")

    small = [r for r in rows if r["w"] < args.min_width]
    print(f"\n小于 {args.min_width}px 的 {len(small)} 张（图标/缩略图，已忽略）")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{item_id}_render.json").write_text(
        json.dumps({"title": page_title if (page_title := None) else "",
                    "all": rows,
                    "detail_images": [r["url"] for r in desc]},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已保存 data/raw/taobao/{item_id}_render.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
