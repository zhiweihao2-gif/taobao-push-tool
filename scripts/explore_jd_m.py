"""分析京东移动端详情页（item.m.jd.com，SSR）真实结构。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from lxml import html as LH

FILE = Path("data/raw/jd/100008348542_M.html")
if len(sys.argv) > 1:
    FILE = Path(sys.argv[1])


def main() -> int:
    text = FILE.read_text(encoding="utf-8", errors="ignore")
    print(f"file={FILE} len={len(text)}\n")
    doc = LH.fromstring(text)

    print("== title ==")
    print("  ", (doc.xpath("//title/text()") or [""])[0][:100])

    print("\n== script 里的全局变量 ==")
    for m in re.finditer(r"(?:window\.)?([A-Za-z_$][\w$]{2,40})\s*=\s*(\{)", text):
        name = m.group(1)
        if name.lower() in ("function", "var", "return", "if", "for"):
            continue
        print(f"  {name}  @{m.start()}")

    print("\n== 关键字出现次数 ==")
    for kw in ("skuName", "wareName", "price", "jdPrice", "skuId", "imageList",
               "images", "colorSize", "saleProp", "规格", "商品介绍", "parameter",
               "详情", "venderId", "category", "shopName", "p-price", "priceInfo"):
        print(f"  {kw:<14} {text.count(kw)}")

    print("\n== class 名 top（含 price/sku/spec/img/detail）==")
    classes: dict[str, int] = {}
    for node in doc.xpath("//*[@class]"):
        for cls in (node.get("class") or "").split():
            if any(k in cls.lower() for k in ("price", "sku", "spec", "img", "detail", "name", "param")):
                classes[cls] = classes.get(cls, 0) + 1
    for cls, n in sorted(classes.items(), key=lambda x: -x[1])[:25]:
        print(f"  {cls:<32} {n}")

    print("\n== 图片 src 样本 ==")
    srcs = []
    for img in doc.xpath("//img"):
        for attr in ("src", "data-src", "data-lazy-img", "data-original"):
            v = img.get(attr)
            if v and ("360buyimg" in v or "jd.com" in v):
                srcs.append(v)
                break
    for s in list(dict.fromkeys(srcs))[:8]:
        print("  ", s[:110])

    print("\n== 尝试定位 JSON 数据块 ==")
    for m in re.finditer(r"<script[^>]*>(.{200,}?)</script>", text, re.S):
        body = m.group(1).strip()
        if body.startswith("{") or body.startswith("["):
            print(f"  JSON-ish script len={len(body)} head={body[:120]}")
        elif "skuId" in body and len(body) > 1000:
            print(f"  script with skuId len={len(body)} head={body[:160]}")

    print("\n== 价格相关文本 ==")
    for m in list(re.finditer(r"[\"']?(?:price|jdPrice|p)\b[\"']?\s*[:=]\s*[\"']?([\d.]+)", text))[:10]:
        print("  ", m.group(0)[:60])
    return 0


if __name__ == "__main__":
    sys.exit(main())
