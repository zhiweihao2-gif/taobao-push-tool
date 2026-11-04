"""逐张上传 + 点第一张回填"""
import sys, time, os, json
sys.path.insert(0, ".")
from src.publish.browser_pub import BrowserSession

def log(m=""): print(m, flush=True)
ITEM = "745703904965"
mdir = os.path.join("data", "media", ITEM)

def get_frame(page):
    for f in page.frames:
        if "sucai-selector" in (f.url or ""): return f
    return None

def state(page):
    return page.evaluate(r"""() => {
      const g = document.querySelector('#struct-mainImagesGroup');
      if (!g) return {filled:-1, empty:-1};
      return {filled: [...g.querySelectorAll("[class*='material-item-view']")].filter(e=>e.querySelector('img')).length,
              empty: g.querySelectorAll('.image-empty').length};
    }""")

def click_done(fr):
    """点上传结果浮层里的『完成』（高度 0，需 force）"""
    try:
        loc = fr.get_by_text("完成", exact=True)
        n = loc.count()
        for i in range(n-1, -1, -1):
            try:
                loc.nth(i).click(force=True, timeout=2000)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False

imgs = sorted(f for f in os.listdir(mdir) if f.startswith("main_"))[:5]
log("待上传 %d 张: %s" % (len(imgs), imgs))

with BrowserSession(reuse_url_contains="publish.htm") as s:
    page = s.page()
    page.set_viewport_size({"width": 1440, "height": 1000})
    log("URL: %s" % page.url[:70])

    for idx, fn in enumerate(imgs):
        st = state(page)
        log("")
        log("=== %d/%d %s  槽位 filled=%d empty=%d ===" % (idx+1, len(imgs), fn, st["filled"], st["empty"]))
        if st["filled"] >= 5:
            log("已满，停")
            break

        fr = get_frame(page)
        if not fr:
            page.evaluate("() => { const g=document.querySelector('#struct-mainImagesGroup'); if(g) g.scrollIntoView({block:'center'}); }")
            time.sleep(0.8)
            page.locator("#struct-mainImagesGroup .empty-container").first.click(force=True)
            for _ in range(20):
                time.sleep(0.6)
                fr = get_frame(page)
                if fr: break
            log("  iframe: %s" % ("OK" if fr else "失败"))
            if not fr: break
            time.sleep(1.5)

        # 关掉残留的「上传结果」浮层
        for _ in range(2):
            if not click_done(fr): break
            time.sleep(0.6)

        # 点「本地上传」
        up = fr.get_by_text("本地上传", exact=True)
        if not up.count():
            log("  [XX] 找不到本地上传")
            break
        path = os.path.abspath(os.path.join(mdir, fn))
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                up.first.click(force=True)
            fc = fc_info.value
            log("  file chooser: multiple=%s" % fc.is_multiple())
            fc.set_files(path)
        except Exception as e:
            log("  [XX] 上传失败: %s" % str(e).split("\n")[0][:90])
            break

        # 等「上传成功」
        ok = False
        for _ in range(24):
            time.sleep(0.6)
            try:
                t = fr.evaluate("() => document.body ? document.body.innerText : ''") or ""
            except Exception:
                t = ""
            if "上传成功" in t:
                ok = True; break
        log("  上传: %s" % ("成功" if ok else "超时"))
        time.sleep(0.8)
        click_done(fr)
        time.sleep(1.6)

        # 点第一张（最新）
        before = state(page)["filled"]
        try:
            fr.locator('[class*="pic_imgBox"]').first.click(force=True)
        except Exception as e:
            log("  [XX] 点卡片失败: %s" % str(e).split("\n")[0][:80])
            break
        time.sleep(2.4)
        after = state(page)["filled"]
        log("  槽位 %d -> %d  %s" % (before, after, "[OK]" if after > before else "[!!]"))

        fr2 = get_frame(page)
        if fr2:
            try:
                fr2.page.keyboard.press("Escape"); time.sleep(0.5)
            except Exception:
                pass

    final = page.evaluate(r"""() => {
      const g = document.querySelector('#struct-mainImagesGroup');
      return {filled: [...g.querySelectorAll("[class*='material-item-view']")].filter(e=>e.querySelector('img')).length,
              empty: g.querySelectorAll('.image-empty').length,
              srcs: [...g.querySelectorAll('img')].map(i => (i.src||'').split('/').slice(-1)[0].slice(0,50))};
    }""")
    log("")
    log("最终: %s" % json.dumps(final, ensure_ascii=False, indent=1))
