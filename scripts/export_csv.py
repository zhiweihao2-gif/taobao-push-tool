"""导出商品数据为 CSV —— 给千牛 / 淘宝助理批量发布用。

用法：
    python scripts/export_csv.py                      # 全部商品 → data/导出_千牛.csv
    python scripts/export_csv.py --id 1073025553052   # 只导一个
    python scripts/export_csv.py --with-sku           # 附带 SKU 明细表（多一个文件）

设计要点
--------
1. **GBK 编码**：千牛/淘宝助理读的是 GBK，用 UTF-8 会乱码。这里写 gbk，
   遇到生僻字（emoji 之类）降级成 `?` 而不是崩掉。
2. **图片用 URL 不是本地路径**：千牛导入时是从网络拉图的，本地路径它读不到。
3. **一行一个商品**：SKU 明细单独一个文件，因为千牛的规格列格式每个类目不同，
   硬塞进主表会导入失败。

⚠ 这个格式是「通用版」，不是千牛官方模板。千牛每个类目的必填属性列都不一样，
   最稳的做法是你从千牛导一个空白模板（或导出一个现有商品），我按那个列名对齐。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path(__file__).resolve().parents[1]
ITEMS_DIR = ROOT / "data" / "items"
RAW_DIR = ROOT / "data" / "raw"
DEFAULT_OUT = ROOT / "data" / "导出_千牛.csv"

MIN_REAL_RAW_BYTES = 1024

MAIN_COLUMNS = [
    "宝贝标题",
    "宝贝类目ID",
    "宝贝类目",
    "一口价",
    "宝贝数量",
    "宝贝属性",
    "宝贝卖点",
    "宝贝主图",
    "宝贝详情图",
    "宝贝视频",
    "宝贝描述",
    "宝贝编码",
    "宝贝状态",
    "源链接",
]

SKU_COLUMNS = [
    "宝贝编码",
    "规格1",
    "规格2",
    "规格3",
    "价格",
    "数量",
    "规格编码",
]


def _is_test(item_id: str, platform: str) -> bool:
    raw = RAW_DIR / platform / f"{item_id}.json"
    if not raw.exists():
        return True
    return raw.stat().st_size < MIN_REAL_RAW_BYTES


def _props_str(item: dict) -> str:
    """把参数表拼成「名称:值;名称:值」—— 千牛的属性列就吃这个格式。"""
    parts = []
    for p in item.get("params") or []:
        n = (p.get("name") or "").strip()
        v = (p.get("value") or "").strip()
        if n and v:
            parts.append(f"{n}:{v}")
    return ";".join(parts)


def _urls(images: list) -> str:
    """图片 URL 用逗号分隔。千牛按顺序读，第一张是主图。"""
    out = []
    for img in images or []:
        u = img.get("url") if isinstance(img, dict) else str(img)
        if u:
            out.append(u)
    return ",".join(out)


def _sell_point(item: dict) -> str:
    """卖点：优先 subtitle，其次标题前 30 字。"""
    st = (item.get("subtitle") or "").strip()
    if st:
        return st[:150]
    return (item.get("title") or "")[:30]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--id", action="append", default=[],
                    help="只导出指定商品 ID，可重复")
    ap.add_argument("--with-sku", action="store_true",
                    help="额外生成 SKU 明细表")
    ap.add_argument("--keep-test", action="store_true")
    args = ap.parse_args()

    want = set(args.id)
    rows: list[dict] = []
    sku_rows: list[dict] = []
    skipped: list[str] = []

    for p in sorted(ITEMS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        item = data.get("item") if isinstance(data, dict) and "item" in data else data
        if not isinstance(item, dict):
            continue
        iid = str(item.get("item_id") or p.stem)
        plat = item.get("platform") or ""
        if want and iid not in want:
            continue
        if _is_test(iid, plat) and not args.keep_test:
            skipped.append(iid)
            continue

        price = item.get("price") or {}
        main_imgs = item.get("main_images") or []
        detail_imgs = item.get("detail_images") or []
        cat = item.get("category") or {}

        rows.append({
            "宝贝标题": item.get("title") or "",
            "宝贝类目ID": cat.get("cid") or "",
            "宝贝类目": " > ".join(cat.get("path") or []),
            "一口价": price.get("min") if price.get("min") is not None else "",
            "宝贝数量": item.get("stock_total") or "",
            "宝贝属性": _props_str(item),
            "宝贝卖点": _sell_point(item),
            "宝贝主图": _urls(main_imgs),
            "宝贝详情图": _urls(detail_imgs),
            "宝贝视频": item.get("video_url") or "",
            "宝贝描述": (item.get("detail_html") or "")[:5000],
            "宝贝编码": iid,
            "宝贝状态": "仓库中",
            "源链接": item.get("source_url") or "",
        })

        if args.with_sku:
            # 把 pid 翻成属性名，方便对照
            pid_name = {p.get("pid"): p.get("name")
                        for p in (item.get("sale_props") or [])}
            vid_name = {}
            for sp in item.get("sale_props") or []:
                for v in sp.get("values") or []:
                    vid_name[str(v.get("vid"))] = v.get("name")

            for s in item.get("skus") or []:
                attrs = s.get("attrs") or {}
                vals = []
                for pid, vid in attrs.items():
                    nm = pid_name.get(pid) or pid
                    vn = vid_name.get(str(vid)) or vid
                    vals.append(f"{nm}={vn}")
                sku_rows.append({
                    "宝贝编码": iid,
                    "规格1": vals[0] if len(vals) > 0 else "",
                    "规格2": vals[1] if len(vals) > 1 else "",
                    "规格3": vals[2] if len(vals) > 2 else "",
                    "价格": s.get("price") if s.get("price") is not None else "",
                    "数量": s.get("quantity") if s.get("quantity") is not None else "",
                    "规格编码": s.get("outer_id") or s.get("sku_id") or "",
                })

    def _write(path: Path, cols: list[str], data: list[dict]) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 千牛读 GBK；用 errors="replace" 保证生僻字不会崩
        with path.open("w", encoding="gbk", errors="replace", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(data)
        return len(data)

    out = Path(args.out)
    n = _write(out, MAIN_COLUMNS, rows)
    print(f"✓ 主表: {out}  ({n} 个商品)")

    if args.with_sku:
        sku_out = out.with_name(out.stem + "_SKU.csv")
        m = _write(sku_out, SKU_COLUMNS, sku_rows)
        print(f"✓ SKU 表: {sku_out}  ({m} 条规格)")

    if skipped:
        print(f"  跳过测试数据 {len(skipped)} 条: {', '.join(skipped)}")
    print()
    print("⚠ 这是通用格式。要让千牛直接导入，请从千牛导出一个空白模板")
    print("  （商品管理 → 发布宝贝 → 批量发布 → 下载模板），把列名发我，我按它对齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
