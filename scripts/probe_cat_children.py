"""探测类目子级接口的参数形式。

用法:
    python scripts/probe_cat_children.py
"""
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import BrowserSession  # noqa: E402

BASE = "https://item.upload.taobao.com"
API = BASE + "/router/asyncOpt.htm"
GEXT = '{"startTraceId":"2150459617936722098508858e0a6c","newImageUi":true}'

# 已知二级类目：50008090=3C数码配件，50018222=DIY电脑
TARGET = "11"  # rootCategory 手机数码? 试几个


def variants(cid):
    out = []
    for key in ("catId", "id", "parentId", "parentCatId", "categoryId", "cat"):
        out.append((key, urllib.parse.urlencode({
            "optType": "categorySelectChildren",
            "fromSmart": "true",
            key: cid,
            "globalExtendInfo": GEXT,
        })))
    return out


def call(page, url):
    js = """async (u) => {
        const r = await fetch(u, {method:'GET', credentials:'include',
                                  headers:{'Accept':'application/json, text/plain, */*'}});
        const t = await r.text();
        return {status: r.status, body: t};
    }"""
    return page.evaluate(js, url)


def summarize(body):
    try:
        d = json.loads(body)
    except Exception:
        return "非 JSON: %s" % body[:100]
    data = d.get("data") or {}
    if not isinstance(data, dict):
        return "data 非 dict: %s" % str(data)[:80]
    src = data.get("dataSource")
    if src is not None:
        n = sum(len(g.get("children") or []) for g in src) if isinstance(src, list) else -1
        return "dataSource %d 组 / %d 子项" % (len(src), n)
    kids = data.get("children") or data.get("childList") or []
    if kids:
        return "children %d 个: %s" % (len(kids), [k.get("name") for k in kids[:5]])
    return "键: %s" % list(data.keys())[:10]


def main():
    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        try:
            p.goto(BASE + "/sell/ai/category.htm", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("goto:", str(e)[:60])
        p.wait_for_timeout(2500)

        for cid in (TARGET, "50008090"):
            print()
            print("=" * 70)
            print("试 cid=%s" % cid)
            print("=" * 70)
            for key, qs in variants(cid):
                url = API + "?" + qs
                try:
                    r = call(p, url)
                except Exception as e:
                    print("  %-14s [!] %s" % (key, str(e).split("\n")[0][:70]))
                    continue
                mark = ""
                if r["status"] == 200 and len(r["body"]) != 25162:
                    mark = "  <== 长度变了！"
                print("  %-14s HTTP %s  %6d 字节  %s%s"
                      % (key, r["status"], len(r["body"]), summarize(r["body"]), mark))


if __name__ == "__main__":
    main()
