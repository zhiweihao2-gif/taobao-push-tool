# -*- coding: utf-8 -*-
"""SKU 颜色建好后，检查当前页面状态并定位 SKU 价格/库存输入框。

不 reload、不重填。只做：
1. 打印当前 URL / tab / 抽屉状态
2. 列出所有可见 input（placeholder + value + rect + 父容器文本）
3. 尝试切到「销售信息」tab
4. 再 dump 一次，重点找价格/数量那一对
5. 截图
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

OUT = ROOT / "data" / "publish_poc"

LIST_JS = r"""
() => {
  const out = [];
  const all = document.querySelectorAll('input,textarea');
  for (let i = 0; i < all.length; i++) {
    const el = all[i];
    const r = el.getBoundingClientRect();
    const vis = !!(r.width > 4 && r.height > 4);
    let label = '';
    let p = el.parentElement;
    for (let d = 0; d < 5 && p; d++) {
      const t = (p.innerText || '').trim();
      if (t && t.length < 60) { label = t.replace(/\s+/g, ' '); break; }
      p = p.parentElement;
    }
    out.push({
      i: i,
      ph: el.placeholder === undefined ? null : el.placeholder,
      name: el.name || '',
      cls: (el.className || '').slice(0, 60),
      val: el.value === undefined ? '' : String(el.value).slice(0, 40),
      vis: vis,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      label: label.slice(0, 60),
    });
  }
  return out;
}
"""

TAB_JS = r"""
() => {
  const out = [];
  const els = document.querySelectorAll('[class*="tab"], .next-tabs-tab, li[role="tab"]');
  for (const el of els) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 12) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    out.push({ t: t, cls: (el.className || '').slice(0, 50),
               rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  }
  return out;
}
"""


def dump(page, tag):
    print("\n" + "=" * 68)
    print("DUMP: " + tag)
    print("=" * 68)
    print("URL: " + page.url)
    fields = page.evaluate(LIST_JS)
    vis = [f for f in fields if f["vis"]]
    print("字段: %d 个（可见 %d）" % (len(fields), len(vis)))
    for f in vis:
        print("  [%2d] ph=%-22r name=%-18s val=%-24r rect=%s | %s"
              % (f["i"], (f["ph"] or "")[:22], f["name"][:18],
                 f["val"][:24], f["rect"], f["label"][:40]))
    return fields


def main():
    with BrowserSession() as sess:
        page = sess.page()
        print("当前页: " + page.url)

        # 1. 当前状态
        dump(page, "初始")

        # 2. tab 列表
        tabs = page.evaluate(TAB_JS)
        print("\n可见 tab:")
        for t in tabs:
            print("  %-10r %s" % (t["t"], t["rect"]))

        # 3. 点「销售信息」
        clicked = False
        for t in tabs:
            if t["t"] == "销售信息":
                x, y, w, h = t["rect"]
                page.mouse.click(x + w // 2, y + h // 2)
                clicked = True
                break
        if not clicked:
            try:
                page.get_by_text("销售信息", exact=True).first.click(force=True, timeout=4000)
                clicked = True
            except Exception as e:
                print("[!] 点销售信息失败: %s" % str(e).split("\n")[0][:100])
        print("\n点了销售信息: %s" % clicked)
        time.sleep(2.0)

        # 4. 再 dump
        dump(page, "销售信息 tab")

        # 5. 找 SKU 表格区域
        sku_info = page.evaluate(r"""
        () => {
          const out = {};
          out.hasMode = !!document.querySelector('[class*="sku-mode"], [class*="SKU模式"]');
          out.modeText = (document.querySelector('[class*="sku-mode"]') || {}).innerText || '';
          out.tables = document.querySelectorAll('table').length;
          out.trs = document.querySelectorAll('tr').length;
          const rows = document.querySelectorAll('[class*="sale-props"] tr, [class*="sku-table"] tr');
          out.skuRows = rows.length;
          // 找所有「元」「件」单位
          const units = [];
          const all = document.querySelectorAll('*');
          for (const el of all) {
            if (el.children.length) continue;
            const t = (el.innerText || '').trim();
            if (t === '元' || t === '件') {
              const r = el.getBoundingClientRect();
              if (r.width > 2) units.push({ t: t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
            }
          }
          out.units = units;
          // 找 送/删 之类
          out.text = (document.body.innerText || '').slice(0, 600);
          return out;
        }
        """)
        print("\n" + "=" * 68)
        print("SKU 区域")
        print("=" * 68)
        print("hasMode: %s  tables: %s  trs: %s  skuRows: %s"
              % (sku_info["hasMode"], sku_info["tables"], sku_info["trs"], sku_info["skuRows"]))
        print("modeText: %r" % sku_info["modeText"][:120])
        print("单位标记 (%d):" % len(sku_info["units"]))
        for u in sku_info["units"]:
            print("  %r %s" % (u["t"], u["rect"]))
        print("\nbody 前 600 字:")
        print(sku_info["text"][:600])

        # 6. 截图
        p = OUT / "resume_state.png"
        sess.screenshot(p, full_page=False)


if __name__ == "__main__":
    main()
