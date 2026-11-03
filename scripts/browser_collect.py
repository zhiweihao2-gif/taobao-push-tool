"""用浏览器代发 mtop 请求，截获响应。

思路：Playwright 打开商品页 -> 页面自己会调 mtop 拿数据 -> 我们监听 response 事件
把 JSON 抠下来。好处：
  1. 请求由真实浏览器发出，带完整指纹/cookie/referer，风控看到的是正常用户
  2. 不用自己算 sign，不用管 _m_h5_tk 过期
  3. 不受我们自己的 mtop 限速器约束（浏览器只有页面级节流）

代价：一次 10~20 秒，比 httpx 直连慢，但比被风控封 30 分钟强。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# 我们关心的 mtop 接口（天猫 PC 详情页走 kangaroo/singleview，不是老的 pcdetail）
WANT = (
    "pcdetail",          # mtop.taobao.pcdetail.data.get  淘宝主数据
    "detail.data.get",
    "singleview",        # mtop.alibaba.fc.api.maoxland.containerfacade.singleview  天猫主数据
    "kangaroo",          # mtop.tmall.kangaroo.*  天猫路由/容器
    "getdesc",           # 详情描述
    "skuCore",
    "querySku",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")


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
        print("用法: python scripts/browser_collect.py <item_id> [--shop]")
        return 2
    item_id = sys.argv[1].strip()

    st = SessionStore()
    ck = st.cookie_header("taobao")
    if not ck:
        print("[!] 没有 taobao cookie，先跑 1-登录淘宝.bat")
        return 1

    grab: dict[str, dict] = {}
    errors: list[str] = []

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 1000},
                            user_agent=UA, locale="zh-CN")
        ctx.add_cookies(cookies_from_header(ck))
        page = ctx.new_page()

        def on_response(resp):
            u = resp.url
            if not any(w in u for w in WANT):
                return
            api = ""
            for seg in u.split("?")[0].split("/"):
                if seg.startswith("mtop."):
                    api = seg
                    break
            try:
                body = resp.json()
            except Exception as e:
                print("  [非JSON] %-40s %s" % (api or "?", str(e)[:60]))
                return
            ret = (body.get("ret") or [""])[0]
            grab[api or u[:80]] = {"ret": ret, "body": body, "url": u[:200]}
            print("  [截获] %-52s %s" % (api or "?", ret[:40]))

        page.on("response", on_response)

        seen: list[str] = []

        def on_request(req):
            u = req.url
            if req.resource_type in ("xhr", "fetch") and ("mtop" in u or "h5api" in u or "api" in u):
                api = ""
                for seg in u.split("?")[0].split("/"):
                    if seg.startswith("mtop."):
                        api = seg
                        break
                seen.append(api or u[:120])

        page.on("request", on_request)

        url = f"https://item.taobao.com/item.htm?id={item_id}"
        print("打开 %s" % url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            errors.append("goto: %s" % str(e).split("\n")[0][:120])
        # 等页面把 mtop 请求发完
        for _ in range(12):
            page.wait_for_timeout(1000)
            if any("pcdetail" in k or "detail.data.get" in k for k in grab):
                page.wait_for_timeout(1500)
                break

        print("\n落地 URL: %s" % page.url)
        print("标题: %s" % page.title())
        if seen:
            print("\n--- 页面发出的 API 请求 (%d) ---" % len(seen))
            for i, s in enumerate(dict.fromkeys(seen)):
                print("  %2d. %s" % (i + 1, s))

        out_dir = ROOT / "data" / "publish_poc"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        raw_path = out_dir / f"mtop_{item_id}_{ts}.json"
        raw_path.write_text(json.dumps(grab, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print("\n截获 %d 个接口 -> %s" % (len(grab), raw_path))

        # 顺手把主数据摘要打出来
        main_key = next((k for k in grab if "pcdetail" in k or "detail.data.get" in k), None)
        if main_key:
            d = grab[main_key]["body"].get("data") or {}
            item = d.get("item") or {}
            print("\n=== 主数据摘要 ===")
            print("标题:", str(item.get("title"))[:60])
            print("图片:", len(item.get("images") or []), "张")
            sku_core = d.get("skuCore") or {}
            print("SKU 价格:", json.dumps(sku_core.get("sku2info") or {}, ensure_ascii=False)[:300])
            comp = (d.get("componentsVO") or {}).get("extensionInfoVO") or {}
            infos = comp.get("infos") or []
            print("参数组:", len(infos), "组")
            for g in infos[:3]:
                for it in (g.get("items") or [])[:6]:
                    print("   ", it.get("title"), "=", it.get("text"))
        else:
            print("\n[!] 没截到主数据接口。已截: %s" % list(grab))
            print("    errors:", errors)

        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
