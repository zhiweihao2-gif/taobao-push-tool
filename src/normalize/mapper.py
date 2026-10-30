"""映射层：类目映射 / 属性映射 / 价格引擎 / SKU 重建 / 发布载荷构造。

这一层是项目核心。类目与属性映射表存 YAML，人工确认一次后永久复用。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from ..core.config import get_settings
from ..core.models import Item, Sku


# ================================================================ 类目映射

class CategoryMapper:
    """源类目 → 目标类目。

    映射表结构::

        taobao->jd:
          "50010850":
            target_cid: "1319"
            target_path: "女装 > 连衣裙"
            confidence: 0.92
            verified: true
    """

    def __init__(self, map_file: str | Path | None = None):
        s = get_settings()
        self.path = Path(map_file) if map_file else s.mapping_dir / "category_map.yaml"
        self.data: dict[str, dict] = {}
        if self.path.exists():
            self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def lookup(self, src_platform: str, target_platform: str, src_cid: str) -> dict | None:
        key = f"{src_platform}->{target_platform}"
        node = (self.data.get(key) or {}).get(str(src_cid))
        if node and node.get("verified"):
            return node
        return None

    def candidates(self, src_path: list[str], target_tree: list[dict],
                   top_k: int = 5) -> list[dict]:
        """用源类目路径文本在目标类目树里召回候选（相似度打分）。"""
        if not src_path or not target_tree:
            return []
        src_leaf = src_path[-1]
        src_tokens = set(_tokens(" ".join(src_path)))
        scored = []
        for node in target_tree:
            tgt_leaf = node.get("name", "")
            ratio = SequenceMatcher(None, src_leaf, tgt_leaf).ratio()
            tgt_tokens = set(_tokens(tgt_leaf))
            jac = len(src_tokens & tgt_tokens) / max(1, len(src_tokens | tgt_tokens))
            score = 0.6 * ratio + 0.3 * jac + 0.1 * (
                1.0 if src_path[0] in (node.get("root_name") or "") else 0.0)
            scored.append({**node, "score": round(score, 3)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def remember(self, src_platform: str, target_platform: str, src_cid: str,
                 target_cid: str, target_path: str, confidence: float = 1.0) -> None:
        key = f"{src_platform}->{target_platform}"
        self.data.setdefault(key, {})[str(src_cid)] = {
            "target_cid": str(target_cid),
            "target_path": target_path,
            "confidence": confidence,
            "verified": True,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(self.data, allow_unicode=True, sort_keys=True),
                             encoding="utf-8")


# ================================================================ 属性映射

class PropertyMapper:
    """属性名/属性值 → 目标平台 pid/vid。

    映射表结构::

        taobao->jd:
          sale_props:
            "1627207":
              target_pid: "1000000016"
              target_name: "颜色"
              value_map: {"28320": {"vid": "1000000017", "name": "黑色"}}
              fallback: drop          # drop | default | ask
    """

    def __init__(self, map_file: str | Path | None = None,
                 synonyms_file: str | Path | None = None):
        s = get_settings()
        self.path = Path(map_file) if map_file else s.mapping_dir / "property_map.yaml"
        self.data: dict[str, Any] = {}
        if self.path.exists():
            self.data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

        syn_path = Path(synonyms_file) if synonyms_file else s.mapping_dir / "synonyms.yaml"
        self.synonyms: dict[str, str] = {}
        if syn_path.exists():
            self.synonyms = yaml.safe_load(syn_path.read_text(encoding="utf-8")) or {}

    def _node(self, src_platform: str, target_platform: str) -> dict:
        return self.data.get(f"{src_platform}->{target_platform}") or {}

    def map_sale_prop(self, src_platform: str, target_platform: str,
                      pid: str) -> dict | None:
        return (self._node(src_platform, target_platform).get("sale_props") or {}).get(str(pid))

    def map_value(self, value_map: dict, src_vid: str, src_name: str) -> dict | None:
        hit = value_map.get(str(src_vid))
        if hit:
            return hit
        # 值名精确 / 别名 / 模糊
        norm = self.synonyms.get(src_name, src_name)
        for tgt_vid, node in value_map.items():
            if node.get("name") == src_name or node.get("name") == norm:
                return node
        best, best_score = None, 0.0
        for tgt_vid, node in value_map.items():
            score = SequenceMatcher(None, norm, node.get("name", "")).ratio()
            if score > best_score:
                best, best_score = node, score
        return best if best_score >= 0.85 else None


# ================================================================ 价格引擎

class PriceEngine:
    def __init__(self, cfg: dict | None = None):
        s = get_settings()
        self.cfg = cfg or s.get("price", {}) or {}

    def apply(self, src_price: float | None) -> float | None:
        if src_price is None:
            return None
        mode = self.cfg.get("mode", "multiplier")
        if mode == "copy":
            out = src_price
        elif mode == "fixed_add":
            out = src_price + float(self.cfg.get("fixed_add", 0))
        else:
            out = src_price * float(self.cfg.get("multiplier", 1.35))
            out = max(out, src_price + float(self.cfg.get("min_margin", 0)))

        step = float(self.cfg.get("round_to", 0.01))
        out = round(out / step) * step
        lo, hi = (self.cfg.get("clamp") or [0.01, 1e9])[:2]
        return round(min(max(out, float(lo)), float(hi)), 2)


# ================================================================ SKU 重建

class SkuRebuilder:
    """把源平台的扁平 SKU 表，按目标平台的属性维度顺序重建矩阵。"""

    def __init__(self, price_engine: PriceEngine | None = None,
                 default_stock: int = 999, max_sku: int = 100):
        s = get_settings()
        self.price_engine = price_engine or PriceEngine()
        self.default_stock = default_stock or int(s.get("publish.default_stock", 999))
        self.max_sku = max_sku or int(s.get("publish.max_sku", 100))

    def rebuild(self, item: Item, prop_order: list[dict],
                stock_mode: str = "fixed") -> list[dict]:
        """prop_order: [{"src_pid":..., "tgt_pid":..., "value_map": {...}}, ...]"""
        out: list[dict] = []
        for sku in item.skus:
            attrs: list[tuple[str, str]] = []
            ok = True
            for spec in prop_order:
                src_vid = sku.attrs.get(spec["src_pid"])
                if not src_vid:
                    ok = False
                    break
                mapped = _map_by_vid(spec.get("value_map", {}), src_vid)
                if not mapped:
                    ok = False
                    break
                attrs.append((str(spec["tgt_pid"]), str(mapped["vid"])))
            if not ok:
                continue
            out.append({
                "props": ";".join(f"{p}:{v}" for p, v in attrs),
                "price": self.price_engine.apply(sku.price) or 0.0,
                "quantity": (sku.quantity if stock_mode == "copy" and sku.quantity
                             else self.default_stock),
                "outer_id": sku.outer_id or sku.sku_id,
            })
            if len(out) >= self.max_sku:
                break

        # 无销售属性商品：生成单一 SKU
        if not out:
            base = item.price.min or 0
            out.append({
                "props": "",
                "price": self.price_engine.apply(base) or 0.0,
                "quantity": self.default_stock,
                "outer_id": item.item_id,
            })
        return out


def _map_by_vid(value_map: dict, src_vid: str) -> dict | None:
    node = value_map.get(str(src_vid))
    if node:
        return node
    return None


# ================================================================ 校验

class Validator:
    def validate(self, payload: dict, required_props: list[str] | None = None) -> list[str]:
        errors: list[str] = []
        if not payload.get("cid"):
            errors.append("目标类目未确定（category_map 未命中，请人工确认）")
        if not payload.get("title"):
            errors.append("标题为空")
        if not payload.get("images"):
            errors.append("主图为空（至少 1 张）")
        elif len(payload["images"]) < 3:
            errors.append(f"主图仅 {len(payload['images'])} 张，建议 ≥ 3 张")
        if not payload.get("skus"):
            errors.append("SKU 为空")
        props_str = payload.get("props") or ""
        for pid in (required_props or []):
            if f"{pid}:" not in props_str:
                errors.append(f"缺少必填属性 pid={pid}")
        for s in payload.get("skus", []):
            if not s.get("price"):
                errors.append("存在价格为 0 的 SKU")
                break
        return errors


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s/>、,，|]+", text) if t]
