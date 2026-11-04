# -*- coding: utf-8 -*-
"""诊断「质保期限」这类字段到底是什么控件。

用法: python scripts/diag_field.py 质保期限 110210
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.publish.browser_pub import BrowserSession, publish_url_for  # noqa: E402

DUMP = """
(label) => {
    const all = Array.from(document.querySelectorAll('*'));
    const leaves = all.filter(e => e.children.length === 0 &&
        (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
    const out = {label: label, matches: leaves.length, items: []};
    leaves.forEach((leaf, li) => {
        const chain = [];
        let n = leaf;
        for (let i = 0; i < 8 && n; i++) {
            chain.push({
                tag: n.tagName,
                cls: (n.className || '').toString().slice(0, 70),
                role: n.getAttribute && n.getAttribute('role') || '',
                rect: (() => { const r = n.getBoundingClientRect();
                              return [Math.round(r.x), Math.round(r.y),
                                      Math.round(r.width), Math.round(r.height)]; })(),
                text: (n.innerText || '').trim().slice(0, 50)
            });
            n = n.parentElement;
        }
        // 找同级/附近的 select / radio / input
        let box = leaf.parentElement;
        for (let i = 0; i < 4 && box; i++) box = box.parentElement || box;
        const ctrls = box ? Array.from(box.querySelectorAll(
            '.next-select, input:not([type=hidden]), [class*="radio"], [class*="checkbox"]'
        )).slice(0, 12).map(e => ({
            tag: e.tagName,
            cls: (e.className || '').toString().slice(0, 60),
            type: e.type || '',
            ph: e.getAttribute && e.getAttribute('placeholder') || '',
            val: e.value || '',
            rect: (() => { const r = e.getBoundingClientRect();
                          return [Math.round(r.x), Math.round(r.y),
                                  Math.round(r.width), Math.round(r.height)]; })()
        })) : [];
        out.items.push({idx: li, chain: chain, controls: ctrls});
    });
    return out;
}
"""

OPTIONS = """
() => {
    const ovs = Array.from(document.querySelectorAll('.next-overlay-wrapper'))
        .filter(o => o.classList.contains('opened'));
    return ovs.map(ov => ({
        cls: (ov.className || '').toString().slice(0, 60),
        rect: (() => { const r = ov.getBoundingClientRect();
                      return [Math.round(r.x), Math.round(r.y),
                              Math.round(r.width), Math.round(r.height)]; })(),
        text: (ov.innerText || '').slice(0, 200),
        optItems: ov.querySelectorAll('[class*="options-item"]').length,
        lis: ov.querySelectorAll('li').length,
        inputs: Array.from(ov.querySelectorAll('input')).map(i => ({
            ph: i.getAttribute('placeholder') || '', w: Math.round(i.getBoundingClientRect().width)
        }))
    }));
}
"""


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "质保期限"
    cid = sys.argv[2] if len(sys.argv) > 2 else "110210"

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        p.goto(publish_url_for(cid), wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(3000)
        print("[..] %s" % (p.url or "")[:110])

        d = p.evaluate(DUMP, label)
        print("\n『%s』匹配 %d 处" % (label, d.get("matches")))
        for it in d.get("items") or []:
            print("\n--- 第 %d 处 ---" % (it["idx"] + 1))
            for c in it["chain"][:6]:
                print("   %-8s %-42s %-10s %s  %r"
                      % (c["tag"], c["cls"], c["role"], c["rect"], c["text"][:28]))
            print("   附近控件:")
            for ctl in it["controls"]:
                print("     %-8s %-38s type=%-8s ph=%-12s val=%-16s %s"
                      % (ctl["tag"], ctl["cls"], ctl["type"], ctl["ph"], ctl["val"][:14], ctl["rect"]))

        # 试着点一下第一个匹配附近的 .next-select
        print("\n[..] 尝试点击附近控件并看 overlay")
        info = p.evaluate("""(label) => {
            const all = Array.from(document.querySelectorAll('*'));
            const leaf = all.find(e => e.children.length === 0 &&
                (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
            if (!leaf) return null;
            let box = leaf.parentElement;
            for (let i = 0; i < 4 && box; i++) box = box.parentElement || box;
            const sel = box ? box.querySelector('.next-select') : null;
            if (!sel) return null;
            sel.scrollIntoView({block: 'center'});
            const r = sel.getBoundingClientRect();
            return {rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]};
        }""", label)
        if info:
            x, y, w, h = info["rect"]
            print("   .next-select rect=%s" % (info["rect"],))
            p.mouse.click(x + 8, y + h / 2)
            p.wait_for_timeout(2200)
            ovs = p.evaluate(OPTIONS) or []
            print("   overlay 数量 %d" % len(ovs))
            for ov in ovs:
                print("     cls=%s rect=%s optItems=%d lis=%d"
                      % (ov["cls"], ov["rect"], ov["optItems"], ov["lis"]))
                print("     text=%r" % ov["text"][:140])
                print("     inputs=%s" % ov["inputs"][:5])
        else:
            print("   [!!] 附近没有 .next-select")

        p.screenshot(path="data/publish_poc/diag_field.png", timeout=8000)
        print("\n[..] 截图 data/publish_poc/diag_field.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
