"""在图片空间弹窗里选中刚上传的图，点「完成」，回到发布页。

上传已成功（144→145 张），现在要选中并确认。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_pause  # noqa: E402

_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { imgs: [], buttons: [], checked: 0 };
  document.querySelectorAll('img').forEach(i => {
    const r = i.getBoundingClientRect();
    if (r.width < 60) return;
    // 找它所在的可点击卡片
    let card = i.closest('[class*="item"],[class*="card"],[class*="pic"]') || i.parentElement;
    const cr = card ? card.getBoundingClientRect() : r;
    out.imgs.push({
      src: (i.src || '').slice(-50),
      cls: (card && card.className || '').toString().slice(0, 70),
      rect: [Math.round(cr.x), Math.round(cr.y), Math.round(cr.width), Math.round(cr.height)],
    });
  });
  document.querySelectorAll('button,[role=button]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 10) return;
    out.buttons.push({ text: clean(b.innerText).slice(0, 16),
                       rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  out.checked = document.querySelectorAll('input[type=checkbox]:checked').length;
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)

        fr = None
        for f in page.frames:
            if "sucai-selector" in f.url:
                fr = f
                break
        if fr is None:
            print("[XX] 图片空间 iframe 不在（可能已关闭）")
            return 1
        print("[1] iframe 在: " + fr.url[:90])

        info = fr.evaluate(_JS)
        print("[2] 图片 %d 张（前 6 个）:" % len(info["imgs"]))
        for x in info["imgs"][:6]:
            print("    %-46s %s" % (x["cls"][:44], x["rect"]))
        print("    按钮: %s" % [b["text"] for b in info["buttons"]])
        print("    已勾选 checkbox: %d" % info["checked"])

        # 第一张图（最新上传的通常排最前）
        if not info["imgs"]:
            print("[XX] iframe 里没有图")
            return 1
        first = info["imgs"][0]
        cx = first["rect"][0] + first["rect"][2] / 2
        cy = first["rect"][1] + first["rect"][3] / 2
        print("\n[3] 点第一张图 (%d,%d)" % (cx, cy))
        try:
            fr.mouse.click(cx, cy)
            page.wait_for_timeout(2000)
            human_pause(0.5, 1.0)
        except Exception as exc:
            print("    点击失败: %s" % exc)

        info2 = fr.evaluate(_JS)
        print("    勾选数: %d" % info2["checked"])

        print("\n[4] 点「完成」")
        done = False
        for txt in ("完成", "确定", "确认"):
            try:
                fr.get_by_text(txt, exact=True).first.click(timeout=4000)
                print("    点了「%s」" % txt)
                done = True
                break
            except Exception:
                continue
        if not done:
            print("    [..] 没找到完成按钮")

        page.wait_for_timeout(4000)
        human_pause(0.8, 1.5)

        print("\n[5] 弹窗是否关闭")
        still = [f for f in page.frames if "sucai-selector" in f.url]
        print("    iframe 数: %d" % len(still))

        # 主图槽位状态
        slots = page.evaluate(
            r"""
            () => {
              const empties = document.querySelectorAll('.image-empty').length;
              const items = document.querySelectorAll('.sell-component-material-item-view').length;
              const imgs = [...document.querySelectorAll('.sell-component-simply-images img')]
                .map(i => ({ src: (i.src||'').slice(-50), w: i.getBoundingClientRect().width }));
              return { empties, items, imgs };
            }
            """
        )
        print("[6] 主图槽: image-empty=%d, item-view=%d, 已放图=%d" % (
            slots["empties"], slots["items"], len(slots["imgs"])))
        for i in slots["imgs"]:
            print("    %s (w=%d)" % (i["src"], i["w"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "after_pick.png"))
        print("\n截图: data/publish_poc/after_pick.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
