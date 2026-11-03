# -*- coding: utf-8 -*-
"""点「提交宝贝信息」并校验成功页。

前面信封图标旁的 10 是站内消息数，不是错误数；「重要属性 4/4」已填满。
提交前再核对一遍关键字段，不全就中止。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

OUT = ROOT / "data" / "publish_poc"
SUCCESS_PATH = "/sell/v2/success.htm"


def main():
    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        page.evaluate("() => window.scrollTo(0, 0)")
        time.sleep(0.8)

        st = page.evaluate(r"""
        () => {
          const out = {};
          out.title = (document.querySelector('input[placeholder*="最多允许输入30"]') || {}).value || '';
          out.brand = (document.querySelector('input[placeholder="请输入"]') || {}).value || '';
          out.m1 = (document.querySelector('input[name="p-20000~1"]') || {}).value || '';
          out.m2 = (document.querySelector('input[name="p-656059583"]') || {}).value || '';
          out.imgs = document.querySelectorAll('[class*="sell-component-simply-images"] img').length;
          const trs = document.querySelectorAll('table tr');
          out.sku = Array.from(trs).map(tr =>
            Array.from(tr.querySelectorAll('input')).slice(0, 2).map(x => x.value));
          // 轴体
          for (const s of document.querySelectorAll('.next-select')) {
            const r = s.getBoundingClientRect();
            if (r.width < 40) continue;
            let label = '';
            let p = s.parentElement;
            for (let d = 0; d < 4 && p; d++) {
              for (const c of p.children) {
                if (c === s || c.contains(s)) break;
                const ct = (c.innerText || '').trim();
                if (ct && ct.length < 20) label = ct.replace(/\s+/g, ' ');
              }
              if (label) break;
              p = p.parentElement;
            }
            if (label === '适配轴体类型') out.axis = (s.innerText || '').trim().slice(0, 20);
          }
          const btn = [...document.querySelectorAll('button')].find(b =>
            (b.innerText || '').indexOf('提交宝贝信息') >= 0);
          out.submitDisabled = btn ? btn.disabled : null;
          out.submitRect = btn ? (() => { const r = btn.getBoundingClientRect();
            return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })() : null;
          return out;
        }""")
        print("\n=== 提交前核对 ===")
        for k, v in st.items():
            print("  %-15s = %r" % (k, v))

        problems = []
        if not st["title"]:
            problems.append("标题空")
        if not st["brand"]:
            problems.append("品牌空")
        if not st["m1"] or not st["m2"]:
            problems.append("型号空")
        if not st.get("axis"):
            problems.append("轴体空")
        if st["imgs"] < 3:
            problems.append("主图 %d 张 < 3" % st["imgs"])
        if not st["sku"] or any((not v or len(v) < 2 or not v[0]) for v in st["sku"]):
            problems.append("SKU 不完整")
        if st["submitDisabled"]:
            problems.append("提交按钮 disabled")

        if problems:
            print("\n[XX] 中止，问题: %s" % problems)
            return

        print("\n全部就绪，点提交…")
        try:
            btn = page.locator("button:has-text('提交宝贝信息')").first
            btn.scroll_into_view_if_needed(timeout=5000)
            time.sleep(0.4)
            btn.click(timeout=8000)
        except Exception as e:
            print("[!!] 点击失败: %s" % str(e).split("\n")[0][:120])
            return

        # 等跳转
        for i in range(40):
            page.wait_for_timeout(700)
            u = page.url
            if SUCCESS_PATH in u:
                print("\n[OK] 提交成功！")
                print("  URL: %s" % u)
                break
            if i % 5 == 4:
                print("  等待… %ds url=%s" % ((i + 1) * 7 // 10, u[:90]))
        else:
            print("\n[!!] 30 秒内没跳到成功页")
            print("  当前 URL: %s" % page.url)

        # 结果
        try:
            txt = page.evaluate("() => (document.body.innerText || '').slice(0, 500)")
            print("\n页面文本:\n%s" % txt)
        except Exception as e:
            print("[..] 读 body 失败（跳转中）: %s" % str(e).split("\n")[0][:100])

        # 提取 primaryId
        u = page.url
        if "primaryId=" in u:
            pid = u.split("primaryId=")[1].split("&")[0]
            print("\n>>> 新商品 ID = %s" % pid)
            print(">>> 商品页: https://item.taobao.com/item.htm?id=%s" % pid)
            (OUT / "last_publish_id.txt").write_text(pid, encoding="utf-8")

        p = OUT / "submitted.png"
        sess.screenshot(p, full_page=False)
        print("\n截图: %s" % p)


if __name__ == "__main__":
    main()
