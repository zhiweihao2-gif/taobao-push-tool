"""T3：无源类目 ID 的候选排序 + 指纹确认键。

背景
----
实测淘宝 `pcdetail` **不返回 categoryId**，所以 `Item.category.cid` 经常是空串。
不能靠「源类目 → 目标类目」的查表，只能用商品自身的可观测特征去目标类目树里找：

- 标题（权重最高，最直接）
- 参数表（品牌/型号/材质这类关键属性命中类目属性表）
- 销售属性名（颜色分类、尺码、端口总数…）

打分权重来自配置 ``category_match.weights``，默认 0.5 / 0.3 / 0.2。

稳定规则
--------
- 同分按 cid 字典序，保证同样输入永远得到同样顺序（验收标准 2 的「稳定规则」）
- 标题、参数、销售属性**全空时返回空列表**，不做无根据的猜测
- 低于 ``min_score`` 的候选不返回

确认键
------
人工确认过的映射按 ``fingerprint`` 存（标题+参数+销售属性名的哈希）。
复用前必须校验目标 cid 在**当前类目树里仍然存在**，否则判定为 EXPIRED，
不许自动命中（验收标准 3）。
"""
from __future__ import annotations

from typing import Any, Protocol

from ..core.config import get_settings
from .category_models import (CategoryCandidate, CategoryMapping, CategoryNode,
                              CategoryTree, MappingError, MappingStatus,
                              build_fingerprint, normalize_text)


# ================================================================ 分词工具


def ngrams(text: str | None, n: int = 2) -> set[str]:
    """字符级 n-gram。中文没有空格，用 2-gram 比空格切分靠谱。"""
    t = normalize_text(text)
    if not t:
        return set()
    if len(t) < n:
        return {t}
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ================================================================ 打分


class MatchWeights:
    def __init__(self, title: float = 0.5, params: float = 0.3,
                 sale_props: float = 0.2):
        total = title + params + sale_props
        if total <= 0:
            raise ValueError("权重合计必须 > 0")
        # 归一化，允许配置写成 5/3/2 这种
        self.title = title / total
        self.params = params / total
        self.sale_props = sale_props / total

    @classmethod
    def from_settings(cls) -> "MatchWeights":
        s = get_settings()
        w = s.get("category_match.weights", None) or {}
        return cls(float(w.get("title", 0.5)),
                   float(w.get("params", 0.3)),
                   float(w.get("sale_props", 0.2)))

    def as_dict(self) -> dict[str, float]:
        return {"title": round(self.title, 4),
                "params": round(self.params, 4),
                "sale_props": round(self.sale_props, 4)}


def title_score(title: str | None, node: CategoryNode) -> float:
    """标题对类目名的**覆盖率**（不是 Jaccard）。

    为什么不用 Jaccard：淘宝标题动辄 30+ 字，类目名只有 2-4 字，
    Jaccard 的分母被长标题撑大，导致所有类目分数一起被稀释到 0.05 量级，
    区分度全丢。改成「类目名的 n-gram 有多少被标题覆盖」，
    短类目名被长标题命中时得分就高，符合直觉。
    """
    t = ngrams(title)
    target = ngrams(node.name + "".join(node.path))
    if not t or not target:
        return 0.0
    return len(t & target) / len(target)


def param_score(params: list[dict[str, str]] | None, node: CategoryNode) -> float:
    """参数表命中类目属性名/属性值的比例。"""
    if not params:
        return 0.0
    prop_names = {normalize_text(p.name) for p in node.props}
    prop_values = {normalize_text(v.name) for p in node.props for v in p.values}

    hits = 0
    for p in params:
        name = normalize_text(p.get("name"))
        value = normalize_text(p.get("value"))
        if (name and name in prop_names) or (value and value in prop_values):
            hits += 1
    return hits / len(params)


def sale_prop_score(names: list[str] | None, node: CategoryNode) -> float:
    """销售属性名命中类目 is_sale 属性名的比例。"""
    if not names:
        return 0.0
    sale_names = {normalize_text(p.name) for p in node.props if p.is_sale}
    if not sale_names:
        return 0.0
    hits = sum(1 for n in names if normalize_text(n) in sale_names)
    return hits / len(names)


# ================================================================ 匹配器


