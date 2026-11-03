"""点 ai/category.htm 上的「开启」，进真实的搜索发品界面并 dump。

引导页本身没有输入框，两个「开启」按钮分别是：
  1) 搜索发品（输入商品名称/类目关键词/条码）
  2) 以图发品（上传图片识别）

本脚本点第 1 个，看它把用户带到哪个 URL、露出什么表单。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    CATEGORY_ENTRY,
    BrowserSession,
    dump_page_summary,
    human_pause,
    write_report,
)

OUT_DIR = ROOT / "data" / "publish_poc"

_DUMP_JS = r"""
() => {
  const out = {};
  const seen = new Set();
  const texts = [];
  document.querySelectorAll('a,button,li,div,span,label,dd,dt').forEach(el => {
    if (texts.length > 500) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') return;
    if (el.children.length > 2) return;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 60) return;
    if (seen.has(t)) return;
    seen.add(t);
    texts.push(t);
  });
  out.texts = texts;

  out.inputs = [...document.querySelectorAll('input,textarea')].map(el => {
    const r = el.getBoundingClientRect();
    return {
      type: el.type, id: el.id, name: el.name,
      placeholder: el.placeholder,
      cls: (el.className || '').toString().slice(0, 100),
      visible: r.width > 4,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    };
  });

  out.buttons = [...document.querySelectorAll('button,a,[role="button"]')].map(el => {
    const r = el.getBoundingClientRect();
    return {
      text: (el.innerText || '').trim().slice(0, 30),
      cls: (el.className || '').toString().slice(0, 80),
      visible: r.width > 4 && r.height > 4,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    };
  }).filter(b => b.visible);

  out.body_text = (document.body.innerText || '').slice(0, 5000);
  out.iframes = [...document.querySelectorAll('iframe')].map(f => (f.src || '').slice(0, 200));
  out.url = location.href;
  return out;
}
"""


def _dump(page, label: str) -> dict:
    data = page.evaluate(_DUMP_JS)
    print("\n" + "=" * 68)
    print(label)
    print("=" * 68)
    print("URL: " + data["url"])
    print("\n-- 输入框 (%d) --" % len(data["inputs"]))
    for i in data["inputs"]:
        mark = "V" if i["visible"] else " "
        print("  [%s] type=%-9s placeholder=%r rect=%s" % (
            mark, i["type"], i["placeholder"], i["rect"]))
    print("\n-- 可见按钮 (%d) --" % len(data["buttons"]))
    for b in data["buttons"][:40]:
        print("  %r rect=%s" % (b["text"], b["rect"]))
    print("\n-- 可见文本 (%d) --" % len(data["texts"]))
    for t in data["texts"][:120]:
        print("  " + t)
    print("\n-- 页面文字 (前 2000) --")
    print(data["body_text"][:2000])
    if data["iframes"]:
        print("\n-- iframe --")
        for f in data["iframes"]:
            print("  " + f)
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--keyword", default=None, help="进搜索界面后输入的关键词")
    args = ap.parse_args()

    with BrowserSession(cdp_url=args.cdp) as s:
        st = s.login_state_detail()
        if not st["logged_in"]:
            print("[XX] 未登录")
            return 1

        print("[1] 打开引导页")
        s.goto(CATEGORY_ENTRY, settle_ms=2500)
        page = s.page()
        page.wait_for_timeout(1500)
        print("    URL: " + s.current_url())

        print("\n[2] 点第一个「开启」（搜索发品）")
        before = len(s._browser.contexts[0].pages)  # type: ignore[attr-defined]
        clicked = False
        try:
            btns = page.get_by_role("button", name="开启")
            n = btns.count()
            print("    找到 %d 个「开启」按钮" % n)
            if n:
                btns.first.click(timeout=8000)
                clicked = True
                print("    [OK] 点了第 1 个")
        except Exception as exc:
            print("    [XX] role=button 失败: %s" % exc)
        if not clicked:
            try:
                page.get_by_text("开启", exact=True).first.click(timeout=8000)
                clicked = True
                print("    [OK] 用 get_by_text 点了")
            except Exception as exc:
                print("    [XX] 还是失败: %s" % exc)

        page.wait_for_timeout(3500)
        human_pause(1.0, 2.0)

        # 可能开新标签页
        pages = s._browser.contexts[0].pages  # type: ignore[attr-defined]
        if len(pages) > before:
            page = pages[-1]
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2500)
            print("    [i] 打开了新标签页")

        data = _dump(page, "开启后的界面")

        if args.keyword:
            print("\n[3] 搜索关键词: " + args.keyword)
            typed = False
            for sel in [
                'input[placeholder*="商品名称"]',
                'input[placeholder*="搜索"]',
                'input[placeholder*="类目"]',
                'input[placeholder*="请输入"]',
                'input[type="text"]',
            ]:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        page.keyboard.type(args.keyword, delay=110)
                        page.keyboard.press("Enter")
                        typed = True
                        print("    [OK] 用 " + sel)
                        break
                except Exception:
                    continue
            if not typed:
                print("    [XX] 没找到搜索框")
            page.wait_for_timeout(5000)
            data = _dump(page, "搜索结果")

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shot = OUT_DIR / "open_search.png"
        s.screenshot(str(shot))
        print("\n截图: " + str(shot))

        write_report(
            {
                "url": page.url,
                "summary": dump_page_summary(page),
                "probe": data,
            },
            OUT_DIR / "open_search.json",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
