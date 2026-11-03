"""完整走通「搜索发品 -> 选中类目 -> 确认下一步 -> 发布页」。

之前的坑：
  - 搜索结果只是「显示」出来，必须点一下那一行才真正选中，
    否则「确认，下一步」一直是 disabled。
  - 按钮 disabled 时 Playwright 会一直等，超时 10 秒后报错，看起来像点不到。

本脚本用 JS 点搜索结果行，再等按钮 enabled 后点确认。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    CATEGORY_ENTRY,
    BrowserSession,
    dump_form_fields,
    dump_page_summary,
    group_fields,
    human_pause,
    write_report,
)

OUT_DIR = ROOT / "data" / "publish_poc"

# 真正的类目项是 li.category-item（不是面包屑 path-text）。
# 实测：点面包屑没有任何效果，点 li.category-item 之后「确认，下一步」才 enabled。
_FIND_RESULT_JS = r"""
(kw) => {
  const items = [];
  document.querySelectorAll('li.category-item').forEach(el => {
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t.includes(kw)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 20 || r.height < 8) return;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return;
    items.push({
      text: t,
      cls: (el.className || '').toString(),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });
  if (!items.length) return { ok: false, reason: 'no li.category-item' };
  // 优先文本完全等于关键词的，否则取第一个
  const pick = items.find(i => i.text === kw) || items[0];
  return { ok: true, pick, total: items.length };
}
"""

_BODY_JS = r"""
() => ({
  url: location.href,
  title: document.title,
  body_text: (document.body.innerText || '').slice(0, 8000),
  iframes: [...document.querySelectorAll('iframe')].map(f => (f.src || '').slice(0, 250)),
})
"""


def _click_button(page, name: str, timeout_ms: int = 8000) -> bool:
    try:
        btn = page.get_by_role("button", name=name)
        if btn.count():
            btn.first.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def _fill_model(page, model: str | None) -> dict:
    """品牌填完后冒出的必填「型号」，对应 input[name="p-20000~1"]。

    这是普通文本框（不是下拉），用真实键盘输入即可。
    """
    want = model or "通用"
    try:
        el = page.query_selector('input[name^="p-"]')
        if el is None or not el.is_visible():
            return {"mode": "not_found"}
        el.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(want, delay=90)
        page.wait_for_timeout(600)
        return {"mode": "typed", "value": el.input_value(), "name": el.get_attribute("name")}
    except Exception as exc:
        return {"mode": "error", "error": str(exc)}


def _wait_enabled(page, name: str, timeout_s: float = 8.0) -> bool:
    """等按钮从 disabled 变可用。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            el = page.get_by_role("button", name=name).first
            if el.is_enabled():
                return True
        except Exception:
            pass
        page.wait_for_timeout(300)
    return False


# 点「无品牌/无注册商标」这类免品牌选项
_CLICK_NO_BRAND_JS = r"""
() => {
  const cands = [];
  document.querySelectorAll('a,span,div,button,label').forEach(el => {
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t.includes('无品牌')) return;
    if (t.length > 40) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return;
    cands.push({ el, t, area: r.width * r.height });
  });
  if (!cands.length) return { ok: false, reason: 'no 无品牌 element' };
  cands.sort((a, b) => a.area - b.area);
  const pick = cands[0];
  pick.el.click();
  return { ok: true, text: pick.t };
}
"""


def _fill_brand(page, brand: str | None) -> dict:
    """类目选完后必须给品牌，否则「确认，下一步」一直 disabled。

    实测结构（键帽类目）：
      input[placeholder="请输入"]            <- 品牌主框（选中的值显示在这）
      input[placeholder=""][rect.y≈443]      <- 下拉里的搜索框（真正的过滤入口）
      input[name="p-20000~1"]                <- 型号属性（pid=20000）
    主框里敲字不会过滤列表，必须在**下拉搜索框**里敲。
    """
    want = brand or "无品牌"

    # 1) 点开品牌下拉
    try:
        main = None
        for inp in page.query_selector_all('input[placeholder="请输入"]'):
            if inp.is_visible():
                main = inp
                break
        if main is None:
            return {"mode": "no_main_input"}
        main.click()
        page.wait_for_timeout(1200)
    except Exception as exc:
        return {"mode": "open_failed", "error": str(exc)}

    # 2) 在下拉搜索框里敲关键词
    typed_in = None
    try:
        for inp in page.query_selector_all("input"):
            if not inp.is_visible():
                continue
            box = inp.bounding_box() or {}
            ph = inp.get_attribute("placeholder") or ""
            # 下拉搜索框：无 placeholder、位于品牌主框下方
            if ph == "" and box.get("y", 0) > 420:
                inp.click()
                page.keyboard.type(want, delay=110)
                typed_in = "dropdown_search"
                break
    except Exception:
        pass

    if typed_in is None:
        # 退一步：直接在主框敲
        try:
            main.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(want, delay=110)
            typed_in = "main_input"
        except Exception as exc:
            return {"mode": "type_failed", "error": str(exc)}

    page.wait_for_timeout(2000)

    # 3) 点选项 —— 用 Playwright 原生定位器（JS .click() 点外层容器不触发选中）
    picked = None
    for label in ("无品牌/无注册商标", "无品牌", want):
        try:
            loc = page.get_by_text(label, exact=True)
            n = loc.count()
            if not n:
                continue
            # 取最后一个（下拉在 DOM 末尾，主框里的文本在前）
            loc.nth(n - 1).click(timeout=5000)
            picked = label
            break
        except Exception:
            continue

    if picked is None:
        # 退化：JS 找最内层可点元素
        picked = page.evaluate(
            r"""
            (want) => {
              const items = [];
              document.querySelectorAll('li,.next-menu-item,[role="option"],[class*="option-item"]').forEach(el => {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 40) return;
                const r = el.getBoundingClientRect();
                if (r.width < 30 || r.height < 8) return;
                items.push({ el, t });
              });
              if (!items.length) return null;
              const pick = items.find(i => i.t === want) || items.find(i => i.t.includes(want)) || items[0];
              pick.el.click();
              return pick.t;
            }
            """,
            want,
        )

    page.wait_for_timeout(1800)

    # 4) 读回品牌主框的值，确认真的选上了
    value = None
    try:
        for inp in page.query_selector_all('input[placeholder="请输入"]'):
            if inp.is_visible():
                value = inp.input_value()
                break
    except Exception:
        pass

    return {"mode": "dropdown", "typed_in": typed_in, "picked": picked, "value": value}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True, help="搜索关键词（也用于无 --path 时的类目名）")
    ap.add_argument("--path", default=None, help="类目路径，用 > 分隔，例如 '电脑硬件/显示器/电脑周边>键帽'")
    ap.add_argument("--viewport-width", type=int, default=1440, help="浏览器视口宽度，太窄会让按钮被裁切")
    ap.add_argument("--brand", default=None, help="品牌名；留空则选「无品牌/无注册商标」")
    ap.add_argument("--model", default=None, help="型号；默认「通用」")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    args = ap.parse_args()

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.keyword)

    with BrowserSession(cdp_url=args.cdp) as s:
        if not s.login_state_detail()["logged_in"]:
            print("[XX] 未登录")
            return 1

        print("[0] 固定视口（窗口太窄时「确认，下一步」会被裁到视口外，点不到）")
        try:
            page0 = s.page()
            page0.set_viewport_size({"width": args.viewport_width, "height": 1000})
            page0.wait_for_timeout(800)
            print("    视口: %s" % page0.evaluate("() => [window.innerWidth, window.innerHeight]"))
        except Exception as exc:
            print("    [..] 设置失败（继续）: %s" % exc)

        print("[1] 打开类目引导页")
        s.goto(CATEGORY_ENTRY, settle_ms=2500)
        page = s.page()
        page.wait_for_timeout(1200)

        print("[2] 确保在搜索发品界面")
        if not _click_button(page, "开启", 5000):
            probe = page.query_selector('input[placeholder*="可输入产品名称"]')
            if probe and probe.is_visible():
                print("    [i] 已在搜索界面")
            else:
                print("    [..] 刷新重试")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                if not _click_button(page, "开启", 5000):
                    print("    [XX] 失败")
                    return 1
        page.wait_for_timeout(2000)

        print("[3] 输入关键词: " + args.keyword)
        inp = page.query_selector('input[placeholder*="可输入产品名称"]')
        if not inp:
            print("    [XX] 没有搜索框")
            return 1
        inp.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(args.keyword, delay=110)
        page.wait_for_timeout(600)

        print("[4] 点「搜索」")
        if not _click_button(page, "搜索"):
            print("    [XX] 搜索按钮点不到")
            return 1
        page.wait_for_timeout(5000)
        human_pause(0.8, 1.5)

        print("[5] 逐级点类目 li.category-item")
        # 实测：类目树是「手风琴展开式」，一级和二级在同一列。
        # 搜索不一定过滤树，最稳的是按路径逐级点。
        levels = [x.strip() for x in (args.path or args.keyword).split(">") if x.strip()]
        print("    路径: " + " > ".join(levels))

        # 实测：这个页面会被横向滚动（scrollLeft≈97），导致类目树 rect 变成负数、
        # 底部「确认，下一步」被推出视口。先把所有容器的 scrollLeft 归零。
        fixed = page.evaluate(
            r"""
            () => {
              let n = 0;
              document.querySelectorAll('*').forEach(el => {
                if (el.scrollLeft > 0) { el.scrollLeft = 0; n++; }
              });
              if (window.scrollX > 0) { window.scrollTo(0, window.scrollY); n++; }
              return n;
            }
            """
        )
        print("    横向归零: %d 个容器" % fixed)
        page.wait_for_timeout(500)

        # 搜索后页面只回显面包屑，类目树是收起的；先点「选择类目」把它叫出来
        total_items = page.locator("li.category-item").count()
        if total_items == 0:
            print("    [..] 树未展开（0 个 category-item），点「选择类目」")
            _click_button(page, "选择类目", 8000)
            page.wait_for_timeout(3000)
            human_pause(0.6, 1.2)
            total_items = page.locator("li.category-item").count()
        print("    树里共 %d 个 category-item" % total_items)

        picked_all = []
        for lv in levels:
            loc = page.locator("li.category-item").filter(has_text=lv)
            n = loc.count()
            print("    [%s] 匹配 %d 个" % (lv, n))
            if n == 0:
                print("    [XX] 找不到「%s」，当前树里没有这一级" % lv)
                body = page.evaluate(_BODY_JS)
                print(body["body_text"][:1200])
                return 1

            # 精确匹配优先
            target = None
            for i in range(n):
                it = loc.nth(i)
                if (it.inner_text() or "").strip() == lv:
                    target = it
                    break
            if target is None:
                target = loc.first
            try:
                target.scroll_into_view_if_needed(timeout=5000)
                page.wait_for_timeout(300)
                target.click(timeout=8000)
                picked_all.append(lv)
                print("      [OK] 点了「%s」" % lv)
            except Exception as exc:
                print("      [XX] 点击失败: %s" % exc)
                return 1
            page.wait_for_timeout(2000)
            human_pause(0.4, 0.9)

            # 验证真的进了 selected 状态（hover 高亮也会让文本命中，但按钮不会 enabled）
            try:
                sel = page.locator("li.category-item.selected")
                sel_texts = [(sel.nth(i).inner_text() or "").strip() for i in range(sel.count())]
                print("      selected: %s" % sel_texts)
            except Exception:
                pass

        page.wait_for_timeout(1200)

        # 关键：类目选完后，第三列要求必须填品牌，否则「确认，下一步」永远 disabled
        print("[5.5] 处理品牌（不填则按钮永远 disabled）")
        brand_done = _fill_brand(page, args.brand)
        print("    品牌处理: " + str(brand_done))
        page.wait_for_timeout(1500)

        # 品牌填完会再冒出必填的「型号」（input[name="p-20000~1"]）
        print("[5.6] 填型号")
        model_done = _fill_model(page, args.model)
        print("    型号处理: " + str(model_done))
        page.wait_for_timeout(1200)

        print("[6] 等「确认，下一步」可用")
        # 实测：类目+品牌+型号都填好后，按钮仍然是 disabled。
        # 必须再点一次「选择类目」把树收起来，React 才把选中态提交到表单状态。
        # 这一步不加，按钮永远是灰的。
        enabled = _wait_enabled(page, "确认，下一步", 3.0)
        if not enabled:
            print("    [..] 仍 disabled，点「选择类目」收起树")
            _click_button(page, "选择类目", 8000)
            page.wait_for_timeout(2500)
            human_pause(0.6, 1.0)
            enabled = _wait_enabled(page, "确认，下一步", 8.0)
        print("    enabled=%s" % enabled)
        if not enabled:
            print("    [XX] 还是 disabled，dump 必填项状态")
            diag = page.evaluate(
                r"""
                () => {
                  const btns = [];
                  document.querySelectorAll('button').forEach(b => {
                    const r = b.getBoundingClientRect();
                    btns.push({ text: (b.innerText||'').trim(), disabled: b.disabled,
                                cls: (b.className||'').toString().slice(0,80),
                                rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
                  });
                  // 所有带 * 的必填标签
                  const req = [];
                  document.querySelectorAll('*').forEach(el => {
                    const t = (el.innerText || '').trim();
                    if (!/^\*/.test(t) || t.length > 20) return;
                    if (el.children.length > 2) return;
                    const r = el.getBoundingClientRect();
                    if (r.width < 10) return;
                    req.push({ text: t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
                  });
                  // 所有可见 input 的当前值
                  const inputs = [];
                  document.querySelectorAll('input,textarea').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width < 10) return;
                    inputs.push({ name: el.getAttribute('name'), ph: el.getAttribute('placeholder'),
                                  value: (el.value||'').slice(0,40),
                                  rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
                  });
                  return { btns, req, inputs, scrollX: window.scrollX, scrollY: window.scrollY };
                }
                """
            )
            print("    scrollX=%s scrollY=%s" % (diag["scrollX"], diag["scrollY"]))
            print("    -- 按钮 --")
            for b in diag["btns"]:
                print("      " + str(b))
            print("    -- 必填标签 --")
            for r in diag["req"]:
                print("      " + str(r))
            print("    -- 输入框 --")
            for i in diag["inputs"]:
                print("      " + str(i))
            s.screenshot(str(OUT_DIR / ("stuck_" + safe + ".png")))
            return 1

        print("[7] 点「确认，下一步」")
        if not _click_button(page, "确认，下一步", 10000):
            print("    [XX] 点不到")
            return 1
        print("    [OK] 已点击，等发布页")
        page.wait_for_timeout(9000)
        human_pause(2.0, 3.0)
        page.wait_for_timeout(3000)

        body = page.evaluate(_BODY_JS)
        print("\n" + "=" * 68)
        print("发布页")
        print("=" * 68)
        print("URL: " + body["url"])
        print("标题: " + str(body["title"]))
        print("\n-- 页面文字 (前 3000) --")
        print(body["body_text"][:3000])
        if body["iframes"]:
            print("\n-- iframe --")
            for f in body["iframes"]:
                print("  " + f)

        fields = dump_form_fields(page)
        groups = group_fields(fields)
        visible = [f for f in fields if f.get("visible")]
        print("\n" + "=" * 68)
        print("可填字段 (%d 个，可见 %d)" % (len(fields), len(visible)))
        print("=" * 68)
        for f in visible:
            print("  %-10s %-8s id=%-24r ph=%-32r label=%r" % (
                f.get("kind"), f.get("tag"), f.get("id"), f.get("placeholder"), f.get("label")))

        print("\n" + "=" * 68)
        print("按用途分组")
        print("=" * 68)
        for k, v in groups.items():
            print("  %-10s %d 个" % (k, len(v)))
            for f in v[:4]:
                print("       id=%r ph=%r label=%r" % (f.get("id"), f.get("placeholder"), f.get("label")))

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        shot = OUT_DIR / ("publish_" + safe + ".png")
        s.screenshot(str(shot))
        print("\n截图: " + str(shot))

        write_report(
            {
                "url": body["url"],
                "keyword": args.keyword,
                "clicked_result": picked_all,
                "summary": dump_page_summary(page),
                "body": body,
                "fields": fields,
                "groups": groups,
            },
            OUT_DIR / ("publish_" + safe + ".json"),
        )
        print("报告: " + str(OUT_DIR / ("publish_" + safe + ".json")))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
