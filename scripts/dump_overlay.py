# -*- coding: utf-8 -*-
"""轴体下拉已能展开（overlay=1），但选项选择器匹配 0 个 —— dump overlay 真实结构。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402


def main():
    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        # 确保已打标记并展开
        m = page.evaluate(r"""
        () => {
          for (const s of document.querySelectorAll('.next-select')) {
            const r = s.getBoundingClientRect();
            if (r.width < 40 || r.height < 20) continue;
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
            if (label === '适配轴体类型') {
              s.setAttribute('data-dsh-axle-sel', '1');
              return {ok: true};
            }
          }
          return {ok: false};
        }""")
        print("标记: %s" % m)

        sel = page.locator('[data-dsh-axle-sel="1"]')
        if sel.count() == 0:
            print("[XX] count 0")
            return
        sel.first.scroll_into_view_if_needed(timeout=8000)
        time.sleep(0.5)
        sel.first.click(timeout=6000)
        time.sleep(1.2)

        # dump overlay
        info = page.evaluate(r"""
        () => {
          const out = [];
          for (const ov of document.querySelectorAll('.next-overlay-wrapper')) {
            const r = ov.getBoundingClientRect();
            out.push({
              cls: ov.className,
              rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
              text: (ov.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 300),
              htmlLen: (ov.innerHTML || '').length,
              htmlHead: (ov.innerHTML || '').slice(0, 1500),
              children: Array.from(ov.children).map(c => ({
                tag: c.tagName,
                cls: (c.className || '').toString().slice(0, 80),
                n: c.children.length,
              })),
            });
          }
          return out;
        }""")
        print("\n所有 .next-overlay-wrapper: %d 个" % len(info))
        for i, ov in enumerate(info):
            print("\n--- overlay[%d] ---" % i)
            print("  cls: %s" % ov["cls"][:100])
            print("  rect: %s" % ov["rect"])
            print("  text: %r" % ov["text"][:250])
            print("  htmlLen: %d" % ov["htmlLen"])
            print("  children: %s" % ov["children"])
            print("  htmlHead:\n%s" % ov["htmlHead"][:1200])

        # 直接在页面上找含「十字轴」的元素
        found = page.evaluate(r"""
        () => {
          const out = [];
          for (const el of document.querySelectorAll('*')) {
            if (el.children.length > 3) continue;
            const t = (el.innerText || '').trim();
            if (t === '十字轴' || t === '火山口' || t === '矮轴' || t === '通用' || t === '按键+底包') {
              const r = el.getBoundingClientRect();
              out.push({
                tag: el.tagName,
                cls: (el.className || '').toString().slice(0, 60),
                text: t,
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                visible: r.width > 2 && r.height > 2,
              });
            }
          }
          return out;
        }""")
        print("\n含轴体选项文本的元素: %d 个" % len(found))
        for f in found:
            print("  %s .%s %r %s vis=%s"
                  % (f["tag"], f["cls"][:40], f["text"], f["rect"], f["visible"]))


if __name__ == "__main__":
    main()
