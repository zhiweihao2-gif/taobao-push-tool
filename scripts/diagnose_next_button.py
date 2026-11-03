"""诊断「确认，下一步」按钮：为什么 disabled / 是否被遮挡 / 父容器样式。

实测线索：按钮 rect=[982,942,98,36] 但截图 y=942 处是空白。
怀疑被父容器裁切或被别的层盖住。
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
  const btn = [...document.querySelectorAll('button')]
    .find(b => (b.innerText || '').includes('确认'));
  if (!btn) return { error: 'no button' };

  const r = btn.getBoundingClientRect();
  const cx = r.x + r.width / 2;
  const cy = r.y + r.height / 2;
  const top = document.elementFromPoint(cx, cy);

  // 逐层往上记录父容器，找出谁在裁切/隐藏
  const chain = [];
  let el = btn;
  for (let i = 0; i < 8 && el; i++) {
    const cs = window.getComputedStyle(el);
    const rr = el.getBoundingClientRect();
    chain.push({
      tag: el.tagName,
      cls: (el.className || '').toString().slice(0, 90),
      rect: [Math.round(rr.x), Math.round(rr.y), Math.round(rr.width), Math.round(rr.height)],
      display: cs.display,
      visibility: cs.visibility,
      opacity: cs.opacity,
      overflow: cs.overflow,
      zIndex: cs.zIndex,
      position: cs.position,
    });
    el = el.parentElement;
  }

  return {
    btn: {
      text: (btn.innerText || '').trim(),
      disabled: btn.disabled,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      offsetParent: btn.offsetParent ? btn.offsetParent.tagName + '.' + (btn.offsetParent.className||'').toString().slice(0,60) : null,
    },
    centerPoint: [Math.round(cx), Math.round(cy)],
    elementAtCenter: top ? { tag: top.tagName, cls: (top.className||'').toString().slice(0,90), text: (top.innerText||'').trim().slice(0,40) } : null,
    isButtonAtCenter: top === btn || btn.contains(top),
    chain,
    viewport: [window.innerWidth, window.innerHeight],
    scroll: [window.scrollX, window.scrollY],
  };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        print("URL: " + page.url)

        before = page.evaluate("() => [window.innerWidth, window.innerHeight]")
        print("原视口: %s" % before)

        # 实测根因：窗口只有 988 宽，页面布局要 1100，按钮 rect x=982~1080
        # 中心点 1031 已超出视口 → elementFromPoint 返回 null，点不到。
        try:
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.wait_for_timeout(1500)
            after = page.evaluate("() => [window.innerWidth, window.innerHeight]")
            print("set_viewport_size 后: %s" % after)
        except Exception as exc:
            print("set_viewport_size 失败: %s" % exc)

        data = page.evaluate(_JS)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
