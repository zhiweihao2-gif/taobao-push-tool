"""淘宝卖家中心浏览器自动化（PoC 底座）。

设计原则（来自多个实战项目的踩坑总结）：

1. **绝不自己启动浏览器**。只通过 CDP 挂接用户手动开着的 Chrome
   （`--remote-debugging-port=9222`）。浏览器被我们重启过，登录态和指纹就变了，
   平台会当成新设备。
2. **登录永远由真人完成**。本模块只负责检测「是否已登录」，不碰扫码/滑块/短信。
3. **标题/价格/库存必须真实键盘输入**。直接 `element.value = x` 不触发淘宝的
   受控组件逻辑，提交时会是空值。用 `keyboard.type()` 逐字符敲。
4. **品牌/型号用原生 setter + input/change 事件**。这类下拉搜索框 React 接管了
   value setter，必须走 `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,
   'value').set` 再 dispatch。
5. **直连类目 URL**，不点类目树。类目树是多级联动，容易点错且慢。

本模块只做到「填完 + 截图 + 等人确认」，**不自动点提交**。
"""

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

CDP_URL_DEFAULT = "http://127.0.0.1:9222"

# 卖家中心首页（判断登录态用）
SELLER_HOME = "https://myseller.taobao.com/home.htm"

# 发布页
PUBLISH_URL = "https://item.upload.taobao.com/sell/v2/publish.htm"
# 类目选择入口（人工用，脚本里不用）
CATEGORY_ENTRY = "https://item.upload.taobao.com/sell/ai/category.htm"
# 提交成功页（判断发布成功用）
SUCCESS_PATH = "/sell/v2/success.htm"

# 登录页特征
_LOGIN_HINTS = (
    "login.taobao.com",
    "loginmyseller.taobao.com",
    "havanaone",
    "member/login",
)

# 淘宝主域，用于判断当前页面是否还在自己的域内
_TAOBAO_HOSTS = ("taobao.com", "tmall.com")


class CDPError(RuntimeError):
    """CDP 连接层错误（浏览器没开 / 端口不对 / 不是 Chrome）。"""


class NotLoggedIn(RuntimeError):
    """卖家中心未登录。"""


# --------------------------------------------------------------------------- #
# CDP 连通性
# --------------------------------------------------------------------------- #


# CDP 端点认得的浏览器标识（Chrome / Chromium / Edge / 无头 Chrome 都能连）
_CDP_BROWSER_TOKENS = ("Chrome", "Chromium", "Edg/", "HeadlessChrome")


def check_chrome_alive(cdp_url: str = CDP_URL_DEFAULT, timeout: float = 5.0) -> tuple[bool, str]:
    """探测 CDP 端口是否有活的 Chromium 内核浏览器。

    Edge 和 Chrome 用的是同一套 CDP 协议，都算可用（本机可能只有 Edge）。
    返回 (是否可用, 说明)。不抛异常，方便启动器做分支判断。
    """
    base = cdp_url.rstrip("/")
    try:
        r = httpx.get(f"{base}/json/version", timeout=timeout)
    except httpx.ConnectError:
        return False, f"连不上 {base} —— 浏览器没开，或者没加 --remote-debugging-port"
    except httpx.TimeoutException:
        return False, f"{base} 超时 —— 浏览器可能还在启动，稍等几秒再试"
    except Exception as exc:  # noqa: BLE001
        return False, f"{base} 探测失败：{type(exc).__name__}: {exc}"

    if r.status_code != 200:
        return False, f"{base} 返回 HTTP {r.status_code}"

    try:
        info = r.json()
    except Exception:  # noqa: BLE001
        return False, f"{base} 返回的不是 JSON —— 这个端口可能被别的程序占了"

    browser = info.get("Browser", "?")
    if not any(tok in browser for tok in _CDP_BROWSER_TOKENS):
        return False, f"{base} 上的程序是 {browser}，不是 Chromium 内核浏览器"
    return True, f"{browser}，WebSocket: {info.get('webSocketDebuggerUrl', '?')[:60]}"


def list_pages(cdp_url: str = CDP_URL_DEFAULT, timeout: float = 3.0) -> list[dict]:
    """列出当前 Chrome 的所有标签页（不连 Playwright，纯 HTTP，很快）。"""
    base = cdp_url.rstrip("/")
    try:
        r = httpx.get(f"{base}/json/list", timeout=timeout)
        r.raise_for_status()
        return [t for t in r.json() if t.get("type") == "page"]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


