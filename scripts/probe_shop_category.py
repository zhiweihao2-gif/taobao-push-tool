"""探测：这个店铺到底能发布哪些类目。

淘宝发布页带错 catId 会直接报「类目不存在或无效」，页面字段为 0。
本脚本打开类目选择入口，把页面上真实可点的类目/搜索框 dump 出来。

用法：
    python scripts/probe_shop_category.py              # 打开发布页（不带 cid）
    python scripts/probe_shop_category.py --entry      # 打开类目选择入口页
    python scripts/probe_shop_category.py --entry --keyword 交换机
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    CATEGORY_ENTRY,
    PUBLISH_URL,
    BrowserSession,
    dump_page_summary,
    human_pause,
    write_report,
)

OUT_DIR = ROOT / "data" / "publish_poc"

_CATEGORY_JS = r"""
() => {
  const out = {};

  const seen = new Set();
  const texts = [];
  document.querySelectorAll('a,button,li,div,span,label,dd,dt').forEach(el => {
    if (texts.length > 400) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') return;
    if (el.children.length > 2) return;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 40) return;
    if (seen.has(t)) return;
    seen.add(t);
    texts.push({ text: t, tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 80) });
  });
  out.texts = texts;

  out.inputs = [...document.querySelectorAll('input,textarea')].map(el => ({
    type: el.type, id: el.id, name: el.name,
    placeholder: el.placeholder,
    cls: (el.className || '').toString().slice(0, 80),
    visible: el.getBoundingClientRect().width > 4,
  }));

  out.links = [...document.querySelectorAll('a[href]')].slice(0, 100).map(a => ({
    text: (a.innerText || '').trim().slice(0, 40),
    href: a.href.slice(0, 200),
  }));

  out.body_text = (document.body.innerText || '').slice(0, 4000);

  const errEl = document.querySelector('[class*="error"],[class*="Error"]');
  out.error_text = errEl ? (errEl.innerText || '').slice(0, 300) : null;

  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", action="store_true", help="打开类目选择入口页")
    ap.add_argument("--keyword", default=None, help="在类目页搜索关键词")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    args = ap.parse_args()

    url = CATEGORY_ENTRY if args.entry else PUBLISH_URL
    print("=" * 68)
    print("探测类目页: " + url)
    print("=" * 68)

    with BrowserSession(cdp_url=args.cdp) as s:
        st = s.login_state_detail()
        print("登录态: logged_in=%s url=%s" % (st["logged_in"], st["url"]))
        if not st["logged_in"]:
            print("[XX] 未登录（命中登录页特征: %s），先在浏览器里扫码" % st["login_hint"])
            return 1

        s.goto(url, settle_ms=2500)
        print("当前页: " + s.current_url())

        human_pause(1.5, 2.5)
        page = s.page()
        page.wait_for_timeout(2000)

        if args.keyword:
            print("")
            print("[..] 搜索类目关键词: " + args.keyword)
            typed = False
            for sel in [
                'input[placeholder*="搜索"]',
                'input[placeholder*="类目"]',
                'input[placeholder*="请输入"]',
                '.next-input input',
                'input[type="text"]',
            ]:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        page.keyboard.type(args.keyword, delay=90)
                        page.keyboard.press("Enter")
                        typed = True
                        print("  [OK] 用 " + sel + " 输入并回车")
                        break
                except Exception:
                    continue
            if not typed:
                print("  [XX] 没找到可见的搜索框")
            page.wait_for_timeout(3000)

        data = page.evaluate(_CATEGORY_JS)

        print("")
        print("=" * 68)
        print("输入框")
        print("=" * 68)
        for i in data.get("inputs", []):
            mark = "V" if i.get("visible") else " "
            print("  [%s] type=%-10s placeholder=%r cls=%r" % (
                mark, i.get("type"), i.get("placeholder"), i.get("cls")))

        print("")
        print("=" * 68)
        print("可见文本块 (%d 个)" % len(data.get("texts", [])))
        print("=" * 68)
        for t in data.get("texts", [])[:120]:
            print("  <%s> %s" % (t["tag"], t["text"]))

        print("")
        print("=" * 68)
        print("页面主区域文字 (前 2000 字)")
        print("=" * 68)
        print((data.get("body_text") or "")[:2000])

        if data.get("error_text"):
            print("")
            print("[!!] 页面报错: " + data["error_text"])

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tag = "entry" if args.entry else "publish"
        shot = OUT_DIR / ("category_" + tag + ".png")
        s.screenshot(str(shot), full_page=False)
        print("")
        print("截图: " + str(shot))

        write_report(
            {
                "url": s.current_url(),
                "summary": dump_page_summary(page),
                "category_probe": data,
            },
            OUT_DIR / ("category_" + tag + ".json"),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
