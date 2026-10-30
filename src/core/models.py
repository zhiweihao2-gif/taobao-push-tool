"""跨平台统一的商品数据模型（pydantic v2）。

采集层输出 RawItem（原始响应原样保留），归一化层输出 Item，
映射层基于 Item 生成目标平台载荷。
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

Platform = Literal["taobao", "tmall", "alibaba", "jd"]


class Image(BaseModel):
    url: str
    local_path: str | None = None
    hosted_url: str | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    role: Literal["main", "detail", "sku", "video_cover"] = "main"


class Video(BaseModel):
    """商品主图视频。

    淘宝实测结构（`item.videos[]`）::

        {"url": "https://cloud.video.taobao.com/play/u/<userId>/p/2/e/6/t/1/<videoId>.mp4?appKey=38829",
         "videoId": "1352964901100101",
         "videoThumbnailURL": "https://img.alicdn.com/.../xxx.jpg",
         "spatialVideoDimension": "3:4",
         "type": "3"}
    """
    url: str
    video_id: str | None = None
    cover: str | None = None
    dimension: str | None = None
    duration: float | None = None
    size_bytes: int | None = None
    local_path: str | None = None
    sha256: str | None = None
    role: Literal["main", "detail"] = "main"


class PropValue(BaseModel):
    vid: str
    name: str
    image: str | None = None


class SaleProp(BaseModel):
    pid: str
    name: str
    values: list[PropValue] = Field(default_factory=list)


class Sku(BaseModel):
    sku_id: str
    attrs: dict[str, str] = Field(default_factory=dict)   # pid -> vid
    prop_path: str | None = None
    price: float | None = None
    quantity: int | None = None
    image: str | None = None
    outer_id: str | None = None


class Param(BaseModel):
    name: str
    value: str
    group: str | None = None


class Category(BaseModel):
    cid: str
    path: list[str] = Field(default_factory=list)
    root_cid: str | None = None


class Seller(BaseModel):
    seller_id: str | None = None
    nick: str | None = None
    shop_name: str | None = None


class PriceInfo(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str = "CNY"
    origin: float | None = None


class Item(BaseModel):
    item_id: str
    platform: Platform
    source_url: str | None = None
    collected_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    title: str
    subtitle: str | None = None
    seller: Seller = Field(default_factory=Seller)
    category: Category

    price: PriceInfo = Field(default_factory=PriceInfo)
    main_images: list[Image] = Field(default_factory=list)
    detail_images: list[Image] = Field(default_factory=list)
    videos: list[Video] = Field(default_factory=list)
    detail_html: str | None = None
    video_url: str | None = None      # 兼容旧字段，等价于 videos[0].url

    sale_props: list[SaleProp] = Field(default_factory=list)
    skus: list[Sku] = Field(default_factory=list)
    params: list[Param] = Field(default_factory=list)

    stock_total: int | None = None
    media_dir: str | None = None
    raw_ref: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        prices = [s.price for s in self.skus if s.price]
        lo = self.price.min or (min(prices) if prices else None)
        hi = self.price.max or (max(prices) if prices else None)
        return (
            f"[{self.platform}] {self.item_id} {self.title[:30]}\n"
            f"  类目: {'/'.join(self.category.path) or self.category.cid}\n"
            f"  价格: {lo} ~ {hi}\n"
            f"  主图: {len(self.main_images)}  详情图: {len(self.detail_images)}\n"
            f"  销售属性: {[p.name for p in self.sale_props]}  SKU: {len(self.skus)}\n"
            f"  参数: {len(self.params)} 条"
        )


class RawItem(BaseModel):
    """采集器原始产物：不解析，原样保留，便于解析逻辑迭代后离线重跑。"""
    platform: Platform
    item_id: str
    fetched_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    payload: dict[str, Any] = Field(default_factory=dict)
    endpoint: str | None = None
    error: str | None = None


class PublishResult(BaseModel):
    ok: bool
    target: str
    mode: str
    target_item_id: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    retryable: bool = False
    raw_response: dict[str, Any] = Field(default_factory=dict)
