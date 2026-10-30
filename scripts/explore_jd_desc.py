"""确认京东移动端参数区结构 + 探测详情图接口。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from lxml import html as LH

FILE = Path("data/raw/jd/100008348542_M.html")
SKU = "100008348542"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def main() -> int:
    text = FILE.read_text(encoding="utf-8", errors="ignore")
    doc = LH.fromstring(text)

    print("== class 精确名统计（detail 相关）==")
    names: dict[str, int] = {}
    for n in doc.xpath("//*[@class]"):
        cls = n.get("class")
        names[cls] = names.get(cls, 0) + 1
    for cls, c in sorted(names.items(), key=lambda x: -x[1]):
        if "detail" in cls:
            print(f"  {cls!r:<46} {c}")

    print("\n== detail-item 精确节点 ==")
    rows = doc.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' detail-item ')]")
    print(f"  n={len(rows)}")
    for r in rows[:6]:
        print("   class=", r.get("class"))
        print("     text=", " ".join(r.text_content().split())[:120])
        lab = r.xpath(".//*[contains(@class,'label')]")
        val = r.xpath(".//*[contains(@class,'value')]")
        print("     label=", " ".join(lab[0].text_content().split())[:40] if lab else None,
              "| value=", " ".join(val[0].text_content().split())[:60] if val else None)

    print("\n== detail_transfer_row（配送信息，应排除）==")
    for r in doc.xpath("//*[contains(@class,'detail_transfer_row')]")[:5]:
        print("   ", " ".join(r.text_content().split())[:110])

    print("\n== 详情图候选接口 ==")
    cands = [
        f"https://desc.jd.com/description/channel?skuId={SKU}&mainSkuId={SKU}",
        f"https://cd.jd.com/description/channel?skuId={SKU}&mainSkuId={SKU}&callback=cb",
        f"https://api.m.jd.com/api?appid=item-v3&functionId=pc_detailpage_wareBusiness&client=pc&clientVersion=1.0.0&body=%7B%22skuId%22%3A%22{SKU}%22%7D",
        f"https://item.m.jd.com/ware/description.json?skuId={SKU}",
        f"https://cdnware.m.jd.com/1.0/ware/desc/{SKU}.json",
        f"https://item.m.jd.com/product/desc/{SKU}.html",
        f"https://desc.jd.com/api/desc?skuId={SKU}",
    ]
    with httpx.Client(timeout=15, follow_redirects=True,
                      headers={"User-Agent": UA, "Referer": f"https://item.m.jd.com/product/{SKU}.html"}) as c:
        for url in cands:
            try:
                r = c.get(url)
                ct = r.headers.get("content-type", "")[:30]
                body = r.text
                has_img = "<img" in body or '"content"' in body
                print(f"  {r.status_code} ct={ct:<30} len={len(body):<8} img={has_img} {url[:70]}")
                if has_img:
                    print("     head:", body[:200].replace("\n", " "))
            except Exception as e:
                print(f"  ERR {type(e).__name__}: {str(e)[:80]} {url[:60]}")

    print("\n== 页面里的详情相关变量 ==")
    for kw in ("descUrl", "detailUrl", "description", "descContent", "wareDesc"):
        m = re.search(rf"{kw}\s*[:=]\s*[\"']([^\"']{{0,120}})", text)
        print(f"  {kw:<14} {m.group(1)[:110] if m else '(无)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
