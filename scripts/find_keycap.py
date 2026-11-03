"""用 mtop 搜索接口找一个真实键帽商品 ID。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.mtop import MTopClient, MTopError  # noqa: E402

CK = ROOT / "config" / "cookies" / "taobao.txt"

CANDIDATES = [
    ("mtop.taobao.wsearch.appsearch", "1.0", {"q": "键帽", "page": "1", "n": "20", "sort": "_sale"}),
    ("mtop.taobao.search.h5search", "1.0", {"q": "键帽", "page": "1"}),
    ("mtop.relationrecommend.wirelessrecommend.recommend", "2.0", {"q": "键帽"}),
]


def main() -> int:
    c = MTopClient(site="taobao", cookie_file=str(CK))
    for api, ver, data in CANDIDATES:
        print("\n=== %s v%s ===" % (api, ver))
        try:
            r = c.call(api, ver, data, retries=2)
        except MTopError as e:
            print("  MTopError: %s :: %s" % (e.code, str(e.message)[:120]))
            continue
        except Exception as e:
            print("  %s: %s" % (type(e).__name__, str(e)[:120]))
            continue

        s = json.dumps(r, ensure_ascii=False)
        print("  返回 %d 字节" % len(s))
        # 找 item id
        import re
        ids = re.findall(r'"(?:itemId|item_id|nid|id)"\s*:\s*"?(\d{10,16})"?', s)
        titles = re.findall(r'"(?:title|raw_title|itemTitle)"\s*:\s*"([^"]{2,40})"', s)
        seen, uniq = set(), []
        for i in ids:
            if i not in seen:
                seen.add(i)
                uniq.append(i)
        print("  ids(%d): %s" % (len(uniq), uniq[:10]))
        print("  titles(%d): %s" % (len(titles), titles[:6]))
        if uniq:
            print("  >>> 用这个试: %s" % uniq[0])
            break
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
