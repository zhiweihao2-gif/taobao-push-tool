"""把 parse_page_json.py 抠出来的 HTML 内嵌 JSON 转成标准 Item 存进库。

这样发布流程（PublishPipeline / publish_poc）可以直接用，不用区分数据来源
是 mtop 接口还是 HTML 直出。

用法：
    python scripts/import_page_json.py data/publish_poc/dom_xxx.extracted.json
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.models import (Category, Image, Item, Param, PriceInfo,  # noqa: E402
                             PropValue, SaleProp, Seller, Sku)
from src.core.storage import save_item  # noqa: E402

# 这些「属性」其实是平台的服务承诺，不是商品自身参数，入库要剔掉
_SERVICE_KEYS = {
    "大促价保", "退货宝", "88VIP退货包运费", "7天无理由退货", "极速退款",
    "价保", "运费险", "花呗分期", "信用卡支付",
}

# 图片 URL 里的服务端裁剪参数：~crop,180,0,1080,1440~
_CROP_RE = re.compile(r"~crop,[^~]*~")
# 尺寸后缀：_320x320q80_.webp / .jpg_430x430q90.jpg
_SIZE_RE = re.compile(r"(\.(?:jpg|jpeg|png|webp|gif))_[^/]*$", re.I)

# 淘宝 PC 详情页把类目 ID 塞在 item.xsRedPocketParams.tbShopRedPocket 的双重转义串里。
# 实测：只用 categoryId/catId/cat_id 搜全部 0 命中，真实字段名是 leafCategory / rootCategory。
_LEAF_RE = re.compile(r'"leafCategory"\s*:\s*"?(\d+)"?')
_ROOT_RE = re.compile(r'"rootCategory"\s*:\s*"?(\d+)"?')


def find_category_ids(text: str) -> tuple[str | None, str | None]:
    """从 HTML/JSON 文本里抠 (rootCategory, leafCategory)。

    注意必须对**整个 HTML** 做正则 —— parse_page_json.extract_obj 按 skuCore
    抠出的那个子对象里不含这两个字段。
    """
    if not text:
        return None, None
    leaf = _LEAF_RE.search(text)
    root = _ROOT_RE.search(text)
    return (root.group(1) if root else None,
            leaf.group(1) if leaf else None)


def resolve_category(cid: str | None) -> Category:
    """拿 leafCategory 去本地类目缓存反查名称/路径。缓存缺失时降级成只有 cid。"""
    if not cid:
        return Category(cid="unknown", path=[])
    try:
        from src.publish.category_api import CategoryCache
        cache = CategoryCache()
        node = cache.get(cid)
        if node:
            return Category(cid=cid, path=list(node.get("path") or []),
                            root_cid=str((node.get("idpath") or [""])[0] or "") or None)
    except Exception:
        pass
    return Category(cid=cid, path=[])


def clean_img(url: str) -> str:
    """去掉服务端裁剪参数和尺寸后缀，拿到尽可能大的原图。"""
    if not url:
        return url
    u = _CROP_RE.sub("", url)
    u = u.replace("_.webp", "")
    u = _SIZE_RE.sub(r"\1", u)
    return u


def dig(obj, *path, default=None):
    cur = obj
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and -len(cur) <= k < len(cur):
            cur = cur[k]
        else:
            return default
        if cur is None:
            return default
    return cur


def collect_params(obj: dict) -> list[Param]:
    """抠参数表。三条来源合并去重，剔掉服务承诺类。"""
    pairs: list[tuple[str, str]] = []

    ext = dig(obj, "componentsVO", "extensionInfoVO", default={}) or {}
    for grp in (ext.get("infos") or []):
        for it in (grp.get("items") or []):
            t, v = it.get("title"), it.get("text")
            if not t or not v:
                continue
            val = ",".join(map(str, v)) if isinstance(v, list) else str(v)
            pairs.append((str(t), val))

    def walk(o, depth=0):
        if depth > 8:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("basicParamList", "paramList") and isinstance(v, list):
                    for it in v:
                        if not isinstance(it, dict):
                            continue
                        n = it.get("propertyName") or it.get("name")
                        val = it.get("valueName") or it.get("value")
                        if n and val:
                            pairs.append((str(n), str(val)))
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for it in o[:60]:
                walk(it, depth + 1)

    walk(obj)

    seen, out = set(), []
    for n, v in pairs:
        if n in _SERVICE_KEYS:
            continue
        key = (n, v)
        if key in seen:
            continue
        seen.add(key)
        out.append(Param(name=n, value=v))
    return out


def build_item(obj: dict, item_id: str, source_url: str | None = None,
               category_cid: str | None = None) -> Item:
    it = obj.get("item") or {}
    title = str(it.get("title") or "").strip()
    if not title:
        raise ValueError("没有标题，这个 JSON 不是商品主数据")

    # 主图
    main_imgs: list[Image] = []
    for u in (it.get("images") or []):
        if not isinstance(u, str) or not u:
            continue
        main_imgs.append(Image(url=clean_img(u), role="main"))

    # 视频
    videos = []
    for v in (it.get("videos") or []):
        if isinstance(v, dict) and v.get("url"):
            videos.append(v)

    # 销售属性
    sale_props: list[SaleProp] = []
    for p in (dig(obj, "skuBase", "props", default=[]) or []):
        vals = []
        for v in (p.get("values") or []):
            vid = str(v.get("vid") or "")
            if not vid:
                continue
            vals.append(PropValue(vid=vid, name=str(v.get("name") or ""),
                                  image=v.get("image")))
        if vals:
            sale_props.append(SaleProp(pid=str(p.get("pid") or ""),
                                       name=str(p.get("name") or ""), values=vals))

    # SKU
    sku2info = dig(obj, "skuCore", "sku2info", default={}) or {}
    skus: list[Sku] = []
    for s in (dig(obj, "skuBase", "skus", default=[]) or []):
        sid = str(s.get("skuId") or "")
        if not sid:
            continue
        info = sku2info.get(sid) or {}
        price = dig(info, "price", "priceText", default=None)
        qty = info.get("quantity")
        try:
            price_f = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price_f = None
        try:
            qty_i = int(qty) if qty not in (None, "") else None
        except (TypeError, ValueError):
            qty_i = None
        attrs: dict[str, str] = {}
        pp = str(s.get("propPath") or "")
        for seg in pp.split(";"):
            if ":" in seg:
                k, v = seg.split(":", 1)
                attrs[k] = v
        skus.append(Sku(sku_id=sid, attrs=attrs, prop_path=pp,
                        price=price_f, quantity=qty_i))

    # 单规格兜底
    if not skus and sku2info:
        d = sku2info.get("0") or {}
        if d:
            p = dig(d, "price", "priceText", default=None)
            try:
                price_f = float(p) if p not in (None, "") else None
            except (TypeError, ValueError):
                price_f = None
            skus.append(Sku(sku_id=item_id, attrs={}, price=price_f,
                            quantity=int(d.get("quantity") or 0)))

    prices = [s.price for s in skus if s.price]
    price_info = PriceInfo(
        min=min(prices) if prices else None,
        max=max(prices) if prices else None,
        origin=min(prices) if prices else None,
    )

    stock_total = None
    if skus and all(s.quantity is not None for s in skus):
        stock_total = sum(s.quantity or 0 for s in skus)

    seller_raw = obj.get("seller") or {}
    seller = Seller(
        seller_id=str(seller_raw.get("userId") or seller_raw.get("sellerId") or "") or None,
        nick=seller_raw.get("sellerNick"),
        shop_name=seller_raw.get("shopName"),
    )

    return Item(
        item_id=item_id,
        platform="taobao",
        source_url=source_url or f"https://item.taobao.com/item.htm?id={item_id}",
        title=title,
        seller=seller,
        category=resolve_category(category_cid),
        price=price_info,
        main_images=main_imgs,
        sale_props=sale_props,
        skus=skus,
        params=collect_params(obj),
        stock_total=stock_total,
        extra={"source": "html_embedded_json",
               "spu_id": it.get("spuId"),
               "videos_raw": videos},
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/import_page_json.py <extracted.json> [item_id]")
        return 2
    p = Path(sys.argv[1])
    if not p.exists():
        print("[!] 不存在: %s" % p)
        return 1
    obj = json.loads(p.read_text(encoding="utf-8"))

    item_id = sys.argv[2] if len(sys.argv) > 2 else None
    if not item_id:
        m = re.search(r"dom_(\d{8,20})_", p.name)
        item_id = m.group(1) if m else str(dig(obj, "item", "itemId", default="") or "")

    # 类目 ID：优先从同名 HTML 里抠（extracted.json 里没有 leafCategory），
    # 找不到再退回 --cid 参数。
    category_cid = None
    html_candidates = [p.with_suffix("").with_suffix(".html"),
                       p.parent / (p.stem.replace(".extracted", "") + ".html")]
    for hp in html_candidates:
        if hp.exists():
            root, leaf = find_category_ids(hp.read_text(encoding="utf-8", errors="ignore"))
            if leaf:
                category_cid = leaf
                print("[OK] 从 %s 抠到 leafCategory=%s rootCategory=%s"
                      % (hp.name, leaf, root))
                break
    if not category_cid:
        for i, a in enumerate(sys.argv):
            if a == "--cid" and i + 1 < len(sys.argv):
                category_cid = sys.argv[i + 1]
    if not category_cid:
        print("[!] 没找到 leafCategory（HTML 里没有 / 天猫页无此字段），category.cid 会是 unknown")

    item = build_item(obj, item_id, category_cid=category_cid)
    print(item.summary())
    print("\n参数明细:")
    for x in item.params:
        print("   %-18s %s" % (x.name, x.value[:50]))
    print("\nSKU:")
    for s in item.skus:
        print("   %-18s %-22s ¥%-8s 库存%s" % (s.sku_id, s.prop_path, s.price, s.quantity))

    path = save_item(item)
    print("\n已写入 %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
