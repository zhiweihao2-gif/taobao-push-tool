"""看 1:1 主图槽位结构 + 删除按钮"""
import sys, time, json
sys.path.insert(0, ".")
from src.publish.browser_pub import BrowserSession

with BrowserSession(reuse_url_contains="publish.htm") as s:
    page = s.page()
    r = page.evaluate(r"""() => {
      const g = document.querySelector("[class*='sell-component-simply-materials-images-group']");
      if (!g) return {err: 'no group'};
      // 组的外层标题
      let p = g, title = '';
      for (let i = 0; i < 4 && p; i++) { const t = (p.innerText||'').split('\n')[0]; if (t && t.length < 20) title = t; p = p.parentElement; }
      const imgs = [...g.querySelectorAll('img')];
      const items = [...g.querySelectorAll("[class*='material-item']")];
      return {
        title: title,
        groupHtml: g.outerHTML.slice(0, 400),
        itemCount: items.length,
        itemHtmls: items.slice(0,2).map(e => e.outerHTML.slice(0, 700)),
        imgSrcs: imgs.map(i => (i.src||'').split('/').pop()),
        hasDelete: g.querySelectorAll("[class*='delete'], [class*='remove'], [class*='close']").length,
      };
    }""")
    print(json.dumps(r, ensure_ascii=False, indent=2)[:4000])
