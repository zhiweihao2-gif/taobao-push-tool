# -*- coding: utf-8 -*-
"""补最后三个：型号1(p-20000~1) / 型号2(p-656059583) / 适配轴体类型。

型号用 JS native setter（键盘输入被 React 重置）。
轴体按上次成功的方式：找容器点左边缘 x+8 展开，再标记选项点击。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

MODELS = [
    ("p-20000~1", "LOBO游戏机"),
    ("p-656059583", "个性键帽"),
]
AXLE = "十字轴"

# 按 name 精确定位 input，用原生 setter 写入
SET_BY_NAME = r"""
(args) => {
  const [name, value] = args;
  const el = document.querySelector('input[name="' + name + '"]');
  if (!el) return {ok: false, err: 'not found'};
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new Event('blur', {bubbles: true}));
  return {ok: true, val: el.value, ro: el.readOnly, dis: el.disabled};
}
"""

# 从 label 文本找到「适配轴体类型」对应的 .next-select 容器
LOCATE_SELECT = r"""
(labelText) => {
  // 找到文本完全等于 labelText 的叶节点
  let labelEl = null;
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    if ((el.innerText || '').trim() === labelText) { labelEl = el; break; }
  }
  if (!labelEl) return {ok: false, err: 'label not found'};
  // 向上 9 层找 .next-select 容器
  let cur = labelEl;
  for (let i = 0; i < 9 && cur; i++) {
    if (cur.classList && cur.classList.contains('next-select')) {
      const inp = cur.querySelector('input[role="combobox"]');
      const r = cur.getBoundingClientRect();
      if (inp) {
        inp.setAttribute('data-dsh-axle-input', '1');
        return {ok: true,
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
                inpRect: (() => { const q = inp.getBoundingClientRect();
                                  return [Math.round(q.x), Math.round(q.y), Math.round(q.width), Math.round(q.height)]; })(),
                cls: cur.className.slice(0, 80)};
      }
    }
    cur = cur.parentElement;
  }
  return {ok: false, err: 'no next-select ancestor'};
}
"""

# 标记展开后 overlay 里的选项
MARK_OPTS = r"""
() => {
  const ovs = document.querySelectorAll('.next-overlay-wrapper.opened');
  if (!ovs.length) return {ok: false, err: 'no opened overlay', count: 0};
  const ov = ovs[ovs.length - 1];
  const nodes = ov.querySelectorAll('li, [role="option"], [class*="menu-item"]');
  const out = [];
  let i = 0;
  for (const n of nodes) {
    const t = (n.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t) continue;
    n.setAttribute('data-dsh-opt', String(i));
    out.push({i: i, t: t.slice(0, 30)});
    i++;
  }
  return {ok: true, count: i, opts: out};
}
"""


def main():
    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        page.evaluate("() => window.scrollTo(0, 0)")
        time.sleep(0.5)

        # ---------- 1. 型号 ----------
        print("\n" + "=" * 55)
        print("1. 型号")
        print("=" * 55)
        for name, val in MODELS:
            r = page.evaluate(SET_BY_NAME, [name, val])
            print("  %s -> %s" % (name, r))
            time.sleep(0.3)

        # 复核
        got = page.evaluate("""() => {
          const out = {};
          for (const n of ['p-20000~1', 'p-656059583']) {
            const el = document.querySelector('input[name="' + n + '"]');
            out[n] = el ? el.value : null;
          }
          return out;
        }""")
        print("  复核: %s" % got)

        # ---------- 2. 适配轴体类型 ----------
        print("\n" + "=" * 55)
        print("2. 适配轴体类型")
        print("=" * 55)
        loc = page.evaluate(LOCATE_SELECT, "适配轴体类型")
        print("  定位: %s" % loc)
        if not loc.get("ok"):
            print("  [XX] 定位失败")
        else:
            x, y, w, h = loc["rect"]
            # 先试着点左边缘
            for dx, tag in ((8, "left+8"), (w // 2, "center"), (w - 30, "right-30")):
                page.mouse.click(x + dx, y + h // 2)
                time.sleep(0.8)
                expanded = page.evaluate("""() => {
                  const el = document.querySelector('[data-dsh-axle-input="1"]');
                  return el ? el.getAttribute('aria-expanded') : null;
                }""")
                print("    点 %s(%d,%d) -> aria-expanded=%s" % (tag, x + dx, y + h // 2, expanded))
                if expanded == "true":
                    break
                page.keyboard.press("Escape")
                time.sleep(0.3)

            opts = page.evaluate(MARK_OPTS)
            print("  选项: count=%s err=%s" % (opts.get("count"), opts.get("err")))
            for o in opts.get("opts", []):
                print("    [%d] %r" % (o["i"], o["t"]))

            # 找「十字轴」
            target = None
            for o in opts.get("opts", []):
                if o["t"] == AXLE or o["t"].startswith(AXLE):
                    target = o["i"]
                    break
            if target is None and opts.get("opts"):
                target = opts["opts"][0]["i"]
                print("  [!] 没找到精确「%s」，退回第一个" % AXLE)

            if target is not None:
                try:
                    page.locator('[data-dsh-opt="%d"]' % target).first.click(force=True, timeout=5000)
                    time.sleep(0.6)
                    print("  [OK] 点了选项 %d" % target)
                except Exception as e:
                    print("  [!!] 点选项失败: %s" % str(e).split("\n")[0][:110])

        # ---------- 3. 让总库存重算 ----------
        print("\n" + "=" * 55)
        print("3. 触发重算（点空白 + 轻微滚动）")
        print("=" * 55)
        page.evaluate("() => window.scrollTo(0, 600)")
        time.sleep(0.6)
        try:
            page.mouse.click(20, 500)
        except Exception:
            pass
        time.sleep(1.0)

        # ---------- 4. 最终状态 ----------
        print("\n" + "=" * 55)
        print("4. 最终状态")
        print("=" * 55)
        final = page.evaluate("""() => {
          const out = {models: {}, axis: '', sku: [], totals: []};
          for (const n of ['p-20000~1', 'p-656059583']) {
            const el = document.querySelector('input[name="' + n + '"]');
            out.models[n] = el ? el.value : null;
          }
          const ax = document.querySelector('[data-dsh-axle-input="1"]');
          out.axis = ax ? ax.value : '(no marker)';
          // 轴体容器文本
          const all = document.querySelectorAll('.next-select');
          for (const s of all) {
            const t = (s.innerText || '').trim();
            if (t && t.length < 20 && t.indexOf('轴') >= 0) { out.axisText = t; break; }
          }
          const trs = document.querySelectorAll('table tr');
          out.sku = Array.from(trs).map(tr =>
            Array.from(tr.querySelectorAll('input')).slice(0, 2).map(x => x.value));
          for (const el of document.querySelectorAll('input')) {
            const r = el.getBoundingClientRect();
            if (r.width > 200 && r.width < 400) out.totals.push({y: Math.round(r.y), val: el.value});
          }
          out.totals.sort((a, b) => a.y - b.y);
          return out;
        }""")
        print("  型号: %s" % final["models"])
        print("  轴体 input=%r  容器文本=%r" % (final["axis"], final.get("axisText")))
        print("  SKU:")
        for i, v in enumerate(final["sku"]):
            print("    row[%d] %s" % (i, v))
        print("  一口价/总库存: %s" % final["totals"])

        # 标题
        t = page.locator('input[placeholder="最多允许输入30个汉字（60字符）"]').first.input_value()
        print("  标题: %r" % t)

        # 剩余错误
        errs = page.evaluate("""() => {
          const out = [];
          for (const el of document.querySelectorAll('[class*="error"], [class*="Error"]')) {
            const t = (el.innerText || '').trim();
            if (t && t.length < 90) out.push(t);
          }
          return [...new Set(out)].slice(0, 15);
        }""")
        print("  错误: %s" % errs)

        p = ROOT / "data" / "publish_poc" / "final_state.png"
        sess.screenshot(p, full_page=False)
        print("\n截图: %s" % p)


if __name__ == "__main__":
    main()
