"""T5：按目标维度重建 SKU 与价格计算。

两条硬约束（来自 .pair/plan.json 验收标准 6、7）
--------------------------------------------------
1. **未映射的维度、组合冲突，一律不许静默丢弃/截断/合并。**
   每个被跳过的源 SKU 都要进 ``SkuPlan.skipped`` 并写明原因，
   让调用方自己决定是中断还是继续。否则会出现「SKU 悄悄少了一半、
   价格全错」这种最坏情况。
2. **库存 0 就是 0。** 不能用 ``qty or default`` 把 0 变成 999。

价格用 ``Decimal``，不用 float —— 浮点误差在电商定价里会直接变成
「实际售价 129.00000000001」这种事故。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from ..core.config import get_settings
from .category_models import (CategoryNode, MappingError, MappingStatus,
                              PropMeta, SkuPlan, SkuPlanItem)


class PriceError(ValueError):
    def __init__(self, code: MappingError, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


# ================================================================ 价格


def to_decimal(value: Any) -> Decimal | None:
    """转 Decimal。None / 非数字 / NaN / Inf 返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d if d.is_finite() else None


def _dec_or(value: Any, default: str) -> Decimal:
    """转 Decimal，只在 None 时用默认值。

    不能写成 ``to_decimal(v) or Decimal(default)`` —— ``Decimal("0")`` 是 falsy。
    """
    d = to_decimal(value)
    return d if d is not None else Decimal(default)


class PriceConfig:
    """价格策略配置。全部用 Decimal。

    注意：**不能用 ``to_decimal(x) or default``**，因为 ``Decimal("0")`` 是 falsy，
    会把合法的 0 替换成默认值。必须显式判 None。
    """

    def __init__(self, mode: str = "multiplier",
                 multiplier: Any = "1.35",
                 fixed_add: Any = "0",
                 min_margin: Any = "0",
                 round_to: Any = "0.01",
                 clamp: tuple[Any, Any] | list[Any] | None = None):
        self.mode = mode or "multiplier"
        self.multiplier = _dec_or(multiplier, "1")
        self.fixed_add = _dec_or(fixed_add, "0")
        self.min_margin = _dec_or(min_margin, "0")
        self.round_to = _dec_or(round_to, "0.01")
        c = list(clamp or ["0.01", "1000000000"])
        self.clamp = (_dec_or(c[0], "0.01"), _dec_or(c[1], "1000000000"))

    @classmethod
    def from_settings(cls) -> "PriceConfig":
        s = get_settings()
        cfg = s.get("price", {}) or {}
        return cls(
            mode=cfg.get("mode", "multiplier"),
            multiplier=cfg.get("multiplier", 1.35),
            fixed_add=cfg.get("fixed_add", 0),
            min_margin=cfg.get("min_margin", 0),
            round_to=cfg.get("round_to", 0.01),
            clamp=cfg.get("clamp"),
        )


