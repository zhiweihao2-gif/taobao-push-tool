"""在「类目+品牌+型号都已填」的现有页面上，多路试探按钮为何 disabled。

不做 reload，直接在当前状态上试，避免选中态丢失。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, click_text, human_pause  # noqa: E402

_STATE = r"""
() => {
  const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').includes('确认'));
  const sel = [...document.querySelectorAll('li.category-item.selected')].map(e => (e.innerText||'').trim());
  const inputs = [...document.querySelectorAll('input')]
    .filter(e => e.getBoundingClientRect().width > 10)
    .map(e => ({ name: e.getAttribute('name'), ph: e.getAttribute('placeholder'), value: (e.value||'').slice(0,30) }));
  // 第三列属性区：把所有可见的「*」必填标签和 select/下拉都列出来
  const reqMarks = [];
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length > 1) return;
    const t = (el.innerText || '').trim();
    if (t === '*' || t === '* ') {
      const r = el.getBoundingClientRect();
      if (r.width > 0) reqMarks.push([Math.round(r.x), Math.round(r.y)]);
    }
  });
  // 找所有可见的 select 和 role=combobox
  const combos = [];
  document.querySelectorAll('[role="combobox"],select,.next-select').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 20) return;
    combos.push({ tag: el.tagName, cls: (el.className||'').toString().slice(0,60),
                  text: (el.innerText||'').trim().slice(0,40),
                  rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return {
    disabled: btn ? btn.disabled : null,
    selected: sel,
    inputs,
    reqMarkCount: reqMarks.length,
    reqMarks,
    combos,
    treeItems: document.querySelectorAll('li.category-item').length,
  };
}
"""


def show(page, tag):
    st = page.evaluate(_STATE)
    print("  [%s] disabled=%s selected=%s tree=%d reqMarks=%d" % (
        tag, st["disabled"], st["selected"], st["treeItems"], st["reqMarkCount"]))
    print("       inputs=%s" % st["inputs"])
    if st["combos"]:
        print("       combos=%s" % st["combos"])
    return st


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(1000)
        print("URL: " + page.url)

        st = show(page, "初始")
        if not st["disabled"]:
            print("\n[OK] 已经 enabled")
            return 0

        print("\nA. 点「选择类目」收起树")
        click_text(page, "选择类目", timeout_ms=6000)
        page.wait_for_timeout(2500)
        human_pause(0.6, 1.0)
        st = show(page, "A")
        if not st["disabled"]:
            print("\n[OK] A 有效")
            return 0

        print("\nB. 点页面空白处 blur")
        page.mouse.click(700, 180)
        page.wait_for_timeout(1800)
        st = show(page, "B")
        if not st["disabled"]:
            print("\n[OK] B 有效")
            return 0

        print("\nC. 强制按钮 focus + Enter")
        page.evaluate(
            r"""
            () => {
              const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').includes('确认'));
              if (btn) { btn.disabled = false; btn.focus(); }
            }
            """
        )
        page.wait_for_timeout(800)
        st = show(page, "C-强制后")
        try:
            click_text(page, "确认，下一步", timeout_ms=6000)
            page.wait_for_timeout(6000)
            print("    点后 URL: " + page.url)
            if "publish" in page.url or "success" in page.url:
                print("\n[OK] 进发布页了")
                return 0
        except Exception as exc:
            print("    点击异常: %s" % exc)

        print("\nD. dump 页面所有可见文本（找遗漏的必填提示）")
        body = page.evaluate(r"() => (document.body.innerText || '')")
        print(body[:2500])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "final_stuck.png"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
