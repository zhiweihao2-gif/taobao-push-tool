"""淘宝类目接口封装（走卖家中心的 asyncOpt 接口，需登录态）。

三个能力:
    search(keyword)     关键词 → 类目列表（带 id/name/path/idpath/leaf/publish）
    children(cat_id)    某个类目 → 直接子类目
    resolve(cat_id)     类目 ID → 完整信息（靠 search 命中或逐级下钻）

为什么不用 open API: 淘宝开放平台的类目接口需要企业资质，个人店铺拿不到。
为什么走这个接口: 类目选择页是 SPA，数据全从 asyncOpt.htm 来，页面只是渲染器。
    实测 200 条路径、无需签名，只要卖家登录态。

用法:
    from src.publish.category_api import CategoryApi

    with CategoryApi() as api:
        cats = api.search("键帽")
        # [{"id": 200670001, "name": "键帽", "path": ["电脑硬件/显示器/电脑周边", "键帽"],
        #   "idpath": [11, 200670001], "leaf": True, "publish": True}]
        kids = api.children(50008090)
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

BASE = "https://item.upload.taobao.com"
SEARCH_URL = BASE + "/sell/ai/asyncOpt.htm"
TREE_URL = BASE + "/router/asyncOpt.htm"
ENTRY_URL = BASE + "/sell/ai/category.htm"

_GEXT = '{"startTraceId":"2150459617936722098508858e0a6c","newImageUi":true}'

_FETCH_JS = """async (u) => {
    const r = await fetch(u, {method:'GET', credentials:'include',
                              headers:{'Accept':'application/json, text/plain, */*'}});
    return {status: r.status, body: await r.text()};
}"""


class CategoryApiError(RuntimeError):
    def __init__(self, message: str, payload: Any = None):
        super().__init__(message)
        self.message = message
        self.payload = payload


def _norm(cat: dict) -> dict:
    """统一字段。path 可能只有两级，idpath 才是完整链路。"""
    cid = cat.get("id") or cat.get("submitId")
    return {
        "id": cid,
        "name": cat.get("name") or "",
        "path": list(cat.get("path") or []),
        "idpath": list(cat.get("idpath") or []),
        "leaf": bool(cat.get("leaf")),
        "publish": bool(cat.get("publish", True)),
        "spell": cat.get("spell") or "",
        "is_brand": bool(cat.get("isBrand")),
        "tips": cat.get("tips") or "",
    }


def _parse(body: str) -> list[dict]:
    try:
        d = json.loads(body)
    except Exception:
        return []
    if not d.get("success", True):
        return []
    data = d.get("data") or {}
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    # search 接口：data.category[]
    for c in (data.get("category") or []):
        if isinstance(c, dict):
            out.append(_norm(c))
    if out:
        return out
    # 树接口：data.dataSource[].children[]（第一层是分组）
    for g in (data.get("dataSource") or []):
        if not isinstance(g, dict):
            continue
        kids = g.get("children")
        if kids:
            for k in kids:
                if isinstance(k, dict):
                    out.append(_norm(k))
        elif g.get("name"):
            # 下钻时没有 children 包裹，直接就是类目
            out.append(_norm(g))
    for key in ("children", "childList", "list"):
        for k in (data.get(key) or []):
            if isinstance(k, dict):
                out.append(_norm(k))
    return out


class CategoryApi:
    """类目查询。两种后端:

    http     默认。用采集用的 cookie（config/cookies/taobao.txt）直连，
             不需要浏览器、不受 mtop 限速器约束、可并发。
    browser  兜底。CDP 挂用户浏览器在页面上下文 fetch，cookie 更全（含卖家域）。
    """

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

    def __init__(self, backend: str = "http", cdp_url: str | None = None,
                 session=None, cookie_header: str | None = None):
        self._backend = backend
        self._cdp_url = cdp_url
        self._own_session = session is None
        self._session = session
        self._page = None
        self._client = None
        self._cookie = cookie_header or ""
        self._last_call = 0.0

    # ---- 生命周期 ----
    def __enter__(self) -> "CategoryApi":
        if self._backend == "browser":
            self._open_browser()
        else:
            self._open_http()
        return self

    def _open_http(self):
        import httpx
        if not self._cookie:
            try:
                from src.collect.session import SessionStore
                self._cookie = SessionStore().cookie_header("taobao") or ""
            except Exception:
                self._cookie = ""
        if not self._cookie:
            raise CategoryApiError("没有淘宝 cookie，先跑登录脚本")
        self._client = httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": self.UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": ENTRY_URL,
                "Cookie": self._cookie,
            },
        )

    def _open_browser(self):
        if self._session is None:
            from src.publish.browser_pub import BrowserSession, CDP_URL_DEFAULT
            self._session = BrowserSession(cdp_url=self._cdp_url or CDP_URL_DEFAULT)
            self._session.__enter__()
        self._page = self._session.page()
        try:
            self._page.set_viewport_size({"width": 1440, "height": 1000})
        except Exception:
            pass
        cur = ""
        try:
            cur = self._page.url or ""
        except Exception:
            pass
        if "item.upload.taobao.com" not in cur:
            try:
                self._page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                raise CategoryApiError("打不开类目页：%s" % str(e).split("\n")[0][:120])
            self._page.wait_for_timeout(2000)

    def __exit__(self, *exc):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        if self._own_session and self._session is not None:
            self._session.__exit__(*exc)
        return False

    # ---- 底层 ----
    def _fetch(self, url: str) -> str:
        if self._backend == "browser":
            r = self._page.evaluate(_FETCH_JS, url)
            if r["status"] != 200:
                raise CategoryApiError("HTTP %s" % r["status"], r["body"][:300])
            return r["body"]
        # 限速：实测 2.3 秒打 144 个请求会触发滑块（_____tmd_____/punish?x5secdata=）
        self._throttle()
        r = self._client.get(url)
        if r.status_code != 200:
            raise CategoryApiError("HTTP %s" % r.status_code, r.text[:300])
        if "_____tmd_____" in r.text or "x5secdata" in r.text[:600]:
            raise CategoryApiError("触发滑块验证，需等待冷却", r.text[:200])
        return r.text

    _MIN_INTERVAL = 0.35   # 秒/请求 ≈ 每秒 3 个

    def _throttle(self):
        import time as _t
        now = _t.time()
        wait = self._MIN_INTERVAL - (now - self._last_call)
        if wait > 0:
            _t.sleep(wait)
        self._last_call = _t.time()

    @staticmethod
    def _search_url(keyword: str) -> str:
        return SEARCH_URL + "?" + urllib.parse.urlencode({
            "optType": "retrievalDataAsyncOpt",
            "jsonBody": json.dumps({"keyword": keyword}, ensure_ascii=False),
            "globalExtendInfo": _GEXT,
        })

    @staticmethod
    def _tree_url(cat_id: str | int | None) -> str:
        p = {"optType": "categorySelectChildren", "fromSmart": "true",
             "globalExtendInfo": _GEXT}
        if cat_id:
            p["catId"] = str(cat_id)
        return TREE_URL + "?" + urllib.parse.urlencode(p)

    # ---- 公开 ----
    def search(self, keyword: str) -> list[dict]:
        """关键词搜类目。平台会给最匹配的那个，通常 1 个。"""
        return _parse(self._fetch(self._search_url(keyword)))

    def children(self, cat_id: str | int | None = None) -> list[dict]:
        """直接子类目。传 None 拿顶级分组下的全部一级类目。"""
        return _parse(self._fetch(self._tree_url(cat_id)))

    def resolve(self, cat_id: str | int) -> dict | None:
        """按 ID 找类目。先试 search（精确），再从一级往下钻。

        返回带完整 idpath 的那条；找不到返回 None。
        """
        cid = str(cat_id)
        # 1. 直接搜索这个 ID（平台支持，实测有效）
        try:
            for c in self.search(cid):
                if str(c["id"]) == cid:
                    return c
        except CategoryApiError:
            pass
        # 2. 从一级递归找
        stack = list(self.children(None))
        guard = 0
        while stack and guard < 400:
            guard += 1
            node = stack.pop(0)
            if str(node["id"]) == cid:
                return node
            if not node["leaf"]:
                try:
                    stack.extend(self.children(node["id"]))
                except CategoryApiError:
                    continue
                if len(stack) > 1200:
                    break
        return None

    def path_names(self, cat_id: str | int) -> list[str]:
        """按 ID 反查类目名链路（从根到叶）。"""
        info = self.resolve(cat_id)
        if info:
            return info["path"] or [info["name"]]
        return []

    def walk(self, cat_id: str | int | None = None, max_depth: int = 3,
             limit: int = 400) -> list[dict]:
        """递归展开子树（有上限，避免把整站类目拉下来）。"""
        out: list[dict] = []
        queue = [(c, 1) for c in self.children(cat_id)]
        while queue and len(out) < limit:
            node, depth = queue.pop(0)
            node["depth"] = depth
            out.append(node)
            if depth < max_depth and not node["leaf"]:
                try:
                    queue.extend((c, depth + 1) for c in self.children(node["id"]))
                except CategoryApiError:
                    continue
        return out


class CategoryCache:
    """本地类目缓存。读 data/category_tree/all.json，零网络、零限速。

    build_category_cache.py 生成，实测 1875 节点 / 1152 末级 / 848KB。

    用法:
        cache = CategoryCache()
        cache.get(200670001)        # {'id','name','path','leaf','tips','parent',...}
        cache.name_of(200670001)    # '键帽'
        cache.path_of(200670001)    # ['电脑硬件/显示器/电脑周边', '键帽']
        cache.find("键帽")           # 按名字模糊找
    """

    DEFAULT_PATH = r"data/category_tree/all.json"

    def __init__(self, path: str | None = None):
        import os
        p = path or self.DEFAULT_PATH
        if not os.path.isabs(p):
            root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            p = os.path.join(root, p)
        self._path = p
        self._nodes: dict[str, dict] = {}
        self._meta: dict = {}
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self._nodes = d.get("nodes") or {}
            self._meta = {k: v for k, v in d.items() if k != "nodes"}

    @property
    def ready(self) -> bool:
        return bool(self._nodes)

    @property
    def count(self) -> int:
        return len(self._nodes)

    @property
    def built_at(self) -> str:
        return str(self._meta.get("built_at") or "")

    def get(self, cat_id: str | int) -> dict | None:
        return self._nodes.get(str(cat_id))

    def name_of(self, cat_id: str | int, default: str = "") -> str:
        n = self.get(cat_id)
        return (n or {}).get("name") or default

    def path_of(self, cat_id: str | int) -> list[str]:
        n = self.get(cat_id)
        return list((n or {}).get("path") or [])

    def tips_of(self, cat_id: str | int) -> str:
        n = self.get(cat_id)
        return str((n or {}).get("tips") or "")

    def is_leaf(self, cat_id: str | int) -> bool:
        n = self.get(cat_id)
        return bool((n or {}).get("leaf"))

    def children_of(self, cat_id: str | int | None) -> list[dict]:
        key = "" if cat_id in (None, "", 0) else str(cat_id)
        return [v for v in self._nodes.values() if v.get("parent") == key]

    def find(self, keyword: str, leaves_only: bool = False,
             limit: int = 20) -> list[dict]:
        """按名称模糊找，精确匹配排前面；纯数字当 cid 精确查。"""
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        # 纯数字当 cid 用（卖家经常直接粘 cid，别让他搜不到）
        if kw.isdigit():
            n = self.get(kw)
            if n and (not leaves_only or n.get("leaf")):
                return [n]
        exact, id_hit, contained, partial = [], [], [], []
        for v in self._nodes.values():
            if leaves_only and not v.get("leaf"):
                continue
            name = str(v.get("name") or "").lower()
            path = " ".join(v.get("path") or []).lower()
            if name == kw:
                exact.append(v)
            elif kw in str(v.get("id") or ""):
                id_hit.append(v)
            elif kw in name or kw in path:
                partial.append(v)
            elif len(name) >= 2 and name in kw:
                # 「机械键盘」→ 类目「键盘」；「USB数据线」→ 类目「数据线」
                contained.append(v)
        return (exact + id_hit + partial + contained)[:limit]

    def describe(self, cat_id: str | int) -> str:
        n = self.get(cat_id)
        if not n:
            return "cid=%s（缓存里没有）" % cat_id
        parts = [" > ".join(n.get("path") or [n.get("name")])]
        if n.get("leaf"):
            parts.append("[末级]")
        if n.get("tips"):
            parts.append("提示: %s" % n["tips"])
        return " ".join(parts)
