"""从京东移动端首页/频道页发现真实 SKU，用于验证多规格商品解析。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

SOURCES = [
    "https://m.jd.com/",
    "https://so.m.jd.com/ware/search.action?keyword=%E4%BF%9D%E6%B8%A9%E6%9D%AF",
    "https://item.m.jd.com/product/100008348542.html",
]


def main() -> int:
    found: list[str] = []
    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        for url in SOURCES:
            try:
                r = c.get(url)
                skus = re.findall(r"(?:product/|item\.jd\.com/)(\d{8,14})\.html", r.text)
                skus += re.findall(r'"skuId"\s*:\s*"?(\d{8,14})"?', r.text)
                uniq = [s for s in dict.fromkeys(skus) if s != "100008348542"]
                print(f"{url[:60]:<62} {r.status_code} len={len(r.text):<8} skus={uniq[:6]}")
                found += uniq
            except Exception as e:
                print(f"{url[:60]:<62} ERR {type(e).__name__}")
    print("\n候选:", list(dict.fromkeys(found))[:10])
    return 0


if __name__ == "__main__":
    sys.exit(main())
