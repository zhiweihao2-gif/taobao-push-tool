"""M2 映射层的统一模型与契约。

这个模块只定义数据形状，不含业务逻辑，目的是让 Provider / Matcher / Mapper /
Publisher 各层有共同的类型基础，避免用裸 dict 传递导致字段名漂移。

设计原则
--------
1. **源类目可以为空**。实测淘宝 `pcdetail` 不返回 `categoryId`，所以
   `Item.category.cid` 经常是 `""`。所有模型都不能假设它存在。
2. **映射结果必须带状态**，不允许用 `None` 混同「没找到」和「找错了」。
   状态用 :class:`MappingStatus`，失败原因用 :class:`MappingError`。
3. **fingerprint 是映射复用的键**。源商品没有类目 ID 时，用标题 + 参数 +
   销售属性名算一个稳定指纹，人工确认后按指纹复用。
4. 全部可序列化（pydantic v2），便于落 YAML / SQLite / HTTP。
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ================================================================ 错误码
#
# 集中定义，避免各层各写各的字符串。新增时同步 docs/04-映射与发布.md。


class MappingError(str, Enum):
    """映射/发布过程中的可枚举失败原因。"""

    # --- 类目树 ---
    INVALID_CATEGORY_TREE = "INVALID_CATEGORY_TREE"   # 本地 JSON 结构非法
    CATEGORY_TREE_UNAVAILABLE = "CATEGORY_TREE_UNAVAILABLE"  # 所有来源都拿不到
    CATEGORY_NOT_MATCHED = "CATEGORY_NOT_MATCHED"     # 候选为空或全部低于阈值

    # --- 属性 ---
    PROPERTY_REQUIRED_MISSING = "PROPERTY_REQUIRED_MISSING"
    PROPERTY_AMBIGUOUS = "PROPERTY_AMBIGUOUS"         # 多个候选同分，需人工裁决
    BRAND_UNRESOLVED = "BRAND_UNRESOLVED"             # 品牌没命中（brand_id 必须为 null）

    # --- SKU / 价格 ---
    SKU_LIMIT_EXCEEDED = "SKU_LIMIT_EXCEEDED"
    SKU_DIMENSION_UNMAPPED = "SKU_DIMENSION_UNMAPPED"
    SKU_COMBINATION_CONFLICT = "SKU_COMBINATION_CONFLICT"
    PRICE_INVALID = "PRICE_INVALID"

    # --- 确认闭环 ---
    MAPPING_EXPIRED = "MAPPING_EXPIRED"               # 已确认但目标 cid 失效
    MAPPING_CONFLICT = "MAPPING_CONFLICT"             # 同一 fingerprint 被确认成不同目标
    MAPPING_STORE_WRITE_FAILED = "MAPPING_STORE_WRITE_FAILED"

    # --- 发布 ---
    BACKEND_ENDPOINT_UNCONFIGURED = "BACKEND_ENDPOINT_UNCONFIGURED"
    NO_CREDENTIALS = "NO_CREDENTIALS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CHANNEL_DISABLED = "CHANNEL_DISABLED"


class MappingStatus(str, Enum):
    """映射结果状态。"""

    OK = "ok"                # 已确定，可直接用
    PENDING = "pending"      # 需要人工确认，candidates 里有可选值
    CONFLICT = "conflict"    # 存在冲突的已确认记录
    EXPIRED = "expired"      # 已确认但目标已失效
    ERROR = "error"          # 硬失败，见 error


# ================================================================ 类目树


class PropValueMeta(BaseModel):
    """目标平台某个属性值（vid）的元数据。"""

    model_config = ConfigDict(extra="allow")

    vid: str
    name: str
    aliases: list[str] = Field(default_factory=list,
                               description="同义词，如 纯棉 → 棉")
    image: str | None = None


class PropMeta(BaseModel):
    """目标平台某个类目下的属性（pid）元数据。"""

    model_config = ConfigDict(extra="allow")

    pid: str
    name: str
    required: bool = False
    is_sale: bool = Field(default=False, description="销售属性，参与生成 SKU")
    is_key: bool = Field(default=False, description="关键属性，影响搜索/类目")
    type: Literal["text", "select", "brand", "number"] = "select"
    values: list[PropValueMeta] = Field(default_factory=list)

    def value_by_name(self, name: str) -> PropValueMeta | None:
        for v in self.values:
            if v.name == name or name in v.aliases:
                return v
        return None


class CategoryNode(BaseModel):
    """目标平台的一个类目节点。"""

    model_config = ConfigDict(extra="allow")

    cid: str
    name: str
    path: list[str] = Field(default_factory=list, description="从根到叶的名称链")
    parent_cid: str | None = None
    is_leaf: bool = True
    props: list[PropMeta] = Field(default_factory=list,
                                  description="仅叶类目有完整属性表")

    @property
    def path_text(self) -> str:
        return " > ".join(self.path or [self.name])


class CategoryTree(BaseModel):
    """一棵目标平台类目树。"""

    model_config = ConfigDict(extra="allow")

    platform: str
    source: str = Field(description="来源标记：local_json / open_api / page")
    nodes: list[CategoryNode] = Field(default_factory=list)
    fetched_at: str | None = None

    def leaves(self) -> list[CategoryNode]:
        return [n for n in self.nodes if n.is_leaf]

    def by_cid(self, cid: str) -> CategoryNode | None:
        for n in self.nodes:
            if n.cid == str(cid):
                return n
        return None


# ================================================================ 候选与映射


class CategoryCandidate(BaseModel):
    """类目匹配的一个候选。"""

    model_config = ConfigDict(extra="allow")

    cid: str
    path: list[str] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list,
                               description="打分依据，如 '标题词命中 3 个'")

    @property
    def path_text(self) -> str:
        return " > ".join(self.path)


class CategoryMapping(BaseModel):
    """类目映射的完整结果。"""

    status: MappingStatus
    target_cid: str | None = None
    target_path: list[str] = Field(default_factory=list)
    candidates: list[CategoryCandidate] = Field(default_factory=list)
    fingerprint: str | None = None
    confidence: float = 0.0
    source: Literal["confirmed", "matched", "none"] = "none"
    error: MappingError | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is MappingStatus.OK and bool(self.target_cid)


class ValueMapping(BaseModel):
    """单个属性值的映射结果。"""

    src_vid: str | None = None
    src_name: str
    target_vid: str | None = None
    target_name: str | None = None
    matched_by: Literal["vid", "name", "alias", "fuzzy", "none"] = "none"
    score: float = 0.0


class PropertyMapping(BaseModel):
    """单个属性的映射结果。"""

    src_pid: str
    src_name: str
    target_pid: str | None = None
    target_name: str | None = None
    values: list[ValueMapping] = Field(default_factory=list)
    required: bool = False
    status: MappingStatus = MappingStatus.OK
    error: MappingError | None = None

    @property
    def unmapped_values(self) -> list[ValueMapping]:
        return [v for v in self.values if not v.target_vid]


class BrandMapping(BaseModel):
    """品牌映射结果。**没命中时 brand_id 必须为 None，不许自造。**"""

    src_brand: str | None = None
    brand_id: str | None = None
    brand_name: str | None = None
    matched_by: Literal["exact", "alias", "none"] = "none"
    required: bool = False
    status: MappingStatus = MappingStatus.OK
    error: MappingError | None = None


class SkuPlanItem(BaseModel):
    """重建后的一个 SKU。"""

    model_config = ConfigDict(extra="allow")

    props: str = Field(description="pid:vid;pid:vid 串")
    price: float
    quantity: int
    outer_id: str | None = None
    src_sku_id: str | None = Field(default=None, description="回溯用，不许丢")
    image: str | None = None


class SkuPlan(BaseModel):
    """SKU 重建结果。

    关键契约：**未映射的维度或组合冲突不许静默丢弃**，必须体现在
    ``skipped`` 里并给出原因，让调用方决定是否中断。
    """

    items: list[SkuPlanItem] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(
        default_factory=list,
        description="被跳过的 SKU：[{src_sku_id, reason, detail}]")
    status: MappingStatus = MappingStatus.OK
    error: MappingError | None = None
    detail: str | None = None


# ================================================================ 发布


class PublishChannel(str, Enum):
    OPEN_API = "open_api"
    BACKEND = "backend"


class PublishMode(str, Enum):
    DRY_RUN = "dry_run"     # 只构造载荷 + 校验，不提交
    DRAFT = "draft"         # 存本地草稿
    DIRECT = "direct"       # 真实上架（M2 不实现）


class PublishResult(BaseModel):
    """发布结果（替代 src/core/models.py 里的同名类，字段更全）。"""

    ok: bool
    channel: str
    mode: str
    target_item_id: str | None = None
    draft_id: str | None = None
    error_code: str | None = None
    error_msg: str | None = None
    retryable: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


# ================================================================ 指纹


_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\u4e00-\u9fff]+")


def normalize_text(text: str | None) -> str:
    """归一化文本：去空白、去标点、统一小写。用于指纹和匹配。"""
    if not text:
        return ""
    s = _WS.sub("", str(text)).lower()
    return _NON_WORD.sub("", s)


def build_fingerprint(
    title: str | None,
    params: list[dict[str, str]] | None = None,
    sale_prop_names: list[str] | None = None,
    platform: str = "",
) -> str:
    """为「没有源类目 ID 的商品」构造稳定指纹。

    组成：平台 + 归一化标题 + 排序后的参数(名=值) + 排序后的销售属性名。
    排序保证顺序无关；归一化保证空白/标点差异不影响。

    两个商品只要规范化输入不同，指纹一定不同（验收标准第 3 条）。
    """
    parts: list[str] = [normalize_text(platform), normalize_text(title)]

    pairs = []
    for p in params or []:
        name = normalize_text(p.get("name"))
        value = normalize_text(p.get("value"))
        if name or value:
            pairs.append(f"{name}={value}")
    parts.append("|".join(sorted(pairs)))

    names = sorted({normalize_text(n) for n in (sale_prop_names or []) if n})
    parts.append(",".join(names))

    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
