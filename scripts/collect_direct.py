# -*- coding: utf-8 -*-
"""直接采集一个淘宝商品并入库，跳过 run_demo 的登录态新鲜度检查。

用途：cookie 超过 24 小时（SessionHealth 报 STALE），但实际还能用时，
run_demo.py 会拒绝发请求。这个脚本直接调 collector，不做前置拦截，
把商品采下来写进 data/items/，供 publish_one.py 使用。

做的事和 run_demo 单条采集一致：
    pcdetail（详情/参数/SKU）-> getdesc v6.0（详情图文）-> Item -> save_item

用法：
    python scripts/collect_direct.py --id 957861219375
    python scripts/collect_direct.py --id 957861219375 --with-media
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.resolve import ResolveError, resolve_item_id  # noqa: E402
from src.collect.taobao import TaobaoCollector  # noqa: E402
from src.core.storage import save_item  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="商品 ID 或链接")
    ap.add_argument("--with-media", action="store_true", help="同时下载主图/详情图")
    ap.add_argument("--no-desc", action="store_true", help="跳过 getdesc v6.0")
    args = ap.parse_args()

    try:
        item_id = resolve_item_id(args.id)
    except ResolveError as e:
        print("[XX] 无法从输入里解析商品 ID: %s" % e)
        return 2
    print("[..] 商品 ID = %s" % item_id)

    # collect() 内部就是 fetch -> to_item，直接返回 Item
    try:
        item = TaobaoCollector().collect(item_id)
    except Exception as e:
        print("[XX] 采集失败 %s: %s" % (type(e).__name__, str(e)[:300]))
        return 1

    print("[OK] %s" % item.title[:70])
    print("     价格 %.2f~%.2f  主图 %d  SKU %d  参数 %d  视频 %d"
          % (item.price.min or 0, item.price.max or 0,
             len(item.main_images), len(item.skus),
             len(item.params), len(getattr(item, "videos", []) or [])))
    print("     类目 %s  (%s)" % (item.category.cid, "/".join(item.category.path) or "未知"))

    if not args.no_desc:
        try:
            before = len(item.detail_images)
            item = TaobaoCollector().attach_desc(item, use_render_fallback=False)
            print("[OK] getdesc v6.0 -> 详情图 %d -> %d 张"
                  % (before, len(item.detail_images)))
        except Exception as e:
            print("[!] getdesc 失败（不影响入库）: %s: %s"
                  % (type(e).__name__, str(e)[:200]))

    if args.with_media:
        try:
            from src.media.downloader import MediaPipeline
            stats = MediaPipeline(item).run(with_detail=bool(item.detail_images))
            print("[OK] 媒体 %s" % stats)
        except Exception as e:
            print("[!] 媒体下载失败: %s: %s" % (type(e).__name__, str(e)[:200]))

    save_item(item)
    print("[OK] 已写入 data/items/%s.json" % item_id)

    # 顺手把关键字段打出来，方便对照目标类目
    print("\n参数:")
    for p in item.params:
        print("   %-16s %s" % (p.name, str(p.value)[:50]))
    if item.skus:
        print("\nSKU:")
        for s in item.skus[:10]:
            print("   %-32s ¥%-7s 库存 %s"
                  % ("/".join(s.attrs.values()) or s.sku_id, s.price, s.quantity))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
