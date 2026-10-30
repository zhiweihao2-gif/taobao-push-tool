"""连通性探针：确认三平台可达性与响应特征。"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

TARGETS = [
    ("jd-item", "https://item.jd.com/100012043978.html", UA_PC),
    ("jd-price", "https://p.3.cn/prices/mgets?skuIds=J_100012043978&type=1", UA_PC),
    ("jd-desc", "https://cd.jd.com/description/channel?skuId=100012043978&mainSkuId=100012043978", UA_PC),
    ("tb-item", "https://item.taobao.com/item.htm?id=1", UA_M),
    ("tb-h5api", "https://h5api.m.taobao.com/h5/mtop.common.getTimestamp/1.0/?jsv=2.6.1&appKey=12574478&t=1&sign=x&api=mtop.common.getTimestamp&v=1.0&type=json&dataType=json&data=%7B%7D", UA_M),
    ("ali-h5api", "https://h5api.m.1688.com/h5/mtop.common.getTimestamp/1.0/?jsv=2.6.1&appKey=12574478&t=1&sign=x&api=mtop.common.getTimestamp&v=1.0&type=json&dataType=json&data=%7B%7D", UA_M),
    ("ali-item", "https://detail.1688.com/offer/610947572360.html", UA_PC),
    ("alicdn-img", "https://img.alicdn.com/imgextra/i1/O1CN01Z5paLz1O0zuMZqf0W_!!6000000001644-0-tps-800-800.jpg", UA_PC),
]


def main() -> int:
    for name, url, ua in TARGETS:
        try:
            with httpx.Client(timeout=15, follow_redirects=True,
                              headers={"User-Agent": ua}) as c:
                r = c.get(url)
            body = r.text[:200].replace("\n", " ")
            print(f"[{name}] {r.status_code} len={len(r.text)} ct={r.headers.get('content-type','')[:40]}")
            print(f"    head: {body[:150]}")
            for k in ("set-cookie", "location"):
                if r.headers.get(k):
                    print(f"    {k}: {str(r.headers.get(k))[:160]}")
        except Exception as e:
            print(f"[{name}] ERROR {type(e).__name__}: {e}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
