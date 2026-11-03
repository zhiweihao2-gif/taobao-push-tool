"""填一口价 + 点真正的「+ 创建规格」按钮。

上次失败原因：
1. 一口价查询用了页面滚动前的坐标过滤，返回空
2. get_by_text("+ 创建规格") 匹配到 SPAN 文本（60x12），force 点到了但不触发
   → 真正的按钮是 button，[414, 1057, 86, 36]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_type  # noqa: E402

_PRICE_JS = r"""
() => {
  const out = [];
  [...document.querySelectorAll('input')].forEach((i, idx) => {
    const r = i.getBoundingClientRect();
    if (r.width < 100 || r.height < 20) return;
    out.push({ idx, ph: i.placeholder || '', val: i.value || '',
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return out;
}
"""

_AFTER = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { overlays: [], skuPanel: null };
  document.querySelectorAll('.next-overlay-wrapper.opened').forEach(o => {
    const r = o.getBoundingClientRect();
    if (r.height < 80) return;
    out.overlays.push({ rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], text: clean(o.innerText).slice(0, 300) });
  });
  // 主页面里 SKU 面板特征
  const body = clean(document.body.innerText);
  const i = body.indexOf('销售规格');
  out.skuPanel = i >= 0 ? body.slice(i, i + 200) : null;
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("URL: " + page.url[:70])

        # ---- 一口价 ----
        print("\n[1] 一口价")
        inputs = page.evaluate(_PRICE_JS)
        print("    大输入框 %d 个:" % len(inputs))
        for i in inputs:
            print("      idx=%-3d ph=%-30r val=%-14r %s" % (i["idx"], i["ph"][:28], i["val"][:12], i["rect"]))

        # 一口价：ph='' 且 宽度≈356 且 在总库存（val 是数字）上面
        stock = None
        for i in inputs:
            if i["ph"] == "" and i["val"].isdigit():
                stock = i
                break
        price = None
        if stock:
            cands = [i for i in inputs if i["ph"] == "" and i["rect"][1] < stock["rect"][1]]
            if cands:
                price = max(cands, key=lambda i: i["rect"][1])
        print("    总库存: %s" % (stock["rect"] if stock else None))
        print("    一口价: %s" % (price["rect"] if price else None))

        if price and not price["val"]:
            page.evaluate("(idx) => { [...document.querySelectorAll('input')][idx].setAttribute('data-dsh-price','1'); }", price["idx"])
            loc = page.locator('[data-dsh-price="1"]')
            loc.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(300)
            human_type(page, '[data-dsh-price="1"]', "39.9")
            page.wait_for_timeout(500)
            print("    写入: %r" % loc.input_value())
        elif price:
            print("    已有值 %r，跳过" % price["val"])
        else:
            print("    [XX] 定位失败")

        # ---- 创建规格 ----
        print("\n[2] 找「+ 创建规格」按钮")
        btns = page.locator("button").filter(has_text="创建规格")
        n = btns.count()
        print("    button 匹配 %d 个" % n)
        target = None
        for i in range(n):
            b = btns.nth(i)
            box = b.bounding_box()
            print("      [%d] %s" % (i, box))
            if box and box["width"] > 60 and box["height"] > 25:
                target = b
                break
        if target is None:
            print("    [XX] 没有合适的按钮")
            return 1

        target.scroll_into_view_if_needed(timeout=6000)
        page.wait_for_timeout(700)
        print("\n[3] 点击")
        try:
            target.click(timeout=8000)
            print("    [OK] 正常点击")
        except Exception as exc:
            print("    [--] %s" % str(exc).split("\n")[0])
            target.click(force=True, timeout=5000)
            print("    [OK] force 点击")

        page.wait_for_timeout(4000)
        info = page.evaluate(_AFTER)
        print("\n[4] 结果")
        print("  浮层 %d:" % len(info["overlays"]))
        for o in info["overlays"]:
            print("    %s" % o["rect"])
            print("      %r" % o["text"][:260])
        print("  SKU 区文本: %r" % (info["skuPanel"] or "")[:220])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sku_panel2.png"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
