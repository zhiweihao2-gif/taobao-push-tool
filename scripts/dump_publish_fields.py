"""dump 发布页每个字段的上下文，建立「字段名 → selector」映射表。

发布页的 input 全都没有 id/name，只能靠父容器文本定位。
对每个可见 input/textarea，向上找 4 层父容器，收集其中的文本节点作为候选字段名。
同时 dump 主图上传槽的结构。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  // 收集容器内除 input 外的文本（截断到 40 字）
  function labelOf(el) {
    let cur = el.parentElement;
    for (let depth = 0; depth < 4 && cur; depth++) {
      const clone = cur.cloneNode(true);
      clone.querySelectorAll('input,textarea,select,button,script,style').forEach(n => n.remove());
      const t = clean(clone.textContent);
      if (t && t.length <= 40) return { text: t, depth: depth + 1, cls: clean(cur.className).slice(0, 70) };
      if (t && t.length > 40) {
        // 太长就取开头，但仍记录
        return { text: t.slice(0, 40) + '…', depth: depth + 1, cls: clean(cur.className).slice(0, 70) };
      }
      cur = cur.parentElement;
    }
    return null;
  }

  const fields = [];
  document.querySelectorAll('input,textarea').forEach((el, i) => {
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) return;
    const cs = window.getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    fields.push({
      i,
      tag: el.tagName,
      type: el.getAttribute('type'),
      name: el.getAttribute('name'),
      id: el.id || null,
      ph: el.getAttribute('placeholder'),
      value: (el.value || '').slice(0, 30),
      readOnly: el.readOnly,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      label: labelOf(el),
    });
  });

  // 主图上传区
  const imgSlots = [];
  document.querySelectorAll('[class*="image"],[class*="upload"],[class*="pic"]').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 40) return;
    const cls = clean(el.className).toString();
    if (!/image|upload|pic/i.test(cls)) return;
    imgSlots.push({
      cls: cls.slice(0, 100),
      tag: el.tagName,
      text: clean(el.innerText).slice(0, 30),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });

  // 所有 button 文本
  const btns = [];
  document.querySelectorAll('button').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 10) return;
    btns.push({ text: clean(b.innerText).slice(0, 20), disabled: b.disabled,
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  // 所有 tab
  const tabs = [];
  document.querySelectorAll('[role="tab"],[class*="tab-item"],[class*="tabItem"]').forEach(t => {
    const r = t.getBoundingClientRect();
    if (r.width < 10) return;
    tabs.push({ text: clean(t.innerText).slice(0, 20), cls: clean(t.className).slice(0, 60),
                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });

  return { url: location.href, fields, imgSlots: imgSlots.slice(0, 40), btns, tabs };
}
"""


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)
        print("URL: " + page.url)
        if "publish.htm" not in page.url:
            print("[XX] 当前不在发布页，先跑 goto_publish.py")
            return 1

        data = page.evaluate(_JS)
        out = ROOT / "data" / "publish_poc" / "publish_fields.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写: " + str(out))

        print("\n" + "=" * 78)
        print("可填字段 (%d)" % len(data["fields"]))
        print("=" * 78)
        for f in data["fields"]:
            lb = f["label"]["text"] if f["label"] else "(无)"
            print("  #%-2d %-8s ph=%-28r label=%-22r rect=%s" % (
                f["i"], f["tag"], (f["ph"] or "")[:26], lb[:20], f["rect"]))

        print("\n" + "=" * 78)
        print("图片上传区 (%d)" % len(data["imgSlots"]))
        print("=" * 78)
        for x in data["imgSlots"]:
            print("  %-50s %s %s" % (x["cls"][:48], x["text"][:18], x["rect"]))

        print("\n" + "=" * 78)
        print("按钮 (%d)" % len(data["btns"]))
        print("=" * 78)
        for b in data["btns"]:
            print("  %-16r disabled=%-6s %s" % (b["text"], b["disabled"], b["rect"]))

        print("\n" + "=" * 78)
        print("Tab (%d)" % len(data["tabs"]))
        print("=" * 78)
        for t in data["tabs"]:
            print("  %-14r %-40s %s" % (t["text"], t["cls"][:38], t["rect"]))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
