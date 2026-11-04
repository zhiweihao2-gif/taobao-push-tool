"""
publish_kb.py —— 键盘类目(cid=110210) 单商品全自动发布
已实测打通：标题 / 品牌 / 适用场景 / 是否机械键盘 / 人体工程学 / 型号 /
            质保期限(fiber) / 一口价 / 总库存 / 5 张主图 / 宝贝详情
用法: python scripts/publish_kb.py --item 745703904965 --cid 110210 [--submit]
"""
import sys, time, json, glob, os, argparse, random, re
sys.path.insert(0, ".")
from src.core.storage import load_item, save_item
from src.publish.browser_pub import BrowserSession, publish_url_for

def log(s=""): print(s, flush=True)


# ---------- SKU 相关 JS ----------

SKU_OPEN = r"""() => {
  const b = [...document.querySelectorAll('button')]
    .find(x => (x.innerText || '').replace(/\s+/g, '').indexOf('创建规格') >= 0);
  if (!b) return false;
  b.scrollIntoView({block: 'center'});
  b.click();
  return true;
}"""

SKU_CHIP = r"""(name) => {
  const d = document.querySelector('.sku-decouple-drawer, .next-drawer');
  if (!d) return false;
  for (const el of [...d.querySelectorAll('span, div, button, li')]) {
    if ((el.innerText || '').replace(/\s+/g, '') !== name) continue;
    if (el.children.length > 3) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.width > 200) continue;
    el.click();
    return true;
  }
  return false;
}"""

SKU_CONFIRM = r"""() => {
  const b = [...document.querySelectorAll('button')]
    .find(x => (x.innerText || '').replace(/\s+/g, '').indexOf('确认创建') >= 0);
  if (!b) return false;
  b.click();
  return true;
}"""

SKU_ROWS = r"""() => {
  const t = document.querySelector('table');
  if (!t) return [];
  return [...t.querySelectorAll('tbody tr')].map((tr, i) => {
    const name = (tr.querySelector('td') || {}).innerText || '';
    const ins = [...tr.querySelectorAll('input')].map(el => {
      const r = el.getBoundingClientRect();
      return {ph: el.placeholder, value: el.value,
              rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]};
    });
    return {i: i, name: name.replace(/\s+/g, ' ').trim().slice(0, 60), inputs: ins};
  });
}"""

# 把第 i 行滚到视口中间，返回该行前两个 input 的新 rect。
# 不能用 window.scrollTo —— 页面滚动容器不是 window。
SKU_SCROLL = r"""(i) => {
  const t = document.querySelector('table');
  if (!t) return null;
  const tr = t.querySelectorAll('tbody tr')[i];
  if (!tr) return null;
  tr.scrollIntoView({block: 'center', inline: 'nearest'});
  const ins = [...tr.querySelectorAll('input')].slice(0, 2).map(el => {
    const r = el.getBoundingClientRect();
    return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height), el.placeholder, el.value];
  });
  return {vh: window.innerHeight, inputs: ins};
}"""

SKU_CLASS_OF = r"""(i) => {
  const tr = document.querySelectorAll('table tbody tr')[i];
  if (!tr) return null;
  const em = tr.querySelector('.next-select em');
  return em ? (em.textContent || '').trim() : null;
}"""

SKU_CLASS_FILL = r"""(i) => {
  const tr = document.querySelectorAll('table tbody tr')[i];
  if (!tr) return 'no-row';
  tr.scrollIntoView({block: 'center'});
  const sel = tr.querySelector('.next-select');
  if (!sel) return 'no-select';
  sel.click();
  return 'clicked';
}"""

SKU_OPT_LIST = r"""() => {
  const out = [];
  document.querySelectorAll('.next-overlay-wrapper').forEach(w => {
    if (!w.classList.contains('opened')) return;
    w.querySelectorAll('[class*="options-item"]').forEach(el => out.push(el.getAttribute('title')));
  });
  return out;
}"""

SKU_OPT_CLICK = r"""(title) => {
  let hit = null;
  document.querySelectorAll('.next-overlay-wrapper').forEach(w => {
    if (!w.classList.contains('opened')) return;
    w.querySelectorAll('[class*="options-item"]').forEach(el => {
      if (!hit && el.getAttribute('title') === title) hit = el;
    });
  });
  if (!hit) return false;
  hit.click();
  return true;
}"""

