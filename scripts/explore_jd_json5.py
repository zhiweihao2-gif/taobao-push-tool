"""用 json5 宽松解析京东 _itemInfo / _itemOnly，摸清可用字段。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import json5

FILE = Path("data/raw/jd/100008348542_M.html")
if len(sys.argv) > 1:
    FILE = Path(sys.argv[1])


def slice_balanced(text: str, var: str) -> str:
    m = re.search(rf"(?:window\.)?{re.escape(var)}\s*=\s*\(?\s*[\[{{]", text)
    if not m:
        return ""
    start = text.index(m.group(0)[-1], m.start())
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        else:
            if ch in "\"'":
                in_str, quote = True, ch
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return ""


def main() -> int:
    text = FILE.read_text(encoding="utf-8", errors="ignore")
    for var in ("_itemOnly", "_itemInfo"):
        raw = slice_balanced(text, var)
        print(f"\n===== {var} raw len={len(raw)} =====")
        if not raw:
            continue
        try:
            obj = json5.loads(raw)
        except Exception as e:
            print("  json5 失败:", e)
            continue
        print("  top keys:", list(obj.keys())[:20])
        Path(f"data/raw/jd/{var.strip('_')}.json").write_text(
            json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

        if var == "_itemOnly":
            item = obj.get("item", {})
            print("  item keys:", list(item.keys()))
            print("  image:", item.get("image"))
            print("  category:", item.get("category"))
        else:
            prod = obj.get("product", {})
            print("  product keys:", list(prod.keys())[:30])
            for k in ("skuName", "category", "brandName", "sellPoint", "model",
                      "weight", "productArea", "colorSize", "newColorSize",
                      "saleProp", "salePropSeq", "color", "size"):
                if k in prod:
                    v = prod[k]
                    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
                    print(f"  product.{k} = {s[:200]}")
            print("  priceFloor:", json.dumps(obj.get("priceFloor"), ensure_ascii=False)[:300])
            print("  top-level 其余 keys:", [k for k in obj.keys()][:30])
            for k in ("skuPro", "clothesColor", "wareInfo", "ad", "detail"):
                if k in obj:
                    print(f"  {k}:", json.dumps(obj[k], ensure_ascii=False)[:250])
    return 0


if __name__ == "__main__":
    sys.exit(main())
