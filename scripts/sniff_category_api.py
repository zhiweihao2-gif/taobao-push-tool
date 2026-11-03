"""监听淘宝类目选择页的 XHR，抓类目树接口。

用法:
    python scripts/sniff_category_api.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import BrowserSession  # noqa: E402

ENTRY = "https://item.upload.taobao.com/sell/ai/category.htm"
OUT = r"data/publish_poc/category_api.json"

INTEREST = ("cate", "category", "cat_", "catid", "tree", "props", "prop", "ai")


def main():
    hits = []
    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})

        def on_resp(resp):
            url = resp.url
            low = url.lower()
            if not any(k in low for k in INTEREST):
                return
            if any(x in low for x in (".js", ".css", ".png", ".jpg", ".webp", ".ico", ".woff")):
                return
            item = {"url": url[:300], "status": resp.status, "size": None, "head": ""}
            try:
                body = resp.text()
                item["size"] = len(body)
                item["head"] = body[:1200]
            except Exception as e:
                item["head"] = "[!] %s" % str(e)[:80]
            hits.append(item)

        p.on("response", on_resp)
        print("打开 %s" % ENTRY)
        try:
            p.goto(ENTRY, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("goto:", str(e)[:80])
        p.wait_for_timeout(4000)

        # 点第一个「开启」
        try:
            p.get_by_text("开启", exact=False).first.click(force=True, timeout=5000)
            print("[OK] 已点开启")
            p.wait_for_timeout(4000)
        except Exception as e:
            print("[!] 点开启失败:", str(e).split("\n")[0][:80])

        # 输入关键词并搜索，触发类目推荐接口
        try:
            box = p.get_by_placeholder("可输入产品名称、类目关键词、条码信息").first
            box.click()
            p.keyboard.type("键帽", delay=60)
            p.get_by_text("搜索", exact=True).first.click(force=True, timeout=5000)
            print("[OK] 已搜索")
            p.wait_for_timeout(5000)
        except Exception as e:
            print("[!] 搜索失败:", str(e).split("\n")[0][:80])

        # 展开类目树，触发树接口
        try:
            p.get_by_text("选择类目", exact=True).first.click(force=True, timeout=5000)
            print("[OK] 已展开类目树")
            p.wait_for_timeout(4000)
        except Exception as e:
            print("[!] 展开失败:", str(e).split("\n")[0][:80])

        p.wait_for_timeout(2000)
        p.remove_listener("response", on_resp)

    print()
    print("=" * 72)
    print("命中 %d 个接口" % len(hits))
    print("=" * 72)
    for h in hits:
        print()
        print("[%s] %d 字节" % (h["status"], h["size"] or 0))
        print("  %s" % h["url"])
        print("  %s" % h["head"][:400].replace("\n", " "))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(hits, f, ensure_ascii=False, indent=2)
    print()
    print("已写入 %s" % OUT)


if __name__ == "__main__":
    main()
