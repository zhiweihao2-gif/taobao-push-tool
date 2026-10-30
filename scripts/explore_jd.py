"""探查京东详情页真实结构，校验采集器 xpath 命中情况。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from lxml import html as LH

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SKU = sys.argv[1] if len(sys.argv) > 1 else "100012043978"


def show(label: str, nodes, limit: int = 3) -> None:
    n = len(nodes)
    print(f"  {label:<38} hits={n}")
    for node in nodes[:limit]:
        txt = " ".join(node.text_content().split())[:90] if hasattr(node, "text_content") else str(node)[:90]
        print(f"      · {txt}")


def main() -> int:
    url = f"https://item.jd.com/{SKU}.html"
    r = httpx.get(url, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    print(f"status={r.status_code} len={len(r.text)} final={r.url}\n")
    Path("data/raw/jd").mkdir(parents=True, exist_ok=True)
    Path(f"data/raw/jd/{SKU}.html").write_text(r.text, encoding="utf-8")

    doc = LH.fromstring(r.text)
    print("== 标题 ==")
    show("//div[contains(@class,'sku-name')]", doc.xpath("//div[contains(@class,'sku-name')]"))
    show("//title", doc.xpath("//title"))
    print("  meta keywords:", (doc.xpath("//meta[@name='keywords']/@content") or [""])[0][:100])

    print("\n== 主图 ==")
    show("//div[@id='spec-img']", doc.xpath("//div[@id='spec-img']"))
    show("//div[@id='spec-list']//img", doc.xpath("//div[@id='spec-list']//img"))
    for node in doc.xpath("//div[@id='spec-img']")[:1]:
        print("      attrs:", {k: v[:80] for k, v in node.attrib.items()})

    print("\n== 参数表 ==")
    show("//div[contains(@class,'p-parameter')]//li",
         doc.xpath("//div[contains(@class,'p-parameter')]//li"), 5)
    show("//ul[contains(@class,'parameter')]//li",
         doc.xpath("//ul[contains(@class,'parameter')]//li"), 5)

    print("\n== 面包屑 ==")
    show("//div[contains(@class,'crumb')]//a", doc.xpath("//div[contains(@class,'crumb')]//a"), 6)

    print("\n== 销售属性 ==")
    show("//div[@id='choose-attrs']", doc.xpath("//div[@id='choose-attrs']"))
    show("//div[contains(@class,'choose-attrs')]//div[contains(@class,'li')]",
         doc.xpath("//div[contains(@class,'choose-attrs')]//div[contains(@class,'li')]"), 4)

    print("\n== 内嵌变量 ==")
    for var in ("pageConfig", "skuList", "__NEXT_DATA__", "window._itemInfo"):
        m = re.search(rf"{var}\s*[=:]\s*", r.text)
        print(f"  {var:<18} found={bool(m)}")
    m = re.search(r'"skuId"\s*:\s*"?(\d+)"?', r.text)
    print("  首个 skuId 匹配:", m.group(1) if m else None)
    print("  HTML 片段前 300:", r.text[:300].replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
