# -*- coding: utf-8 -*-
"""probe_cat.py —— 给一个 catId，dump 出该类目的完整发布 schema。

用途：打通一个新类目的第一步。产出 `data/category_schema/{cid}.json`，
后续 publish 流程按它决定「填哪些字段、每个下拉能选什么」。

不修改任何表单值（只展开下拉读选项，读完 ESC 关掉）。

用法：
  python scripts/probe_cat.py --cid 200670001
  python scripts/probe_cat.py --cid 200670001 --no-options   # 只列字段不看选项（快）

识别规则：
  - 必填：label 文本里含 '*' 或 '＊'，或父容器含「必填」「重要」
  - pid：input 的 name 属性形如 p-20000 或 p-20000~1
  - 下拉：所在 .next-select 容器；选项用 div.options-item[title]（实测唯一可靠方式）
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

PUBLISH_URL = "https://item.upload.taobao.com/sell/v2/publish.htm"
DEFAULT_OUT = ROOT / "data" / "category_schema"

ENUM_FIELDS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];

  const findLabel = (el) => {
    let p = el.parentElement;
    for (let d = 0; d < 5 && p; d++) {
      for (const c of p.children) {
        if (c === el || c.contains(el)) break;
        const t = clean(c.innerText);
        if (t && t.length <= 20) return t;
      }
      p = p.parentElement;
    }
    const id = el.id;
    if (id) {
      const l = document.querySelector('label[for="' + id + '"]');
      if (l) return clean(l.innerText);
    }
    p = el.parentElement;
    for (let d = 0; d < 6 && p; d++) {
      const t = clean(p.innerText);
      if (t && t.length <= 30) return t;
      p = p.parentElement;
    }
    return '';
  };

  const isRequiredNearby = (el) => {
    let p = el.parentElement;
    for (let d = 0; d < 5 && p; d++) {
      const t = clean(p.innerText);
      if (t.indexOf('必填') >= 0 || t.indexOf('重要') >= 0) return true;
      p = p.parentElement;
    }
    return false;
  };

  document.querySelectorAll('.next-select').forEach((s) => {
    const r = s.getBoundingClientRect();
    if (r.width < 30 || r.height < 16) return;
    const inp = s.querySelector('input');
    const label = findLabel(s);
    const txt = clean(s.innerText).slice(0, 40);
    out.push({
      kind: 'select',
      label: label,
      pid: inp && inp.name ? inp.name : '',
      placeholder: inp ? (inp.placeholder || '') : '',
      value: txt,
      required: label.indexOf('*') >= 0 || label.indexOf('＊') >= 0 || isRequiredNearby(s),
      multi: /next-select-multiple/.test(s.className),
      readonly: inp ? !!inp.readOnly : null,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });

  document.querySelectorAll('input,textarea').forEach((el) => {
    if (el.type === 'checkbox' || el.type === 'radio' || el.type === 'hidden') return;
    if (el.closest('.next-select')) return;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 14) return;
    const label = findLabel(el);
    out.push({
      kind: el.tagName === 'TEXTAREA' ? 'textarea' : 'text',
      label: label,
      pid: el.name || '',
      placeholder: el.placeholder || '',
      value: String(el.value || '').slice(0, 40),
      maxlen: el.maxLength > 0 ? el.maxLength : null,
      required: label.indexOf('*') >= 0 || label.indexOf('＊') >= 0 || isRequiredNearby(el),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });

  document.querySelectorAll('input[type=radio],input[type=checkbox]').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 8) return;
    const label = findLabel(el);
    out.push({
      kind: el.type,
      label: label,
      pid: el.name || '',
      placeholder: '',
      value: el.checked ? 'checked' : '',
      required: label.indexOf('*') >= 0,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });

  return out;
}
"""

MARK_OPTIONS = r"""
() => {
  const ovs = [...document.querySelectorAll('.next-overlay-wrapper')]
    .filter(o => o.classList.contains('opened'));
  if (!ovs.length) return {err: 'no overlay'};
  const ov = ovs[ovs.length - 1];
  const out = [];
  ov.querySelectorAll('[class*="options-item"]').forEach(el => {
    const t = (el.getAttribute('title') || '').trim();
    if (t) out.push(t);
  });
  return {count: out.length, options: out.slice(0, 150)};
}
"""

