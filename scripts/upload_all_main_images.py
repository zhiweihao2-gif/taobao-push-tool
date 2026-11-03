"""传满 5 张主图（修正版）。

关键：上传后会出现「上传结果」浮层，里面有「完成」按钮。
浮层 rect 高度为 0，Playwright 判定不可见 → 必须 force=True 点。
点完「完成」图片才进网格。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402

MEDIA = ROOT / "data" / "media" / "1073025553052"
TARGET = 5

_STATE = r"""
() => ({
  empties: document.querySelectorAll('.image-empty').length,
  n: document.querySelectorAll('.sell-component-simply-images img').length,
})"""

_CARDS = "() => document.querySelectorAll('[class*=\"pic_imgBox\"]').length"


def find_space(page):
    for f in page.frames:
        if "sucai-selector" in f.url:
            return f
    return None


def click_done(fr) -> bool:
    """点上传结果浮层里的「完成」。浮层 rect 高度为 0，必须 force=True。"""
    try:
        loc = fr.get_by_text("完成", exact=True)
        n = loc.count()
    except Exception:
        return False
    for i in range(n - 1, -1, -1):
        try:
            loc.nth(i).click(force=True, timeout=2500)
            return True
        except Exception:
            continue
    return False


def clear_overlays(fr, page) -> int:
    """关掉所有「上传结果」浮层，返回关了几个。"""
    closed = 0
    for _ in range(3):
        try:
            cnt = fr.evaluate(
                "() => document.querySelectorAll('.next-overlay-wrapper.opened').length"
            )
        except Exception:
            cnt = 0
        if not cnt:
            break
        if click_done(fr):
            closed += 1
            page.wait_for_timeout(900)
        else:
            break
    return closed


def main() -> int:
    files = sorted(MEDIA.glob("main_*.jpg"))
    print("本地主图 %d 张" % len(files))

    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(500)

        st = page.evaluate(_STATE)
        print("起始: empties=%d imgs=%d" % (st["empties"], st["n"]))

        fr = find_space(page)
        if fr is None:
            print("[..] 打开图片空间")
            page.locator(".image-empty").first.click()
            page.wait_for_timeout(2500)
            fr = find_space(page)
        if fr is None:
            print("[XX] 打不开图片空间")
            return 1

        base_cards = fr.evaluate(_CARDS)
        print("网格里现有 %d 张图" % base_cards)

        # 先清掉上次残留的「上传结果」浮层
        n_closed = clear_overlays(fr, page)
        if n_closed:
            print("清掉残留浮层 %d 个" % n_closed)
            page.wait_for_timeout(800)
            base_cards = fr.evaluate(_CARDS)
            print("清后网格: %d 张" % base_cards)
        print()

        done = st["n"]
        todo = files[done:TARGET] if done < TARGET else []
        print("待传 %d 张: %s\n" % (len(todo), [p.name for p in todo]))

        for idx, path in enumerate(todo, start=done + 1):
            t0 = time.time()
            print("[%d/%d] %s" % (idx, TARGET, path.name))

            # 关残留弹窗
            if find_space(page) is None:
                empties = page.locator(".image-empty")
                if empties.count() == 0:
                    print("    [XX] 没空槽")
                    break
                empties.first.scroll_into_view_if_needed(timeout=5000)
                empties.first.click()
                page.wait_for_timeout(2200)
                fr = find_space(page)
                if fr is None:
                    print("    [XX] 图片空间没开")
                    break
            else:
                clear_overlays(fr, page)

            # 上传
            try:
                with page.expect_file_chooser(timeout=8000) as fc:
                    fr.get_by_text("本地上传", exact=True).first.click()
                fc.value.set_files(str(path))
            except Exception as exc:
                print("    [XX] 上传失败: %s" % exc)
                break

            # 等「上传结果」浮层出现（含「个文件上传成功」）
            ok = False
            for _ in range(24):
                page.wait_for_timeout(400)
                try:
                    txt = fr.evaluate("() => document.body.innerText")
                except Exception:
                    txt = ""
                if "上传成功" in txt or "上传失败" in txt:
                    ok = True
                    break
            print("    结果浮层: %s" % ("出现" if ok else "未出现"))

            # 点「完成」关浮层
            hit = click_done(fr)
            print("    点「完成」: %s" % ("OK" if hit else "未找到"))
            page.wait_for_timeout(1200)

            # 校验网格图片数
            try:
                now = fr.evaluate(_CARDS)
            except Exception:
                now = base_cards
            print("    网格: %d -> %d" % (base_cards, now))
            base_cards = now

            # 点第一张（最新上传的）
            try:
                cards = fr.locator('[class*="pic_imgBox"]')
                cards.first.scroll_into_view_if_needed(timeout=4000)
                cards.first.click(timeout=6000)
                page.wait_for_timeout(1000)
                print("    点图: OK")
            except Exception as exc:
                print("    点图失败: %s" % str(exc).split("\n")[0])

            # 关选择弹窗
            page.keyboard.press("Escape")
            page.wait_for_timeout(700)

            st = page.evaluate(_STATE)
            print("    -> empties=%d imgs=%d  %.1fs\n" % (st["empties"], st["n"], time.time() - t0))
            if st["n"] < idx:
                print("    [!] 槽位没增加\n")

        st = page.evaluate(_STATE)
        print("=" * 50)
        print("最终: empties=%d imgs=%d" % (st["empties"], st["n"]))
        s.screenshot(str(ROOT / "data" / "publish_poc" / "main_images_done.png"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
