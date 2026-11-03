# -*- coding: utf-8 -*-
"""填 SKU 三列必填下拉（终版）。

真实结构（本轮 dump 确认）：
  <div class="options-item" title="单品">
    <div class="sell-o-info">
      <div class="info-content">单品</div>
      <div class="sell-rich-text bottom">与商品类目一致，单独售卖的商品</div>
    </div>
  </div>
已选中项内有 <i class="next-icon ... selected">。

所以按 `[class*="options-item"]` + `title` 精确匹配，一次命中，不碰容器文本。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

OUT = ROOT / "data" / "publish_poc"
COLS = ["SKU分类", "套装配列", "图案"]
WANTS = ["单品", "10颗", "Ctrl"]

MARK = r"""
() => {
  const ovs = [...document.querySelectorAll('.next-overlay-wrapper')]
    .filter(o => o.classList.contains('opened'));
  if (!ovs.length) return {err: 'no overlay', opts: []};
  const ov = ovs[ovs.length - 1];
  const out = [];
  ov.querySelectorAll('[class*="options-item"]').forEach((el) => {
    const t = (el.getAttribute('title') || '').trim();
    if (!t) return;
    el.setAttribute('data-dsh-opt', String(out.length));
    out.push({i: out.length, text: t,
              selected: !!el.querySelector('[class*="selected"]')});
  });
  return {count: out.length, opts: out};
}
"""


def close_all(page):
    for _ in range(3):
        if page.evaluate("""() => document.querySelectorAll('.next-overlay-wrapper.opened').length""") == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)


def fill_cell(page, ri, ci, want):
    tr = page.locator("table tr").nth(ri)
    sels = tr.locator(".next-select")
    if ci >= sels.count():
        return {"ok": False, "err": "no col"}
    sel = sels.nth(ci)

    close_all(page)
    sel.scroll_into_view_if_needed(timeout=6000)
    page.wait_for_timeout(350)

    opened = False
    for attempt in range(3):
        try:
            sel.click(position={"x": 8, "y": 18}, timeout=6000)
        except Exception:
            try:
                sel.click(force=True, timeout=6000)
            except Exception:
                pass
        page.wait_for_timeout(2000)
        m = page.evaluate(MARK)
        if m.get("opts"):
            opened = True
            break
        print("        (尝试%d 未展开)" % (attempt + 1))

    if not opened:
        return {"ok": False, "err": "no opts after 3 tries"}

    opts = m["opts"]
    print("        选项 %d: %s" % (len(opts), [o["text"] for o in opts][:8]))

    target = next((o for o in opts if o["text"] == want), None)
    if target is None:
        target = next((o for o in opts if want in o["text"]), None)
    if target is None:
        target = opts[0]
        print("        [!] 无 %r，用 %r" % (want, target["text"]))

    try:
        page.locator('[data-dsh-opt="%d"]' % target["i"]).click(force=True, timeout=5000)
        page.wait_for_timeout(800)
        close_all(page)
        return {"ok": True, "picked": target["text"]}
    except Exception as e:
        return {"ok": False, "err": str(e).split("\n")[0][:100]}


def read_row(page, ri):
    return page.evaluate(r"""
    (ri) => {
      const trs = document.querySelectorAll('table tr');
      if (ri >= trs.length) return null;
      return Array.from(trs[ri].querySelectorAll('.next-select'))
        .map(s => {
          const txt = (s.innerText || '').replace(/\s+/g, ' ').trim();
          return txt.slice(0, 18) || '(空)';
        });
    }""", ri)


def main():
    ok_count = 0
    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)
        total = page.evaluate("""() => document.querySelectorAll('table tr').length""")
        print("行数: %d" % total)

        for ri in range(total):
            print("\n--- row%d ---" % ri)
            for ci in range(3):
                r = fill_cell(page, ri, ci, WANTS[ci])
                if r.get("ok"):
                    ok_count += 1
                print("  %-8s -> %s" % (COLS[ci], r))
                time.sleep(0.4)

        print("\n" + "=" * 55)
        print("成功 %d / %d" % (ok_count, total * 3))
        print("=" * 55)
        for ri in range(total):
            print("  row%d: %s" % (ri, read_row(page, ri)))

        # 错误计数：从「优化建议」区域读
        info = page.evaluate(r"""
        () => {
          const body = document.body.innerText || '';
          const out = {};
          const i = body.indexOf('优化建议');
          if (i >= 0) out.panel = body.slice(i, i + 220).replace(/\n+/g, ' | ');
          const m = body.match(/错误\s*\((\d+)\)/) || body.match(/错误\s*\n\s*\((\d+)\)/);
          out.errCount = m ? m[1] : null;
          return out;
        }""")
        print("\n错误面板: %r" % info.get("panel", "")[:200])

        p = OUT / "sku_sel_done.png"
        sess.screenshot(p, full_page=False)
        print("截图: %s" % p)


if __name__ == "__main__":
    main()
