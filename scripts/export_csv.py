"""把已采集的商品导出成 CSV / Excel，方便直接看和筛选。

用法::

    python scripts/export_csv.py
    python scripts/export_csv.py --out 我的商品.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_settings  # noqa: E402

HEADERS = [
    "商品ID", "平台", "标题", "价格下限", "价格上限",
    "主图数", "详情图数", "销售属性", "SKU数", "参数数",
    "品牌", "店铺", "采集时间", "缺什么", "数据文件",
]


def load_all() -> list[dict]:
    s = get_settings()
    rows: list[dict] = []
    if not s.items_dir.exists():
        return rows
    for p in sorted(s.items_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not d.get("title"):
            continue
        # 排除采集失败时误存的错误页标题
        if any(m in d["title"] for m in ("HTTP Status", "Bad Request", "Not Found",
                                         "京东(JD.COM)", "安全验证")):
            continue
        caps = (d.get("extra") or {}).get("capabilities") or {}
        missing = [k for k, v in caps.items() if v in (False, 0, "missing", "need_login")]
        params = {p["name"]: p["value"] for p in d.get("params") or []}
        rows.append({
            "商品ID": d.get("item_id", ""),
            "平台": d.get("platform", ""),
            "标题": d.get("title", ""),
            "价格下限": (d.get("price") or {}).get("min") or "",
            "价格上限": (d.get("price") or {}).get("max") or "",
            "主图数": len(d.get("main_images") or []),
            "详情图数": len(d.get("detail_images") or []),
            "销售属性": " / ".join(p["name"] for p in d.get("sale_props") or []),
            "SKU数": len(d.get("skus") or []),
            "参数数": len(d.get("params") or []),
            "品牌": params.get("品牌", ""),
            "店铺": (d.get("seller") or {}).get("shop_name", ""),
            "采集时间": d.get("collected_at", ""),
            "缺什么": "、".join(missing) if missing else "无",
            "数据文件": str(p),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/商品汇总.csv")
    args = ap.parse_args()

    rows = load_all()
    if not rows:
        print("还没有采集过任何商品。先双击 2-采集一个商品.bat")
        return 1

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    # utf-8-sig 让 Excel 直接打开不乱码
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(rows)

    print(f"已导出 {len(rows)} 个商品 → {out}")
    print()
    print(f"{'商品ID':<16}{'价格':<14}{'SKU':<5}{'主图':<5}{'标题'}")
    print("-" * 90)
    for r in rows:
        price = f"{r['价格下限']}~{r['价格上限']}"
        print(f"{r['商品ID']:<16}{price:<14}{r['SKU数']:<5}{r['主图数']:<5}{r['标题'][:40]}")
    print()
    print(f"用 Excel 打开：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
