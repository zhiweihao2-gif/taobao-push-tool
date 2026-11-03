"""验证：点击目标应该是 li 里的 p.text，而不是 li 本身。

线索：li 结构是
  <li class="category-item" title="...">
    <div class="pre-item"></div>
    <p class="text" data-spm-anchor-id="...">文本</p>
    <span class="icon-wrap"><i class="next-icon next-icon-arrow-right leaf-icon"></i></span>
  </li>
一级带 arrow-right 图标（可展开），二级 icon-wrap 为空（叶子）。
正常行为：点一级只展开（不该 selected），点叶子才 selected。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    BrowserSession, CATEGORY_ENTRY, click_text, human_pause,
)

_STATE = r"""
() => {
  const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').includes('确认'));
  const sel = [...document.querySelectorAll('li.category-item.selected')]
    .map(e => (e.innerText||'').trim());
  return {
    disabled: btn ? btn.disabled : null,
    selected: sel,
    treeItems: document.querySelectorAll('li.category-item').length,
    inputs: [...document.querySelectorAll('input')]
      .filter(e => e.getBoundingClientRect().width > 10)
      .map(e => ({ name: e.getAttribute('name'), ph: e.getAttribute('placeholder'), value: (e.value||'').slice(0,30) })),
  };
}
"""


def show(page, tag):
    st = page.evaluate(_STATE)
    print("  [%s] disabled=%s selected=%s tree=%d" % (
        tag, st["disabled"], st["selected"], st["treeItems"]))
    return st


def click_item_text(page, text):
    """点 li 里的 p.text（真正的点击目标）"""
    li = page.locator("li.category-item").filter(has_text=text)
    n = li.count()
    if n == 0:
        print("    找不到 li: %s" % text)
        return False
    # 精确匹配 title
    target = None
    for i in range(n):
        it = li.nth(i)
        if (it.get_attribute("title") or "") == text:
            target = it
            break
    if target is None:
        target = li.first
    p = target.locator("p.text")
    try:
        p.scroll_into_view_if_needed(timeout=4000)
        page.wait_for_timeout(250)
        p.click(timeout=6000)
        print("    点了 p.text: %s" % text)
        return True
    except Exception as exc:
        print("    p.text 点击失败(%s)，退回 li.click" % exc)
        try:
            target.click(timeout=6000)
            return True
        except Exception as exc2:
            print("    li 也失败: %s" % exc2)
            return False


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)

        print("[1] 打开类目引导页")
        if "category.htm" in page.url:
            print("    已在类目页，reload")
            try:
                page.reload(wait_until="commit", timeout=20000)
            except Exception as exc:
                print("    reload 失败(%s)，直接继续" % exc)
        else:
            try:
                page.goto(CATEGORY_ENTRY, wait_until="commit", timeout=25000)
            except Exception as exc:
                print("    goto 失败(%s)，直接继续" % exc)
        page.wait_for_timeout(4000)
        human_pause(1.0, 1.8)

        print("[2] 点「开启」")
        click_text(page, "开启", timeout_ms=6000)
        page.wait_for_timeout(3000)
        human_pause(0.8, 1.4)
        show(page, "开启后")

        print("[3] 点「选择类目」展开树")
        click_text(page, "选择类目", timeout_ms=6000)
        page.wait_for_timeout(3000)
        human_pause(0.6, 1.2)
        show(page, "展开后")

        print("[4] 点一级「电脑硬件/显示器/电脑周边」的 p.text")
        if not click_item_text(page, "电脑硬件/显示器/电脑周边"):
            return 1
        page.wait_for_timeout(2500)
        human_pause(0.5, 1.0)
        st = show(page, "点一级后")

        print("[5] 点叶子「键帽」的 p.text")
        if not click_item_text(page, "键帽"):
            return 1
        page.wait_for_timeout(2500)
        human_pause(0.5, 1.0)
        st = show(page, "点叶子后")

        if st["disabled"]:
            print("\n[..] 仍 disabled，填品牌+型号再试")
            sys.path.insert(0, str(ROOT / "scripts"))
            from goto_publish import _fill_brand, _fill_model  # type: ignore
            print("    品牌: %s" % _fill_brand(page, "无品牌/无注册商标"))
            page.wait_for_timeout(1500)
            print("    型号: %s" % _fill_model(page, "通用"))
            page.wait_for_timeout(1500)
            st = show(page, "填完后")

        if st["disabled"]:
            print("\n[XX] 还是 disabled")
            s.screenshot(str(ROOT / "data" / "publish_poc" / "ptext_disabled.png"))
            return 1

        print("\n[OK] 按钮 enabled！")
        s.screenshot(str(ROOT / "data" / "publish_poc" / "ptext_enabled.png"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
