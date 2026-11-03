"""从天猫/淘宝商品页 DOM 里直接抠数据（不走接口）。

背景：天猫 PC 详情页（detail.tmall.com）走 CSR，但页面调的那几个 mtop 接口
（singleview / kangaroo）返回的是容器配置不是商品数据，`value` 是空的 `{}`。
商品数据是服务端直出 + 前端渲染进 DOM 的，所以只能从 DOM 抠。

这个脚本做两件事：
  1. 把整个 HTML 存下来（方便后面改 selector 时不用重新请求）
  2. 用一批候选 selector 尝试抠出 标题/价格/主图/参数/SKU，打印命中情况
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")

# 逐字段的候选 selector，按优先级排；命中第一个即可
SELECTORS = {
    "title": [
        "h1",
        "[class*='ItemHeader--mainTitle']",
        "[class*='ItemTitle']",
        ".tb-main-title",
        "[class*='title'] h1",
        "div[class*='Title'] span",
    ],
    "price": [
        "[class*='Price--priceText']",
        "[class*='Price'] [class*='text']",
        ".tm-price",
        "#J_StrPrice .tm-price",
        "[class*='highlightPrice'] span",
        "[class*='priceWrap'] span",
    ],
    "params": [
        "[class*='Params--'] li",
        "[class*='attributes'] li",
        "#J_AttrUL li",
        "[class*='param'] li",
        "ul[class*='Attr'] li",
    ],
    "sku": [
        "[class*='SkuContent--'] [class*='valueItem']",
        "[class*='sku'] [class*='item']",
        "#J_DetailEntity li",
        "[class*='Sku'] dd a",
    ],
    "mainImg": [
        "[class*='PicGallery'] img",
        "[class*='thumbnail'] img",
        "#J_UlThumb img",
        "[class*='MainPic'] img",
        "ul[class*='Thumbnail'] img",
    ],
}

# 页面里所有大图（兜底）
IMG_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('img').forEach(i => {
    const s = i.currentSrc || i.src || '';
    if (!s.includes('alicdn')) return;
    const w = i.naturalWidth || 0, h = i.naturalHeight || 0;
    if (w >= 400 && h >= 400) out.push({url: s, w, h});
  });
  return out;
}
"""


def cookies_from_header(text: str) -> list[dict]:
    out = []
    for chunk in text.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            out.append({"name": k.strip(), "value": v.strip(),
                        "domain": ".taobao.com", "path": "/"})
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/dom_collect.py <item_id>")
        return 2
    item_id = sys.argv[1].strip()

    st = SessionStore()
    ck = st.cookie_header("taobao")
    if not ck:
        print("[!] 没有 taobao cookie")
        return 1

    out_dir = ROOT / "data" / "publish_poc"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 1000},
                            user_agent=UA, locale="zh-CN")
        ctx.add_cookies(cookies_from_header(ck))
        page = ctx.new_page()
        url = f"https://item.taobao.com/item.htm?id={item_id}"
        print("打开 %s" % url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("[!] goto: %s" % str(e).split("\n")[0][:120])
        page.wait_for_timeout(4000)
        # 滚一遍触发懒加载
        for _ in range(4):
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(600)

        print("落地 URL: %s" % page.url)
        print("标题: %s" % page.title())

        html = page.content()
        hp = out_dir / f"dom_{item_id}_{ts}.html"
        hp.write_text(html, encoding="utf-8")
        print("HTML %d 字节 -> %s" % (len(html), hp))

        result: dict = {"item_id": item_id, "landing_url": page.url,
                        "page_title": page.title(), "fields": {}}

        print("\n=== 字段命中情况 ===")
        for name, sels in SELECTORS.items():
            hit = None
            for sel in sels:
                try:
                    loc = page.locator(sel)
                    n = loc.count()
                except Exception:
                    continue
                if n == 0:
                    continue
                texts = []
                for i in range(min(n, 20)):
                    try:
                        t = (loc.nth(i).inner_text(timeout=800) or "").strip()
                    except Exception:
                        t = ""
                    if t:
                        texts.append(t)
                if texts:
                    hit = {"selector": sel, "count": n, "values": texts[:20]}
                    break
            result["fields"][name] = hit
            if hit:
                print("  [OK] %-8s %-32s ×%-3d %s"
                      % (name, hit["selector"], hit["count"],
                         str(hit["values"][:4])[:100]))
            else:
                print("  [--] %-8s 全部 selector 未命中" % name)

        imgs = page.evaluate(IMG_JS)
        result["big_images"] = imgs[:30]
        print("\n=== 大图（≥400×400）%d 张 ===" % len(imgs))
        for it in imgs[:10]:
            print("  %dx%d  %s" % (it["w"], it["h"], it["url"][:110]))

        jp = out_dir / f"dom_{item_id}_{ts}.json"
        jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n-> %s" % jp)

        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
