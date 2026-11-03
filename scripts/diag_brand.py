"""诊断品牌下拉：列表有多长、能不能过滤、无品牌在哪。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, publish_url_for  # noqa: E402

_DUMP = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return { err: 'no overlay' };
  // 找可滚动的内层容器
  let sc = ov, best = 0;
  ov.querySelectorAll('*').forEach(el => {
    if (el.scrollHeight > el.clientHeight + 20 && el.scrollHeight > best) {
      best = el.scrollHeight; sc = el;
    }
  });
  const txt = clean(ov.innerText);
  return {
    ovText: txt.slice(0, 200),
    ovTextTail: txt.slice(-200),
    ovLen: txt.length,
    scrollHeight: sc.scrollHeight,
    clientHeight: sc.clientHeight,
    scCls: (sc.className || '').toString().slice(0, 80),
    hasNoBrand: txt.includes('无品牌'),
    itemCount: ov.querySelectorAll('li,[role=option],[class*="menu-item"],[class*="option"]').length,
  };
}
"""

_SCROLL_DOWN = r"""
(n) => {
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return 0;
  let sc = ov, best = 0;
  ov.querySelectorAll('*').forEach(el => {
    if (el.scrollHeight > el.clientHeight + 20 && el.scrollHeight > best) {
      best = el.scrollHeight; sc = el;
    }
  });
  sc.scrollTop = sc.scrollHeight;
  return sc.scrollTop;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        s.goto(publish_url_for("200670001"), wait_until="domcontentloaded", settle_ms=2000)
        page.wait_for_timeout(3000)

        main_box = page.locator('input[placeholder="请输入"]').first
        main_box.scroll_into_view_if_needed(timeout=5000)
        main_box.click(timeout=5000)
        page.wait_for_timeout(2000)

        print("=== 展开后 ===")
        d = page.evaluate(_DUMP)
        print(json.dumps(d, ensure_ascii=False, indent=2)[:1200])

        print("\n=== 滚到底 ===")
        page.evaluate(_SCROLL_DOWN, 1)
        page.wait_for_timeout(1500)
        d2 = page.evaluate(_DUMP)
        print(json.dumps(d2, ensure_ascii=False, indent=2)[:1200])

        print("\n=== 在主框敲「无品牌」 ===")
        try:
            main_box.click(timeout=3000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type("无品牌", delay=80)
            page.wait_for_timeout(2000)
            d3 = page.evaluate(_DUMP)
            print(json.dumps(d3, ensure_ascii=False, indent=2)[:1200])
        except Exception as e:
            print("敲字失败: %s" % str(e)[:150])

        print("\n=== 敲完是否有关闭 ===")
        closed = page.evaluate(
            "() => [...document.querySelectorAll('.next-overlay-wrapper')].filter(o => o.classList.contains('opened')).length"
        )
        print("opened overlay: %s" % closed)

        s.screenshot(str(ROOT / "data" / "publish_poc" / "brand_diag.png"), timeout_ms=25000)
        print("\n截图 data/publish_poc/brand_diag.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
