"""找商品类目 ID 和详情图：多版本 pcdetail + PC 页面（带登录态）。

请求数刻意控制在 8 个以内，间隔 3 秒，避免触发滑块。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.mtop import MTopClient  # noqa: E402
from src.collect.session import SessionStore  # noqa: E402

ITEM = sys.argv[1] if len(sys.argv) > 1 else "1073025553052"

VERSIONS = [
    ("3.3.2", {"id": ITEM, "detail_v": "3.3.2", "exParams": "{}"}),
    ("4.0", {"id": ITEM, "detail_v": "4.0", "exParams": "{}"}),
    ("5.0", {"id": ITEM, "detail_v": "5.0", "exParams": "{}"}),
    ("no-detail_v", {"id": ITEM, "exParams": "{}"}),
]


def scan_category(obj, path: str = "") -> list[tuple[str, str]]:
    """递归找所有可能是类目 ID 的字段。"""
    hits: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if re.search(r"(category|catid|cid|leafcat|rootcat|itemcat)", k, re.I):
                hits.append((p, json.dumps(v, ensure_ascii=False)[:120]))
            hits += scan_category(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            hits += scan_category(v, f"{path}[{i}]")
    return hits


def main() -> int:
    cookies = SessionStore().load("taobao")
    client = MTopClient(site="taobao", cookies=cookies)

    print("== 1. pcdetail 不同 detail_v 版本 ==")
    best = None
    for label, data in VERSIONS:
        try:
            r = client._raw_call("mtop.taobao.pcdetail.data.get", "1.0", data,
                                 allow_fail=True)
            ret = (r.get("ret") or ["?"])[0]
            code = ret.split("::")[0]
            d = r.get("data") or {}
            cats = scan_category(d)
            print(f"  detail_v={label:<12} {code:<26} keys={len(d)} 类目字段={cats[:3]}")
            if code == "SUCCESS" and d and best is None:
                best = d
        except Exception as e:
            print(f"  detail_v={label:<12} ERR {type(e).__name__}")
        time.sleep(3)

    if best:
        out = ROOT / "data/raw/taobao"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{ITEM}_pcdetail_full.json").write_text(
            json.dumps(best, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n== 2. PC 详情页（带登录态）==")
    with httpx.Client(timeout=30, follow_redirects=True, cookies=cookies,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/124.0.0.0 Safari/537.36",
                               "Referer": "https://www.taobao.com/"}) as c:
        url = f"https://item.taobao.com/item.htm?id={ITEM}"
        r = c.get(url)
        html = r.text
        print(f"  status={r.status_code} len={len(html)}")
        Path("data/raw/taobao").mkdir(parents=True, exist_ok=True)
        Path(f"data/raw/taobao/{ITEM}_pc2.html").write_text(html, encoding="utf-8")

        for kw in ("__INIT_DATA__", "TShop.Setup", "descUrl", "apiDesc",
                   "categoryId", "rootCategoryId", "g_config", "detailUrl"):
            idx = html.find(kw)
            print(f"    {kw:<18} {'@'+str(idx) if idx>=0 else 'not found'}"
                  + (f"  {html[max(0,idx-20):idx+100]}".replace('\n', ' ') if idx >= 0 else ""))

        m = re.search(r'"categoryId"\s*:\s*"?(\d+)"?', html) or \
            re.search(r'categoryId["\']?\s*[:=]\s*["\'](\d+)', html)
        print("    类目正则:", m.group(1) if m else None)

        m2 = re.search(r'descUrl["\']?\s*[:=]\s*["\']([^"\']+)', html)
        print("    详情 URL:", m2.group(1)[:120] if m2 else None)

    return 0


if __name__ == "__main__":
    sys.exit(main())
