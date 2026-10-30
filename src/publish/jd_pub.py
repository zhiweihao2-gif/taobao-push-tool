"""京东发布器：开放平台 jingdong.ware.write.add 通道。

与淘宝发布器同构：build_payload 组装 → 校验 → submit。
图片需先走 jingdong.image.write.upload 拿 URL，再填入 images。
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from ..core.config import get_settings
from ..core.models import Item, PublishResult
from .base import BasePublisher

GATEWAY = "https://api.jd.com/routerjson"


class JDPublisher(BasePublisher):
    target = "jd"

    def __init__(self, shop: str = "default", mode: str = "draft"):
        super().__init__(shop=shop, mode=mode)
        s = get_settings()
        self.app_key = s.get("jd.app_key", "")
        self.app_secret = s.get("jd.app_secret", "")
        self.access_token = s.get(f"jd.shops.{shop}.access_token", "")

    # ------------------------------------------------------------ 载荷
    def build_payload(self, item: Item, target_cid: str | None = None) -> dict:
        cid = target_cid or self.resolve_category(item)
        title = self.sanitizer.clean_title(item.title)

        prop_order: list[dict] = []
        props: list[dict] = []
        for sp in item.sale_props:
            spec = self.prop_mapper.map_sale_prop(item.platform, self.target, sp.pid)
            if not spec:
                continue
            prop_order.append({"src_pid": sp.pid, "tgt_pid": spec["target_pid"],
                               "value_map": spec.get("value_map", {})})
            values = []
            for v in sp.values:
                mapped = self.prop_mapper.map_value(spec.get("value_map", {}), v.vid, v.name)
                if mapped:
                    values.append({"valueId": mapped["vid"], "valueName": mapped["name"]})
            if values:
                props.append({
                    "attrId": spec["target_pid"],
                    "attrName": spec.get("target_name", sp.name),
                    "attrValues": values,
                })

        skus = self.sku_builder.rebuild(item, prop_order, stock_mode="fixed")
        images = [img.hosted_url or img.url for img in item.main_images[:5]]

        return {
            "categoryId": cid,
            "wareName": title,
            "jdPrice": str(min((s["price"] for s in skus), default=0)),
            "marketPrice": str(item.price.origin or 0),
            "stockNum": str(sum(s["quantity"] for s in skus) or 999),
            "images": images,
            "desc": self.sanitizer.clean_html(item.detail_html or ""),
            "props": props,
            "skus": [{
                "skuId": s["outer_id"],
                "jdPrice": str(s["price"]),
                "stockNum": s["quantity"],
                "attributes": s["props"],
            } for s in skus],
            "wareStatus": 2 if self.mode == "direct" else 1,
        }

    # ------------------------------------------------------------ 提交
    def submit(self, payload: dict) -> PublishResult:
        if not (self.app_key and self.app_secret and self.access_token):
            return PublishResult(
                ok=False, target=self.target, mode=self.mode,
                error_code="NO_CREDENTIALS",
                error_msg="缺少京东开放平台凭据（app_key/app_secret/access_token）",
                raw_response={"payload_preview": {k: str(v)[:120] for k, v in payload.items()}},
            )
        params = {
            "method": "jingdong.ware.write.add",
            "app_key": self.app_key,
            "access_token": self.access_token,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
            "360buy_param_json": _dumps(payload),
        }
        params["sign"] = _sign(params, self.app_secret)
        try:
            body = httpx.post(GATEWAY, data=params, timeout=30).json()
        except Exception as e:
            return PublishResult(ok=False, target=self.target, mode=self.mode,
                                 error_msg=str(e), retryable=True)
        if "error_response" in body:
            err = body["error_response"]
            return PublishResult(ok=False, target=self.target, mode=self.mode,
                                 error_code=str(err.get("code")),
                                 error_msg=err.get("zh_desc") or err.get("en_desc"),
                                 raw_response=body)
        return PublishResult(ok=True, target=self.target, mode=self.mode,
                             target_item_id=str(
                                 body.get("jingdong_ware_write_add_responce", {})
                                 .get("wareId", "")),
                             raw_response=body)


def _dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _sign(params: dict, secret: str) -> str:
    items = sorted((k, v) for k, v in params.items() if k != "sign" and v is not None)
    raw = secret + "".join(f"{k}{v}" for k, v in items) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
