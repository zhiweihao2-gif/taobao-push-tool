"""T2 测试：类目树 Provider 来源链。

跑法::

    python tests/test_category_provider.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalize.category_models import MappingError  # noqa: E402
from src.normalize.category_provider import (CategoryTreeError,  # noqa: E402
                                             CategoryTreeService,
                                             LocalJsonProvider,
                                             OpenApiProvider, PageProvider,
                                             dump_tree)

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


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="ct_test_"))


def test_local_ok() -> None:
    print("[1] 本地 JSON 正常加载")
    p = LocalJsonProvider(EXAMPLE)
    check("available", p.available())
    tree = p.load("taobao")
    check("platform", tree.platform == "taobao", tree.platform)
    check("source 标记", tree.source == "local_json", tree.source)
    check("节点数 >= 4", len(tree.nodes) >= 4, str(len(tree.nodes)))
    leaves = tree.leaves()
    check("叶类目 3 个", len(leaves) == 3, str(len(leaves)))
    check("非叶类目被正确标记", len([n for n in tree.nodes if not n.is_leaf]) == 1)

    sw = tree.by_cid("50023905")
    check("能按 cid 取到交换机", sw is not None and sw.name == "交换机")
    check("交换机有 5 个属性", len(sw.props) == 5, str(len(sw.props)))
    brand = next(p for p in sw.props if p.pid == "20000")
    check("品牌是 required + brand 类型",
          brand.required and brand.type == "brand", f"{brand.required} {brand.type}")
    color = next(p for p in sw.props if p.pid == "1627207")
    check("颜色分类是销售属性", color.is_sale)
    check("属性值别名可用",
          color.value_by_name("百兆") is not None
          and color.value_by_name("百兆").vid == "44467701705")
    check("路径文本", sw.path_text.endswith("交换机"), sw.path_text)


def test_missing_file() -> None:
    print("\n[2] 文件不存在")
    p = LocalJsonProvider(tmpdir() / "nope.json")
    check("available 为 False", not p.available())
    raised = False
    try:
        p.load("taobao")
    except CategoryTreeError as e:
        raised = True
        check("错误码是 CATEGORY_TREE_UNAVAILABLE",
              e.code is MappingError.CATEGORY_TREE_UNAVAILABLE, e.code.value)
    check("抛异常", raised)


def test_invalid_json() -> None:
    print("\n[3] 非法 JSON → INVALID_CATEGORY_TREE")
    d = tmpdir()

    cases = {
        "语法错误": "{ not json",
        "顶层不是对象": "[]",
        "nodes 缺失": '{"platform":"taobao"}',
        "nodes 为空": '{"platform":"taobao","nodes":[]}',
        "节点缺 cid": '{"nodes":[{"name":"x"}]}',
        "节点缺 name": '{"nodes":[{"cid":"1"}]}',
        "节点不是对象": '{"nodes":["x"]}',
    }
    for label, content in cases.items():
        f = d / f"{abs(hash(label))}.json"
        f.write_text(content, encoding="utf-8")
        p = LocalJsonProvider(f)
        try:
            p.load("taobao")
            check(f"{label} 应当失败", False)
        except CategoryTreeError as e:
            check(f"{label} → INVALID_CATEGORY_TREE",
                  e.code is MappingError.INVALID_CATEGORY_TREE, e.code.value)


def test_chain_fallback() -> None:
    print("\n[4] 来源链按顺序回退")
    d = tmpdir()
    missing = LocalJsonProvider(d / "nope.json")
    ok = LocalJsonProvider(EXAMPLE)
    svc = CategoryTreeService([missing, ok])
    tree = svc.load("taobao")
    check("跳过缺失的来源，用第二个", tree.source == "local_json")
    check("拿到叶类目", len(tree.leaves()) == 3)

    print("\n[5] 全部来源失败")
    svc2 = CategoryTreeService([LocalJsonProvider(d / "a.json"),
                                OpenApiProvider("", "", "")])
    raised = False
    try:
        svc2.load("taobao")
    except CategoryTreeError as e:
        raised = True
        check("错误码 CATEGORY_TREE_UNAVAILABLE",
              e.code is MappingError.CATEGORY_TREE_UNAVAILABLE, e.code.value)
        check("错误信息列出各来源原因", "local_json" in e.detail, e.detail[:60])
    check("抛异常", raised)
    check("load_or_none 返回 None", svc2.load_or_none("taobao") is None)


def test_invalid_not_masked() -> None:
    print("\n[6] 非法 JSON 不被后续来源掩盖")
    d = tmpdir()
    bad = d / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    svc = CategoryTreeService([LocalJsonProvider(bad), LocalJsonProvider(EXAMPLE)])
    try:
        svc.load("taobao")
        check("非法 JSON 应当中断来源链", False)
    except CategoryTreeError as e:
        check("直接抛 INVALID_CATEGORY_TREE 而不是回退",
              e.code is MappingError.INVALID_CATEGORY_TREE, e.code.value)


def test_unavailable_providers() -> None:
    print("\n[7] 无资质的来源正确标记为不可用")
    check("OpenApi 无凭据 → available False", not OpenApiProvider("", "", "").available())
    check("OpenApi 有凭据 → available True",
          OpenApiProvider("k", "s", "t").available())
    check("Page 默认关闭", not PageProvider().available())
    check("Page 显式开启", PageProvider(True).available())

    raised = False
    try:
        OpenApiProvider("k", "s", "t").load("taobao")
    except CategoryTreeError as e:
        raised = True
        check("OpenApi 占位实现明确报未实现",
              e.code is MappingError.CATEGORY_TREE_UNAVAILABLE)
    check("OpenApi 抛异常而非返回假树", raised)


def test_dump_roundtrip() -> None:
    print("\n[8] dump → load 往返")
    d = tmpdir()
    src = LocalJsonProvider(EXAMPLE).load("taobao")
    out = dump_tree(src, d / "roundtrip.json")
    back = LocalJsonProvider(out).load("taobao")
    check("节点数一致", len(back.nodes) == len(src.nodes))
    check("叶类目数一致", len(back.leaves()) == len(src.leaves()))
    a = src.by_cid("50023905")
    b = back.by_cid("50023905")
    check("属性数一致", len(a.props) == len(b.props))
    check("属性值别名保留",
          b.props[1].values[0].aliases == a.props[1].values[0].aliases)


def test_from_settings_default() -> None:
    print("\n[9] 默认配置（无 category_tree 段）")
    svc = CategoryTreeService.from_settings()
    check("默认至少有一个 provider", len(svc.providers) >= 1, str(len(svc.providers)))
    check("默认首选 local_json",
          svc.providers[0].kind == "local_json", svc.providers[0].kind)


def main() -> int:
    test_local_ok()
    test_missing_file()
    test_invalid_json()
    test_chain_fallback()
    test_invalid_not_masked()
    test_unavailable_providers()
    test_dump_roundtrip()
    test_from_settings_default()
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
