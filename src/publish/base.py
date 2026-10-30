"""发布器基类：统一 publish(item, mode) -> PublishResult。"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..core.config import get_settings
from ..core.models import Item, PublishResult
from ..normalize.mapper import (CategoryMapper, PriceEngine, PropertyMapper,
                                SkuRebuilder, Validator)
from ..normalize.sanitizer import TextSanitizer


class BasePublisher(ABC):
    target: str = ""

    def __init__(self, shop: str = "default", mode: str = "draft"):
        s = get_settings()
        self.shop = shop
        self.mode = mode or s.get("publish.default_mode", "draft")
        self.cat_mapper = CategoryMapper()
        self.prop_mapper = PropertyMapper()
        self.price_engine = PriceEngine()
        self.sku_builder = SkuRebuilder(price_engine=self.price_engine)
        self.sanitizer = TextSanitizer()
        self.validator = Validator()

    # ------------------------------------------------------------ 载荷构造
    @abstractmethod
    def build_payload(self, item: Item, target_cid: str | None = None) -> dict:
        ...

    @abstractmethod
    def submit(self, payload: dict) -> PublishResult:
        ...

    # ------------------------------------------------------------ 主流程
    def publish(self, item: Item, target_cid: str | None = None) -> PublishResult:
        if self.mode == "dry_run":
            payload = self.build_payload(item, target_cid)
            errors = self.validator.validate(payload)
            return PublishResult(
                ok=not errors, target=self.target, mode=self.mode,
                error_msg="; ".join(errors) or None,
                raw_response={"payload": payload},
            )

        payload = self.build_payload(item, target_cid)
        errors = self.validator.validate(payload)
        if errors:
            return PublishResult(ok=False, target=self.target, mode=self.mode,
                                 error_code="VALIDATION_FAILED",
                                 error_msg="; ".join(errors),
                                 raw_response={"payload": payload})

        time.sleep(1.0 / max(0.05, float(get_settings().get("ratelimit.publish", 0.2))))
        return self.submit(payload)

    # ------------------------------------------------------------ 工具
    def resolve_category(self, item: Item) -> str | None:
        hit = self.cat_mapper.lookup(item.platform, self.target, item.category.cid)
        return hit["target_cid"] if hit else None
