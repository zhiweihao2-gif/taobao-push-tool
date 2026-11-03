"""建类目树缓存：一级全量 + 二级全量（可选下钻三级）。

用法:
    python scripts/build_category_cache.py              # 一级+二级
    python scripts/build_category_cache.py --depth 3    # 再下钻一级
    python scripts/build_category_cache.py --workers 6
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.category_api import CategoryApi, CategoryApiError  # noqa: E402

OUT = r"data/category_tree/all.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    t0 = time.time()
    with CategoryApi(backend="http") as api:
        print("抓一级…")
        tops = api.children(None)
        print("  一级 %d 个" % len(tops))

        nodes = {}
        for c in tops:
            nodes[str(c["id"])] = {**c, "parent": ""}
        print("  用时 %.1fs" % (time.time() - t0))

        frontier = [c for c in tops if not c["leaf"]]
        for depth in range(2, args.depth + 1):
            print("下钻第 %d 层（%d 个待展开）…" % (depth, len(frontier)))
            nxt = []
            t1 = time.time()
            done = 0

            def work(c):
                try:
                    return c, api.children(c["id"])
                except CategoryApiError:
                    return c, []

            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = [ex.submit(work, c) for c in frontier]
                for fut in as_completed(futs):
                    parent, kids = fut.result()
                    done += 1
                    if done % 20 == 0:
                        print("    %d/%d" % (done, len(frontier)))
                    for k in kids:
                        key = str(k["id"])
                        if key in nodes:
                            continue
                        nodes[key] = {**k, "parent": str(parent["id"])}
                        if not k["leaf"]:
                            nxt.append(k)
            print("  第 %d 层完成：累计 %d 个节点，用时 %.1fs"
                  % (depth, len(nodes), time.time() - t1))
            frontier = nxt
            if not frontier:
                break

    ring = [v for v in nodes.values() if v["leaf"]]
    print()
    print("=" * 60)
    print("总节点 %d，其中末级 %d" % (len(nodes), len(ring)))
    print("总用时 %.1fs" % (time.time() - t0))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    data = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "depth": args.depth,
        "count": len(nodes),
        "leaf_count": len(ring),
        "nodes": nodes,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("已写入 %s（%d 字节）" % (args.out, os.path.getsize(args.out)))


if __name__ == "__main__":
    main()
