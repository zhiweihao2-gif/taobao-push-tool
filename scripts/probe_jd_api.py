"""系统探测京东可用 JSON 接口（价格 / 库存 / SKU / 详情图）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

SKU = sys.argv[1] if len(sys.argv) > 1 else "100008348542"
AREA = "1_72_55674_0"
CAT = "9987,653,655"

TESTS = [
    ("soa-getWareBusiness",
     f"https://item-soa.jd.com/getWareBusiness?skuId={SKU}&area={AREA}&cat={CAT}"),
    ("soa-getWareBusiness-min",
     f"https://item-soa.jd.com/getWareBusiness?skuId={SKU}"),
    ("price-3cn-https", f"https://p.3.cn/prices/mgets?skuIds=J_{SKU}&type=1"),
    ("price-3cn-http", f"http://p.3.cn/prices/mgets?skuIds=J_{SKU}&type=1"),
    ("stock-http", f"http://c0.3.cn/stock?skuId={SKU}&area={AREA}&cat={CAT}&buyNum=1"),
    ("stock-https", f"https://c0.3.cn/stock?skuId={SKU}&area={AREA}&cat={CAT}&buyNum=1"),
    ("desc-cd", f"https://cd.jd.com/description/channel?skuId={SKU}&mainSkuId={SKU}"),
    ("desc-m", f"https://item.m.jd.com/ware/description.action?wareId={SKU}"),
    ("api-m-detail",
     f"https://api.m.jd.com/api?appid=item-v3&functionId=pc_detailpage_wareBusiness"
     f"&client=pc&clientVersion=1.0.0&body=%7B%22skuId%22%3A%22{SKU}%22%7D"),
    ("api-m-detail2",
     f"https://api.m.jd.com/?appid=item-v3&functionId=pc_detailpage_wareBusiness"
     f"&client=pc&clientVersion=1.0.0&body=%7B%22skuId%22%3A%22{SKU}%22%7D"),
    ("api-m-colorSize",
     f"https://api.m.jd.com/?appid=item-v3&functionId=pc_detailpage_wareBusiness"
     f"&client=pc&clientVersion=1.0.0&body=%7B%22skuId%22%3A%22{SKU}%22%2C%22cat%22%3A%22{CAT}%22%7D"),
]


def main() -> int:
    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": UA,
                               "Referer": f"https://item.jd.com/{SKU}.html",
                               "Accept": "application/json, text/plain, */*"}) as c:
        # 先访问首页拿 cookie
        try:
            c.get("https://www.jd.com/")
        except Exception:
            pass

        for label, url in TESTS:
            try:
                r = c.get(url)
                ct = r.headers.get("content-type", "")
                body = r.text
                print(f"[{label}] {r.status_code} ct={ct[:30]} len={len(body)}")
                if "json" in ct or body.lstrip().startswith(("{", "[")):
                    print("    JSON:", body[:400].replace("\n", " "))
                else:
                    print("    HTML:", body[:180].replace("\n", " "))
            except Exception as e:
                print(f"[{label}] ERR {type(e).__name__}: {str(e)[:110]}")
            print()

        # 移动端带 cookie 再取一次页面，看价格是否解密
        print("== 带 cookie 重新取移动端页面 ==")
        try:
            r = c.get(f"https://item.m.jd.com/product/{SKU}.html",
                      headers={"User-Agent": UA_M})
            import re
            m = re.search(r'"priceFloor"\s*:\s*(\{[^}]{0,300})', r.text)
            print("  priceFloor:", m.group(1)[:250] if m else "(未找到)")
            m2 = re.search(r'"newColorSize"\s*:\s*(\[[^\]]{0,200})', r.text)
            print("  newColorSize:", m2.group(1)[:200] if m2 else "(未找到)")
        except Exception as e:
            print("  ERR", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
