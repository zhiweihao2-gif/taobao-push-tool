"""1688 采集器：mtop.1688.offer.detail.get（h5api.m.1688.com，同一套 sign 机制）。"""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..core.config import get_settings
from ..core.models import (Category, Image, Item, Param, PriceInfo, PropValue,
                           RawItem, SaleProp, Seller, Sku)
from .base import (BaseCollector, clean_image_url, dig, extract_img_urls,
                   maybe_json)
from .mtop import MTopClient
from .session import RiskDetector, SessionHealth, SessionStore

API_DETAIL = "mtop.1688.offer.detail.get"
API_DETAIL_ALT = "mtop.alibaba.cbu.offer.detail.get"

_INIT_DATA = re.compile(r"window\.__INIT_DATA\s*=\s*(\{.*?\});", re.S)


class Alibaba1688Collector(BaseCollector):
    platform = "alibaba"

    def __init__(self, cookie_file: str | None = None, shop: str = "default"):
        s = get_settings()
        self.shop = shop
        self.session = SessionStore()
        self.health = SessionHealth(self.session)
        self.status = self.health.check("alibaba", shop=shop)
        cookies = self.session.load("alibaba", shop=shop)
        self.client = MTopClient(site="alibaba", cookies=cookies,
                                 cookie_file=cookie_file or None)
        self.http = httpx.Client(
            timeout=float(s.get("http.timeout", 15)),
            follow_redirects=True,
            cookies=cookies or None,
            headers={"User-Agent": s.get("http.user_agent_pc")},
        )

    def source_url(self, item_id: str) -> str:
        return f"https://detail.1688.com/offer/{item_id}.html"

    def fetch(self, item_id: str) -> RawItem:
        payload: dict[str, Any] = {}
        endpoint, error = API_DETAIL, None
        for api in (API_DETAIL, API_DETAIL_ALT):
            try:
                resp = self.client.call(api, "1.0",
                                        {"offerId": item_id, "detail_v": "1.0"})
                payload = resp.get("data") or {}
                endpoint = api
                break
            except Exception as e:
                code, hint = RiskDetector().classify(self.platform, 200, str(e))
                error = f"{code}: {hint}" if hint else f"{type(e).__name__}: {e}"
        if not payload:
            endpoint = "html_fallback"
            payload = self._fetch_from_html(item_id)
        return RawItem(platform=self.platform, item_id=item_id, payload=payload,
                       endpoint=endpoint, error=error)

    def _fetch_from_html(self, item_id: str) -> dict[str, Any]:
        from .ratelimit import RateLimiter
        RateLimiter("alibaba").acquire()
        html = self.http.get(self.source_url(item_id)).text
        # 1688 反爬拦截页特征
        if "_____tmd_____" in html or "x5secdata" in html or len(html) < 3000:
            code, hint = RiskDetector().classify(self.platform, 200, html)
            return {"_blocked": True, "_block_code": code, "_block_hint": hint}
        m = _INIT_DATA.search(html)
        if m:
            data = maybe_json(m.group(1))
            if isinstance(data, dict):
                return data
        return {}

    def to_item(self, raw: RawItem) -> Item:
        d = raw.payload or {}
        model = dig(d, "globalData.tempModel", default=None) or d
        title = str(dig(model, "offerTitle", "subject", default="") or "")

        imgs = []
        for node in (dig(model, "imageList", default=[]) or []):
            u = node.get("fullPathImageURI") or node.get("imageURI") or ""
            u = clean_image_url(u)
            if u:
                imgs.append(u)

        # 销售属性 + SKU
        sale_props: list[SaleProp] = []
        sku_model = model.get("skuModel") or {}
        for idx, p in enumerate(sku_model.get("skuProps") or []):
            values = [
                PropValue(vid=f"{idx}:{j}", name=str(v.get("name") or ""),
                          image=clean_image_url(v.get("imageUrl") or ""))
                for j, v in enumerate(p.get("value") or [])
            ]
            sale_props.append(SaleProp(pid=f"prop{idx}", name=str(p.get("prop") or ""),
                                       values=values))

        skus: list[Sku] = []
        for node in (sku_model.get("skuInfoMap") or []):
            spec = node.get("specAttrs") or ""
            attrs: dict[str, str] = {}
            for idx, seg in enumerate(spec.split(">")):
                seg = seg.strip()
                if ":" in seg:
                    k, v = seg.split(":", 1)
                    attrs[f"name:{k.strip()}"] = v.strip()
            skus.append(Sku(
                sku_id=str(node.get("specId") or ""),
                attrs=attrs,
                prop_path=spec,
                price=_to_float(node.get("price")),
                quantity=_to_int(node.get("canBookCount") or node.get("amountOnSale")),
                outer_id=str(node.get("specId") or ""),
            ))

        params = [
            Param(name=str(p.get("name") or ""), value=str(p.get("value") or ""))
            for p in (model.get("productFeatureList") or [])
        ]

        prices = [s.price for s in skus if s.price]
        detail_url = str(d.get("detailUrl") or model.get("detailUrl") or "")
        detail_html, detail_imgs = "", []
        if detail_url.startswith("http"):
            try:
                detail_html = self.http.get(detail_url).text
                detail_imgs = extract_img_urls(detail_html)
            except Exception:
                pass

        return Item(
            item_id=raw.item_id,
            platform=self.platform,
            source_url=self.source_url(raw.item_id),
            title=title,
            seller=Seller(shop_name=str(dig(model, "companyName", default="") or "")),
            category=Category(
                cid=str(dig(model, "categoryId", default="") or ""),
                path=[str(x) for x in (dig(model, "categoryPath", default=[]) or [])
                      if isinstance(x, str)],
            ),
            price=PriceInfo(min=min(prices) if prices else None,
                            max=max(prices) if prices else None),
            main_images=[Image(url=u, role="main") for u in imgs],
            detail_images=[Image(url=u, role="detail") for u in detail_imgs],
            detail_html=detail_html or None,
            sale_props=sale_props,
            skus=skus,
            params=params,
            stock_total=sum(s.quantity or 0 for s in skus) or None,
            raw_ref=f"raw/alibaba/{raw.item_id}.json",
            extra={
                "endpoint": raw.endpoint,
                "collect_error": raw.error,
                "blocked": bool(d.get("_blocked")),
                "session": self.status.as_dict(),
                "capabilities": {
                    "title": bool(title),
                    "main_images": len(imgs),
                    "detail_images": len(detail_imgs),
                    "sale_props": len(sale_props),
                    "skus": len(skus),
                    "params": len(params),
                    "price": "ok" if prices else "missing",
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