class CategoryMatcher:
    """在目标类目树里为商品找 Top-N 候选。"""

    def __init__(self, tree: CategoryTree,
                 weights: MatchWeights | None = None,
                 top_n: int | None = None,
                 min_score: float | None = None):
        s = get_settings()
        self.tree = tree
        self.weights = weights or MatchWeights.from_settings()
        self.top_n = int(top_n if top_n is not None
                         else (s.get("category_match.top_n", 5) or 5))
        self.min_score = float(min_score if min_score is not None
                               else (s.get("category_match.min_score", 0.25) or 0.25))
        self._leaves = tree.leaves()

    # ------------------------------------------------------------ 主入口
    def match(self, title: str | None,
              params: list[dict[str, str]] | None = None,
              sale_prop_names: list[str] | None = None) -> list[CategoryCandidate]:
        """返回候选列表，按分数降序、同分按 cid 升序。

        三个输入全空 → 返回空列表（不做无根据猜测）。
        """
        has_input = bool(normalize_text(title)) or bool(params) or bool(sale_prop_names)
        if not has_input:
            return []

        scored: list[CategoryCandidate] = []
        for node in self._leaves:
            ts = title_score(title, node)
            ps = param_score(params, node)
            ss = sale_prop_score(sale_prop_names, node)

            score = (self.weights.title * ts
                     + self.weights.params * ps
                     + self.weights.sale_props * ss)
            if score < self.min_score:
                continue

            reasons = []
            if ts > 0:
                reasons.append(f"标题词重合 {ts:.2f}")
            if ps > 0:
                reasons.append(f"参数命中 {ps:.2f}")
            if ss > 0:
                reasons.append(f"销售属性命中 {ss:.2f}")

            scored.append(CategoryCandidate(
                cid=node.cid,
                path=list(node.path),
                score=round(min(score, 1.0), 4),
                reasons=reasons or ["低置信"],
            ))

        # 稳定排序：分数降序，同分 cid 升序
        scored.sort(key=lambda c: (-c.score, c.cid))
        return scored[: self.top_n]

    def match_from_item(self, item: Any) -> list[CategoryCandidate]:
        """从 :class:`src.core.models.Item` 直接取输入。"""
        params = [{"name": p.name, "value": p.value}
                  for p in (getattr(item, "params", None) or [])]
        sale_names = [p.name for p in (getattr(item, "sale_props", None) or [])]
        return self.match(getattr(item, "title", None), params, sale_names)


# ================================================================ 确认存储契约


class ConfirmedMappingStore(Protocol):
    """已确认映射的存储接口。T6 提供 YAML/SQLite 实现，T3 只依赖契约。"""

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        """返回 {target_cid, target_path, confirmed_at, ...} 或 None。"""

    def put(self, fingerprint: str, record: dict[str, Any]) -> None:
        """写入或覆盖一条已确认记录。"""


# ================================================================ 解析器


class CategoryResolver:
    """把「已确认映射 + 自动匹配」合成最终结果。

    优先级：
    1. 指纹命中已确认记录，且目标 cid 在当前树里仍存在 → OK
    2. 指纹命中但目标 cid 已不在树里 → EXPIRED（不自动命中）
    3. 自动匹配有候选 → PENDING（等人工确认）
    4. 没候选 → ERROR(CATEGORY_NOT_MATCHED)
    """

    def __init__(self, tree: CategoryTree,
                 store: ConfirmedMappingStore | None = None,
                 matcher: CategoryMatcher | None = None):
        self.tree = tree
        self.store = store
        self.matcher = matcher or CategoryMatcher(tree)

    # ------------------------------------------------------------ 主入口
    def resolve(self, item: Any) -> CategoryMapping:
        title = getattr(item, "title", None)
        params = [{"name": p.name, "value": p.value}
                  for p in (getattr(item, "params", None) or [])]
        sale_names = [p.name for p in (getattr(item, "sale_props", None) or [])]
        platform = getattr(item, "platform", "") or ""
        fp = build_fingerprint(title, params, sale_names, platform)
        return self.resolve_by_fingerprint(fp, title, params, sale_names)

    def resolve_by_fingerprint(self, fingerprint: str,
                               title: str | None = None,
                               params: list[dict[str, str]] | None = None,
                               sale_prop_names: list[str] | None = None) -> CategoryMapping:
        # 1. 已确认
        if self.store:
            record = self.store.get(fingerprint)
            if record and record.get("target_cid"):
                cid = str(record["target_cid"])
                node = self.tree.by_cid(cid)
                if node is not None and node.is_leaf:
                    return CategoryMapping(
                        status=MappingStatus.OK,
                        target_cid=cid,
                        target_path=list(node.path or record.get("target_path") or []),
                        fingerprint=fingerprint,
                        confidence=1.0,
                        source="confirmed",
                    )
                # 目标 cid 失效：不许自动命中
                return CategoryMapping(
                    status=MappingStatus.EXPIRED,
                    fingerprint=fingerprint,
                    error=MappingError.MAPPING_EXPIRED,
                    detail=f"已确认的目标类目 {cid} 在当前类目树中不存在或不是叶类目",
                    candidates=self.matcher.match(title, params, sale_prop_names),
                )

        # 2. 自动匹配
        cands = self.matcher.match(title, params, sale_prop_names)
        if not cands:
            return CategoryMapping(
                status=MappingStatus.ERROR,
                fingerprint=fingerprint,
                error=MappingError.CATEGORY_NOT_MATCHED,
                detail="没有达到阈值的候选；标题/参数/销售属性可能全为空",
            )
        return CategoryMapping(
            status=MappingStatus.PENDING,
            fingerprint=fingerprint,
            confidence=cands[0].score,
            candidates=cands,
            detail="需要人工确认目标类目",
        )
