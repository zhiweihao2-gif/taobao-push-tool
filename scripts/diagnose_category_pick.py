"""诊断：类目列表里「键帽」到底哪个元素才是真正可选的。

现象：品牌 + 型号都填了，「确认，下一步」还是 disabled。
怀疑第 2 列那个只是 hover 高亮，没有真正 select。

本脚本：
  1. dump 所有文本含关键词的元素（tag/class/rect/cursor/父子）
  2. 逐个尝试点击，每次点完检查「确认，下一步」是否 enabled
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

_DUMP = r"""
(kw) => {
  const hits = [];
  document.querySelectorAll('*').forEach(el => {
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (t !== kw && !(t.includes(kw) && t.length <= kw.length + 20)) return;
    const r = el.getBoundingClientRect();
    const st = window.getComputedStyle(el);
    if (r.width < 4 || r.height < 4) return;
    if (st.visibility === 'hidden' || st.display === 'none') return;
    hits.push({
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0, 120),
      text: t.slice(0, 60),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      cursor: st.cursor,
      bg: st.backgroundColor,
      color: st.color,
      role: el.getAttribute('role'),
      childCount: el.children.length,
    });
  });
  return hits;
}
"""

_BTN_STATE = r"""
() => {
  const out = [];
  document.querySelectorAll('button').forEach(b => {
    const t = (b.innerText || '').trim();
    const r = b.getBoundingClientRect();
    out.push({
      text: t,
      disabled: b.disabled,
      cls: (b.className || '').toString().slice(0, 100),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });
  return out;
}
"""


def main() -> int:
    kw = sys.argv[1] if len(sys.argv) > 1 else "键帽"

    with BrowserSession() as s:
        page = s.page()
        print("URL: " + page.url)

        print("\n=== 按钮状态 ===")
        for b in page.evaluate(_BTN_STATE):
            print(json.dumps(b, ensure_ascii=False))

        print("\n=== 含 %r 的元素 ===" % kw)
        hits = page.evaluate(_DUMP, kw)
        for i, h in enumerate(hits):
            print("%2d %s" % (i, json.dumps(h, ensure_ascii=False)))

        # 逐个点，看哪个能让按钮 enabled
        print("\n=== 逐个点击尝试 ===")
        for i, h in enumerate(hits):
            x = h["rect"][0] + h["rect"][2] / 2
            y = h["rect"][1] + h["rect"][3] / 2
            try:
                page.mouse.click(x, y)
            except Exception as exc:
                print("  %2d 坐标点击失败: %s" % (i, exc))
                continue
            page.wait_for_timeout(1200)
            states = page.evaluate(_BTN_STATE)
            target = next((b for b in states if "确认" in b["text"]), None)
            enabled = target and not target["disabled"]
            print("  %2d rect=%s -> 确认按钮 disabled=%s" % (
                i, h["rect"], target["disabled"] if target else "N/A"))
            if enabled:
                print("      [OK] 就是它: tag=%s cls=%s" % (h["tag"], h["cls"]))
                s.screenshot(str(ROOT / "data" / "publish_poc" / "found_category_click.png"))
                return 0

        print("\n[XX] 所有候选都点了，按钮仍 disabled")
        s.screenshot(str(ROOT / "data" / "publish_poc" / "still_disabled.png"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
