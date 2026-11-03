# -*- coding: utf-8 -*-
"""第 6 行填不进去 —— 试四条路，逐条记录结果。

A. 滚到该行可见再点
B. 用 force click + fill()
C. JS 原生 setter + input/change 事件
D. 逐字符 keyboard.type 且每个字符后打印当前值（看是不是被 React 重置）
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

TARGET_ROW = 5
PRICE = "140"
QTY = "34"


def snap(page, tag):
    v = page.evaluate("""() => {
      const trs = document.querySelectorAll('table tr');
      const out = [];
      for (let i = 0; i < trs.length; i++) {
        const inp = trs[i].querySelectorAll('input');
        out.push([i, inp[0] ? inp[0].value : null, inp[1] ? inp[1].value : null,
                  inp[0] ? inp[0].readOnly : null, inp[0] ? inp[0].disabled : null]);
      }
      return out;
    }""")
    print("  [%s] %s" % (tag, v))
    return v


def main():
    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        # 先看第 6 行 input 的属性
        detail = page.evaluate("""(i) => {
          const trs = document.querySelectorAll('table tr');
          if (i >= trs.length) return {err: 'no row ' + i};
          const tr = trs[i];
          const inp = tr.querySelectorAll('input');
          const r0 = inp[0] ? inp[0].getBoundingClientRect() : null;
          const r1 = inp[1] ? inp[1].getBoundingClientRect() : null;
          return {
            nInp: inp.length,
            i0: inp[0] ? {cls: inp[0].className, ro: inp[0].readOnly, dis: inp[0].disabled,
                          ph: inp[0].placeholder, val: inp[0].value,
                          rect: r0 ? [Math.round(r0.x), Math.round(r0.y), Math.round(r0.width), Math.round(r0.height)] : null} : null,
            i1: inp[1] ? {cls: inp[1].className, ro: inp[1].readOnly, dis: inp[1].disabled,
                          ph: inp[1].placeholder, val: inp[1].value,
                          rect: r1 ? [Math.round(r1.x), Math.round(r1.y), Math.round(r1.width), Math.round(r1.height)] : null} : null,
            rowText: (tr.innerText || '').replace(/\\s+/g, ' ').slice(0, 100),
          };
        }""", TARGET_ROW)
        print("\n第6行详情: %s" % detail)

        snap(page, "初始")

        tr = page.locator("table tr").nth(TARGET_ROW)

        # --- A: 滚到可见再点 ---
        print("\n--- A: scroll_into_view + click ---")
        try:
            tr.scroll_into_view_if_needed(timeout=5000)
            time.sleep(0.6)
            tr.locator("input").nth(0).click(timeout=5000)
            page.keyboard.press("Control+A")
            page.keyboard.type(PRICE, delay=40)
            time.sleep(0.6)
            snap(page, "A")
        except Exception as e:
            print("  [!!] %s" % str(e).split("\n")[0][:120])

        # --- B: fill() ---
        print("\n--- B: fill() ---")
        try:
            tr.locator("input").nth(0).fill(PRICE, timeout=5000)
            time.sleep(0.6)
            snap(page, "B")
        except Exception as e:
            print("  [!!] %s" % str(e).split("\n")[0][:120])

        # --- C: JS 原生 setter ---
        print("\n--- C: JS native setter + events ---")
        try:
            r = page.evaluate("""(args) => {
              const [i, v] = args;
              const tr = document.querySelectorAll('table tr')[i];
              const el = tr.querySelectorAll('input')[0];
              const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
              setter.call(el, v);
              el.dispatchEvent(new Event('input', {bubbles: true}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
              el.dispatchEvent(new Event('blur', {bubbles: true}));
              return {after: el.value};
            }""", [TARGET_ROW, PRICE])
            print("  JS 返回: %s" % r)
            time.sleep(0.8)
            snap(page, "C")
        except Exception as e:
            print("  [!!] %s" % str(e).split("\n")[0][:120])

        # --- D: 逐字符 ---
        print("\n--- D: 逐字符 type（打印中间值）---")
        try:
            inp = tr.locator("input").nth(0)
            inp.click(force=True, timeout=5000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            for ch in PRICE:
                page.keyboard.type(ch, delay=0)
                time.sleep(0.12)
                cur = page.evaluate("""(i) => {
                  const tr = document.querySelectorAll('table tr')[i];
                  return tr.querySelectorAll('input')[0].value;
                }""", TARGET_ROW)
                print("    敲 %r → 当前值 %r" % (ch, cur))
            time.sleep(0.5)
            snap(page, "D")
        except Exception as e:
            print("  [!!] %s" % str(e).split("\n")[0][:120])

        # 最终
        print("\n" + "=" * 60)
        final = snap(page, "最终")
        if final and len(final) > TARGET_ROW:
            print("\n第6行: 价格=%r 数量=%r" % (final[TARGET_ROW][1], final[TARGET_ROW][2]))

        # 错误提示
        errs = page.evaluate("""() => {
          const out = [];
          for (const el of document.querySelectorAll('[class*="error"], [class*="Error"]')) {
            const t = (el.innerText || '').trim();
            if (t && t.length < 80) out.push(t);
          }
          return [...new Set(out)].slice(0, 20);
        }""")
        print("\n页面错误提示: %s" % errs)


if __name__ == "__main__":
    main()
