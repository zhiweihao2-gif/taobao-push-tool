"""按源商品的 SKU 结构创建销售规格并逐行填价/库存。

流程（实测）：
  1. 点「+ 创建规格」→ 打开抽屉
  2. 抽屉里默认「分层展示·选择标准属性构建规格」+ 属性「颜色分类*」
  3. 颜色输入框 placeholder='主色(必选)'，右侧有个**无文本 button** 就是「+」
     —— filter(has_text="+") 匹配不到，必须按坐标点中心
  4. 每加一个颜色：点输入框 → Ctrl+A → Delete → 逐字符输入 → 点「+」
  5. 点「确认创建」→ 生成 SKU 表格
  6. 表格每行：颜色名 | 价格 | 数量 | SKU分类 | 套装配列 | 图案
     价格/数量是 input，直接键盘输入；后三个是下拉，get_by_text 点不到，按坐标点
  7. 三个下拉的选项文本要用 JS 打 data-dsh-opt 标记再点
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.storage import load_item  # noqa: E402
from src.publish.browser_pub import BrowserSession  # noqa: E402

OUT = ROOT / "data" / "publish_poc"

_PANEL = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  let panel = null;
  document.querySelectorAll('div').forEach(el => {
    if (panel) return;
    const t = clean(el.innerText);
    if (t.includes('选择展示模式') && t.includes('确认创建') && el.children.length < 80) panel = el;
  });
  if (!panel) return null;
  const r = panel.getBoundingClientRect();
  const inputs = [...panel.querySelectorAll('input')].map(i => {
    const ir = i.getBoundingClientRect();
    return { ph: i.placeholder || '', val: (i.value || '').slice(0, 24),
             rect: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)] };
  });
  const plus = [...panel.querySelectorAll('button')]
    .filter(b => !clean(b.innerText))
    .map(b => { const br = b.getBoundingClientRect();
                return { rect: [Math.round(br.x), Math.round(br.y), Math.round(br.width), Math.round(br.height)] }; })
    .filter(b => b.rect[2] > 20 && b.rect[2] < 60);
  return { rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
           inputs, plus, text: clean(panel.innerText).slice(0, 260) };
}
"""

# SKU 表格里每一行的可编辑单元
_ROWS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const rows = [];
  document.querySelectorAll('tr').forEach(tr => {
    const t = clean(tr.innerText);
    if (!t || !/元|件/.test(t)) return;
    const ins = [...tr.querySelectorAll('input')].map(i => {
      const r = i.getBoundingClientRect();
      return { val: (i.value || '').slice(0, 16),
               rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
    }).filter(i => i.rect[2] > 30);
    if (!ins.length) return;
    rows.push({ text: t.slice(0, 80), inputs: ins,
                rect: (() => { const r = tr.getBoundingClientRect();
                               return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })() });
  });
  return rows;
}
"""

_MARK_OPTS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return { err: 'no overlay', opts: [] };
  const cand = [...ov.querySelectorAll('li,[role=option],[class*="menu-item"],[class*="option"]')];
  const out = [];
  cand.forEach((el, i) => {
    const t = clean(el.innerText);
    if (!t) return;
    el.setAttribute('data-dsh-opt', String(i));
    out.push({ i, text: t.slice(0, 40) });
  });
  return { count: out.length, opts: out };
}
"""


def log(m: str) -> None:
    print(m, flush=True)


def open_sku_drawer(page) -> bool:
    if page.evaluate("() => document.body.innerText.includes('选择展示模式')"):
        log("    抽屉已开")
        return True
    try:
        btn = page.locator("button").filter(has_text="创建规格").first
        btn.scroll_into_view_if_needed(timeout=8000)
        page.wait_for_timeout(600)
        try:
            btn.click(timeout=5000)
        except Exception:
            btn.click(force=True, timeout=5000)
        page.wait_for_timeout(2800)
        return bool(page.evaluate("() => document.body.innerText.includes('选择展示模式')"))
    except Exception as e:
        log("    [XX] 开抽屉失败 %s" % str(e).split("\n")[0][:100])
        return False


