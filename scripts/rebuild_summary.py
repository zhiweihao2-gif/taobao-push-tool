"""重新生成 data/商品汇总.csv —— 扫描 data/items/*.json 重建汇总表。

用法：
    python scripts/rebuild_summary.py                  # 只重建 CSV
    python scripts/rebuild_summary.py --reparse        # 先从 raw 重新解析，再重建
    python scripts/rebuild_summary.py --keep-test      # 保留测试数据

设计要点：
- 以 data/items/*.json 为准（结构化真相），不信旧 CSV
- 测试数据识别：raw 原始文件 < 1KB 的视为假数据（真采集的 raw 至少 20KB）
- --reparse 会用当前解析器重跑 raw，修掉旧版本解析器的 bug（比如单规格价格丢失）
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
DEFAULT_OUT = ROOT / "data" / "商品汇总.csv"

MIN_REAL_RAW_BYTES = 1024

COLUMNS = [
    "商品ID", "平台", "标题", "价格下限", "价格上限",
    "主图数", "详情图数", "视频", "销售属性", "SKU数", "参数数",
    "品牌", "店铺", "采集时间", "缺什么", "数据文件",
]


def _brand_of(item: dict) -> str:
    """品牌：优先 extra.brand，其次参数表里找「品牌」。"""
    b = (item.get("extra") or {}).get("brand")
    if b:
        return str(b)
    for p in item.get("params") or []:
        if (p.get("name") or "").strip() in ("品牌", "Brand", "brand"):
            return str(p.get("value") or "")
    return ""


def _missing(item: dict) -> list[str]:
    miss: list[str] = []
    price = item.get("price") or {}
    if price.get("min") is None and price.get("max") is None:
        miss.append("price")
    if not item.get("sale_props"):
        miss.append("sale_props")
    if not (item.get("main_images") or []):
        miss.append("main_images")
    if not (item.get("detail_images") or []):
        miss.append("detail_images")
    if not item.get("skus"):
        miss.append("skus")
    if not item.get("params"):
        miss.append("params")
    if not (item.get("category") or {}).get("cid"):
        miss.append("category")
    if not _brand_of(item):
        miss.append("brand")
    return miss


def _row(item: dict, path: Path) -> dict:
    price = item.get("price") or {}
    seller = item.get("seller") or {}
    props = item.get("sale_props") or []
    return {
        "商品ID": item.get("item_id") or "",
        "平台": item.get("platform") or "",
        "标题": (item.get("title") or "").replace("\n", " ")[:80],
        "价格下限": price.get("min") if price.get("min") is not None else "",
        "价格上限": price.get("max") if price.get("max") is not None else "",
        "主图数": len(item.get("main_images") or []),
        "详情图数": len(item.get("detail_images") or []),
        "视频": "有" if item.get("video_url") else "",
        "销售属性": " / ".join(p.get("name", "") for p in props),
        "SKU数": len(item.get("skus") or []),
        "参数数": len(item.get("params") or []),
        "品牌": _brand_of(item),
        "店铺": seller.get("shop_name") or "",
        "采集时间": (item.get("collected_at") or "")[:19],
        "缺什么": "、".join(_missing(item)) or "完整",
        "数据文件": str(path),
    }


def _is_test_data(item_id: str, platform: str) -> bool:
    """测试数据判定：raw 原始文件不存在或过小。"""
    raw = RAW_DIR / platform / f"{item_id}.json"
    if not raw.exists():
        return True
    return raw.stat().st_size < MIN_REAL_RAW_BYTES


def _reparse_one(item_id: str, platform: str) -> dict | None:
    """用当前解析器重跑 raw，返回新的 item dict。"""
    raw_path = RAW_DIR / platform / f"{item_id}.json"
    if not raw_path.exists():
        return None
    d = json.loads(raw_path.read_text(encoding="utf-8"))
    payload = d.get("payload")
    if not payload:
        return None

    from src.core.models import RawItem

    raw = RawItem(
        platform=platform,
        item_id=item_id,
        payload=payload,
        endpoint=d.get("endpoint"),
        fetched_at=d.get("fetched_at"),
    )

    class _FakeStatus:
        """to_item 只需要 status.as_dict()，重新解析时用占位。"""
        def as_dict(self):
            return {"ok": True, "reason": "reparse"}

    try:
        if platform == "taobao":
            from src.collect.taobao import TaobaoCollector
            col = TaobaoCollector.__new__(TaobaoCollector)
            col.platform = "taobao"
            col.status = _FakeStatus()
        elif platform == "jd":
            from src.collect.jd import JDCollector
            col = JDCollector.__new__(JDCollector)
            col.platform = "jd"
            col.status = _FakeStatus()
        elif platform == "alibaba":
            from src.collect.alibaba import Alibaba1688Collector
            col = Alibaba1688Collector.__new__(Alibaba1688Collector)
            col.platform = "alibaba"
            col.status = _FakeStatus()
        else:
            return None
        item = col.to_item(raw)
    except Exception as e:
        print(f"  ⚠ {item_id} 重新解析失败: {type(e).__name__}: {e}")
        return None

    # Item 是 Pydantic 模型，用 model_dump；dataclass 用 asdict 兜底
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json")
    try:
        from dataclasses import asdict
        return asdict(item)
    except Exception as e:
        print(f"  ⚠ {item_id} 结果无法序列化: {type(e).__name__}: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--keep-test", action="store_true")
    ap.add_argument("--reparse", action="store_true",
                    help="先从 data/raw 用当前解析器重新解析（修旧数据）")
    args = ap.parse_args()

    if not ITEMS_DIR.exists():
        print(f"✗ 目录不存在: {ITEMS_DIR}")
        return 1

    if args.reparse:
        print("=== 重新解析 raw ===")
        for p in sorted(ITEMS_DIR.glob("*.json")):
            try:
                old = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            iid = str(old.get("item_id") or p.stem)
            plat = old.get("platform") or ""
            if _is_test_data(iid, plat) and not args.keep_test:
                continue
            new = _reparse_one(iid, plat)
            if new:
                # 详情图是 Playwright 渲染后才有的，raw 里没有 —— 重新解析时保留旧的，
                # 否则 --reparse 会把渲染成果冲掉。
                if not new.get("detail_images") and old.get("detail_images"):
                    new["detail_images"] = old["detail_images"]
                    new.setdefault("extra", {})["detail_images_from"] = "preserved"
                # 旧数据也没有，就去捡渲染阶段的落盘结果
                if not new.get("detail_images"):
                    render_path = RAW_DIR / plat / f"{iid}_render.json"
                    if render_path.exists():
                        try:
                            rd = json.loads(render_path.read_text(encoding="utf-8"))
                            urls = rd.get("detail_images") or []
                            if urls:
                                new["detail_images"] = [
                                    {"url": u, "role": "detail"} for u in urls
                                ]
                                new.setdefault("extra", {})[
                                    "detail_images_from"] = "render.json"
                                print(f"    ↳ 从 {render_path.name} 补回 "
                                      f"{len(urls)} 张详情图")
                        except Exception as e:
                            print(f"    ⚠ 读 {render_path.name} 失败: {e}")
                if not new.get("media_dir") and old.get("media_dir"):
                    new["media_dir"] = old["media_dir"]
                p.write_text(json.dumps(new, ensure_ascii=False, indent=2),
                             encoding="utf-8")
                old_p = (old.get("price") or {}).get("min")
                new_p = (new.get("price") or {}).get("min")
                flag = "  ← 价格修复" if old_p != new_p else ""
                print(f"  ✓ {iid:15} {plat:8} 价格 {old_p} → {new_p}{flag}")
        print()

    rows: list[dict] = []
    skipped: list[str] = []
    for p in sorted(ITEMS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ⚠ 跳过 {p.name}: {e}")
            continue
        item = data.get("item") if isinstance(data, dict) and "item" in data else data
        if not isinstance(item, dict):
            continue
        iid = str(item.get("item_id") or p.stem)
        plat = item.get("platform") or ""
        if _is_test_data(iid, plat) and not args.keep_test:
            skipped.append(f"{iid}({plat})")
            continue
        item.setdefault("item_id", iid)
        rows.append(_row(item, p))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig：Excel 直接识别中文，不乱码
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    print(f"✓ 已生成 {out}")
    print(f"  商品 {len(rows)} 条"
          + (f"，跳过测试残留 {len(skipped)} 条（{', '.join(skipped)}）" if skipped else ""))
    print()
    for r in rows:
        price = (f"{r['价格下限']}~{r['价格上限']}"
                 if r["价格下限"] != "" else "无价格")
        print(f"  [{r['平台']:7}] {r['商品ID']:15} {price:14} "
              f"主图{r['主图数']} 详情{r['详情图数']} SKU{r['SKU数']}  "
              f"{r['缺什么']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
