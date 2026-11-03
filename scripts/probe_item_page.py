"""用 Playwright 打开淘宝商品页，直接从页面全局变量里抠数据。

不走 mtop 接口，所以不受 mtop 的限速/冷却约束；代价是慢（一次 10~20 秒）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.session import SessionStore  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

PROBE_JS = r"""
() => {
  const out = {keys: [], initData: null, itemData: null, title: document.title};
  // 常见全局变量
  const names = ['__INIT_DATA__', '__INITIAL_STATE__', '_DATA_', 'g_page_config',
                 'TShop', '__NEXT_DATA__', 'window.__INIT_DATA__'];
  for (const n of names) {
    try { if (window[n]) out.keys.push(n); } catch (e) {}
  }
  try { if (window.__INIT_DATA__) out.initData = JSON.stringify(window.__INIT_DATA__).slice(0, 200); } catch (e) {}
  // TShop.Setup 参数
  try {
    out.hasTShop = typeof TShop !== 'undefined';
    if (out.hasTShop && TShop.Setup) out.tshop = 'has Setup';
  } catch (e) {}
  // 从 DOM 抓兜底信息
  const t = document.querySelector('h1, .tb-main-title, [class*="Title--"]');
  out.domTitle = t ? t.innerText.trim().slice(0, 80) : '';
  const imgs = Array.from(document.images).map(i => i.src).filter(s => s.includes('alicdn'));
  out.imgCount = imgs.length;
  out.images = imgs.slice(0, 8);
  out.bodyLen = (document.body && document.body.innerText || '').length;
  out.bodyHead = (document.body && document.body.innerText || '').slice(0, 200);
  return out;
}
"""


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/probe_item_page.py <item_id>")
        return 2
    item_id = sys.argv[1].strip()

    st = SessionStore()
    ck_text = st.cookie_header("taobao")
    if not ck_text:
        print("[!] 没有 taobao cookie")
        return 1
    cookies = []
    for chunk in ck_text.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            cookies.append({"name": k.strip(), "value": v.strip(),
                            "domain": ".taobao.com", "path": "/"})

    url = f"https://item.taobao.com/item.htm?id={item_id}"
    print("打开 %s" % url)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
            locale="zh-CN",
        )
        ctx.add_cookies(cookies)
        p = ctx.new_page()
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("[!] goto: %s" % str(e).split("\n")[0][:120])
        p.wait_for_timeout(3500)
        print("落地 URL: %s" % p.url)
        print("标题: %s" % p.title())
        try:
            info = p.evaluate(PROBE_JS)
        except Exception as e:
            print("[!] evaluate: %s" % str(e)[:150])
            info = {}
        print(json.dumps(info, ensure_ascii=False, indent=2)[:2500])

        out = ROOT / "data" / "publish_poc" / f"page_probe_{item_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n写到 %s" % out)
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
