"""键盘类目 SKU：切换到「颜色分类」属性，dump 它的可选值。

用法：
    python scripts/probe_sku_color.py --cid 110210
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.publish.browser_pub import BrowserSession, publish_url_for  # noqa: E402

OUT = Path("data/publish_poc")

OPEN_DRAWER = r"""() => {
  const btns = [...document.querySelectorAll('button')];
  const b = btns.find(x => (x.innerText || '').replace(/\s+/g, '').indexOf('创建规格') >= 0);
  if (!b) return {ok: false};
  b.scrollIntoView({block: 'center'});
  b.click();
  return {ok: true};
}"""

# 读出属性区所有 chip 的文本 + 选中态
LIST_CHIPS = r"""() => {
  const out = [];
  const drawer = document.querySelector('.sku-decouple-drawer, .next-drawer');
  if (!drawer) return out;
  // 属性 chip 一般是 span / div，文本是属性名，选中态带蓝框
  const cands = drawer.querySelectorAll('span, div, button, li');
  const seen = new Set();
  cands.forEach(el => {
    const t = (el.innerText || '').replace(/\s+/g, '');
    if (!t || t.length > 10) return;
    // 只要没有子元素带同样文本的「叶子」
    if (el.children.length > 3) return;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.width > 200 || r.height < 14 || r.height > 40) return;
    if (seen.has(t + '@' + Math.round(r.y))) return;
    seen.add(t + '@' + Math.round(r.y));
    const cls = String(el.className || '');
    const style = getComputedStyle(el);
    out.push({
      text: t,
      cls: cls.slice(0, 90),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      border: style.borderColor,
      color: style.color,
      bg: style.backgroundColor
    });
  });
  return out;
}"""

# 点某个 chip（按文本）
CLICK_CHIP = r"""(name) => {
  const drawer = document.querySelector('.sku-decouple-drawer, .next-drawer');
  if (!drawer) return {ok: false, why: 'no drawer'};
  const cands = [...drawer.querySelectorAll('span, div, button, li')];
  for (const el of cands) {
    const t = (el.innerText || '').replace(/\s+/g, '');
    if (t !== name) continue;
    if (el.children.length > 3) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.width > 200) continue;
    el.click();
    return {ok: true, rect: [r.x, r.y, r.width, r.height]};
  }
  return {ok: false, why: 'not found: ' + name};
}"""

# dump 抽屉里的 select 和它们的选项
DUMP_SELECTS = r"""() => {
  const drawer = document.querySelector('.sku-decouple-drawer, .next-drawer');
  if (!drawer) return [];
  const out = [];
  drawer.querySelectorAll('.next-select').forEach((sel, i) => {
    const r = sel.getBoundingClientRect();
    const inp = sel.querySelector('input');
    const em = sel.querySelector('em');
    out.push({
      i: i,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      ph: inp ? inp.placeholder : null,
      readOnly: inp ? inp.readOnly : null,
      val: em ? (em.textContent || '').trim() : (inp ? inp.value : null),
      expanded: inp ? inp.getAttribute('aria-expanded') : null,
      cls: String(sel.className || '').slice(0, 120)
    });
  });
  return out;
}"""

READ_OPTIONS = r"""() => {
  const out = [];
  document.querySelectorAll('.next-overlay-wrapper').forEach(w => {
    if (!w.classList.contains('opened')) return;
    w.querySelectorAll('[class*="options-item"]').forEach(el => {
      out.push({title: el.getAttribute('title'), text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80)});
    });
  });
  return out;
}"""


def log(m: str) -> None:
    print(m, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="110210")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    a = ap.parse_args()

    with BrowserSession(a.cdp, timeout_ms=40000) as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(publish_url_for(a.cid), wait_until="domcontentloaded", timeout=60000)

        for _ in range(30):
            if page.evaluate("() => document.querySelectorAll('.sell-catProp-item').length") >= 3:
                break
            time.sleep(0.5)
        time.sleep(1.5)

        log("[..] 打开抽屉 %s" % json.dumps(page.evaluate(OPEN_DRAWER), ensure_ascii=False))
        time.sleep(3.0)

        chips = page.evaluate(LIST_CHIPS)
        log("")
        log("=== 属性 chip 列表 ===")
        for c in chips:
            log("  %-16s bg=%-22s border=%-22s rect=%s"
                % (c.get("text"), c.get("bg"), c.get("border"), c.get("rect")))

        # 先取消两个默认属性
        for name in ("是否客制化键盘", "热插拔轴体"):
            r = page.evaluate(CLICK_CHIP, name)
            log("[..] 取消 %s -> %s" % (name, json.dumps(r, ensure_ascii=False)))
            time.sleep(1.2)

        # 加颜色分类
        r = page.evaluate(CLICK_CHIP, "颜色分类")
        log("[..] 选颜色分类 -> %s" % json.dumps(r, ensure_ascii=False))
        time.sleep(2.0)

        sels = page.evaluate(DUMP_SELECTS)
        log("")
        log("=== 抽屉内 select ===")
        for x in sels:
            log("  [%d] val=%-12r ph=%-10r ro=%s rect=%s"
                % (x["i"], x["val"], x["ph"], x["readOnly"], x["rect"]))

        # 点第一个 select 看选项
        if sels:
            tgt = sels[0]
            x, y, w, h = tgt["rect"]
            log("")
            log("[..] 点第 1 个 select 左边缘 (%d,%d)" % (x + 8, y + h // 2))
            page.mouse.click(x + 8, y + h // 2)
            time.sleep(2.0)
            opts = page.evaluate(READ_OPTIONS)
            log("选项 %d 个:" % len(opts))
            for o in opts[:40]:
                log("    %-24r %s" % (o.get("title"), o.get("text", "")[:50]))

        page.screenshot(path=str(OUT / "probe_sku_color.png"), full_page=False, timeout=30000)
        log("")
        log("截图 %s" % (OUT / "probe_sku_color.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
