"""详情图清洗 CLI（实现已移到 src/collect/detail_img.py，这里只做薄包装）。

用法:
    python scripts/clean_detail_images.py <render.json 或 getdesc_v6.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.base import extract_img_with_size          # noqa: E402
from src.collect.detail_img import clean_detail_images      # noqa: E402,F401


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/clean_detail_images.py <render.json|getdesc_v6.json>")
        return 2
    p = Path(sys.argv[1])
    data = json.loads(p.read_text(encoding="utf-8"))

    urls: list[str] = []
    sizes: list[list[int]] | None = None

    # getdesc v6 形态：{"data":{"pcDescContent": "<html>"}}
    html = ((data.get("data") or {}).get("pcDescContent")
            or data.get("pcDescContent") or "")
    if html:
        items = extract_img_with_size(html)
        urls = [x["url"] for x in items]
        sizes = [[x["w"], x["h"]] for x in items]
    else:
        urls = data.get("all") or data.get("detail_images") or []
        sizes = data.get("sizes")

    out = clean_detail_images(urls, sizes)
    print("原始 %d 张 -> 清洗后 %d 张" % (len(urls), len(out)))
    for i, u in enumerate(out, 1):
        print("  %2d. %s" % (i, u))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
