"""T4：属性、品牌与必填项映射。

三个子问题
----------
1. **销售属性映射**：源 `pid/vid` → 目标类目属性表里的 `pid/vid`。
   顺序：精确 vid → 精确 name → 别名 → 模糊(≥0.85) → 未命中。
   多个候选同分 → 挂起（``PROPERTY_AMBIGUOUS``），不许瞎选。

2. **品牌映射**：源品牌名 → 目标类目 brand 类型属性的 vid。
   **没命中时 ``brand_id`` 必须为 None，不许自造品牌**（验收标准 4）。
   必填品牌没命中 → 产生 ``BRAND_UNRESOLVED`` 错误。

3. **必填校验**：按**完整 pid** 判断。要求 `pid=12` 时，只有 `pid=112`
   的载荷仍然算缺失（验收标准 5）—— 所以不能用子串匹配，必须解析成
   `{pid: vid}` 后做集合比较。

配置
----
- ``data/mapping/property_map.yaml``：源 pid → 目标 pid 的显式映射
- ``data/mapping/synonyms.yaml``：属性值同义词（纯棉 → 棉）
- ``data/mapping/brand_map.yaml``：品牌名 → 目标品牌 vid
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from ..core.config import get_settings
from .category_models import (BrandMapping, CategoryNode, MappingError,
                              MappingStatus, PropMeta, PropertyMapping,
                              ValueMapping, normalize_text)

FUZZY_THRESHOLD = 0.85
AMBIGUOUS_MARGIN = 0.02


# ================================================================ 属性串工具


def parse_props(props: str | None) -> dict[str, str]:
    """``"pid:vid;pid:vid"`` → ``{pid: vid}``。"""
    out: dict[str, str] = {}
    for seg in (props or "").split(";"):
        seg = seg.strip()
        if not seg or ":" not in seg:
            continue
        pid, vid = seg.split(":", 1)
        pid, vid = pid.strip(), vid.strip()
        if pid:
            out[pid] = vid
    return out


def build_props(pairs: dict[str, str]) -> str:
    """``{pid: vid}`` → ``"pid:vid;pid:vid"``（按 pid 排序保证稳定）。"""
    return ";".join(f"{pid}:{vid}" for pid, vid in sorted(pairs.items()) if vid)


def missing_required(props: str | dict[str, str] | None,
                     required_pids: list[str] | None) -> list[str]:
    """返回缺失的必填 pid 列表。

    **必须按完整 pid 比对**：``pid=12`` 的载荷不含 ``pid=112``。
    另外 ``"12:"``（pid 有但 vid 为空）也算缺失——只有 pid 没有值不算填了。
    """
    if isinstance(props, str):
        parsed = parse_props(props)
    else:
        parsed = dict(props or {})
    have = {str(pid) for pid, vid in parsed.items() if str(vid or "").strip()}
    return [str(p) for p in (required_pids or []) if str(p) not in have]


# ================================================================ 资源加载


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class MappingResources:
    """三份映射表的统一加载器。"""

    def __init__(self, property_map: str | Path | None = None,
                 synonyms: str | Path | None = None,
                 brand_map: str | Path | None = None):
        s = get_settings()
        self.property_map = _load_yaml(
            Path(property_map) if property_map else s.mapping_dir / "property_map.yaml")
        self.synonyms = _load_yaml(
            Path(synonyms) if synonyms else s.mapping_dir / "synonyms.yaml")
        self.brand_map = _load_yaml(
            Path(brand_map) if brand_map else s.mapping_dir / "brand_map.yaml")

    # ------------------------------------------------------------ 查询
    def prop_rule(self, src_platform: str, target_platform: str,
                  src_pid: str) -> dict | None:
        node = self.property_map.get(f"{src_platform}->{target_platform}") or {}
        for section in ("sale_props", "key_props", "normal_props"):
            hit = (node.get(section) or {}).get(str(src_pid))
            if hit:
                return hit
        return None

    def brand_vid(self, brand_name: str) -> dict | None:
        """按品牌名查目标品牌记录。支持 {name: {vid, ...}} 或 {name: vid} 两种写法。"""
        if not brand_name:
            return None
        norm = normalize_text(brand_name)
        for key, val in (self.brand_map or {}).items():
            if normalize_text(key) == norm:
                if isinstance(val, dict):
                    return val
                return {"vid": str(val), "name": key}
        return None

    def synonym(self, value: str) -> str | None:
        if not value:
            return None
        norm = normalize_text(value)
        for src, tgt in (self.synonyms or {}).items():
            if normalize_text(src) == norm:
                return str(tgt)
        return None


# ================================================================ 解析器


class PropertyResolver:
    """把源商品的销售属性/参数映射到目标类目的属性体系。"""

    def __init__(self, node: CategoryNode, resources: MappingResources | None = None,
                 src_platform: str = "taobao", target_platform: str = "taobao"):
        self.node = node
        self.res = resources or MappingResources()
        self.src_platform = src_platform
        self.target_platform = target_platform

    # ------------------------------------------------------------ 属性定位
    def find_target_prop(self, src_pid: str, src_name: str) -> tuple[PropMeta | None, str]:
        """按 显式映射 → pid → 名称 → 别名 找目标属性。返回 (prop, 依据)。"""
        rule = self.res.prop_rule(self.src_platform, self.target_platform, src_pid)
        if rule and rule.get("target_pid"):
            target_pid = str(rule["target_pid"])
            for p in self.node.props:
                if p.pid == target_pid:
                    return p, "explicit"
            return None, "explicit_missing"

        for p in self.node.props:
            if p.pid == str(src_pid):
                return p, "pid"

        norm = normalize_text(src_name)
        if norm:
            for p in self.node.props:
                if normalize_text(p.name) == norm:
                    return p, "name"
            syn = self.res.synonym(src_name)
            if syn:
                syn_norm = normalize_text(syn)
                for p in self.node.props:
                    if normalize_text(p.name) == syn_norm:
                        return p, "alias"
        return None, "none"

    # ------------------------------------------------------------ 值映射
    def map_value(self, prop: PropMeta, src_vid: str | None,
                  src_name: str, rule: dict | None = None) -> ValueMapping:
        """单个属性值映射。歧义时 target_vid 为 None 并标 PROPERTY_AMBIGUOUS。"""
        name = (src_name or "").strip()

        # 1. 显式映射表（最高优先级）
        if rule:
            value_map = rule.get("value_map") or {}
            hit = value_map.get(str(src_vid)) if src_vid else None
            if isinstance(hit, dict) and hit.get("vid"):
                return ValueMapping(src_vid=src_vid, src_name=name,
                                    target_vid=str(hit["vid"]),
                                    target_name=hit.get("name") or name,
                                    matched_by="vid", score=1.0)
            if isinstance(hit, str):
                return ValueMapping(src_vid=src_vid, src_name=name,
                                    target_vid=hit, target_name=name,
                                    matched_by="vid", score=1.0)

        # 2. 精确 vid
        if src_vid:
            for v in prop.values:
                if v.vid == str(src_vid):
                    return ValueMapping(src_vid=src_vid, src_name=name,
                                        target_vid=v.vid, target_name=v.name,
                                        matched_by="vid", score=1.0)

        norm = normalize_text(name)
        if not norm:
            return ValueMapping(src_vid=src_vid, src_name=name, matched_by="none")

        # 3. 精确名称
        for v in prop.values:
            if normalize_text(v.name) == norm:
                return ValueMapping(src_vid=src_vid, src_name=name,
                                    target_vid=v.vid, target_name=v.name,
                                    matched_by="name", score=1.0)

        # 4. 显式同义词
        syn = self.res.synonym(name)
        if syn:
            syn_norm = normalize_text(syn)
            for v in prop.values:
                if normalize_text(v.name) == syn_norm:
                    return ValueMapping(src_vid=src_vid, src_name=name,
                                        target_vid=v.vid, target_name=v.name,
                                        matched_by="alias", score=1.0)

        # 5. 属性值自带别名
        for v in prop.values:
            if any(normalize_text(a) == norm for a in v.aliases):
                return ValueMapping(src_vid=src_vid, src_name=name,
                                    target_vid=v.vid, target_name=v.name,
                                    matched_by="alias", score=1.0)

        # 6. 模糊匹配 + 歧义检测
        scored: list[tuple[float, Any]] = []
        for v in prop.values:
            score = SequenceMatcher(None, norm, normalize_text(v.name)).ratio()
            scored.append((score, v))
        scored.sort(key=lambda x: (-x[0], x[1].vid))

        if scored and scored[0][0] >= FUZZY_THRESHOLD:
            best_score, best = scored[0]
            if len(scored) > 1 and best_score - scored[1][0] <= AMBIGUOUS_MARGIN:
                # 同分歧义：挂起，不瞎选
                return ValueMapping(src_vid=src_vid, src_name=name,
                                    matched_by="fuzzy", score=round(best_score, 4))
            return ValueMapping(src_vid=src_vid, src_name=name,
                                target_vid=best.vid, target_name=best.name,
                                matched_by="fuzzy", score=round(best_score, 4))

        return ValueMapping(src_vid=src_vid, src_name=name, matched_by="none")

    # ------------------------------------------------------------ 销售属性
    def resolve_sale_props(self, sale_props: list[Any]) -> list[PropertyMapping]:
        """``sale_props`` 是 ``Item.sale_props``（有 pid/name/values 的对象）。"""
        out: list[PropertyMapping] = []
        for sp in sale_props or []:
            src_pid = str(getattr(sp, "pid", "") or "")
            src_name = str(getattr(sp, "name", "") or "")
            target_prop, basis = self.find_target_prop(src_pid, src_name)
            rule = self.res.prop_rule(self.src_platform, self.target_platform, src_pid)

            if target_prop is None:
                out.append(PropertyMapping(
                    src_pid=src_pid, src_name=src_name,
                    required=bool((rule or {}).get("required")),
                    status=MappingStatus.PENDING,
                    error=MappingError.PROPERTY_AMBIGUOUS if basis == "explicit_missing" else None,
                ))
                continue

            values: list[ValueMapping] = []
            for v in (getattr(sp, "values", None) or []):
                values.append(self.map_value(
                    target_prop,
                    str(getattr(v, "vid", "") or "") or None,
                    str(getattr(v, "name", "") or ""),
                    rule))

            ambiguous = any(v.matched_by == "fuzzy" and not v.target_vid for v in values)
            status = MappingStatus.PENDING if ambiguous else MappingStatus.OK
            out.append(PropertyMapping(
                src_pid=src_pid, src_name=src_name,
                target_pid=target_prop.pid, target_name=target_prop.name,
                values=values,
                required=target_prop.required,
                status=status,
                error=MappingError.PROPERTY_AMBIGUOUS if ambiguous else None,
            ))
        return out

    # ------------------------------------------------------------ 品牌
    def resolve_brand(self, params: list[Any] | None,
                      brand_required: bool | None = None) -> BrandMapping:
        """从参数表取「品牌」并映射。**没命中时 brand_id 为 None。**"""
        src_brand = None
        for p in (params or []):
            name = normalize_text(getattr(p, "name", "") or "")
            if name in ("品牌", "brand", "商标"):
                src_brand = str(getattr(p, "value", "") or "").strip()
                break

        brand_prop = next(
            (p for p in self.node.props if p.type == "brand" or
             normalize_text(p.name) in ("品牌", "brand")), None)
        required = bool(brand_prop.required) if brand_prop else bool(brand_required)

        if not src_brand:
            return BrandMapping(
                src_brand=None, required=False if not required else True,
                status=MappingStatus.PENDING if required else MappingStatus.OK,
                error=MappingError.BRAND_UNRESOLVED if required else None,
            )

        # 1. 显式品牌表：**必须校验目标 vid 属于当前类目的品牌属性**
        #    复审 blocker：连衣裙类目只有 brand_none，源品牌 Dahua 却能拿到
        #    brand_dahua 并写进载荷 —— 等于给商品挂了一个该类目不允许的品牌。
        hit = self.res.brand_vid(src_brand)
        if hit and hit.get("vid"):
            vid = str(hit["vid"])
            allowed = {v.vid for v in (brand_prop.values if brand_prop else [])}
            if brand_prop is not None and vid in allowed:
                return BrandMapping(src_brand=src_brand, brand_id=vid,
                                    brand_name=hit.get("name") or src_brand,
                                    matched_by="exact")
            if brand_prop is None:
                # 类目没有品牌属性，没法校验，按未命中处理
                return BrandMapping(
                    src_brand=src_brand, brand_id=None, matched_by="none",
                    status=MappingStatus.PENDING if required else MappingStatus.OK,
                    error=MappingError.BRAND_UNRESOLVED if required else None)
            # 类目有品牌属性但不含这个品牌 → 不许用
            return BrandMapping(
                src_brand=src_brand, brand_id=None, brand_name=None, matched_by="none",
                status=MappingStatus.PENDING if required else MappingStatus.OK,
                error=MappingError.BRAND_UNRESOLVED if required else None,
            )

        # 2. 目标类目 brand 属性的值
        if brand_prop:
            vm = self.map_value(brand_prop, None, src_brand)
            if vm.target_vid:
                return BrandMapping(src_brand=src_brand, brand_id=vm.target_vid,
                                    brand_name=vm.target_name,
                                    matched_by="exact" if vm.matched_by == "name" else "alias")

        # 3. 没命中：brand_id 必须为 None，不许自造
        return BrandMapping(
            src_brand=src_brand, brand_id=None, brand_name=None, matched_by="none",
            status=MappingStatus.PENDING if required else MappingStatus.OK,
            error=MappingError.BRAND_UNRESOLVED if required else None,
        )

    # ------------------------------------------------------------ 必填
    def required_pids(self) -> list[str]:
        return [p.pid for p in self.node.props if p.required]

    def validate_required(self, props: str | dict[str, str]) -> list[str]:
        return missing_required(props, self.required_pids())


# ================================================================ 汇总


class PropertyResult:
    """属性解析的完整结果。"""

    def __init__(self, sale_props: list[PropertyMapping],
                 brand: BrandMapping,
                 props: dict[str, str],
                 required_missing: list[str]):
        self.sale_props = sale_props
        self.brand = brand
        self.props = props
        self.required_missing = required_missing

    @property
    def ok(self) -> bool:
        return (not self.required_missing
                and all(m.status is MappingStatus.OK for m in self.sale_props)
                and self.brand.brand_id is not None
                if self.brand.src_brand else not self.required_missing)

    @property
    def props_string(self) -> str:
        return build_props(self.props)

    def pending_items(self) -> list[str]:
        out = [f"{m.src_name}: 无目标属性" for m in self.sale_props
               if m.target_pid is None]
        out += [f"{m.src_name}/{v.src_name}: 值歧义" for m in self.sale_props
                for v in m.values if not v.target_vid]
        if self.brand.src_brand and not self.brand.brand_id:
            out.append(f"品牌 {self.brand.src_brand}: 未命中")
        out += [f"缺必填属性 pid={p}" for p in self.required_missing]
        return out


def resolve_properties(item: Any, node: CategoryNode,
                       resources: MappingResources | None = None,
                       src_platform: str = "taobao",
                       target_platform: str = "taobao") -> PropertyResult:
    """一站式：销售属性 + 品牌 + 必填校验。"""
    r = PropertyResolver(node, resources, src_platform, target_platform)
    mappings = r.resolve_sale_props(getattr(item, "sale_props", None) or [])
    brand = r.resolve_brand(getattr(item, "params", None) or [])

    props: dict[str, str] = {}
    for m in mappings:
        if not m.target_pid:
            continue
        for v in m.values:
            if v.target_vid:
                props[m.target_pid] = v.target_vid
    if brand.brand_id:
        brand_prop = next((p for p in node.props if p.type == "brand"), None)
        if brand_prop:
            props[brand_prop.pid] = brand.brand_id

    missing = missing_required(props, r.required_pids())
    return PropertyResult(mappings, brand, props, missing)
