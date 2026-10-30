"""找到真实京东 SKU，并测试多种详情数据源的可达性与结构。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
from lxml import html as LH

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def new_client() -> httpx.Client:
    return httpx.Client(
        timeout=20, follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    )


def find_skus(c: httpx.Client, keyword: str = "保温杯") -> list[str]:
    print(f"== 搜索页找 SKU: {keyword} ==")
    for url in (
        f"https://search.jd.com/Search?keyword={keyword}&enc=utf-8",
        f"https://so.m.jd.com/ware/search.action?keyword={keyword}",
    ):
        try:
            r = c.get(url)
            skus = re.findall(r'data-sku="(\d+)"', r.text)
            if not skus:
                skus = re.findall(r'item\.jd\.com/(\d{6,})\.html', r.text)
            uniq = list(dict.fromkeys(skus))[:8]
            print(f"  {url[:60]} -> {r.status_code} len={len(r.text)} skus={uniq[:8]}")
            if uniq:
                return uniq
        except Exception as e:
            print(f"  {url[:60]} ERR {type(e).__name__}: {e}")
    return []


def probe_detail(c: httpx.Client, sku: str) -> None:
    print(f"\n== 详情页 {sku} ==")
    for url, label in (
        (f"https://item.jd.com/{sku}.html", "PC"),
        (f"https://item.m.jd.com/product/{sku}.html", "M"),
    ):
        try:
            r = c.get(url, headers={"Referer": "https://www.jd.com/"})
            doc = LH.fromstring(r.text)
            title = (doc.xpath("//title/text()") or [""])[0][:60]
            hits = {
                "sku-name": len(doc.xpath("//div[contains(@class,'sku-name')]")),
                "spec-img": len(doc.xpath("//div[@id='spec-img']")),
                "p-parameter-li": len(doc.xpath("//div[contains(@class,'p-parameter')]//li")),
                "crumb": len(doc.xpath("//div[contains(@class,'crumb')]//a")),
                "choose-attrs": len(doc.xpath("//div[@id='choose-attrs']")),
            }
            print(f"  [{label}] {r.status_code} len={len(r.text)} title={title}")
            print(f"      xpath hits: {hits}")
            Path(f"data/raw/jd/{sku}_{label}.html").write_text(r.text, encoding="utf-8")
        except Exception as e:
            print(f"  [{label}] ERR {type(e).__name__}: {e}")


def probe_apis(c: httpx.Client, sku: str) -> None:
    print(f"\n== 辅助接口 {sku} ==")
    tests = [
        ("price-http", f"http://p.3.cn/prices/mgets?skuIds=J_{sku}&type=1"),
        ("price-soa", f"https://item-soa.jd.com/getWareBusiness?skuId={sku}"),
        ("stock", f"https://c0.3.cn/stock?skuId={sku}&area=1_72_2799_0&cat=670,671,1195&buyNum=1"),
        ("desc", f"https://cd.jd.com/description/channel?skuId={sku}&mainSkuId={sku}"),
        ("h5-detail", f"https://item.m.jd.com/ware/detail.json?wareId={sku}"),
    ]
    for label, url in tests:
        try:
            r = c.get(url, headers={"Referer": f"https://item.jd.com/{sku}.html"})
            body = r.text[:220].replace("\n", " ")
            print(f"  [{label}] {r.status_code} ct={r.headers.get('content-type','')[:28]} len={len(r.text)}")
            print(f"      {body}")
        except Exception as e:
            print(f"  [{label}] ERR {type(e).__name__}: {str(e)[:120]}")


def main() -> int:
    Path("data/raw/jd").mkdir(parents=True, exist_ok=True)
    with new_client() as c:
        try:
            home = c.get("https://www.jd.com/")
            print(f"== 首页 {home.status_code} cookies={[k.name for k in c.cookies.jar][:8]}")
        except Exception as e:
            print("首页 ERR", e)

        skus = find_skus(c)
        if not skus:
            skus = ["100008348542"]
        for sku in skus[:2]:
            probe_detail(c, sku)
            probe_apis(c, sku)
    return 0


if __name__ == "__main__":
    sys.exit(main())
