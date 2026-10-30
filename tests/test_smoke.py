"""离线冒烟测试：不联网，用构造的原始 payload 验证整条链路。

跑法::

    python tests/test_smoke.py

覆盖：
1. mtop 签名算法（已知向量）
2. 淘宝 pcdetail payload → Item 归一化
3. Item 落盘 / 读回
4. 类目映射命中
5. 属性 + SKU 重建
6. 发布载荷构造 + dry_run 校验
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.base import api_stack_map, clean_image_url, extract_img_urls  # noqa: E402
from src.collect.taobao import TaobaoCollector  # noqa: E402
from src.core.models import RawItem  # noqa: E402
from src.core.storage import load_item, save_item  # noqa: E402
from src.normalize.mapper import CategoryMapper, PriceEngine, PropertyMapper, SkuRebuilder  # noqa: E402
from src.normalize.sanitizer import TextSanitizer  # noqa: E402
from src.publish.taobao_pub import TaobaoPublisher  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


# ---------------------------------------------------------------- 1. 签名
def test_sign() -> None:
    print("\n[1] mtop 签名")
    import time as _time
    from src.collect.mtop import MTopClient
    c = MTopClient(site="taobao")
    c._token, c._token_at, c.app_key = "abc123", _time.time(), "12574478"
    t = "1699999999999"
    data = '{"itemId":"1"}'
    expect = hashlib.md5(f"abc123&{t}&12574478&{data}".encode()).hexdigest()
    check("sign 与公式一致", c.sign(t, data) == expect, c.sign(t, data))


# ---------------------------------------------------------------- 2. 工具函数
def test_helpers() -> None:
    print("\n[2] 工具函数")
    check("去尺寸后缀",
          clean_image_url("//img.alicdn.com/imgextra/i1/a.jpg_430x430q90.jpg")
          == "https://img.alicdn.com/imgextra/i1/a.jpg",
          clean_image_url("//img.alicdn.com/imgextra/i1/a.jpg_430x430q90.jpg"))
    stack = api_stack_map([{"name": "descInfo", "value": '{"descUrl":"//d/x"}'}])
    check("apiStack 字符串解包", stack["descInfo"]["descUrl"] == "//d/x")
    imgs = extract_img_urls('<img src="//img.alicdn.com/a.jpg"><img data-ks-lazyload="//img.alicdn.com/b.jpg">')
    check("详情图抽取", len(imgs) == 2, str(imgs))


# ---------------------------------------------------------------- 3. 归一化
SAMPLE = {
    "item": {
        "itemId": "600000000001",
        "title": "正品 官方 女士棉质连衣裙 2024新款 最便宜",
        "images": ["//img.alicdn.com/imgextra/i1/main1.jpg_430x430q90.jpg",
                   "//img.alicdn.com/imgextra/i1/main2.jpg"],
        "categoryId": "50010850",
        "rootCategoryId": "16",
        "sellerId": "2200000000001",
        "videos": [{
            "url": "https://cloud.video.taobao.com/play/u/2200000000001/p/2/e/6/t/1/1352964901100101.mp4?appKey=38829",
            "videoId": "1352964901100101",
            "videoThumbnailURL": "//img.alicdn.com/imgextra/i3/cover.jpg",
            "spatialVideoDimension": "3:4",
        }],
    },
    "seller": {"nick": "测试卖家", "shopName": "测试店铺"},
    "skuBase": {
        "props": [
            {"pid": "1627207", "name": "颜色分类",
             "values": [{"vid": "28320", "name": "黑色", "image": "//img.alicdn.com/sku1.jpg"},
                        {"vid": "28321", "name": "白色"}]},
            {"pid": "20509", "name": "尺码",
             "values": [{"vid": "28314", "name": "S"}, {"vid": "28315", "name": "M"}]},
        ],
        "skus": [
            {"skuId": "5000000001", "propPath": "1627207:28320;20509:28314"},
            {"skuId": "5000000002", "propPath": "1627207:28320;20509:28315"},
            {"skuId": "5000000003", "propPath": "1627207:28321;20509:28314"},
        ],
    },
    "skuCore": {"sku2info": {
        "5000000001": {"price": {"priceText": "129.00"}, "quantity": "50"},
        "5000000002": {"price": {"priceText": "129.00"}, "quantity": "30"},
        "5000000003": {"price": {"priceText": "139.00"}, "quantity": "20"},
    }},
    "props": {"groupProps": [{"基本信息": {"props": [
        {"name": "品牌", "value": "无品牌"},
        {"name": "材质", "value": "棉"},
    ]}}]},
    "apiStack": [{"name": "descInfo", "value": json.dumps({"descUrl": "//desc.alicdn.com/i3/d.jpg"})}],
    "_desc_html": '<p>详情</p><img src="//img.alicdn.com/imgextra/i2/detail1.jpg">'
                  '<img data-ks-lazyload="//img.alicdn.com/imgextra/i2/detail2.jpg">',
}


def test_normalize() -> None:
    print("\n[3] 归一化")
    col = TaobaoCollector.__new__(TaobaoCollector)
    col.platform = "taobao"
    col.status = type("S", (), {"as_dict": lambda self=None: {"ok": True}})()
    raw = RawItem(platform="taobao", item_id="600000000001", payload=SAMPLE)
    item = col.to_item(raw)

    check("标题", item.title.startswith("正品"), item.title)
    check("主图 2 张且已去后缀",
          len(item.main_images) == 2 and item.main_images[0].url.endswith("main1.jpg"),
          str([i.url for i in item.main_images]))
    check("销售属性 2 维", len(item.sale_props) == 2)
    check("SKU 3 条", len(item.skus) == 3)
    check("SKU 价格解析", item.skus[0].price == 129.0, str(item.skus[0].price))
    check("价格区间 129~139", (item.price.min, item.price.max) == (129.0, 139.0),
          str((item.price.min, item.price.max)))
    check("参数表 2 条", len(item.params) == 2)
    check("详情图 2 张", len(item.detail_images) == 2,
          str([i.url for i in item.detail_images]))
    check("视频 URL", bool(item.video_url), item.video_url)
    check("类目 cid", item.category.cid == "50010850")

    path = save_item(item)
    back = load_item(item.item_id)
    check("落盘后可读回", back is not None and back.title == item.title)
    print(f"      → {path}")


# ---------------------------------------------------------------- 4. 映射
def test_mapping() -> None:
    print("\n[4] 映射与 SKU 重建")
    item = load_item("600000000001")
    assert item is not None

    cm = CategoryMapper()
    hit = cm.lookup("taobao", "taobao", "50010850")
    check("类目映射命中", hit is not None and hit["target_cid"] == "50010850")

    pm = PropertyMapper()
    spec = pm.map_sale_prop("taobao", "taobao", "1627207")
    check("属性映射命中", spec is not None and spec["target_pid"] == "1627207")
    val = pm.map_value(spec["value_map"], "28320", "黑色")
    check("属性值映射命中", val is not None and val["vid"] == "28320")

    price = PriceEngine({"mode": "multiplier", "multiplier": 1.35, "round_to": 0.01,
                         "clamp": [9.9, 99999]})
    check("价格加价 35%", price.apply(129.0) == 174.15, str(price.apply(129.0)))

    prop_order = []
    for sp in item.sale_props:
        s = pm.map_sale_prop("taobao", "taobao", sp.pid)
        if s:
            prop_order.append({"src_pid": sp.pid, "tgt_pid": s["target_pid"],
                               "value_map": s["value_map"]})
    rb = SkuRebuilder(price_engine=price, default_stock=999)
    skus = rb.rebuild(item, prop_order, stock_mode="fixed")
    check("SKU 重建 3 条", len(skus) == 3, str(len(skus)))
    check("SKU props 串格式", skus[0]["props"].count(":") == 2, skus[0]["props"])
    check("SKU 库存固定 999", skus[0]["quantity"] == 999)


# ---------------------------------------------------------------- 5. 清洗
def test_sanitize() -> None:
    print("\n[5] 文案清洗")
    s = TextSanitizer()
    out = s.clean_title("正品 官方 女士棉质连衣裙 2024新款 最便宜 联系微信abc123")
    check("极限词/联系方式已剔除",
          not any(w in out for w in ("正品", "官方", "最便宜", "微信")), out)
    check("清洗后仍有内容", len(out) >= 4, out)


# ---------------------------------------------------------------- 6. 发布载荷
def test_publish_payload() -> None:
    print("\n[6] 发布载荷（dry_run）")
    item = load_item("600000000001")
    assert item is not None
    pub = TaobaoPublisher(mode="dry_run")
    payload = pub.build_payload(item)
    check("cid 已解析", payload["cid"] == "50010850", str(payload["cid"]))
    check("标题已清洗", "正品" not in payload["title"], payload["title"])
    check("props 串非空", "1627207:" in payload["props"], payload["props"])
    check("SKU 四件套齐全",
          all(payload[k] for k in ("sku_properties", "sku_prices",
                                   "sku_quantities", "sku_outer_ids")))
    check("草稿状态", payload["approve_status"] == "instock")

    result = pub.publish(item)
    print(f"      dry_run ok={result.ok} msg={result.error_msg}")


def main() -> int:
    test_sign()
    test_helpers()
    test_normalize()
    test_mapping()
    test_sanitize()
    test_publish_payload()
    print(f"\n{'=' * 40}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
