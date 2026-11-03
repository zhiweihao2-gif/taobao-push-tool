"""一条命令把 Item 发到淘宝店铺。

    python scripts/publish_one.py --item 902412139914 --cid 200670001
    python scripts/publish_one.py --item 902412139914 --cid 200670001 --submit

默认只填到「提交前」，加 --submit 才真提交。

前置条件：
  1. scripts/start_chrome_debug.py 已启动带 9222 的浏览器
  2. 该浏览器里卖家中心已扫码登录

设计取舍（都是踩坑换来的）：
  - 视口必须 ≥1200 宽。988 宽时「确认，下一步」被裁到视口外，elementFromPoint 返回 null。
  - 所有文本字段用键盘逐字符输入（human_type），直接 set value 不触发 React。
  - 品牌不是普通输入框：主框只回显，必须在下拉里那个空 placeholder 的搜索框敲字。
  - 「适配轴体类型」是 readonly 多选，点左边 x+8 才展开（中心点落在已选 tag 上、右边缘是箭头空区）。
  - 选项不是独立文本节点，get_by_text 匹配不到 → 打 data-dsh-opt 标记再点。
  - 主图点开的是图片空间 iframe（realTimeSel=true，点图直接回填），不是系统文件选择器。
  - 上传后有「上传结果」浮层挡住按钮，且高度为 0 → 必须 click(force=True)。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.storage import load_item  # noqa: E402
from src.publish.browser_pub import (  # noqa: E402
    BrowserSession,
    human_pause,
    human_type,
    publish_url_for,
    set_value_native,
)

OUT_DIR = ROOT / "data" / "publish_poc"

# ---------- 通用工具 ----------

_MARK_INPUTS_JS = r"""
() => {
  // 先清掉上一轮标记，否则同一元素会带两个 idx，locator 触发 strict mode violation
  document.querySelectorAll('[data-dsh-idx]').forEach(el => el.removeAttribute('data-dsh-idx'));
  let n = 0;
  document.querySelectorAll('input,textarea').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return;
    el.setAttribute('data-dsh-idx', String(n++));
  });
  return n;
}
"""

_LIST_INPUTS_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('input,textarea').forEach(el => {
    const idx = el.getAttribute('data-dsh-idx');
    if (idx === null) return;
    out.push({
      idx: +idx,
      name: el.getAttribute('name') || '',
      ph: el.getAttribute('placeholder') || '',
      value: (el.value || '').slice(0, 50),
      readonly: !!el.readOnly,
      type: el.getAttribute('type') || '',
    });
  });
  return out;
}
"""

