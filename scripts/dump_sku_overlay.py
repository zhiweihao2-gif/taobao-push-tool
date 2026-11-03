# -*- coding: utf-8 -*-
"""dump SKU 行下拉 overlay 的真实 DOM 结构 —— 找出选项元素的真实 selector。

v2 的 MARK_OPTS 用 li/[role=option]/menu-item 命中 0 个，但 overlay.innerText 有内容，
说明选项是别的标签。
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

        # 展开第 0 行第 0 列（SKU分类）
        tr = page.locator("table tr").nth(0)
        sel = tr.locator(".next-select").nth(0)
        sel.scroll_into_view_if_needed(timeout=6000)
        page.wait_for_timeout(400)
        try:
            sel.click(position={"x": 8, "y": 18}, timeout=6000)
        except Exception:
            sel.click(force=True, timeout=6000)
        page.wait_for_timeout(2500)

        info = page.evaluate(r"""
        () => {
          const ovs = [...document.querySelectorAll('.next-overlay-wrapper')]
            .filter(o => o.classList.contains('opened'));
          const out = {nOv: ovs.length, details: []};
          if (!ovs.length) return out;
          const ov = ovs[ovs.length - 1];
          const r = ov.getBoundingClientRect();
          out.rect = [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)];
          out.innerHTML = (ov.innerHTML || '').slice(0, 3000);
          // 各种 selector 命中数
          const sels = ['li', '[role=option]', '[class*="menu-item"]', '[class*="option"]',
                        '[class*="select-item"]', '[class*="item"]', 'div[class]', 'span[class]'];
          out.counts = {};
          for (const s of sels) {
            try { out.counts[s] = ov.querySelectorAll(s).length; } catch (e) { out.counts[s] = -1; }
          }
          // 叶子节点（无元素子节点）且文本非空的元素
          const leaves = [];
          for (const el of ov.querySelectorAll('*')) {
            if (el.children.length) continue;
            const t = (el.innerText || '').trim();
            if (!t) continue;
            leaves.push({tag: el.tagName, cls: (el.className || '').toString().slice(0, 50),
                         t: t.slice(0, 20)});
          }
          out.leaves = leaves.slice(0, 20);
          return out;
        }""")
        print("\noverlay 数: %d  rect=%s" % (info["nOv"], info.get("rect")))
        print("selector 命中数: %s" % info.get("counts"))
        print("\n叶子节点 %d 个:" % len(info.get("leaves", [])))
        for l in info.get("leaves", []):
            print("  <%s class=%r> %r" % (l["tag"], l["cls"], l["t"]))
        print("\ninnerHTML (前 2500):\n%s" % info.get("innerHTML", "")[:2500])


if __name__ == "__main__":
    main()
