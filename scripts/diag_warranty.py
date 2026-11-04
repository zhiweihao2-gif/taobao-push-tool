# -*- coding: utf-8 -*-
"""单独诊断质保期限的「数字 + 单位」控件，把每一步的中间状态打出来。

用法: python scripts/diag_warranty.py 110210
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.publish.browser_pub import BrowserSession, publish_url_for  # noqa: E402

LOC = """
(label) => {
    const all = Array.from(document.querySelectorAll('*'));
    const leaf = all.find(e => e.children.length === 0 &&
        (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
    if (!leaf) return {ok: false, why: 'label not found'};
    leaf.scrollIntoView({block: 'center'});
    const lr = leaf.getBoundingClientRect();
    const rowY = lr.y + lr.height / 2;
    const out = {ok: true, labelRect: [lr.x, lr.y, lr.width, lr.height], rowY: rowY,
                 nums: [], sels: []};

    document.querySelectorAll('input[placeholder="数字"]').forEach(i => {
        const r = i.getBoundingClientRect();
        out.nums.push({x: Math.round(r.x), y: Math.round(r.y),
                       w: Math.round(r.width), h: Math.round(r.height),
                       dy: Math.round(r.y + r.height / 2 - rowY),
                       val: i.value});
    });
    document.querySelectorAll('.next-select').forEach(s => {
        const r = s.getBoundingClientRect();
        const dy = Math.round(r.y + r.height / 2 - rowY);
        if (Math.abs(dy) < 80) {
            out.sels.push({x: Math.round(r.x), y: Math.round(r.y),
                           w: Math.round(r.width), h: Math.round(r.height), dy: dy,
                           cls: (s.className || '').toString().slice(0, 70),
                           txt: (s.innerText || '').trim().slice(0, 30)});
        }
    });
    return out;
}
"""


def dump_state(page, tag: str) -> None:
    ovs = page.evaluate("""() => {
        return Array.from(document.querySelectorAll('.next-overlay-wrapper'))
            .map(o => ({
                opened: o.classList.contains('opened'),
                rect: (() => { const r = o.getBoundingClientRect();
                              return [Math.round(r.x), Math.round(r.y),
                                      Math.round(r.width), Math.round(r.height)]; })(),
                optItems: o.querySelectorAll('[class*="options-item"]').length,
                text: (o.innerText || '').replace(/\\n/g, '|').slice(0, 90)
            })).filter(o => o.opened || o.optItems > 0);
    }""") or []
    print("  [%s] overlay %d 个: %s" % (tag, len(ovs), json.dumps(ovs, ensure_ascii=False)[:260]))


def main() -> int:
    cid = sys.argv[1] if len(sys.argv) > 1 else "110210"

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        p.goto(publish_url_for(cid), wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(3200)
        print("[..] %s" % (p.url or "")[:110])

        info = p.evaluate(LOC, "质保期限")
        print("\n定位结果:")
        print(json.dumps(info, ensure_ascii=False, indent=1)[:1200])

        if not info.get("ok"):
            print("[XX] 定位失败")
            return 1

        # 找数字框
        nums = [n for n in info["nums"] if abs(n["dy"]) < 60]
        if not nums:
            print("[XX] 同一行没有数字框（所有数字框: %s）" % info["nums"])
            return 1
        n = nums[0]
        print("\n数字框: x=%d y=%d w=%d h=%d val=%r" % (n["x"], n["y"], n["w"], n["h"], n["val"]))

        p.mouse.click(n["x"] + n["w"] / 2, n["y"] + n["h"] / 2)
        p.wait_for_timeout(300)
        p.keyboard.press("Control+A")
        p.keyboard.press("Delete")
        for ch in "12":
            p.keyboard.type(ch)
            p.wait_for_timeout(90)
        p.wait_for_timeout(500)
        dump_state(p, "填数字后")
        v = p.evaluate("""() => {
            const a = Array.from(document.querySelectorAll('input[placeholder="数字"]'));
            return a.map(i => i.value);
        }""")
        print("  数字框现在的值: %s" % v)

        # 找单位下拉
        sels = [x for x in info["sels"] if abs(x["dy"]) < 60 and x["x"] > n["x"] + n["w"] - 10]
        print("\n候选单位下拉: %s" % json.dumps(sels, ensure_ascii=False)[:400])
        if not sels:
            # 放宽：不限制 x
            sels = [x for x in info["sels"] if abs(x["dy"]) < 60]
            print("  放宽后: %s" % json.dumps(sels, ensure_ascii=False)[:400])

        for k, sel in enumerate(sels[:2]):
            print("\n--- 试第 %d 个单位下拉 x=%d y=%d w=%d cls=%s txt=%r ---"
                  % (k + 1, sel["x"], sel["y"], sel["w"], sel["cls"][:40], sel["txt"]))
            for dx, tag in ((8, "左边缘"), (sel["w"] / 2, "中心"), (sel["w"] - 8, "右边缘")):
                p.keyboard.press("Escape")
                p.wait_for_timeout(200)
                p.mouse.click(sel["x"] + dx, sel["y"] + sel["h"] / 2)
                p.wait_for_timeout(1800)
                got = p.evaluate("""() => {
                    const ov = Array.from(document.querySelectorAll('.next-overlay-wrapper'))
                        .find(o => o.classList.contains('opened'));
                    if (!ov) return [];
                    return Array.from(ov.querySelectorAll('[class*="options-item"]'))
                        .map(it => it.getAttribute('title') || (it.innerText || '').trim());
                }""") or []
                print("  %s click(x+%d) -> overlay 选项 %d 个 %s"
                      % (tag, dx, len(got), got[:8]))
                if got:
                    break

        p.screenshot(path="data/publish_poc/diag_warranty.png", timeout=8000)
        print("\n[..] 截图 data/publish_poc/diag_warranty.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
