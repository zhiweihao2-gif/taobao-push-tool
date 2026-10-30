"""M2 全流程演示：拿 M1 抓到的真实商品，走一遍映射 → 确认 → 发布。

这个脚本就是「M1 抓到数据之后能干什么」的可执行答案。

用法::

    python scripts/demo_m2.py 1073025553052
    python scripts/demo_m2.py 1073025553052 --auto-confirm   # 自动确认第一候选
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.storage import load_item  # noqa: E402
from src.normalize.category_match import CategoryResolver  # noqa: E402
from src.normalize.category_models import MappingStatus  # noqa: E402
from src.normalize.category_provider import CategoryTreeService, LocalJsonProvider  # noqa: E402
from src.normalize.mapping_tasks import MappingTaskStore  # noqa: E402
from src.normalize.property_resolver import resolve_properties  # noqa: E402
from src.normalize.sku_builder import PriceConfig, PriceEngine, SkuPlanner  # noqa: E402
from src.publish.pipeline import PublishPipeline, PublishRequest  # noqa: E402

TREE = ROOT / "data/mapping/category_tree.example.json"


def hr(title: str = "") -> None:
    print()
    if title:
        print(f"── {title} " + "─" * max(0, 58 - len(title)))
    else:
        print("─" * 62)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("item_id", nargs="?", default="1073025553052")
    ap.add_argument("--auto-confirm", action="store_true",
                    help="自动确认第一候选（演示用，真实场景要人工选）")
    args = ap.parse_args()

    item = load_item(args.item_id)
    if item is None:
        print(f"× 没找到商品 {args.item_id}，先跑 run_demo.py 采集")
        return 1

    # 用临时目录，不污染真实映射表
    tmp = Path(tempfile.mkdtemp(prefix="demo_m2_"))
    tree_service = CategoryTreeService([LocalJsonProvider(TREE)])
    store = MappingTaskStore(tmp / "app.db", tmp / "category_map.yaml")
    pipeline = PublishPipeline(tree_service=tree_service, task_store=store,
                               price_engine=PriceEngine(
                                   PriceConfig(mode="multiplier", multiplier="1.35",
                                               round_to="0.01", clamp=["9.9", "99999"])),
                               stock_mode="copy")   # 与下面 SKU 表格用同一策略

    hr("0. M1 抓到的原始数据")
    print(f"  商品 ID : {item.item_id}")
    print(f"  标题    : {item.title}")
    print(f"  平台    : {item.platform}")
    print(f"  源类目  : {item.category.cid or '(空 — 淘宝 pcdetail 不返回)'}")
    print(f"  价格    : {item.price.min} ~ {item.price.max}")
    print(f"  主图    : {len(item.main_images)} 张")
    print(f"  视频    : {len(item.videos)} 个")
    print(f"  详情图  : {len(item.detail_images)} 张")
    print(f"  销售属性: {[p.name for p in item.sale_props]}")
    print(f"  SKU     : {len(item.skus)} 条")
    print(f"  参数    : {[p.name for p in item.params][:6]}")

    # ---------------- 1. 类目匹配
    hr("1. 类目匹配（源类目为空，用标题+参数+销售属性找）")
    tree = tree_service.load("taobao")
    resolver = CategoryResolver(tree, store=store)
    mapping = resolver.resolve(item)
    print(f"  状态: {mapping.status.value}")
    print(f"  指纹: {mapping.fingerprint}")
    print(f"  候选:")
    for i, c in enumerate(mapping.candidates, 1):
        mark = " ← 最高分" if i == 1 else ""
        print(f"    {i}. {c.cid}  {c.path_text:<34} 分数 {c.score:.3f}{mark}")
        if c.reasons:
            print(f"       依据: {', '.join(c.reasons)}")

    if not mapping.candidates:
        print("  × 没有候选，后续无法继续")
        return 1

    # ---------------- 2. 确认
    hr("2. 人工确认（真实场景是你在网页上点选）")
    if mapping.status is MappingStatus.OK:
        print(f"  已有确认记录，直接复用: {mapping.target_cid}")
        target_cid = mapping.target_cid
    else:
        target_cid = mapping.candidates[0].cid
        task = store.submit_pending(mapping.fingerprint or "", item.item_id,
                                    item.platform, "taobao", mapping.candidates,
                                    mapping.detail or "")
        print(f"  挂起任务: {task.task_id}")
        print(f"  待确认候选: {[c.cid for c in mapping.candidates]}")
        if args.auto_confirm:
            ok, msg = store.confirm(task.task_id, target_cid)
            print(f"  自动确认 → {msg}")
            if not ok:
                return 1
        else:
            print(f"  （加 --auto-confirm 会自动选第一个）")
            print(f"  假设你选了: {target_cid}")

    node = tree.by_cid(str(target_cid))
    print(f"  目标类目: {node.path_text}（{len(node.props)} 个属性）")

    # ---------------- 3. 属性映射
    hr("3. 属性映射（源 pid/vid → 目标 pid/vid）")
    props = resolve_properties(item, node, src_platform=item.platform,
                               target_platform="taobao")
    for m in props.sale_props:
        mark = "✓" if m.target_pid else "×"
        print(f"  {mark} {m.src_name} → {m.target_name or '(未映射)'}")
        for v in m.values:
            vm = "✓" if v.target_vid else "×"
            print(f"      {vm} {v.src_name:<22} → {v.target_name or '(未命中)'}"
                  f"  [{v.matched_by}]")
    b = props.brand
    print(f"  品牌: {b.src_brand} → {b.brand_name or '(未命中)'}"
          f"  brand_id={b.brand_id}  [{b.matched_by}]")
    print(f"  最终 props 串: {props.props_string}")
    if props.required_missing:
        print(f"  ⚠ 缺必填: {props.required_missing}")

    # ---------------- 4. SKU 重建
    hr("4. SKU 重建 + 加价")
    planner = SkuPlanner(node, price_engine=pipeline.price_engine, stock_mode="copy")
    plan = planner.rebuild(item.skus, props.sale_props)
    print(f"  源 SKU {len(item.skus)} 条 → 重建 {len(plan.items)} 条"
          f"（跳过 {len(plan.skipped)}）")
    print(f"  {'props 串':<38}{'原价':>8}{'售价':>10}{'库存':>8}")
    for s in plan.items:
        src = next((x.price for x in item.skus if x.sku_id == s.src_sku_id), None)
        print(f"  {s.props:<38}{str(src):>8}{s.price:>10.2f}{s.quantity:>8}")
    for s in plan.skipped:
        print(f"  ⚠ 跳过 {s.get('src_sku_id')}: {s.get('reason')} — {s.get('detail')}")

    # ---------------- 5. dry_run
    hr("5. dry_run：生成上架载荷（不提交、不联网）")
    res = pipeline.publish(PublishRequest(
        item, channel="open_api", mode="dry_run",
        target_platform="taobao", target_cid=str(target_cid)))
    if res.ok:
        p = res.payload
        print(f"  ✓ 载荷生成成功")
        print(f"    cid        : {p['cid']}")
        print(f"    title      : {p['title']}")
        print(f"    props      : {p['props']}")
        print(f"    price      : {p['price']}")
        print(f"    num        : {p['num']}")
        print(f"    images     : {len(p['images'])} 张")
        print(f"    skus       : {len(p['skus'])} 条")
        print(f"    approve    : {p['approve_status']}")
    else:
        print(f"  × {res.error_code}: {res.error_msg}")
        for e in (res.raw_response.get("summary", {}).get("errors") or []):
            print(f"    - {e}")

    # ---------------- 6. draft
    hr("6. draft：落到本地草稿表（不提交、不联网）")
    res2 = pipeline.publish(PublishRequest(
        item, channel="open_api", mode="draft",
        target_platform="taobao", target_cid=str(target_cid)))
    if res2.ok:
        print(f"  ✓ 草稿已保存  draft_id = {res2.draft_id}")
        from src.core.storage import get_draft
        d = get_draft(res2.draft_id) if res2.draft_id else None
        if d:
            print(f"    可在 data/app.db 的 drafts 表查到")
    else:
        print(f"  × {res2.error_code}: {res2.error_msg}")

    # ---------------- 7. direct
    hr("7. direct：真实上架（M2 明确拒绝）")
    res3 = pipeline.publish(PublishRequest(
        item, channel="open_api", mode="direct",
        target_platform="taobao", target_cid=str(target_cid)))
    print(f"  {res3.error_code}: {res3.error_msg}")

    hr()
    print("  演示结束。以上全程零网络请求。")
    print(f"  临时映射表: {tmp}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
