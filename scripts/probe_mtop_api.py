"""探测 mtop API 名是否存在：NOT_FOUNDED = 名字不对，其他错误 = 名字对但被拦。

用法: python scripts/probe_mtop_api.py alibaba|taobao
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

HOSTS = {"taobao": "h5api.m.taobao.com", "alibaba": "h5api.m.1688.com"}

CANDIDATES = {
    "alibaba": [
        "mtop.1688.offer.detail.get",
        "mtop.alibaba.cbu.offer.detail.get",
        "mtop.alibaba.offer.detail.get",
        "mtop.1688.offer.get",
        "mtop.1688.offer.detail",
        "mtop.1688.trade.offer.detail.get",
        "mtop.1688.offer.detail.query",
        "mtop.alibaba.cbu.offer.get",
        "mtop.1688.offer.detail.info.get",
        "mtop.1688.detail.offer.get",
        "mtop.alibaba.trade.offer.detail.get",
        "mtop.1688.offer.detail.v2.get",
    ],
    "taobao": [
        "mtop.taobao.pcdetail.data.get",
        "mtop.taobao.detail.getdesc",
        "mtop.taobao.pcdetail.detail.get",
        "mtop.taobao.item.detail.get",
        "mtop.taobao.h5.detail.get",
    ],
}


def call(host: str, api: str, data: dict) -> dict:
    params = {
        "jsv": "2.6.1", "appKey": "12574478", "t": "1", "sign": "0",
        "api": api, "v": "1.0", "type": "json", "dataType": "json",
        "data": json.dumps(data, separators=(",", ":")),
    }
    url = f"https://{host}/h5/{api}/1.0/"
    with httpx.Client(timeout=15, headers={"User-Agent": UA,
                                           "Referer": "https://detail.1688.com/"}) as c:
        r = c.get(url, params=params)
        try:
            return r.json()
        except ValueError:
            return {"_raw": r.text[:120]}


def main() -> int:
    site = sys.argv[1] if len(sys.argv) > 1 else "alibaba"
    host = HOSTS[site]
    data = {"offerId": "610947572360", "detail_v": "1.0"} if site == "alibaba" \
        else {"itemId": "600000000001", "detail_v": "3.3.2", "exParams": "{}"}

    print(f"host={host}\n")
    for api in CANDIDATES[site]:
        try:
            resp = call(host, api, data)
            ret = (resp.get("ret") or ["?"])[0]
            code = ret.split("::")[0]
            verdict = "API 不存在" if "NOT_FOUNDED" in code or "ILLEGAL_API" in code \
                else ("✓ API 存在（被拦/需登录）" if code != "SUCCESS" else "✓ API 存在且可调")
            print(f"  {api:<44} {code:<28} {verdict}")
        except Exception as e:
            print(f"  {api:<44} ERR {type(e).__name__}: {str(e)[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
