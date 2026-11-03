"""滚到 SKU 表格，dump 6 行结构（价格/数量输入框坐标）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_DUMP = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const t = clean(document.body.innerText);
  // 直接找所有 placeholder 为「价格」「数量」的 input —— SKU 行的价格/数量框
  const inputs = [...document.querySelectorAll('input')].map(i => {
    const r = i.getBoundingClientRect();
    return { ph: i.placeholder || '', v: i.value || '',
             rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
  });
  const priceLike = inputs.filter(x => ['价格', '数量'].includes(x.ph) && x.rect[2] > 40);
  // SKU 行：包含「元」和「件」的最小行容器
  const rows = [];
  document.querySelectorAll('div,tr').forEach(el => {
    const et = clean(el.innerText);
    if (!et || et.length > 120 || et.length < 4) return;
    const ins = [...el.querySelectorAll('input')].filter(i => {
      const p = i.placeholder || '';
      return p === '价格' || p === '数量';
    });
    if (ins.length < 2) return;
    const r = el.getBoundingClientRect();
    if (r.height > 90) return;
    rows.push({ cls: (el.className || '').toString().slice(0, 50),
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                txt: et.slice(0, 60),
                price: ins.find(i => i.placeholder === '价格') ? (() => {
                  const q = ins.find(i => i.placeholder === '价格').getBoundingClientRect();
                  return [Math.round(q.x), Math.round(q.y), Math.round(q.width), Math.round(q.height)];
                })() : null,
                qty: ins.find(i => i.placeholder === '数量') ? (() => {
                  const q = ins.find(i => i.placeholder === '数量').getBoundingClientRect();
                  return [Math.round(q.x), Math.round(q.y), Math.round(q.width), Math.round(q.height)];
                })() : null });
  });
  const seen = new Set(); const uniq = [];
  rows.forEach(r => { const k = r.rect[1] + ':' + r.rect[0]; if (!seen.has(k)) { seen.add(k); uniq.push(r); } });
  return { scrollY: Math.round(window.scrollY), priceLike, rows: uniq.slice(0, 12),
           hasMode: t.includes('SKU 模式') };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)

        print("[滚到销售规格]", flush=True)
        page.evaluate(
            """() => {
              const t = document.body.innerText || '';
              const i = t.indexOf('销售规格');
              if (i >= 0) window.scrollTo(0, Math.max(0, i / t.length * document.body.scrollHeight - 300));
            }"""
        )
        page.wait_for_timeout(1200)

        info = page.evaluate(_DUMP)
        print(json.dumps(info, ensure_ascii=False, indent=1)[:4000], flush=True)

        (ROOT / "data" / "publish_poc" / "sku_rows.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            page.screenshot(path=str(ROOT / "data" / "publish_poc" / "sku_rows.png"), timeout=25000)
            print("  截图 ok", flush=True)
        except Exception as e:
            print("  [!] %s" % str(e)[:60], flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