_MARK_OPTS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  // 只认真正展开的那一个 overlay。用 [class*="overlay"] 会把整页 tab 都算进来。
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return { err: 'no overlay', opts: [] };
  const cand = [...ov.querySelectorAll('li,[role=option],[class*="menu-item"],[class*="option"]')];
  const out = [];
  cand.forEach((el, i) => {
    const t = clean(el.innerText);
    if (!t) return;
    el.setAttribute('data-dsh-opt', String(i));
    out.push({ i, text: t.slice(0, 40) });
  });
  return { count: out.length, opts: out, ovText: clean(ov.innerText).slice(0, 200) };
}
"""

# 按 label 文本找控件（比按 idx 猜可靠）
_LOCATE_BY_LABEL = r"""
(label) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const labels = [...document.querySelectorAll('*')].filter(
    e => e.children.length === 0 && clean(e.innerText) === label);
  if (!labels.length) return { err: 'label not found' };
  let p = labels[0];
  for (let k = 0; k < 9 && p; k++) {
    const inp = p.querySelector && p.querySelector('input[role=combobox]');
    if (inp) {
      const sel = inp.closest('.next-select');
      if (!sel) { p = p.parentElement; continue; }
      sel.scrollIntoView({ block: 'center', behavior: 'instant' });
      const r = sel.getBoundingClientRect();
      return { rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
    }
    p = p.parentElement;
  }
  return { err: 'no select' };
}
"""


def log(msg: str) -> None:
    print(msg, flush=True)


# 按单位文本（「元」/「件」）找右边那个输入框。
# 不能按「没有 placeholder 的框」顺序猜 —— 同组还有购买须知/商家编码/条形码，
# 猜错会把库存写进「购买须知」，提交时报「文本包含违规信息: 834」。
_LOCATE_BY_UNIT_JS = r"""
(unit) => {
  const leaves = [...document.querySelectorAll('*')].filter(
    e => e.children.length === 0 && (e.innerText || '').trim() === unit);
  for (const l of leaves) {
    let p = l.parentElement;
    for (let k = 0; k < 4 && p; k++) {
      const ins = [...p.querySelectorAll('input')].filter(i => {
        const r = i.getBoundingClientRect();
        return r.width > 80 && r.height > 14;
      });
      if (ins.length) {
        const inp = ins[ins.length - 1];
        inp.setAttribute('data-dsh-unit', unit);
        const r = inp.getBoundingClientRect();
        return { ok: true, value: (inp.value || '').slice(0, 30),
                 rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] };
      }
      p = p.parentElement;
    }
  }
  return { ok: false, reason: 'no input near ' + unit };
}
"""


def fill_by_unit(page, unit: str, text: str, label: str) -> bool:
    try:
        page.evaluate("() => document.querySelectorAll('[data-dsh-unit]').forEach(e => e.removeAttribute('data-dsh-unit'))")
        info = page.evaluate(_LOCATE_BY_UNIT_JS, unit)
        log("    %s 定位(%s): %s" % (label, unit, json.dumps(info, ensure_ascii=False)[:160]))
        if not info.get("ok"):
            return False
        loc = page.locator('[data-dsh-unit="%s"]' % unit)
        loc.scroll_into_view_if_needed(timeout=4000)
        loc.click(timeout=4000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        for ch in text:
            page.keyboard.type(ch)
            page.wait_for_timeout(25)
        page.wait_for_timeout(350)
        got = loc.input_value()
        ok = got.strip() == text.strip()
        log("    %s %-8s -> %r (%s)" % ("[OK]" if ok else "[!!]", label, got, "一致" if ok else "不一致"))
        return ok
    except Exception as e:
        log("    [XX] %s 异常 %s" % (label, str(e).split("\n")[0][:100]))
        return False


def mark_inputs(page) -> int:
    return page.evaluate(_MARK_INPUTS_JS)


def list_inputs(page) -> list[dict]:
    return page.evaluate(_LIST_INPUTS_JS)


def find_input(inputs: list[dict], *, name=None, ph=None, idx=None,
               ph_contains=None, exclude_ph=None) -> dict | None:
    for it in inputs:
        if idx is not None and it["idx"] != idx:
            continue
        if name is not None and it["name"] != name:
            continue
        if ph is not None and it["ph"] != ph:
            continue
        if ph_contains is not None and ph_contains not in it["ph"]:
            continue
        if exclude_ph is not None and it["ph"] == exclude_ph:
            continue
        return it
    return None


def type_into(page, idx: int, text: str, label: str) -> bool:
    sel = '[data-dsh-idx="%d"]' % idx
    try:
        loc = page.locator(sel)
        loc.scroll_into_view_if_needed(timeout=4000)
        loc.click(timeout=4000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        for ch in text:
            page.keyboard.type(ch)
            page.wait_for_timeout(20)
        page.wait_for_timeout(250)
        got = loc.input_value()
        ok = got.strip() == text.strip()
        log("    %s %-14s -> %r (%s)" % ("[OK]" if ok else "[!!]", label, got, "一致" if ok else "不一致"))
        return ok
    except Exception as e:
        log("    [XX] %-14s %s" % (label, str(e).split("\n")[0][:100]))
        return False


# 标记下拉里那个真正的搜索框（带放大镜那个，placeholder 为空）
_MARK_BRAND_SEARCH_JS = r"""
() => {
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return { ok: false, reason: 'no overlay' };
  const ins = [...ov.querySelectorAll('input')].filter(i => {
    const r = i.getBoundingClientRect();
    return r.width > 40 && r.height > 12;
  });
  if (!ins.length) return { ok: false, reason: 'no input in overlay' };
  ins[0].setAttribute('data-dsh-brand-search', '1');
  const r = ins[0].getBoundingClientRect();
  return { ok: true, count: ins.length,
           rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
           ph: ins[0].getAttribute('placeholder') };
}
"""

# 过滤后点列表里第一个匹配项
_CLICK_FIRST_MATCH_JS = r"""
(want) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const ov = [...document.querySelectorAll('.next-overlay-wrapper')].find(o => o.classList.contains('opened'));
  if (!ov) return { ok: false, reason: 'no overlay' };
  const cand = [...ov.querySelectorAll('li,[role=option],[class*="menu-item"],[class*="option"]')];
  const items = cand.map(el => ({ el, t: clean(el.innerText) })).filter(x => x.t);
  const hit = items.find(x => x.t === want) || items.find(x => x.t.includes('无品牌')) || items[0];
  if (!hit) return { ok: false, reason: 'empty list', sample: clean(ov.innerText).slice(0, 200) };
  hit.el.setAttribute('data-dsh-brand-pick', '1');
  return { ok: true, text: hit.t.slice(0, 40), total: items.length,
           sample: items.slice(0, 8).map(x => x.t.slice(0, 20)) };
}
"""


def pick_brand(page, brand_text: str = "无品牌/无注册商标") -> bool:
    """品牌：主框只回显；下拉里那个带放大镜的 input 才是筛选框。

    踩过的坑：
      - 主框 `input[placeholder="请输入"]` 里敲字**不会**过滤列表。
      - 下拉里的筛选框 placeholder 是空字符串，页面上还有其他空 placeholder 的 input，
        按 placeholder 找会点错 → 必须限定在 `.next-overlay-wrapper.opened` 内找。
      - 列表是虚拟滚动，只渲染 A 开头那几十个，不筛选根本滚不到「无品牌」。
      - 选项文本是拼起来的，get_by_text 匹配不到，得用 JS 打标记再点。
    """
    log("  品牌 -> %s" % brand_text)
    try:
        main = page.locator('input[placeholder="请输入"]').first
        main.scroll_into_view_if_needed(timeout=4000)
        main.click(timeout=4000)
        page.wait_for_timeout(1500)

        m = page.evaluate(_MARK_BRAND_SEARCH_JS)
        log("    筛选框: %s" % json.dumps(m, ensure_ascii=False)[:200])
        if not m.get("ok"):
            log("    [XX] 找不到下拉里的筛选框")
            return False

        sb = page.locator('[data-dsh-brand-search="1"]')
        sb.click(timeout=3000)
        page.wait_for_timeout(300)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        for ch in "无品牌":
            page.keyboard.type(ch)
            page.wait_for_timeout(70)
        page.wait_for_timeout(1600)

        r = page.evaluate(_CLICK_FIRST_MATCH_JS, brand_text)
        log("    过滤后: %s" % json.dumps(r, ensure_ascii=False)[:220])
        if not r.get("ok"):
            log("    [XX] 过滤后没有选项")
            return False

        page.locator('[data-dsh-brand-pick="1"]').click(force=True, timeout=4000)
        page.wait_for_timeout(1000)

        v = main.input_value()
        log("    品牌回显 %r" % v)
        if v.strip():
            log("    [OK] 品牌已选")
            return True
        log("    [XX] 品牌没选中")
        return False
    except Exception as e:
        log("    [XX] 品牌异常 %s" % str(e).split("\n")[0][:120])
        return False


def pick_select_by_label(page, label: str, want: str) -> bool:
    """按 label 找到 readonly 多选下拉，点左边缘展开，选项打标记后点。

    踩过的坑：
      - 点中心不展开（落在已选 tag 上），点右边缘也不展开（箭头空区），只有 x+8 有效。
      - 选项文本是拼起来的，get_by_text 匹配不到。
      - overlay 必须只取 .next-overlay-wrapper.opened，否则会把整页 tab 算进选项。
    """
    log("  %s -> %s" % (label, want))
    try:
        info = page.evaluate(_LOCATE_BY_LABEL, label)
        if info.get("err"):
            log("    [XX] 定位失败: %s" % info["err"])
            return False
        x, y, w, h = info["rect"]
        page.mouse.click(x + 8, y + h / 2)
        page.wait_for_timeout(2200)

        m = page.evaluate(_MARK_OPTS_JS)
        opts = m.get("opts") or []
        if not opts:
            # 点中心再试一次
            page.mouse.click(x + w / 2, y + h / 2)
            page.wait_for_timeout(2200)
            m = page.evaluate(_MARK_OPTS_JS)
            opts = m.get("opts") or []
        log("    选项 %d 个: %s" % (len(opts), [o["text"][:12] for o in opts][:12]))
        if not opts:
            log("    [XX] 下拉没展开")
            return False

        target = next((o for o in opts if o["text"] == want), None)
        if target is None:
            target = next((o for o in opts if want in o["text"]), None)
        if target is None:
            log("    [XX] 没有 %r" % want)
            page.keyboard.press("Escape")
            return False
        page.locator('[data-dsh-opt="%d"]' % target["i"]).click(force=True, timeout=4000)
        page.wait_for_timeout(1000)
        log("    [OK] 已选 %r" % target["text"])
        return True
    except Exception as e:
        log("    [XX] %s 异常 %s" % (label, str(e).split("\n")[0][:100]))
        return False


# ---------- 主图 ----------

_UPLOAD_DONE_JS = r"""
() => {
  const b = document.body.innerText || '';
  return b.includes('上传成功') || b.includes('上传结果');
}
"""


def _click_done(fr) -> bool:
    loc = fr.get_by_text("完成", exact=True)
    n = loc.count()
    for i in range(n - 1, -1, -1):
        try:
            loc.nth(i).click(force=True, timeout=2500)
            return True
        except Exception:
            continue
    return False


def _clear_overlays(fr, page) -> int:
    cleared = 0
    for _ in range(3):
        try:
            open_cnt = fr.evaluate("() => document.querySelectorAll('.next-overlay-wrapper.opened').length")
        except Exception:
            open_cnt = 0
        if not open_cnt:
            break
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            cleared += 1
        except Exception:
            break
    return cleared


def _pick_card_by_name(fr, page, stem: str) -> bool:
    """在图片空间里按文件名前缀点卡片。

    不能点 cards.first —— 图片空间是店铺共享的，上次别的商品传的图还在里面，
    卡片顺序也不是「最新在前」，点第一次次点错，结果 5 张图只回填了 3 张。
    """
    try:
        cards = fr.locator('[class*="pic_imgBox"]')
        n = cards.count()
        for i in range(n):
            try:
                c = cards.nth(i)
                txt = c.get_attribute("title") or ""
                if not txt:
                    txt = c.inner_text(timeout=500) or ""
                # 卡片文字可能是 main_xxx... 截断形式，也可能是完整文件名
                if stem[:14] in txt:
                    c.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    return True
            except Exception:
                continue
        # 退一步：找不到名字就点第一张（至少别中断流程）
        log("    [..] 没按名字找到卡片，退回点第一张")
        cards.first.click(timeout=5000)
        page.wait_for_timeout(1200)
        return True
    except Exception as e:
        log("    [XX] 点卡片失败 %s" % str(e).split("\n")[0][:100])
        return False


def upload_main_images(page, files: list[Path], want: int = 5) -> int:
    """把本地图片传到图片空间并回填到 1:1 主图槽位。返回成功张数。"""
    files = [f for f in files if f.exists()][:want]
    if not files:
        log("  [XX] 没有可用主图文件")
        return 0

    done = 0
    for fi, fpath in enumerate(files, 1):
        log("  主图 %d/%d %s" % (fi, len(files), fpath.name))
        try:
            before = page.locator("[class*='sell-component-simply-images'] img").count()
            empties = page.locator("div.image-empty")
            if empties.count() == 0:
                log("    [..] 槽位已满")
                break
            empties.first.scroll_into_view_if_needed(timeout=4000)
            empties.first.click(timeout=5000)
            page.wait_for_timeout(2200)

            fr = None
            for f in page.frames:
                if "sucai-selector" in (f.url or "") or "sucai.wangpu" in (f.url or ""):
                    fr = f
                    break
            if fr is None:
                log("    [XX] 没找到图片空间 iframe")
                page.keyboard.press("Escape")
                continue

            _clear_overlays(fr, page)

            with page.expect_file_chooser(timeout=10000) as fc_info:
                up = fr.get_by_text("本地上传", exact=True)
                if up.count() == 0:
                    up = fr.locator("button:has-text('本地上传')")
                up.first.click(force=True, timeout=6000)
            fc = fc_info.value
            fc.set_files(str(fpath))
            log("    已选择文件，等上传…")

            ok_upload = False
            for _ in range(20):
                page.wait_for_timeout(700)
                if fr.evaluate(_UPLOAD_DONE_JS):
                    ok_upload = True
                    break
            txt = ""
            try:
                txt = fr.evaluate("() => (document.body.innerText || '').slice(0, 400)")
            except Exception:
                pass
            if "重复" in txt:
                log("    [..] 提示图片重复，直接选空间里已有的那张")
            _click_done(fr)
            page.wait_for_timeout(1000)

            stem = fpath.stem
            if not _pick_card_by_name(fr, page, stem):
                page.keyboard.press("Escape")
                continue
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
            except Exception:
                pass

            after = page.locator("[class*='sell-component-simply-images'] img").count()
            log("    槽位图 %d -> %d" % (before, after))
            if after > before:
                done += 1
                log("    [OK] 已回填")
            else:
                log("    [!!] 回填没生效")
        except Exception as e:
            log("    [XX] %s" % str(e).split("\n")[0][:120])
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

    log("  主图完成 %d 张" % done)
    return done


# ---------- 宝贝详情 ----------

def fill_detail(page, html: str) -> bool:
    log("  宝贝详情：源码导入")
    try:
        btn = page.get_by_text("源码导入", exact=True)
        if btn.count() == 0:
            log("    [XX] 找不到「源码导入」")
            return False
        btn.first.scroll_into_view_if_needed(timeout=4000)
        btn.first.click(timeout=5000)
        page.wait_for_timeout(1500)

        ed = page.locator("div.cke_wysiwyg_div").first
        if ed.count() == 0:
            ed = page.locator("[contenteditable='true']").first
        ed.scroll_into_view_if_needed(timeout=4000)
        ed.click(timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(html)
        page.wait_for_timeout(600)
        got = ed.inner_text()
        log("    写入 %d 字符（读回 %d）" % (len(html), len(got)))

        imp = page.get_by_text("导入", exact=True)
        n = imp.count()
        for i in range(n - 1, -1, -1):
            try:
                imp.nth(i).click(force=True, timeout=3000)
                page.wait_for_timeout(1200)
                log("    [OK] 已导入")
                return True
            except Exception:
                continue
        log("    [XX] 点不到「导入」")
        return False
    except Exception as e:
        log("    [XX] 详情异常 %s" % str(e).split("\n")[0][:120])
        return False


# ---------- 主流程 ----------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True, help="data/items/{id}.json 的 item_id")
    ap.add_argument("--cid", required=True, help="目标类目 ID，如 200670001")
    ap.add_argument("--submit", action="store_true", help="填完后真点提交")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--brand", default="无品牌/无注册商标")
    ap.add_argument("--model", default=None, help="型号，默认取 Item 参数里的「型号」")
    ap.add_argument("--model2", default=None, help="第二个型号（部分类目有）")
    ap.add_argument("--axle", default=None, help="适配轴体类型，默认取 Item 参数")
    ap.add_argument("--skip-sku", action="store_true")
    args = ap.parse_args()

    item = load_item(args.item)
    if item is None:
        log("[XX] 找不到商品 %s" % args.item)
        return 1

    log("=" * 68)
    log("商品 %s  %s" % (item.item_id, item.title[:46]))
    log("目标类目 %s   价格 %s~%s   SKU %d  主图 %d"
        % (args.cid, item.price.min, item.price.max, len(item.skus), len(item.main_images)))
    log("=" * 68)

    params = {p.name: p.value for p in item.params}
    model = args.model or params.get("型号") or "通用"
    model2 = args.model2 or ("61键" if any("键" in v for v in params.values()) else "通用")
    axle = args.axle or params.get("适配轴体类型") or "通用"
    log("品牌=%s 型号1=%s 型号2=%s 轴体=%s" % (args.brand, model, model2, axle))

    # 价格：取最低 SKU 价
    price = None
    try:
        price = float(item.price.min or min(s.price for s in item.skus if s.price))
    except (TypeError, ValueError):
        price = 39.9
    total_qty = item.stock_total or sum((s.quantity or 0) for s in item.skus) or 100
    log("一口价=%s 总库存=%s" % (price, total_qty))

    os_url = publish_url_for(args.cid)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with BrowserSession(cdp_url=args.cdp) as s:
        page = s.page()
        try:
            page.set_viewport_size({"width": 1440, "height": 1000})
        except Exception:
            pass

        log("\n[1] 打开发布页")
        if not s.goto(os_url, wait_until="domcontentloaded", settle_ms=2500):
            log("    [XX] 打不开")
            return 1
        page.wait_for_timeout(3500)
        cur = page.url
        log("    URL %s" % cur)
        if "login" in cur:
            log("    [XX] 跳登录了，先扫码")
            return 1
        body_head = page.evaluate("() => (document.body.innerText || '').slice(0, 200)")
        if "类目不存在" in body_head or "类目为空" in body_head:
            log("    [XX] 这个类目对该店铺无效: %s" % body_head[:80])
            return 1

        n = mark_inputs(page)
        inputs = list_inputs(page)
        log("    可见字段 %d 个" % n)
        for it in inputs:
            log("      idx=%-2d name=%-14r ph=%-34r val=%r"
                % (it["idx"], it["name"], it["ph"][:34], it["value"][:24]))

        # ---- 基础信息 ----
        log("\n[2] 品牌")
        pick_brand(page, args.brand)

        n = mark_inputs(page)
        inputs = list_inputs(page)
        # 型号框的 name 是 p-xxx（形如 p-20000~1 / p-656059583）；
        # 品牌框的 placeholder 也是「请输入」但没有 name，按 placeholder 找会把品牌框当型号框，
        # 结果把品牌覆盖成型号值，提交时报「必填项型号不能为空」。
        p_inputs = [it for it in inputs if (it["name"] or "").startswith("p-")]
        log("\n[3] 型号（带 name 的 p-* 字段 %d 个: %s）"
            % (len(p_inputs), [it["name"] for it in p_inputs]))
        if p_inputs:
            type_into(page, p_inputs[0]["idx"], model, "型号1(%s)" % p_inputs[0]["name"])
        if len(p_inputs) > 1:
            type_into(page, p_inputs[1]["idx"], model2, "型号2(%s)" % p_inputs[1]["name"])

        # 填完型号后回头确认品牌还在（型号曾把品牌覆盖掉）
        brand_now = page.evaluate(
            "() => {const el = document.querySelector('input[placeholder=\"请输入\"]');"
            " return el ? el.value : '';}"
        )
        if not (brand_now or "").strip():
            log("    [!!] 品牌被清空了，重填一次")
            pick_brand(page, args.brand)
        else:
            log("    品牌复核: %r" % brand_now)

        # ---- 标题 ----
        log("\n[4] 标题")
        n = mark_inputs(page)
        inputs = list_inputs(page)
        t = find_input(inputs, ph_contains="30个汉字")
        if t:
            title = item.title[:30]
            type_into(page, t["idx"], title, "宝贝标题")

        # ---- 适配轴体类型 ----
        log("\n[5] 适配轴体类型")
        pick_select_by_label(page, "适配轴体类型", axle)

        # ---- 一口价 / 总库存 ----
        log("\n[6] 一口价 / 总库存")
        fill_by_unit(page, "元", str(price), "一口价")
        page.wait_for_timeout(400)
        fill_by_unit(page, "件", str(total_qty), "总库存")

        # ---- 主图 ----
        log("\n[7] 主图")
        media_dir = ROOT / "data" / "media" / item.item_id
        files = sorted(media_dir.glob("main_*.jpg")) if media_dir.exists() else []
        if not files:
            files = sorted(media_dir.glob("main_*.png")) if media_dir.exists() else []
        log("    本地主图 %d 张" % len(files))
        if files:
            upload_main_images(page, files, want=5)
        else:
            log("    [..] 没有本地图，跳过（需要先跑采集下载图片）")

        s.screenshot(str(OUT_DIR / ("p1_%s_after_base.png" % item.item_id)))

        # ---- 汇总当前状态 ----
        log("\n[8] 当前填写状态")
        page.wait_for_timeout(800)
        n = mark_inputs(page)
        inputs = list_inputs(page)
        for it in inputs:
            if it["value"]:
                log("    idx=%-2d %-34r = %r" % (it["idx"], it["ph"][:34], it["value"][:30]))
        errs = page.evaluate(
            r"""() => {
              const out = [];
              document.querySelectorAll('*').forEach(el => {
                const t = (el.innerText || '').trim();
                if (/^错误\s*\(\d+\)/.test(t) && el.children.length < 4) out.push(t.slice(0, 40));
              });
              return out;
            }"""
        )
        log("    错误区: %s" % errs)

        if args.submit:
            log("\n[9] 提交")
            btn = page.get_by_text("提交宝贝信息", exact=True)
            if btn.count() == 0:
                log("    [XX] 找不到提交按钮")
                return 1
            btn.first.scroll_into_view_if_needed(timeout=4000)
            btn.first.click(timeout=8000)
            log("    已点击，等跳转…")
            for _ in range(20):
                page.wait_for_timeout(1000)
                if "success" in page.url:
                    break
            log("    落地 URL: %s" % page.url)
            if "success" in page.url:
                log("    [OK] 发布成功")
            else:
                log("    [!!] 没跳成功页，看截图")
            s.screenshot(str(OUT_DIR / ("p1_%s_submit.png" % item.item_id)))
        else:
            log("\n[9] 未提交（加 --submit 才真提交）")
            s.screenshot(str(OUT_DIR / ("p1_%s_ready.png" % item.item_id)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
