# -*- coding: utf-8 -*-
"""补第 1 张主图（上轮 iframe 没及时出现）。复用 upload_main2.upload_one。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.publish.browser_pub import BrowserSession  # noqa: E402
from upload_main2 import upload_one  # noqa: E402

ITEM_ID = "902412139914"
MEDIA = ROOT / "data" / "media" / ITEM_ID
OUT = ROOT / "data" / "publish_poc"

# 当前已回填的 4 张（从日志）
DONE = {"main_45d9a31d6636cfd8.jpg", "main_89e28247a470df99.jpg",
        "main_e6ac114a43ea36d9.jpg", "main_f075b1e97d0412d0.jpg"}


def main():
    files = [f for f in sorted(MEDIA.glob("main_*.jpg")) if f.name not in DONE]
    print("待补: %s" % [f.name for f in files])

    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        st = page.evaluate("""() => ({
          imgs: document.querySelectorAll('[class*="sell-component-simply-images"] img').length,
          empties: document.querySelectorAll('div.image-empty').length,
        })""")
        print("当前槽位: %s" % st)

        for f in files:
            print("\n[补] %s" % f.name)
            for attempt in (1, 2):
                try:
                    ok = upload_one(page, f)
                    if ok:
                        print("  => OK (attempt %d)" % attempt)
                        break
                    print("  => 未生效 (attempt %d)" % attempt)
                except Exception as e:
                    print("  [!!] %s" % str(e).split("\n")[0][:120])
                time.sleep(1.5)

        print("\n" + "=" * 55)
        st = page.evaluate("""() => ({
          imgs: document.querySelectorAll('[class*="sell-component-simply-images"] img').length,
          empties: document.querySelectorAll('div.image-empty').length,
        })""")
        print("最终槽位: %s" % st)

        # 全局核对
        full = page.evaluate(r"""
        () => {
          const out = {};
          out.title = (document.querySelector('input[placeholder*="最多允许输入30"]') || {}).value || '';
          out.brand = (document.querySelector('input[placeholder="请输入"]') || {}).value || '';
          out.m1 = (document.querySelector('input[name="p-20000~1"]') || {}).value || '';
          out.m2 = (document.querySelector('input[name="p-656059583"]') || {}).value || '';
          const trs = document.querySelectorAll('table tr');
          out.sku = Array.from(trs).map(tr =>
            Array.from(tr.querySelectorAll('input')).slice(0, 2).map(x => x.value));
          const body = document.body.innerText || '';
          const m = body.match(/错误\s*\((\d+)\)/);
          out.errCount = m ? parseInt(m[1]) : null;
          const btn = [...document.querySelectorAll('button')].find(b =>
            (b.innerText || '').indexOf('提交宝贝信息') >= 0);
          out.submitDisabled = btn ? btn.disabled : null;
          return out;
        }""")
        print("\n=== 最终状态 ===")
        for k, v in full.items():
            print("  %-15s = %r" % (k, v))

        p = OUT / "all_main_done.png"
        sess.screenshot(p, full_page=False)
        print("截图: %s" % p)


if __name__ == "__main__":
    main()
