"""T7：发布流水线（双通道 + dry_run / 本地 draft）。

设计目标
--------
1. **物理上禁止联网。** 发布层把「构造载荷」和「提交」彻底分开：

   - ``dry_run``：只构造 + 校验，返回载荷，**不发任何请求**
   - ``draft``：构造 + 校验 + 写本地 SQLite 草稿表，**不发任何请求**
   - ``direct``：M2 不实现，直接返回 ``CHANNEL_DISABLED``，**不发任何请求**

   HTTP 客户端通过构造函数注入（默认 None），即使误调也不会真的联网。
   验收标准 11 要求「注入必失败的网络 mock 后调用次数为 0」，这一层天然满足。

2. **后台 endpoint 不许猜。** 未配置时返回 ``BACKEND_ENDPOINT_UNCONFIGURED``，
   仓库里不出现任何硬编码的发布接口路径（验收标准 12）。

3. **通道与模式正交。**
   ``open_api``（有资质）/ ``backend``（商家后台模拟）是通道，
   ``dry_run`` / ``draft`` / ``direct`` 是模式，可以任意组合。

数据流
------
::

    Item ──类目解析──▶ CategoryMapping
         ──属性映射──▶ PropertyResult
         ──SKU 重建──▶ SkuPlan
         ──载荷构造──▶ payload dict
         ──校验──────▶ errors
         ──模式分发──▶ dry_run 返回 / draft 落库 / direct 拒绝
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

from ..core.config import get_settings
from ..core.models import Item
from ..core.storage import count_drafts, save_draft
from ..normalize.category_match import CategoryResolver
from ..normalize.category_models import (CategoryMapping, MappingError,
                                         MappingStatus, PublishChannel,
                                         PublishMode)
from ..normalize.category_models import PublishResult as PublishOutcome
from ..normalize.category_provider import CategoryTreeService
from ..normalize.mapping_tasks import MappingTaskStore
from ..normalize.property_resolver import (MappingResources, PropertyResult,
                                           resolve_properties)
from ..normalize.sanitizer import TextSanitizer
from ..normalize.sku_builder import PriceEngine, SkuPlanner


# ================================================================ 请求/结果


class PublishRequest:
    def __init__(self, item: Item,
                 channel: str = "open_api",
                 mode: str = "dry_run",
                 target_platform: str = "taobao",
                 target_cid: str | None = None,
                 shop: str = "default"):
        self.item = item
        self.channel = channel
        self.mode = mode
        self.target_platform = target_platform
        self.target_cid = target_cid
        self.shop = shop


class PreparedPublish:
    """构造完成、尚未提交的发布包。"""

    def __init__(self, item: Item, category: CategoryMapping,
                 props: PropertyResult, skus: Any, payload: dict,
                 errors: list[str], blocked_reason: str | None = None):
        self.item = item
        self.category = category
        self.props = props
        self.skus = skus
        self.payload = payload
        self.errors = errors
        self.blocked_reason = blocked_reason

    @property
    def ok(self) -> bool:
        return not self.errors and self.blocked_reason is None

    def summary(self) -> dict[str, Any]:
        return {
            "item_id": self.item.item_id,
            "title": (self.item.title or "")[:40],
            "category": {"status": self.category.status.value,
                         "target_cid": self.category.target_cid},
            "skus": len(getattr(self.skus, "items", None) or []),
            "skipped_skus": len(getattr(self.skus, "skipped", None) or []),
            "props": self.props.props_string if self.props is not None else "",
            "errors": self.errors,
            "blocked": self.blocked_reason,
        }


# ================================================================ 通道协议


class PublishChannelAdapter(Protocol):
    """真正把载荷提交到远端的东西。M2 不提供可用实现。"""

    name: str

    def configured(self) -> bool: ...

    def submit(self, payload: dict, shop: str) -> PublishOutcome: ...


class UnconfiguredChannel:
    """占位通道：永远返回未配置。**不猜 endpoint，不联网。**"""

    def __init__(self, name: str, reason: str):
        self.name = name
        self.reason = reason

    def configured(self) -> bool:
        return False

    def submit(self, payload: dict, shop: str) -> PublishOutcome:
        return PublishOutcome(
            ok=False, channel=self.name, mode="direct",
            error_code=MappingError.BACKEND_ENDPOINT_UNCONFIGURED.value,
            error_msg=self.reason)


# ================================================================ 流水线


class PublishPipeline:
    def __init__(self, tree_service: CategoryTreeService | None = None,
                 task_store: MappingTaskStore | None = None,
                 resources: MappingResources | None = None,
                 price_engine: PriceEngine | None = None,
                 sanitizer: TextSanitizer | None = None,
                 draft_saver: Callable[..., str] | None = None,
                 draft_counter: Callable[[], int] | None = None,
                 stock_mode: str = "fixed"):
        self.tree_service = tree_service or CategoryTreeService.from_settings()
        self.task_store = task_store
        self.resources = resources or MappingResources()
        self.price_engine = price_engine or PriceEngine()
        self.sanitizer = sanitizer or TextSanitizer()
        # fixed = 统一用 publish.default_stock；copy = 尊重源库存（0 就是 0）
        self.stock_mode = stock_mode
        # 允许注入，测试里替换成假实现，保证不碰真实 SQLite
        self._save_draft = draft_saver or save_draft
        self._count_drafts = draft_counter or count_drafts
        # 最近一次 prepare 产生的待确认任务 id（供 publish 回传）
        self._last_task_id: str | None = None

    # ------------------------------------------------------------ 准备
    def prepare(self, req: PublishRequest) -> PreparedPublish:
        item = req.item

        # 1. 类目
        tree = self.tree_service.load_or_none(req.target_platform)
        if tree is None:
            return PreparedPublish(
                item, CategoryMapping(status=MappingStatus.ERROR,
                                      error=MappingError.CATEGORY_TREE_UNAVAILABLE),
                None, None, {}, ["拿不到目标类目树"], )

        resolver = CategoryResolver(tree, store=self.task_store)
        if req.target_cid:
            mapping = CategoryMapping(status=MappingStatus.OK,
                                      target_cid=req.target_cid,
                                      target_path=[],
                                      source="confirmed")
            node = tree.by_cid(req.target_cid)
            if node is not None:
                mapping.target_path = list(node.path)
        else:
            # 复审 major：必须把商品的完整信号传给 resolver，
            # 只传 fingerprint 会导致候选永远为空（fingerprint 只是查找键）。
            mapping = resolver.resolve(item)
            if mapping.status is MappingStatus.PENDING and self.task_store:
                task = self.task_store.submit_pending(
                    fingerprint=mapping.fingerprint or "",
                    item_id=item.item_id,
                    platform=item.platform,
                    target_platform=req.target_platform,
                    candidates=mapping.candidates,
                    detail=mapping.detail or "",
                )
                self._last_task_id = task.task_id
            else:
                self._last_task_id = None

        # 类目未定 → 不构造载荷，直接挂起
        if not mapping.ok:
            return PreparedPublish(
                item, mapping, None, None, {},
                [f"类目未确定：{mapping.status.value}"
                 + (f" ({mapping.error.value})" if mapping.error else "")],
                blocked_reason="CATEGORY_PENDING")

        node = tree.by_cid(mapping.target_cid or "")
        if node is None:
            return PreparedPublish(
                item, mapping, None, None, {},
                [f"目标类目 {mapping.target_cid} 不在类目树中"],
                blocked_reason="CATEGORY_NOT_IN_TREE")

        # 2. 属性 + 品牌 + 必填
        props = resolve_properties(item, node, self.resources,
                                   src_platform=item.platform,
                                   target_platform=req.target_platform)

        # 3. SKU
        planner = SkuPlanner(node, price_engine=self.price_engine,
                             stock_mode=self.stock_mode)
        skus = planner.rebuild(getattr(item, "skus", None) or [],
                               props.sale_props)

        # 4. 载荷
        payload = self._build_payload(item, node, mapping, props, skus, req)

        # 5. 校验
        errors: list[str] = list(props.pending_items())
        # 复审 major：SKU 部分失败 / 组合冲突时 status 是 PENDING 且 error 为 None，
        # 只看 error 会让失败的 SKU 被静默丢掉。任何非 OK 都要阻断。
        if skus.error is not None:
            errors.append(f"SKU: {skus.error.value} {skus.detail or ''}".strip())
        elif skus.status is not MappingStatus.OK:
            detail = "; ".join(
                f"{s.get('src_sku_id')}: {s.get('reason')} ({s.get('detail')})"
                for s in (skus.skipped or []))
            errors.append(f"SKU: 有 {len(skus.skipped or [])} 个未成功重建 → {detail}")
        if not payload.get("images"):
            errors.append("主图为空")
        if not payload.get("title"):
            errors.append("标题为空")

        return PreparedPublish(item, mapping, props, skus, payload, errors)

    # ------------------------------------------------------------ 发布
    def publish(self, req: PublishRequest) -> PublishOutcome:
        prepared = self.prepare(req)

        if prepared.blocked_reason == "CATEGORY_PENDING":
            return PublishOutcome(
                ok=False, channel=req.channel, mode=req.mode,
                error_code=MappingError.CATEGORY_NOT_MATCHED.value,
                error_msg="; ".join(prepared.errors),
                payload=prepared.payload,
                raw_response={"summary": prepared.summary(),
                              "task_id": self._last_task_id,
                              "candidates": [c.model_dump() for c in
                                             prepared.category.candidates]})

        if not prepared.ok:
            return PublishOutcome(
                ok=False, channel=req.channel, mode=req.mode,
                error_code=MappingError.VALIDATION_FAILED.value,
                error_msg="; ".join(prepared.errors),
                payload=prepared.payload,
                raw_response={"summary": prepared.summary()})

        # dry_run：到此为止，绝不提交
        if req.mode == PublishMode.DRY_RUN.value or req.mode == "dry_run":
            return PublishOutcome(
                ok=True, channel=req.channel, mode="dry_run",
                payload=prepared.payload,
                raw_response={"summary": prepared.summary()})

        # draft：写本地草稿，绝不提交
        if req.mode == PublishMode.DRAFT.value or req.mode == "draft":
            # 复审 major：backend 通道未配置 endpoint 时必须明确标记，
            # 不能因为"本地草稿写成功了"就假装通道可用。
            if req.channel == "backend" and not self._backend_configured():
                return PublishOutcome(
                    ok=False, channel=req.channel, mode="draft",
                    error_code=MappingError.BACKEND_ENDPOINT_UNCONFIGURED.value,
                    error_msg=("后台发布 endpoint 未配置。仓库里不写入猜测的接口路径，"
                               "请先在 config 里填经过验证的 endpoint。"),
                    payload=prepared.payload,
                    raw_response={"summary": prepared.summary()})

            try:
                draft_id = self._save_draft(
                    item_id=prepared.item.item_id,
                    target=req.target_platform,
                    channel=req.channel,
                    target_cid=prepared.category.target_cid,
                    payload=prepared.payload,
                )
            except Exception as e:
                return PublishOutcome(
                    ok=False, channel=req.channel, mode="draft",
                    error_code="DRAFT_WRITE_FAILED",
                    error_msg=f"草稿写入失败: {type(e).__name__}: {e}",
                    payload=prepared.payload,
                    raw_response={"summary": prepared.summary()})
            return PublishOutcome(
                ok=True, channel=req.channel, mode="draft",
                draft_id=draft_id,
                payload=prepared.payload,
                raw_response={"summary": prepared.summary()})

        # direct：M2 不实现，明确拒绝，不发请求
        return PublishOutcome(
            ok=False, channel=req.channel, mode=str(req.mode),
            error_code=MappingError.CHANNEL_DISABLED.value,
            error_msg="direct 模式在 M2 未实现：发布接口尚未验证，"
                      "仓库里不写入猜测的 endpoint。请用 dry_run 或 draft。",
            payload=prepared.payload,
            raw_response={"summary": prepared.summary()})

    # ------------------------------------------------------------ 载荷
    def _build_payload(self, item: Item, node: Any, mapping: CategoryMapping,
                       props: PropertyResult, skus: Any,
                       req: PublishRequest) -> dict[str, Any]:
        title = self.sanitizer.clean_title(item.title)
        images = [img.hosted_url or img.local_path or img.url
                  for img in (item.main_images or [])]
        sku_items = list(getattr(skus, "items", []) or [])

        return {
            "target": req.target_platform,
            "channel": req.channel,
            "cid": mapping.target_cid,
            "category_path": mapping.target_path,
            "title": title,
            "props": props.props_string,
            "price": str(min((s.price for s in sku_items), default="")),
            "num": sum(s.quantity for s in sku_items),
            "images": images,
            "skus": [
                {"props": s.props, "price": s.price, "quantity": s.quantity,
                 "outer_id": s.outer_id}
                for s in sku_items
            ],
            "sku_properties": ",".join(s.props for s in sku_items),
            "sku_prices": ",".join(str(s.price) for s in sku_items),
            "sku_quantities": ",".join(str(s.quantity) for s in sku_items),
            "sku_outer_ids": ",".join(str(s.outer_id or "") for s in sku_items),
            "approve_status": "onsale" if str(req.mode) == "direct" else "instock",
        }

    # ------------------------------------------------------------ 工具
    def _backend_configured(self) -> bool:
        """后台通道是否配置了**经过验证的** endpoint。

        仓库里不写死任何发布路径；只有用户在 config 里显式填了才认。
        """
        s = get_settings()
        endpoint = s.get("taobao.publish_endpoint", "") or ""
        return bool(str(endpoint).strip())

    def _fingerprint(self, item: Item, target_platform: str) -> str:
        from ..normalize.category_models import build_fingerprint
        params = [{"name": p.name, "value": p.value}
                  for p in (getattr(item, "params", None) or [])]
        names = [p.name for p in (getattr(item, "sale_props", None) or [])]
        return build_fingerprint(getattr(item, "title", None), params, names,
                                 getattr(item, "platform", "") or "")
