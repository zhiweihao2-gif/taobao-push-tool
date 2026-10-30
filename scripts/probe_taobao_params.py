"""在真实登录态下调试淘宝 pcdetail / getdesc 的参数名。

用法: python scripts/probe_taobao_params.py <商品ID>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.mtop import MTopClient  # noqa: E402
from src.collect.session import SessionStore  # noqa: E402

ITEM = sys.argv[1] if len(sys.argv) > 1 else "1086103866612"

CASES = [
    # (api, version, data, 说明)
    ("mtop.taobao.detail.getdesc", "1.0", {"id": ITEM}, "getdesc: id"),
    ("mtop.taobao.detail.getdesc", "1.0", {"itemId": ITEM}, "getdesc: itemId"),

    ("mtop.taobao.pcdetail.data.get", "1.0", {"itemId": ITEM}, "pcdetail: itemId"),
    ("mtop.taobao.pcdetail.data.get", "1.0", {"id": ITEM}, "pcdetail: id"),
    ("mtop.taobao.pcdetail.data.get", "1.0",
     {"itemId": ITEM, "detail_v": "3.3.2"}, "pcdetail: itemId+detail_v"),
    ("mtop.taobao.pcdetail.data.get", "1.0",
     {"itemId": ITEM, "exParams": "{}"}, "pcdetail: itemId+exParams"),
    ("mtop.taobao.pcdetail.data.get", "1.0",
     {"itemId": ITEM, "detail_v": "3.3.2", "exParams": "{}"}, "pcdetail: 三件套(当前实现)"),
    ("mtop.taobao.pcdetail.data.get", "1.0",
     {"id": ITEM, "detail_v": "3.3.2", "exParams": "{}"}, "pcdetail: id+detail_v+exParams"),
    ("mtop.taobao.pcdetail.data.get", "1.0",
     {"itemId": ITEM, "itemNumId": ITEM, "detail_v": "3.3.2", "exParams": "{}"},
     "pcdetail: 双 id 字段"),

    # 其他可能的详情接口
    ("mtop.taobao.item.detail.get", "1.0", {"itemId": ITEM}, "item.detail: itemId"),
    ("mtop.taobao.item.get", "1.0", {"itemId": ITEM}, "item.get: itemId"),
    ("mtop.taobao.detail.data.get", "1.0", {"itemId": ITEM}, "detail.data: itemId"),
]


def main() -> int:
    cookies = SessionStore().load("taobao")
    print(f"cookie 数量: {len(cookies)}")
    print(f"商品 ID: {ITEM}\n")

    client = MTopClient(site="taobao", cookies=cookies)
    hits: list[tuple[str, dict]] = []

    for api, ver, data, label in CASES:
        try:
            resp = client._raw_call(api, ver, data, allow_fail=True)
            ret = (resp.get("ret") or ["?"])[0]
            code = ret.split("::")[0]
            ok = code == "SUCCESS"
            d = resp.get("data") or {}
            print(f"  {label:<34} {code:<30} data_keys={list(d)[:6]}")
            if ok:
                hits.append((label, d))
        except Exception as e:
            print(f"  {label:<34} ERR {type(e).__name__}: {str(e)[:60]}")
        time.sleep(1.5)

    if hits:
        print("\n=== 成功的组合 ===")
        for label, d in hits:
            print(f"\n[{label}]")
            print(json.dumps(d, ensure_ascii=False)[:800])
            out = ROOT / "data/raw/taobao"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"probe_{label.replace(': ', '_').replace(' ', '_')}.json").write_text(
                json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        print("\n没有任何组合成功，需要换思路（可能接口已改版）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
