"""dump「销售信息」tab 的规格编辑区。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { buttons: [], inputs: [], selects: [], texts: [] };

  document.querySelectorAll('button,a,[role=button],span[class*="btn"]').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) return;
    const t = clean(b.innerText);
    if (!t || t.length > 24) return;
    out.buttons.push({ t, tag: b.tagName, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  document.querySelectorAll('input').forEach(i => {
    const r = i.getBoundingClientRect();
    if (r.width < 30 || r.height < 15) return;
    out.inputs.push({
      ph: i.placeholder || '',
      val: (i.value || '').slice(0, 20),
      cls: clean(i.className).slice(0, 50),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });

  document.querySelectorAll('[class*="select"],[class*="dropdown"]').forEach(s => {
    const r = s.getBoundingClientRect();
    if (r.width < 60 || r.height < 20) return;
    const t = clean(s.innerText);
    if (!t || t.length > 30) return;
    out.selects.push({ t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  // 销售信息区的文本块
  document.querySelectorAll('div,section').forEach(el => {
    const t = clean(el.innerText);
    if (!t || t.length > 60 || t.length < 2) return;
    if (/销售规格|一口价|总库存|创建规格|添加规格|SKU|商家编码|批量/.test(t)) {
      const r = el.getBoundingClientRect();
      if (r.width > 60 && r.height > 10) out.texts.push({ t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
    }
  });
  out.texts = out.texts.slice(0, 20);
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(400)
        print("URL: " + page.url[:70])

        print("\n[1] 点「销售信息」tab")
        try:
            page.get_by_text("销售信息", exact=True).first.click(timeout=6000)
            page.wait_for_timeout(2500)
            print("    [OK]")
        except Exception as exc:
            print("    [XX] %s" % str(exc).split("\n")[0])

        info = page.evaluate(_JS)
        print("\n按钮 %d 个:" % len(info["buttons"]))
        for b in info["buttons"]:
            if any(k in b["t"] for k in ("规格", "库存", "价格", "导入", "模板", "添加", "创建", "编码")):
                print("  *%-14r %-6s %s" % (b["t"], b["tag"], b["rect"]))
        print("\n输入框 %d 个:" % len(info["inputs"]))
        for i in info["inputs"]:
            print("  ph=%-16r val=%-14r %s" % (i["ph"], i["val"], i["rect"]))
        print("\n下拉 %d 个:" % len(info["selects"]))
        for d in info["selects"][:12]:
            print("  %-24r %s" % (d["t"], d["rect"]))
        print("\n相关文本:")
        for t in info["texts"]:
            print("  %-40r %s" % (t["t"], t["rect"]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "sales_tab.png"))
        print("\n截图: data/publish_poc/sales_tab.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
