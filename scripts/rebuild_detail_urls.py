"""重建 detail_urls.json：把 detail_*.jpg 上传到自己的图片空间，再按文件名取回自家 URL。

为什么要这一步
--------------
淘宝发布页有**防盗图保护**：详情 HTML 里如果直接写原卖家的
``img.alicdn.com/imgextra/iX/<原卖家ID>/O1CN....jpg``，提交时报

    商品描述内容保存失败，原因是：发布详情数据失败，请稍后重试!
    详情中引用了他人图片，请检查后重新发布，
    图片：https://img.alicdn.com/imgextra/i1/2216675291672/O1CN01....jpg

只有先把图上传到自己的图片空间（本店 ID = 2858247125），再用空间返回的 URL
才能过。主图之所以一直没事，就是因为主图走的就是「上传到空间」这条路。

用法
----
    python scripts/rebuild_detail_urls.py --item 745703904965
    python scripts/rebuild_detail_urls.py --item 745703904965 --no-upload   # 只重新读 URL

输出
----
``data/publish_poc/detail_urls_<item>.json``，形如

    {"detail_01_d665453d26eaafb7.jpg": "https://img.alicdn.com/imgextra/iX/2858247125/O1CN....jpg", ...}

``publish_kb.py`` 的详情段会按 ``item.detail_images[i].local_path`` 的 basename 查这张表。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import BrowserSession, publish_url_for, check_chrome_alive  # noqa: E402

DEFAULT_CID = "110210"

# 点开图片空间：DOM 点击不行（React 事件接不住），必须直接调 memoizedProps.onClick
OPEN_SPACE = r"""() => {
  const e = document.querySelector('#struct-mainImagesGroup .image-empty');
  if (!e) return 'no-slot';
  const k = Object.keys(e).filter(x => x.startsWith('__react'));
  if (!k.length) return 'no-fiber';
  const p = e[k[0]].memoizedProps;
  if (!p || typeof p.onClick !== 'function') return 'no-onClick';
  p.onClick();
  return 'ok';
}"""

# 上传结果浮层里的「完成」——rect 高度 0，Playwright 判不可见，必须 force
DONE_BTN = "[class*='UploadP'][class*='btn'], button:has-text('完成')"

# 从文件名节点向上找卡片容器，读出 img.src，去掉尺寸后缀就是原图
COLLECT = r"""() => {
  const norm = u => String(u || '')
      .replace(/_(\d+x\d+)[^/]*$/, '')
      .replace(/\?.*$/, '');
  const out = {};
  const titles = document.querySelectorAll('[class*="PicList_title_wrap"]');
  titles.forEach(t => {
    const name = (t.innerText || '').trim();
    if (!name) return;
    let c = t;
    for (let i = 0; i < 6 && c; i++) {
      if (String(c.className || '').indexOf('PicList_PicturesShow_main-show') >= 0) break;
      c = c.parentElement;
    }
    const img = c ? c.querySelector('img') : null;
    if (img && img.src) out[name] = norm(img.src);
  });
  return out;
}"""


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--cid", default=DEFAULT_CID)
    ap.add_argument("--no-upload", action="store_true", help="只重新读 URL，不上传")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    a = ap.parse_args()

    files = sorted(glob.glob("data/media/%s/detail_*.jpg" % a.item))
    if not files:
        log("[XX] data/media/%s/ 下没有 detail_*.jpg" % a.item)
        return 1
    log("本地详情图 %d 张: %s … %s" % (len(files), os.path.basename(files[0]),
                                      os.path.basename(files[-1])))

    alive, msg = check_chrome_alive(a.cdp)
    if not alive:
        log("[XX] 浏览器没开: %s" % msg)
        return 1

    with BrowserSession(cdp_url=a.cdp, timeout_ms=60000) as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        url = publish_url_for(a.cid)
        log("[1] 打开发布页 %s" % url)
        page.goto("about:blank", wait_until="commit")
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_selector(".sell-catProp-item", state="attached", timeout=60000)
        time.sleep(3.5)

        log("[2] 打开图片空间")
        r = page.evaluate(OPEN_SPACE)
        log("    %s" % r)
        time.sleep(3.0)

        if not a.no_upload:
            fl = page.frame_locator("iframe[src*='sucai-selector']")
            log("[3] 点「本地上传」并投喂文件")
            # 图片空间里的 <input type=file> 是隐藏的且不在 iframe 的可枚举 frame 里，
            # 只能靠 Playwright 的 file chooser 拦截 —— 先挂监听，再点按钮。
            try:
                with page.expect_file_chooser(timeout=30000) as fc_info:
                    fl.locator("button:has-text('本地上传')").first.click(force=True, timeout=10000)
                fc_info.value.set_files(files)
                log("    已投喂 %d 个文件" % len(files))
            except Exception as e:
                log("    [XX] 投喂失败: %s" % str(e).split("\n")[0][:80])
                return 1

            # 等浮层出现「N 个文件上传成功」
            ok = False
            for i in range(90):
                time.sleep(1.0)
                try:
                    t = fl.locator("body").inner_text(timeout=3000)
                except Exception:
                    continue
                if ("%d 个文件上传成功" % len(files)) in t or "个文件上传成功" in t:
                    log("    上传完成（%ds）" % (i + 1))
                    ok = True
                    break
            if not ok:
                log("    [!!] 没等到上传成功提示，继续尝试读 URL")

            # 点「完成」关浮层（force：它 rect 高度是 0）
            for sel in ("[class*='UploadP'] button", "button:has-text('完成')"):
                try:
                    fl.locator(sel).last.click(force=True, timeout=4000)
                    log("    点完成: %s" % sel)
                    break
                except Exception:
                    continue
            time.sleep(4.0)

        log("[4] 读图片空间里的文件名 → URL")
        got = {}
        for _ in range(5):
            try:
                got = page.frame_locator("iframe[src*='sucai-selector']").locator("body").evaluate(COLLECT)
            except Exception as e:
                log("    [!!] %s" % str(e).split("\n")[0][:60])
                got = {}
            if got:
                break
            time.sleep(2.5)

        want = {os.path.basename(p): p for p in files}
        hit = {n: got[n] for n in want if n in got}
        log("    空间里共看到 %d 个文件名，命中 %d / %d" % (len(got), len(hit), len(want)))

        if len(hit) < len(want):
            miss = [n for n in want if n not in got][:5]
            log("    [!!] 未命中示例: %s" % ", ".join(miss))

        out_dir = os.path.join("data", "publish_poc")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "detail_urls_%s.json" % a.item)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(hit, f, ensure_ascii=False, indent=2)
        log("[5] 已写 %s（%d 条）" % (out, len(hit)))
        for n in sorted(hit)[:3]:
            log("    %s -> %s" % (n, hit[n][:88]))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