@dataclass
class BrowserSession:
    """挂接用户手动开着的 Chrome。

    用法::

        with BrowserSession() as sess:
            page = sess.page()
            sess.goto(SELLER_HOME)
            if not sess.is_logged_in():
                raise SystemExit("请先在浏览器里扫码登录卖家中心")
    """

    cdp_url: str = CDP_URL_DEFAULT
    timeout_ms: int = 30000
    # 复用哪个标签页：None 表示新建
    reuse_url_contains: str | None = None
    _pw: Any = field(default=None, repr=False)
    _browser: Any = field(default=None, repr=False)
    _context: Any = field(default=None, repr=False)
    _page: Any = field(default=None, repr=False)

    # -- 生命周期 ---------------------------------------------------------- #

    def __enter__(self) -> "BrowserSession":
        ok, msg = check_chrome_alive(self.cdp_url)
        if not ok:
            raise CDPError(msg)

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:  # noqa: BLE001
            self._pw.stop()
            self._pw = None
            raise CDPError(f"CDP 连接失败：{type(exc).__name__}: {exc}") from exc

        # 用第一个 context（就是用户自己那个 profile 的 context），
        # 这样 cookie / localStorage / 指纹全部沿用，不产生新设备。
        contexts = self._browser.contexts
        if not contexts:
            self._context = self._browser.new_context()
        else:
            self._context = contexts[0]

        self._context.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, *exc_info: object) -> None:
        # 注意：只断开 Playwright，不关闭用户的浏览器
        self._page = None
        self._context = None
        self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None

    # -- 页面 -------------------------------------------------------------- #

    def page(self):
        """拿一个可用标签页。优先复用已有页面，避免开一堆。"""
        if self._page is not None:
            return self._page

        pages = [p for p in self._context.pages if not p.is_closed()]
        if self.reuse_url_contains:
            for p in pages:
                if self.reuse_url_contains in (p.url or ""):
                    self._page = p
                    return p

        # 找一个空标签页（about:blank / newtab）
        for p in pages:
            u = p.url or ""
            if u in ("about:blank", "") or "newtab" in u:
                self._page = p
                return p

        self._page = pages[0] if pages else self._context.new_page()
        return self._page

    def goto(self, url: str, wait_until: str = "domcontentloaded", settle_ms: int = 1200):
        page = self.page()
        page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)
        if settle_ms:
            page.wait_for_timeout(settle_ms)
        return page

    def current_url(self) -> str:
        return self.page().url or ""

    # -- 登录态 ------------------------------------------------------------ #

    def is_logged_in(self, navigate: bool = True) -> bool:
        """访问卖家中心首页，看是否被踢到登录页。

        判定依据：只要还在淘宝/天猫域下、且没命中登录页特征，就算已登录。
        不能要求必须停在 myseller —— 发布页域名是 item.upload.taobao.com，
        用「必须在 myseller」判会把这个正常页面误判成未登录。
        """
        page = self.page()
        if navigate:
            self.goto(SELLER_HOME, settle_ms=1500)
        url = (page.url or "").lower()
        if any(h in url for h in _LOGIN_HINTS):
            return False
        return "taobao.com" in url or "tmall.com" in url

    def login_state_detail(self) -> dict:
        """返回结构化登录态，便于启动器给提示。"""
        page = self.page()
        url = page.url or ""
        low = url.lower()
        hit = next((h for h in _LOGIN_HINTS if h in low), None)
        on_taobao = "taobao.com" in low or "tmall.com" in low
        return {
            "logged_in": hit is None and on_taobao,
            "url": url,
            "login_hint": hit,
            "title": _safe_title(page),
        }

    # -- 截图 -------------------------------------------------------------- #

    def screenshot(self, path: str | Path, full_page: bool = False, timeout_ms: int = 60000) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page().screenshot(
                path=str(p),
                full_page=full_page,
                timeout=timeout_ms,
                animations="disabled",
                caret="hide",
            )
        except Exception as exc:
            # 截图失败不该让整个探测流程崩掉，但要让人看见原因
            print("[!] 截图失败 %s: %s" % (p.name, str(exc).split("\n")[0][:120]))
        return p


# --------------------------------------------------------------------------- #
# 页面结构探测
# --------------------------------------------------------------------------- #


