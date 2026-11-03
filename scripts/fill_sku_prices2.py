# -*- coding: utf-8 -*-
"""补第 6 行 SKU（坐标法在末行偏移失效），并核对一口价/总库存。

改用 Playwright table tr locator 定位，彻底避开坐标漂移。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

# 行索引 → (颜色, 价格, 库存)
ROWS = [
    (0, "LOBO游戏机-灰色（透光）", "39", "200"),
    (1, "LOBO游戏机-粉色（透光）", "39", "0"),
    (2, "LOBO游戏机-蓝色（透光）", "39", "200"),
    (3, "LOBO游戏机-黄色（透光）", "39", "200"),
    (4, "第二弹-复古游戏机（透光）", "39", "200"),
    (5, "我全都要-4颗游戏机", "140", "34"),
]


def fill_cell(page, row_idx, col_idx, text):
    """用 table tr > input 定位，col_idx: 0=价格 1=数量。"""
    tr = page.locator("table tr").nth(row_idx)
    inp = tr.locator("input").nth(col_idx)
    inp.scroll_into_view_if_needed(timeout=5000)
    inp.click(force=True, timeout=5000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(text, delay=30)
    time.sleep(0.25)
    return inp.input_value()


def read_all(page):
    return page.evaluate("""() => {
      const trs = document.querySelectorAll('table tr');
      const rows = Array.from(trs).map(tr =>
        Array.from(tr.querySelectorAll('input')).slice(0, 2).map(x => x.value));
      // 一口价/总库存：找 y>820 且宽>200 的无 placeholder input
      const big = [];
      for (const el of document.querySelectorAll('input')) {
        const r = el.getBoundingClientRect();
        if (r.width > 200 && r.y > 800 && r.y < 1050) {
          big.push({rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                    val: el.value});
        }
      }
      return {rows: rows, priceStock: big};
    }""")


def main():
    with BrowserSession() as sess:
        page = sess.page()
        print("当前页: " + page.url)

        before = read_all(page)
        print("\n填之前:")
        for i, v in enumerate(before["rows"]):
            print("  row[%d] %s" % (i, v))
        print("  一口价/总库存候选: %s" % before["priceStock"])

        # 只补空的那种行（幂等：已有值的跳过）
        for idx, color, price, qty in ROWS:
            cur = before["rows"][idx] if idx < len(before["rows"]) else []
            if len(cur) >= 2 and cur[0] and cur[1]:
                print("\n[--] 行%d 已有值 %s，跳过" % (idx + 1, cur))
                continue
            print("\n[>>] 填行%d %s" % (idx + 1, color))
            try:
                v0 = fill_cell(page, idx, 0, price)
                v1 = fill_cell(page, idx, 1, qty)
                print("     回读: 价格=%r 数量=%r" % (v0, v1))
            except Exception as e:
                print("     [!!] %s" % str(e).split("\n")[0][:120])
            time.sleep(0.4)

        after = read_all(page)
        print("\n" + "=" * 60)
        print("填之后:")
        for i, v in enumerate(after["rows"]):
            print("  row[%d] %s" % (i, v))
        print("  一口价/总库存: %s" % after["priceStock"])

        # 滚动到销售信息区截图
        try:
            page.evaluate("() => window.scrollTo(0, 300)")
            time.sleep(0.5)
        except Exception:
            pass
        p = ROOT / "data" / "publish_poc" / "sku_filled2.png"
        sess.screenshot(p, full_page=False)
        print("\n截图: %s" % p)


if __name__ == "__main__":
    main()
