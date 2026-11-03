"""按用户描述的正确交互填颜色：填完当前位 → 点 add → 新位置出现在末尾 → 填新位置。

关键纠正（之前一直填错）：
  - `ul.sell-color-item-container` 里的 li 会随每次 add **增多**，新 li 在末尾。
  - 必须填 **最后一个** `input[placeholder="主色(必选)"]`，填 .first 就是覆盖第一项。
  - 「颜色分类(N)」里的 N 是**属性个数**（恒为 1），不是值的个数 → 判据用 li 数 / 非空 input 数。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

COLORS = [
    "LOBO游戏机-灰色（透光）",
    "LOBO游戏机-粉色（透光）",
    "LOBO游戏机-蓝色（透光）",
    "LOBO游戏机-黄色（透光）",
    "第二弹-复古游戏机（透光）",
    "我全都要-4颗游戏机",
]

_STATE = r"""
() => {
  const ul = document.querySelector('.sell-color-item-container');
  if (!ul) return { lis: 0, vals: [], filled: 0 };
  const vals = [...ul.querySelectorAll('input')].map(i => i.value || '');
  return { lis: ul.querySelectorAll('li').length, vals,
           filled: vals.filter(v => v.trim()).length };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(600)

        print("[reload 清空状态]", flush=True)
        page.evaluate("() => location.reload()")
        page.wait_for_timeout(6500)

        print("[开抽屉]", flush=True)
        try:
            b = page.locator("button").filter(has_text="创建规格").first
            b.scroll_into_view_if_needed(timeout=8000)
            page.wait_for_timeout(500)
            b.click(timeout=6000)
            page.wait_for_timeout(3200)
        except Exception as e:
            print("  [XX] %s" % str(e).split("\n")[0][:110], flush=True)
            return 1

        st = page.evaluate(_STATE)
        print("  初始 lis=%d filled=%d vals=%s" % (st["lis"], st["filled"], st["vals"]), flush=True)

        for i, c in enumerate(COLORS, 1):
            before = page.evaluate(_STATE)
            try:
                # 关键：填最后一个输入位
                inp = page.get_by_placeholder("主色(必选)").last
                inp.click(timeout=6000, force=True)
                page.wait_for_timeout(220)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.wait_for_timeout(150)
                page.keyboard.type(c, delay=35)
                page.wait_for_timeout(450)
            except Exception as e:
                print("  %d [XX] 输入 %s" % (i, str(e).split("\n")[0][:90]), flush=True)
                continue

            mid = page.evaluate(_STATE)
            try:
                page.locator("button.add").first.click(timeout=6000, force=True)
            except Exception as e:
                print("  %d [XX] 点+ %s" % (i, str(e).split("\n")[0][:90]), flush=True)
                continue
            page.wait_for_timeout(1200)

            after = page.evaluate(_STATE)
            ok = after["filled"] > before["filled"]
            print("  %d/%d [%s] %-28s  lis %d→%d  filled %d→%d"
                  % (i, len(COLORS), "OK" if ok else "!!", c[:28],
                     before["lis"], after["lis"], before["filled"], after["filled"]), flush=True)

        fin = page.evaluate(_STATE)
        print("\n[最终] lis=%d filled=%d" % (fin["lis"], fin["filled"]), flush=True)
        print(json.dumps(fin["vals"], ensure_ascii=False), flush=True)

        try:
            page.screenshot(path=str(ROOT / "data" / "publish_poc" / "sku_colors.png"), timeout=25000)
            print("  截图 ok", flush=True)
        except Exception as e:
            print("  [!] %s" % str(e)[:60], flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
