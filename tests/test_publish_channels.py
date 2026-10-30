"""T7 测试：双通道 dry_run / draft / direct，以及「零网络调用」。

跑法::

    python tests/test_publish_channels.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.models import (Category, Image, Item, Param, PriceInfo,  # noqa: E402
                             PropValue, SaleProp, Sku)
from src.normalize.category_models import MappingError  # noqa: E402
from src.normalize.category_provider import CategoryTreeService, LocalJsonProvider  # noqa: E402
from src.normalize.mapping_tasks import MappingTaskStore  # noqa: E402
from src.normalize.sku_builder import PriceConfig, PriceEngine  # noqa: E402
from src.publish.pipeline import PublishPipeline, PublishRequest  # noqa: E402

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


def make_item() -> Item:
    return Item(
        item_id="t100",
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
            Sku(sku_id="s2", attrs={"1627207": "44467701705", "4054020": "15295387"},
                price=22.0, quantity=20),
            Sku(sku_id="s3", attrs={"1627207": "44467701706", "4054020": "43502"},
                price=31.0, quantity=30),
            Sku(sku_id="s4", attrs={"1627207": "44467701706", "4054020": "15295387"},
                price=55.0, quantity=40),
        ],
        params=[Param(name="品牌", value="Dahua"),
                Param(name="传输速度", value="1000Mbps")],
        extra={"capabilities": {}},
    )


def make_pipeline(drafts: list | None = None, stock_mode: str = "copy"):
    """构造一个不碰真实 SQLite 的 pipeline：草稿用内存列表模拟。"""
    store = drafts if drafts is not None else []

    def fake_save(item_id, target, channel, target_cid, payload):
        draft_id = f"draft{len(store) + 1:03d}"
        store.append({"draft_id": draft_id, "item_id": item_id, "target": target,
                      "channel": channel, "target_cid": target_cid,
                      "payload": payload})
        return draft_id

    return PublishPipeline(
        tree_service=CategoryTreeService([LocalJsonProvider(EXAMPLE)]),
        price_engine=PriceEngine(PriceConfig(mode="copy", round_to="0.01")),
        draft_saver=fake_save,
        draft_counter=lambda: len(store),
        stock_mode=stock_mode,
    ), store


def req(mode="dry_run", channel="open_api", cid="50023905"):
    return PublishRequest(make_item(), channel=channel, mode=mode,
                          target_platform="taobao", target_cid=cid)


# ---------------------------------------------------------------- dry_run


def test_dry_run() -> None:
    print("[1] dry_run：返回载荷，草稿表不变（验收第 10 条）")
    pipe, drafts = make_pipeline()
    before = len(drafts)
    res = pipe.publish(req("dry_run"))

    check("成功", res.ok, res.error_msg or "")
    check("模式是 dry_run", res.mode == "dry_run", res.mode)
    check("没有 draft_id", res.draft_id is None, str(res.draft_id))
    check("载荷非空", bool(res.payload), str(res.payload)[:50])
    check("载荷含 cid", res.payload.get("cid") == "50023905", str(res.payload.get("cid")))
    check("载荷含 4 个 SKU", len(res.payload.get("skus") or []) == 4,
          str(len(res.payload.get("skus") or [])))
    check("载荷含 props 串", "1627207:" in (res.payload.get("props") or ""),
          res.payload.get("props"))
    check("载荷含主图 3 张", len(res.payload.get("images") or []) == 3)
    check("草稿表记录数不变", len(drafts) == before, f"{before} -> {len(drafts)}")


def test_draft() -> None:
    print("\n[2] draft：写本地草稿，返回可查询的 draft_id（验收第 10 条）")
    pipe, drafts = make_pipeline()
    before = len(drafts)
    res = pipe.publish(req("draft"))

    check("成功", res.ok, res.error_msg or "")
    check("模式是 draft", res.mode == "draft", res.mode)
    check("返回 draft_id", bool(res.draft_id), str(res.draft_id))
    check("草稿表 +1", len(drafts) == before + 1, f"{before} -> {len(drafts)}")
    saved = drafts[-1]
    check("草稿记录 item_id", saved["item_id"] == "t100", saved["item_id"])
    check("草稿记录 channel", saved["channel"] == "open_api", saved["channel"])
    check("草稿含完整载荷", "skus" in saved["payload"])


def test_direct_disabled() -> None:
    print("\n[3] direct：M2 未实现，明确拒绝")
    pipe, drafts = make_pipeline()
    before = len(drafts)
    res = pipe.publish(req("direct"))

    check("不成功", not res.ok)
    check("错误码 CHANNEL_DISABLED",
          res.error_code == MappingError.CHANNEL_DISABLED.value, str(res.error_code))
    check("错误信息说明未实现", "M2" in (res.error_msg or ""), res.error_msg or "")
    check("草稿表不变", len(drafts) == before)


def test_category_pending() -> None:
    print("\n[4] 类目未确定 → pending + 候选，不构造载荷")
    pipe, _ = make_pipeline()
    item = make_item()
    item.title = ""  # 清空标题让匹配失败
    r = PublishRequest(item, channel="open_api", mode="draft",
                       target_platform="taobao", target_cid=None)
    res = pipe.publish(r)
    check("不成功", not res.ok)
    check("错误码 CATEGORY_NOT_MATCHED",
          res.error_code == MappingError.CATEGORY_NOT_MATCHED.value, str(res.error_code))
    check("返回候选字段", "candidates" in (res.raw_response or {}),
          str(list((res.raw_response or {}).keys())))


# ---------------------------------------------------------------- 零网络


def test_zero_network() -> None:
    print("\n[5] 零网络调用（验收第 11 条）")

    def boom(*a, **kw):
        raise AssertionError("发布层不该创建 httpx 客户端")

    calls = {"client": 0, "get": 0, "post": 0}

    def counting_client(*a, **kw):
        calls["client"] += 1
        raise AssertionError("不该创建 httpx.Client")

    def counting_get(*a, **kw):
        calls["get"] += 1
        raise AssertionError("不该发 GET")

    def counting_post(*a, **kw):
        calls["post"] += 1
        raise AssertionError("不该发 POST")

    with patch.object(httpx, "Client", counting_client), \
         patch.object(httpx, "get", counting_get), \
         patch.object(httpx, "post", counting_post):
        for channel in ("open_api", "backend"):
            for mode in ("dry_run", "draft", "direct"):
                pipe, _ = make_pipeline()
                pipe.publish(req(mode, channel))
                # 再直接走 prepare（只构造不提交）
                pipe.prepare(req(mode, channel))

    check("httpx.Client 创建 0 次", calls["client"] == 0, str(calls))
    check("httpx.get 调用 0 次", calls["get"] == 0, str(calls))
    check("httpx.post 调用 0 次", calls["post"] == 0, str(calls))


# ---------------------------------------------------------------- endpoint


def test_no_guessed_endpoint() -> None:
    print("\n[6] 仓库里没有猜测的后台发布 endpoint（验收第 12 条）")
    publish_dir = ROOT / "src" / "publish"
    suspicious = []
    # 后台发布接口常见的猜测写法
    patterns = [
        r"item\.publish\.taobao\.com",
        r"publish\.taobao\.com/publish",
        r"sell\.taobao\.com/.*publish",
        r"/publish/submit",
        r"backend_endpoint\s*[:=]\s*[\"']https?://",
    ]
    for f in publish_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for pat in patterns:
            if re.search(pat, text):
                suspicious.append(f"{f.name}: {pat}")
    check("src/publish 下无猜测的发布 endpoint", not suspicious, str(suspicious))

    cfg = ROOT / "config" / "settings.example.yaml"
    cfg_text = cfg.read_text(encoding="utf-8")
    guessed = re.findall(r"(?:publish_endpoint|backend_endpoint)\s*:\s*[\"']?https?://\S+",
                         cfg_text)
    check("配置里没有硬编码发布 endpoint", not guessed, str(guessed))


def test_unconfigured_channel() -> None:
    print("\n[7] 通道未配置 → BACKEND_ENDPOINT_UNCONFIGURED")
    from src.publish.pipeline import UnconfiguredChannel
    ch = UnconfiguredChannel("backend", "后台发布 endpoint 未配置")
    check("configured() 为 False", not ch.configured())
    res = ch.submit({}, "default")
    check("submit 返回失败", not res.ok)
    check("错误码 BACKEND_ENDPOINT_UNCONFIGURED",
          res.error_code == MappingError.BACKEND_ENDPOINT_UNCONFIGURED.value,
          str(res.error_code))
    check("错误信息可读", "endpoint" in (res.error_msg or "").lower(), res.error_msg or "")


# ---------------------------------------------------------------- 载荷质量


def test_payload_quality() -> None:
    print("\n[8] 载荷字段完整性")
    pipe, _ = make_pipeline()
    res = pipe.publish(req("dry_run"))
    p = res.payload

    required = ["cid", "title", "props", "price", "num", "images", "skus",
                "sku_properties", "sku_prices", "sku_quantities"]
    for key in required:
        check(f"载荷含 {key}", key in p, str(list(p.keys())))

    check("props 含品牌 pid 20000", "20000:" in p["props"], p["props"])
    check("props 含颜色 pid 1627207", "1627207:" in p["props"], p["props"])
    check("props 含端口 pid 4054020", "4054020:" in p["props"], p["props"])
    check("SKU 四件套长度一致",
          len(p["sku_properties"].split(",")) == len(p["sku_prices"].split(","))
          == len(p["sku_quantities"].split(",")), "长度不一致")
    check("总库存 = 各 SKU 之和", p["num"] == 100, str(p["num"]))
    check("最低价是 15.5", p["price"] == "15.5", p["price"])


def test_title_sanitized() -> None:
    print("\n[9] 标题清洗生效")
    pipe, _ = make_pipeline()
    item = make_item()
    item.title = "正品官方 大华交换机 最便宜 包邮"
    r = PublishRequest(item, channel="open_api", mode="dry_run",
                       target_platform="taobao", target_cid="50023905")
    res = pipe.publish(r)
    title = res.payload.get("title", "")
    check("极限词被剔除",
          not any(w in title for w in ("正品", "官方", "最便宜", "包邮")), title)
    check("仍有内容", len(title) > 2, title)


def test_unknown_cid() -> None:
    print("\n[10] 目标 cid 不在树里 → 明确报错")
    pipe, _ = make_pipeline()
    res = pipe.publish(req("dry_run", cid="99999999"))
    check("不成功", not res.ok)
    check("提示不在类目树", "不在类目树" in (res.error_msg or ""), res.error_msg or "")


def main() -> int:
    test_dry_run()
    test_draft()
    test_direct_disabled()
    test_category_pending()
    test_zero_network()
    test_no_guessed_endpoint()
    test_unconfigured_channel()
    test_payload_quality()
    test_title_sanitized()
    test_unknown_cid()
    print(f"\n{'=' * 44}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
