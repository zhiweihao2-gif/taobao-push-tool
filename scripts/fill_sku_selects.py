# -*- coding: utf-8 -*-
"""逐行填 SKU 的三个必填下拉：SKU分类 / 套装配列 / 图案。

定位：table tr > .next-select，nth(0)=SKU分类 nth(1)=套装配列 nth(2)=图案。
展开：点左边缘 position={"x":8,"y":18}（中心落在已选 tag 上不展开）。
选项：展开后给 .next-overlay-wrapper.opened 内的元素打 data-dsh-opt 再点。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

OUT = ROOT / "data" / "publish_poc"

COLS = ["SKU分类", "套装配列", "图案"]

MARK_OPTS = r"""
() => {
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')]
    .find(o => o.classList.contains('opened'));
  if (!ov) return {err: 'no overlay', opts: []};
  const out = [];
  ov.querySelectorAll('li,[role=option],[class*="menu-item"],[class*="option"]').forEach((el, i) => {
    const clean = (x) => (x || '').replace(/\s+/g, ' ').trim();
    const t = clean(el.innerText);
    if (!t) return;
    el.setAttribute('data-dsh-opt', String(out.length));
    out.push({i: out.length, text: t.slice(0, 26)});
  });
  return {count: out.length, opts: out, ovText: (ov.innerText || '').slice(0, 200)};
}
"""


def row_selects(page, row_idx):
    return page.evaluate(r"""
    (ri) => {
      const trs = document.querySelectorAll('table tr');
      if (ri >= trs.length) return {err: 'no row'};
      const out = [];
      trs[ri].querySelectorAll('.next-select').forEach((s, i) => {
        const r = s.getBoundingClientRect();
        out.push({i: i, text: (s.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 24),
                  rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                  cls: s.className.slice(0, 50)});
      });
      return {n: out.length, list: out};
    }""", row_idx)


def pick(page, row_idx, col_idx, want=None, dump_only=False):
    tr = page.locator("table tr").nth(row_idx)
    sels = tr.locator(".next-select")
    cnt = sels.count()
    if col_idx >= cnt:
        return {"ok": False, "err": "col %d >= %d" % (col_idx, cnt)}

    sel = sels.nth(col_idx)
    sel.scroll_into_view_if_needed(timeout=6000)
    page.wait_for_timeout(300)
    try:
        sel.click(position={"x": 8, "y": 18}, timeout=6000)
    except Exception:
        sel.click(force=True, timeout=6000)
    page.wait_for_timeout(2200)

    m = page.evaluate(MARK_OPTS)
    if not m.get("opts"):
        # 中心再试
        sel.click(force=True, timeout=6000)
        page.wait_for_timeout(2200)
        m = page.evaluate(MARK_OPTS)

    opts = m.get("opts") or []
    if dump_only:
        page.keyboard.press("Escape")
        return {"ok": True, "opts": [o["text"] for o in opts]}

    if not opts:
        return {"ok": False, "err": "no opts", "ovText": m.get("ovText", "")[:120]}

    target = None
    if want:
        target = next((o for o in opts if o["text"] == want), None)
        if target is None:
            target = next((o for o in opts if want in o["text"]), None)
    if target is None:
        target = opts[0]
    try:
        page.locator('[data-dsh-opt="%d"]' % target["i"]).click(force=True, timeout=5000)
        page.wait_for_timeout(700)
        return {"ok": True, "picked": target["text"]}
    except Exception as e:
        return {"ok": False, "err": str(e).split("\n")[0][:100]}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "dump"

    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        # 先看第 0 行的三个下拉
        info = row_selects(page, 0)
        print("\n第 0 行 .next-select: %s" % info)
        if info.get("err"):
            print("[XX] 表格结构不对")
            return

        if mode == "dump":
            print("\n=== 逐行 dump 每列选项（只 dump 不选）===")
            for ri in range(1):
                for ci in range(min(3, info["n"])):
                    r = pick(page, ri, ci, dump_only=True)
                    print("  row%d col%d (%s): %s"
                          % (ri, ci, COLS[ci] if ci < len(COLS) else "?",
                             r.get("opts", r.get("err"))[:12]))
            return

        # mode == fill
        # SKU分类=单品；套装配列=10颗（该类目最小粒度，源商品是 1 颗）；图案=Ctrl（源商品无图案属性，取首个可用）
        WANTS = ["单品", "10颗", "Ctrl"]
        total = 6
        for ri in range(total):
            for ci in range(min(3, info["n"])):
                want = WANTS[ci] if ci < len(WANTS) else None
                r = pick(page, ri, ci, want=want)
                print("  row%d %s -> %s" % (ri, COLS[ci] if ci < len(COLS) else ci, r))
                time.sleep(0.3)

        # 复核
        print("\n=== 复核 ===")
        for ri in range(total):
            i2 = row_selects(page, ri)
            vals = [s["text"] for s in i2.get("list", [])]
            print("  row%d: %s" % (ri, vals))

        # 错误数
        err = page.evaluate(r"""() => {
          const body = document.body.innerText || '';
          const m = body.match(/错误\s*\((\d+)\)/);
          return m ? m[1] : null;
        }""")
        print("\n错误计数: %s" % err)

        p = OUT / "sku_attrs_done.png"
        sess.screenshot(p, full_page=False)
        print("截图: %s" % p)


if __name__ == "__main__":
    main()
