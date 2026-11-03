"""抓某个类目的子级，并递归下钻。

用法:
    python scripts/fetch_cat_children.py --cid 50008090
    python scripts/fetch_cat_children.py --cid 50008090 --recurse --out data/category_tree/3c.json
"""
import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import BrowserSession  # noqa: E402

BASE = "https://item.upload.taobao.com"
API = BASE + "/router/asyncOpt.htm"
GEXT = '{"startTraceId":"2150459617936722098508858e0a6c","newImageUi":true}'


def build(cid):
    return API + "?" + urllib.parse.urlencode({
        "optType": "categorySelectChildren",
        "fromSmart": "true",
        "catId": cid,
        "globalExtendInfo": GEXT,
    })


def call(page, url):
    js = """async (u) => {
        const r = await fetch(u, {method:'GET', credentials:'include',
                                  headers:{'Accept':'application/json, text/plain, */*'}});
        return await r.text();
    }"""
    return page.evaluate(js, url)


def parse(body):
    try:
        d = json.loads(body)
    except Exception:
        return []
    data = d.get("data") or {}
    if not isinstance(data, dict):
        return []
    out = []
    src = data.get("dataSource")
    if isinstance(src, list):
        for g in src:
            for k in (g.get("children") or []):
                out.append(k)
            if not g.get("children") and g.get("name"):
                out.append(g)
    for key in ("children", "childList", "list"):
        lst = data.get(key)
        if isinstance(lst, list):
            out.extend([x for x in lst if isinstance(x, dict)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", required=True)
    ap.add_argument("--recurse", action="store_true")
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        try:
            p.goto(BASE + "/sell/ai/category.htm", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        p.wait_for_timeout(2500)

        body = call(p, build(args.cid))
        print("cid=%s  HTTP body %d 字节" % (args.cid, len(body)))
        kids = parse(body)
        print("解析出 %d 个子项" % len(kids))
        for k in kids[:40]:
            print("  id=%-12s leaf=%-5s %-28s path=%s"
                  % (k.get("id"), k.get("leaf"), (k.get("name") or "")[:28],
                     " > ".join(k.get("path") or [])))
        if len(kids) > 40:
            print("  ... 还有 %d 个" % (len(kids) - 40))

        if not kids:
            print()
            print("原始前 600 字符:")
            print(body[:600])

        if args.out:
            os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(body)
            print("\n已存 %s" % args.out)

        if args.recurse and kids:
            print()
            print("递归下钻（深度 ≤ %d）…" % args.max_depth)
            tree = {"cid": args.cid, "children": []}

            def walk(node, depth):
                if depth > args.max_depth:
                    return
                for k in kids_of(node):
                    pass

            # 简单逐层展开
            seen = 0
            for k in kids:
                if k.get("leaf"):
                    continue
                seen += 1
                if seen > 15:
                    print("  (只展开前 15 个)")
                    break
                sub = parse(call(p, build(k.get("id"))))
                print("  [%s] %s -> %d 个子项"
                      % (k.get("id"), (k.get("name") or "")[:20], len(sub)))
                tree["children"].append({
                    "id": k.get("id"),
                    "name": k.get("name"),
                    "children": [{"id": x.get("id"), "name": x.get("name"), "leaf": x.get("leaf")}
                                 for x in sub],
                })
            if args.out:
                out2 = args.out.replace(".json", "_tree.json")
                with open(out2, "w", encoding="utf-8") as f:
                    json.dump(tree, f, ensure_ascii=False, indent=2)
                print("\n已存递归树 %s" % out2)


def kids_of(node):
    return node.get("children") or []


if __name__ == "__main__":
    main()
