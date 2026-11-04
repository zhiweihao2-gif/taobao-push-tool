# -*- coding: utf-8 -*-
"""搜「键盘」类商品并逐个验证，只留下真实可打开的。

为什么需要这一步：淘宝搜索接口（mtop.taobao.wsearch.appsearch）返回的 ID 里
混着推广位、失效商品、聚合卡片，直接拿去 pcdetail 会拿到
    pcTrade.redirectUrl = error.item.taobao.com/error/noitem
所以必须逐个 probe。

用法：
    python scripts/search_verify.py --q 机械键盘 --want 3
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.mtop import MTopClient, MTopError  # noqa: E402

CK = ROOT / "config" / "cookies" / "taobao.txt"


def search(client: MTopClient, q: str, n: str = "20") -> list[dict]:
    r = client.call(
        "mtop.taobao.wsearch.appsearch", "1.0",
        {"q": q, "page": "1", "n": n, "sort": "_sale"}, retries=2,
    )
    s = json.dumps(r, ensure_ascii=False)
    ids = []
    seen = set()
    for m in re.finditer(r'"(?:itemId|item_id|nid)"\s*:\s*"?(\d{10,16})"?', s):
        i = m.group(1)
        if i not in seen:
            seen.add(i)
            ids.append(i)
    # 标题按出现顺序，和 ids 不一定一一对应，只作参考
    titles = re.findall(r'"(?:title|raw_title|itemTitle)"\s*:\s*"([^"]{2,60})"', s)
    return [{"id": i, "title": titles[k] if k < len(titles) else ""}
            for k, i in enumerate(ids)]


def probe_detail(client: MTopClient, item_id: str) -> dict | None:
    """返回 {title, price, ok} 或 None（商品不存在）。"""
    try:
        d = client.call("mtop.taobao.pcdetail.data.get", "1.0",
                        {"id": item_id, "detail_v": "3.3.2", "exParams": "{}"},
                        retries=2)
    except MTopError as e:
        return None
    except Exception:
        return None

    body = d.get("data") if isinstance(d.get("data"), dict) else d
    pc = (body or {}).get("pcTrade") or {}
    if pc.get("redirectReason") or pc.get("redirectUrl"):
        return None

    item_node = (body or {}).get("item") or {}
    title = str(item_node.get("title") or "")
    if not title:
        return None
    sku2 = (((body or {}).get("skuCore") or {}).get("sku2info") or {})
    prices = []
    for v in sku2.values():
        if not isinstance(v, dict):
            continue
        pt = ((v.get("price") or {}).get("priceText")
              or (v.get("price") or {}).get("priceMoney"))
        try:
            prices.append(float(pt))
        except (TypeError, ValueError):
            pass
    return {"title": title, "price": (min(prices), max(prices)) if prices else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="机械键盘")
    ap.add_argument("--want", type=int, default=3)
    ap.add_argument("--n", default="20")
    args = ap.parse_args()

    c = MTopClient(site="taobao", cookie_file=str(CK))
    try:
        hits = search(c, args.q, args.n)
        print("[..] 搜索「%s」-> %d 个候选 ID" % (args.q, len(hits)))
        if not hits:
            return 1

        good = []
        for k, h in enumerate(hits[:15], 1):
            info = probe_detail(c, h["id"])
            if not info:
                print("  %2d. %-16s [失效]" % (k, h["id"]))
                continue
            pr = ""
            if info["price"]:
                pr = " ¥%s~%s" % info["price"]
            print("  %2d. %-16s [OK]%s  %s"
                  % (k, h["id"], pr, info["title"][:44]))
            good.append({"id": h["id"], **info})
            if len(good) >= args.want:
                break

        print("\n可用商品 %d 个：" % len(good))
        for g in good:
            print("  %s  %s" % (g["id"], g["title"][:50]))
        if good:
            out = ROOT / "data" / "publish_poc" / "verified_items.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(good, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print("\n写入 %s" % out)
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
