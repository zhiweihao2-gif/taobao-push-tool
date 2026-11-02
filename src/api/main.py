"""REST 服务：采集 / 查询 / 映射候选 / 发布。"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..collect.alibaba import Alibaba1688Collector
from ..collect.jd import JDCollector
from ..collect.resolve import ResolveError, resolve_item_id
from ..collect.taobao import TaobaoCollector
from ..core.models import Item
from ..core.storage import (enqueue, list_items, load_item, log_publish,
                            save_item, save_raw)
from ..media.downloader import MediaPipeline
from ..normalize.mapper import PriceEngine

app = FastAPI(title="一键铺货 API", version="0.1.0")

# M2 映射 / 发布接口（独立 router，不改动本文件已有路由）
from .mapping_api import router as mapping_router  # noqa: E402

app.include_router(mapping_router)

# M3 批量 Web 界面（纯静态单页，同源调用上面的接口）
from pathlib import Path as _Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEB_DIR = _Path(__file__).resolve().parents[2] / "web"
if _WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")

    @app.get("/", include_in_schema=False)
    def _index() -> FileResponse:
        return FileResponse(str(_WEB_DIR / "index.html"))

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

    # 先把「链接 / 短链 / 推广链 / 纯数字」统一解析成商品 ID。
    # 之前这里直接用 req.item_id，用户粘完整 URL 时会拿 URL 当 ID，
    # 最后崩在「文件名语法不正确」上。
    try:
        item_id = resolve_item_id(req.item_id, req.platform)
    except ResolveError as e:
        raise HTTPException(400, str(e)) from e

    collector = cls()
    try:
        raw = collector.fetch(item_id)
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





@app.post("/queue/collect")
def queue_collect(req: CollectReq) -> dict:
    tid = enqueue(req.platform, req.item_id, kind="collect")
    return {"ok": True, "task_id": tid}


@app.get("/price/preview")
def price_preview(price: float) -> dict:
    engine = PriceEngine()
    return {"src": price, "target": engine.apply(price), "cfg": engine.cfg}
