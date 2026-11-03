# -*- coding: utf-8 -*-
"""上传 5 张主图（复用 publish_one.upload_main_images）+ 填宝贝详情 + 最终核对。

前置：型号/标题/品牌/轴体/SKU 都已就绪（imgs=0, empties=17）。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.publish.browser_pub import BrowserSession  # noqa: E402
from src.core.storage import load_item  # noqa: E402
from publish_one import upload_main_images, fill_detail  # noqa: E402

ITEM_ID = "902412139914"


def build_detail_html(item) -> str:
    parts = []
    for u in item.detail_images:
        url = u.get("url") if isinstance(u, dict) else getattr(u, "url", None)
        if not url:
            continue
        parts.append('<p><img src="%s" style="width:100%%;display:block;"/></p>' % url)
    return "".join(parts)


def main():
    item = load_item(ITEM_ID)
    if not item:
        print("[XX] 找不到 item")
        return
    print("主图 %d 张，详情图 %d 张" % (len(item.main_images), len(item.detail_images)))

    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        # ---- 1. 主图 ----
        media_dir = ROOT / "data" / "media" / ITEM_ID
        files = sorted(media_dir.glob("main_*.jpg"))
        print("\n本地主图: %d 个" % len(files))
        for f in files:
            print("  %s (%d KB)" % (f.name, f.stat().st_size // 1024))

        print("\n" + "=" * 55)
        print("1. 上传主图")
        print("=" * 55)
        if not files:
            print("[XX] 没有主图文件")
        else:
            n = upload_main_images(page, files, want=5)
            print("\n上传成功 %d 张" % n)
            st = page.evaluate("""() => ({
              imgs: document.querySelectorAll('[class*="sell-component-simply-images"] img').length,
              empties: document.querySelectorAll('[class*="image-empty"]').length,
            })""")
            print("槽位状态: %s" % st)

        # ---- 2. 宝贝详情 ----
        print("\n" + "=" * 55)
        print("2. 宝贝详情")
        print("=" * 55)
        html = build_detail_html(item)
        if html:
            print("HTML 长度 %d" % len(html))
            ok = fill_detail(page, html)
            print("[%s] fill_detail 返回 %s" % ("OK" if ok else "!!", ok))
        else:
            print("[--] 没有详情图，跳过")

        # ---- 3. 最终核对 ----
        print("\n" + "=" * 55)
        print("3. 最终核对")
        print("=" * 55)
        page.evaluate("() => window.scrollTo(0, 0)")
        time.sleep(1.0)
        st = page.evaluate(r"""
        () => {
          const out = {};
          for (const n of ['p-20000~1', 'p-656059583']) {
            const el = document.querySelector('input[name="' + n + '"]');
            out[n] = el ? el.value : null;
          }
          out.title = (document.querySelector('input[placeholder*="最多允许输入30"]') || {}).value || '';
          out.brand = (document.querySelector('input[placeholder="请输入"]') || {}).value || '';
          out.imgs = document.querySelectorAll('[class*="sell-component-simply-images"] img').length;
          out.empties = document.querySelectorAll('[class*="image-empty"]').length;
          const trs = document.querySelectorAll('table tr');
          out.sku = Array.from(trs).map(tr =>
            Array.from(tr.querySelectorAll('input')).slice(0, 2).map(x => x.value));
          const body = document.body.innerText || '';
          const m = body.match(/错误\s*\((\d+)\)/);
          out.errCount = m ? parseInt(m[1]) : null;
          // 提交按钮
          const btn = [...document.querySelectorAll('button')].find(b =>
            (b.innerText || '').indexOf('提交宝贝信息') >= 0);
          out.submitDisabled = btn ? btn.disabled : null;
          // 详情
          out.detailImgs = document.querySelectorAll('[class*="sell-component-detail"] img, .cke_wysiwyg_div img').length;
          return out;
        }""")
        for k, v in st.items():
            print("  %-15s = %r" % (k, v))

        p = ROOT / "data" / "publish_poc" / "ready_to_submit.png"
        sess.screenshot(p, full_page=False)
        print("\n截图: %s" % p)


if __name__ == "__main__":
    main()
