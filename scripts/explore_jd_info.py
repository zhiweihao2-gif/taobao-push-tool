"""深挖 _itemInfo / propArr 原始文本，确定解析策略。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FILE = Path("data/raw/jd/100008348542_M.html")
if len(sys.argv) > 1:
    FILE = Path(sys.argv[1])


def slice_obj(text: str, var: str, span: int = 6000) -> str:
    m = re.search(rf"(?:window\.)?{re.escape(var)}\s*=\s*\(?\s*[\[{{]", text)
    if not m:
        return ""
    start = m.start()
    return text[start:start + span]


def main() -> int:
    text = FILE.read_text(encoding="utf-8", errors="ignore")

    print("===== _itemInfo 片段 =====")
    print(slice_obj(text, "_itemInfo", 3000))

    print("\n===== propArr 片段 =====")
    print(slice_obj(text, "propArr", 1200))

    print("\n===== skuPro 片段 =====")
    print(slice_obj(text, "skuPro", 1500))

    print("\n===== 价格相关 =====")
    for pat in (r'"price"\s*:\s*\{[^}]{0,200}\}', r'"p"\s*:\s*"[\d.]+"',
                r'"jdPrice"\s*:\s*"?[\d.]+', r'"op"\s*:\s*"?[\d.]+'):
        for m in list(re.finditer(pat, text))[:3]:
            print("  ", m.group(0)[:180])

    print("\n===== detail-item 结构 =====")
    from lxml import html as LH
    doc = LH.fromstring(text)
    for node in doc.xpath("//*[contains(@class,'detail-item')]")[:8]:
        print("  ", node.get("class"), "|", " ".join(node.text_content().split())[:100])

    print("\n===== 参数/规格候选区块 =====")
    for cls in ("detail_sku_v1_main", "detail-item_label", "detail-item_value",
                "detail_row", "detail_transfer_row"):
        nodes = doc.xpath(f"//*[contains(@class,'{cls}')]")
        if nodes:
            print(f"  [{cls}] n={len(nodes)}")
            for n in nodes[:4]:
                print("     ", " ".join(n.text_content().split())[:110])

    print("\n===== 详情图（detail 区域 img）=====")
    imgs = doc.xpath("//*[contains(@class,'detail')]//img/@src | //*[contains(@class,'detail')]//img/@data-lazy-img")
    for u in list(dict.fromkeys(imgs))[:10]:
        print("  ", u[:120])

    print("\n===== 颜色/规格 JSON =====")
    for kw in ('"colorSize"', '"saleProp"', '"颜色"', '"规格"', '"尺寸"', '"版本"'):
        print(f"  {kw}: {text.count(kw)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
