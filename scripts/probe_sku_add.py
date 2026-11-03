"""最小复现：为什么 6 个颜色只进 3 个（严格交替 OK/!!）。

假设 A：点「+」后输入框进 disabled/readonly，下一轮 type 打不进去。
假设 B：点「+」后输入框 DOM 被替换，Playwright 的 .first 命中了隐藏副本。
假设 C：点「+」的坐标在第二轮落到别的地方（比如弹层）。
假设 D：添加有速率限制，第二次必须等更久。

每步打印：输入框 value / disabled / readonly / visible / rect / 元素句柄数量，
以及「+」点击前后 draft 列表长度。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

COLORS = ["灰色A", "粉色B", "蓝色C", "黄色D"]

_PROBE = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body.innerText);
  // 抽屉面板：含「选择展示模式」和「确认创建」的最深容器
  let panel = null;
  document.querySelectorAll('div').forEach(el => {
    if (panel) return;
    const t = clean(el.innerText);
    if (t.includes('选择展示模式') && t.includes('确认创建')) panel = el;
  });
  const scope = panel || document;
  const inputs = [...scope.querySelectorAll('input')].map((i, idx) => {
    const r = i.getBoundingClientRect();
    const cs = getComputedStyle(i);
    return { idx, ph: i.placeholder || '', val: (i.value || '').slice(0, 20),
             dis: i.disabled, ro: i.readOnly,
             vis: r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden',
             rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
  });
  // 已加进去的 draft chip：抽屉里「颜色分类(n)」的 n
  const m = body.match(/颜色分类\s*[（(](\d+)[）)]/);
  return { hasPanel: !!panel,
           count: m ? Number(m[1]) : null,
           inputs,
           plusBtns: [...scope.querySelectorAll('button')]
             .filter(b => !clean(b.innerText))
             .map(b => { const r = b.getBoundingClientRect();
                         return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; })
             .filter(r => r[2] > 20 && r[2] < 60 && r[3] > 20 && r[3] < 60) };
}
"""


def log(m: str) -> None:
    print(m, flush=True)


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(900)

        # 关掉可能残留的抽屉
        for _ in range(2):
            if not page.evaluate("() => (document.body.innerText||'').includes('选择展示模式')"):
                break
            try:
                page.get_by_text("取消", exact=True).last.click(force=True, timeout=3000)
                page.wait_for_timeout(1200)
            except Exception:
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)

        log("[开抽屉]")
        try:
            b = page.locator("button").filter(has_text="创建规格").first
            b.scroll_into_view_if_needed(timeout=8000)
            page.wait_for_timeout(500)
            b.click(timeout=6000)
            page.wait_for_timeout(3000)
        except Exception as e:
            log("  [XX] %s" % str(e).split("\n")[0][:110])
        st = page.evaluate(_PROBE)
        log("  hasPanel=%s  count=%s  inputs=%d  plus=%d"
            % (st["hasPanel"], st["count"], len(st["inputs"]), len(st["plusBtns"])))

        for i, c in enumerate(COLORS, 1):
            log("\n=== 第 %d 轮 %s ===" % (i, c))
            before = page.evaluate(_PROBE)
            log("  before: count=%s" % before["count"])
            cin = next((x for x in before["inputs"] if "主色" in x["ph"]), None)
            if not cin:
                log("  [XX] 无主色框：%s" % [(x["ph"], x["vis"], x["dis"]) for x in before["inputs"]])
                break
            log("  主色框: vis=%s dis=%s ro=%s val=%r rect=%s"
                % (cin["vis"], cin["dis"], cin["ro"], cin["val"], cin["rect"]))

            # 用 nth(idx) 精确定位到刚才探测到的那个框
            loc = page.locator('input[placeholder="%s"]').nth(cin["idx"])
            n = page.locator('input[placeholder="%s"]').count()
            log("  同 placeholder 的 input 数=%d，用 nth(%d)" % (n, cin["idx"]))
            try:
                loc.click(timeout=5000, force=True)
                page.wait_for_timeout(200)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.wait_for_timeout(150)
                page.keyboard.type(c, delay=40)
                page.wait_for_timeout(500)
                typed = page.evaluate(_PROBE)
                tcin = next((x for x in typed["inputs"] if "主色" in x["ph"]), None)
                log("  type 后 value=%r" % ((tcin or {}).get("val"),))
            except Exception as e:
                log("  [XX] 输入失败 %s" % str(e).split("\n")[0][:110])
                continue

            # 点 + ：优先用与本轮主色框同一行的 plus
            plus = None
            for r in before["plusBtns"]:
                if abs(r[1] - cin["rect"][1]) < 40 and r[0] > cin["rect"][0]:
                    plus = r
                    break
            if plus is None and before["plusBtns"]:
                plus = before["plusBtns"][0]
            if plus is None:
                log("  [XX] 无 + 按钮")
                break
            px, py = plus[0] + plus[2] // 2, plus[1] + plus[3] // 2
            log("  点 + @(%d,%d)" % (px, py))
            page.mouse.click(px, py)
            page.wait_for_timeout(1600)

            after = page.evaluate(_PROBE)
            acin = next((x for x in after["inputs"] if "主色" in x["ph"]), None)
            log("  after : count=%s  框 value=%r  vis=%s  dis=%s"
                % (after["count"], (acin or {}).get("val"),
                   (acin or {}).get("vis"), (acin or {}).get("dis")))
            if after["count"] is not None and before["count"] is not None:
                log("  → %s (%s → %s)"
                    % ("[OK]" if after["count"] > before["count"] else "[!!] 没加进去",
                       before["count"], after["count"]))

        log("\n[最终]")
        fin = page.evaluate(_PROBE)
        log("  count=%s" % fin["count"])
        try:
            page.screenshot(path=str(ROOT / "data" / "publish_poc" / "sku_probe.png"),
                            timeout=20000, animations="disabled", caret="hide")
            log("  截图 ok")
        except Exception as e:
            log("  [!] 截图 %s" % str(e)[:70])
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