def add_colors(page, colors: list[str]) -> int:
    panel = page.evaluate(_PANEL)
    if not panel:
        log("    [XX] 拿不到抽屉面板")
        return 0
    cin = next((i for i in panel["inputs"] if "主色" in i["ph"]), None)
    if cin is None:
        log("    [XX] 找不到主色输入框，输入框表: %s" % [i["ph"] for i in panel["inputs"]])
        return 0
    plus = None
    for b in panel["plus"]:
        if abs(b["rect"][1] - cin["rect"][1]) < 40 and b["rect"][0] > cin["rect"][0]:
            plus = b
            break
    if plus is None and panel["plus"]:
        plus = panel["plus"][0]
    if plus is None:
        log("    [XX] 找不到 + 按钮")
        return 0
    px = plus["rect"][0] + plus["rect"][2] // 2
    py = plus["rect"][1] + plus["rect"][3] // 2
    log("    + 按钮 (%d,%d)，输入框 ph=%r" % (px, py, cin["ph"]))

    loc = page.locator('input[placeholder="%s"]' % cin["ph"]).first
    ok = 0
    for c in colors:
        try:
            loc.click(timeout=4000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            for ch in c:
                page.keyboard.type(ch)
                page.wait_for_timeout(18)
            page.wait_for_timeout(320)
            page.mouse.click(px, py)
            page.wait_for_timeout(850)
            now = page.evaluate(_PANEL)
            vals = [i["val"] for i in (now["inputs"] if now else [])]
            hit = any(c[:6] in v for v in vals)
            ok += 1 if hit else 0
            log("    [%s] %-24s" % ("OK" if hit else "!!", c))
        except Exception as e:
            log("    [XX] %-24s %s" % (c, str(e).split("\n")[0][:80]))
    return ok


def confirm_drawer(page) -> bool:
    try:
        page.locator("button:has-text('确认创建')").first.click(timeout=8000)
        page.wait_for_timeout(4000)
        return True
    except Exception as e:
        log("    [XX] 确认创建失败 %s" % str(e).split("\n")[0][:100])
        return False


def fill_rows(page, skus: list[dict]) -> int:
    """skus: [{name, price, qty}]，按颜色名匹配行。"""
    page.wait_for_timeout(1200)
    rows = page.evaluate(_ROWS)
    log("    SKU 行 %d 个" % len(rows))
    for r in rows:
        log("      %s inputs=%d" % (r["text"][:50], len(r["inputs"])))

    ok = 0
    for s in skus:
        target = None
        for r in rows:
            if s["name"][:6] in r["text"]:
                target = r
                break
        if target is None:
            log("    [XX] 找不到行 %s" % s["name"])
            continue
        ins = target["inputs"]
        if len(ins) < 2:
            log("    [XX] 行里 input 不足 %s" % s["name"])
            continue
        # 第一个是价格，第二个是数量
        for val, idx, label in ((str(s["price"]), 0, "价格"), (str(s["qty"]), 1, "数量")):
            rr = ins[idx]["rect"]
            page.mouse.click(rr[0] + rr[2] // 2, rr[1] + rr[3] // 2)
            page.wait_for_timeout(220)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            for ch in val:
                page.keyboard.type(ch)
                page.wait_for_timeout(25)
            page.wait_for_timeout(260)
        now = page.evaluate(_ROWS)
        cur = next((x for x in now if s["name"][:6] in x["text"]), None)
        vals = [i["val"] for i in cur["inputs"]] if cur else []
        hit = len(vals) >= 2 and vals[0].replace(".0", "") == str(s["price"]).replace(".0", "")
        ok += 1 if hit else 0
        log("    [%s] %-22s 价格=%s 数量=%s" % ("OK" if hit else "!!", s["name"][:22],
                                                vals[0] if vals else "?", vals[1] if len(vals) > 1 else "?"))
    return ok


def pick_dropdown_by_xy(page, x: int, y: int, want: str, label: str) -> bool:
    try:
        page.mouse.click(x, y)
        page.wait_for_timeout(1500)
        m = page.evaluate(_MARK_OPTS)
        opts = m.get("opts") or []
        if not opts:
            log("      [XX] %s 下拉没开" % label)
            return False
        t = next((o for o in opts if o["text"] == want), None)
        if t is None:
            t = next((o for o in opts if want in o["text"]), None)
        if t is None:
            log("      [XX] %s 没有 %r，可选 %s" % (label, want, [o["text"][:8] for o in opts][:10]))
            page.keyboard.press("Escape")
            return False
        page.locator('[data-dsh-opt="%d"]' % t["i"]).click(force=True, timeout=4000)
        page.wait_for_timeout(700)
        log("      [OK] %s = %r" % (label, t["text"][:20]))
        return True
    except Exception as e:
        log("      [XX] %s %s" % (label, str(e).split("\n")[0][:80]))
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--sku-category", default="单品")
    ap.add_argument("--pack", default="1颗")
    ap.add_argument("--pattern", default=None)
    args = ap.parse_args()

    item = load_item(args.item)
    if item is None:
        log("找不到商品 %s" % args.item)
        return 1

    # 从 Item 的 SKU 推颜色/价格/库存
    prop_name = item.sale_props[0].name if item.sale_props else "颜色分类"
    val_name = {}
    if item.sale_props:
        for v in item.sale_props[0].values:
            val_name[v.vid] = v.name
    skus = []
    for s in item.skus:
        vid = list(s.attrs.values())[0] if s.attrs else ""
        nm = val_name.get(vid) or s.attrs.get("1627207") or "默认"
        skus.append({"name": nm, "price": int(s.price or 39), "qty": int(s.quantity or 0)})
    log("源 SKU %d 个（属性 %s）:" % (len(skus), prop_name))
    for s in skus:
        log("   %-26s ¥%s 库存 %s" % (s["name"], s["price"], s["qty"]))

    with BrowserSession(cdp_url=args.cdp) as sess:
        page = sess.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)
        log("\n当前页: %s" % page.url[:90])

        log("\n[1] 打开 SKU 抽屉")
        if not open_sku_drawer(page):
            log("[XX] 抽屉打不开，先确认停在发布页")
            return 1

        log("\n[2] 添加 %d 个颜色值" % len(skus))
        added = add_colors(page, [s["name"] for s in skus])
        log("    加了 %d/%d 个" % (added, len(skus)))
        sess.screenshot(str(OUT / "sku_colors_all.png"))

        log("\n[3] 确认创建")
        if not confirm_drawer(page):
            return 1
        sess.screenshot(str(OUT / "sku_table.png"))

        log("\n[4] 逐行填价格/库存")
        filled = fill_rows(page, skus)
        log("    填好 %d/%d 行" % (filled, len(skus)))

        log("\n[5] 填三个必填下拉（每行）")
        # 下拉位置从表格行 rect 推算：SKU分类约 x+560, 套装配列约 x+700, 图案约 x+870
        rows = page.evaluate(_ROWS)
        done = 0
        for r in rows:
            x0, y0, w, h = r["rect"]
            cy = y0 + h // 2
            pick_dropdown_by_xy(page, x0 + 560, cy, args.sku_category, "SKU分类")
            pick_dropdown_by_xy(page, x0 + 700, cy, args.pack, "套装配列")
            if args.pattern:
                pick_dropdown_by_xy(page, x0 + 870, cy, args.pattern, "图案")
            done += 1
        log("    处理 %d 行" % done)

        sess.screenshot(str(OUT / "sku_final.png"))
        log("\n截图: data/publish_poc/sku_final.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
