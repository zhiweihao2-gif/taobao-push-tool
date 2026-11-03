"""dump 类目项的完整 DOM 结构，找「怎么才算真正选中」的线索。

已知：点了「电脑硬件/显示器/电脑周边」和「键帽」后，两个 li 都带 .selected，
但「确认，下一步」仍 disabled。怀疑一级不该被选中（它应该只是"展开")。
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
  const out = { selected: [], sample: [], buttons: [] };

  document.querySelectorAll('li.category-item').forEach(el => {
    const t = (el.innerText || '').trim();
    if (el.classList.contains('selected')) {
      out.selected.push({ text: t, html: el.outerHTML.slice(0, 400),
                          cls: el.className, attrs: [...el.attributes].map(a => a.name + '=' + a.value) });
    }
  });

  // 取前 3 个未选中的做对照
  let n = 0;
  document.querySelectorAll('li.category-item').forEach(el => {
    if (n >= 3) return;
    if (el.classList.contains('selected')) return;
    n++;
    out.sample.push({ text: (el.innerText || '').trim(), html: el.outerHTML.slice(0, 400), cls: el.className });
  });

  // 所有按钮 + 是否 disabled
  document.querySelectorAll('button').forEach(b => {
    out.buttons.push({ text: (b.innerText || '').trim(), disabled: b.disabled,
                       cls: (b.className || '').toString().slice(0, 100) });
  });

  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)
        print("URL: " + page.url)
        data = page.evaluate(_JS)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
