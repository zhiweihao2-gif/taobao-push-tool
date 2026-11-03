"""点「提交宝贝信息」并校验结果。

提交是真实动作：成功会跳到 /sell/v2/success.htm。
失败会弹「错误(N)」对话框或顶部 toast。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, SUCCESS_PATH  # noqa: E402

_DUMP = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { url: location.href, dialogs: [], toasts: [], buttons: [] };
  document.querySelectorAll('.next-dialog,.next-message,.next-toast,[class*="dialog"]').forEach(d => {
    const r = d.getBoundingClientRect();
    if (r.height < 30) return;
    const t = clean(d.innerText);
    if (!t) return;
    out.dialogs.push({ rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], text: t.slice(0, 600) });
  });
  document.querySelectorAll('button').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.height < 20) return;
    const t = clean(b.innerText);
    if (!t) return;
    out.buttons.push({ t: t.slice(0, 30), disabled: b.disabled, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)
        print("URL: " + page.url[:100])

        # 提交前状态
        pre = page.evaluate(_DUMP)
        print("\n提交前 dialogs: %d" % len(pre["dialogs"]))
        for d in pre["dialogs"]:
            print("  %s %r" % (d["rect"], d["text"][:200]))
        submit_btns = [b for b in pre["buttons"] if "提交" in b["t"]]
        print("提交按钮: %s" % submit_btns)

        s.screenshot(str(ROOT / "data" / "publish_poc" / "before_submit.png"), timeout_ms=15000)

        # 点提交
        print("\n=== 点「提交宝贝信息」 ===")
        clicked = False
        for sel in ("button:has-text('提交宝贝信息')", "text=提交宝贝信息"):
            try:
                page.locator(sel).first.click(timeout=8000)
                clicked = True
                print("  点了 %s" % sel)
                break
            except Exception as exc:
                print("  %s 失败: %s" % (sel, str(exc).split("\n")[0][:70]))
        if not clicked:
            # 坐标兜底
            if submit_btns:
                r = submit_btns[0]["rect"]
                page.mouse.click(r[0] + r[2] // 2, r[1] + r[3] // 2)
                clicked = True
                print("  坐标点击 %s" % submit_btns[0]["rect"])

        # 等结果
        print("\n等待结果...")
        for i in range(20):
            page.wait_for_timeout(1500)
            url = page.url
            if SUCCESS_PATH in url:
                print("  [OK] 跳到成功页: %s" % url[:120])
                break
            st = page.evaluate(_DUMP)
            if st["dialogs"]:
                print("  [!!] 弹窗 %d 个 @%ds" % (len(st["dialogs"]), (i + 1) * 1.5))
                for d in st["dialogs"]:
                    print("    %s %r" % (d["rect"], d["text"][:400]))
                break
        else:
            print("  [??] 20 轮未跳转，URL: %s" % page.url[:100])

        page.wait_for_timeout(1500)
        print("\n最终 URL: " + page.url[:140])
        print("是否成功页: %s" % (SUCCESS_PATH in page.url))
        body = page.evaluate("() => document.body.innerText")
        print("页面文本前 600: %r" % body[:600])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "after_submit.png"), timeout_ms=20000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
