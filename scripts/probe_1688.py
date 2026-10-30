"""1688 采集入口探测：移动端页面 / PC 页面 / 更多 mtop API 名。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MORE_APIS = [
    "mtop.alibaba.pc.offer.detail.get",
    "mtop.alibaba.offer.detail.query",
    "mtop.alibaba.cbu.offer.detail.query",
    "mtop.1688.item.detail.get",
    "mtop.1688.offer.detail.getInfo",
    "mtop.alibaba.detail.offer.get",
    "mtop.alibaba.offer.detail.info.get",
    "mtop.1688.offer.detail.v3.get",
    "mtop.alibaba.pc.detail.get",
    "mtop.1688.offer.query.get",
]

PAGES = [
    ("m-1688", "https://m.1688.com/offer/{id}.html", UA_M),
    ("pc-1688", "https://detail.1688.com/offer/{id}.html", UA_PC),
    ("m-1688-detail", "https://m.1688.com/offer_detail.htm?offerId={id}", UA_M),
]

OFFER = sys.argv[1] if len(sys.argv) > 1 else "610947572360"


def probe_api(api: str) -> None:
    params = {
        "jsv": "2.6.1", "appKey": "12574478", "t": "1", "sign": "0",
        "api": api, "v": "1.0", "type": "json", "dataType": "json",
        "data": json.dumps({"offerId": OFFER, "detail_v": "1.0"},
                           separators=(",", ":")),
    }
    try:
        with httpx.Client(timeout=15, headers={"User-Agent": UA_M}) as c:
            r = c.get(f"https://h5api.m.1688.com/h5/{api}/1.0/", params=params)
        ret = (r.json().get("ret") or ["?"])[0]
        code = ret.split("::")[0]
        verdict = "不存在" if "NOT_FOUNDED" in code else "存在"
        print(f"  {api:<46} {code:<26} {verdict}")
    except Exception as e:
        print(f"  {api:<46} ERR {type(e).__name__}")


def probe_page(label: str, tpl: str, ua: str) -> None:
    url = tpl.format(id=OFFER)
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": ua}) as c:
            r = c.get(url)
        text = r.text
        markers = {
            "__INIT_DATA": "__INIT_DATA" in text,
            "iDataOffer": "iDataOffer" in text,
            "offerTitle": "offerTitle" in text,
            "x5sec": ("x5sec" in text or "_____tmd_____" in text),
            "globalData": "globalData" in text,
        }
        print(f"  [{label}] {r.status_code} len={len(text)} {markers}")
        if len(text) > 20000:
            Path(f"data/raw/alibaba/{OFFER}_{label}.html").parent.mkdir(
                parents=True, exist_ok=True)
            Path(f"data/raw/alibaba/{OFFER}_{label}.html").write_text(text, encoding="utf-8")
    except Exception as e:
        print(f"  [{label}] ERR {type(e).__name__}: {str(e)[:70]}")


def main() -> int:
    print(f"offer={OFFER}\n== mtop API 名 ==")
    for api in MORE_APIS:
        probe_api(api)
    print("\n== 页面入口 ==")
    for label, tpl, ua in PAGES:
        probe_page(label, tpl, ua)
    return 0


if __name__ == "__main__":
    sys.exit(main())
