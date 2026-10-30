"""T3 测试：类目候选匹配与指纹确认键。

跑法::

    python tests/test_category_match.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalize.category_match import (CategoryMatcher, CategoryResolver,  # noqa: E402
                                          MatchWeights, jaccard, ngrams)
from src.normalize.category_models import (MappingError, MappingStatus,  # noqa: E402
                                           build_fingerprint, normalize_text)
from src.normalize.category_provider import LocalJsonProvider  # noqa: E402

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


class MemStore:
    """内存版确认存储，验证契约用。"""

    def __init__(self):
        self.data: dict[str, dict] = {}

    def get(self, fp: str):
        return self.data.get(fp)

    def put(self, fp: str, record: dict):
        self.data[fp] = record


class FakeItem:
    def __init__(self, title, params=None, sale_props=None, platform="taobao", cid=""):
        self.title = title
        self.params = [type("P", (), {"name": n, "value": v})() for n, v in (params or [])]
        self.sale_props = [type("S", (), {"name": n})() for n in (sale_props or [])]
        self.platform = platform
        self.category = type("C", (), {"cid": cid})()


def tree():
    return LocalJsonProvider(EXAMPLE).load("taobao")


# ---------------------------------------------------------------- 工具


def test_ngrams() -> None:
    print("[1] 分词与相似度")
    a = ngrams("大华交换机")
    b = ngrams("交换机")
    check("2-gram 非空", len(a) > 0, str(a))
    check("重合度在 0~1", 0 <= jaccard(a, b) <= 1, str(jaccard(a, b)))
    check("完全相同 = 1.0", jaccard(a, ngrams("大华交换机")) == 1.0)
    check("空集 = 0", jaccard(set(), b) == 0.0)
    check("normalize 去标点空白",
          normalize_text(" 大华 / 交换机！ ") == "大华交换机",
          normalize_text(" 大华 / 交换机！ "))


def test_weights() -> None:
    print("\n[2] 权重归一化")
    w = MatchWeights(5, 3, 2)
    check("5/3/2 归一化为 0.5/0.3/0.2",
          abs(w.title - 0.5) < 1e-9 and abs(w.params - 0.3) < 1e-9
          and abs(w.sale_props - 0.2) < 1e-9, str(w.as_dict()))
    raised = False
    try:
        MatchWeights(0, 0, 0)
    except ValueError:
        raised = True
    check("全零权重被拒绝", raised)


# ---------------------------------------------------------------- 匹配


def test_match_switch() -> None:
    print("\n[3] 交换机商品 → 交换机类目")
    m = CategoryMatcher(tree())
    cands = m.match(
        title="大华监控百兆/千兆网线交换机DH-S1000C-8GT千兆8口即插即用包邮",
        params=[{"name": "品牌", "value": "Dahua"},
                {"name": "型号", "value": "1000"},
                {"name": "传输速度", "value": "1000Mbps"}],
        sale_prop_names=["颜色分类", "端口总数"],
    )
    check("有候选", len(cands) > 0, str(len(cands)))
    check("第一名是交换机", cands[0].cid == "50023905", cands[0].cid)
    check("分数 > 0.45", cands[0].score > 0.45, str(cands[0].score))
    check("第一名显著领先第二名",
          len(cands) == 1 or cands[0].score - cands[1].score > 0.2,
          str([(c.cid, c.score) for c in cands]))
    check("带打分依据", len(cands[0].reasons) > 0, str(cands[0].reasons))
    check("非叶类目不在候选里", all(c.cid != "16" for c in cands))


def test_match_dress() -> None:
    print("\n[4] 连衣裙商品 → 连衣裙类目")
    m = CategoryMatcher(tree())
    cands = m.match(
        title="夏季新款女士连衣裙 棉质 中长款",
        params=[{"name": "材质成分", "value": "棉"}],
        sale_prop_names=["颜色分类", "尺码"],
    )
    check("第一名是连衣裙", cands[0].cid == "50010850", cands[0].cid)
    check("第一名分数明显领先",
          len(cands) == 1 or cands[0].score - cands[1].score > 0.1,
          str([(c.cid, c.score) for c in cands]))


def test_empty_input() -> None:
    print("\n[5] 空输入 → 空列表（不做无根据猜测）")
    m = CategoryMatcher(tree())
    check("全空", m.match(None, None, None) == [])
    check("空字符串标题", m.match("", [], []) == [])
    check("只有空白", m.match("   ", [], []) == [])
    check("全 None", m.match(None, [], []) == [])
    check("空白参数不算输入", m.match("", [{"name": "", "value": ""}], []) == [])


def test_stability() -> None:
    print("\n[6] 排序稳定（同输入同输出）")
    m = CategoryMatcher(tree())
    args = ("交换机", [{"name": "端口总数", "value": "8个"}], ["端口总数"])
    r1 = m.match(*args)
    r2 = m.match(*args)
    check("两次结果完全一致",
          [(c.cid, c.score) for c in r1] == [(c.cid, c.score) for c in r2])
    if len(r1) > 1:
        keys = [(-c.score, c.cid) for c in r1]
        check("按 (-score, cid) 排序", keys == sorted(keys), str(keys))


def test_top_n_and_threshold() -> None:
    print("\n[7] Top-N 与阈值")
    m = CategoryMatcher(tree(), top_n=1)
    cands = m.match("交换机", [{"name": "品牌", "value": "Dahua"}], ["端口总数"])
    check("top_n=1 只返回 1 个", len(cands) == 1, str(len(cands)))

    m2 = CategoryMatcher(tree(), min_score=0.99)
    check("阈值过高 → 空列表", m2.match("交换机", [], []) == [])


# ---------------------------------------------------------------- 指纹


def test_fingerprint() -> None:
    print("\n[8] 指纹：顺序无关、内容敏感")
    p1 = [{"name": "品牌", "value": "Dahua"}, {"name": "端口", "value": "8个"}]
    p2 = [{"name": "端口", "value": "8个"}, {"name": "品牌", "value": "Dahua"}]
    f1 = build_fingerprint("大华 交换机", p1, ["颜色分类", "端口总数"], "taobao")
    f2 = build_fingerprint("大华交换机", p2, ["端口总数", "颜色分类"], "taobao")
    f3 = build_fingerprint("大华 交换机", [{"name": "品牌", "value": "Hikvision"}],
                           ["颜色分类", "端口总数"], "taobao")
    f4 = build_fingerprint("大华 交换机", p1, ["颜色分类", "端口总数"], "jd")
    check("参数/属性顺序无关", f1 == f2, f"{f1} vs {f2}")
    check("参数值不同 → 指纹不同", f1 != f3)
    check("平台不同 → 指纹不同", f1 != f4)
    check("指纹长度 32", len(f1) == 32, str(len(f1)))


# ---------------------------------------------------------------- 解析


def test_resolve_pending() -> None:
    print("\n[9] 无确认记录 → PENDING + 候选")
    t = tree()
    r = CategoryResolver(t, store=None)
    item = FakeItem("大华监控交换机8口千兆",
                    [("品牌", "Dahua"), ("传输速度", "1000Mbps")],
                    ["颜色分类", "端口总数"])
    res = r.resolve(item)
    check("状态 PENDING", res.status is MappingStatus.PENDING, res.status.value)
    check("有候选", len(res.candidates) > 0, str(len(res.candidates)))
    check("第一名是交换机", res.candidates[0].cid == "50023905")
    check("带 fingerprint", bool(res.fingerprint), str(res.fingerprint))
    check("不是 ok", not res.ok)


def test_resolve_confirmed() -> None:
    print("\n[10] 已确认 → OK 并复用")
    t = tree()
    store = MemStore()
    item = FakeItem("大华监控交换机8口千兆",
                    [("品牌", "Dahua")], ["端口总数"])
    r = CategoryResolver(t, store=store)
    fp = r.resolve(item).fingerprint
    store.put(fp, {"target_cid": "50023905", "target_path": ["网络设备", "交换机"]})

    res = r.resolve(item)
    check("状态 OK", res.status is MappingStatus.OK, res.status.value)
    check("target_cid 正确", res.target_cid == "50023905", str(res.target_cid))
    check("source=confirmed", res.source == "confirmed", res.source)
    check("ok 属性为真", res.ok)
    check("重新加载 store 后仍命中",
          CategoryResolver(t, store=store).resolve(item).ok)


def test_resolve_expired() -> None:
    print("\n[11] 已确认但目标 cid 失效 → EXPIRED，不自动命中")
    t = tree()
    store = MemStore()
    item = FakeItem("大华监控交换机8口千兆", [("品牌", "Dahua")], ["端口总数"])
    r = CategoryResolver(t, store=store)
    fp = r.resolve(item).fingerprint
    store.put(fp, {"target_cid": "99999999", "target_path": ["已下线类目"]})

    res = r.resolve(item)
    check("状态 EXPIRED", res.status is MappingStatus.EXPIRED, res.status.value)
    check("错误码 MAPPING_EXPIRED",
          res.error is MappingError.MAPPING_EXPIRED, str(res.error))
    check("不返回 target_cid", res.target_cid is None, str(res.target_cid))
    check("不是 ok", not res.ok)
    check("仍给出候选供重新确认", len(res.candidates) > 0, str(len(res.candidates)))


def test_resolve_not_matched() -> None:
    print("\n[12] 无候选 → ERROR(CATEGORY_NOT_MATCHED)")
    r = CategoryResolver(tree())
    res = r.resolve(FakeItem("", [], []))
    check("状态 ERROR", res.status is MappingStatus.ERROR, res.status.value)
    check("错误码 CATEGORY_NOT_MATCHED",
          res.error is MappingError.CATEGORY_NOT_MATCHED, str(res.error))


def test_empty_cid_item() -> None:
    print("\n[13] 验收第 2 条：category.cid 为空的商品也能出候选")
    r = CategoryResolver(tree())
    item = FakeItem("女士连衣裙夏季新款", [("材质成分", "棉")],
                    ["颜色分类", "尺码"], cid="")
    check("源 cid 确实为空", item.category.cid == "")
    res = r.resolve(item)
    check("仍然给出候选", len(res.candidates) > 0, str(len(res.candidates)))
    check("第一名是连衣裙", res.candidates[0].cid == "50010850")


def main() -> int:
    test_ngrams()
    test_weights()
    test_match_switch()
    test_match_dress()
    test_empty_input()
    test_stability()
    test_top_n_and_threshold()
    test_fingerprint()
    test_resolve_pending()
    test_resolve_confirmed()
    test_resolve_expired()
    test_resolve_not_matched()
    test_empty_cid_item()
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
