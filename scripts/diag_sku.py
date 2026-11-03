"""SKU 调试：关抽屉重来，一次加 6 个颜色，dump 表结构。

已确认的两个坑：
  1. 加完一个颜色后输入框节点会被 React 重建，上一轮的 locator 变成 detached，
     click 静默失败 → 必须**每轮重新 query**，不能缓存 locator。
  2. SKU 表格不是 <table>，`document.querySelectorAll('tr')` 返回 0，
     是 div 网格 → 要按 class 前缀或列头文本定位。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

OUT = ROOT / "data" / "publish_poc"

COLORS = [
    "LOBO游戏机-灰色（透光）",
    "LOBO游戏机-粉色（透光）",
    "LOBO游戏机-蓝色（透光）",
    "LOBO游戏机-黄色（透光）",
    "第二弹-复古游戏机（透光）",
    "我全都要-4颗游戏机",
]

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
  const p = panel.getBoundingClientRect();
  const ins = [...panel.querySelectorAll('input')].map(i => {
    const r = i.getBoundingClientRect();
    return { ph: i.placeholder || '', val: (i.value || '').slice(0, 26),
             rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
  });
  const plus = [...panel.querySelectorAll('button')]
    .filter(b => !clean(b.innerText))
    .map(b => { const r = b.getBoundingClientRect();
                return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })
    .filter(r => r[2] > 20 && r[2] < 60);
  // 已加的 tag：抽屉里显示为 chip 的元素
  const tags = [...panel.querySelectorAll('[class*="tag"],[class*="chip"],[class*="label"]')]
    .map(e => clean(e.innerText)).filter(t => t && t.length < 30);
  return { rect: [Math.round(p.x), Math.round(p.y), Math.round(p.width), Math.round(p.height)],
           ins, plus, tags: [...new Set(tags)],
           text: clean(panel.innerText).slice(0, 300) };
}
"""


def log(m: str) -> None:
    print(m, flush=True)


def close_drawer(page) -> None:
    log("  关抽屉…")
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(900)
        page.keyboard.press("Escape")
        page.wait_for_timeout(900)
    except Exception:
        pass
    still = page.evaluate("() => (document.body.innerText || '').includes('选择展示模式')")
    if still:
        # 点「取消」
        for txt in ("取消", "关闭"):
            try:
                b = page.get_by_text(txt, exact=True)
                if b.count():
                    b.last.click(force=True, timeout=3000)
                    page.wait_for_timeout(1200)
                    break
            except Exception:
                continue
    log("  抽屉开着: %s" % page.evaluate("() => (document.body.innerText || '').includes('选择展示模式')"))


def open_drawer(page) -> bool:
    log("  开抽屉…")
    if page.evaluate("() => (document.body.innerText || '').includes('选择展示模式')"):
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
    except Exception as e:
        log("  [XX] %s" % str(e).split("\n")[0][:100])
    return bool(page.evaluate("() => (document.body.innerText || '').includes('选择展示模式')"))


def add_colors(page, colors: list[str]) -> int:
    """每轮重新 query 输入框 —— 缓存 locator 会在 React 重建后失效。"""
    ok = 0
    for i, c in enumerate(colors, 1):
        panel = page.evaluate(_PANEL)
        if not panel:
            log("  [XX] 面板没了")
            break
        cin = next((x for x in panel["ins"] if "主色" in x["ph"]), None)
        if cin is None:
            log("  [XX] 找不到主色框，现有 ph: %s" % [x["ph"] for x in panel["ins"]])
            break
        plus = None
        for r in panel["plus"]:
            if abs(r[1] - cin["rect"][1]) < 40 and r[0] > cin["rect"][0]:
                plus = r
                break
        if plus is None and panel["plus"]:
            plus = panel["plus"][0]
        if plus is None:
            log("  [XX] 找不到 +")
            break
        px, py = plus[0] + plus[2] // 2, plus[1] + plus[3] // 2

        try:
            # 关键：每轮重新 locator
            loc = page.locator('input[placeholder="%s"]' % cin["ph"]).first
            loc.click(timeout=4000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(150)
            for ch in c:
                page.keyboard.type(ch)
                page.wait_for_timeout(15)
            page.wait_for_timeout(400)
            page.mouse.click(px, py)
            page.wait_for_timeout(1000)

            now = page.evaluate(_PANEL)
            tags = (now or {}).get("tags") or []
            hit = any(c[:8] in t for t in tags) or any(c[:8] in ((x["val"] or "")) for x in (now or {}).get("ins", []))
            ok += 1 if hit else 0
            log("  %d/%d [%s] %-26s tags=%d" % (i, len(colors), "OK" if hit else "!!", c[:26], len(tags)))
        except Exception as e:
            log("  %d/%d [XX] %-26s %s" % (i, len(colors), c[:26], str(e).split("\n")[0][:80]))
    return ok


def dump_after_create(page) -> dict:
    """确认创建后 dump 真实表格结构。"""
    return page.evaluate(
        r"""
        () => {
          const clean = s => (s || '').replace(/\s+/g, ' ').trim();
          const out = { tables: document.querySelectorAll('table').length,
                        trs: document.querySelectorAll('tr').length,
                        rows: [], priceLike: [] };
          // 所有含「元」「件」且带 input 的中等尺寸容器
          document.querySelectorAll('div,li').forEach(el => {
            const t = clean(el.innerText);
            if (!t || t.length > 200) return;
            const cnt = el.querySelectorAll('input').length;
            if (!cnt) return;
            const r = el.getBoundingClientRect();
            if (r.width < 300 || r.height < 30 || r.height > 120) return;
            out.rows.push({ tag: el.tagName,
                            cls: (el.className || '').toString().slice(0, 80),
                            rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                            inputs: [...el.querySelectorAll('input')].map(i => {
                              const ir = i.getBoundingClientRect();
                              return { val: (i.value || '').slice(0, 12),
                                       ph: i.placeholder || '',
                                       rect: [Math.round(ir.x), Math.round(ir.y), Math.round(ir.width), Math.round(ir.height)] };
                            }),
                            text: t.slice(0, 120) });
          });
          // 去重：只保留最内层的（input 数与父层相同但尺寸更小）
          const uniq = [];
          out.rows.forEach(r => {
            const dup = uniq.find(u => u.rect[0] === r.rect[0] && u.rect[1] === r.rect[1]
                                    && u.rect[2] === r.rect[2] && u.rect[3] === r.rect[3]);
            if (!dup) uniq.push(r);
          });
          out.rows = uniq.slice(0, 12);
          return out;
        }
        """
    )


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)
        log("URL: %s" % page.url[:90])

        close_drawer(page)
        if not open_drawer(page):
            log("[XX] 抽屉打不开")
            return 1

        log("\n[1] 加 %d 个颜色" % len(COLORS))
        added = add_colors(page, COLORS)
        log("    成功 %d/%d" % (added, len(COLORS)))

        log("\n[2] 确认创建")
        try:
            page.locator("button:has-text('确认创建')").first.click(timeout=8000)
            page.wait_for_timeout(4500)
            log("    [OK]")
        except Exception as e:
            log("    [XX] %s" % str(e).split("\n")[0][:100])

        log("\n[3] dump 表格结构")
        info = dump_after_create(page)
        print(json.dumps(info, ensure_ascii=False, indent=1)[:3000])

        p = OUT / "sku_structure.json"
        p.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        log("\n-> %s" % p)

        try:
            page.screenshot(path=str(OUT / "sku_after_create.png"), timeout=30000)
        except Exception as e:
            log("[!] 截图失败 %s" % str(e)[:80])
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
