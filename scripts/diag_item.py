# -*- coding: utf-8 -*-
"""诊断单个商品的 pcdetail 原始响应，区分「不存在」「被限制」「解析判据太严」。

用法: python scripts/diag_item.py 745703904965
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.browser_collect2 import MtopInBrowser  # noqa: E402
from src.publish.browser_pub import BrowserSession  # noqa: E402

WARMUP = "https://item.taobao.com/item.htm?id=902412139914"


def main() -> int:
    iid = sys.argv[1] if len(sys.argv) > 1 else ""
    if not iid:
        print("用法: python scripts/diag_item.py <item_id>")
        return 2

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})

        # 先直接打开商品页，看真实落地情况（这一步比走接口更接近真实用户）
        url = "https://item.taobao.com/item.htm?id=%s" % iid
        print("[..] 打开 %s" % url)
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("[!] goto: %s" % str(e).split("\n")[0][:100])
        p.wait_for_timeout(3500)

        cur = p.url or ""
        title = p.title() or ""
        body_head = p.evaluate("() => document.body ? document.body.innerText.slice(0,400) : ''") or ""
        html_len = len(p.evaluate("() => document.documentElement.outerHTML") or "")
        print("[..] 落地 URL: %s" % cur[:120])
        print("[..] 标题: %s" % title[:100])
        print("[..] HTML %d 字符" % html_len)
        print("[..] 正文头部: %r" % body_head[:200])

        # 页面里是否有商品数据
        for marker in ("skuBase", "skuCore", "leafCategory", "ItemTitle", "tbShopRedPocket"):
            n = p.evaluate("(m) => (document.documentElement.outerHTML.split(m).length - 1)", marker)
            print("      %-18s %d 处" % (marker, n))

        # 再从接口层看
        print()
        print("[..] 走 mtop pcdetail")
        mb = MtopInBrowser(p)
        print("[..] token = %s" % (mb.token[:20] or "(空)"))
        try:
            d = mb.call("mtop.taobao.pcdetail.data.get", "1.0",
                        {"id": iid, "detail_v": "3.3.2", "exParams": "{}"})
            print("[OK] ret = %s" % d.get("ret"))
            data = d.get("data") or {}
            print("[..] data keys = %s" % (list(data.keys())[:20] if isinstance(data, dict) else type(data)))
            pc = (data.get("pcTrade") or {}) if isinstance(data, dict) else {}
            print("[..] pcTrade = %s" % json.dumps(pc, ensure_ascii=False)[:300])
            item = (data.get("item") or {}) if isinstance(data, dict) else {}
            print("[..] item.title = %r" % str(item.get("title") or "")[:100])
            print("[..] item 字段数 = %d" % len(item))
            out = "data/publish_poc/diag_%s.json" % iid
            os.makedirs("data/publish_poc", exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            print("[..] 完整响应 -> %s (%d 字节)" % (out, os.path.getsize(out)))
        except Exception as e:
            print("[XX] %s" % str(e)[:300])

        p.screenshot(path="data/publish_poc/diag_%s.png" % iid, full_page=False)
        print("[..] 截图 data/publish_poc/diag_%s.png" % iid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
