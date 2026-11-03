"""用「源码导入」填宝贝详情（contenteditable 版）。

弹窗结构：'源码导入 源码 格式 字体 大小 导入取消'
编辑器是 contenteditable div，不是 textarea。
用 page.keyboard.insert_text() 直接灌 HTML 文本，再点「导入」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

ITEM_ID = "1073025553052"

_PROBE = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const eds = [];
  document.querySelectorAll('[contenteditable="true"],[contenteditable=""]').forEach(e => {
    const r = e.getBoundingClientRect();
    eds.push({
      tag: e.tagName,
      cls: clean(e.className).slice(0, 60),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      visible: r.width > 100 && r.height > 40,
      len: (e.innerText || '').length,
    });
  });
  return eds;
}
"""

_RESULT = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const imgs = document.querySelectorAll('.sell-component-detail img, [class*="detail"] img').length;
  const body = clean(document.body.innerText);
  const m = body.match(/错误\s*\(?\s*(\d+)\s*\)?/);
  return { imgs, err: m ? m[1] : null, hasDetail: body.includes('亲，您还没有在详情编辑器中编辑') };
}
"""


def build_html(item_id: str) -> str:
    p = ROOT / "data" / "items" / ("%s.json" % item_id)
    data = json.loads(p.read_text(encoding="utf-8"))
    imgs = data.get("detail_images") or []
    title = data.get("title") or ""
    print("商品: %s" % title[:40])
    print("详情图 %d 张" % len(imgs))

    parts = []
    for u in imgs:
        if isinstance(u, dict):
            u = u.get("url") or u.get("hosted_url") or ""
        if not u:
            continue
        u = str(u).replace("http://", "https://")
        parts.append('<p><img src="%s" style="max-width:100%%;"/></p>' % u)
    if not parts:
        parts.append("<p>%s</p>" % title)
    html = "<p><br/></p>".join(parts)
    print("HTML %d 字符" % len(html))
    return html


def main() -> int:
    html = build_html(ITEM_ID)

    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("\nURL: " + page.url[:70])

        # 如果弹窗已经开着就复用
        already = page.evaluate("() => document.body.innerText.includes('源码导入') && !!document.querySelector('[contenteditable=\"true\"]')")
        if not already:
            print("\n[1] 点「源码导入」")
            page.get_by_text("源码导入", exact=True).first.scroll_into_view_if_needed(timeout=6000)
            page.wait_for_timeout(600)
            page.get_by_text("源码导入", exact=True).first.click()
            page.wait_for_timeout(2500)
        else:
            print("\n[1] 弹窗已开着，复用")

        eds = page.evaluate(_PROBE)
        print("    contenteditable %d 个:" % len(eds))
        for e in eds:
            print("      vis=%-5s len=%-4d %-30s %s" % (e["visible"], e["len"], e["cls"], e["rect"]))

        vis = [e for e in eds if e["visible"]]
        if not vis:
            print("\n[XX] 没有可见编辑器")
            s.screenshot(str(ROOT / "data" / "publish_poc" / "source_dialog.png"))
            return 1

        # 取面积最大的那个（源码编辑器）
        target_box = max(vis, key=lambda e: e["rect"][2] * e["rect"][3])
        print("\n[2] 用编辑器 %s" % target_box["rect"])

        loc = page.locator('[contenteditable="true"]')
        idx = None
        for i in range(loc.count()):
            b = loc.nth(i).bounding_box()
            if b and abs(b["width"] - target_box["rect"][2]) < 3 and abs(b["height"] - target_box["rect"][3]) < 3:
                idx = i
                break
        if idx is None:
            idx = loc.count() - 1
        ed = loc.nth(idx)
        ed.click()
        page.wait_for_timeout(400)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.wait_for_timeout(300)

        print("    灌入 HTML")
        page.keyboard.insert_text(html)
        page.wait_for_timeout(1200)

        after = page.evaluate(_PROBE)
        for e in after:
            if e["visible"]:
                print("    写入后 len=%d" % e["len"])
                break

        print("\n[3] 点「导入」")
        hit = False
        for desc, fn in (
            ("role=button", lambda: page.get_by_role("button", name="导入").first.click(timeout=4000)),
            ("button:has-text", lambda: page.locator("button:has-text('导入')").first.click(timeout=4000)),
            ("text last force", lambda: page.get_by_text("导入", exact=True).last.click(force=True, timeout=4000)),
            ("text first force", lambda: page.get_by_text("导入", exact=True).first.click(force=True, timeout=4000)),
        ):
            try:
                fn()
                print("    [OK] 用 %s 点到了" % desc)
                hit = True
                break
            except Exception as exc:
                print("    [--] %s: %s" % (desc, str(exc).split("\n")[0][:70]))
        if not hit:
            s.screenshot(str(ROOT / "data" / "publish_poc" / "source_filled.png"))
            print("    [XX] 都没点到，截图看状态")
            return 1

        page.wait_for_timeout(3500)
        res = page.evaluate(_RESULT)
        print("\n[4] 结果")
        print("    详情图元素: %d" % res["imgs"])
        print("    错误计数: %s" % res["err"])
        print("    还显示「未编辑」: %s" % res["hasDetail"])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "detail_done.png"))
        print("\n截图: data/publish_poc/detail_done.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
