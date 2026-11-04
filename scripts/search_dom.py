# -*- coding: utf-8 -*-
"""从淘宝搜索结果页的 DOM 里抓真实商品链接。

为什么不用 mtop 搜索：mtop.taobao.wsearch.appsearch 返回的 itemId 经实测
指向的商品全部 404（搜「机械键盘」10 个候选逐个 pcdetail 全是 noitem），
那些数字更像是广告单元 ID 或店铺内部 ID，不是可打开的 itemId。
DOM 里的 <a href> 是页面真正渲染出来的链接，最可靠。

用法:
    python scripts/search_dom.py --q 机械键盘
    python scripts/search_dom.py --q 机械键盘 --pick 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.publish.browser_pub import BrowserSession  # noqa: E402

SEARCH_URL = "https://s.taobao.com/search?q=%s"

_ID_RE = re.compile(r"[?&](?:id|itemId)=(\d{10,16})")
_ITEM_HOSTS = ("item.taobao.com", "detail.tmall.com", "item.tmall.com",
               "chaoshi.detail.tmall.com", "world.taobao.com")


def extract_links(page) -> list[dict]:
    """抓所有 a[href] 里的商品链接，返回 [{id, host, href, title}]。"""
    return page.evaluate("""(hosts) => {
        const out = [];
        const seen = {};
        document.querySelectorAll('a[href]').forEach(a => {
            const href = a.href || '';
            if (!href) return;
            for (const h of hosts) {
                if (href.indexOf(h) >= 0) {
                    const m = href.match(/[?&](?:id|itemId)=(\\d{10,16})/);
                    if (!m) return;
                    const id = m[1];
                    if (seen[id]) return;
                    seen[id] = 1;
                    // 找卡片里的标题：向上找 5 层，取最长的短文本
                    let title = (a.getAttribute('title') || '').trim();
                    if (!title) {
                        let n = a;
                        for (let i = 0; i < 5 && n; i++) {
                            const t = (n.innerText || '').trim().split('\\n')[0];
                            if (t.length > title.length && t.length < 120) title = t;
                            n = n.parentElement;
                        }
                    }
                    out.push({id: id, host: h, href: href, title: title.slice(0, 90)});
                    return;
                }
            }
        });
        return out;
    }""", list(_ITEM_HOSTS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", required=True)
    ap.add_argument("--pick", type=int, default=0,
                    help="只输出第 N 个（1 起）的 ID，方便管道使用")
    ap.add_argument("--save", default="")
    a = ap.parse_args()

    url = SEARCH_URL % __import__("urllib.parse", fromlist=["quote"]).quote(a.q)
    print("[..] 打开 %s" % url)

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("[!] goto: %s" % str(e).split("\n")[0][:100])
        p.wait_for_timeout(3000)

        cur = p.url or ""
        body_head = p.evaluate("() => document.body ? document.body.innerText.slice(0,300) : ''") or ""
        print("[..] 当前 %s" % cur[:100])
        print("[..] 页面头部 %r" % body_head[:140])

        if any(k in cur for k in ("login.", "havanaone", "punish", "x5secdata")) \
                or "_____tmd_____" in body_head:
            print("[XX] 被风控/未登录拦住，需要先扫码或等冷却")
            return 3

        # 滚动两轮，让懒加载出来的卡片渲染完
        for _ in range(2):
            p.mouse.wheel(0, 1400)
            p.wait_for_timeout(1200)

        items = extract_links(p)
        print("[OK] 抓到 %d 个商品链接" % len(items))
        for k, it in enumerate(items[:20], 1):
            print("  %2d. %-16s %-28s %s" % (k, it["id"], it["host"], it["title"][:42]))

        if not items:
            print("[!] 一个都没有。可能：搜索结果页是骨架/风控页，或 DOM 结构变了")
            p.screenshot(path="data/publish_poc/search_dom.png", full_page=False)
            print("[..] 截图 data/publish_poc/search_dom.png")
            return 1

        if a.save:
            os.makedirs(os.path.dirname(a.save) or ".", exist_ok=True)
            with open(a.save, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            print("[..] 已写入 %s" % a.save)

        if a.pick:
            if 1 <= a.pick <= len(items):
                print("\nPICK %s" % items[a.pick - 1]["id"])
                return 0
            print("[XX] --pick %d 超出范围" % a.pick)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
