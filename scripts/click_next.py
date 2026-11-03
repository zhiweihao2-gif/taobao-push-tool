"""在类目已选中的状态下，点「确认，下一步」，落到真实发布页并 dump。

前置：先用 find_category.py --keyword X 搜索并让平台选中类目。
本脚本不搜索，直接点确认 —— 用来观察发布页的真实结构（字段 / iframe / selector）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    BrowserSession,
    dump_form_fields,
    dump_page_summary,
    group_fields,
    human_pause,
    write_report,
)

OUT_DIR = ROOT / "data" / "publish_poc"

_BODY_JS = r"""
() => ({
  url: location.href,
  title: document.title,
  body_text: (document.body.innerText || '').slice(0, 8000),
  iframes: [...document.querySelectorAll('iframe')].map(f => (f.src || '').slice(0, 250)),
  errors: [...document.querySelectorAll('[class*="error"],[class*="Error"],[class*="err"]')]
    .map(e => (e.innerText || '').trim().slice(0, 200)).filter(Boolean).slice(0, 20),
})
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--tag", default="next")
    args = ap.parse_args()

    with BrowserSession(cdp_url=args.cdp) as s:
        st = s.login_state_detail()
        if not st["logged_in"]:
            print("[XX] 未登录")
            return 1

        page = s.page()
        print("当前页: " + page.url)

        # 确认页面上真有「确认，下一步」
        clicked = False
        for name in ("确认，下一步", "确认"):
            try:
                btn = page.get_by_role("button", name=name)
                if btn.count():
                    btn.first.click(timeout=10000)
                    clicked = True
                    print("[OK] 点了「%s」" % name)
                    break
            except Exception as exc:
                print("[..] role=button %s 失败: %s" % (name, exc))
        if not clicked:
            try:
                page.get_by_text("确认，下一步", exact=False).first.click(timeout=10000)
                clicked = True
                print("[OK] get_by_text 点了")
            except Exception as exc:
                print("[XX] 点不到: %s" % exc)
                return 1

        print("[..] 等发布页加载")
        page.wait_for_timeout(8000)
        human_pause(1.5, 2.5)
        page.wait_for_timeout(3000)

        print("\n" + "=" * 68)
        print("发布页落点")
        print("=" * 68)
        print("URL: " + page.url)

        body = page.evaluate(_BODY_JS)
        print("标题: " + str(body["title"]))
        print("\n-- 页面文字 (前 3000) --")
        print(body["body_text"][:3000])

        if body["errors"]:
            print("\n-- 错误元素 --")
            for e in body["errors"]:
                print("  " + e)

        if body["iframes"]:
            print("\n-- iframe --")
            for f in body["iframes"]:
                print("  " + f)

        # 表单字段
        fields = dump_form_fields(page)
        groups = group_fields(fields)
        print("\n" + "=" * 68)
        print("可填字段 (%d 个，可见 %d)" % (
            len(fields), sum(1 for f in fields if f.get("visible"))))
        print("=" * 68)
        for f in fields:
            if not f.get("visible"):
                continue
            print("  %-10s %-8s id=%-24r ph=%-30r label=%r" % (
                f.get("kind"), f.get("tag"), f.get("id"), f.get("placeholder"), f.get("label")))

        print("\n" + "=" * 68)
        print("按用途分组")
        print("=" * 68)
        for k, v in groups.items():
            print("  %-10s %d 个" % (k, len(v)))
            for f in v[:5]:
                print("       id=%r ph=%r label=%r" % (f.get("id"), f.get("placeholder"), f.get("label")))

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shot = OUT_DIR / ("after_next_" + args.tag + ".png")
        s.screenshot(str(shot))
        print("\n截图: " + str(shot))

        write_report(
            {
                "url": page.url,
                "summary": dump_page_summary(page),
                "body": body,
                "fields": fields,
                "groups": {k: v for k, v in groups.items()},
            },
            OUT_DIR / ("after_next_" + args.tag + ".json"),
        )
        print("报告: " + str(OUT_DIR / ("after_next_" + args.tag + ".json")))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
