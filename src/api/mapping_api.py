"""T8：映射与发布相关的 HTTP 接口。

挂在 ``/map`` 和 ``/publish`` 下，与原有采集接口（``/collect``、``/items``）并存。
用 ``include_router`` 挂进 ``src/api/main.py``，不改动原文件的路由。

接口一览
--------
| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/map/category` | 对已采集商品做类目匹配，返回候选 |
| GET  | `/map/tasks` | 待确认任务列表 |
| POST | `/map/confirm` | 确认某个任务的类目 |
| POST | `/map/reject` | 拒绝某个任务 |
| POST | `/publish/prepare` | dry_run：只构造载荷 |
| POST | `/publish/draft` | draft：写本地草稿 |
| GET  | `/drafts` | 草稿列表 |

**所有接口都不会发起外部网络请求**（发布层零网络，见 T7）。
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.storage import get_draft, list_drafts, load_item
from ..normalize.category_match import CategoryResolver
from ..normalize.category_provider import CategoryTreeError, CategoryTreeService
from ..normalize.mapping_tasks import MappingTaskStore
from ..publish.pipeline import PublishPipeline, PublishRequest

router = APIRouter(tags=["mapping"])

_tree_service: CategoryTreeService | None = None
_task_store: MappingTaskStore | None = None
_pipeline: PublishPipeline | None = None


def get_tree_service() -> CategoryTreeService:
    global _tree_service
    if _tree_service is None:
        _tree_service = CategoryTreeService.from_settings()
    return _tree_service


def get_task_store() -> MappingTaskStore:
    global _task_store
    if _task_store is None:
        _task_store = MappingTaskStore()
    return _task_store


def get_pipeline() -> PublishPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = PublishPipeline(tree_service=get_tree_service(),
                                    task_store=get_task_store())
    return _pipeline


# ================================================================ 请求模型


class CategoryMapReq(BaseModel):
    item_id: str
    target_platform: str = "taobao"


class ConfirmReq(BaseModel):
    task_id: str
    target_cid: str
    target_path: list[str] = Field(default_factory=list)


class RejectReq(BaseModel):
    task_id: str
    reason: str = ""


class PublishReq(BaseModel):
    item_id: str
    target_platform: str = "taobao"
    channel: Literal["open_api", "backend"] = "open_api"
    mode: Literal["dry_run", "draft", "direct"] = "dry_run"
    target_cid: str | None = None
    shop: str = "default"


# ================================================================ 类目


@router.post("/map/category")
def map_category(req: CategoryMapReq) -> dict[str, Any]:
    item = load_item(req.item_id)
    if item is None:
        raise HTTPException(404, f"商品未采集: {req.item_id}")

    try:
        tree = get_tree_service().load(req.target_platform)
    except CategoryTreeError as e:
        return {"ok": False, "error_code": e.code.value, "error_msg": e.detail}

    resolver = CategoryResolver(tree, store=get_task_store())
    mapping = resolver.resolve(item)
    return {
        "ok": mapping.ok,
        "status": mapping.status.value,
        "target_cid": mapping.target_cid,
        "target_path": mapping.target_path,
        "fingerprint": mapping.fingerprint,
        "confidence": mapping.confidence,
        "source": mapping.source,
        "error_code": mapping.error.value if mapping.error else None,
        "detail": mapping.detail,
        "candidates": [c.model_dump() for c in mapping.candidates],
    }


@router.get("/map/tasks")
def map_tasks(status: str = "pending", limit: int = 50) -> dict[str, Any]:
    store = get_task_store()
    tasks = store.list_by_status(status, limit=limit)
    return {"status": status, "count": len(tasks),
            "tasks": [t.as_dict() for t in tasks]}


@router.post("/map/confirm")
def map_confirm(req: ConfirmReq) -> dict[str, Any]:
    ok, msg = get_task_store().confirm(req.task_id, req.target_cid, req.target_path)
    return {"ok": ok, "message": msg, "task_id": req.task_id,
            "target_cid": req.target_cid}


@router.post("/map/reject")
def map_reject(req: RejectReq) -> dict[str, Any]:
    ok = get_task_store().reject(req.task_id, req.reason)
    return {"ok": ok, "task_id": req.task_id}


# ================================================================ 发布


@router.post("/publish/prepare")
def publish_prepare(req: PublishReq) -> dict[str, Any]:
    item = load_item(req.item_id)
    if item is None:
        raise HTTPException(404, f"商品未采集: {req.item_id}")

    prepared = get_pipeline().prepare(PublishRequest(
        item, channel=req.channel, mode=req.mode,
        target_platform=req.target_platform, target_cid=req.target_cid,
        shop=req.shop))
    return {
        "ok": prepared.ok,
        "summary": prepared.summary(),
        "errors": prepared.errors,
        "blocked": prepared.blocked_reason,
        "payload": prepared.payload,
    }


@router.post("/publish/draft")
def publish_draft(req: PublishReq) -> dict[str, Any]:
    item = load_item(req.item_id)
    if item is None:
        raise HTTPException(404, f"商品未采集: {req.item_id}")

    req.mode = "draft"
    res = get_pipeline().publish(PublishRequest(
        item, channel=req.channel, mode="draft",
        target_platform=req.target_platform, target_cid=req.target_cid,
        shop=req.shop))
    return res.model_dump()


@router.get("/drafts")
def drafts(limit: int = 50) -> dict[str, Any]:
    items = list_drafts(limit)
    for d in items:
        d.pop("payload", None)     # 列表不返回大字段
    return {"count": len(items), "drafts": items}


@router.get("/drafts/{draft_id}")
def draft_detail(draft_id: str) -> dict[str, Any]:
    d = get_draft(draft_id)
    if d is None:
        raise HTTPException(404, "草稿不存在")
    return d
