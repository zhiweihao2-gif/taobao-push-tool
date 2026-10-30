"""分析真实 pcdetail 响应：类目、参数表、详情图分别在哪。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FILE = Path("data/raw/taobao/probe_pcdetail_id.json")
if len(sys.argv) > 1:
    FILE = Path(sys.argv[1])


def main() -> int:
    d = json.loads(FILE.read_text(encoding="utf-8"))
    c = d.get("componentsVO") or {}
    print("componentsVO keys:", list(c.keys()))
    for k, v in c.items():
        s = json.dumps(v, ensure_ascii=False)
        print(f"  {k}: len={len(s)}")
        print(f"     {s[:200]}")

    print("\n--- pcTrade ---")
    print(json.dumps(d.get("pcTrade") or {}, ensure_ascii=False)[:400])

    print("\n--- 关键词定位 ---")
    raw = json.dumps(d, ensure_ascii=False)
    for kw in ("descInfo", "descUrl", "apiDesc", "categoryId", "catId",
               "cid", "detailUrl", "desc", "categoryPath", "brand"):
        idx = raw.find(kw)
        if idx >= 0:
            print(f"  {kw:<14} @{idx:<7} {raw[max(0,idx-40):idx+140]}")
        else:
            print(f"  {kw:<14} not found")

    print("\n--- sku2info 全部 key ---")
    s2i = (d.get("skuCore") or {}).get("sku2info") or {}
    for k, v in s2i.items():
        price = ((v.get("price") or {}).get("priceText"))
        qty = v.get("quantity")
        print(f"  {k:<18} price={price} qty={qty}")
    print("\n--- skuBase 完整 ---")
    sb = d.get("skuBase") or {}
    for p in sb.get("props") or []:
        names = [v.get("name") for v in (p.get("values") or [])]
        print(f"  pid={p.get('pid')} name={p.get('name')} values={names}")
    print("  skus:", json.dumps(sb.get("skus") or [], ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
