"""类目树 Provider：把「目标平台类目树从哪来」抽象成可插拔的来源链。

为什么需要这一层
----------------
类目树有三个来源，各有前提：

| 来源 | 前提 | 现状 |
|---|---|---|
| `local_json` | 无 | ✅ 随时可用，M2 的主力 |
| `open_api` | 淘宝开放平台资质 + 类目授权 | ❌ 个人店拿不到 |
| `page` | 登录态 + 页面结构稳定 | ⚠️ 未验证 |

所以设计成**来源链**：按配置顺序逐个尝试，第一个成功的就用。本地 JSON 排第一，
保证没有资质、没有登录态时也能开发、测试、跑通整条链路。

验收契约（见 .pair/plan.json 第 1 条）
---------------------------------------
- 只用本地 JSON 时，返回合法叶类目及其属性
- 本地文件不存在 → 按配置顺序尝试下一个来源
- 本地 JSON 结构非法 → 抛 ``CategoryTreeError(INVALID_CATEGORY_TREE)``
- 所有来源都失败 → 抛 ``CategoryTreeError(CATEGORY_TREE_UNAVAILABLE)``
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..core.config import get_settings
from .category_models import (CategoryNode, CategoryTree, MappingError,
                              PropMeta, PropValueMeta)


class CategoryTreeError(RuntimeError):
    """类目树加载失败。``code`` 是 :class:`MappingError` 里的枚举值。"""

    def __init__(self, code: MappingError, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


# ================================================================ 基类


class CategoryTreeProvider(ABC):
    """类目树来源。"""

    kind: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """配置是否满足（不代表一定能取到数据）。"""

    @abstractmethod
    def load(self, platform: str) -> CategoryTree:
        """取类目树。失败时抛 :class:`CategoryTreeError`。"""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} kind={self.kind}>"


# ================================================================ 本地 JSON


class LocalJsonProvider(CategoryTreeProvider):
    """从本地 JSON 文件读类目树。**M2 的主力来源，无需任何资质。**

    文件结构见 ``data/mapping/category_tree.example.json``。
    """

    kind = "local_json"

    def __init__(self, path: str | Path | None = None):
        s = get_settings()
        self.path = Path(path) if path else s.mapping_dir / "category_tree.taobao.json"

    def available(self) -> bool:
        return self.path.exists()

    def load(self, platform: str) -> CategoryTree:
        if not self.path.exists():
            raise CategoryTreeError(
                MappingError.CATEGORY_TREE_UNAVAILABLE,
                f"本地类目树不存在: {self.path}")

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise CategoryTreeError(
                MappingError.INVALID_CATEGORY_TREE,
                f"JSON 解析失败 {self.path.name}: {e}") from e
        except OSError as e:
            raise CategoryTreeError(
                MappingError.CATEGORY_TREE_UNAVAILABLE, str(e)) from e

        if not isinstance(raw, dict):
            raise CategoryTreeError(
                MappingError.INVALID_CATEGORY_TREE,
                f"顶层必须是对象，实际是 {type(raw).__name__}")

        nodes_raw = raw.get("nodes")
        if not isinstance(nodes_raw, list) or not nodes_raw:
            raise CategoryTreeError(
                MappingError.INVALID_CATEGORY_TREE, "nodes 缺失或为空数组")

        nodes: list[CategoryNode] = []
        for idx, item in enumerate(nodes_raw):
            if not isinstance(item, dict):
                raise CategoryTreeError(
                    MappingError.INVALID_CATEGORY_TREE,
                    f"nodes[{idx}] 不是对象")
            if not item.get("cid"):
                raise CategoryTreeError(
                    MappingError.INVALID_CATEGORY_TREE,
                    f"nodes[{idx}] 缺少 cid")
            if not item.get("name"):
                raise CategoryTreeError(
                    MappingError.INVALID_CATEGORY_TREE,
                    f"nodes[{idx}] 缺少 name")
            try:
                nodes.append(CategoryNode.model_validate(item))
            except ValidationError as e:
                raise CategoryTreeError(
                    MappingError.INVALID_CATEGORY_TREE,
                    f"nodes[{idx}] 结构非法: {e.errors()[0].get('msg')}") from e

        tree = CategoryTree(
            platform=str(raw.get("platform") or platform),
            source=self.kind,
            nodes=nodes,
            fetched_at=raw.get("fetched_at"),
        )
        return tree


# ================================================================ 开放平台


class OpenApiProvider(CategoryTreeProvider):
    """淘宝开放平台 ``taobao.itemcats.get``。

    **没有资质时不可用**——个人店拿不到类目授权。所以这里只做接口占位：
    配置缺失时 ``available()`` 返回 False，来源链自动跳过；
    被强制调用时抛 CATEGORY_TREE_UNAVAILABLE，绝不返回伪造的类目树。
    """

    kind = "open_api"

    def __init__(self, app_key: str | None = None,
                 app_secret: str | None = None,
                 session_key: str | None = None):
        s = get_settings()
        self.app_key = app_key or s.get("taobao.app_key", "")
        self.app_secret = app_secret or s.get("taobao.app_secret", "")
        self.session_key = session_key or s.get("taobao.session_key", "")

    def available(self) -> bool:
        return bool(self.app_key and self.app_secret and self.session_key)

    def load(self, platform: str) -> CategoryTree:
        if not self.available():
            raise CategoryTreeError(
                MappingError.CATEGORY_TREE_UNAVAILABLE,
                "开放平台凭据缺失（需要 app_key/app_secret/session_key 与类目授权）")
        # 真实实现需要逐层拉取 cid 树并缓存；M2 不猜接口参数，留待资质到位后接。
        raise CategoryTreeError(
            MappingError.CATEGORY_TREE_UNAVAILABLE,
            "开放平台类目树通道尚未实现（需资质到位后按官方文档接入）")


# ================================================================ 页面抓取


class PageProvider(CategoryTreeProvider):
    """从卖家后台/前台页面抓类目树。

    未验证——淘宝页面是客户端渲染且结构随版本变化，M2 不实现。
    保留接口位，避免以后改来源链结构。
    """

    kind = "page"

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)

    def available(self) -> bool:
        return self.enabled

    def load(self, platform: str) -> CategoryTree:
        raise CategoryTreeError(
            MappingError.CATEGORY_TREE_UNAVAILABLE,
            "页面类目树通道尚未实现（需要登录态与稳定页面结构）")


# ================================================================ 来源链


_PROVIDER_FACTORY = {
    "local_json": LocalJsonProvider,
    "open_api": OpenApiProvider,
    "page": PageProvider,
}


class CategoryTreeService:
    """按配置顺序尝试多个来源，第一个成功的就用。

    配置（``config/settings.yaml``）::

        category_tree:
          sources:
            - type: local_json
              path: data/mapping/category_tree.taobao.json
            - type: open_api
            - type: page
              enabled: false
    """

    def __init__(self, providers: list[CategoryTreeProvider] | None = None):
        self.providers = providers or []

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_settings(cls) -> "CategoryTreeService":
        s = get_settings()
        raw = s.get("category_tree.sources", None)
        if raw is None:
            # 默认只用本地 JSON，保证开箱可用
            return cls([LocalJsonProvider()])

        providers: list[CategoryTreeProvider] = []
        for item in raw:
            if isinstance(item, str):
                item = {"type": item}
            kind = item.get("type")
            factory = _PROVIDER_FACTORY.get(kind)
            if not factory:
                continue
            if kind == "local_json":
                providers.append(LocalJsonProvider(item.get("path")))
            elif kind == "open_api":
                providers.append(OpenApiProvider())
            elif kind == "page":
                providers.append(PageProvider(item.get("enabled", False)))
        return cls(providers)

    # ------------------------------------------------------------ 取树
    def load(self, platform: str = "taobao") -> CategoryTree:
        errors: list[str] = []
        for p in self.providers:
            if not p.available():
                errors.append(f"{p.kind}: 未配置/不可用")
                continue
            try:
                return p.load(platform)
            except CategoryTreeError as e:
                errors.append(f"{p.kind}: {e.code.value}")
                # 非法 JSON 是配置错误，不该被后续来源掩盖
                if e.code is MappingError.INVALID_CATEGORY_TREE:
                    raise
            except Exception as e:  # 未知异常不吞，但要带上来源信息
                errors.append(f"{p.kind}: {type(e).__name__}")
        raise CategoryTreeError(
            MappingError.CATEGORY_TREE_UNAVAILABLE,
            "所有类目树来源都失败 → " + "; ".join(errors) if errors else "没有配置任何来源")

    def load_or_none(self, platform: str = "taobao") -> CategoryTree | None:
        try:
            return self.load(platform)
        except CategoryTreeError:
            return None


# ================================================================ 工具


def dump_tree(tree: CategoryTree, path: str | Path) -> Path:
    """把类目树写成 LocalJsonProvider 能读的格式（便于导出/备份）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "platform": tree.platform,
        "source": tree.source,
        "fetched_at": tree.fetched_at,
        "nodes": [n.model_dump(mode="json") for n in tree.nodes],
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
