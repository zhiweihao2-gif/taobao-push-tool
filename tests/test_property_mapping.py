# T4 测试：属性 / 品牌 / 必填项映射。

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalize.category_models import (MappingError, MappingStatus,  # noqa: E402
                                           PropMeta, PropValueMeta)
from src.normalize.category_provider import LocalJsonProvider  # noqa: E402
from src.normalize.property_resolver import (MappingResources,  # noqa: E402
                                             PropertyResolver, build_props,
                                             missing_required, parse_props,
                                             resolve_properties)

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


class V:
    def __init__(self, vid, name):
        self.vid, self.name = vid, name


class SP:
    def __init__(self, pid, name, values):
        self.pid, self.name = pid, name
        self.values = [V(v, n) for v, n in values]


class P:
    def __init__(self, name, value):
        self.name, self.value = name, value


class Item:
    def __init__(self, sale_props=None, params=None, title="", platform="taobao"):
        self.sale_props = sale_props or []
        self.params = params or []
        self.title = title
        self.platform = platform


def switch_node():
    return LocalJsonProvider(EXAMPLE).load("taobao").by_cid("50023905")


def dress_node():
    return LocalJsonProvider(EXAMPLE).load("taobao").by_cid("50010850")


# ---------------------------------------------------------------- 属性串


def test_props_string() -> None:
    print("[1] 属性串解析与构造")
    p = parse_props("1627207:44467701705;4054020:43502")
    check("解析 2 个", len(p) == 2, str(p))
    check("值正确", p["1627207"] == "44467701705", str(p))
    check("空串", parse_props("") == {})
    check("None", parse_props(None) == {})
    check("忽略非法段", parse_props("a:b;;c") == {"a": "b"})
    check("构造按 pid 排序",
          build_props({"b": "2", "a": "1"}) == "a:1;b:2",
          build_props({"b": "2", "a": "1"}))
    check("往返一致",
          build_props(parse_props("x:1;y:2")) == "x:1;y:2")


def test_missing_required() -> None:
    print("\n[2] 必填校验按完整 pid（验收第 5 条）")
    check("pid 12 有值 → 不缺", missing_required("12:99", ["12"]) == [])
    check("只有 pid 112 → pid 12 仍缺",
          missing_required("112:99", ["12"]) == ["12"],
          str(missing_required("112:99", ["12"])))
    check("112 有值 → pid 112 不缺",
          missing_required("112:99", ["112"]) == [])
    check("多个缺失",
          missing_required("1:1", ["1", "2", "3"]) == ["2", "3"])
    check("dict 输入", missing_required({"1": "x"}, ["1", "2"]) == ["2"])
    check("空载荷全缺", missing_required("", ["1"]) == ["1"])
    check("无必填 → 空", missing_required("1:1", []) == [])


# ---------------------------------------------------------------- 属性映射


def test_find_target_prop() -> None:
    print("\n[3] 目标属性定位")
    r = PropertyResolver(switch_node())
    p, basis = r.find_target_prop("1627207", "颜色分类")
    # property_map.yaml 里有 taobao->taobao 的显式映射，所以走 explicit 分支（优先级最高）
    check("按显式映射或 pid 命中", p is not None and basis in ("explicit", "pid"), basis)
    check("pid 正确", p.pid == "1627207")

    p2, basis2 = r.find_target_prop("999999", "端口总数")
    check("pid 不存在时按名称命中", p2 is not None and basis2 == "name", basis2)
    check("名称命中到 4054020", p2.pid == "4054020")

    p3, basis3 = r.find_target_prop("888888", "完全不存在的属性")
    check("找不到时返回 None", p3 is None, basis3)


def test_value_mapping() -> None:
    print("\n[4] 属性值映射四级优先")
    node = switch_node()
    r = PropertyResolver(node)
    color = next(p for p in node.props if p.pid == "1627207")

    m1 = r.map_value(color, "44467701705", "随便什么")
    check("精确 vid 优先", m1.target_vid == "44467701705" and m1.matched_by == "vid",
          f"{m1.target_vid} {m1.matched_by}")

    m2 = r.map_value(color, None, "【千兆非POE交换机】")
    check("精确名称命中", m2.target_vid == "44467701706", str(m2.target_vid))
    check("matched_by=name", m2.matched_by == "name", m2.matched_by)

    m3 = r.map_value(color, None, "千兆")
    check("别名命中（属性自带 aliases）",
          m3.target_vid == "44467701706", str(m3.target_vid))
    check("matched_by=alias", m3.matched_by == "alias", m3.matched_by)

    m4 = r.map_value(color, None, "根本对不上")
    check("对不上 → target_vid None", m4.target_vid is None, str(m4.target_vid))
    check("matched_by=none", m4.matched_by == "none", m4.matched_by)


def test_synonyms_file() -> None:
    print("\n[5] 显式同义词表（synonyms.yaml）")
    node = dress_node()
    r = PropertyResolver(node)
    mat = next(p for p in node.props if p.pid == "20010")
    m = r.map_value(mat, None, "纯棉")
    check("纯棉 → 棉", m.target_vid == "mat_cotton", str(m.target_vid))
    check("matched_by=alias", m.matched_by == "alias", m.matched_by)

    port = next(p for p in switch_node().props if p.pid == "4054020")
    r2 = PropertyResolver(switch_node())
    m2 = r2.map_value(port, None, "8口")
    check("8口 → 8个", m2.target_vid == "15295387", str(m2.target_vid))


