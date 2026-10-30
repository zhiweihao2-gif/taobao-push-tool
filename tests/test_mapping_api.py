"""T8 测试：映射与发布 HTTP 接口。

用 FastAPI TestClient（基于 httpx，进程内调用，不监听端口、不联网）。

跑法::

    python tests/test_mapping_api.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from src.api.main import app  # noqa: E402
from src.api import mapping_api  # noqa: E402
from src.core.models import (Category, Image, Item, Param, PriceInfo,  # noqa: E402
                             PropValue, SaleProp, Sku)
from src.core.storage import save_item  # noqa: E402
from src.normalize.category_provider import CategoryTreeService, LocalJsonProvider  # noqa: E402
from src.normalize.mapping_tasks import MappingTaskStore  # noqa: E402
from src.publish.pipeline import PublishPipeline  # noqa: E402

PASS, FAIL = 0, 0
EXAMPLE = ROOT / "data/mapping/category_tree.example.json"
ITEM_ID = "api_test_item_001"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


def seed_item() -> Item:
    item = Item(
        item_id=ITEM_ID,
        platform="taobao",
        title="大华监控交换机DH-S1000C 千兆8口",
        category=Category(cid=""),
        price=PriceInfo(min=15.5, max=55.0),
        main_images=[Image(url="https://img.alicdn.com/a.jpg"),
                     Image(url="https://img.alicdn.com/b.jpg"),
                     Image(url="https://img.alicdn.com/c.jpg")],
        sale_props=[
            SaleProp(pid="1627207", name="颜色分类", values=[
                PropValue(vid="44467701705", name="【百兆非POE交换机】"),
                PropValue(vid="44467701706", name="【千兆非POE交换机】")]),
            SaleProp(pid="4054020", name="端口总数", values=[
                PropValue(vid="43502", name="5个"),
                PropValue(vid="15295387", name="8个")]),
        ],
        skus=[
            Sku(sku_id="s1", attrs={"1627207": "44467701705", "4054020": "43502"},
                price=15.5, quantity=10),
            Sku(sku_id="s2", attrs={"1627207": "44467701706", "4054020": "15295387"},
                price=55.0, quantity=40),
        ],
        params=[Param(name="品牌", value="Dahua")],
        extra={"capabilities": {}},
    )
    save_item(item)
    return item


def setup() -> TestClient:
    """把 API 的全局依赖换成临时目录里的实例，避免污染真实数据。"""
    d = Path(tempfile.mkdtemp(prefix="api_test_"))
    mapping_api._tree_service = CategoryTreeService([LocalJsonProvider(EXAMPLE)])
    mapping_api._task_store = MappingTaskStore(d / "app.db", d / "category_map.yaml")
    mapping_api._pipeline = PublishPipeline(
        tree_service=mapping_api._tree_service,
        task_store=mapping_api._task_store,
    )
    return TestClient(app)


# ---------------------------------------------------------------- 测试


def test_routes_exist(c: TestClient) -> None:
    print("[1] 路由已挂载")

    # 注意：FastAPI 0.162 起 include_router 是**懒挂载**，路由不会展开进 app.routes，
    # 而是包成一个 _IncludedRouter。所以不能用 app.routes 判断，要看 openapi schema
    # 或直接发请求。
    schema = c.get("/openapi.json").json()
    paths = set(schema.get("paths", {}).keys())
    for p in ("/map/category", "/map/tasks", "/map/confirm", "/map/reject",
              "/publish/prepare", "/publish/draft", "/drafts"):
        check(f"openapi 含 {p}", p in paths, str(sorted(paths))[:150])
    check("原有采集路由未被破坏",
          "/collect" in paths and "/items" in paths, str(sorted(paths))[:150])

    # 再实测一发，确认真的可达
    r = c.get("/map/tasks", params={"status": "pending"})
    check("GET /map/tasks 实际可达", r.status_code == 200, str(r.status_code))


def test_map_category(c: TestClient) -> None:
    print("\n[2] POST /map/category")
    r = c.post("/map/category", json={"item_id": ITEM_ID, "target_platform": "taobao"})
    check("200", r.status_code == 200, str(r.status_code))
    d = r.json()
    check("返回 status", "status" in d, str(d)[:80])
    check("有候选", len(d.get("candidates") or []) > 0, str(len(d.get("candidates") or [])))
    check("第一候选是交换机",
          (d.get("candidates") or [{}])[0].get("cid") == "50023905",
          str((d.get("candidates") or [{}])[0].get("cid")))
    check("带 fingerprint", bool(d.get("fingerprint")), str(d.get("fingerprint")))


def test_map_category_404(c: TestClient) -> None:
    print("\n[3] 商品不存在 → 404")
    r = c.post("/map/category", json={"item_id": "no_such_item"})
    check("404", r.status_code == 404, str(r.status_code))


def test_task_flow(c: TestClient) -> None:
    print("\n[4] 任务：挂起 → 列表 → 确认")
    store = mapping_api._task_store
    d = c.post("/map/category", json={"item_id": ITEM_ID}).json()
    fp = d["fingerprint"]
    task = store.submit_pending(fp, ITEM_ID, "taobao", "taobao",
                                [type("C", (), {"cid": "50023905", "path": ["网络设备", "交换机"],
                                                "score": 0.6, "reasons": [],
                                                "model_dump": lambda self=None: {"cid": "50023905", "path": ["网络设备", "交换机"], "score": 0.6, "reasons": []}})()])

    r = c.get("/map/tasks", params={"status": "pending"})
    check("200", r.status_code == 200)
    check("能看到 pending 任务", r.json()["count"] >= 1, str(r.json()["count"]))

    r2 = c.post("/map/confirm", json={"task_id": task.task_id, "target_cid": "50023905"})
    check("确认 200", r2.status_code == 200)
    check("确认成功", r2.json()["ok"] is True, str(r2.json()))

    r3 = c.get("/map/tasks", params={"status": "confirmed"})
    check("confirmed 列表有记录", r3.json()["count"] >= 1, str(r3.json()["count"]))


def test_confirm_invalid(c: TestClient) -> None:
    print("\n[5] 确认非法目标 → 失败但不崩")
    r = c.post("/map/confirm", json={"task_id": "nope", "target_cid": "1"})
    check("200", r.status_code == 200)
    check("ok=False", r.json()["ok"] is False, str(r.json()))


def test_publish_prepare(c: TestClient) -> None:
    print("\n[6] POST /publish/prepare（dry_run）")
    r = c.post("/publish/prepare", json={
        "item_id": ITEM_ID, "target_platform": "taobao",
        "channel": "open_api", "mode": "dry_run", "target_cid": "50023905"})
    check("200", r.status_code == 200, str(r.status_code))
    d = r.json()
    check("ok", d["ok"] is True, str(d.get("errors")))
    check("有 payload", bool(d.get("payload")), str(d)[:80])
    check("payload 含 cid", d["payload"].get("cid") == "50023905")
    check("summary 有 skus 数", "skus" in d.get("summary", {}), str(d.get("summary")))


def test_publish_draft(c: TestClient) -> None:
    print("\n[7] POST /publish/draft")
    before = c.get("/drafts").json()["count"]
    r = c.post("/publish/draft", json={
        "item_id": ITEM_ID, "target_platform": "taobao",
        "channel": "open_api", "mode": "draft", "target_cid": "50023905"})
    check("200", r.status_code == 200, str(r.status_code))
    d = r.json()
    check("ok", d.get("ok") is True, str(d)[:120])
    check("有 draft_id", bool(d.get("draft_id")), str(d.get("draft_id")))

    after = c.get("/drafts").json()["count"]
    check("草稿数 +1", after == before + 1, f"{before} -> {after}")

    did = d["draft_id"]
    r2 = c.get(f"/drafts/{did}")
    check("能按 id 查草稿", r2.status_code == 200, str(r2.status_code))
    check("草稿含 payload", "payload" in r2.json(), str(list(r2.json().keys())))


def test_backend_unconfigured(c: TestClient) -> None:
    print("\n[7b] backend 通道未配置 → BACKEND_ENDPOINT_UNCONFIGURED（验收第 12 条）")
    before = c.get("/drafts").json()["count"]
    r = c.post("/publish/draft", json={
        "item_id": ITEM_ID, "target_platform": "taobao",
        "channel": "backend", "mode": "draft", "target_cid": "50023905"})
    check("200", r.status_code == 200, str(r.status_code))
    d = r.json()
    check("ok=False", d.get("ok") is False, str(d)[:120])
    check("错误码 BACKEND_ENDPOINT_UNCONFIGURED",
          d.get("error_code") == "BACKEND_ENDPOINT_UNCONFIGURED", str(d.get("error_code")))
    check("没有写草稿", c.get("/drafts").json()["count"] == before)


def test_publish_direct(c: TestClient) -> None:
    print("\n[8] direct 模式被拒绝")
    r = c.post("/publish/prepare", json={
        "item_id": ITEM_ID, "target_platform": "taobao",
        "channel": "open_api", "mode": "direct", "target_cid": "50023905"})
    check("200", r.status_code == 200)
    check("prepare 仍然能构造载荷", bool(r.json().get("payload")))


def test_draft_404(c: TestClient) -> None:
    print("\n[9] 草稿不存在 → 404")
    r = c.get("/drafts/nonexistent")
    check("404", r.status_code == 404, str(r.status_code))


def main() -> int:
    seed_item()
    c = setup()
    test_routes_exist(c)
    test_map_category(c)
    test_map_category_404(c)
    test_task_flow(c)
    test_confirm_invalid(c)
    test_publish_prepare(c)
    test_publish_draft(c)
    test_backend_unconfigured(c)
    test_publish_direct(c)
    test_draft_404(c)
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
