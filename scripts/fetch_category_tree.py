"""直接调用淘宝类目接口，抓完整类目树。

用法:
    python scripts/fetch_category_tree.py            # 抓一级+二级
    python scripts/fetch_category_tree.py --query 键帽
    python scripts/fetch_category_tree.py --children 11
"""
import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import BrowserSession  # noqa: E402

BASE = "https://item.upload.taobao.com"
SEARCH_API = BASE + "/sell/ai/asyncOpt.htm"
TREE_API = BASE + "/router/asyncOpt.htm"

GEXT = '{"startTraceId":"2150459617936722098508858e0a6c","newImageUi":true}'
OUT_DIR = r"data/category_tree"


def build_url(kind, **kw):
    if kind == "search":
        q = urllib.parse.urlencode({
            "optType": "retrievalDataAsyncOpt",
            "jsonBody": json.dumps({"keyword": kw["keyword"]}, ensure_ascii=False),
            "globalExtendInfo": GEXT,
        })
        return SEARCH_API + "?" + q
    q = urllib.parse.urlencode({
        "optType": "categorySelectChildren",
        "fromSmart": "true",
        "globalExtendInfo": GEXT,
    })
    return TREE_API + "?" + q


def call(page, url):
    """在页面上下文里同源 fetch，自动带 cookie。"""
    js = """async (u) => {
        const r = await fetch(u, {method:'GET', credentials:'include',
                                  headers:{'Accept':'application/json, text/plain, */*'}});
        const t = await r.text();
        return {status: r.status, body: t};
    }"""
    return page.evaluate(js, url)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="", help="类目关键词搜索")
    ap.add_argument("--children", default="", help="展开某个类目 ID 的子类目")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        print("打开类目页建立会话…")
        try:
            p.goto(BASE + "/sell/ai/category.htm", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("  goto:", str(e)[:60])
        p.wait_for_timeout(2500)

        if args.query:
            url = build_url("search", keyword=args.query)
            print("\n搜索 %r" % args.query)
            r = call(p, url)
            print("  HTTP %s  %d 字节" % (r["status"], len(r["body"])))
            try:
                d = json.loads(r["body"])
                cats = (d.get("data") or {}).get("category") or []
                for c in cats:
                    print("  id=%-12s leaf=%-5s publish=%-5s  %s"
                          % (c.get("id"), c.get("leaf"), c.get("publish"),
                             " > ".join(c.get("path") or [])))
                    print("      idpath=%s spell=%s" % (c.get("idpath"), c.get("spell")))
            except Exception as e:
                print("  解析失败:", str(e)[:80], r["body"][:200])
            path = os.path.join(OUT_DIR, "search_%s.json" % args.query)
            with open(path, "w", encoding="utf-8") as f:
                f.write(r["body"])
            print("  已存 %s" % path)
            return

        url = build_url("tree")
        print("\n抓类目树")
        r = call(p, url)
        print("  HTTP %s  %d 字节" % (r["status"], len(r["body"])))
        try:
            d = json.loads(r["body"])
            data = d.get("data") or {}
            src = data.get("dataSource") or []
            total = sum(len(g.get("children") or []) for g in src)
            print("  一级分组 %d 个，二级类目 %d 个" % (len(src), total))
            for g in src[:3]:
                kids = g.get("children") or []
                print("  [%s] %s (%d 子项)" % (g.get("groupId"), g.get("groupName"), len(kids)))
                for k in kids[:4]:
                    print("      id=%-10s leaf=%-5s %s" % (k.get("id"), k.get("leaf"), k.get("name")))
        except Exception as e:
            print("  解析失败:", str(e)[:80])
        path = os.path.join(OUT_DIR, "tree_all.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(r["body"])
        print("  已存 %s" % path)


if __name__ == "__main__":
    main()
