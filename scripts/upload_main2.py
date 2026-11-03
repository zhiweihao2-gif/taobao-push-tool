# -*- coding: utf-8 -*-
"""上传 5 张主图（修版）。

上一版失败：`empties.first.click()` 超时（被遮挡），且 `div.image-empty` 有 17 个
（含 3:4 主图、白底图），必须先按槽位尺寸筛出 1:1 主图（h≈88，3:4 是 h≈118）。

每个槽位流程：
  scroll_into_view -> click(force) -> 找 sucai iframe -> 清浮层
  -> expect_file_chooser + 本地上传 -> set_files
  -> 轮询「上传成功」-> 点「完成」(force) -> 按文件名点卡片 -> ESC
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

ITEM_ID = "902412139914"
MEDIA = ROOT / "data" / "media" / ITEM_ID
OUT = ROOT / "data" / "publish_poc"


def slots(page, want_h=(70, 110)):
    """返回 1:1 主图槽位（可见的 div.image-empty）。"""
    return page.evaluate(r"""
    (range) => {
      const [lo, hi] = range;
      const out = [];
      document.querySelectorAll('div.image-empty').forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.height < lo || r.height > hi) return;
        if (r.width < 40) return;
        el.setAttribute('data-dsh-slot', String(out.length));
        out.push({i: out.length, w: Math.round(r.width), h: Math.round(r.height),
                  x: Math.round(r.x), y: Math.round(r.y)});
      });
      return out;
    }""", list(want_h))


def find_sucai_frame(page):
    for f in page.frames:
        u = f.url or ""
        if "sucai-selector" in u or "sucai.wangpu" in u:
            return f
    return None


def clear_overlays(page, fr):
    for _ in range(3):
        n = fr.evaluate("""() => document.querySelectorAll('.next-overlay-wrapper.opened').length""")
        if n == 0:
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)


def click_done(fr):
    loc = fr.get_by_text("完成", exact=True)
    for i in range(loc.count() - 1, -1, -1):
        try:
            loc.nth(i).click(force=True, timeout=2500)
            return True
        except Exception:
            continue
    return False


def pick_card(fr, stem):
    """按文件名前缀点最新上传的那张卡。"""
    n = fr.evaluate(r"""
    (stem) => {
      const cards = [...document.querySelectorAll('[class*="pic_imgBox"]')];
      let hit = -1;
      cards.forEach((c, i) => {
        const t = (c.getAttribute('title') || '') + ' ' + (c.innerText || '');
        if (t.indexOf(stem) >= 0) hit = i;
      });
      if (hit >= 0) {
        cards[hit].setAttribute('data-dsh-pick', '1');
        return hit;
      }
      if (cards.length) {
        cards[0].setAttribute('data-dsh-pick', '0');
        return 0;
      }
      return -1;
    }""", stem)
    if n < 0:
        return False
    try:
        fr.locator('[data-dsh-pick]').first.click(force=True, timeout=4000)
        return True
    except Exception:
        return False


def upload_one(page, fpath):
    before = page.evaluate("""() => document.querySelectorAll('[class*="sell-component-simply-images"] img').length""")
    sl = slots(page)
    if not sl:
        print("      [XX] 没有可用 1:1 槽位")
        return False
    print("      槽位 %d 个，用 [0]" % len(sl))

    loc = page.locator('[data-dsh-slot="0"]')
    loc.scroll_into_view_if_needed(timeout=6000)
    page.wait_for_timeout(400)
    loc.click(force=True, timeout=6000)
    page.wait_for_timeout(2400)

    fr = find_sucai_frame(page)
    if fr is None:
        print("      [XX] 没找到图片空间 iframe（frames: %s）"
              % [f.url[:50] for f in page.frames][:5])
        page.keyboard.press("Escape")
        return False
    print("      iframe OK")

    clear_overlays(page, fr)

    try:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            up = fr.get_by_text("本地上传", exact=True)
            if up.count() == 0:
                up = fr.locator("button:has-text('本地上传')")
            up.first.click(force=True, timeout=6000)
        fc_info.value.set_files(str(fpath))
        print("      已选文件，等上传…")
    except Exception as e:
        print("      [XX] 上传失败 %s" % str(e).split("\n")[0][:110])
        page.keyboard.press("Escape")
        return False

    ok = False
    for _ in range(20):
        page.wait_for_timeout(700)
        if fr.evaluate("""() => {
            const t = document.body.innerText || '';
            return t.indexOf('上传成功') >= 0 || t.indexOf('上传结果') >= 0;
        }"""):
            ok = True
            break
    print("      %s 上传结果浮层" % ("OK" if ok else "!!"))

    click_done(fr)
    page.wait_for_timeout(800)

    stem = fpath.name[:14]
    if pick_card(fr, stem):
        print("      点了卡片 %s" % stem)
    else:
        print("      [!!] 找不到卡片 %s" % stem)

    page.wait_for_timeout(900)
    page.keyboard.press("Escape")
    page.wait_for_timeout(600)

    after = page.evaluate("""() => document.querySelectorAll('[class*="sell-component-simply-images"] img').length""")
    print("      回填: %d -> %d" % (before, after))
    return after > before


def main():
    files = sorted(MEDIA.glob("main_*.jpg"))
    print("本地主图 %d 个" % len(files))
    for f in files:
        print("  %s" % f.name)

    with BrowserSession() as sess:
        page = sess.page()
        print("URL: " + page.url)

        done = 0
        for i, f in enumerate(files[:5], 1):
            print("\n[%d/5] %s" % (i, f.name))
            try:
                if upload_one(page, f):
                    done += 1
                    print("      => OK")
                else:
                    print("      => 未生效")
            except Exception as e:
                print("      [!!] %s" % str(e).split("\n")[0][:120])
            time.sleep(0.8)

        print("\n" + "=" * 55)
        print("成功 %d / %d" % (done, min(5, len(files))))
        st = page.evaluate("""() => ({
          imgs: document.querySelectorAll('[class*="sell-component-simply-images"] img').length,
          empties: document.querySelectorAll('div.image-empty').length,
        })""")
        print("槽位: %s" % st)

        p = OUT / "after_main.png"
        sess.screenshot(p, full_page=False)
        print("截图: %s" % p)


if __name__ == "__main__":
    main()
