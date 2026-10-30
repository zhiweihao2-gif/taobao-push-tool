"""T5 测试：SKU 重建与价格计算。

跑法::

    python tests/test_sku_rebuild.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalize.category_models import (MappingError, MappingStatus,  # noqa: E402
                                           PropMeta, PropValueMeta,
                                           PropertyMapping, ValueMapping)
from src.normalize.category_provider import LocalJsonProvider  # noqa: E402
from src.normalize.sku_builder import (PriceConfig, PriceEngine,  # noqa: E402
                                       PriceError, SkuPlanner, to_decimal)

PASS, FAIL = 0, 0
EXAMPLE = ROOT / "data/mapping/category_tree.example.json"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


class Sku:
    def __init__(self, sku_id, attrs, price, quantity=None, outer_id=None):
        self.sku_id = sku_id
        self.attrs = attrs
        self.price = price
        self.quantity = quantity
        self.outer_id = outer_id
        self.image = None


class Item:
    def __init__(self, skus):
        self.skus = skus


def switch_node():
    return LocalJsonProvider(EXAMPLE).load("taobao").by_cid("50023905")


def mappings():
    """模拟 T4 产出：颜色 1627207、端口 4054020。"""
    return [
        PropertyMapping(
            src_pid="1627207", src_name="颜色分类",
            target_pid="1627207", target_name="颜色分类",
            values=[ValueMapping(src_vid="44467701705", src_name="百兆",
                                 target_vid="44467701705", target_name="【百兆非POE交换机】"),
                    ValueMapping(src_vid="44467701706", src_name="千兆",
                                 target_vid="44467701706", target_name="【千兆非POE交换机】")]),
        PropertyMapping(
            src_pid="4054020", src_name="端口总数",
            target_pid="4054020", target_name="端口总数",
            values=[ValueMapping(src_vid="43502", src_name="5个",
                                 target_vid="43502", target_name="5个"),
                    ValueMapping(src_vid="15295387", src_name="8个",
                                 target_vid="15295387", target_name="8个")]),
    ]


# ---------------------------------------------------------------- 价格


def test_decimal() -> None:
    print("[1] Decimal 转换")
    check("字符串", to_decimal("12.34") == Decimal("12.34"))
    check("整数", to_decimal(12) == Decimal("12"))
    check("None", to_decimal(None) is None)
    check("NaN", to_decimal("NaN") is None)
    check("Inf", to_decimal("Infinity") is None)
    check("乱字符串", to_decimal("abc") is None)
    check("bool 不算数字", to_decimal(True) is None)


def test_price_basic() -> None:
    print("\n[2] 价格计算（Decimal 舍入）")
    e = PriceEngine(PriceConfig(mode="multiplier", multiplier="1.35",
                                round_to="0.01", clamp=["0.01", "99999"]))
    check("129 * 1.35 = 174.15", e.apply("129") == Decimal("174.15"), str(e.apply("129")))
    check("100 * 1.35 = 135.00", e.apply(100) == Decimal("135.00"), str(e.apply(100)))
    check("返回值是 Decimal", isinstance(e.apply("10"), Decimal))

    e2 = PriceEngine(PriceConfig(mode="copy", round_to="0.01"))
    check("copy 模式原价", e2.apply("88.888") == Decimal("88.89"), str(e2.apply("88.888")))

    e3 = PriceEngine(PriceConfig(mode="fixed_add", fixed_add="5", round_to="0.01"))
    check("fixed_add +5", e3.apply("10") == Decimal("15.00"), str(e3.apply("10")))


def test_price_rounding() -> None:
    print("\n[3] 舍入规则")
    e = PriceEngine(PriceConfig(mode="copy", round_to="0.01"))
    check("0.005 → 0.01（ROUND_HALF_UP）", e.apply("0.005") == Decimal("0.01"),
          str(e.apply("0.005")))
    check("0.004 → 0.00（clamp 到 0.01）", e.apply("0.004") == Decimal("0.01"),
          str(e.apply("0.004")))

    e2 = PriceEngine(PriceConfig(mode="copy", round_to="0.1"))
    check("round_to=0.1：12.34 → 12.3", e2.apply("12.34") == Decimal("12.3"),
          str(e2.apply("12.34")))

    e3 = PriceEngine(PriceConfig(mode="copy", round_to="1"))
    check("round_to=1：12.5 → 13", e3.apply("12.5") == Decimal("13"), str(e3.apply("12.5")))


def test_price_invalid() -> None:
    print("\n[4] 非法价格一律报错（验收第 7 条）")
    e = PriceEngine(PriceConfig())
    cases = [
        ("缺价 None", None),
        ("缺价 空串", ""),
        ("非数字", "abc"),
        ("NaN", "NaN"),
        ("Infinity", "Infinity"),
        ("零价", 0),
        ("负价", -5),
    ]
    for label, val in cases:
        raised = False
        try:
            e.apply(val)
        except PriceError as ex:
            raised = True
            check(f"{label} → PRICE_INVALID",
                  ex.code is MappingError.PRICE_INVALID, ex.code.value)
        check(f"{label} 抛异常", raised)

    raised = False
    try:
        PriceEngine(PriceConfig(round_to="0"))
    except PriceError as ex:
        raised = True
        check("非法步长 round_to=0 → PRICE_INVALID",
              ex.code is MappingError.PRICE_INVALID, ex.code.value)
    check("非法步长抛异常", raised)


def test_price_clamp() -> None:
    print("\n[5] 价格上下限")
    e = PriceEngine(PriceConfig(mode="copy", round_to="0.01", clamp=["9.9", "100"]))
    check("低于下限被抬到 9.9", e.apply("5") == Decimal("9.90"), str(e.apply("5")))
    check("高于上限被压到 100", e.apply("500") == Decimal("100.00"),
          str(e.apply("500")))


# ---------------------------------------------------------------- SKU


def test_rebuild_basic() -> None:
    print("\n[6] 基础重建：维度顺序与 props 串")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy", round_to="0.01")),
                         max_sku=100, default_stock=999)
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "15.5"),
        Sku("s2", {"1627207": "44467701705", "4054020": "15295387"}, "22.0"),
        Sku("s3", {"1627207": "44467701706", "4054020": "43502"}, "31.0"),
        Sku("s4", {"1627207": "44467701706", "4054020": "15295387"}, "55.0"),
    ])
    plan = planner.rebuild(item.skus, mappings())

    check("状态 OK", plan.status is MappingStatus.OK, plan.status.value)
    check("4 个 SKU 全部重建", len(plan.items) == 4, str(len(plan.items)))
    check("没有跳过", plan.skipped == [], str(plan.skipped))
    # 目标类目 sale_props 顺序：1627207(颜色) 在前，4054020(端口) 在后
    check("props 串维度顺序正确",
          plan.items[0].props.startswith("1627207:"), plan.items[0].props)
    check("props 串含两个维度",
          plan.items[0].props.count(":") == 2, plan.items[0].props)
    check("价格正确", plan.items[0].price == 15.5, str(plan.items[0].price))
    check("src_sku_id 保留（可回溯）",
          plan.items[0].src_sku_id == "s1", str(plan.items[0].src_sku_id))


def test_stock_zero() -> None:
    print("\n[7] 库存 0 必须保持 0（验收第 6 条）")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")),
                         default_stock=999, stock_mode="copy")
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "10", quantity=0),
        Sku("s2", {"1627207": "44467701706", "4054020": "43502"}, "20", quantity=5),
    ])
    plan = planner.rebuild(item.skus, mappings())
    check("2 个都重建", len(plan.items) == 2, str(len(plan.items)))
    check("库存 0 保持 0", plan.items[0].quantity == 0, str(plan.items[0].quantity))
    check("库存 5 保持 5", plan.items[1].quantity == 5, str(plan.items[1].quantity))

    planner2 = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")),
                          default_stock=999, stock_mode="fixed")
    plan2 = planner2.rebuild(item.skus, mappings())
    check("fixed 模式下统一 999", all(i.quantity == 999 for i in plan2.items),
          str([i.quantity for i in plan2.items]))


def test_unmapped_not_dropped() -> None:
    print("\n[8] 未映射维度必须记录，不许静默丢弃（验收第 6 条）")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")))
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "10"),
        Sku("s2", {"1627207": "44467701705", "999999": "88888"}, "20"),  # 未知维度
        Sku("s3", {"1627207": "999", "4054020": "43502"}, "30"),          # 未知值
    ])
    plan = planner.rebuild(item.skus, mappings())
    check("只成功 1 个", len(plan.items) == 1, str(len(plan.items)))
    check("跳过 2 个", len(plan.skipped) == 2, str(len(plan.skipped)))
    check("跳过的都带 src_sku_id",
          all(s.get("src_sku_id") for s in plan.skipped), str(plan.skipped))
    check("原因都是 SKU_DIMENSION_UNMAPPED",
          all(s["reason"] == MappingError.SKU_DIMENSION_UNMAPPED.value
              for s in plan.skipped), str([s["reason"] for s in plan.skipped]))
    check("状态 PENDING（有跳过项）", plan.status is MappingStatus.PENDING,
          plan.status.value)


def test_conflict_not_merged() -> None:
    print("\n[9] 组合冲突不许合并（验收第 6 条）")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")))
    # 两个不同源 SKU 映射到同一个目标组合
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "10"),
        Sku("s2", {"1627207": "44467701705", "4054020": "43502"}, "20"),
    ])
    plan = planner.rebuild(item.skus, mappings())
    check("只保留 1 个", len(plan.items) == 1, str(len(plan.items)))
    check("另 1 个进 skipped", len(plan.skipped) == 1, str(len(plan.skipped)))
    check("原因是 SKU_COMBINATION_CONFLICT",
          plan.skipped[0]["reason"] == MappingError.SKU_COMBINATION_CONFLICT.value,
          plan.skipped[0]["reason"])
    check("冲突详情指出对方 sku_id",
          "s1" in plan.skipped[0]["detail"], plan.skipped[0]["detail"])


def test_limit_exceeded() -> None:
    print("\n[10] SKU 超限 → SKU_LIMIT_EXCEEDED，且**不截断**（验收第 6 条）")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")), max_sku=2)
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "10"),
        Sku("s2", {"1627207": "44467701705", "4054020": "15295387"}, "20"),
        Sku("s3", {"1627207": "44467701706", "4054020": "43502"}, "30"),
    ])
    plan = planner.rebuild(item.skus, mappings())
    check("状态 ERROR", plan.status is MappingStatus.ERROR, plan.status.value)
    check("错误码 SKU_LIMIT_EXCEEDED",
          plan.error is MappingError.SKU_LIMIT_EXCEEDED, str(plan.error))
    check("items 为空（不截断，不输出部分 SKU）", plan.items == [],
          str(len(plan.items)))
    check("超限情况写进 skipped",
          any(s["reason"] == MappingError.SKU_LIMIT_EXCEEDED.value
              for s in plan.skipped), str([s["reason"] for s in plan.skipped]))
    check("detail 说明是整体拒绝而非截断",
          "不截断" in (plan.detail or "") or "整体拒绝" in (plan.detail or ""),
          plan.detail or "")


def test_price_error_in_sku() -> None:
    print("\n[11] 单个 SKU 价格非法 → 跳过并记录")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")))
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "10"),
        Sku("s2", {"1627207": "44467701706", "4054020": "43502"}, None),
    ])
    plan = planner.rebuild(item.skus, mappings())
    check("只成功 1 个", len(plan.items) == 1, str(len(plan.items)))
    check("另 1 个原因是 PRICE_INVALID",
          plan.skipped[0]["reason"] == MappingError.PRICE_INVALID.value,
          plan.skipped[0]["reason"])


def test_all_failed() -> None:
    print("\n[12] 全部失败 → ERROR 且说明原因")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")))
    item = Item([Sku("s1", {"999999": "1"}, "10")])
    plan = planner.rebuild(item.skus, mappings())
    check("items 为空", plan.items == [])
    check("状态 ERROR", plan.status is MappingStatus.ERROR)
    check("skipped 有记录", len(plan.skipped) == 1, str(len(plan.skipped)))


def test_passthrough_same_platform() -> None:
    print("\n[13] 同平台直通（不给映射表）")
    node = switch_node()
    planner = SkuPlanner(node, PriceEngine(PriceConfig(mode="copy")))
    item = Item([
        Sku("s1", {"1627207": "44467701705", "4054020": "43502"}, "10"),
    ])
    plan = planner.rebuild(item.skus, prop_mappings=None)
    check("直通成功", len(plan.items) == 1, str(len(plan.items)))
    check("props 串保留原 pid/vid",
          plan.items[0].props == "1627207:44467701705;4054020:43502",
          plan.items[0].props)


def main() -> int:
    test_decimal()
    test_price_basic()
    test_price_rounding()
    test_price_invalid()
    test_price_clamp()
    test_rebuild_basic()
    test_stock_zero()
    test_unmapped_not_dropped()
    test_conflict_not_merged()
    test_limit_exceeded()
    test_price_error_in_sku()
    test_all_failed()
    test_passthrough_same_platform()
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
