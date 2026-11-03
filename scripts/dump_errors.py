"""展开「错误(N)」面板，dump 具体缺什么。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_DUMP_ERRORS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { panels: [], missing: [], rows: [] };
  // 错误面板
  document.querySelectorAll('[class*="error"],[class*="Error"],[class*="issue"],[class*="Issue"]').forEach(d => {
    const r = d.getBoundingClientRect();
    if (r.height < 20 || r.width < 80) return;
    const t = clean(d.innerText);
    if (!t || t.length < 4) return;
    out.panels.push({ rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], text: t.slice(0, 800) });
  });
  // 必填未填的标记
  document.querySelectorAll('*').forEach(e => {
    if (e.children.length) return;
    const t = clean(e.innerText);
    if (!t) return;
    if (/必填项未填|请填写|请选择|不能为空/.test(t) && t.length < 40) {
      const r = e.getBoundingClientRect();
      if (r.height < 12) return;
      out.missing.push({ t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
    }
  });
  return out;
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)
        print("URL: " + page.url[:80])

        # 找「错误」tab 并点
        print("\n=== 点「错误」 ===")
        hit = False
        for sel in ("text=错误", "text=/错误\\s*\\(\\d\\)/"):
            try:
                page.locator(sel).first.click(timeout=6000)
                hit = True
                print("  点了 %s" % sel)
                break
            except Exception as exc:
                print("  %s 失败: %s" % (sel, str(exc).split("\n")[0][:60]))
        page.wait_for_timeout(1800)

        st = page.evaluate(_DUMP_ERRORS)
        print("\n面板 %d 个:" % len(st["panels"]))
        seen = set()
        for p in st["panels"]:
            k = p["text"][:60]
            if k in seen:
                continue
            seen.add(k)
            print("  %s" % p["rect"])
            print("    %r" % p["text"][:500])

        print("\n缺失标记 %d 个:" % len(st["missing"]))
        seen2 = set()
        for m in st["missing"]:
            k = m["t"] + str(m["rect"][:2])
            if k in seen2:
                continue
            seen2.add(k)
            print("  %-24r %s" % (m["t"][:24], m["rect"]))

        body = page.evaluate("() => document.body.innerText")
        print("\n全文（前 1500）:\n" + body[:1500])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "errors.png"), timeout_ms=20000)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