_DUMP_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  const labelFor = (el) => {
    // 1) <label for=id>
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return (l.innerText || '').trim().slice(0, 40);
    }
    // 2) 最近的 label 祖先
    const lp = el.closest('label');
    if (lp) return (lp.innerText || '').trim().slice(0, 40);
    // 3) 同级/父级里带 * 的文本（淘宝必填项常是 <div><span>*</span>标题</div>）
    let p = el.parentElement;
    for (let i = 0; i < 4 && p; i++, p = p.parentElement) {
      const t = (p.innerText || '').trim();
      if (t && t.length <= 40 && /[*＊]|标题|价格|库存|品牌|型号|类目|属性|规格/.test(t)) {
        return t.replace(/\s+/g, ' ').slice(0, 40);
      }
    }
    return '';
  };
  const push = (el, kind) => {
    const r = el.getBoundingClientRect();
    const key = kind + '|' + (el.id || '') + '|' + (el.name || '') + '|' + Math.round(r.x) + ',' + Math.round(r.y);
    if (seen.has(key)) return;
    seen.add(key);
    out.push({
      kind,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      id: el.id || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      cls: (el.className || '').toString().slice(0, 80),
      label: labelFor(el),
      visible: !!(r.width && r.height),
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      value: (el.value === undefined ? '' : String(el.value)).slice(0, 40),
      disabled: !!el.disabled,
      readonly: !!el.readOnly,
    });
  };
  document.querySelectorAll('input').forEach(e => push(e, 'input'));
  document.querySelectorAll('textarea').forEach(e => push(e, 'textarea'));
  document.querySelectorAll('select').forEach(e => push(e, 'select'));
  // contenteditable（富文本详情）
  document.querySelectorAll('[contenteditable="true"]').forEach(e => push(e, 'contenteditable'));
  return out;
}
"""


def _safe_title(page) -> str:
    try:
        return (page.title() or "")[:80]
    except Exception:  # noqa: BLE001
        return ""


def dump_form_fields(page) -> list[dict]:
    """把当前页所有可填字段 dump 出来，用于写填表逻辑前先看清结构。"""
    try:
        return page.evaluate(_DUMP_JS)
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "error", "error": f"{type(exc).__name__}: {exc}"}]


def dump_page_summary(page) -> dict:
    """页面概览：URL / 标题 / 关键区块文本 / 是否有错误提示。"""
    js = r"""
    () => {
      const text = (document.body && document.body.innerText) || '';
      const errs = [];
      document.querySelectorAll('[class*="error"],[class*="err-"],[class*="warning"]').forEach(e => {
        const t = (e.innerText || '').trim();
        if (t && t.length < 120) errs.push(t);
      });
      const steps = [];
      document.querySelectorAll('[class*="step"],[class*="tab-item"],[role="tab"]').forEach(e => {
        const t = (e.innerText || '').trim();
        if (t && t.length < 30) steps.push(t);
      });
      return {
        url: location.href,
        title: document.title,
        text_len: text.length,
        text_head: text.slice(0, 600),
        errors: [...new Set(errs)].slice(0, 20),
        steps: [...new Set(steps)].slice(0, 30),
        iframes: [...document.querySelectorAll('iframe')].map(f => f.src).slice(0, 10),
      };
    }
    """
    try:
        return page.evaluate(js)
    except Exception as exc:  # noqa: BLE001
        return {"url": getattr(page, "url", ""), "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# 输入原语
# --------------------------------------------------------------------------- #


def human_pause(lo: float = 0.4, hi: float = 1.2) -> None:
    """真人节奏的随机停顿。平台对固定间隔比对人慢更敏感。"""
    time.sleep(random.uniform(lo, hi))


def human_type(page, selector: str, text: str, clear_first: bool = True) -> None:
    """真实键盘逐字符输入。

    淘宝的标题/价格输入框是受控组件，`fill()` 或直接赋 value 在提交时
    会被清空或判为非法。必须聚焦后逐字符敲。
    """
    el = page.locator(selector).first
    el.click()
    human_pause(0.15, 0.35)
    if clear_first:
        # 全选再删，比 clear() 更像人
        page.keyboard.press("Control+A")
        human_pause(0.05, 0.12)
        page.keyboard.press("Delete")
        human_pause(0.1, 0.25)
    for ch in text:
        page.keyboard.type(ch)
        # 打字间隔抖一下，别是均匀的
        time.sleep(random.uniform(0.02, 0.09))


def set_value_native(page, selector: str, value: str) -> None:
    """用原生 setter 赋值并派发 input/change。

    用于品牌/型号这类 React 接管的搜索框 —— 它们劫持了 value 的 setter，
    直接 `el.value = x` 不会更新组件内部状态。
    """
    js = r"""
    ([sel, val]) => {
      const el = document.querySelector(sel);
      if (!el) return 'not_found';
      const proto = el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const desc = Object.getOwnPropertyDescriptor(proto, 'value');
      if (desc && desc.set) desc.set.call(el, val); else el.value = val;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      return 'ok';
    }
    """
    return page.evaluate(js, [selector, value])


def click_text(page, text: str, exact: bool = False, timeout_ms: int = 5000) -> bool:
    """按可见文本点元素。用于点「确认」「下一步」这类按钮。"""
    try:
        loc = page.get_by_text(text, exact=exact).first
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.click()
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 发布页导航
# --------------------------------------------------------------------------- #


def publish_url_for(cid: str | int, from_ai: bool = True) -> str:
    """直连某个类目的发布页。

    比点类目树稳 —— 类目树是多级联动，容易点错一级就进错类目。
    """
    q = f"?catId={cid}"
    if from_ai:
        q += "&fromAICategory=true"
    return PUBLISH_URL + q


def is_publish_page(url: str) -> bool:
    return "/sell/v2/publish.htm" in (url or "")


def is_success_page(url: str) -> bool:
    return SUCCESS_PATH in (url or "")


# --------------------------------------------------------------------------- #
# 探测报告
# --------------------------------------------------------------------------- #


def build_probe_report(session: BrowserSession, cid: str | int | None = None) -> dict:
    """完整探测：登录态 → 卖家中心 → 发布页 → 字段 dump。"""
    report: dict[str, Any] = {
        "cdp_url": session.cdp_url,
        "chrome": check_chrome_alive(session.cdp_url)[1],
        "pages_before": [{"url": p.get("url"), "title": p.get("title")} for p in list_pages(session.cdp_url)],
        "steps": [],
    }

    page = session.page()

    # 1) 登录态
    session.goto(SELLER_HOME, settle_ms=2000)
    state = session.login_state_detail()
    report["login"] = state
    report["steps"].append({"step": "seller_home", **state})
    if not state["logged_in"]:
        report["ok"] = False
        report["hint"] = "卖家中心未登录 —— 请在这个 Chrome 里扫码登录 myseller.taobao.com，然后重跑"
        return report

    # 2) 发布页（不带 catId，先看落地到哪）
    session.goto(PUBLISH_URL, settle_ms=2500)
    summary = dump_page_summary(page)
    report["steps"].append({"step": "publish_bare", "url": summary.get("url"), "title": summary.get("title")})

    if any(h in (summary.get("url") or "").lower() for h in _LOGIN_HINTS):
        report["ok"] = False
        report["hint"] = "发布页被踢到登录 —— 卖家登录态可能已过期"
        return report

    # 3) 带 catId 直连
    if cid:
        session.goto(publish_url_for(cid), settle_ms=3000)
        summary = dump_page_summary(page)
        report["steps"].append({"step": "publish_with_cid", "cid": str(cid), **summary})
        report["fields"] = dump_form_fields(page)
        report["screenshot"] = str(session.screenshot(f"data/publish_poc/probe_{cid}.png"))

    report["ok"] = True
    return report


def write_report(report: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 字段归类（把 dump 出来的原始字段按用途猜一遍，减少人工看的时间）
# --------------------------------------------------------------------------- #

_FIELD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("title", re.compile(r"标题|商品名|name|title", re.I)),
    ("price", re.compile(r"价格|售价|一口价|price", re.I)),
    ("stock", re.compile(r"库存|数量|stock|quantity|num", re.I)),
    ("outer_id", re.compile(r"商家编码|货号|outer|code|条码|barcode", re.I)),
    ("brand", re.compile(r"品牌|brand", re.I)),
    ("model", re.compile(r"型号|model", re.I)),
]


def classify_field(f: dict) -> str:
    hay = " ".join(
        str(f.get(k) or "") for k in ("label", "placeholder", "id", "name", "cls")
    )
    for name, pat in _FIELD_PATTERNS:
        if pat.search(hay):
            return name
    return "unknown"


def group_fields(fields: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in fields:
        if f.get("kind") == "error":
            out.setdefault("error", []).append(f)
            continue
        if not f.get("visible"):
            continue
        out.setdefault(classify_field(f), []).append(f)
    return out
