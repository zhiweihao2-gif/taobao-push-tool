"""抓淘宝「发布宝贝」接口：打开卖家后台发布页，录下真实请求。

前提：你需要**卖家账号**（买家 cookie 进不了卖家后台，实测会跳
loginmyseller.taobao.com 单独登录）。

用法::

    python scripts/capture_publish.py                # 弹浏览器，你登录后手动发布一次
    python scripts/capture_publish.py --headful      # 同上（默认就是有头）

流程：
1. 打开卖家发布页，等你在浏览器里登录 + 填完商品信息
2. 你点「提交/发布」的那一刻，脚本录下所有相关请求
3. 请求原文存到 data/publish_capture/，同时打印 endpoint 和字段结构

拿到结果后，把 endpoint 填进 config/settings.yaml 的 taobao.publish_endpoint，
PublishPipeline 的 backend 通道就能用了。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "publish_capture"

PUBLISH_URL = "https://sell.taobao.com/auction/publish/publish.htm"

# 关心的请求特征
INTEREST = re.compile(
    r"(publish|item|auction|product|offer).*(submit|add|create|save|publish)",
    re.I)
INTEREST_HOSTS = ("sell.taobao.com", "item.taobao.com", "h5api.m.taobao.com",
                  "pcapi.taobao.com", "market.m.taobao.com", "trade.taobao.com")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=900,
                    help="最多等多少秒（默认 15 分钟）")
    ap.add_argument("--url", default=PUBLISH_URL)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    print("=" * 62)
    print("  抓淘宝发布接口")
    print("=" * 62)
    print()
    print("  1. 浏览器打开后，用**卖家账号**登录（可能要求单独登录）")
    print("  2. 正常走一遍「发布宝贝」：填标题、选类目、传图、设价格")
    print("  3. 点「提交」的那一刻，脚本自动录下请求")
    print("  4. 录到之后会自动保存并提示你关闭")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(no_viewport=True, locale="zh-CN")
        page = ctx.new_page()

        def on_request(req):
            try:
                if req.method not in ("POST", "PUT"):
                    return
                url = req.url
                host = re.sub(r"^https?://([^/]+).*", r"\1", url)
                if host not in INTEREST_HOSTS and not INTEREST.search(url):
                    return
                body = None
                try:
                    body = req.post_data
                except Exception:
                    pass
                item = {
                    "url": url,
                    "method": req.method,
                    "headers": dict(req.headers),
                    "post_data": (body[:20000] if body else None),
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                captured.append(item)
                print(f"\n  ★ 录到 {req.method} {url[:110]}")
                if body:
                    print(f"     body 长度 {len(body)}，前 200 字符：")
                    print(f"     {body[:200]}")
            except Exception:
                pass

        page.on("request", on_request)
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)

        deadline = time.time() + args.timeout
        last = 0.0
        while time.time() < deadline:
            if captured:
                # 录到后再等 3 秒，可能还有后续请求
                time.sleep(3)
                break
            now = time.time()
            if now - last > 30:
                print(f"  等待中… 剩余 {int(deadline - now)} 秒"
                      f"（已录 {len(captured)} 条）")
                last = now
            time.sleep(1)

        try:
            browser.close()
        except Exception:
            pass

    if not captured:
        print("\n× 没录到任何发布请求。")
        print("  可能原因：没有卖家身份 / 没走到提交那一步 / 页面结构变了")
        return 1

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_file = OUT / f"publish_{stamp}.json"
    out_file.write_text(json.dumps(captured, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(f"\n✓ 录到 {len(captured)} 条请求 → {out_file}")
    print()
    print("  去重后的 endpoint：")
    seen = set()
    for c in captured:
        key = c["url"].split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        print(f"    {c['method']}  {key}")
    print()
    print("  下一步：把上面的 endpoint 填进 config/settings.yaml")
    print("    taobao:")
    print("      publish_endpoint: \"<上面那个 URL>\"")
    print("  然后 backend 通道的 draft 模式就能用了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
