"""类目树工具：校验 / 统计 / 从 CSV 导入 / 生成模板。

为什么要这个：类目树是 M2 的匹配目标，只有 3 个类目没法用。
但真实类目树几百上千个，手工写 JSON 不现实，所以提供 CSV 导入。

用法::

    python scripts/category_tree_tool.py validate                 # 校验现有树
    python scripts/category_tree_tool.py stats                    # 看统计
    python scripts/category_tree_tool.py template                 # 生成 CSV 模板
    python scripts/category_tree_tool.py import tree.csv          # 从 CSV 导入

CSV 格式（表头必须有）::

    cid,name,path,parent_cid,is_leaf,props
    50023905,交换机,网络设备>网络设备>交换机,50023904,true,"品牌:B:required;端口总数:S:required;颜色分类:S"
    50010850,连衣裙,女装/女士精品>连衣裙,16,true,"品牌:B:required;尺码:S:required"

``props`` 字段用分号分隔，每项 ``名称:类型[:required]``：
- 类型 ``S`` = 销售属性（参与生成 SKU）
- 类型 ``B`` = 品牌属性
- 类型 ``K`` = 关键属性
- 类型 ``N`` = 普通参数
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalize.category_models import (CategoryNode, CategoryTree,  # noqa: E402
                                           PropMeta, PropValueMeta)
from src.normalize.category_provider import (CategoryTreeError,  # noqa: E402
                                             LocalJsonProvider, dump_tree)

DEFAULT_TREE = ROOT / "data/mapping/category_tree.taobao.json"
EXAMPLE_TREE = ROOT / "data/mapping/category_tree.example.json"

TYPE_MAP = {"S": "select", "B": "brand", "K": "select", "N": "select"}


def parse_props(spec: str) -> list[PropMeta]:
    """``品牌:B:required;端口总数:S`` → [PropMeta(...)]"""
    out: list[PropMeta] = []
    if not spec:
        return out
    for i, part in enumerate(spec.split(";")):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split(":")]
        name = bits[0]
        code = bits[1].upper() if len(bits) > 1 else "N"
        required = len(bits) > 2 and bits[2].lower() in ("required", "1", "true", "y")
        out.append(PropMeta(
            pid=f"csv_{i}_{abs(hash(name)) % 100000}",
            name=name,
            required=required,
            is_sale=(code == "S"),
            is_key=(code in ("B", "K")),
            type=TYPE_MAP.get(code, "select"),
            values=[],
        ))
    return out


def load_tree(path: Path) -> CategoryTree:
    return LocalJsonProvider(path).load("taobao")


# ================================================================ 命令


def cmd_validate(args) -> int:
    path = Path(args.path)
    print(f"校验 {path}")
    try:
        tree = load_tree(path)
    except CategoryTreeError as e:
        print(f"  × {e.code.value}: {e.detail}")
        return 1

    errors: list[str] = []
    warns: list[str] = []

    # 1. cid 唯一
    seen: dict[str, int] = {}
    for n in tree.nodes:
        seen[n.cid] = seen.get(n.cid, 0) + 1
    dup = [cid for cid, c in seen.items() if c > 1]
    if dup:
        errors.append(f"重复 cid: {dup}")

    # 2. 父节点存在（根节点 parent 可空）
    cids = set(seen)
    for n in tree.nodes:
        if n.parent_cid and n.parent_cid not in cids:
            warns.append(f"{n.cid}({n.name}) 的父节点 {n.parent_cid} 不在树里")

    # 3. 叶类目必须有属性
    for n in tree.nodes:
        if n.is_leaf and not n.props:
            warns.append(f"叶类目 {n.cid}({n.name}) 没有任何属性，无法做属性映射")

    # 4. 叶类目至少一个销售属性（否则生成不了 SKU）
    for n in tree.nodes:
        if n.is_leaf and n.props and not any(p.is_sale for p in n.props):
            warns.append(f"叶类目 {n.cid}({n.name}) 没有销售属性")

    # 5. 属性 pid 唯一
    for n in tree.nodes:
        pids = [p.pid for p in n.props]
        if len(pids) != len(set(pids)):
            errors.append(f"类目 {n.cid} 有重复 pid")

    print(f"  节点 {len(tree.nodes)}，叶类目 {len(tree.leaves())}")
    if errors:
        print(f"\n  ✗ {len(errors)} 个错误：")
        for e in errors:
            print(f"    - {e}")
    if warns:
        print(f"\n  ⚠ {len(warns)} 个警告：")
        for w in warns[:20]:
            print(f"    - {w}")
        if len(warns) > 20:
            print(f"    …还有 {len(warns) - 20} 条")
    if not errors and not warns:
        print("\n  ✓ 完全通过")
    return 0 if not errors else 1


def cmd_stats(args) -> int:
    path = Path(args.path)
    tree = load_tree(path)
    print(f"{path}")
    print(f"  平台      : {tree.platform}")
    print(f"  来源      : {tree.source}")
    print(f"  节点总数  : {len(tree.nodes)}")
    print(f"  叶类目    : {len(tree.leaves())}")
    total_props = sum(len(n.props) for n in tree.nodes)
    total_values = sum(len(p.values) for n in tree.nodes for p in n.props)
    print(f"  属性总数  : {total_props}")
    print(f"  属性值总数: {total_values}")
    print()
    print(f"  {'cid':<14}{'类目':<30}{'属性':>6}{'销售':>6}{'值':>7}")
    print("  " + "-" * 66)
    for n in tree.leaves():
        sale = len([p for p in n.props if p.is_sale])
        vals = sum(len(p.values) for p in n.props)
        print(f"  {n.cid:<14}{n.path_text[:28]:<30}{len(n.props):>6}{sale:>6}{vals:>7}")
    return 0


def cmd_template(args) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["cid", "name", "path", "parent_cid", "is_leaf", "props"])
        w.writerow(["50023905", "交换机", "网络设备>网络设备>交换机", "50023904",
                    "true", "品牌:B:required;端口总数:S:required;颜色分类:S;传输速度:N"])
        w.writerow(["50010850", "连衣裙", "女装/女士精品>连衣裙", "16",
                    "true", "品牌:B:required;颜色分类:S:required;尺码:S:required;材质成分:K"])
    print(f"模板已生成 → {out}")
    print()
    print("  props 字段格式：名称:类型[:required]，分号分隔")
    print("    S = 销售属性（生成 SKU）  B = 品牌  K = 关键属性  N = 普通参数")
    print("  path 用 > 分隔层级")
    print("  填好后跑：python scripts/category_tree_tool.py import <文件>")
    return 0


def cmd_import(args) -> int:
    src = Path(args.csv)
    if not src.exists():
        print(f"× 文件不存在: {src}")
        return 1

    # utf-8-sig 兼容 Excel 导出的 BOM
    with src.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("× CSV 是空的")
        return 1

    nodes: list[CategoryNode] = []
    skipped: list[str] = []
    for i, r in enumerate(rows, 2):
        cid = (r.get("cid") or "").strip()
        name = (r.get("name") or "").strip()
        if not cid or not name:
            skipped.append(f"第 {i} 行缺 cid 或 name")
            continue
        path_raw = (r.get("path") or name).strip()
        path = [p.strip() for p in path_raw.replace("/", ">").split(">") if p.strip()]
        is_leaf = str(r.get("is_leaf", "true")).strip().lower() not in ("false", "0", "no", "n")
        nodes.append(CategoryNode(
            cid=cid, name=name, path=path,
            parent_cid=(r.get("parent_cid") or "").strip() or None,
            is_leaf=is_leaf,
            props=parse_props(r.get("props") or ""),
        ))

    if not nodes:
        print("× 没有解析出任何节点")
        for s in skipped:
            print(f"  - {s}")
        return 1

    tree = CategoryTree(platform=args.platform, source="csv_import", nodes=nodes)
    out = Path(args.out)
    dump_tree(tree, out)

    print(f"✓ 导入 {len(nodes)} 个节点 → {out}")
    print(f"  叶类目 {len(tree.leaves())}")
    if skipped:
        print(f"  ⚠ 跳过 {len(skipped)} 行：")
        for s in skipped[:10]:
            print(f"    - {s}")
    print()
    print("  下一步：校验一下")
    print(f"    python scripts/category_tree_tool.py validate --path {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="类目树工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate", help="校验结构")
    p.add_argument("--path", default=str(DEFAULT_TREE), help="类目树 JSON 路径")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("stats", help="统计")
    p.add_argument("--path", default=str(DEFAULT_TREE), help="类目树 JSON 路径")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("template", help="生成 CSV 模板")
    p.add_argument("--out", default="data/mapping/category_tree_template.csv")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("import", help="从 CSV 导入")
    p.add_argument("csv")
    p.add_argument("--out", default=str(DEFAULT_TREE))
    p.add_argument("--platform", default="taobao")
    p.set_defaults(func=cmd_import)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
