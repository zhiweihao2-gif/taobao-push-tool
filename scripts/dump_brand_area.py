"""dump 品牌区：找出「无品牌/无注册商标」到底是什么元素。

当前页面应停在类目已选中、品牌待填的状态。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_JS = r"""
() => {
  const hits = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.innerText || '').trim();
    if (!t.includes('无品牌')) return;
    if (t.length > 200) return;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    hits.push({
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0, 120),
      text: t.slice(0, 120),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      display: st.display,
      visibility: st.visibility,
      cursor: st.cursor,
      childCount: el.children.length,
      href: el.getAttribute && el.getAttribute('href'),
      role: el.getAttribute && el.getAttribute('role'),
      onclick: !!(el.onclick),
    });
  });
  // 只要叶子级（childCount 小的）
  const leaves = hits.filter(h => h.childCount <= 2 && h.rect[2] > 4 && h.rect[3] > 4);
  return { all: hits.length, leaves };
}
"""

_INPUTS = r"""
() => [...document.querySelectorAll('input')].map(el => {
  const r = el.getBoundingClientRect();
  return {
    type: el.type, id: el.id, name: el.name,
    placeholder: el.placeholder, value: el.value,
    cls: (el.className || '').toString().slice(0, 100),
    visible: r.width > 4,
    rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
  };
}).filter(i => i.visible)
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        print("URL: " + page.url)

        print("\n=== 可见 input ===")
        for i in page.evaluate(_INPUTS):
            print(json.dumps(i, ensure_ascii=False))

        print("\n=== 含「无品牌」的叶子元素 ===")
        data = page.evaluate(_JS)
        print("总计命中 %d 个，叶子 %d 个" % (data["all"], len(data["leaves"])))
        for h in data["leaves"]:
            print(json.dumps(h, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
