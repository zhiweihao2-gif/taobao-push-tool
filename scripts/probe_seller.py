"""探测：这个淘宝账号有没有卖家/店铺身份，以及发布入口在哪。

结论只有三种：
  - 有店铺 → 继续抓发布接口
  - 没店铺 → 发布接口这条路走不通，直接放弃
  - 登录态失效 → 先重新登录

用 Playwright 走真实页面，不猜接口。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402

# 依次尝试的入口，命中即停
PROBES = [
    ("卖家中心", "https://myseller.taobao.com/home.htm"),
    ("千牛卖家中心", "https://sell.taobao.com/auction/merchandise/auction_list.htm"),
    ("发布宝贝", "https://sell.taobao.com/auction/publish/publish.htm"),
    ("我要开店", "https://openshop.taobao.com/"),
]

NO_SHOP_MARKS = ("我要开店", "免费开店", "立即开店", "还没有店铺", "开通店铺",
                 "卖家入驻", "商家入驻")


def main() -> int:
    cookies = SessionStore().load("taobao")
    if not cookies:
        print("× 没有登录态，先跑 1-登录淘宝.bat")
        return 1

    from playwright.sync_api import sync_playwright

    print(f"cookie {len(cookies)} 个，开始探测…\n")
    verdict = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="zh-CN", viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        for k, v in cookies.items():
            try:
                ctx.add_cookies([{"name": k, "value": v,
                                  "domain": ".taobao.com", "path": "/"}])
            except Exception:
                pass
        page = ctx.new_page()

        for label, url in PROBES:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=35000)
                time.sleep(2.5)
                title = page.title()
                final = page.url
                body = page.inner_text("body")[:1200] if page.query_selector("body") else ""

                redirected = final != url and "login" in final
                no_shop = any(m in body for m in NO_SHOP_MARKS)

                print(f"[{label}]")
                print(f"  标题: {title[:60]}")
                print(f"  落地: {final[:90]}")
                print(f"  跳登录: {redirected}   出现开店引导: {no_shop}")

                if redirected:
                    verdict = verdict or "NO_LOGIN"
                elif no_shop:
                    verdict = "NO_SHOP"
                    print(f"  → 命中开店引导，判定无店铺")
                    break
                elif label == "发布宝贝" and not redirected:
                    # 能打开发布页基本说明有卖家身份
                    has_form = bool(page.query_selector(
                        "input, textarea, [class*='publish'], [class*='form']"))
                    print(f"  有表单元素: {has_form}")
                    if has_form:
                        verdict = "HAS_SHOP"
                        print(f"  → 发布页可打开且有表单，判定有卖家身份")
                        break
                elif label == "卖家中心" and not redirected and not no_shop:
                    verdict = verdict or "MAYBE_SHOP"
            except Exception as e:
                print(f"[{label}] ERR {type(e).__name__}: {str(e)[:80]}")
            print()

        browser.close()

    print("=" * 56)
    mapping = {
        "HAS_SHOP": "✓ 有卖家身份 —— 可以继续抓发布接口",
        "MAYBE_SHOP": "? 疑似有卖家身份 —— 需要人工确认",
        "NO_SHOP": "× 没有店铺 —— 发布接口这条路走不通",
        "NO_LOGIN": "× 登录态失效 —— 先重新登录",
        None: "? 探测不出结论 —— 页面可能被风控",
    }
    print(mapping.get(verdict, verdict))
    print("=" * 56)
    return 0


if __name__ == "__main__":
    sys.exit(main())
