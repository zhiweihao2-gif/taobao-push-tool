# -*- coding: utf-8 -*-
"""补第 6 行数量 + 重填基础信息 + 上传主图 + 核对。

教训固化：
- 表格行必须在视口内：先 scroll_into_view_if_needed 再 click
- 价格显示格式会是 "39.00"，比较时按 Decimal 而非字符串
- reload 会清空标题/品牌/型号/轴体/主图，但 SKU 颜色存在草稿里
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from src.publish.browser_pub import BrowserSession, human_type  # noqa: E402
from src.core.storage import load_item  # noqa: E402

ITEM_ID = "902412139914"
OUT = ROOT / "data" / "publish_poc"

# (行索引, 颜色, 价格, 库存)
ROWS = [
    (0, "LOBO游戏机-灰色（透光）", "39", "200"),
    (1, "LOBO游戏机-粉色（透光）", "39", "0"),
    (2, "LOBO游戏机-蓝色（透光）", "39", "200"),
    (3, "LOBO游戏机-黄色（透光）", "39", "200"),
    (4, "第二弹-复古游戏机（透光）", "39", "200"),
    (5, "我全都要-4颗游戏机", "140", "34"),
]


def set_cell(page, row_idx, col_idx, text):
    """表格单元格：必须滚动到可见。"""
    tr = page.locator("table tr").nth(row_idx)
    tr.scroll_into_view_if_needed(timeout=6000)
    time.sleep(0.2)
    inp = tr.locator("input").nth(col_idx)
    inp.click(force=True, timeout=6000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(text, delay=35)
    time.sleep(0.3)
    return inp.input_value()


def read_sku_table(page):
    return page.evaluate("""() => {
      const trs = document.querySelectorAll('table tr');
      return Array.from(trs).map(tr =>
        Array.from(tr.querySelectorAll('input')).slice(0, 2).map(x => x.value));
    }""")


def read_totals(page):
    return page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('input')) {
        const r = el.getBoundingClientRect();
        if (r.width > 200 && r.width < 400) {
          out.push({y: Math.round(r.y), val: el.value});
        }
      }
      return out.sort((a, b) => a.y - b.y);
    }""")


def main():
    item = load_item(ITEM_ID)
    if not item:
        print("[XX] 找不到 item")
        return
    print("源商品: %s  价格 %.2f~%.2f  主图 %d"
          % (item.title[:30], item.price.min, item.price.max, len(item.main_images)))

    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        # ---------- 1. 补 SKU 数量 ----------
        print("\n" + "=" * 60)
        print("1. 核对/补齐 6 行 SKU")
        print("=" * 60)
        cur = read_sku_table(page)
        for i, v in enumerate(cur):
            print("  row[%d] %s" % (i, v))

        for idx, color, price, qty in ROWS:
            if idx >= len(cur):
                print("[!] 行%d 不存在" % (idx + 1))
                continue
            p, q = (cur[idx] + ["", ""])[:2]
            need_p = not p
            need_q = not q or q == "0" and qty != "0"
            if not need_p and not need_q:
                print("[--] 行%d 已完整 (%s/%s)，跳过" % (idx + 1, p, q))
                continue
            print("[>>] 行%d %s 补: 价格%s 数量%s" % (idx + 1, color[:20],
                                              "" if not need_p else "=" + price,
                                              "" if not need_q else "=" + qty))
            try:
                if need_p:
                    print("     价格回读 %r" % set_cell(page, idx, 0, price))
                if need_q:
                    print("     数量回读 %r" % set_cell(page, idx, 1, qty))
            except Exception as e:
                print("     [!!] %s" % str(e).split("\n")[0][:110])
            time.sleep(0.3)

        cur = read_sku_table(page)
        print("\n填完后:")
        for i, v in enumerate(cur):
            print("  row[%d] %s" % (i, v))
        print("一口价/总库存: %s" % read_totals(page))

        # ---------- 2. 重填基础信息 ----------
        print("\n" + "=" * 60)
        print("2. 重填基础信息")
        print("=" * 60)

        # 滚到顶部
        page.evaluate("() => window.scrollTo(0, 0)")
        time.sleep(0.6)

        # 标题
        title = item.title[:30]
        try:
            sel = 'input[placeholder="最多允许输入30个汉字（60字符）"]'
            human_type(page, sel, title)
            got = page.locator(sel).first.input_value()
            print("[%s] 标题: %r" % ("OK" if got else "!!", got))
        except Exception as e:
            print("[!!] 标题: %s" % str(e).split("\n")[0][:110])

        # 品牌
        try:
            from scripts.publish_one import pick_brand  # type: ignore
            ok = pick_brand(page, "无品牌")
            print("[%s] 品牌 pick_brand 返回 %s" % ("OK" if ok else "!!", ok))
        except Exception as e:
            print("[!!] 品牌: %s" % str(e).split("\n")[0][:110])

        # 型号1 / 型号2
        models = [("p-20000~1", "LOBO游戏机"), ("p-656059583", "个性键帽")]
        for name, val in models:
            try:
                loc = page.locator('input[name="%s"]' % name)
                if loc.count() == 0:
                    print("[!!] 型号 %s 不存在" % name)
                    continue
                loc.first.scroll_into_view_if_needed(timeout=5000)
                loc.first.click(force=True)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.type(val, delay=35)
                time.sleep(0.3)
                print("[%s] 型号 %s = %r" % ("OK" if loc.first.input_value() else "!!",
                                            name, loc.first.input_value()))
            except Exception as e:
                print("[!!] 型号 %s: %s" % (name, str(e).split("\n")[0][:110]))

        # 适配轴体类型
        try:
            from scripts.publish_one import pick_select_by_label  # type: ignore
            ok = pick_select_by_label(page, "适配轴体类型", "十字轴")
            print("[%s] 轴体 返回 %s" % ("OK" if ok else "!!", ok))
        except Exception as e:
            print("[!!] 轴体: %s" % str(e).split("\n")[0][:110])

        # ---------- 3. 状态汇总 ----------
        print("\n" + "=" * 60)
        print("3. 当前所有可见字段")
        print("=" * 60)
        fields = page.evaluate("""() => {
          const out = [];
          for (const el of document.querySelectorAll('input')) {
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            let label = '';
            let p = el.parentElement;
            for (let d = 0; d < 5 && p; d++) {
              const t = (p.innerText || '').trim();
              if (t && t.length < 50) { label = t.replace(/\\s+/g, ' '); break; }
              p = p.parentElement;
            }
            out.push({ph: (el.placeholder || '').slice(0, 20),
                      name: el.name || '',
                      val: String(el.value || '').slice(0, 24),
                      y: Math.round(r.y),
                      label: label.slice(0, 32)});
          }
          return out;
        }""")
        for f in fields:
            if f["y"] > 1350:
                continue
            print("  y=%-6d ph=%-20r name=%-14s val=%-24r | %s"
                  % (f["y"], f["ph"], f["name"], f["val"], f["label"]))

        # 错误
        errs = page.evaluate("""() => {
          const out = [];
          for (const el of document.querySelectorAll('[class*="error"], [class*="Error"], [class*="warning"]')) {
            const t = (el.innerText || '').trim();
            if (t && t.length < 90) out.push(t);
          }
          return [...new Set(out)].slice(0, 15);
        }""")
        print("\n错误/警告: %s" % errs)


if __name__ == "__main__":
    main()
