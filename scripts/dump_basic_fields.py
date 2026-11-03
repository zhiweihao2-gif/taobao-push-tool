"""dump 基础信息区所有字段（含必填未填的），带 data-dsh-idx 标记。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_MARK = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const nodes = document.querySelectorAll('input,textarea,[contenteditable="true"]');
  let i = 0;
  nodes.forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) return;
    el.setAttribute('data-dsh-idx', String(i));
    // 向上找 label
    let label = '';
    let p = el.parentElement;
    for (let k = 0; k < 6 && p; k++) {
      const t = clean(p.innerText);
      if (t && t.length <= 40 && !/^\d+\/\d+$/.test(t)) { label = t; break; }
      p = p.parentElement;
    }
    out.push({
      idx: i,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      ph: el.getAttribute('placeholder') || '',
      val: (el.value !== undefined ? el.value : el.innerText || '').slice(0, 40),
      label: label.slice(0, 40),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      required: /[*＊]/.test(label),
    });
    i++;
  });
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)
        print("URL: " + page.url[:70])

        # 滚到基础信息区
        page.evaluate("() => window.scrollTo(0, 1200)")
        page.wait_for_timeout(900)
        fields = page.evaluate(_MARK)
        print("\n字段 %d 个:" % len(fields))
        for f in fields:
            flag = " *" if f["required"] else "  "
            print("%s [%2d] %-8s ph=%-28r val=%-20r %-24r %s" % (
                flag, f["idx"], f["tag"], f["ph"][:26], f["val"][:18], f["label"][:22], f["rect"]))

        (ROOT / "data" / "publish_poc" / "basic_fields.json").write_text(
            json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")

        s.screenshot(str(ROOT / "data" / "publish_poc" / "basic_area.png"), timeout_ms=20000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
