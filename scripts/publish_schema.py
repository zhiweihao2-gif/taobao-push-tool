# -*- coding: utf-8 -*-
"""按类目 schema 填必填项 —— 不依赖任何硬编码字段名。

publish_one.py 是照键帽类目（200670001）写的：品牌文本框 + 型号文本框 + 轴体下拉。
键盘类目（110210）完全不一样：4 个必填全是下拉，没有 p-* 的 name 字段。
所以这里改成：

    读 data/category_schema/<cid>.json 的必填字段清单
      -> 每个字段拿「源商品参数」里同名的值
      -> 找不到就用常识兜底（适用场景=通用 / 是否机械键盘=机械键盘 ...）
      -> 全部用 pick_select_by_label 点选

用法:
    python scripts/publish_schema.py --item 745703904965 --cid 110210
    python scripts/publish_schema.py --item 745703904965 --cid 110210 --submit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from src.core.storage import load_item  # noqa: E402
from src.publish.browser_pub import (  # noqa: E402
    BrowserSession, human_pause, is_success_page, publish_url_for,
)

SCHEMA_DIR = os.path.join(ROOT, "data", "category_schema")
OUT_DIR = os.path.join(ROOT, "data", "publish_poc")

# 源商品没这个参数、但目标类目必填时，按常识给一个可选的默认值。
# 值必须落在该字段的 options 里，否则会报「选项不在候选里」。
GUESS = {
    "适用场景": ["通用", "办公", "游戏"],
    "是否机械键盘": ["机械键盘", "非机械键盘"],
    "是否支持人体工程学": ["是", "否"],
    "品牌": ["无品牌", "无品牌/无注册商标"],
    "宝贝类型": ["全新"],
    "成色": ["全新"],
    "款式": ["全新"],
    "是否透光": ["是", "否"],
    "质保期限": ["12个月", "1年", "无"],
}


def log(s: str = "") -> None:
    print(s, flush=True)


def load_schema(cid: str) -> dict:
    p = os.path.join(SCHEMA_DIR, "%s.json" % cid)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def pick_select_by_label(page, label: str, want: str, options: list[str] | None = None) -> tuple[bool, str]:
    """按 label 文本找到 .next-select，点 x+8 展开，匹配 options-item[title]。"""
    LOCATE = """
    (label) => {
        const all = Array.from(document.querySelectorAll('*'));
        const leaf = all.find(e => e.children.length === 0 &&
            (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
        if (!leaf) return {ok: false, why: 'label not found'};
        let n = leaf;
        for (let i = 0; i < 9 && n; i++) {
            const inp = n.querySelector && n.querySelector('input[role="combobox"]');
            if (inp) {
                const sel = inp.closest('.next-select');
                if (sel) {
                    sel.scrollIntoView({block: 'center'});
                    sel.setAttribute('data-dsh-sel', '1');
                    const r = sel.getBoundingClientRect();
                    return {ok: true, rect: [r.x, r.y, r.width, r.height]};
                }
            }
            n = n.parentElement;
        }
        return {ok: false, why: 'no next-select ancestor'};
    }
    """
    MARK = """
    () => {
        const ov = Array.from(document.querySelectorAll('.next-overlay-wrapper'))
            .find(o => o.classList.contains('opened'));
        if (!ov) return [];
        const items = Array.from(ov.querySelectorAll('[class*="options-item"]'));
        const out = [];
        items.forEach((it, i) => {
            it.setAttribute('data-dsh-opt', String(i));
            out.push(it.getAttribute('title') || (it.innerText || '').split('\\n')[0].trim());
        });
        return out;
    }
    """
    try:
        page.evaluate("() => document.querySelectorAll('[data-dsh-sel],[data-dsh-opt]').forEach(e => {e.removeAttribute('data-dsh-sel'); e.removeAttribute('data-dsh-opt');})")
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        info = page.evaluate(LOCATE, label)
        if not info.get("ok"):
            return False, info.get("why", "?")
        x, y, w, h = info["rect"]
        # 中心会落在已选 tag 上不展开；右边缘是箭头空区；只有左边缘能展开
        page.mouse.click(x + 8, y + h / 2)
        page.wait_for_timeout(2000)

        opts = page.evaluate(MARK) or []
        if not opts:
            return False, "无选项"

        cands = options or [want]
        hit = None
        for c in cands:
            for i, t in enumerate(opts):
                if t == c:
                    hit = i
                    break
            if hit is not None:
                break
        # 精确没有就退到「包含」
        if hit is None:
            for c in cands:
                for i, t in enumerate(opts):
                    if c and c in t:
                        hit = i
                        break
                if hit is not None:
                    break

        # 品牌这种长列表默认只显示十几个，直接找是找不到的。
        # 下拉顶部有个搜索框（placeholder 为 null、宽 > 40），在它里面敲字才会过滤。
        if hit is None:
            search = None
            try:
                search = page.evaluate("""() => {
                    const ov = Array.from(document.querySelectorAll('.next-overlay-wrapper'))
                        .find(o => o.classList.contains('opened'));
                    if (!ov) return null;
                    const inp = Array.from(ov.querySelectorAll('input'))
                        .find(i => i.getBoundingClientRect().width > 40);
                    if (!inp) return null;
                    inp.setAttribute('data-dsh-search', '1');
                    const r = inp.getBoundingClientRect();
                    return {rect: [r.x, r.y, r.width, r.height]};
                }""")
            except Exception:
                search = None

            if search and search.get("rect"):
                sx, sy, sw, sh = search["rect"]
                page.mouse.click(sx + sw / 2, sy + sh / 2)
                page.wait_for_timeout(300)
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                for ch in want:
                    page.keyboard.type(ch)
                    page.wait_for_timeout(60)
                page.wait_for_timeout(1200)
                opts2 = page.evaluate(MARK) or []
                for c in cands:
                    for i, t in enumerate(opts2):
                        if t == c or (c and c in t):
                            hit = i
                            opts = opts2
                            break
                    if hit is not None:
                        break
                if hit is None:
                    return False, "搜「%s」后仍无（有 %s）" % (want, (opts2 or [])[:8])

        if hit is None:
            return False, "候选里没有 %s（有 %s）" % (want, opts[:8])

        page.locator('[data-dsh-opt="%d"]' % hit).click(force=True)
        page.wait_for_timeout(700)

        # 回读校验：点完不算数，em 文本真的变了才算
        verify = page.evaluate('''
        (label) => {
            const all = Array.from(document.querySelectorAll('*'));
            const leaf = all.find(e => e.children.length === 0 &&
                (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
            if (!leaf) return null;
            let n = leaf;
            for (let i = 0; i < 9 && n; i++) {
                const inp = n.querySelector && n.querySelector('input[role="combobox"]');
                if (inp) {
                    const sel = inp.closest('.next-select');
                    if (!sel) return null;
                    const em = sel.querySelector('em');
                    return em ? (em.textContent || '').trim() : (inp.value || '').trim();
                }
                n = n.parentElement;
            }
            return null;
        }
        ''', label)
        if verify and (verify == opts[hit] or want in verify or verify in opts[hit]):
            return True, opts[hit]
        return False, "点了但未生效（回读=%r，期望=%r）" % (verify, opts[hit])
    except Exception as e:
        return False, str(e).split("\n")[0][:90]


def type_by_label(page, label: str, text: str) -> bool:
    """按 label 找 input，走 JS setter + 事件。"""
    JS = """
    ([label, val]) => {
        const all = Array.from(document.querySelectorAll('*'));
        const leaf = all.find(e => e.children.length === 0 &&
            (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
        if (!leaf) return 'label not found';
        let n = leaf;
        for (let i = 0; i < 9 && n; i++) {
            const inp = n.querySelector && n.querySelector('input:not([type=hidden])');
            if (inp && inp.type !== 'checkbox' && inp.type !== 'radio') {
                inp.scrollIntoView({block: 'center'});
                const set = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                set.call(inp, val);
                inp.dispatchEvent(new Event('input',  {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                inp.dispatchEvent(new Event('blur',   {bubbles: true}));
                return 'ok:' + inp.value;
            }
            n = n.parentElement;
        }
        return 'no input';
    }
    """
    try:
        r = page.evaluate(JS, [label, text])
        page.wait_for_timeout(300)
        return str(r).startswith("ok:")
    except Exception:
        return False


def fill_number_with_unit(page, label: str, number: str, unit: str) -> tuple[bool, str]:
    """填「数字 + 单位」组合控件（质保期限这类）。

    这类字段在 DOM 里是一个 sell-catProp-item 容器，里面并排两个控件：
        input[placeholder="数字"]   +   .next-select（单位）
    按 label 的 y 坐标找同一行的那两个，不要靠 DOM 祖先链（会串到别的字段上）。
    """
    LOC = """
    (label) => {
        const all = Array.from(document.querySelectorAll('*'));
        const leaf = all.find(e => e.children.length === 0 &&
            (e.textContent || '').trim().replace(/[*＊\\s]/g, '') === label);
        if (!leaf) return {ok: false, why: 'label not found'};
        const lr = leaf.getBoundingClientRect();
        const rowY = lr.y + lr.height / 2;

        // 数字框：placeholder=数字，且垂直位置和 label 接近
        const nums = Array.from(document.querySelectorAll('input[placeholder="数字"]'))
            .filter(i => { const r = i.getBoundingClientRect();
                           return Math.abs(r.y + r.height / 2 - rowY) < 60; });
        // 单位下拉：x 在数字框右边，同一行
        const sels = Array.from(document.querySelectorAll('.next-select'))
            .filter(s => { const r = s.getBoundingClientRect();
                           return Math.abs(r.y + r.height / 2 - rowY) < 60
                                  && r.x > lr.x; });

        if (!nums.length) return {ok: false, why: '没有数字框'};
        const num = nums[0];
        // 一定要先滚到视口中间，否则 getBoundingClientRect 拿到的是
        // 滚动前的坐标，鼠标点会落空（实测单位下拉因此一直打不开）
        num.scrollIntoView({block: 'center'});
        num.setAttribute('data-dsh-num', '1');
        const nr = num.getBoundingClientRect();

        // 单位下拉必须是「紧挨数字框右边」的那个。只按 r.x > lr.x 会选到
        // 同一行别的字段（实测选中了「是否支持人体工程学」，选项只有 否/是）。
        const rightEdge = nr.x + nr.width - 4;
        const after = sels.filter(s => s.getBoundingClientRect().x >= rightEdge)
                          .sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
        let sel = after.length ? after[0] : null;
        // 再兜一层：宽度要接近数字框（单位下拉一般 80~140 宽）
        if (sel && sel.getBoundingClientRect().width > 200) sel = after[1] || null;
        if (sel) sel.setAttribute('data-dsh-unit', '1');
        return {ok: true,
                numRect: [nr.x, nr.y, nr.width, nr.height],
                hasUnit: !!sel,
                unitRect: sel ? (() => { const r = sel.getBoundingClientRect();
                                        return [r.x, r.y, r.width, r.height]; })() : null};
    }
    """
    try:
        page.evaluate("() => document.querySelectorAll('[data-dsh-num],[data-dsh-unit]').forEach(e => {e.removeAttribute('data-dsh-num'); e.removeAttribute('data-dsh-unit');})")
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        info = page.evaluate(LOC, label)
        if not info.get("ok"):
            return False, info.get("why", "?")

        nx, ny, nw, nh = info["numRect"]
        page.mouse.click(nx + nw / 2, ny + nh / 2)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        for ch in number:
            page.keyboard.type(ch)
            page.wait_for_timeout(70)
        page.wait_for_timeout(400)
        got = page.locator('[data-dsh-num="1"]').input_value()

        # 数字框常常吞掉前几个字符，回读不对就走 JS setter
        if got.strip() != number.strip():
            page.evaluate("""([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return;
                const set = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                set.call(el, val);
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur',   {bubbles: true}));
            }""", ['[data-dsh-num="1"]', number])
            page.wait_for_timeout(400)
            got = page.locator('[data-dsh-num="1"]').input_value()

        # 关键：数字填完必须 Tab 失焦。数字框没提交前，单位下拉带
        # next-inactive 类（disabled 态），任何点击都打不开（实测 6 种方式全失败）。
        page.keyboard.press("Tab")
        page.wait_for_timeout(900)

        inactive = page.evaluate("""() => {
            const s = document.querySelector('[data-dsh-unit="1"]');
            return s ? (s.className || '').toString().indexOf('next-inactive') >= 0 : null;
        }""")
        if inactive:
            # 还没活过来，再点一下数字框再 Tab 一次
            nx2 = page.locator('[data-dsh-num="1"]')
            nx2.click(force=True)
            page.keyboard.press("Tab")
            page.wait_for_timeout(900)
            inactive = page.evaluate("""() => {
                const s = document.querySelector('[data-dsh-unit="1"]');
                return s ? (s.className || '').toString().indexOf('next-inactive') >= 0 : null;
            }""")

        msg = "%s" % got
        if info.get("hasUnit") and unit:
            ux, uy, uw, uh = info["unitRect"]
            opts = []
            # 单位下拉是 single 版，和 multiple 版的可点区域不同：
            # 先试左边缘（已选 tag 情况），没展开就试中心，再试右边缘。
            for dx in (8, uw / 2, uw - 8):
                page.mouse.click(ux + dx, uy + uh / 2)
                page.wait_for_timeout(1600)
                opts = page.evaluate("""() => {
                    const ov = Array.from(document.querySelectorAll('.next-overlay-wrapper'))
                        .find(o => o.classList.contains('opened'));
                    if (!ov) return [];
                    const out = [];
                    ov.querySelectorAll('[class*="options-item"]').forEach((it, i) => {
                        it.setAttribute('data-dsh-opt', String(i));
                        out.push(it.getAttribute('title') || (it.innerText || '').trim());
                    });
                    return out;
                }""") or []
                if opts:
                    break
            hit = None
            # 实测这个类目的单位选项就叫「月」（不是「个月」），所以要放在最前面试
            for want in ([unit] + ["月", "个月", "年", "天", "日"]):
                if hit is not None:
                    break
                for i, t in enumerate(opts):
                    if t == want or (want and want in t):
                        hit = i
                        break
            if hit is not None:
                page.locator('[data-dsh-opt="%d"]' % hit).click(force=True)
                page.wait_for_timeout(500)
                msg += " " + str(opts[hit])
            else:
                msg += " (单位选不上，有 %s)" % opts[:6]
        return True, msg
    except Exception as e:
        return False, str(e).split("\n")[0][:90]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--cid", required=True)
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    a = ap.parse_args()

    item = load_item(a.item)
    if not item:
        log("[XX] 找不到商品 %s" % a.item)
        return 1

    schema = load_schema(a.cid)
    if not schema.get("valid"):
        log("[XX] 类目 %s 的 schema 无效或不存在（先跑 probe_cat.py）" % a.cid)
        return 1

    fields = schema.get("fields") or []
    required = [f for f in fields if f.get("required")]
    log("=" * 66)
    log("商品 %s  %s" % (a.item, item.title[:44]))
    log("类目 %s  %s" % (a.cid, " > ".join(schema.get("cat_path") or [])))
    log("必填 %d 个: %s" % (len(required), [f.get("label") for f in required]))
    log("=" * 66)

    # 源商品参数（同名匹配用）
    params = {p.name: str(p.value) for p in item.params}
    log("\n源商品参数 %d 条: %s" % (len(params), list(params)[:10]))

    os.makedirs(OUT_DIR, exist_ok=True)
    ok_n = 0
    todo = []

    with BrowserSession(cdp_url=a.cdp) as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})

        log("\n[1] 打开发布页")
        if not s.goto(publish_url_for(a.cid), wait_until="domcontentloaded", settle_ms=2800):
            log("    [XX] 打不开")
            return 1
        cur = page.url or ""
        if "upload.taobao.com" not in cur:
            log("    [XX] 没跳到发布页: %s" % cur[:100])
            return 1
        log("    URL %s" % cur[:110])

        log("\n[2] 标题")
        title = (item.title or "")[:60]
        if type_by_label(page, "宝贝标题", title):
            log("    [OK] %s" % title[:44])
        else:
            log("    [!!] 标题写入失败")

        log("\n[3] 必填属性（按 schema 逐个填）")
        for f in required:
            label = (f.get("label") or "").strip()
            kind = f.get("kind") or "input"
            if not label or label in ("基础信息", "物流服务", "启用"):
                continue

            val = params.get(label)
            cands = ([val] if val else []) + GUESS.get(label, [])
            if not cands:
                todo.append("%s（源商品没有，也没有默认值）" % label)
                log("    [--] %-14s 无可填值，跳过" % label)
                continue

            if kind == "select":
                ok, msg = pick_select_by_label(page, label, cands[0], cands)
            else:
                ok = type_by_label(page, label, cands[0])
                msg = cands[0]
            if ok:
                ok_n += 1
                log("    [OK] %-14s -> %s" % (label, str(msg)[:40]))
            else:
                todo.append("%s（%s）" % (label, str(msg)[:60]))
                log("    [XX] %-14s %s" % (label, str(msg)[:60]))

        log("\n[3.5] 补充字段（probe 探不到的动态必填，提交后靠报错反推）")
        # 型号：填了品牌之后才渲染出来的文本框，schema 里没有
        model = params.get("型号") or "通用"
        if type_by_label(page, "型号", model):
            log("    [OK] 型号 -> %s" % model)
        else:
            log("    [--] 型号 没找到（该类目可能不需要）")
        # 质保期限：schema 里没标 required，但提交时拦；是「数字 + 单位」组合控件
        ok, msg = fill_number_with_unit(page, "质保期限", "12", "月")
        log("    %s %-10s -> %s" % ("[OK]" if ok else "[--]", "质保期限", str(msg)[:40]))

        log("\n[4] 价格 / 库存")
        price = item.price.min or (min(s.price for s in item.skus if s.price) if item.skus else None)
        qty = item.stock_total or sum((s.quantity or 0) for s in item.skus) or 100
        ptxt = ("%d" % price) if price and float(price) == int(price) else ("%.2f" % (price or 0)).rstrip("0").rstrip(".")
        for unit, val, nm in (("元", ptxt, "一口价"), ("件", str(qty), "总库存")):
            r = page.evaluate("""([unit, val]) => {
                const all = Array.from(document.querySelectorAll('*'));
                const leaf = all.find(e => e.children.length === 0 &&
                    (e.textContent || '').trim() === unit);
                if (!leaf) return 'unit not found';
                let n = leaf.parentElement;
                for (let i = 0; i < 4 && n; i++) {
                    const inp = n.querySelector('input:not([type=checkbox]):not([type=radio])');
                    if (inp) {
                        inp.scrollIntoView({block: 'center'});
                        const set = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        set.call(inp, val);
                        inp.dispatchEvent(new Event('input',  {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        inp.dispatchEvent(new Event('blur',   {bubbles: true}));
                        return 'ok:' + inp.value;
                    }
                    n = n.parentElement;
                }
                return 'no input';
            }""", [unit, val])
            log("    %s %-6s -> %s" % ("[OK]" if str(r).startswith("ok:") else "[XX]", nm, r))

        log("\n[5] 主图")
        local = [i.local_path for i in item.main_images if i.local_path]
        log("    本地主图 %d 张" % len(local))
        if local:
            try:
                from publish_one import upload_main_images
                from pathlib import Path as _P
                n = upload_main_images(page, [_P(x) for x in local[:5]])
                log("    [OK] 回填 %s 张" % n)
            except Exception as e:
                log("    [XX] 上传失败: %s" % str(e).split("\n")[0][:100])
            # 图片空间是 iframe 弹窗，传完不关会盖住整页，
            # 后面的每次点击都会落在弹窗上（实测导致质保期限单位打不开）
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
            except Exception:
                pass

        try:
            page.screenshot(path=os.path.join(OUT_DIR, "schema_%s.png" % a.cid),
                            full_page=False, timeout=8000,
                            animations="disabled", caret="hide")
        except Exception as _e:
            log("    [!] 截图跳过: %s" % str(_e).split("\n")[0][:60])
        log("\n[6] 截图 data/publish_poc/schema_%s.png" % a.cid)

        log("\n" + "=" * 66)
        log("必填填上 %d/%d" % (ok_n, len(required)))
        if todo:
            log("还没搞定:")
            for t in todo:
                log("   - %s" % t)
        log("=" * 66)

        if a.submit:
            log("\n[7] 提交")
            try:
                page.get_by_text("提交宝贝信息", exact=True).first.click(force=True, timeout=8000)
            except Exception as e:
                log("    [XX] 点不到提交: %s" % str(e).split("\n")[0][:100])
            page.wait_for_timeout(6000)
            now = page.url or ""
            if is_success_page(now):
                log("    [OK] 发布成功 %s" % now[:120])
                try:
                    page.screenshot(path=os.path.join(OUT_DIR, "success_%s.png" % a.cid),
                                    full_page=False, timeout=8000)
                except Exception:
                    pass
            else:
                log("    [!!] 没跳成功页，当前 %s" % now[:110])
                try:
                    errs = page.evaluate(
                        "() => (document.body ? document.body.innerText : '')"
                        ".split('\\n').filter(l => l.includes('错误') || l.includes('必填') || l.includes('不能为空')).slice(0,10)")
                    for e in (errs or []):
                        log("      %s" % e[:90])
                except Exception:
                    pass
                page.screenshot(path=os.path.join(OUT_DIR, "fail_%s.png" % a.cid), full_page=False)
        else:
            log("\n[7] 未提交（加 --submit 才真提交）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