# ---------- JS ----------
WAIT_READY = "() => document.querySelectorAll('.sell-catProp-item').length"
LIST_INPUTS = r"""() => {
  document.querySelectorAll('[data-dsh-i]').forEach(e => e.removeAttribute('data-dsh-i'));
  const out = [];
  document.querySelectorAll('input, textarea').forEach((el, k) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    let lab = '';
    let n = el;
    for (let i = 0; i < 9 && n; i++) { const t = (n.innerText||'').trim(); if (t && t.length < 30) { lab = t; break; } n = n.parentElement; }
    el.setAttribute('data-dsh-i', String(out.length));
    out.push({i: out.length, ph: el.placeholder||'', name: el.name||'', val: el.value||'', lab});
  });
  return out;
}"""
SET_VAL = r"""(arg) => {
  const el = document.querySelector('[data-dsh-i="' + arg.i + '"]');
  if (!el) return 'no el';
  el.focus();
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  set.call(el, arg.v);
  ['input','change','blur'].forEach(t => el.dispatchEvent(new Event(t, {bubbles:true})));
  return el.value;
}"""
PICK_OPT = r"""(arg) => {
  document.querySelectorAll('[data-dsh-o]').forEach(e => e.removeAttribute('data-dsh-o'));
  const o = [...document.querySelectorAll('.next-overlay-wrapper')].find(e => e.classList.contains('opened'));
  if (!o) return {ok:false, err:'no overlay'};
  const items = [...o.querySelectorAll('[class*="options-item"]')];
  const hit = items.find(e => (e.getAttribute('title')||'') === arg);
  if (!hit) return {ok:false, err:'not found', opts: items.slice(0,6).map(e => e.getAttribute('title'))};
  hit.setAttribute('data-dsh-o','1');
  return {ok:true, n: items.length};
}"""
LOCATE_LABEL = r"""(labelText) => {
  document.querySelectorAll('[data-dsh-sel]').forEach(e => e.removeAttribute('data-dsh-sel'));
  const leaves = [...document.querySelectorAll('span,div,label')].filter(e => {
    const t = (e.innerText||'').trim();
    return t === labelText && e.children.length === 0;
  });
  for (const lf of leaves) {
    let n = lf, sel = null;
    for (let i = 0; i < 9 && n; i++) {
      const s = n.querySelector && n.querySelector('input[role="combobox"]');
      if (s) { sel = s.closest('.next-select'); break; }
      n = n.parentElement;
    }
    if (sel) { sel.setAttribute('data-dsh-sel', '1'); return {ok:true}; }
  }
  return {ok:false};
}"""
FIBER_WARRANTY = r"""(arg) => {
  const items = [...document.querySelectorAll('.sell-catProp-item')];
  const box = items.find(e => (e.innerText||'').indexOf(arg.label) >= 0);
  if (!box) return {ok:false, err:'no box'};
  const sel = box.querySelector('.next-select');
  if (!sel) return {ok:false, err:'no select'};
  const k = Object.keys(sel).filter(x => x.startsWith('__react'));
  if (!k.length) return {ok:false, err:'no fiber'};
  let f = sel[k[0]];
  for (let i = 0; i < 14 && f; i++) {
    const p = f.memoizedProps || {};
    if (p.units && typeof p.onChange === 'function') {
      try { p.onChange(arg.value); } catch (ex) { return {ok:false, err:'threw ' + String(ex).slice(0,60)}; }
      return {ok:true, depth:i};
    }
    f = f.return;
  }
  return {ok:false, err:'no units fiber'};
}"""
OPEN_SPACE = r"""() => {
  const e = document.querySelector('#struct-mainImagesGroup .image-empty');
  if (!e) return 'no slot';
  const k = Object.keys(e).filter(x => x.startsWith('__react'));
  if (!k.length) return 'no fiber';
  e[k[0]].memoizedProps.onClick();
  return 'ok';
}"""
MARK_CARD = r"""el => {
  document.querySelectorAll('[data-dsh-pick]').forEach(e => e.removeAttribute('data-dsh-pick'));
  let c = el;
  for (let i = 0; i < 6 && c; i++) {
    if (String(c.className||'').indexOf('PicList_PicturesShow_main-show') >= 0) break;
    c = c.parentElement;
  }
  if (!c) return {ok:false};
  c.setAttribute('data-dsh-pick','1');
  return {ok:true};
}"""

# ---------- helpers ----------
def slot_state(page):
    return page.evaluate(r"""() => {
      const g = document.querySelector('#struct-mainImagesGroup');
      if (!g) return {filled:0, empty:0};
      return {filled: [...g.querySelectorAll('img')].length, empty: g.querySelectorAll('.image-empty').length};
    }""")

def set_input(page, i, v):
    page.locator('[data-dsh-i="%d"]' % i).first.click(force=True)
    time.sleep(0.2)
    page.keyboard.press("Control+A"); page.keyboard.press("Delete")
    page.keyboard.type(str(v), delay=25)
    time.sleep(0.3)
    got = page.locator('[data-dsh-i="%d"]' % i).first.input_value()
    if got != str(v):
        r = page.evaluate(SET_VAL, {"i": i, "v": str(v)})
        got = str(r)
    return got

def pick_select(page, label, want):
    page.keyboard.press("Escape"); time.sleep(0.3)
    for attempt in range(3):
        r = page.evaluate(LOCATE_LABEL, label)
        if not r.get("ok"): return False, "label 未定位"
        try:
            page.locator('[data-dsh-sel="1"]').first.evaluate("el => el.scrollIntoView({block:'center'})")
            time.sleep(0.5)
            bb = page.locator('[data-dsh-sel="1"]').first.bounding_box()
            page.locator('[data-dsh-sel="1"]').first.click(force=True, position={"x": 8, "y": bb["height"] / 2})
        except Exception as e:
            return False, str(e).split("\n")[0][:70]
        time.sleep(2.0)
        pr = page.evaluate(PICK_OPT, want)
        if pr.get("ok"):
            try:
                page.locator('[data-dsh-o="1"]').first.click(force=True, timeout=6000)
                time.sleep(1.5)
                return True, want
            except Exception as e:
                return False, str(e).split("\n")[0][:70]
        # 列表里没有 -> 在下拉的搜索框里真实鼠标点击 + 敲字
        try:
            box = page.evaluate(r"""() => {
              const o = [...document.querySelectorAll('.next-overlay-wrapper')].find(e => e.classList.contains('opened'));
              if (!o) return null;
              const inp = [...o.querySelectorAll('input')].find(i => i.getBoundingClientRect().width > 40);
              if (!inp) return null;
              const r = inp.getBoundingClientRect();
              return {x: r.left + r.width/2, y: r.top + r.height/2};
            }""")
            if box:
                page.mouse.click(box["x"], box["y"])
                time.sleep(0.4)
                page.keyboard.press("Control+A"); page.keyboard.press("Delete")
                page.keyboard.type(want, delay=90)
                time.sleep(2.0)
            pr = page.evaluate(PICK_OPT, want)
            if pr.get("ok"):
                page.locator('[data-dsh-o="1"]').first.click(force=True, timeout=6000)
                time.sleep(1.5)
                return True, want
        except Exception:
            pass
    return False, "三次未选中：%s" % json.dumps(pr, ensure_ascii=False)[:100]