class PriceEngine:
    """价格计算。非法输入一律抛 :class:`PriceError`，绝不返回 0 或原值。"""

    def __init__(self, config: PriceConfig | None = None):
        self.cfg = config or PriceConfig.from_settings()
        if self.cfg.round_to <= 0:
            raise PriceError(MappingError.PRICE_INVALID,
                             f"round_to 必须 > 0，实际 {self.cfg.round_to}")

    def apply(self, src_price: Any) -> Decimal:
        d = to_decimal(src_price)
        if d is None:
            raise PriceError(MappingError.PRICE_INVALID,
                             f"价格缺失或不是有限数字: {src_price!r}")
        if d <= 0:
            raise PriceError(MappingError.PRICE_INVALID, f"价格必须 > 0，实际 {d}")

        mode = self.cfg.mode
        if mode == "copy":
            out = d
        elif mode == "fixed_add":
            out = d + self.cfg.fixed_add
        else:  # multiplier（默认）
            out = d * self.cfg.multiplier
            floor = d + self.cfg.min_margin
            if out < floor:
                out = floor

        step = self.cfg.round_to
        # 按 step 量化，ROUND_HALF_UP（商业习惯，不是银行家舍入）
        steps = (out / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        out = steps * step

        lo, hi = self.cfg.clamp
        if out < lo:
            out = lo
        elif out > hi:
            out = hi

        # 归一化到 step 的小数位，去掉 129.000000 这种尾巴
        places = max(0, -step.as_tuple().exponent)
        return out.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


# ================================================================ SKU


class SkuPlanner:
    """按目标类目的销售属性维度重建 SKU。

    输入：源商品的 SKU 列表（每个带 ``attrs: {src_pid: src_vid}``）+ 已完成的属性映射。
    输出：:class:`SkuPlan`，包含成功项与**显式记录的被跳过项**。
    """

    def __init__(self, node: CategoryNode,
                 price_engine: PriceEngine | None = None,
                 max_sku: int | None = None,
                 default_stock: int | None = None,
                 stock_mode: str = "fixed"):
        s = get_settings()
        self.node = node
        self.price = price_engine or PriceEngine()
        self.max_sku = int(max_sku if max_sku is not None
                           else (s.get("publish.max_sku", 100) or 100))
        self.default_stock = int(default_stock if default_stock is not None
                                 else (s.get("publish.default_stock", 999) or 999))
        self.stock_mode = stock_mode      # fixed | copy
        # 目标平台的销售属性，顺序即输出 props 串的维度顺序
        self.sale_props: list[PropMeta] = [p for p in node.props if p.is_sale]

    # ------------------------------------------------------------ 主入口
    def rebuild(self, skus: list[Any],
                prop_mappings: list[Any] | None = None) -> SkuPlan:
        """``prop_mappings`` 是 :class:`PropertyMapping` 列表（T4 产出）。

        没给就按 pid 直通（同平台场景）。
        """
        # src_pid -> (target_pid, {src_vid: target_vid})
        pid_map: dict[str, tuple[str, dict[str, str]]] = {}
        for m in (prop_mappings or []):
            src_pid = str(getattr(m, "src_pid", "") or "")
            target_pid = getattr(m, "target_pid", None)
            if not src_pid or not target_pid:
                continue
            vmap: dict[str, str] = {}
            for v in (getattr(m, "values", None) or []):
                if getattr(v, "src_vid", None) and getattr(v, "target_vid", None):
                    vmap[str(v.src_vid)] = str(v.target_vid)
            pid_map[src_pid] = (str(target_pid), vmap)

        target_pids = [p.pid for p in self.sale_props]
        items: list[SkuPlanItem] = []
        skipped: list[dict[str, Any]] = []
        seen_combos: dict[str, str] = {}

        for sku in (skus or []):
            src_id = str(getattr(sku, "sku_id", "") or "")
            attrs: dict[str, str] = dict(getattr(sku, "attrs", None) or {})

            # 1. 映射到目标维度
            mapped: dict[str, str] = {}
            unmapped_dims: list[str] = []
            for src_pid, src_vid in attrs.items():
                pair = pid_map.get(str(src_pid))
                if not pair:
                    # 没给映射表时按 pid 直通（同平台）
                    if not prop_mappings:
                        if str(src_pid) in target_pids:
                            mapped[str(src_pid)] = str(src_vid)
                        else:
                            unmapped_dims.append(str(src_pid))
                    else:
                        unmapped_dims.append(str(src_pid))
                    continue
                target_pid, vmap = pair
                target_vid = vmap.get(str(src_vid))
                if not target_vid:
                    unmapped_dims.append(f"{src_pid}:{src_vid}")
                    continue
                mapped[target_pid] = target_vid

            if unmapped_dims:
                skipped.append({
                    "src_sku_id": src_id,
                    "reason": MappingError.SKU_DIMENSION_UNMAPPED.value,
                    "detail": f"未映射的维度/值: {', '.join(unmapped_dims)}",
                })
                continue

            # 2. 必须覆盖所有目标销售维度
            missing = [pid for pid in target_pids if pid not in mapped]
            if missing:
                skipped.append({
                    "src_sku_id": src_id,
                    "reason": MappingError.SKU_DIMENSION_UNMAPPED.value,
                    "detail": f"缺少目标维度: {', '.join(missing)}",
                })
                continue

            # 3. 按目标顺序拼 props
            props = ";".join(f"{pid}:{mapped[pid]}" for pid in target_pids)

            # 4. 组合冲突：两个源 SKU 映射到同一个目标组合
            if props in seen_combos:
                skipped.append({
                    "src_sku_id": src_id,
                    "reason": MappingError.SKU_COMBINATION_CONFLICT.value,
                    "detail": f"与 {seen_combos[props]} 映射到同一组合 {props}",
                })
                continue
            seen_combos[props] = src_id

            # 5. 价格
            try:
                price = self.price.apply(getattr(sku, "price", None))
            except PriceError as e:
                skipped.append({
                    "src_sku_id": src_id,
                    "reason": e.code.value,
                    "detail": e.detail,
                })
                continue

            # 6. 库存：0 必须保持 0
            qty = self._quantity(sku)

            items.append(SkuPlanItem(
                props=props,
                price=float(price),
                quantity=qty,
                outer_id=str(getattr(sku, "outer_id", None) or src_id) or None,
                src_sku_id=src_id or None,
                image=getattr(sku, "image", None),
            ))

        # 7. 超限：**不截断**。截断会让「一部分 SKU 悄悄消失」，价格和库存全错。
        if len(items) > self.max_sku:
            return SkuPlan(
                items=[],
                skipped=skipped + [{
                    "src_sku_id": None,
                    "reason": MappingError.SKU_LIMIT_EXCEEDED.value,
                    "detail": f"目标平台上限 {self.max_sku}，实际 {len(items)}，"
                              f"全部 {len(items)} 个 SKU 均未输出（不截断）",
                }],
                status=MappingStatus.ERROR,
                error=MappingError.SKU_LIMIT_EXCEEDED,
                detail=f"SKU 数 {len(items)} 超过上限 {self.max_sku}，已整体拒绝而非截断",
            )

        if not items:
            return SkuPlan(
                items=[], skipped=skipped,
                status=MappingStatus.ERROR,
                error=MappingError.SKU_DIMENSION_UNMAPPED,
                detail="没有任何 SKU 成功重建，见 skipped",
            )

        status = MappingStatus.PENDING if skipped else MappingStatus.OK
        return SkuPlan(items=items, skipped=skipped, status=status,
                       detail=f"{len(skipped)} 个 SKU 被跳过" if skipped else None)

    # ------------------------------------------------------------ 库存
    def _quantity(self, sku: Any) -> int:
        if self.stock_mode == "copy":
            qty = getattr(sku, "quantity", None)
            if qty is None:
                return self.default_stock
            try:
                return max(0, int(qty))     # 0 就是 0，不替换
            except (TypeError, ValueError):
                return self.default_stock
        return self.default_stock


# ================================================================ 便捷入口


def plan_skus(item: Any, node: CategoryNode,
              prop_mappings: list[Any] | None = None,
              price_engine: PriceEngine | None = None,
              stock_mode: str = "fixed") -> SkuPlan:
    planner = SkuPlanner(node, price_engine=price_engine, stock_mode=stock_mode)
    return planner.rebuild(getattr(item, "skus", None) or [], prop_mappings)
