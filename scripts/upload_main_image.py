"""在图片空间弹窗里点「本地上传」，把本地图片传上去并选中。

已知：点主图槽位弹出 iframe（name="mainImagesGroup"，
src=market.m.taobao.com/app/crs-qn/sucai-selector-ng/index?type=pic&max=1...），
弹窗里有「本地上传」按钮，还有已上传素材网格。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession, human_pause  # noqa: E402

_IFRAME = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = { buttons: [], imgs: [], inputs: [] };
  document.querySelectorAll('button,[role=button],a').forEach(b => {
    const r = b.getBoundingClientRect();
    if (r.width < 10) return;
    out.buttons.push({ text: clean(b.innerText).slice(0, 20),
                       rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  document.querySelectorAll('input').forEach(i => {
    const r = i.getBoundingClientRect();
    out.inputs.push({ type: i.getAttribute('type'), ph: i.getAttribute('placeholder'),
                      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  document.querySelectorAll('img').forEach(i => {
    const r = i.getBoundingClientRect();
    if (r.width < 40) return;
    out.imgs.push({ alt: i.getAttribute('alt'), src: (i.src || '').slice(-60),
                    rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] });
  });
  return out;
}
"""


def find_frame(page):
    for f in page.frames:
        if "sucai-selector" in f.url:
            return f
    return None


def main() -> int:
    img_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if img_arg:
        img = Path(img_arg)
    else:
        img = ROOT / "data" / "media" / "1073025553052" / "main_6acbd81af3de3aa2.jpg"
    if not img.exists():
        print("[XX] 图片不存在: " + str(img))
        return 1
    print("图片: %s (%d 字节)" % (img, img.stat().st_size))

    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(800)
        if "publish.htm" not in page.url:
            print("[XX] 不在发布页")
            return 1

        fr = find_frame(page)
        if fr is None:
            print("[..] 图片空间 iframe 没开，先点主图槽位")
            page.locator(".image-empty").first.click()
            page.wait_for_timeout(3500)
            fr = find_frame(page)
        if fr is None:
            print("[XX] 还是找不到 iframe")
            for f in page.frames:
                print("    " + f.url[:110])
            return 1
        print("[1] iframe: " + fr.url[:100])

        info = fr.evaluate(_IFRAME)
        print("[2] iframe 内按钮:")
        for b in info["buttons"]:
            print("    %-18r %s" % (b["text"], b["rect"]))
        print("    inputs: %s" % info["inputs"])
        print("    图片 %d 张" % len(info["imgs"]))

        print("\n[3] 点「本地上传」")
        uploaded = False
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                fr.get_by_text("本地上传", exact=True).first.click(timeout=6000)
            fc = fc_info.value
            print("    [OK] 捕获 file chooser, multiple=%s" % fc.is_multiple())
            fc.set_files(str(img))
            uploaded = True
            print("    已设置文件: " + str(img))
        except Exception as exc:
            print("    [..] 失败: %s: %s" % (type(exc).__name__, str(exc)[:200]))

        if not uploaded:
            print("\n[3b] 换方式：找 iframe 里的 input[type=file]")
            try:
                fi = fr.locator("input[type=file]")
                n = fi.count()
                print("    file input 数量: %d" % n)
                if n:
                    fi.first.set_input_files(str(img))
                    uploaded = True
                    print("    已设置")
            except Exception as exc:
                print("    失败: %s" % exc)

        if not uploaded:
            s.screenshot(str(ROOT / "data" / "publish_poc" / "upload_failed.png"))
            print("[XX] 上传未发起，截图已存")
            return 1

        print("\n[4] 等上传完成（10 秒）")
        page.wait_for_timeout(10000)
        human_pause(1.0, 2.0)
        info = fr.evaluate(_IFRAME)
        print("    iframe 内图片 %d 张，按钮: %s" % (
            len(info["imgs"]), [b["text"] for b in info["buttons"]][:12]))

        s.screenshot(str(ROOT / "data" / "publish_poc" / "after_upload.png"))
        print("\n截图: data/publish_poc/after_upload.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
