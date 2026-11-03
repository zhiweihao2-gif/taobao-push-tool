"""开抽屉 → 打印主色输入框的精确码点 → 三种输入方式对比。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_FIND = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  let panel = null;
  document.querySelectorAll('div').forEach(el => {
    if (panel) return;
    const t = clean(el.innerText);
    if (t.includes('选择展示模式') && t.includes('确认创建')) panel = el;
  });
  if (!panel) return { panel: false };
  return {
    panel: true,
    inputs: [...panel.querySelectorAll('input')].map(i => {
      const r = i.getBoundingClientRect();
      const ph = i.placeholder || '';
      return { ph, codes: [...ph].map(c => c.codePointAt(0)),
               repr: JSON.stringify(ph),
               rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
               cls: (i.className || '').toString().slice(0, 60) };
    }),
  };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)

        print("[开抽屉]", flush=True)
        try:
            b = page.locator("button").filter(has_text="创建规格").first
            b.scroll_into_view_if_needed(timeout=8000)
            page.wait_for_timeout(500)
            b.click(timeout=6000)
            page.wait_for_timeout(3200)
        except Exception as e:
            print("  [XX] %s" % str(e).split("\n")[0][:120], flush=True)

        st = page.evaluate(_FIND)
        print(json.dumps(st, ensure_ascii=False, indent=1), flush=True)

        if not st.get("panel"):
            return 1

        # 找含「主色」的框
        cin = next((x for x in st["inputs"] if "主色" in x["ph"]), None)
        if not cin:
            print("[XX] 没找到主色框", flush=True)
            return 1

        print("\n[方式1] JS 设值 + dispatch input/change（React 合成事件）", flush=True)
        r1 = page.evaluate(
            r"""
            (phRepr) => {
              const clean = s => (s || '').replace(/\s+/g, ' ').trim();
              let panel = null;
              document.querySelectorAll('div').forEach(el => {
                if (panel) return;
                const t = clean(el.innerText);
                if (t.includes('选择展示模式') && t.includes('确认创建')) panel = el;
              });
              const ph = JSON.parse(phRepr);
              const inp = [...panel.querySelectorAll('input')].find(i => i.placeholder === ph);
              if (!inp) return { ok: false, why: 'no input by exact placeholder' };
              const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
              set.call(inp, '测试A');
              inp.dispatchEvent(new Event('input', { bubbles: true }));
              inp.dispatchEvent(new Event('change', { bubbles: true }));
              return { ok: true, val: inp.value };
            }
            """,
            cin["repr"],
        )
        print("  %s" % json.dumps(r1, ensure_ascii=False), flush=True)

        print("\n[方式2] Playwright get_by_placeholder", flush=True)
        try:
            loc = page.get_by_placeholder(cin["ph"])
            print("  count=%d" % loc.count(), flush=True)
            loc.first.click(timeout=4000, force=True)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type("测试B", delay=40)
            page.wait_for_timeout(400)
            v = page.evaluate(
                r"""
                (phRepr) => {
                  const ph = JSON.parse(phRepr);
                  const i = [...document.querySelectorAll('input')].find(x => x.placeholder === ph);
                  return i ? i.value : '<no input>';
                }
                """,
                cin["repr"],
            )
            print("  value=%r" % v, flush=True)
        except Exception as e:
            print("  [XX] %s" % str(e).split("\n")[0][:120], flush=True)

        print("\n[方式3] CSS 属性选择器（转义）", flush=True)
        try:
            esc = cin["ph"].replace("\\", "\\\\").replace('"', '\\"')
            sel = 'input[placeholder="%s"]' % esc
            n = page.locator(sel).count()
            print("  selector=%s  count=%d" % (sel, n), flush=True)
        except Exception as e:
            print("  [XX] %s" % str(e).split("\n")[0][:120], flush=True)

        return 0


if __name__ == "__main__":
    raise SystemExit(main())
