"""探测键盘类目的 SKU 创建抽屉结构。

打开发布页 → 滚到「销售规格」→ 点「+ 创建规格」→ dump 抽屉里的：
  展示模式、属性列表、颜色输入框、各项必填列的选项。

用法：
    python scripts/probe_sku_kb.py --cid 110210
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.publish.browser_pub import BrowserSession, publish_url_for  # noqa: E402

OUT = Path("data/publish_poc")

WAIT_READY = r"""() => {
  return document.querySelectorAll('.sell-catProp-item').length;
}"""

# 滚到销售规格区并点「+ 创建规格」
OPEN_DRAWER = r"""() => {
  const btns = [...document.querySelectorAll('button')];
  const b = btns.find(x => (x.innerText || '').replace(/\s+/g, '').indexOf('创建规格') >= 0);
  if (!b) return {ok: false, why: 'no button'};
  b.scrollIntoView({block: 'center'});
  b.click();
  return {ok: true, text: (b.innerText || '').trim()};
}"""

# dump 抽屉
DUMP_DRAWER = r"""() => {
  const out = {found: false, mods: [], props: [], inputs: [], labels: [], buttons: []};
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();

  // 抽屉容器：sku-decouple-drawer / next-drawer
  const drawer = document.querySelector('.sku-decouple-drawer, .next-drawer');
  if (!drawer) return out;
  out.found = true;
  out.text = norm(drawer.innerText).slice(0, 1200);

  // 展示模式
  drawer.querySelectorAll('.select-mode, [class*="select-mode"]').forEach(el => {
    out.mods.push({cls: el.className, text: norm(el.innerText)});
  });

  // 属性列表（属性 1/3 那一排）
  drawer.querySelectorAll('.sell-common-sale-props-sort-box-btn, button.sort-btn').forEach(el => {
    const r = el.getBoundingClientRect();
    out.props.push({cls: el.className, text: norm(el.innerText), rect: [r.x, r.y, r.width, r.height]});
  });

  // 所有 input
  drawer.querySelectorAll('input').forEach(el => {
    const r = el.getBoundingClientRect();
    out.inputs.push({
      type: el.type, ph: el.placeholder, value: el.value,
      cls: el.className, readOnly: el.readOnly,
      rect: [r.x, r.y, r.width, r.height]
    });
  });

  // 带 * 或「重要」的 label
  drawer.querySelectorAll('label, .sell-o-message, [class*="label"]').forEach(el => {
    const t = norm(el.innerText);
    if (t && t.length < 30 && (t.indexOf('*') >= 0 || t.indexOf('重要') >= 0 || t.indexOf('必选') >= 0)) {
      const r = el.getBoundingClientRect();
      if (r.width > 0) out.labels.push({text: t, cls: el.className, rect: [r.x, r.y, r.width, r.height]});
    }
  });

  // 按钮
  drawer.querySelectorAll('button').forEach(el => {
    const r = el.getBoundingClientRect();
    out.buttons.push({cls: el.className, text: norm(el.innerText), rect: [r.x, r.y, r.width, r.height]});
  });

  return out;
}"""


def log(m: str) -> None:
    print(m, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="110210")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--save", default=None)
    a = ap.parse_args()

    with BrowserSession(a.cdp, timeout_ms=40000) as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        url = publish_url_for(a.cid)
        log("[..] %s" % url)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 等表单渲染
        for i in range(30):
            n = page.evaluate(WAIT_READY)
            if n and n >= 3:
                log("[OK] 表单就绪 (.sell-catProp-item=%d)" % n)
                break
            time.sleep(0.5)
        else:
            log("[XX] 表单未就绪")
            page.screenshot(str(OUT / "probe_sku_kb_notready.png"))
            return 1

        time.sleep(2.0)

        # 打开抽屉
        r = page.evaluate(OPEN_DRAWER)
        log("[..] 点创建规格: %s" % json.dumps(r, ensure_ascii=False))
        time.sleep(3.0)

        page.screenshot(path=str(OUT / "probe_sku_kb_drawer.png"), full_page=False, timeout=30000)

        d = page.evaluate(DUMP_DRAWER)
        log("")
        log("=" * 60)
        log("抽屉: %s" % ("找到" if d.get("found") else "没找到"))
        if d.get("found"):
            log("文本: %s" % d.get("text", "")[:400])
            log("")
            log("--- 展示模式 ---")
            for m in d.get("mods", []):
                log("  %s" % m.get("text", "")[:120])
            log("")
            log("--- 属性按钮 ---")
            for m in d.get("props", []):
                log("  %-14s rect=%s" % (m.get("text", ""), m.get("rect")))
            log("")
            log("--- input ---")
            for m in d.get("inputs", []):
                log("  ph=%-16r val=%-12r ro=%s rect=%s"
                    % (m.get("ph"), m.get("value"), m.get("readOnly"), m.get("rect")))
            log("")
            log("--- 必填 label ---")
            for m in d.get("labels", []):
                log("  %-28s rect=%s" % (m.get("text", ""), m.get("rect")))
            log("")
            log("--- 按钮 ---")
            for m in d.get("buttons", []):
                if m.get("text"):
                    log("  %-16s rect=%s" % (m.get("text", "")[:16], m.get("rect")))

        p = Path(a.save) if a.save else OUT / ("sku_kb_%s.json" % a.cid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        log("")
        log("已保存 %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
