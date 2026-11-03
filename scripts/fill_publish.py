"""在发布页填标题 / 一口价 / 总库存，然后截图核对，不提交。

发布页的 input 全都没有 id/name，先给每个可见 input 打上 data-dsh-idx 标记，
后续用 [data-dsh-idx="N"] 精确定位（比 nth() 稳，因为 nth 索引含隐藏元素）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_pause, human_type  # noqa: E402

_MARK_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  function labelOf(el) {
    let cur = el.parentElement;
    for (let d = 0; d < 4 && cur; d++) {
      const c = cur.cloneNode(true);
      c.querySelectorAll('input,textarea,select,button,script,style').forEach(n => n.remove());
      const t = clean(c.textContent);
      if (t) return t.slice(0, 40);
      cur = cur.parentElement;
    }
    return '';
  }
  const out = [];
  let n = 0;
  document.querySelectorAll('input,textarea').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) return;
    const cs = window.getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    el.setAttribute('data-dsh-idx', String(n));
    out.push({
      idx: n,
      tag: el.tagName,
      ph: el.getAttribute('placeholder') || '',
      value: (el.value || '').slice(0, 30),
      label: labelOf(el),
    });
    n++;
  });
  return out;
}
"""


def find(fields, *, label=None, ph=None, label_contains=None):
    for f in fields:
        if label is not None and f["label"] != label:
            continue
        if ph is not None and f["ph"] != ph:
            continue
        if label_contains is not None and label_contains not in f["label"]:
            continue
        return f
    return None


def fill(page, f, text, tag="") -> bool:
    sel = '[data-dsh-idx="%d"]' % f["idx"]
    try:
        loc = page.locator(sel)
        loc.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(300)
        loc.click(timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.wait_for_timeout(150)
        # 真实键盘输入：直接 set value 不会触发 React 的 onChange
        for ch in text:
            page.keyboard.type(ch)
            page.wait_for_timeout(20)
        page.wait_for_timeout(400)
        got = loc.input_value()
        print("    [%s] %s -> %r (回读 %r)" % (tag, f["label"] or f["ph"], text, got))
        return got == text
    except Exception as exc:
        print("    [%s] 失败: %s" % (tag, exc))
        return False


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "机械键盘键帽 PBT 热升华 客制化 61键 适配Cherry轴"
    price = sys.argv[2] if len(sys.argv) > 2 else "39.9"
    stock = sys.argv[3] if len(sys.argv) > 3 else "200"

    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)
        if "publish.htm" not in page.url:
            print("[XX] 不在发布页，先跑 goto_publish.py")
            return 1

        print("[1] 标记字段")
        fields = page.evaluate(_MARK_JS)
        print("    可见字段 %d 个" % len(fields))

        print("[2] 填宝贝标题")
        f = find(fields, ph="最多允许输入30个汉字（60字符）")
        if f is None:
            print("    [XX] 找不到标题框")
            return 1
        fill(page, f, title[:60], "标题")
        human_pause(0.5, 1.0)

        print("[3] 填一口价")
        f = find(fields, label="元")
        if f is None:
            print("    [XX] 找不到一口价")
            return 1
        fill(page, f, price, "价格")
        human_pause(0.5, 1.0)

        print("[4] 填总库存")
        f = find(fields, label="件")
        if f is None:
            print("    [XX] 找不到库存")
            return 1
        fill(page, f, stock, "库存")
        human_pause(0.5, 1.0)

        print("[5] 重新标记并回读全部字段值")
        page.evaluate(r"() => document.querySelectorAll('[data-dsh-idx]').forEach(e => e.removeAttribute('data-dsh-idx'))")
        fields = page.evaluate(_MARK_JS)
        for f in fields:
            if f["value"]:
                print("    idx=%-2d %-24r = %r" % (f["idx"], (f["label"] or f["ph"])[:22], f["value"]))

        print("[6] 截图")
        shot = ROOT / "data" / "publish_poc" / "filled.png"
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(800)
        s.screenshot(str(shot), full_page=False)
        print("    " + str(shot))

        # 检查提交按钮状态
        btn = page.evaluate(
            r"""
            () => {
              const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').includes('提交宝贝信息'));
              return b ? { text: (b.innerText||'').trim(), disabled: b.disabled } : null;
            }
            """
        )
        print("[7] 提交按钮: %s" % btn)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
