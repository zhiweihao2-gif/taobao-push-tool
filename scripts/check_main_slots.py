"""看主图槽位现状"""
import sys, time, json
sys.path.insert(0, ".")
from src.publish.browser_pub import BrowserSession

with BrowserSession(reuse_url_contains="publish.htm") as s:
    page = s.page()
    r = page.evaluate(r"""() => {
      const out = {};
      // 1:1 主图组
      const groups = [...document.querySelectorAll("[class*='sell-component-simply-materials-images-group']")];
      out.groups = groups.map((g, i) => {
        const imgs = [...g.querySelectorAll('img')];
        const empties = [...g.querySelectorAll('.image-empty')];
        return {
          i,
          imgs: imgs.length,
          empties: empties.length,
          srcs: imgs.map(im => (im.src||'').split('/').pop().slice(0, 40)),
        };
      });
      // 所有 image-empty
      out.allEmpties = document.querySelectorAll('.image-empty').length;
      out.allGroupImgs = document.querySelectorAll("[class*='sell-component-simply-images'] img").length;
      // 详情编辑器
      const eds = [...document.querySelectorAll('.cke_wysiwyg_div')];
      out.editors = eds.map(e => ({imgs: e.querySelectorAll('img').length, len: (e.innerHTML||'').length}));
      // 详情预览区
      const prev = document.querySelector("[class*='detail-preview'], [class*='sell-component-detail']");
      out.previewImgs = prev ? prev.querySelectorAll('img').length : null;
      return out;
    }""")
    print(json.dumps(r, ensure_ascii=False, indent=2)[:2500])
