"""采集器回归测试：用真实抓取的页面/响应做夹具，不联网。

依赖 `data/raw/` 下的真实样本（由 scripts/probe_jd.py 等生成）。
样本缺失时自动跳过对应用例，不算失败。

跑法::

    python tests/test_collectors.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.alibaba import Alibaba1688Collector  # noqa: E402
from src.collect.base import api_stack_map, clean_image_url, extract_img_urls  # noqa: E402
from src.collect.jd import JDCollector, _js_object, _jd_img, _parse_params  # noqa: E402
from src.collect.mtop import MTopError  # noqa: E402
from src.collect.session import RiskDetector  # noqa: E402
from src.collect.taobao import TaobaoCollector  # noqa: E402
from src.core.models import RawItem  # noqa: E402

PASS, FAIL, SKIP = 0, 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  - {name} (跳过: {why})")


JD_M = ROOT / "data/raw/jd/100008348542_M.html"

# 1688 反爬拦截页的真实特征（实测 detail.1688.com 返回 2397 字节的跳转脚本）
ALI_BLOCKED_HTML = (    '<script>sessionStorage.x5referer = window.location.href;'
    'var url = window.location.protocol + "//detail.1688.com//offer/610947572360.html'
    '/_____tmd_____/punish?x5secdata=xfX7jdXXjtQoLD4EfrO61nKjIVe7LfPagLltX-'
    'CYm-fsAV60eoooZdU3NG61sRUdpNNrLRYkuCDtd4Te8NlV_--QhfjliYBkYagY7chZiqDNchTyQ'
    '&x5step=2";</script>')


# ---------------------------------------------------------------- 京东
def test_jd_real_page() -> None:
    print("\n[1] 京东移动端真实页面解析")
    if not JD_M.exists():
        skip("京东移动端页面", f"缺少 {JD_M}")
        return
    html = JD_M.read_text(encoding="utf-8", errors="ignore")

    only = _js_object(html, "_itemOnly") or {}
    info = _js_object(html, "_itemInfo") or {}
    check("_itemOnly 解析成功", bool(only.get("item")), str(list(only)[:3]))
    check("_itemInfo 解析成功", bool(info.get("product")), str(list(info)[:5]))
    check("嵌套 JSON 已展开",
          isinstance(info.get("stock"), dict) and bool(info["stock"].get("D")),
          str(info.get("stock"))[:80])

    col = JDCollector.__new__(JDCollector)
    col.platform = "jd"
    raw = RawItem(platform="jd", item_id="100008348542",
                  payload={"m_html": html, "desc_html": ""})
    item = col.to_item(raw)

    check("标题含 iPhone 11", "iPhone 11" in item.title, item.title[:60])
    check("主图为 360buyimg 原图",
          len(item.main_images) == 3 and all("360buyimg.com/n0/" in i.url for i in item.main_images),
          str([i.url[:50] for i in item.main_images]))
    check("店铺名", item.seller.shop_name == "Apple产品京东自营旗舰店", item.seller.shop_name)
    check("类目 id 链", item.extra["category_ids"] == ["9987", "653", "655"],
          str(item.extra["category_ids"]))
    check("品牌", item.extra["brand"] == "Apple", str(item.extra["brand"]))
    check("价格脱敏被识别", item.extra["price_masked"] is True)
    check("价格未硬编码成 0", item.price.min is None, str(item.price.min))
    check("参数表非空且无配送噪声", len(item.params) >= 3 and
          not any("运费" in p.name or "配送" in p.name for p in item.params),
          str([p.name for p in item.params]))
    check("单规格退化为 1 个 SKU", len(item.skus) == 1 and item.skus[0].sku_id == "100008348542")
    check("capabilities 标注缺项",
          item.extra["capabilities"]["price"] == "need_login",
          json.dumps(item.extra["capabilities"], ensure_ascii=False))


def test_jd_helpers() -> None:
    print("\n[2] 京东工具函数")
    check("jfs 路径拼 CDN",
          _jd_img("jfs/t1/148767/39/18017/86358/5fd32ff0E/x.jpg")
          == "https://img14.360buyimg.com/n0/jfs/t1/148767/39/18017/86358/5fd32ff0E/x.jpg",
          _jd_img("jfs/t1/a/b.jpg"))
    check("缩略图转原图",
          "n0" in _jd_img("https://img14.360buyimg.com/s450x450_jfs/t1/a/b.jpg"),
          _jd_img("https://img14.360buyimg.com/s450x450_jfs/t1/a/b.jpg"))
    check("空值安全", _jd_img("") == "")
    check("括号配平容忍单引号",
          (_js_object("var x = ({a:'b', c:{d:1}});", "x") or {}).get("a") == "b")
    check("缺失变量返回 None", _js_object("nothing here", "_itemInfo") is None)

    params = _parse_params(
        '<div class="p-parameter"><ul><li>品牌：Apple</li>'
        '<li>型号：iPhone 11</li><li>免运费：￥0.00</li></ul></div>')
    names = [p.name for p in params]
    check("参数表提取并过滤配送项",
          "品牌" in names and "型号" in names and "免运费" not in names, str(names))


# ---------------------------------------------------------------- 淘宝
def test_taobao_blocked() -> None:
    print("\n[3] 淘宝无登录态的行为")
    col = TaobaoCollector.__new__(TaobaoCollector)
    col.platform = "taobao"
    col.status = type("S", (), {"as_dict": lambda self=None: {"ok": False,
                                                              "reason": "NO_COOKIE_FILE"}})()
    raw = RawItem(platform="taobao", item_id="600000000001", payload={},
                  endpoint="html_fallback",
                  error="RISK_CONTROL: 淘宝风控拦截，需要登录态（cookie2/_tb_token_）或过滑块")
    item = col.to_item(raw)
    check("空 payload 不抛异常", item is not None)
    check("标题为空时 capability 为 False", item.extra["capabilities"]["title"] is False,
          json.dumps(item.extra["capabilities"], ensure_ascii=False))

    code, hint = RiskDetector().classify("taobao", 200,
                                         "RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试!")
    check("RGV587 归类为风控", code == "RISK_CONTROL" and "登录" in hint, f"{code} {hint}")
    code2, _ = RiskDetector().classify("taobao", 200, "FAIL_SYS_TOKEN_EMPTY::令牌为空")
    check("TOKEN_EMPTY 归类正确", code2 == "TOKEN_EMPTY", code2)


def test_taobao_fixture() -> None:
    """用构造的真实结构 payload 验证 pcdetail 解析（不需要登录）。"""
    print("\n[4] 淘宝 pcdetail 结构解析（夹具）")
    payload = {
        "item": {"itemId": "600000000001", "title": "夏季新款 女士 连衣裙",
                 "images": ["//img.alicdn.com/imgextra/i1/a.jpg_430x430q90.jpg",
                            "//img.alicdn.com/imgextra/i1/b.jpg"],
                 "categoryId": "50010850", "rootCategoryId": "16"},
        "seller": {"nick": "卖家A", "shopName": "店铺A"},
        "skuBase": {"props": [
            {"pid": "1627207", "name": "颜色分类",
             "values": [{"vid": "28320", "name": "黑色"},
                        {"vid": "28321", "name": "白色"}]},
            {"pid": "20509", "name": "尺码",
             "values": [{"vid": "28314", "name": "S"}, {"vid": "28315", "name": "M"}]},
        ], "skus": [
            {"skuId": "1", "propPath": "1627207:28320;20509:28314"},
            {"skuId": "2", "propPath": "1627207:28321;20509:28315"},
        ]},
        "skuCore": {"sku2info": {
            "1": {"price": {"priceText": "99.00"}, "quantity": "10"},
            "2": {"price": {"priceText": "109.00"}, "quantity": "20"},
        }},
        "props": {"groupProps": [{"基本信息": {"props": [
            {"name": "品牌", "value": "无品牌"}, {"name": "材质", "value": "棉"}]}}]},
        "apiStack": [{"name": "descInfo", "value": json.dumps({"descUrl": "//desc.alicdn.com/i3/d.jpg"})}],
        "_desc_html": '<img src="//img.alicdn.com/imgextra/i2/d1.jpg">'
                      '<img data-ks-lazyload="//img.alicdn.com/imgextra/i2/d2.jpg">',
    }
    col = TaobaoCollector.__new__(TaobaoCollector)
    col.platform = "taobao"
    col.status = type("S", (), {"as_dict": lambda self=None: {"ok": True}})()
    item = col.to_item(RawItem(platform="taobao", item_id="600000000001", payload=payload))

    check("标题", item.title == "夏季新款 女士 连衣裙", item.title)
    check("主图去尺寸后缀",
          item.main_images[0].url == "https://img.alicdn.com/imgextra/i1/a.jpg",
          item.main_images[0].url)
    check("销售属性 2 维", [p.name for p in item.sale_props] == ["颜色分类", "尺码"])
    check("SKU 2 条且带价格", len(item.skus) == 2 and item.skus[0].price == 99.0,
          str([(s.sku_id, s.price) for s in item.skus]))
    check("SKU attrs 已拆解", item.skus[0].attrs == {"1627207": "28320", "20509": "28314"},
          str(item.skus[0].attrs))
    check("价格区间 99~109", (item.price.min, item.price.max) == (99.0, 109.0),
          str((item.price.min, item.price.max)))
    check("参数表 2 条", len(item.params) == 2)
    check("详情图 2 张", len(item.detail_images) == 2,
          str([i.url for i in item.detail_images]))
    check("apiStack 已解包", item.extra["api_stack_keys"] == ["descInfo"],
          str(item.extra["api_stack_keys"]))


# ---------------------------------------------------------------- 1688
def test_alibaba_blocked() -> None:
    print("\n[5] 1688 拦截页识别")
    html = ALI_BLOCKED_HTML
    code, hint = RiskDetector().classify("alibaba", 200, html)
    # 1688 拦截页同时含 x5sec 与 _____tmd_____，归类为 SLIDER_REQUIRED 或 ANTI_BOT 都算正确
    check("x5sec 页识别为滑块/反爬拦截",
          code in ("ANTI_BOT", "SLIDER_REQUIRED"), f"{code} {hint}")

    col = Alibaba1688Collector.__new__(Alibaba1688Collector)
    col.platform = "alibaba"
    col.status = type("S", (), {"as_dict": lambda self=None: {"ok": False}})()
    payload = {"_blocked": True, "_block_code": code, "_block_hint": hint}
    item = col.to_item(RawItem(platform="alibaba", item_id="610947572360",
                               payload=payload, error=f"{code}: {hint}"))
    check("拦截态不抛异常且标记 blocked", item.extra["blocked"] is True)
    check("拦截态标题为空", item.title == "", item.title)


# ---------------------------------------------------------------- 汇总
def main() -> int:
    test_jd_real_page()
    test_jd_helpers()
    test_taobao_blocked()
    test_taobao_fixture()
    test_alibaba_blocked()
    print(f"\n{'=' * 40}\n通过 {PASS} / 失败 {FAIL} / 跳过 {SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
