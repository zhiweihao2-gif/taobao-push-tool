"""淘宝 / 天猫 采集器。

主通道：mtop.taobao.pcdetail.data.get（结构化、字段全）
详情图文：mtop.taobao.detail.getdesc
兜底：详情页内嵌 window.__INIT_DATA__ / TShop.Setup()
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..core.config import get_settings
from ..core.models import (Category, Image, Item, Param, PriceInfo, PropValue,
                           RawItem, SaleProp, Seller, Sku, Video)
from .base import (BaseCollector, api_stack_map, clean_image_url, dig,
                   extract_img_urls, maybe_json)
from .mtop import MTopClient
from .session import RiskDetector, SessionHealth, SessionStore

API_DETAIL = "mtop.taobao.pcdetail.data.get"
# 实测：pcdetail 的业务参数名是 `id`，不是 `itemId`。
# 传 itemId 会返回 FAIL_SYS_BIZPARAM_MISSED::缺少业务参数id。
API_DESC = "mtop.taobao.detail.getdesc"   # 实测已下线（FAIL_SYS_API_NOT_FOUNDED），仅保留探测用

_INIT_DATA = re.compile(r"window\.__INIT_DATA__\s*=\s*(\{.*?\});", re.S)
_TSHOP = re.compile(r"TShop\.Setup\(\s*(\{.*?\})\s*\)", re.S)


class TaobaoCollector(BaseCollector):
    platform = "taobao"

    def __init__(self, cookie_file: str | None = None, site: str = "taobao",
                 shop: str = "default"):
        s = get_settings()
        self.shop = shop
        self.session = SessionStore()
        self.health = SessionHealth(self.session)
        self.status = self.health.check("taobao", shop=shop)
        cookies = self.session.load("taobao", shop=shop)
        self.client = MTopClient(site=site, cookies=cookies,
                                 cookie_file=cookie_file or None)
        self.http = httpx.Client(
            timeout=float(s.get("http.timeout", 15)),
            follow_redirects=True,
            cookies=cookies or None,
            headers={"User-Agent": s.get("http.user_agent_pc")},
        )

    # ------------------------------------------------------------ fetch
    def source_url(self, item_id: str) -> str:
        return f"https://item.taobao.com/item.htm?id={item_id}"

    def fetch(self, item_id: str) -> RawItem:
        payload: dict[str, Any] = {}
        endpoint = API_DETAIL
        error = None
        try:
            resp = self.client.call(
                API_DETAIL, "1.0",
                {"id": item_id, "detail_v": "3.3.2", "exParams": "{}"},
            )
            payload = resp.get("data") or {}
        except Exception as e:
            error_code, hint = RiskDetector().classify(self.platform, 200, str(e))
            error = f"{error_code}: {hint}" if hint else f"{type(e).__name__}: {e}"
            endpoint = "html_fallback"
            payload = self._fetch_from_html(item_id)

        payload.setdefault("_desc_html", "")
        return RawItem(platform=self.platform, item_id=item_id, payload=payload,
                       endpoint=endpoint, error=error)

    def _fetch_from_html(self, item_id: str) -> dict[str, Any]:
        from .ratelimit import RateLimiter
        RateLimiter("taobao").acquire()      # HTML 兜底也要限速
        html = self.http.get(self.source_url(item_id)).text
        for pattern in (_INIT_DATA, _TSHOP):
            m = pattern.search(html)
            if m:
                data = maybe_json(m.group(1))
                if isinstance(data, dict):
                    return data.get("data", data)
        return {}

    # ------------------------------------------------------------ 详情图渲染
    def attach_rendered_desc(self, item: Item) -> Item:
        """用 Playwright 渲染详情页补 detail_images。

        淘宝 descUrl/apiDesc 接口已下线、PC 页是客户端渲染，静态请求拿不到详情图。
        这是唯一可行路径，代价是每个商品多 10~20 秒。
        """
        from .render import RenderError, render_detail_images

        try:
            r = render_detail_images(item.item_id, self.session.load("taobao"))
        except RenderError as e:
            item.extra["render_error"] = str(e)
            return item
        except Exception as e:
            item.extra["render_error"] = f"{type(e).__name__}: {e}"
            return item

        urls = r.get("detail_images") or []
        item.detail_images = [Image(url=u, role="detail") for u in urls]
        caps = item.extra.setdefault("capabilities", {})
        caps["detail_images"] = len(urls)
        item.extra["render"] = {
            "clicked_tab": r.get("clicked"),
            "page_images": r.get("all_count"),
            "detail_images": len(urls),
            "sizes": r.get("sizes"),
        }
        return item

    # ------------------------------------------------------------ to_item
    def to_item(self, raw: RawItem) -> Item:
        d = raw.payload or {}
        item_node = d.get("item") or d.get("itemDO") or {}
        sku_base = d.get("skuBase") or {}
        sku_core = d.get("skuCore") or {}
        sku2info = dig(sku_core, "sku2info", default={}) or {}
        stack = api_stack_map(d.get("apiStack"))

        title = dig(item_node, "title", default="") or dig(d, "title", default="")
        images = [clean_image_url(u) for u in (item_node.get("images") or []) if u]
        images = [u for u in images if u]

        # 销售属性
        sale_props: list[SaleProp] = []
        for p in sku_base.get("props") or []:
            values = [
                PropValue(
                    vid=str(v.get("vid") or v.get("valueId") or ""),
                    name=str(v.get("name") or v.get("valueName") or ""),
                    image=clean_image_url(v.get("image") or ""),
                )
                for v in (p.get("values") or [])
            ]
            sale_props.append(SaleProp(
                pid=str(p.get("pid") or p.get("propId") or ""),
                name=str(p.get("name") or p.get("propName") or ""),
                values=values,
            ))

        # SKU 扁平表
        skus: list[Sku] = []
        for s in sku_base.get("skus") or []:
            sid = str(s.get("skuId") or "")
            prop_path = s.get("propPath") or ""
            attrs = {}
            for seg in prop_path.split(";"):
                if ":" in seg:
                    pid, vid = seg.split(":", 1)
                    attrs[pid] = vid
            info = sku2info.get(sid) or {}
            price_txt = dig(info, "price.priceText", "price.priceMoney", default=None)
            qty = dig(info, "quantity", "quantityText", default=None)
            skus.append(Sku(
                sku_id=sid,
                attrs=attrs,
                prop_path=prop_path,
                price=_to_float(price_txt),
                quantity=_to_int(qty),
                outer_id=sid,
            ))

        # 单规格兜底：skuBase.skus 为空时，价格/库存在 sku2info["0"] 里。
        # 实测商品 630278968130（304不锈钢垫片）：skuBase.props/skus 都是 0 条，
        # 但 sku2info["0"] = {price:{priceText:"10"}, quantity:"200"}。
        # 不补这一步，单规格商品的价格和库存会整个丢失。
        single_sku_used = False
        if not skus:
            default = sku2info.get("0") or {}
            if default:
                price_txt = dig(default, "price.priceText", "price.priceMoney",
                                default=None)
                qty = dig(default, "quantity", default=None)
                skus.append(Sku(
                    sku_id=raw.item_id,
                    attrs={},
                    price=_to_float(price_txt),
                    quantity=_to_int(qty),
                    outer_id=raw.item_id,
                ))
                single_sku_used = True

        # 价格区间
        prices = [s.price for s in skus if s.price]
        price_info = PriceInfo(
            min=min(prices) if prices else _to_float(dig(item_node, "price", default=None)),
            max=max(prices) if prices else None,
        )

        # 参数表
        # 新版（实测）：componentsVO.extensionInfoVO.infos[].items[]，每项 {title, text[]}
        # 旧版兜底：props.groupProps[].{组名: {props: [{name, value}]}}
        params: list[Param] = []
        for info in (dig(d, "componentsVO.extensionInfoVO.infos", default=[]) or []):
            if not isinstance(info, dict):
                continue
            group = str(info.get("title") or "")
            for item in (info.get("items") or []):
                if not isinstance(item, dict):
                    continue
                name = str(item.get("title") or "").strip()
                texts = item.get("text") or []
                value = " ".join(str(t) for t in texts if t).strip()
                if name and value:
                    params.append(Param(group=group or None, name=name, value=value))

        if not params:
            for group in (dig(d, "props.groupProps", default=[]) or []):
                if not isinstance(group, dict):
                    continue
                for gname, gbody in group.items():
                    if not isinstance(gbody, dict):
                        continue
                    for p in (gbody.get("props") or []):
                        params.append(Param(group=str(gname),
                                            name=str(p.get("name") or ""),
                                            value=str(p.get("value") or "")))

        # 详情图
        desc_html = d.get("_desc_html") or ""
        if not desc_html:
            desc_html = dig(stack.get("descInfo", {}), "data.desc",
                            default="") or dig(stack.get("descInfo", {}), "descUrl", default="")
        detail_urls = extract_img_urls(desc_html) if "<img" in (desc_html or "") else []

        cat_path = []
        for node in (dig(item_node, "categoryPath", default=[]) or []):
            if isinstance(node, dict):
                cat_path.append(str(node.get("name") or ""))

        # 主图视频（实测：item.videos[] 里带 mp4 直链 + 封面 + 画幅比）
        videos: list[Video] = []
        for v in (item_node.get("videos") or []):
            if not isinstance(v, dict):
                continue
            u = str(v.get("url") or "").strip()
            if not u:
                continue
            if u.startswith("//"):
                u = "https:" + u
            videos.append(Video(
                url=u,
                video_id=str(v.get("videoId") or "") or None,
                cover=clean_image_url(v.get("videoThumbnailURL") or "") or None,
                dimension=str(v.get("spatialVideoDimension") or "") or None,
            ))

        return Item(
            item_id=raw.item_id,
            platform=self.platform,
            source_url=self.source_url(raw.item_id),
            title=title,
            seller=Seller(
                seller_id=str(dig(item_node, "sellerId", default="") or ""),
                nick=str(dig(d, "seller.nick", default="") or ""),
                shop_name=str(dig(d, "seller.shopName", default="") or ""),
            ),
            category=Category(
                cid=str(item_node.get("categoryId") or ""),
                path=cat_path,
                root_cid=str(item_node.get("rootCategoryId") or ""),
            ),
            price=price_info,
            main_images=[Image(url=u, role="main") for u in images],
            detail_images=[Image(url=u, role="detail") for u in detail_urls],
            videos=videos,
            detail_html=desc_html or None,
            video_url=videos[0].url if videos else None,
            sale_props=sale_props,
            skus=skus,
            params=params,
            stock_total=sum(s.quantity or 0 for s in skus) or None,
            raw_ref=f"raw/taobao/{raw.item_id}.json",
            extra={
                "api_stack_keys": list(stack.keys()),
                "endpoint": raw.endpoint,
                "collect_error": raw.error,
                "session": self.status.as_dict(),
                "capabilities": {
                    "title": bool(title),
                    "main_images": len(images),
                    "detail_images": len(detail_urls),
                    "videos": len(videos),
                    "sale_props": len(sale_props),
                    "skus": len(skus),
                    "params": len(params),
                    "price": "ok" if price_info.min is not None else "missing",
                },
            },
        )


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
