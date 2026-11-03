"""从「类目为空」的发布页点回退链接，进类目选择页并 dump。

publish.htm 不带 catId -> 「类目为空或不存在」
publish.htm?catId=X   -> X 无效时「类目不存在或无效」
所以必须先拿到这个店铺真实可用的 catId。

本脚本走平台自己的回退入口，看它到底把用户带到哪个类目选择界面。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    PUBLISH_URL,
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
  document.querySelectorAll('a,button,li,div,span,label,dd,dt,option').forEach(el => {
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

  out.inputs = [...document.querySelectorAll('input,textarea')].map(el => ({
    type: el.type, id: el.id, name: el.name,
    placeholder: el.placeholder,
    cls: (el.className || '').toString().slice(0, 100),
    visible: el.getBoundingClientRect().width > 4,
    rect: (() => { const r = el.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })(),
  }));

  out.body_text = (document.body.innerText || '').slice(0, 5000);
  out.iframes = [...document.querySelectorAll('iframe')].map(f => (f.src || '').slice(0, 200));
  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--keyword", default=None, help="拿到搜索框后顺手搜一个词")
    args = ap.parse_args()

    with BrowserSession(cdp_url=args.cdp) as s:
        st = s.login_state_detail()
        print("登录态: logged_in=%s" % st["logged_in"])
        if not st["logged_in"]:
            print("[XX] 未登录")
            return 1

        print("\n[1] 打开 publish.htm（不带 catId）")
        s.goto(PUBLISH_URL, settle_ms=2500)
        page = s.page()
        page.wait_for_timeout(1500)
        print("    URL: " + s.current_url())

        print("\n[2] 点「点击这里返回重新选择其他类目」")
        clicked = False
        for sel in [
            'a:has-text("重新选择其他类目")',
            'a:has-text("重新选择")',
            'text=点击这里返回',
        ]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    clicked = True
                    print("    [OK] 用 " + sel + " 点击")
                    break
            except Exception:
                continue
        if not clicked:
            # 退化：按文本遍历
            try:
                loc = page.get_by_text("重新选择其他类目", exact=False).first
                loc.click(timeout=5000)
                clicked = True
                print("    [OK] 用 get_by_text 点击")
            except Exception as exc:
                print("    [XX] 点不到: %s" % exc)

        if clicked:
            page.wait_for_timeout(3500)
            human_pause(1.0, 2.0)

        print("\n[3] 落点")
        print("    URL: " + s.current_url())
        print("    标题: " + (page.title() or ""))

        if args.keyword:
            print("\n[4] 搜索关键词: " + args.keyword)
            typed = False
            for sel in [
                'input[placeholder*="搜索"]',
                'input[placeholder*="商品名称"]',
                'input[placeholder*="类目"]',
                'input[placeholder*="请输入"]',
                'input[type="text"]',
            ]:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        el.click()
                        page.keyboard.type(args.keyword, delay=100)
                        page.keyboard.press("Enter")
                        typed = True
                        print("    [OK] 用 " + sel)
                        break
                except Exception:
                    continue
            if not typed:
                print("    [XX] 没找到搜索框")
            page.wait_for_timeout(4000)

        data = page.evaluate(_DUMP_JS)

        print("\n" + "=" * 68)
        print("输入框 (%d 个)" % len(data["inputs"]))
        print("=" * 68)
        for i in data["inputs"]:
            mark = "V" if i["visible"] else " "
            print("  [%s] type=%-9s placeholder=%r rect=%s" % (
                mark, i["type"], i["placeholder"], i["rect"]))

        print("\n" + "=" * 68)
        print("可见文本 (%d 个)" % len(data["texts"]))
        print("=" * 68)
        for t in data["texts"][:150]:
            print("  " + t)

        print("\n" + "=" * 68)
        print("页面文字 (前 2500 字)")
        print("=" * 68)
        print(data["body_text"][:2500])

        if data["iframes"]:
            print("\niframe:")
            for f in data["iframes"]:
                print("  " + f)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shot = OUT_DIR / "pick_category.png"
        s.screenshot(str(shot))
        print("\n截图: " + str(shot))

        write_report(
            {
                "url": s.current_url(),
                "summary": dump_page_summary(page),
                "probe": data,
            },
            OUT_DIR / "pick_category.json",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
