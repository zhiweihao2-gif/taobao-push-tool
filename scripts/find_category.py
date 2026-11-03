"""走淘宝新版「搜索发品」流程，拿到真实可用类目。

背景：
  publish.htm?catId=X  -> X 无效时直接报「类目不存在或无效」，字段 0 个
  ai/category.htm      -> 引导页，必须先点「开启」才露出搜索框
  开启后：input[placeholder*="可输入产品名称"] + 「搜索」按钮 + 「选择类目」按钮

用法：
    python scripts/find_category.py --keyword 键帽
    python scripts/find_category.py --keyword 键帽 --pick 0     # 点第 1 个推荐结果
    python scripts/find_category.py --keyword 键帽 --pick 0 --next   # 再点「确认，下一步」
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    CATEGORY_ENTRY,
    BrowserSession,
    dump_page_summary,
    human_pause,
    write_report,
)

OUT_DIR = ROOT / "data" / "publish_poc"

# 抓「搜索结果」区域：平台推荐的类目卡片 + 任何 catId 线索
_RESULT_JS = r"""
() => {
  const out = {};

  // 所有含 catId 的链接 / data 属性
  const catHits = [];
  document.querySelectorAll('a[href*="catId"],a[href*="cat_id"],[data-catid],[data-category-id]').forEach(el => {
    const r = el.getBoundingClientRect();
    catHits.push({
      text: (el.innerText || '').trim().slice(0, 60),
      href: (el.getAttribute('href') || '').slice(0, 200),
      catid: el.getAttribute('data-catid') || el.getAttribute('data-category-id') || '',
      visible: r.width > 4,
    });
  });
  out.cat_hits = catHits.slice(0, 80);

  // 可见文本（找推荐类目名）
  const seen = new Set();
  const texts = [];
  document.querySelectorAll('li,div,span,a,button,p,label').forEach(el => {
    if (texts.length > 600) return;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return;
    if (el.children.length > 2) return;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 70) return;
    if (seen.has(t)) return;
    seen.add(t);
    texts.push({ text: t, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  out.texts = texts;

  out.body_text = (document.body.innerText || '').slice(0, 6000);
  out.url = location.href;
  return out;
}
"""


def _search(page, keyword: str) -> bool:
    for sel in [
        'input[placeholder*="可输入产品名称"]',
        'input[placeholder*="产品名称"]',
        'input[placeholder*="类目关键词"]',
        'input[type="text"]',
    ]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Delete")
                page.keyboard.type(keyword, delay=110)
                print("    输入到 " + sel)
                return True
        except Exception:
            continue
    return False


def _click_button(page, name: str) -> bool:
    try:
        btn = page.get_by_role("button", name=name)
        if btn.count():
            btn.first.click(timeout=8000)
            return True
    except Exception:
        pass
    try:
        page.get_by_text(name, exact=True).first.click(timeout=8000)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--pick", type=int, default=None, help="点第 N 个推荐结果（0 起）")
    ap.add_argument("--next", action="store_true", help="再点「确认，下一步」")
    args = ap.parse_args()

    with BrowserSession(cdp_url=args.cdp) as s:
        st = s.login_state_detail()
        if not st["logged_in"]:
            print("[XX] 未登录")
            return 1

        print("[1] 打开类目引导页")
        s.goto(CATEGORY_ENTRY, settle_ms=2500)
        page = s.page()
        page.wait_for_timeout(1200)

        print("[2] 点「开启」进搜索发品")
        if not _click_button(page, "开启"):
            # 页面可能停在搜索界面（上次已点开），此时没有「开启」按钮
            probe = page.query_selector('input[placeholder*="可输入产品名称"]')
            if probe and probe.is_visible():
                print("    [i] 已经在搜索发品界面，跳过")
            else:
                print("    [XX] 没点到「开启」，也没找到搜索框；先手动刷新页面")
                s.goto(CATEGORY_ENTRY, settle_ms=2500)
                page = s.page()
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                if not _click_button(page, "开启"):
                    print("    [XX] 刷新后仍点不到")
                    return 1
        page.wait_for_timeout(2500)
        human_pause(0.6, 1.2)

        print("[3] 输入关键词: " + args.keyword)
        if not _search(page, args.keyword):
            print("    [XX] 没找到搜索框")
            return 1
        page.wait_for_timeout(600)

        print("[4] 点「搜索」")
        if not _click_button(page, "搜索"):
            print("    [XX] 没点到搜索")
            return 1
        page.wait_for_timeout(5000)
        human_pause(1.0, 2.0)

        data = page.evaluate(_RESULT_JS)

        print("\n" + "=" * 68)
        print("搜索结果 — 带 catId 的元素 (%d)" % len(data["cat_hits"]))
        print("=" * 68)
        for h in data["cat_hits"]:
            mark = "V" if h["visible"] else " "
            print("  [%s] catid=%r text=%r" % (mark, h["catid"], h["text"]))
            if h["href"]:
                print("       href=" + h["href"])

        print("\n" + "=" * 68)
        print("页面可见文本 (%d)" % len(data["texts"]))
        print("=" * 68)
        for t in data["texts"][:150]:
            print("  %r rect=%s" % (t["text"], t["rect"]))

        print("\n" + "=" * 68)
        print("页面文字")
        print("=" * 68)
        print(data["body_text"][:3000])

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.keyword)
        s.screenshot(str(OUT_DIR / ("search_" + safe + ".png")))

        if args.pick is not None:
            print("\n[5] 点第 %d 个推荐结果" % args.pick)
            # 推荐结果通常是可点的类目卡片，用 cat_hits 里的可见项
            cands = [h for h in data["cat_hits"] if h["visible"] and h["text"]]
            if cands and args.pick < len(cands):
                target = cands[args.pick]
                try:
                    page.get_by_text(target["text"], exact=False).first.click(timeout=8000)
                    print("    [OK] 点了 %r" % target["text"])
                except Exception as exc:
                    print("    [XX] 点击失败: %s" % exc)
            else:
                # 退化：按文本索引找短文本块
                short = [t for t in data["texts"] if 2 <= len(t["text"]) <= 20]
                if args.pick < len(short):
                    try:
                        page.get_by_text(short[args.pick]["text"], exact=False).first.click(timeout=8000)
                        print("    [OK] 退化点击 %r" % short[args.pick]["text"])
                    except Exception as exc:
                        print("    [XX] 退化点击失败: %s" % exc)
            page.wait_for_timeout(3000)
            human_pause(0.8, 1.5)

            data2 = page.evaluate(_RESULT_JS)
            print("\n" + "=" * 68)
            print("选完类目后")
            print("=" * 68)
            print("URL: " + data2["url"])
            print(data2["body_text"][:2000])
            s.screenshot(str(OUT_DIR / ("picked_" + safe + ".png")))

            if args.next:
                print("\n[6] 点「确认，下一步」")
                if _click_button(page, "确认，下一步") or _click_button(page, "确认"):
                    print("    [OK] 已点击")
                else:
                    print("    [XX] 没点到")
                page.wait_for_timeout(6000)
                human_pause(1.5, 2.5)
                print("\n" + "=" * 68)
                print("发布页落点")
                print("=" * 68)
                print("URL: " + page.url)
                data3 = page.evaluate(_RESULT_JS)
                print(data3["body_text"][:3000])
                s.screenshot(str(OUT_DIR / ("publish_page_" + safe + ".png")))
                write_report(
                    {"url": page.url, "summary": dump_page_summary(page), "probe": data3},
                    OUT_DIR / ("publish_page_" + safe + ".json"),
                )
                print("\n截图: " + str(OUT_DIR / ("publish_page_" + safe + ".png")))
                return 0

        write_report(
            {"url": page.url, "summary": dump_page_summary(page), "probe": data},
            OUT_DIR / ("search_" + safe + ".json"),
        )
        print("\n截图: " + str(OUT_DIR / ("search_" + safe + ".png")))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
