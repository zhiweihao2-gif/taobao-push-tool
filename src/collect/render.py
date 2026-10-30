"""用 Playwright 渲染详情页，抠出详情图。

为什么需要这一层：淘宝的 descUrl / apiDesc 接口全部下线，PC 页面是客户端渲染，
静态请求拿不到详情图。只能开真实浏览器渲染页面。

实测要点（2026-10）：
1. 必须**先点开「详情」tab**，光滚动整页只会拿到主图和页面装饰图
2. 详情图宽度通常 700~800（淘宝详情页正文宽度）
3. 页面装饰图（banner/活动图）宽高比极大（如 3456x864、708x84），要排除
4. 详情图属于卖家 ID，可以用 `/imgextra/iN/<sellerId>/` 路径特征二次确认
"""
from __future__ import annotations

import re
import time
from typing import Any

DESC_TAB_TEXTS = ("宝贝详情", "商品详情", "详情", "商品介绍", "图文详情")

COLLECT_JS = r"""
() => {
  const norm = (u) => {
    if (!u) return '';
    if (u.startsWith('//')) u = 'https:' + u;
    if (!/alicdn\.com/.test(u)) return '';
    if (u.startsWith('data:')) return '';
    return u.replace(/\.(jpg|jpeg|png|webp)_[^/]*$/i, '.$1');
  };
  const pick = (img) => norm(
    img.getAttribute('data-ks-lazyload') || img.getAttribute('data-src') ||
    img.getAttribute('data-lazyload') || img.currentSrc || img.src);
  const out = [];
  const seen = new Set();
  document.querySelectorAll('img').forEach(img => {
    const u = pick(img);
    if (!u || seen.has(u)) return;
    seen.add(u);
    out.push({
      url: u,
      w: img.naturalWidth || 0,
      h: img.naturalHeight || 0,
      top: img.getBoundingClientRect().top + window.scrollY
    });
  });
  return out;
}
"""


class RenderError(RuntimeError):
    pass


def render_detail_images(item_id: str, cookies: dict[str, str],
                         headless: bool = True, timeout: int = 60000,
                         min_width: int = 600, max_ratio: float = 6.0,
                         scroll_rounds: int = 15) -> dict[str, Any]:
    """返回 {"detail_images": [...], "all_count": N, "title": str, "clicked": str}"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RenderError("缺少 playwright：python -m pip install playwright && "
                          "python -m playwright install chromium") from e

    url = f"https://item.taobao.com/item.htm?id={item_id}"
    result: dict[str, Any] = {"detail_images": [], "all_count": 0,
                              "title": "", "clicked": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            locale="zh-CN", viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for k, v in (cookies or {}).items():
            try:
                ctx.add_cookies([{"name": k, "value": v,
                                  "domain": ".taobao.com", "path": "/"}])
            except Exception:
                pass

        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        try:
            page.wait_for_function(
                "() => document.title && document.title.length > 8", timeout=20000)
        except Exception:
            pass
        time.sleep(2)
        result["title"] = page.title()

        # 等主图先渲染出来，否则后面点 tab 也点不到
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('img').length >= 10", timeout=15000)
        except Exception:
            pass

        # 点开详情 tab（关键步骤，之前这里静默失败导致只拿到 1 张图）
        result["clicked"] = _click_desc_tab(page)
        if result["clicked"]:
            time.sleep(3)
        else:
            result["click_note"] = "没找到可点击的详情 tab"

        # 滚动触发懒加载
        for _ in range(scroll_rounds):
            page.mouse.wheel(0, 1200)
            time.sleep(0.6)
        time.sleep(2.5)

        rows = page.evaluate(COLLECT_JS) or []
        result["all_count"] = len(rows)
        browser.close()

    # 过滤：
    #   1. 宽度够（详情正文宽度）
    #   2. 排除宽高比过大的 banner 长条（3456x864、708x84 这类）
    #   3. 排除正方形主图（800x800），主图已由 mtop 接口拿到，这里只要详情长图
    picked: list[dict] = []
    for r in rows:
        w, h = r.get("w") or 0, r.get("h") or 0
        if w < min_width or h <= 0:
            continue
        if w / h > max_ratio:
            continue
        if _looks_like_main_image(w, h):
            continue
        picked.append(r)

    # 优先保留「卖家自己的图」：路径形如 /imgextra/iN/<sellerId>/
    seller_hits = [r for r in picked if _seller_owned(r["url"])]
    if seller_hits:
        picked = seller_hits

    picked.sort(key=lambda r: r.get("top", 0))
    result["detail_images"] = [r["url"] for r in picked]
    result["sizes"] = [(r["w"], r["h"]) for r in picked]
    return result


def _looks_like_main_image(w: int, h: int) -> bool:
    """正方形且边长 ≤ 900 的，基本都是主图/缩略图，不是详情长图。"""
    if h == 0:
        return True
    ratio = w / h
    return 0.95 <= ratio <= 1.05 and w <= 900


def _click_desc_tab(page) -> str:
    """点开「详情」tab。逐个候选文本、逐个可见元素尝试，返回点中的文本。"""
    for txt in DESC_TAB_TEXTS:
        try:
            loc = page.get_by_text(txt, exact=False)
            n = min(loc.count(), 5)
        except Exception:
            continue
        for i in range(n):
            try:
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                el.scroll_into_view_if_needed(timeout=2000)
                time.sleep(0.3)
                el.click(timeout=4000)
                return txt
            except Exception:
                continue
    # 兜底：直接按 class 找详情 tab
    for sel in ('[class*="tab"] >> text=详情', 'a:has-text("详情")',
                'div[role="tab"]:has-text("详情")'):
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.scroll_into_view_if_needed(timeout=2000)
                el.click(timeout=4000)
                return "详情(selector)"
        except Exception:
            continue
    return ""


_NUM_SEG = re.compile(r"/imgextra/i\d+/(\d{6,})/")


def _seller_owned(url: str) -> bool:
    """详情图路径里带卖家数字 ID，装饰图通常不带。"""
    return bool(_NUM_SEG.search(url))
