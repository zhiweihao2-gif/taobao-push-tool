"""最小验证：搜索后不做任何类目树操作，「确认，下一步」是不是本来就 enabled。

假设：搜索「键帽」→ 平台直接给出类目路径 → 按钮立刻可点。
之前展开类目树反而把选中状态搞乱了。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    BrowserSession, CATEGORY_ENTRY, click_text, human_pause, human_type,
)

_BTN = r"""
() => {
  const out = [];
  document.querySelectorAll('button').forEach(b => {
    const r = b.getBoundingClientRect();
    out.push({ text: (b.innerText||'').trim(), disabled: b.disabled,
               rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  const sel = [...document.querySelectorAll('li.category-item.selected')]
    .map(e => (e.innerText||'').trim());
  return { btns: out, selected: sel, url: location.href };
}
"""


def main() -> int:
    kw = sys.argv[1] if len(sys.argv) > 1 else "键帽"

    with BrowserSession() as s:
        page = s.page()
        print("[1] 打开类目引导页")
        s.goto(CATEGORY_ENTRY)
        page.wait_for_timeout(2500)

        print("[2] 点第一个「开启」")
        click_text(page, "开启", timeout_ms=6000)
        page.wait_for_timeout(3000)
        human_pause(0.8, 1.4)

        st = page.evaluate(_BTN)
        print("    开启后 selected=%s" % st["selected"])
        print("    开启后按钮: %s" % [b for b in st["btns"] if "确认" in b["text"]])

        print("[3] 输入关键词并搜索")
        box = page.query_selector("input[placeholder*='可输入产品名称']")
        if box is None:
            print("    [XX] 找不到搜索框")
            return 1
        box.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        human_type(page, "input[placeholder*='可输入产品名称']", kw)
        page.wait_for_timeout(500)
        click_text(page, "搜索", timeout_ms=6000)
        page.wait_for_timeout(4000)
        human_pause(0.8, 1.4)

        st = page.evaluate(_BTN)
        print("\n[4] 搜索后状态")
        print("    URL: %s" % st["url"])
        print("    selected: %s" % st["selected"])
        for b in st["btns"]:
            print("    %s" % b)

        target = next((b for b in st["btns"] if "确认" in b["text"]), None)
        if target is None:
            print("\n[XX] 没有「确认，下一步」按钮")
            return 1

        if target["disabled"]:
            print("\n[XX] 搜索后按钮仍 disabled，看截图")
            s.screenshot(str(ROOT / "data" / "publish_poc" / "after_search_disabled.png"))
            return 1

        print("\n[OK] 搜索后按钮就是 enabled，直接点")
        click_text(page, "确认，下一步", timeout_ms=8000)
        page.wait_for_timeout(9000)
        human_pause(2.0, 3.0)

        st = page.evaluate(_BTN)
        print("    点后 URL: %s" % st["url"])
        s.screenshot(str(ROOT / "data" / "publish_poc" / "after_next.png"))
        body = page.evaluate(r"() => (document.body.innerText || '').slice(0, 1200)")
        print("\n-- 页面文字 --")
        print(body)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
