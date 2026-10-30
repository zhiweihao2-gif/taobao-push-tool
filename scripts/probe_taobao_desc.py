"""在真实登录态下找：类目 ID、详情图 URL。"""
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

ITEM = sys.argv[1] if len(sys.argv) > 1 else "1086103866612"

DESC_APIS = [
    ("mtop.taobao.pcdetail.desc.get", "1.0", {"id": ITEM}),
    ("mtop.taobao.detail.desc.get", "1.0", {"id": ITEM}),
    ("mtop.taobao.pcdetail.data.get", "1.0",
     {"id": ITEM, "detail_v": "3.3.2", "exParams": "{}", "withDesc": "true"}),
    ("mtop.taobao.desc.get", "1.0", {"id": ITEM}),
]


def main() -> int:
    cookies = SessionStore().load("taobao")
    client = MTopClient(site="taobao", cookies=cookies)

    print("== 1. 详情接口探测 ==")
    for api, ver, data in DESC_APIS:
        try:
            r = client._raw_call(api, ver, data, allow_fail=True)
            ret = (r.get("ret") or ["?"])[0]
            d = r.get("data") or {}
            print(f"  {api:<38} {ret.split('::')[0]:<28} keys={list(d)[:5]}")
        except Exception as e:
            print(f"  {api:<38} ERR {type(e).__name__}")
        time.sleep(1.2)

    print("\n== 2. PC 详情页 HTML ==")
    with httpx.Client(timeout=25, follow_redirects=True, cookies=cookies,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/124.0.0.0 Safari/537.36"}) as c:
        url = f"https://item.taobao.com/item.htm?id={ITEM}"
        r = c.get(url)
        html = r.text
        print(f"  status={r.status_code} len={len(html)} final={r.url}")
        Path("data/raw/taobao").mkdir(parents=True, exist_ok=True)
        Path(f"data/raw/taobao/{ITEM}_pc.html").write_text(html, encoding="utf-8")

        print("\n  -- 关键标记 --")
        for kw in ("__INIT_DATA__", "TShop.Setup", "descUrl", "apiDesc",
                   "categoryId", "rootCategoryId", "cid", "desc.alicdn",
                   "detail.tmall", "gdp.alicdn"):
            idx = html.find(kw)
            print(f"    {kw:<18} {'@'+str(idx) if idx>=0 else 'not found'}"
                  + (f"  ...{html[max(0,idx-30):idx+90]}".replace('\n',' ') if idx >= 0 else ""))

        print("\n  -- 类目线索 --")
        for pat in (r'"categoryId"\s*:\s*"?(\d+)"?', r'"rootCategoryId"\s*:\s*"?(\d+)"?',
                    r'catId["\']?\s*[:=]\s*["\']?(\d+)'):
            m = re.search(pat, html)
            print(f"    {pat[:30]:<32} -> {m.group(1) if m else None}")

        print("\n  -- 详情图线索 --")
        m = re.search(r'descUrl["\']?\s*[:=]\s*["\']([^"\']+)', html)
        print("    descUrl:", m.group(1)[:120] if m else None)
        imgs = re.findall(r'(//(?:desc|gd\d|img)\d*\.alicdn\.com/[^"\'\s]+)', html)
        print(f"    页面内 alicdn 图片链接 {len(imgs)} 个（去重后 {len(set(imgs))}）")
        for u in list(dict.fromkeys(imgs))[:8]:
            print("      ", u[:110])
    return 0


if __name__ == "__main__":
    sys.exit(main())