def test_ambiguous() -> None:
    print("\n[6] 歧义挂起，不瞎选")
    node = switch_node()
    # 构造两个对同一输入相似度完全相同的值（ratio 都是 0.875），差距 0 → 判歧义
    node.props.append(PropMeta(
        pid="90001", name="测试歧义", is_sale=True,
        values=[PropValueMeta(vid="a1", name="ABCDEFGH"),
                PropValueMeta(vid="a2", name="ABCDEFGI")]))
    r = PropertyResolver(node)
    prop = next(p for p in node.props if p.pid == "90001")

    m = r.map_value(prop, None, "ABCDEFGJ")
    check("同分时不返回 target_vid", m.target_vid is None, str(m.target_vid))
    check("matched_by=fuzzy 且 score 有值",
          m.matched_by == "fuzzy" and m.score > 0, f"{m.matched_by} {m.score}")

    # 差距够大时正常命中
    node2 = switch_node()
    node2.props.append(PropMeta(
        pid="90002", name="测试明确", is_sale=True,
        values=[PropValueMeta(vid="b1", name="ABCDEFGH"),
                PropValueMeta(vid="b2", name="ZZZZZZZZ")]))
    r2 = PropertyResolver(node2)
    prop2 = next(p for p in node2.props if p.pid == "90002")
    m2 = r2.map_value(prop2, None, "ABCDEFG")
    check("差距够大时正常命中", m2.target_vid == "b1", str(m2.target_vid))


def test_resolve_sale_props() -> None:
    print("\n[7] 销售属性整体解析")
    item = Item(sale_props=[
        SP("1627207", "颜色分类", [("44467701705", "【百兆非POE交换机】"),
                                   ("44467701706", "【千兆非POE交换机】")]),
        SP("4054020", "端口总数", [("43502", "5个"), ("15295387", "8个")]),
    ])
    res = resolve_properties(item, switch_node())
    check("2 个属性都映射上",
          all(m.target_pid for m in res.sale_props),
          str([(m.src_name, m.target_pid) for m in res.sale_props]))
    check("值全部命中",
          all(v.target_vid for m in res.sale_props for v in m.values))
    check("props 串含两个 pid",
          "1627207:" in res.props_string and "4054020:" in res.props_string,
          res.props_string)


# ---------------------------------------------------------------- 品牌


def test_brand_ok() -> None:
    print("\n[8] 品牌命中")
    item = Item(params=[P("品牌", "Dahua")])
    res = resolve_properties(item, switch_node())
    check("brand_id 有值", res.brand.brand_id == "brand_dahua", str(res.brand.brand_id))
    check("matched_by=exact", res.brand.matched_by == "exact", res.brand.matched_by)
    check("props 里含品牌 pid",
          "20000" in res.props, str(res.props))


def test_brand_alias() -> None:
    print("\n[9] 品牌别名命中")
    item = Item(params=[P("品牌", "大华")])
    res = resolve_properties(item, switch_node())
    check("大华 → brand_dahua", res.brand.brand_id == "brand_dahua",
          str(res.brand.brand_id))


def test_brand_missing() -> None:
    print("\n[10] 品牌未命中 → brand_id 必须为 None（验收第 4 条）")
    item = Item(params=[P("品牌", "某个不存在的牌子XYZ")])
    res = resolve_properties(item, switch_node())
    check("brand_id 是 None", res.brand.brand_id is None, str(res.brand.brand_id))
    check("状态 PENDING", res.brand.status is MappingStatus.PENDING,
          res.brand.status.value)
    check("错误码 BRAND_UNRESOLVED",
          res.brand.error is MappingError.BRAND_UNRESOLVED, str(res.brand.error))
    check("不许自造品牌 ID",
          res.brand.brand_id is None and "brand" not in str(res.brand.brand_id))
    check("props 里没有品牌 pid", "20000" not in res.props, str(res.props))


def test_brand_absent() -> None:
    print("\n[11] 商品没填品牌 + 品牌必填 → 报错")
    item = Item(params=[])
    res = resolve_properties(item, switch_node())
    check("src_brand 为空", res.brand.src_brand is None)
    check("required=True", res.brand.required is True, str(res.brand.required))
    check("错误码 BRAND_UNRESOLVED",
          res.brand.error is MappingError.BRAND_UNRESOLVED, str(res.brand.error))


# ---------------------------------------------------------------- 必填


def test_required_end_to_end() -> None:
    print("\n[12] 必填项端到端")
    # 只给颜色、不给端口 → 端口总数 required 应报缺
    item = Item(sale_props=[
        SP("1627207", "颜色分类", [("44467701705", "百兆")]),
    ], params=[P("品牌", "Dahua")])
    res = resolve_properties(item, switch_node())
    check("缺 4054020", "4054020" in res.required_missing,
          str(res.required_missing))
    check("pending_items 列出缺项",
          any("4054020" in x for x in res.pending_items()),
          str(res.pending_items()))

    # 全给齐
    item2 = Item(sale_props=[
        SP("1627207", "颜色分类", [("44467701705", "百兆")]),
        SP("4054020", "端口总数", [("43502", "5个")]),
    ], params=[P("品牌", "Dahua")])
    res2 = resolve_properties(item2, switch_node())
    check("全给齐时无缺失", res2.required_missing == [], str(res2.required_missing))


def main() -> int:
    test_props_string()
    test_missing_required()
    test_find_target_prop()
    test_value_mapping()
    test_synonyms_file()
    test_ambiguous()
    test_resolve_sale_props()
    test_brand_ok()
    test_brand_alias()
    test_brand_missing()
    test_brand_absent()
    test_required_end_to_end()
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
