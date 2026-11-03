"""填 SKU 数量 + 总库存，然后检查提交前状态。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_type  # noqa: E402

_GRID = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  // 找 SKU 表格行
  const rows = [];
  document.querySelectorAll('tr,[class*="sku-row"],[class*="SkuRow"],[class*="table-row"]').forEach(tr => {
    const r = tr.getBoundingClientRect();
    if (r.height < 30 || r.height > 120 || r.width < 300) return;
    const t = clean(tr.innerText);
    if (!t) return;
    const inputs = [...tr.querySelectorAll('input')].map(i => {
      const ir = i.getBoundingClientRect();
      return { ph: i.placeholder || '', val: (i.value || '').slice(0, 12), rect: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)] };
    });
    rows.push({ text: t.slice(0, 60), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], inputs });
  });

  // 底部一口价/总库存
  const bottoms = [];
  [...document.querySelectorAll('input')].forEach(i => {
    const r = i.getBoundingClientRect();
    if (r.width < 100 || r.height < 20 || r.y < 0 || r.y > 1000) return;
    bottoms.push({ ph: i.placeholder || '', val: (i.value || '').slice(0, 12), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return { rows, bottoms };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)
        print("URL: " + page.url[:70])

        info = page.evaluate(_GRID)
        print("\nSKU 行 %d:" % len(info["rows"]))
        for r in info["rows"]:
            print("  %-50r %s" % (r["text"][:48], r["rect"]))
            for i in r["inputs"]:
                print("      ph=%-12r val=%-10r %s" % (i["ph"], i["val"], i["rect"]))

        print("\n可见大输入框 %d:" % len(info["bottoms"]))
        for b in info["bottoms"]:
            print("  ph=%-14r val=%-12r %s" % (b["ph"], b["val"], b["rect"]))

        # 填 SKU 数量：找 val='0' 且宽度小的输入框（表格里的数量列）
        print("\n[1] 填 SKU 数量")
        filled = 0
        for r in info["rows"]:
            for i in r["inputs"]:
                if i["val"] in ("0", "") and 40 < i["rect"][2] < 120:
                    try:
                        page.evaluate("""([x, y]) => {
                          const el = document.elementFromPoint(x, y);
                          if (el && el.tagName === 'INPUT') el.setAttribute('data-dsh-qty', '1');
                        }""", [i["rect"][0] + i["rect"][2] // 2, i["rect"][1] + i["rect"][3] // 2])
                        loc = page.locator('[data-dsh-qty="1"]').first
                        if loc.count() == 0:
                            continue
                        loc.scroll_into_view_if_needed(timeout=4000)
                        human_type(page, '[data-dsh-qty="1"]', "100")
                        page.wait_for_timeout(600)
                        print("    写入 %r" % loc.input_value())
                        page.evaluate("() => document.querySelectorAll('[data-dsh-qty]').forEach(e => e.removeAttribute('data-dsh-qty'))")
                        filled += 1
                    except Exception as exc:
                        print("    失败: %s" % str(exc).split("\n")[0][:70])
        if not filled:
            print("    [!] 没找到数量框，试试直接找表格里第 3 个 input")

        page.wait_for_timeout(1200)
        info2 = page.evaluate(_GRID)
        print("\n[2] 填后状态")
        for r in info2["rows"]:
            print("  %-50r" % r["text"][:48])
            for i in r["inputs"]:
                print("      ph=%-12r val=%-10r" % (i["ph"], i["val"]))
        print("  底部:")
        for b in info2["bottoms"]:
            print("    ph=%-14r val=%-12r %s" % (b["ph"], b["val"], b["rect"]))

        # 检查提交按钮状态
        btn = page.locator("button").filter(has_text="提交宝贝信息").first
        try:
            disabled = btn.is_disabled()
            print("\n[3] 提交按钮 disabled=%s" % disabled)
        except Exception as exc:
            print("\n[3] 查不到提交按钮: %s" % str(exc).split("\n")[0][:60])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "ready_to_submit.png"), timeout_ms=15000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
