"""提取京东移动端的 saleProp / propSeq / newColorSize / priceFloor 等 JS 变量。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FILE = Path("data/raw/jd/100008348542_M.html")
if len(sys.argv) > 1:
    FILE = Path(sys.argv[1])


def grab(text: str, var: str, span: int = 2000) -> str:
    m = re.search(rf"(?:window\.|_itemInfo\.)?{re.escape(var)}\s*[:=]\s*", text)
    if not m:
        return ""
    return text[m.end():m.end() + span]


def main() -> int:
    text = FILE.read_text(encoding="utf-8", errors="ignore")
    for var in ("saleProp", "propSeq", "newColorSize", "priceFloor", "colorSize",
                "skuList", "wareInfo", "descUrl", "detailUrl"):
        print(f"\n===== {var} =====")
        snippet = grab(text, var, 1500)
        print(snippet[:1200] if snippet else "  (未找到)")

    print("\n===== 所有 jfs 图片路径 =====")
    jfs = list(dict.fromkeys(re.findall(r"jfs/t\d+/[\w/]+\.(?:jpg|png|webp)", text)))
    for p in jfs[:15]:
        print("  ", p)

    print("\n===== 价格数字候选 =====")
    for m in list(re.finditer(r'"price"\s*:\s*"([^"]{1,12})"', text))[:8]:
        print("  price =", m.group(1))
    for m in list(re.finditer(r'"jdPrice"\s*:\s*"([^"]{1,12})"', text))[:5]:
        print("  jdPrice =", m.group(1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
