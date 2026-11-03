"""创建 SKU 规格（坐标点「+」版）。

上一版失败：`+` 按钮是 <button> 无文本 [948,269,32,32]，
filter(has_text="+") 匹配不到 → 改用 page.mouse.click 点它的中心。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

COLORS = ["黑色", "白色", "透明"]

_PANEL = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  let panel = null;
  document.querySelectorAll('div').forEach(el => {
    if (panel) return;
    const t = clean(el.innerText);
    if (t.includes('选择展示模式') && t.includes('确认创建') && el.children.length < 60) panel = el;
  });
  if (!panel) return null;
  const r = panel.getBoundingClientRect();
  const inputs = [...panel.querySelectorAll('input')].map(i => {
    const ir = i.getBoundingClientRect();
    return { ph: i.placeholder || '', val: (i.value || '').slice(0, 16), rect: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)] };
  });
  // 找颜色输入框右侧那个「+」：无文本 button
  const plus = [...panel.querySelectorAll('button')]
    .filter(b => !clean(b.innerText))
    .map(b => { const br = b.getBoundingClientRect(); return { rect: [Math.round(br.x), Math.round(br.y), Math.round(br.width), Math.round(br.height)] }; })
    .filter(b => b.rect[2] > 20 && b.rect[2] < 60);
  return { rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], inputs, plus, text: clean(panel.innerText).slice(0, 300) };
}
"""

_STATE = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body.innerText);
  return {
    open: body.includes('选择展示模式'),
    skuHint: (() => { const i = body.indexOf('销售规格'); return i >= 0 ? body.slice(i, i + 120) : null; })(),
    hasTable: /批量设置|一口价|库存/.test(body) && /SKU/.test(body),
  };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)
        print("URL: " + page.url[:70])

        # 1) 确保抽屉开着
        if not page.evaluate("() => document.body.innerText.includes('选择展示模式')"):
            print("\n[1] 打开 SKU 抽屉")
            btn = page.locator("button").filter(has_text="创建规格").first
            btn.scroll_into_view_if_needed(timeout=6000)
            page.wait_for_timeout(500)
            try:
                btn.click(timeout=5000)
            except Exception:
                btn.click(force=True, timeout=5000)
            page.wait_for_timeout(2500)
        else:
            print("\n[1] 抽屉已开着")

        panel = page.evaluate(_PANEL)
        if not panel:
            print("[XX] 抽屉没开")
            return 1
        print("面板输入框:")
        for i in panel["inputs"]:
            print("  ph=%-14r val=%-12r %s" % (i["ph"], i["val"], i["rect"]))
        print("无文本按钮(可能是+):")
        for b in panel["plus"]:
            print("  %s" % b["rect"])

        # 找颜色输入框
        cin = None
        for i in panel["inputs"]:
            if "主色" in i["ph"]:
                cin = i
                break
        if cin is None:
            print("[XX] 找不到主色输入框")
            return 1

        # 「+」应该在颜色输入框同一行、右侧
        plus_btn = None
        for b in panel["plus"]:
            if abs(b["rect"][1] - cin["rect"][1]) < 40 and b["rect"][0] > cin["rect"][0]:
                plus_btn = b
                break
        if plus_btn is None and panel["plus"]:
            plus_btn = panel["plus"][0]
        if plus_btn is None:
            print("[XX] 找不到 + 按钮")
            return 1

        px = plus_btn["rect"][0] + plus_btn["rect"][2] // 2
        py = plus_btn["rect"][1] + plus_btn["rect"][3] // 2
        print("\n[2] + 按钮中心 (%d, %d)" % (px, py))

        sel = 'input[placeholder="%s"]' % cin["ph"]
        loc = page.locator(sel).first

        for c in COLORS:
            t0 = time.time()
            loc.click(timeout=4000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(c, delay=35)
            page.wait_for_timeout(350)
            page.mouse.click(px, py)
            page.wait_for_timeout(900)
            now = page.evaluate(_PANEL)
            vals = [i["val"] for i in now["inputs"]] if now else []
            print("  + %s -> 输入框值 %s  %.1fs" % (c, vals, time.time() - t0))

        page.wait_for_timeout(600)
        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_colors.png"), timeout_ms=15000)

        # 3) 确认创建
        print("\n[3] 确认创建")
        try:
            page.locator("button:has-text('确认创建')").first.click(timeout=6000)
            print("    [OK]")
        except Exception as exc:
            print("    [XX] %s" % str(exc).split("\n")[0][:80])
            return 1

        page.wait_for_timeout(4000)
        st = page.evaluate(_STATE)
        print("\n[4] 结果")
        print("    抽屉还开着: %s" % st["open"])
        print("    SKU 区: %r" % (st["skuHint"] or "")[:200])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_created.png"), timeout_ms=15000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
