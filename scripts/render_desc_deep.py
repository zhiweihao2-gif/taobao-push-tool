"""详情图深挖：点开「宝贝详情」tab、进 iframe、抓详情区图片。

淘宝新版详情页的图文是懒加载的，且在独立 tab / iframe 里，
光滚动整页抓不到，必须主动切换。
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

DESC_TAB_TEXTS = ["宝贝详情", "商品详情", "详情", "商品介绍", "图文详情"]

COLLECT_JS = r"""
() => {
  const norm = (u) => {
    if (!u) return '';
    if (u.startsWith('//')) u = 'https:' + u;
    if (!/alicdn\.com/.test(u)) return '';
    return u.replace(/\.(jpg|jpeg|png|webp)_[^/]*$/i, '.$1');
  };
  const pick = (img) => norm(
    img.getAttribute('data-ks-lazyload') || img.getAttribute('data-src') ||
    img.getAttribute('data-lazyload') || img.currentSrc || img.src);
  const rows = [];
  const seen = new Set();
  document.querySelectorAll('img').forEach(img => {
    const u = pick(img);
    if (!u || seen.has(u)) return;
    seen.add(u);
    rows.push({url: u, w: img.naturalWidth || 0, h: img.naturalHeight || 0,
               cls: (img.parentElement && img.parentElement.className || '').toString().slice(0, 50)});
  });
  return rows;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("item_id")
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    cookies = SessionStore().load("taobao")
    if not cookies:
        print("× 没有登录态")
        return 1

    from playwright.sync_api import sync_playwright

    item_id = args.item_id
    url = f"https://item.taobao.com/item.htm?id={item_id}"
    print(f"打开 {url}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(
            locale="zh-CN", viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for k, v in cookies.items():
            try:
                ctx.add_cookies([{"name": k, "value": v, "domain": ".taobao.com", "path": "/"}])
            except Exception:
                pass
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_function(
                "() => document.title && document.title.length > 8", timeout=20000)
        except Exception:
            pass
        time.sleep(2)
        print(f"标题: {page.title()[:70]}")

        # 1. 列出 iframe
        frames = page.frames
        print(f"\n页面 iframe {len(frames)} 个:")
        for f in frames:
            print(f"  {f.url[:100] or '(about:blank)'}")

        # 2. 找并点击「宝贝详情」tab
        clicked = []
        for txt in DESC_TAB_TEXTS:
            try:
                loc = page.get_by_text(txt, exact=False).first
                if loc.count() > 0:
                    loc.click(timeout=3000)
                    clicked.append(txt)
                    print(f"  点击了「{txt}」")
                    time.sleep(2.5)
                    break
            except Exception:
                continue
        if not clicked:
            print("  没有可点击的详情 tab，尝试直接滚动")

        # 3. 滚动加载
        for _ in range(15):
            page.mouse.wheel(0, 1200)
            time.sleep(0.6)
        time.sleep(3)

        # 4. 从主页面 + 所有 iframe 收集
        all_imgs: dict[str, dict] = {}
        for name, frame in [("main", page.main_frame)] + \
                           [(f"frame{i}", f) for i, f in enumerate(frames) if f != page.main_frame]:
            try:
                rows = frame.evaluate(COLLECT_JS) or []
            except Exception:
                continue
            for r in rows:
                if r["url"] not in all_imgs or r["w"] > all_imgs[r["url"]]["w"]:
                    all_imgs[r["url"]] = {**r, "from": name}
            print(f"  [{name}] {len(rows)} 张")

        # 5. 按宽度筛
        big = [v for v in all_imgs.values() if v["w"] >= 600]
        big.sort(key=lambda x: -x["w"])
        print(f"\n去重后 {len(all_imgs)} 张，宽度 >= 600 的 {len(big)} 张：")
        for r in big:
            print(f"  {r['w']:>5}x{r['h']:<5} [{r['from']}] {r['url'][:95]}")

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{item_id}_desc_deep.json").write_text(
            json.dumps({"title": page.title(), "frames": [f.url for f in frames],
                        "clicked": clicked, "all": list(all_imgs.values()),
                        "detail_images": [r["url"] for r in big]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()

    print(f"\n已保存 data/raw/taobao/{item_id}_desc_deep.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
