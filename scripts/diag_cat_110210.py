# -*- coding: utf-8 -*-
"""诊断 catId=110210 为什么打不开发布页。

probe_cat.py 跑 110210 时拿到的两个字段是
    label="跳至内容"  pid="go"  value="搜索"
    label="跳至内容"  pid="q"   value="文墨天机"
这明显是某个搜索页的输入框，不是淘宝发布页。
本脚本打印真实 URL / 标题 / body 片段 / 所有标签页，找出页面到底去哪了。

用法：python scripts/diag_cat_110210.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import (  # noqa: E402
    BrowserSession, PUBLISH_URL, list_pages,
)


def main() -> int:
    print("=" * 60)
    print("当前所有标签页")
    print("=" * 60)
    for i, p in enumerate(list_pages(), 1):
        print("  %d. %-70s %s" % (i, p.get("url", "")[:70], p.get("title", "")[:30]))

    with BrowserSession() as sess:
        page = sess.page()
        print("\n复用标签页: %s" % page.url[:100])

        for cid in ("110210", "200670001"):
            url = "%s?catId=%s&fromAICategory=true" % (PUBLISH_URL, cid)
            print("\n" + "=" * 60)
            print("catId=%s -> %s" % (cid, url))
            print("=" * 60)
            page = sess.page()
            try:
                page.set_viewport_size({"width": 1440, "height": 1000})
            except Exception:
                pass
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print("  goto 异常: %s" % str(e).split("\n")[0][:160])
            page.wait_for_timeout(4000)

            print("  实际 URL : %s" % page.url[:160])
            try:
                print("  标题     : %s" % page.title()[:100])
            except Exception:
                pass
            body = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            print("  body 长度: %d" % len(body))
            print("  body 前 300:")
            for line in body[:300].split("\n"):
                if line.strip():
                    print("    | %s" % line.strip()[:110])
            for kw in ("类目不存在或无效", "类目为空或不存在",
                       "当前类目", "宝贝标题", "基础信息", "销售信息"):
                print("  含 %-12s : %s" % (kw, kw in body))
            # 输入框快照
            inputs = page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('input,textarea').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width < 20 || r.height < 10) return;
                    out.push({ph: el.placeholder || '', name: el.name || '',
                              val: (el.value || '').slice(0, 20),
                              y: Math.round(r.y), w: Math.round(r.width)});
                });
                return out.slice(0, 12);
            }""")
            print("  可见输入框 %d 个:" % len(inputs))
            for it in inputs:
                print("    ph=%-28r name=%-18r val=%-14r y=%d w=%d"
                      % (it["ph"][:28], it["name"][:18], it["val"][:14], it["y"], it["w"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