def fill_warranty(page, label, text):
    """质保期限 = 数字框 + 单位下拉（UI 打不开，走 React fiber onChange）。

    onChange 接受拼接字符串（如 "12个月"），但前提是数字框里已经有 12，
    否则组件内部会用空数字重算，结果变成「质保期限 个月 必须输入整数」。
    """
    import re as _re
    m = _re.match(r"\s*(\d+)\s*(个月|年|月|天|日)?", text or "")
    num = m.group(1) if m else "12"
    unit = "个月"
    if m and m.group(2):
        unit = "个月" if m.group(2) == "月" else m.group(2)

    # 1) 先真实键盘填数字框
    try:
        nbox = page.get_by_placeholder("数字", exact=True).first
        nbox.scroll_into_view_if_needed(timeout=6000)
        nbox.click(force=True, timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        time.sleep(0.2)
        page.keyboard.type(num, delay=40)
        time.sleep(0.4)
        got_num = nbox.input_value()
    except Exception as e:
        got_num = "<ERR:%s>" % type(e).__name__

    # 2) 再调 fiber 的 onChange（拼好的字符串）
    r = page.evaluate(FIBER_WARRANTY, {"label": label, "value": "%s%s" % (num, unit)})
    time.sleep(1.0)
    ok = page.evaluate(r"""(label) => {
      const box = [...document.querySelectorAll('.sell-catProp-item')].find(e => (e.innerText||'').indexOf(label) >= 0);
      return box ? (box.innerText||'').replace(/\n/g,' ').slice(0,40) : null;
    }""", label)
    return {"fiber": r, "num": got_num, "want": "%s%s" % (num, unit)}, ok

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--cid", default="110210")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--with-sku", action="store_true", help="同时创建 SKU 规格（键盘类目需先切属性）")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    a = ap.parse_args()

    it = load_item(a.item)
    if not it: raise SystemExit("找不到商品 %s" % a.item)

    title = (it.title or "")[:60]
    params = {p.name: p.value for p in (it.params or [])}
    brand = params.get("品牌") or "无品牌/无注册商标"
    model = params.get("型号") or ""
    model2 = ""
    for p in (it.params or []):
        if p.name == "型号" and p.value != model:
            model2 = p.value; break
    scene = params.get("适用场景") or "通用"
    mech = params.get("是否机械键盘") or "机械键盘"
    ergo = params.get("是否支持人体工程学") or "是"
    presale = params.get("质保期限") or "12个月"
    presale = presale if presale.endswith(("个月", "年")) else "12个月"
    min_price = min([s.price for s in (it.skus or []) if s.price] or [it.price.min or 0])
    total_stock = sum([s.quantity or 0 for s in (it.skus or [])]) or 100

    log("=" * 62)
    log("商品 %s  %s" % (a.item, title[:36]))
    log("类目 %s  品牌 %s  型号 %s / %s" % (a.cid, brand, model, model2))
    log("适用场景 %s  机械 %s  人体工学 %s  质保 %s" % (scene, mech, ergo, presale))
    log("价格 %s  库存 %s" % (min_price, total_stock))
    log("=" * 62)

    with BrowserSession(cdp_url=a.cdp, reuse_url_contains="publish.htm") as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        # 关键：goto 到「当前已经在这个 URL」的页面不会真正重载，会保留上次
        # 残留的抽屉/浮层/半填状态（表现为「型号框 None」「主色框消失」
        # 「图片空间点了不弹」）。先跳 about:blank 强制卸载，再进发布页。
        try:
            page.goto("about:blank", wait_until="commit", timeout=15000)
            time.sleep(0.6)
        except Exception:
            pass
        page.goto(publish_url_for(int(a.cid)), wait_until="domcontentloaded", timeout=60000)

        # 等骨架
        for i in range(30):
            n = page.evaluate(WAIT_READY)
            if n >= 5: break
            time.sleep(1.0)
        log("[1] 页面就绪 (catProp=%s)" % page.evaluate(WAIT_READY))
        time.sleep(1.5)

        fields = page.evaluate(LIST_INPUTS)
        def find_i(ph=None, lab=None, nm=None):
            for f in fields:
                if nm and nm in (f["name"] or ""): return f["i"]
                if ph is not None and f["ph"] == ph: return f["i"]
                if lab and lab in (f["lab"] or ""): return f["i"]
            return None

        # 标题
        ti = find_i(ph="最多允许输入30个汉字（60字符）")
        if ti is not None: log("[2] 标题 -> %s" % set_input(page, ti, title))
        else: log("[2] [XX] 找不到标题框")

        # 4 个必填 select
        for label, want in [("品牌", brand), ("适用场景", scene), ("是否机械键盘", mech), ("是否支持人体工程学", ergo)]:
            ok, msg = pick_select(page, label, want)
            log("[3] %-10s -> %s %s" % (label, "OK" if ok else "XX", msg))

        # 型号
        fields = page.evaluate(LIST_INPUTS)
        mi = find_i(nm="p-20000~1") or find_i(nm="p-656059583")
        if mi is not None and model:
            log("[4] 型号 -> %s" % set_input(page, mi, model))
        else:
            log("[4] [XX] 型号框 %s" % mi)
        if model2:
            fields = page.evaluate(LIST_INPUTS)
            for f in fields:
                if "p-20000" in (f["name"] or "") or "p-656059583" in (f["name"] or ""):
                    if (f["val"] or "") == "":
                        log("[4b] 型号2 -> %s" % set_input(page, f["i"], model2)); break

        # 质保期限
        wr, wtxt = fill_warranty(page, "质保期限", presale)
        log("[5] 质保期限 -> %s  (回读: %s)" % (json.dumps(wr, ensure_ascii=False), wtxt))

        # 价格 / 库存
        for unit, val in [("元", min_price), ("件", total_stock)]:
            r = page.evaluate(r"""(arg) => {
              document.querySelectorAll('[data-dsh-u]').forEach(e => e.removeAttribute('data-dsh-u'));
              const u = [...document.querySelectorAll('span,div')].filter(e => (e.innerText||'').trim() === arg.unit && e.children.length === 0);
              for (const el of u) {
                let n = el, i = 0;
                for (; i < 6 && n; i++) {
                  const ins = n.querySelectorAll && n.querySelectorAll('input');
                  if (ins && ins.length) {
                    const t = [...ins].find(x => x.getBoundingClientRect().width > 80);
                    if (t) { t.setAttribute('data-dsh-u','1'); return {ok:true}; }
                  }
                  n = n.parentElement;
                }
              }
              return {ok:false};
            }""", {"unit": unit})
            if r.get("ok"):
                page.locator('[data-dsh-u="1"]').first.evaluate("el => el.scrollIntoView({block:'center'})")
                time.sleep(0.4)
                page.locator('[data-dsh-u="1"]').first.click(force=True)
                time.sleep(0.35)
                page.keyboard.press("Control+A"); page.keyboard.press("Delete")
                time.sleep(0.25)
                page.keyboard.type(str(val), delay=45)
                time.sleep(0.3)
                log("[6] %s -> %s" % (unit, page.locator('[data-dsh-u="1"]').first.input_value()))
            else:
                log("[6] [XX] 找不到 %s 的输入框" % unit)

        # ============ SKU（可选）============
        # 键盘类目的 SKU 抽屉默认塞了「是否客制化键盘 / 热插拔轴体」两个属性，
        # 必须先取消它们再加「颜色分类」，否则建出来的表格维度不对。
        if a.with_sku:
            log("[6b] 建 SKU ...")
            colors = []
            if it.sale_props:
                _sp = it.sale_props[0]
                _d = _sp if isinstance(_sp, dict) else _sp.model_dump()
                colors = [v.get("name") if isinstance(v, dict) else getattr(v, "name", "")
                          for v in (_d.get("values") or [])]
                colors = [c for c in colors if c]
            log("     颜色 %d 个" % len(colors))
            if colors:
                # 打开抽屉
                if not page.evaluate(SKU_OPEN):
                    log("     [XX] 找不到「+ 创建规格」")
                else:
                    time.sleep(3.0)
                    for _nm in ("是否客制化键盘", "热插拔轴体"):
                        page.evaluate(SKU_CHIP, _nm); time.sleep(1.1)
                    page.evaluate(SKU_CHIP, "颜色分类"); time.sleep(2.0)
                    for _c in colors:
                        _sel = 'input[placeholder="主色(必选)"]'
                        if page.locator(_sel).count() == 0:
                            log("     [XX] 主色框消失"); break
                        _inp = page.locator(_sel).last
                        _inp.scroll_into_view_if_needed(timeout=5000)
                        _inp.click(force=True, timeout=5000)
                        page.keyboard.press("Control+A"); page.keyboard.press("Delete")
                        page.keyboard.type(_c, delay=22)
                        _bt = page.locator("button.next-btn.add")
                        if _bt.count():
                            try: _bt.first.click(force=True, timeout=5000)
                            except Exception: pass
                        time.sleep(0.3)
                    log("     填入 %d / %d" % (
                        len([v for v in [page.locator('input[placeholder="主色(必选)"]').nth(i).input_value()
                                         for i in range(page.locator('input[placeholder="主色(必选)"]').count())] if v]),
                        page.locator('input[placeholder="主色(必选)"]').count()))
                    if page.evaluate(SKU_CONFIRM):
                        time.sleep(3.5)
                        log("     抽屉关闭 = %s" % page.evaluate(
                            "() => !document.querySelector('.sku-decouple-drawer, .next-drawer')"))

            # 价格 / 库存 / 分类
            _cur = page.evaluate(SKU_ROWS)
            log("     SKU 表格 %d 行" % len(_cur))
            for _i, _s in enumerate(it.skus or []):
                if _i >= len(_cur): break
                _d = _s if isinstance(_s, dict) else _s.model_dump()
                _p = float(_d.get("price") or 0)
                _q = int(_d.get("quantity") or 0)
                _pt = ("%d" % _p) if _p == int(_p) else ("%.2f" % _p)
                _info = page.evaluate(SKU_SCROLL, _i)
                if not isinstance(_info, dict) or len(_info.get("inputs") or []) < 2:
                    continue
                _in = _info["inputs"]
                _px, _py, _pw, _ph = _in[0][:4]
                page.mouse.click(_px + _pw // 2, _py + _ph // 2); time.sleep(0.12)
                page.keyboard.press("Control+A"); page.keyboard.press("Delete")
                page.keyboard.type(_pt, delay=22); time.sleep(0.12)
                _info2 = page.evaluate(SKU_SCROLL, _i)
                _qx, _qy, _qw, _qh = (_info2.get("inputs") or _in)[1][:4]
                page.mouse.click(_qx + _qw // 2, _qy + _qh // 2); time.sleep(0.12)
                page.keyboard.press("Control+A"); page.keyboard.press("Delete")
                page.keyboard.type(str(_q), delay=22); time.sleep(0.12)
                _v = [x[5] for x in ((page.evaluate(SKU_SCROLL, _i) or {}).get("inputs") or [])
                      if isinstance(x, list) and len(x) >= 6]
                log("     行%-2d %-7s %-5s %s" % (_i, _v[0] if _v else "?", _v[1] if len(_v) > 1 else "?",
                                                  "" if _v and _v[1] == str(_q) else "<<<"))

            # SKU 分类（带一次重试）
            for _pass in range(2):
                _miss = []
                for _i in range(len(page.evaluate(SKU_ROWS))):
                    if not page.evaluate(SKU_CLASS_OF, _i):
                        _miss.append(_i)
                if not _miss:
                    break
                for _i in _miss:
                    _tr = page.locator("table tbody tr").nth(_i)
                    _sel = _tr.locator(".next-select").first
                    try:
                        _sel.scroll_into_view_if_needed(timeout=6000)
                        _box = _sel.bounding_box()
                        if not _box:
                            log("     分类 行%-2d no-box" % _i); continue
                        page.mouse.click(_box["x"] + 8, _box["y"] + _box["height"] / 2)
                        time.sleep(1.7)
                        _opts = page.evaluate(SKU_OPT_LIST)
                        _want = "单品" if "单品" in _opts else (_opts[0] if _opts else "")
                        if _want:
                            page.evaluate(SKU_OPT_CLICK, _want)
                            time.sleep(0.3)
                        page.keyboard.press("Escape")
                        time.sleep(0.2)
                        log("     分类 行%-2d -> %s" % (_i, page.evaluate(SKU_CLASS_OF, _i) or "empty"))
                    except Exception as _e:
                        log("     分类 行%-2d 失败 %s" % (_i, type(_e).__name__))
                time.sleep(0.5)
            log("[6b] SKU 完成")

            # 键盘类目还有一列「键盘类型」是必填的自由文本框（ph='请输入'），
            # 不填会报「【某个 SKU】: 键盘类型不能为空」，计 12 条错误。
            _ktype = params.get("键盘类型") or params.get("是否机械键盘") or "机械键盘"
            _fill_cnt = 0
            # 先把第 0 行的所有 input 打出来，确认「键盘类型」那格到底是哪个
            # （每行有 28 个 input，placeholder 可能重名）
            _r0 = page.evaluate(SKU_ROWS)
            if _r0:
                log("     行0 inputs: %s" % json.dumps(_r0[0].get("inputs", [])[:8], ensure_ascii=False)[:600])
            # 「键盘类型」那格到底是什么控件？dump 表头 + 该 input 的 outerHTML
            _dbg = page.evaluate(r"""() => {
              const t = document.querySelector('table');
              if (!t) return {err: 'no table'};
              const ths = [...t.querySelectorAll('thead th, thead td')].map(e => (e.innerText||'').trim()).slice(0, 14);
              const tr0 = t.querySelectorAll('tbody tr')[0];
              const in0 = tr0 ? tr0.querySelectorAll('input')[3] : null;
              return {ths: ths, html: in0 ? in0.outerHTML.slice(0, 260) : null,
                      ro: in0 ? in0.readOnly : null, dis: in0 ? in0.disabled : null};
            }""")
            log("     表头: %s" % json.dumps((_dbg or {}).get("ths"), ensure_ascii=False))
            log("     input[3]: ro=%s dis=%s html=%s" % ((_dbg or {}).get("ro"), (_dbg or {}).get("dis"), (_dbg or {}).get("html")))

            def _kt_pass():
                """填一遍所有空的「键盘类型」格子，返回本次填成功的个数。

                坑：这格是每行第 4 个 input（index 3，placeholder='请输入'，
                宽约 146px）。页面滚动容器不是 window，scroll_into_view_if_needed
                对它无效（rect.y 仍是 -206），所以先用 tr.scrollIntoView 把整行
                滚到中间，再取新 rect 用 mouse.click 点中心。
                """
                n = 0
                rows = page.evaluate(SKU_ROWS)
                for _i in range(len(rows)):
                    ins = rows[_i].get("inputs") or []
                    if len(ins) < 4:
                        continue
                    if (ins[3].get("value") or "").strip():
                        continue
                    try:
                        page.evaluate(SKU_SCROLL, _i)
                        time.sleep(0.35)
                        # 「键盘类型」的控件是 compound：
                        #   input[role=combobox]  ← 只负责回显，点它不展开
                        #   span.next-select-auto-complete[aria-haspopup=true]  ← 真正要点的
                        # 用 Playwright locator 让它自己算滚动和 iframe 偏移。
                        _sel = page.locator("table tbody tr").nth(_i).locator(
                            "span.next-select-auto-complete").first
                        _sel.scroll_into_view_if_needed(timeout=6000)
                        time.sleep(0.2)
                        _sel.click(force=True, timeout=5000)
                        time.sleep(0.9)
                        # 点 span 有时 React 事件没接住（下拉不出来）。
                        # span 上有 __reactEventHandlers 里的 onClick，直接调它最稳。
                        _oc = page.evaluate(r"""(i) => {
                          const t = document.querySelector('table');
                          const tr = t ? t.querySelectorAll('tbody tr')[i] : null;
                          const inp = tr ? tr.querySelectorAll('input')[3] : null;
                          const sp = inp ? inp.closest('span.next-select') : null;
                          if (!sp) return 'no-span';
                          const k = Object.keys(sp).filter(x => x.startsWith('__reactEventHandlers'))[0];
                          if (!k || typeof sp[k].onClick !== 'function') return 'no-onClick';
                          try {
                            sp[k].onClick({currentTarget: sp, target: sp, nativeEvent: {},
                                           preventDefault(){}, stopPropagation(){}, persist(){},
                                           defaultPrevented: false});
                            return 'ok';
                          } catch (e) { return 'err:' + String(e).slice(0, 40); }
                        }""", _i)
                        time.sleep(1.0)
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Delete")
                        time.sleep(0.2)
                        page.keyboard.type(str(_ktype), delay=45)
                        time.sleep(0.9)
                        # input[3] 是 role=combobox（aria-expanded=false），光打字不生效，
                        # 且打字后下拉有时不弹 —— 补「点左边缘 + ArrowDown」最多 3 轮。
                        for _k in range(3):
                            _exp = page.evaluate(r"""() => {
                              const ov = [...document.querySelectorAll('.next-overlay-wrapper')]
                                         .find(o => o.classList.contains('opened'));
                              return (ov && ov.querySelectorAll('[class*="options-item"]').length) ? 1 : 0;
                            }""")
                            if _exp:
                                break
                            # 下拉没展开就再点一次外层 span（点 input 无效）
                            try:
                                _sel.click(force=True, timeout=4000)
                            except Exception:
                                pass
                            time.sleep(0.7)
                            page.keyboard.press("ArrowDown")
                            time.sleep(0.7)
                        # input[3] 是 role=combobox 的下拉搜索框，光打字不生效，
                        # 必须在弹出的 .next-overlay-wrapper.opened 里点选项。
                        _hit = page.evaluate(r"""(want) => {
                          // 下拉可能挂在 portal 上、class 不一定带 opened，
                          // 所以直接全局找「可见的 options-item」。
                          const all = [...document.querySelectorAll('[class*="options-item"]')];
                          const vis = all.filter(e => {
                            const r = e.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                          });
                          if (!vis.length) return {err: 'no-items', total: all.length};
                          const titles = vis.map(e => e.getAttribute('title') || (e.innerText||'').trim());
                          let k = titles.findIndex(t => t === want);
                          if (k < 0) k = titles.findIndex(t => t && t.indexOf(want) >= 0);
                          if (k < 0) k = 0;
                          vis[k].setAttribute('data-dsh-kt', '1');
                          return {ok: true, picked: titles[k], all: titles.slice(0, 10)};
                        }""", str(_ktype))
                        if not (_hit or {}).get("ok"):
                            if _i == 0:
                                _pc = page.evaluate(r"""(i) => {
                                  const t = document.querySelector('table');
                                  const tr = t ? t.querySelectorAll('tbody tr')[i] : null;
                                  const inp = tr ? tr.querySelectorAll('input')[3] : null;
                                  const sp = inp ? inp.closest('span.next-select') : null;
                                  if (!sp) return {err: 'no span'};
                                  const k = Object.keys(sp).filter(x => x.startsWith('__react'));
                                  let out = {keys: k, props: null, propsKeys: null};
                                  if (k.length) {
                                    const p = sp[k[0]].memoizedProps || {};
                                    out.propsKeys = Object.keys(p).slice(0, 40);
                                    out.props = {disabled: p.disabled, value: p.value,
                                                 options: (p.options || p.dataSource || []).length};
                                  }
                                  const r = sp.getBoundingClientRect();
                                  out.rect = [Math.round(r.x), Math.round(r.y),
                                              Math.round(r.width), Math.round(r.height)];
                                  return out;
                                }""", _i)
                                log("     行0 span 诊断: %s" % json.dumps(_pc, ensure_ascii=False)[:600])
                            log("     键盘类型 行%-2d 选项异常 %s" % (_i, json.dumps(_hit, ensure_ascii=False)[:120]))
                            page.keyboard.press("Escape")
                            continue
                        page.locator("[data-dsh-kt='1']").first.click(force=True, timeout=5000)
                        time.sleep(0.4)
                        page.evaluate("() => document.querySelectorAll('[data-dsh-kt]').forEach(e => e.removeAttribute('data-dsh-kt'))")
                        _now = page.evaluate(SKU_ROWS)[_i]["inputs"][3]["value"]
                        if (_now or "").strip():
                            n += 1
                            if _i == 0:
                                log("     键盘类型 选项: %s  -> 选中 %s" % (json.dumps(_hit.get("all"), ensure_ascii=False), _hit.get("picked")))
                        else:
                            log("     键盘类型 行%-2d 回读空" % _i)
                    except Exception as _e2:
                        log("     键盘类型 行%-2d 失败 %s: %s" % (_i, type(_e2).__name__, str(_e2)[:60]))
                return n

            # 第一行经常吞字（滚动/焦点没稳住），跑两轮，第二轮只补空的
            for _p in range(2):
                _got = _kt_pass()
                _fill_cnt += _got
                if _got == 0:
                    break
                log("     键盘类型第 %d 轮 填了 %d 行" % (_p + 1, _got))
            log("     键盘类型 -> %s（累计填了 %d 行）" % (_ktype, _fill_cnt))

        # 主图
        names = [os.path.basename(p) for p in sorted(glob.glob("data/media/%s/main_*.jpg" % a.item))[:5]]
        log("[7] 主图 %d 张本地: %s" % (len(names), names))
        local_paths = sorted(glob.glob("data/media/%s/main_*.jpg" % a.item))[:5]
        _uploaded = False
        for name in names:
            st = slot_state(page)
            if st["filled"] >= 5: break
            if not page.evaluate("""() => { const o=document.querySelector('.next-overlay-wrapper.opened');
                  return !!o && !!o.querySelector('iframe[src*="sucai-selector"]'); }"""):
                # 图片空间 iframe 偶尔点了不弹（页面状态复杂时 React 事件没接住）。
                # 重试 3 次，每次重新调一次 React onClick 并等 2 秒。
                for _try in range(3):
                    page.evaluate(OPEN_SPACE)
                    time.sleep(2.0)
                    ok = page.evaluate("""() => { const o=document.querySelector('.next-overlay-wrapper.opened');
                          return !!o && !!o.querySelector('iframe[src*="sucai-selector"]'); }""")
                    if ok:
                        time.sleep(1.5)
                        break
                    log("    图片空间未弹出，重试 %d/3" % (_try + 1))
                else:
                    log("    [XX] 图片空间三次都没弹出"); break
            # 图片空间 iframe 可能出现多个（每点一次槽位就新增一个），必须用最后一个
            fl = page.frame_locator("iframe[src*='sucai-selector']").last
            titles = fl.locator("[class*='PicList_title_wrap']")
            try:
                titles.first.wait_for(timeout=25000)
            except Exception:
                log("    [XX] %s 图片列表 25s 未出现" % name)
                page.keyboard.press("Escape"); time.sleep(1.0); continue
            # 空间是店铺共享的，里面已经有 100+ 张历史图（详情图会把它挤满），
            # 先把这 5 张传一遍，保证能按 sha 找到。
            if not _uploaded and local_paths:
                try:
                    with page.expect_file_chooser(timeout=30000) as _fc:
                        fl.locator("button:has-text('本地上传')").first.click(force=True, timeout=10000)
                    _fc.value.set_files(local_paths)
                    log("    已投喂 %d 张主图到图片空间" % len(local_paths))
                    for _w in range(30):
                        time.sleep(1.0)
                        try:
                            _t = fl.locator("body").inner_text(timeout=3000)
                        except Exception:
                            continue
                        if "个文件上传成功" in _t or "上传成功" in _t:
                            log("    主图上传完成（%ds）" % (_w + 1))
                            break
                    for _s in ("[class*='UploadP'] button", "button:has-text('完成')"):
                        try:
                            fl.locator(_s).last.click(force=True, timeout=4000)
                            break
                        except Exception:
                            continue
                    time.sleep(3.0)
                    _uploaded = True
                except Exception as _e:
                    log("    上传主图失败: %s" % str(_e).split("\n")[0][:60])
            hit = -1
            # 本地名是 main_01_<sha16>.jpg（带序号），空间里可能是旧名 main_<sha16>.jpg，
            # 所以按 sha16 片段匹配，而不是整串相等。
            _want = name
            _mm = re.match(r"^(?:main|detail)_(\d+)_([0-9a-f]{16})\.jpg$", name)
            if _mm:
                _want = _mm.group(2)
            for i in range(min(titles.count(), 200)):
                if _want in titles.nth(i).inner_text(): hit = i; break
            if hit < 0: log("    %s 未找到（按 %s 匹配）" % (name, _want)); continue
            titles.nth(hit).evaluate(MARK_CARD)
            try:
                fl.locator("[data-dsh-pick='1']").first.click(force=True, timeout=8000)
                time.sleep(2.2)
            except Exception as e:
                log("    %s 点击失败 %s" % (name, str(e).split("\n")[0][:50]))
            after = slot_state(page)
            log("    %s  filled %d->%d %s" % (name[:22], st["filled"], after["filled"], "OK" if after["filled"] > st["filled"] else "!!"))
            if after["filled"] >= 5: break
            page.keyboard.press("Escape"); time.sleep(1.0)
        log("[7] 主图最终 %s" % json.dumps(slot_state(page), ensure_ascii=False))

        # 详情
        # 关键：平台有防盗图保护，详情 HTML 里直接写原卖家的 img.alicdn.com URL 会被
        # 判「详情中引用了他人图片」而拒绝保存。必须先把图上传到自己的图片空间，
        # 再用上一步拿到的自家 URL（图片空间 ID = 2858247125）。
        # detail_urls.json 由 find_detail_urls.py 按本地文件名 → 自家原图 URL 生成。
        dimgs = []
        # 优先读按商品分开的表（rebuild_detail_urls.py 生成），再退回全局表
        _urlmap_path = None
        for _cand in (os.path.join("data", "publish_poc", "detail_urls_%s.json" % a.item),
                      os.path.join("data", "publish_poc", "detail_urls.json")):
            if os.path.isfile(_cand):
                _urlmap_path = _cand
                break
        if _urlmap_path:
            with open(_urlmap_path, encoding="utf-8") as _f:
                _urlmap = json.load(_f)
            # 按本地文件名排序 = 原始顺序（文件名带 detail_01/02/… 序号）
            _locals = []
            for _d in (it.detail_images or []):
                _lp = _d.get("local_path") if isinstance(_d, dict) else getattr(_d, "local_path", None)
                if _lp:
                    _locals.append(_lp)
            _missing = []
            for _lp in _locals:
                _own = _urlmap.get(os.path.basename(_lp))
                if _own:
                    dimgs.append(_own)
                else:
                    _missing.append(os.path.basename(_lp))
            log("[8] 用自家图片空间 URL %d / %d 张（%s）" % (len(dimgs), len(_locals),
                                                          os.path.basename(_urlmap_path)))
            if _missing:
                log("[8] [!!] %d 张没有自家 URL: %s" % (len(_missing), ", ".join(_missing[:3])))
        if not dimgs:
            # 兜底：没有上传记录时退回原 URL（会被防盗图拦，仅用于调试）
            dimgs = [d.get("url") if isinstance(d, dict) else getattr(d, "url", None)
                     for d in (it.detail_images or [])]
            dimgs = [u for u in dimgs if u]
            log("[8] [!!] 没有 detail_urls.json，用原始 URL（会被防盗图拦截）")
        if dimgs:
            html = "".join('<p><img src="%s" style="width:100%%;display:block;"/></p>' % u for u in dimgs)
            log("[8] 详情 %d 图" % len(dimgs))
            try:
                page.get_by_text("源码导入", exact=True).first.click(force=True, timeout=8000)
                # 对话框里的 CKEditor 是异步渲染的，点完立刻取 contenteditable 会拿到 null，
                # 表现为「日志说导入完成、实际编辑器是空的」。
                page.wait_for_selector('[contenteditable="true"]', state="visible", timeout=15000)
                time.sleep(1.2)
                _w = page.evaluate(r"""(h) => {
                  const ed = document.querySelector('[contenteditable="true"]');
                  if (!ed) return 'no editor';
                  ed.focus(); ed.innerHTML = h;
                  ['input','change','keyup','blur'].forEach(t => ed.dispatchEvent(new Event(t, {bubbles:true})));
                  return 'ok len=' + ed.innerHTML.length;
                }""", html)
                log("[8] 编辑器写入: %s" % _w)
                time.sleep(1.2)
                page.get_by_text("导入", exact=True).last.click(force=True, timeout=8000)
                # 等详情渲染完（"正在载入详情" 消失）
                for k in range(40):
                    time.sleep(1.0)
                    busy = page.evaluate(r"""() => {
                      const t = document.body ? document.body.innerText : '';
                      return t.indexOf('正在载入详情') >= 0 || t.indexOf('正在载入') >= 0;
                    }""")
                    if not busy:
                        break
                log("[8] 导入完成（等 %ds）" % (k + 1))
                time.sleep(2.0)
            except Exception as e:
                log("[8] [XX] %s" % str(e).split("\n")[0][:70])
        else:
            log("[8] 无详情图，跳过")

        # 提交
        if a.submit:
            log("[9] 点提交...")
            page.get_by_text("提交宝贝信息", exact=True).first.click(force=True, timeout=10000)
            time.sleep(8)
            url = page.url
            log("[9] 落地: %s" % url[:120])
            if "success.htm" in url:
                log("[9] ✅ 发布成功")
            else:
                # 错误面板不在固定文案里，直接扫整页找「不能为空 / 请检查 / 请修改 / 必填 / 失败」
                errs = page.evaluate(r"""() => {
                  const t = document.body ? document.body.innerText : '';
                  const pats = ['不能为空','请检查','请修改','必填项','保存失败','发布失败','请重新','不支持','不符合'];
                  const out = [];
                  const lines = t.split('\n');
                  for (let i = 0; i < lines.length; i++) {
                    const s = lines[i].trim();
                    if (!s || s.length > 120) continue;
                    if (pats.some(p => s.indexOf(p) >= 0)) out.push(s);
                  }
                  return Array.from(new Set(out)).slice(0, 25);
                }""")
                log("[9] ✗ 未成功，命中 %d 条：" % len(errs or []))
                for _e in (errs or []):
                    log("      · %s" % _e)
        else:
            log("[9] 未加 --submit，停在提交前")

        try:
            page.screenshot(path="data/publish_poc/kb_final.png", timeout=20000, animations="disabled")
            log("[OK] 截图 data/publish_poc/kb_final.png")
        except Exception as e:
            log("[!] 截图 %s" % str(e).split("\n")[0][:60])

if __name__ == "__main__":
    main()
