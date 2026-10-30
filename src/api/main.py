"""REST 服务：采集 / 查询 / 映射候选 / 发布。"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..collect.alibaba import Alibaba1688Collector
from ..collect.jd import JDCollector
from ..collect.taobao import TaobaoCollector
from ..core.models import Item
from ..core.storage import (enqueue, list_items, load_item, log_publish,
                            save_item, save_raw)
from ..media.downloader import MediaPipeline
from ..normalize.mapper import CategoryMapper, PriceEngine
from ..publish.taobao_pub import TaobaoPublisher

app = FastAPI(title="一键铺货 API", version="0.1.0")

COLLECTORS = {
    "taobao": TaobaoCollector,
    "tmall": TaobaoCollector,
    "alibaba": Alibaba1688Collector,
    "jd": JDCollector,
}


class CollectReq(BaseModel):
    platform: Literal["taobao", "tmall", "alibaba", "jd"]
    item_id: str
    download_media: bool = True
    with_detail: bool = False


class PublishReq(BaseModel):
    item_id: str
    target: Literal["taobao", "jd"] = "taobao"
    shop: str = "default"
    mode: Literal["draft", "direct", "dry_run"] = "draft"
    target_cid: str | None = None


class MapReq(BaseModel):
    src_platform: str
    target_platform: str
    src_cid: str
    target_cid: str
    target_path: str = ""


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/collect")
def collect(req: CollectReq) -> dict:
    cls = COLLECTORS.get(req.platform)
    if not cls:
        raise HTTPException(400, f"unsupported platform: {req.platform}")
    collector = cls()
    try:
        raw = collector.fetch(req.item_id)
        save_raw(raw)
        item = collector.to_item(raw)
        if req.download_media:
            MediaPipeline(item).run(with_detail=req.with_detail)
        path = save_item(item)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e
    return {"ok": True, "item_id": item.item_id, "json": str(path),
            "summary": item.summary()}


@app.get("/items")
def items(limit: int = 50) -> dict:
    return {"items": list_items(limit)}


@app.get("/items/{item_id}")
def item_detail(item_id: str) -> Item:
    item = load_item(item_id)
    if not item:
        raise HTTPException(404, "item not found")
    return item


@app.post("/map/candidates")
def map_candidates(req: MapReq) -> dict:
    mapper = CategoryMapper()
    item = load_item(req.src_cid)
    if not item:
        raise HTTPException(404, "source item not found (src_cid 传已采集的 item_id)")
    tree_path = CategoryMapper().path.parent / f"category_tree_{req.target_platform}.json"
    tree = []
    if tree_path.exists():
        import json
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
    return {"candidates": mapper.candidates(item.category.path, tree)}


@app.post("/map/confirm")
def map_confirm(req: MapReq) -> dict:
    CategoryMapper().remember(req.src_platform, req.target_platform, req.src_cid,
                              req.target_cid, req.target_path)
    return {"ok": True}


@app.post("/publish")
def publish(req: PublishReq) -> dict:
    item = load_item(req.item_id)
    if not item:
        raise HTTPException(404, "item not found")
    if req.target == "taobao":
        publisher = TaobaoPublisher(shop=req.shop, mode=req.mode)
    else:
        raise HTTPException(400, "jd publisher 见 src/publish/jd_pub.py（按同样模式实现）")

    payload = publisher.build_payload(item, req.target_cid)
    result = publisher.publish(item, req.target_cid)
    log_publish(item.item_id, req.target, req.shop, req.mode, result, request=payload)
    return {"result": result.model_dump(), "payload_preview": {
        k: (str(v)[:200]) for k, v in payload.items()}}


@app.post("/queue/collect")
def queue_collect(req: CollectReq) -> dict:
    tid = enqueue(req.platform, req.item_id, kind="collect")
    return {"ok": True, "task_id": tid}


@app.get("/price/preview")
def price_preview(price: float) -> dict:
    engine = PriceEngine()
    return {"src": price, "target": engine.apply(price), "cfg": engine.cfg}