TABS = r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll('.next-tabs-tab, [role="tab"]')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length > 10) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 10) continue;
    out.push({name: t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
  }
  return out;
}
"""

CAT_PATH = r"""
() => {
  const t = document.body.innerText || '';
  const m = t.match(/当前类目[：:]\s*([^\n]+)/);
  return m ? m[1].trim() : '';
}
"""

PRICE_HINT = r"""
() => {
  const t = document.body.innerText || '';
  const m = t.match(/本类目[^\n]{0,30}最低[^\n]{0,20}/);
  return m ? m[0] : '';
}
"""

SKU_INFO = r"""
() => {
  const out = {headers: [], modeText: '', dims: []};
  const t = document.body.innerText || '';
  const m = t.match(/SKU\s*模式[^\n]{0,40}/);
  out.modeText = m ? m[0] : '';
  const dm = t.match(/【([^】]{1,12})】/g);
  out.dims = dm ? dm.map(x => x.replace(/[【】]/g, '')) : [];
  const tr = document.querySelector('table tr');
  if (tr) {
    out.headers = Array.from(tr.children)
      .map(c => (c.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .map(x => x.slice(0, 20));
  }
  return out;
}
"""


def log(msg=""):
    print(msg, flush=True)


def close_overlays(page):
    for _ in range(3):
        if page.evaluate("""() => document.querySelectorAll('.next-overlay-wrapper.opened').length""") == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(350)


def read_options(page, sel, wait=2000):
    close_overlays(page)
    try:
        sel.scroll_into_view_if_needed(timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(250)
    for _ in range(2):
        try:
            sel.click(position={"x": 8, "y": 14}, timeout=4000)
        except Exception:
            try:
                sel.click(force=True, timeout=4000)
            except Exception:
                pass
        page.wait_for_timeout(wait)
        m = page.evaluate(MARK_OPTIONS)
        if m.get("options"):
            close_overlays(page)
            return m["options"]
    close_overlays(page)
    return []


def probe(cid, read_opts=True, timeout_ms=60000):
    rep = {
        "cid": cid,
        "probed_at": datetime.now().isoformat(timespec="seconds"),
        "cat_path": "", "valid": False, "error": "",
        "price_hint": "", "tabs": [], "fields": [], "sku": {},
    }

    with BrowserSession() as sess:
        page = sess.page()
        try:
            page.set_viewport_size({"width": 1440, "height": 1000})
        except Exception:
            pass

        url = "%s?catId=%s&fromAICategory=true" % (PUBLISH_URL, cid)
        log("打开 %s" % url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            log("  [..] goto: %s" % str(e).split("\n")[0][:100])
        page.wait_for_timeout(3500)

        body = page.evaluate("() => document.body.innerText || ''")
        if "类目不存在或无效" in body:
            rep["error"] = "类目不存在或无效（该店铺无此权限）"
            log("  [XX] 类目不存在或无效")
            return rep
        if "类目为空或不存在" in body:
            rep["error"] = "类目为空或不存在"
            log("  [XX] 类目为空")
            return rep
        rep["valid"] = True

        rep["cat_path"] = page.evaluate(CAT_PATH)
        rep["price_hint"] = page.evaluate(PRICE_HINT)
        log("  类目: %s" % rep["cat_path"])
        if rep["price_hint"]:
            log("  价格提示: %s" % rep["price_hint"])

        BLOCK_TABS = {"基础信息", "销售信息", "物流服务", "支付信息", "图文描述", "售后服务"}
        seen, tab_list = set(), []
        for t in page.evaluate(TABS):
            if t["name"] in seen or t["name"] not in BLOCK_TABS:
                continue
            seen.add(t["name"])
            tab_list.append(t)
        if not tab_list:
            tab_list = [{"name": "基础信息"}]
        rep["tabs"] = [t["name"] for t in tab_list]
        log("  tab: %s" % rep["tabs"])

        all_fields = []
        for t in tab_list:
            name = t["name"]
            try:
                page.get_by_text(name, exact=True).first.click(force=True, timeout=4000)
                page.wait_for_timeout(1300)
            except Exception:
                pass
            fields = page.evaluate(ENUM_FIELDS)
            for f in fields:
                f["tab"] = name
            all_fields.extend(fields)
            log("  [%s] %d 个字段" % (name, len(fields)))
            if name == "销售信息":
                rep["sku"] = page.evaluate(SKU_INFO)
                log("      SKU 表头: %s" % rep["sku"].get("headers", [])[:14])
                log("      SKU 模式: %r" % rep["sku"].get("modeText", ""))

        if read_opts:
            log("\n读下拉选项…")
            # SKU 行内下拉
            if rep["sku"]:
                tr = page.locator("table tr")
                if tr.count() > 0:
                    sels = tr.first.locator(".next-select")
                    n = sels.count()
                    log("  SKU 首行下拉 %d 个" % n)
                    rows = []
                    for ci in range(n):
                        opts = read_options(page, sels.nth(ci))
                        rows.append({"col": ci, "options": opts})
                        log("    col%d: %d 个 %s" % (ci, len(opts), opts[:5]))
                    rep["sku"]["row_selects"] = rows

            # 页面其余下拉
            sels = page.locator(".next-select")
            n = sels.count()
            log("  页面共 %d 个 .next-select" % n)
            for i in range(n):
                sel = sels.nth(i)
                try:
                    box = sel.bounding_box()
                except Exception:
                    continue
                if not box or box["width"] < 30:
                    continue
                target = None
                for f in all_fields:
                    if f["kind"] != "select" or f.get("options") is not None:
                        continue
                    fr = f["rect"]
                    if abs(fr[0] - box["x"]) < 8 and abs(fr[1] - box["y"]) < 40:
                        target = f
                        break
                opts = read_options(page, sel)
                if target is not None:
                    target["options"] = opts
                lbl = target["label"] if target else ""
                log("    [%d] %-18r %d 个 %s" % (i, lbl[:18], len(opts), opts[:5]))

        rep["fields"] = all_fields

    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", required=True)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--timeout", type=int, default=60000)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rep = probe(args.cid, read_opts=not args.no_options, timeout_ms=args.timeout)

    p = out_dir / ("%s.json" % args.cid)
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    log("\n" + "=" * 60)
    log("结果")
    log("=" * 60)
    log("  valid   : %s" % rep["valid"])
    if rep["error"]:
        log("  error   : %s" % rep["error"])
    log("  类目    : %s" % rep["cat_path"])
    log("  字段数  : %d" % len(rep["fields"]))
    req = [f for f in rep["fields"] if f.get("required")]
    log("  必填数  : %d" % len(req))
    for f in req:
        o = f.get("options")
        log("    * %-16s %-9s pid=%-14s %s"
            % (f["label"][:16], f["kind"], f["pid"][:14],
               ("%d 选项" % len(o)) if o else ""))
    log("  SKU 表头: %s" % rep["sku"].get("headers", [])[:12])
    log("\n写入 %s" % p)


if __name__ == "__main__":
    main()
