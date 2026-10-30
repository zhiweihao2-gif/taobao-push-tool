"""京东采集器 v2 —— 基于移动端 SSR 页面（item.m.jd.com）。

实测结论（2026-10）：
- `item.jd.com/{sku}.html`（PC）已改为客户端渲染，返回 35KB 骨架页，无商品数据
- `p.3.cn/prices`、`c0.3.cn/stock`、`item-soa.jd.com`、`cd.jd.com/description` 均已下线/返回骨架
- `api.m.jd.com` 需要 sign，无 sign 返回 `{"code":"1","echo":"no access"}`
- **`item.m.jd.com/product/{sku}.html` 仍是服务端渲染**，内嵌 `window._itemOnly` 与 `window._itemInfo` 两份 JSON
- 未登录时 `priceFloor.price` 被脱敏为 `4??9`，`commonInfo.priceLoginText="登录查看价格"`

因此主通道是移动端页面 + 内嵌 JSON 解析；价格缺失时标记 `price_masked`。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from lxml import html as lxml_html

from ..core.config import get_settings
from ..core.models import (Category, Image, Item, Param, PriceInfo, RawItem,
                           SaleProp, PropValue, Seller, Sku)
from .base import BaseCollector, clean_image_url, extract_img_urls

URL_M = "https://item.m.jd.com/product/{sku}.html"
URL_PC = "https://item.jd.com/{sku}.html"
IMG_BASE = "https://img14.360buyimg.com/n0/"

# 详情图候选接口（逐个尝试，全部失败则返回空）
DESC_CANDIDATES = [
    "https://cd.jd.com/description/channel?skuId={sku}&mainSkuId={sku}",
    "https://item.m.jd.com/ware/description.json?wareId={sku}",
    "https://desc.jd.com/description/channel?skuId={sku}&mainSkuId={sku}",
]

_JS_VARS = ("_itemOnly", "_itemInfo")


class JDCollector(BaseCollector):
    platform = "jd"

    def __init__(self, cookie_file: str | None = None):
        s = get_settings()
        self.cookie_file = cookie_file or str(s.cookie_dir / "jd.txt")
        self.timeout = float(s.get("http.timeout", 20))
        # 移动端页面约 240KB，弱网下 20s 会超时；给到 35s 并重试
        self.http = httpx.Client(
            timeout=httpx.Timeout(35.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": s.get("http.user_agent_taobao"),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        self._load_cookies()

    def _get(self, url: str, retries: int = 2, **kw) -> httpx.Response:
        """带重试的 GET：ReadTimeout / 连接错误重试，其余直接抛出。"""
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self.http.get(url, **kw)
            except (httpx.ReadTimeout, httpx.ConnectTimeout,
                    httpx.RemoteProtocolError, httpx.ConnectError) as e:
                last = e
                if attempt >= retries:
                    break
                time.sleep(1.0 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _load_cookies(self) -> None:
        try:
            from .session import SessionStore  # 延迟导入，session 模块可选
            for k, v in SessionStore().load("jd").items():
                self.http.cookies.set(k, v, domain=".jd.com")
        except Exception:
            pass

    def source_url(self, item_id: str) -> str:
        return URL_PC.format(sku=item_id)

    # ------------------------------------------------------------ fetch
    def fetch(self, item_id: str) -> RawItem:
        payload: dict[str, Any] = {}
        error = None
        try:
            r = self._get(URL_M.format(sku=item_id),
                          headers={"Referer": "https://item.m.jd.com/"})
            payload["m_html"] = r.text
            payload["m_status"] = r.status_code
        except Exception as e:
            error = f"mobile: {type(e).__name__}: {e}"

        if not payload.get("m_html"):
            try:
                # 注意：UA 是移动端，PC URL 会被重定向到移动端页面，因此兜底同样有效
                payload["pc_html"] = self._get(URL_PC.format(sku=item_id)).text
            except Exception as e:
                error = f"{error}; pc: {type(e).__name__}: {e}"

        payload["desc_html"] = self._fetch_desc(item_id)
        return RawItem(platform=self.platform, item_id=item_id, payload=payload,
                       endpoint="m_ssr", error=error)

    def _fetch_desc(self, sku: str) -> str:
        for tpl in DESC_CANDIDATES:
            try:
                r = self._get(tpl.format(sku=sku), retries=0,
                              headers={"Referer": URL_M.format(sku=sku)})
                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    data = r.json()
                    html = (data.get("content") or data.get("description")
                            or (data.get("data") or {}).get("content") or "")
                    if html:
                        return html
                elif "html" in ct and len(r.text) > 2000 and "<img" in r.text:
                    return r.text
            except Exception:
                continue
        return ""

    # ------------------------------------------------------------ to_item
    def to_item(self, raw: RawItem) -> Item:
        m_html = raw.payload.get("m_html") or raw.payload.get("pc_html") or ""
        item_only = _js_object(m_html, "_itemOnly") or {}
        item_info = _js_object(m_html, "_itemInfo") or {}

        node = item_only.get("item") or {}
        prod = item_info.get("product") or {}
        stock = item_info.get("stock") or {}
        common = item_info.get("commonInfo") or {}

        title = str(prod.get("skuName") or node.get("skuName") or "").strip()
        if not title:
            doc = lxml_html.fromstring(m_html or "<html></html>")
            t = (doc.xpath("//title/text()") or [""])[0].strip()
            # 兜底标题必须像商品名，排除错误页 / 京东通用标题
            if t and not _is_bad_title(t):
                title = t.split("【")[0].strip()

        # 主图：jfs 路径 → CDN URL
        imgs: list[str] = []
        for p in (node.get("image") or []):
            u = _jd_img(p)
            if u and u not in imgs:
                imgs.append(u)
        if prod.get("imageurl"):
            u = _jd_img(prod["imageurl"])
            if u and u not in imgs:
                imgs.insert(0, u)

        # 类目
        cat_raw = str(prod.get("category") or "")
        cat_ids = [c for c in cat_raw.split(",") if c] or list(node.get("category") or [])
        cat_path = self._category_path(m_html)

        # 销售属性 / SKU
        sale_props, skus = _parse_sku(item_info, node, prod, raw.item_id)

        # 参数表
        params = _parse_params(m_html)
        for k, label in (("sellPoint", "卖点"), ("model", "型号"),
                         ("productArea", "产地"), ("weight", "重量(kg)")):
            v = prod.get(k)
            if v:
                params.append(Param(name=label, value=str(v)))
        if prod.get("upc"):
            params.append(Param(name="UPC", value=str(prod["upc"])))

        # 价格（可能脱敏）
        price_txt = str((item_info.get("priceFloor") or {}).get("price") or "")
        masked = "?" in price_txt
        price_val = None if masked else _to_float(price_txt)

        # 详情图
        desc_html = raw.payload.get("desc_html") or ""
        detail_urls = [_jd_img(u) for u in extract_img_urls(desc_html)] if desc_html else []
        detail_urls = [u for u in detail_urls if u]

        shop_name = str((stock.get("D") or {}).get("shopName") or "")

        return Item(
            item_id=raw.item_id,
            platform=self.platform,
            source_url=self.source_url(raw.item_id),
            title=title,
            seller=Seller(seller_id=str(prod.get("venderId") or node.get("venderID") or ""),
                          shop_name=shop_name),
            category=Category(cid=cat_ids[-1] if cat_ids else "", path=cat_path,
                              root_cid=cat_ids[0] if cat_ids else ""),
            price=PriceInfo(min=price_val, max=price_val),
            main_images=[Image(url=u, role="main") for u in imgs],
            detail_images=[Image(url=u, role="detail") for u in detail_urls],
            detail_html=desc_html or None,
            video_url=(f"https://vod.300hu.com/{item_info.get('mainVideoId')}.mp4"
                       if item_info.get("mainVideoId") else None),
            sale_props=sale_props,
            skus=skus,
            params=params,
            stock_total=_to_int(stock.get("StockState")),
            raw_ref=f"raw/jd/{raw.item_id}.json",
            extra={
                "category_ids": cat_ids,
                "brand": prod.get("brandName") or node.get("brandName"),
                "brand_id": prod.get("brandId") or node.get("brandId"),
                "price_masked": masked,
                "price_login_text": common.get("priceLoginText"),
                "stock_state": stock.get("StockState"),
                "sku_status": prod.get("skuStatus"),
                "video_id": item_info.get("mainVideoId"),
                "has_desc_html": bool(desc_html),
                "capabilities": {
                    "title": bool(title),
                    "main_images": len(imgs),
                    "params": len(params),
                    "sale_props": len(sale_props),
                    "price": "ok" if price_val is not None else "need_login",
                    "detail_images": len(detail_urls) if detail_urls else "need_login",
                },
            },
        )

    def _category_path(self, html: str) -> list[str]:
        if not html:
            return []
        doc = lxml_html.fromstring(html)
        crumbs = [" ".join(a.text_content().split())
                  for a in doc.xpath("//*[contains(@class,'crumb')]//a")]
        return [c for c in crumbs if c and c not in ("首页", "京东")]


# ---------------------------------------------------------------- 工具

def _js_object(text: str, var: str) -> dict | None:
    """括号配平截取 `window.<var> = ({...})`，再用 json5 宽松解析。"""
    if not text:
        return None
    m = re.search(rf"(?:window\.)?{re.escape(var)}\s*=\s*\(?\s*[\[{{]", text)
    if not m:
        return None
    start = text.index(m.group(0)[-1], m.start())
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        else:
            if ch in "\"'":
                in_str, quote = True, ch
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    raw = text[start:i + 1]
                    try:
                        import json5
                        return json5.loads(raw)
                    except Exception:
                        try:
                            return json.loads(raw)
                        except Exception:
                            return None
        i += 1
    return None


_BAD_TITLE_MARKS = ("HTTP Status", "Bad Request", "Not Found", "Error",
                    "京东(JD.COM)", "京东JD.COM", "验证", "安全验证", "访问过于频繁")


def _is_bad_title(t: str) -> bool:
    """判断从 <title> 抓到的文本是不是错误页标题，而不是商品名。"""
    if len(t.strip()) < 6:
        return True
    return any(m in t for m in _BAD_TITLE_MARKS)


def _jd_img(path: str) -> str:
    """jfs 相对路径或 CDN URL → 原图 URL。"""
    if not path:
        return ""
    p = path.strip()
    if p.startswith("//"):
        p = "https:" + p
    if p.startswith("http"):
        return clean_image_url(_jd_big(p))
    return IMG_BASE + p.lstrip("/")


_SIZE = re.compile(r"/(?:s\d+x\d+_)?(?:n\d|imgzone)/")


def _jd_big(url: str) -> str:
    """把京东 CDN 的缩略图路径统一换成原图路径。

    三种常见形态：
      /n0/jfs/...            → 原图（不变）
      /n1/jfs/...            → /n0/jfs/...
      /s450x450_jfs/...      → /n0/jfs/...
    """
    if "360buyimg.com" not in url:
        return url
    url = re.sub(r"/s\d+x\d+_jfs/", "/n0/jfs/", url)
    url = re.sub(r"/n\d+/", "/n0/", url)
    url = re.sub(r"/imgzone/", "/n0/", url)
    return url


def _parse_params(html: str) -> list[Param]:
    """参数表提取。

    实测：京东移动端 `detail-item` 节点里放的是**配送服务信息**（免运费/配送期数/
    配送次数/每期数量），不是商品参数；商品参数表本身是异步加载的。
    因此这里只取 PC 版 `p-parameter` 结构，并过滤配送类噪声。
    """
    if not html:
        return []
    doc = lxml_html.fromstring(html)
    params: list[Param] = []

    for li in doc.xpath("//ul[contains(@class,'parameter')]/li | "
                        "//div[contains(@class,'p-parameter')]//li"):
        text = " ".join(li.text_content().split())
        for sep in ("：", ":"):
            if sep in text:
                k, v = text.split(sep, 1)
                params.append(Param(name=k.strip(), value=v.strip()))
                break

    seen, out = set(), []
    for p in params:
        if p.name in seen or _is_shipping_noise(p.name):
            continue
        seen.add(p.name)
        out.append(p)
    return out


_NOISE_KEYS = ("免运费", "配送", "送至", "发货", "每期", "期数", "次数", "运费",
               "包邮", "库存", "数量", "起订")


def _is_shipping_noise(name: str) -> bool:
    return any(k in name for k in _NOISE_KEYS)


def _parse_sku(item_info: dict, node: dict, prod: dict,
               item_id: str) -> tuple[list[SaleProp], list[Sku]]:
    """从 _itemInfo 提取销售属性与 SKU；单规格商品退化为 1 个 SKU。"""
    sale_props: list[SaleProp] = []
    skus: list[Sku] = []

    color_size = (prod.get("newColorSize") or item_info.get("newColorSize")
                  or prod.get("colorSize") or item_info.get("colorSize") or [])
    sale_prop = (prod.get("saleProp") or item_info.get("saleProp") or {})
    sale_seq = (prod.get("salePropSeq") or item_info.get("salePropSeq") or {})

    if color_size and isinstance(color_size, list):
        # 多规格：newColorSize 每项形如 {"skuId":..,"颜色":..,"尺码":..}
        dims: dict[str, list[str]] = {}
        for entry in color_size:
            if not isinstance(entry, dict):
                continue
            for k, v in entry.items():
                if k in ("skuId", "price", "stock", "img", "skuImg"):
                    continue
                if isinstance(v, str) and v:
                    dims.setdefault(k, [])
                    if v not in dims[k]:
                        dims[k].append(v)
        for idx, (dim, values) in enumerate(dims.items()):
            pid = f"jd_dim_{idx}"
            sale_props.append(SaleProp(
                pid=pid,
                name=str(sale_prop.get(dim) or dim),
                values=[PropValue(vid=f"{pid}:{v}", name=v) for v in values],
            ))
        for entry in color_size:
            if not isinstance(entry, dict) or not entry.get("skuId"):
                continue
            attrs = {}
            for idx, dim in enumerate(dims):
                if entry.get(dim):
                    attrs[f"jd_dim_{idx}"] = f"jd_dim_{idx}:{entry[dim]}"
            skus.append(Sku(
                sku_id=str(entry["skuId"]),
                attrs=attrs,
                price=_to_float(entry.get("price")),
                quantity=_to_int(entry.get("stock")),
                image=_jd_img(entry.get("img") or entry.get("skuImg") or ""),
                outer_id=str(entry["skuId"]),
            ))
    elif sale_seq and isinstance(sale_seq, dict):
        # 只有维度没有 SKU 组合：生成属性，SKU 用笛卡尔积占位
        for idx, (dim, values) in enumerate(sale_seq.items()):
            if not isinstance(values, list) or not values:
                continue
            pid = f"jd_dim_{idx}"
            sale_props.append(SaleProp(
                pid=pid,
                name=str(sale_prop.get(dim) or dim),
                values=[PropValue(vid=f"{pid}:{v}", name=str(v)) for v in values],
            ))

    if not skus:
        price_txt = str((item_info.get("priceFloor") or {}).get("price") or "")
        skus = [Sku(
            sku_id=item_id,
            attrs={},
            price=None if "?" in price_txt else _to_float(price_txt),
            quantity=None,
            outer_id=item_id,
        )]
    return sale_props, skus


def _to_float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None
