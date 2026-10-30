"""T6 测试：人工确认闭环、持久化与恢复。

跑法::

    python tests/test_mapping_tasks.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalize.category_match import CategoryResolver  # noqa: E402
from src.normalize.category_models import (CategoryCandidate,  # noqa: E402
                                           MappingError, MappingStatus,
                                           build_fingerprint)
from src.normalize.category_provider import LocalJsonProvider  # noqa: E402
from src.normalize.mapping_tasks import (ST_CONFIRMED, ST_PENDING,  # noqa: E402
                                         ST_REJECTED, MappingTaskStore,
                                         atomic_write_yaml)

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


def env():
    d = Path(tempfile.mkdtemp(prefix="mt_test_"))
    return d / "app.db", d / "category_map.yaml", d


def cands():
    return [CategoryCandidate(cid="50023905", path=["网络设备", "交换机"], score=0.59),
            CategoryCandidate(cid="50010850", path=["女装", "连衣裙"], score=0.25)]


class Item:
    def __init__(self, title, params=None, sale_props=None, platform="taobao"):
        self.title = title
        self.params = [type("P", (), {"name": n, "value": v})() for n, v in (params or [])]
        self.sale_props = [type("S", (), {"name": n})() for n in (sale_props or [])]
        self.platform = platform
        self.category = type("C", (), {"cid": ""})()


# ---------------------------------------------------------------- 原子写


def test_atomic_write() -> None:
    print("[1] YAML 原子写")
    d = Path(tempfile.mkdtemp(prefix="aw_"))
    f = d / "m.yaml"
    atomic_write_yaml(f, {"a": 1})
    check("首次写入成功", f.exists() and yaml.safe_load(f.read_text("utf-8"))["a"] == 1)
    before = f.read_text(encoding="utf-8")

    def boom(tmp: Path, text: str) -> None:
        raise OSError("模拟磁盘满")

    raised = False
    try:
        atomic_write_yaml(f, {"a": 2, "b": 3}, writer=boom)
    except OSError:
        raised = True
    check("写入失败抛异常", raised)
    check("原文件内容不变（验收第 9 条）",
          f.read_text(encoding="utf-8") == before, f.read_text(encoding="utf-8")[:40])
    check("失败后没有残留临时文件",
          not list(d.glob(".map_*.tmp")), str(list(d.glob("*"))))


# ---------------------------------------------------------------- 挂起


def test_pending() -> None:
    print("\n[2] 挂起任务")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp001", "item1", "taobao", "taobao", cands(), "需确认")
    check("任务 id 非空", bool(t.task_id), t.task_id)
    check("状态 pending", t.status == ST_PENDING, t.status)
    check("候选保存完整", len(t.candidates) == 2, str(len(t.candidates)))
    check("候选分数保留", t.candidates[0].score == 0.59, str(t.candidates[0].score))
    check("list_pending 能看到", len(st.list_pending()) == 1)

    t2 = st.submit_pending("fp001", "item1", "taobao", "taobao", cands())
    check("同指纹重复挂起不新建任务", t2.task_id == t.task_id, f"{t.task_id} vs {t2.task_id}")
    check("任务表仍只有 1 条", len(st.list_pending()) == 1)


def test_confirm_ok() -> None:
    print("\n[3] 确认 → 写 YAML → 复用")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp002", "item1", "taobao", "taobao", cands())

    ok, msg = st.confirm(t.task_id, "50023905")
    check("确认成功", ok, msg)
    check("YAML 已生成", yml.exists())
    data = yaml.safe_load(yml.read_text(encoding="utf-8"))
    rec = data["taobao->taobao"]["_by_fingerprint"]["fp002"]
    check("YAML 记录 target_cid", rec["target_cid"] == "50023905", str(rec))
    check("YAML 记录 target_path", rec["target_path"] == ["网络设备", "交换机"],
          str(rec.get("target_path")))

    task = st.get_task(t.task_id)
    check("任务状态变 confirmed", task.status == ST_CONFIRMED, task.status)
    check("list_pending 清空", len(st.list_pending()) == 0)

    # 重新加载 store（模拟重启），相同指纹应命中
    st2 = MappingTaskStore(db, yml)
    got = st2.get_confirmed("fp002")
    check("重新加载后仍能取到", got is not None and got["target_cid"] == "50023905",
          str(got))


def test_confirm_validation() -> None:
    print("\n[4] 确认的边界校验")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp003", "i", "taobao", "taobao", cands())

    ok, msg = st.confirm("不存在的任务", "50023905")
    check("任务不存在 → 失败", not ok, msg)

    ok, msg = st.confirm(t.task_id, "")
    check("target_cid 为空 → 失败", not ok, msg)

    ok, msg = st.confirm(t.task_id, "99999999")
    check("目标不在候选里 → 失败（过期候选可观察，验收第 8 条）", not ok, msg)
    check("失败信息含候选列表", "50023905" in msg, msg)

    ok, msg = st.confirm(t.task_id, "50023905")
    check("合法确认成功", ok, msg)
    ok2, msg2 = st.confirm(t.task_id, "50010850")
    check("已确认的任务不能再确认", not ok2, msg2)


def test_confirm_write_failure() -> None:
    print("\n[5] YAML 写失败 → 任务仍 pending（验收第 9 条）")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp004", "i", "taobao", "taobao", cands())
    st.confirm(t.task_id, "50023905")
    before = yml.read_text(encoding="utf-8")

    # 换一个会失败的 writer
    st2 = MappingTaskStore(db, yml, writer=lambda tmp, text: (_ for _ in ()).throw(OSError("boom")))
    t2 = st2.submit_pending("fp005", "i2", "taobao", "taobao", cands())
    ok, msg = st2.confirm(t2.task_id, "50010850")
    check("确认失败", not ok, msg)
    check("错误码 MAPPING_STORE_WRITE_FAILED",
          MappingError.MAPPING_STORE_WRITE_FAILED.value in msg, msg)
    check("原 YAML 内容不变", yml.read_text(encoding="utf-8") == before)
    check("任务仍是 pending", st2.get_task(t2.task_id).status == ST_PENDING,
          st2.get_task(t2.task_id).status)


def test_idempotent() -> None:
    print("\n[6] 重复确认不产生重复记录（验收第 9 条）")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp006", "i", "taobao", "taobao", cands())
    st.confirm(t.task_id, "50023905")

    # 再开一个任务，同指纹，确认同一个目标
    t2 = st.submit_pending("fp006", "i", "taobao", "taobao", cands())
    if t2.task_id == t.task_id:
        # 任务已 confirmed，submit 会复用 pending 查询失败 → 新建
        pass
    st.confirm(t2.task_id, "50023905")

    data = yaml.safe_load(yml.read_text(encoding="utf-8"))
    by_fp = data["taobao->taobao"]["_by_fingerprint"]
    check("同指纹只有 1 条记录", len([k for k in by_fp if k == "fp006"]) == 1, str(by_fp))
    check("记录内容正确", by_fp["fp006"]["target_cid"] == "50023905")


def test_conflict() -> None:
    print("\n[7] 冲突确认可观察")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp007", "i", "taobao", "taobao", cands())
    st.confirm(t.task_id, "50023905")

    conflict = st.detect_conflict("fp007", "50010850")
    check("检测到冲突", conflict is not None, str(conflict))
    check("冲突信息含 MAPPING_CONFLICT",
          MappingError.MAPPING_CONFLICT.value in (conflict or ""), str(conflict))
    check("同目标不算冲突", st.detect_conflict("fp007", "50023905") is None)
    check("未确认指纹不算冲突", st.detect_conflict("fp999", "1") is None)


def test_reject() -> None:
    print("\n[8] 拒绝任务")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    t = st.submit_pending("fp008", "i", "taobao", "taobao", cands())
    check("拒绝成功", st.reject(t.task_id, "类目不对"))
    check("状态 rejected", st.get_task(t.task_id).status == ST_REJECTED)
    check("pending 列表清空", len(st.list_pending()) == 0)
    check("重复拒绝返回 False", not st.reject(t.task_id))


# ---------------------------------------------------------------- 与 Resolver 集成


def test_resolver_integration() -> None:
    print("\n[9] 与 CategoryResolver 集成（验收第 8 条闭环）")
    tree = LocalJsonProvider(EXAMPLE).load("taobao")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)

    item = Item("大华监控交换机8口千兆", [("品牌", "Dahua")], ["颜色分类", "端口总数"])
    r = CategoryResolver(tree, store=st)
    res = r.resolve(item)
    check("首次 PENDING", res.status is MappingStatus.PENDING, res.status.value)
    check("有候选", len(res.candidates) > 0)

    task = st.submit_pending(res.fingerprint, "item-x", "taobao", "taobao", res.candidates)
    ok, msg = st.confirm(task.task_id, res.candidates[0].cid)
    check("确认成功", ok, msg)

    res2 = r.resolve(item)
    check("同输入再解析 → OK", res2.status is MappingStatus.OK, res2.status.value)
    check("source=confirmed", res2.source == "confirmed", res2.source)
    check("target_cid 正确", res2.target_cid == res.candidates[0].cid,
          str(res2.target_cid))

    # 换一个 store 实例（模拟进程重启）
    st2 = MappingTaskStore(db, yml)
    r2 = CategoryResolver(tree, store=st2)
    res3 = r2.resolve(item)
    check("重启后仍命中", res3.status is MappingStatus.OK, res3.status.value)


def test_expired_mapping() -> None:
    print("\n[10] 已确认但目标失效 → EXPIRED")
    tree = LocalJsonProvider(EXAMPLE).load("taobao")
    db, yml, _ = env()
    st = MappingTaskStore(db, yml)
    fp = build_fingerprint("某个商品", [], [], "taobao")
    st.put(fp, {"target_cid": "77777777", "target_path": ["已下线"]})

    r = CategoryResolver(tree, store=st)
    res = r.resolve_by_fingerprint(fp, "某个商品", [], [])
    check("状态 EXPIRED", res.status is MappingStatus.EXPIRED, res.status.value)
    check("错误码 MAPPING_EXPIRED", res.error is MappingError.MAPPING_EXPIRED)


def main() -> int:
    test_atomic_write()
    test_pending()
    test_confirm_ok()
    test_confirm_validation()
    test_confirm_write_failure()
    test_idempotent()
    test_conflict()
    test_reject()
    test_resolver_integration()
    test_expired_mapping()
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
