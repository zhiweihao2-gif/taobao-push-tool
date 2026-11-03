"""详情图清洗：把服务端切片的 URL 还原成原图并按原始纵向顺序排。

踩的坑：
  淘宝详情图长图会被服务端切成多段返回，URL 形如
      .../O1CN01RhScJE2Jc2P39sFtw~crop,0,0,790,1580~_!!2217235289441.png
      .../O1CN01RhScJE2Jc2P39sFtw~crop,0,1580,790,220~_!!2217235289441.png
  同一个 O1CN01 id 被切两段（上段 1580 高、下段 220 高）。直接按抓取顺序插图，
  段序会乱、同一张图重复出现。

  正确处理：
    1. 用正则剥掉 ~crop,x,y,w,h~ → 得到原图 URL
    2. 从 crop 参数里读出 y 偏移，按 (O1CN id, y) 排序 → 恢复原始顺序
    3. 按 O1CN id 去重（同一张长图只保留一张）
    4. 剔除主图（正方形、边长 ≥1000）、超大图（>4000 通常是主图原图）、
       占位符（10x10）、横幅条（高 <200）

数据来源优先级：
  mtop.taobao.detail.getdesc v6.0 的 pcDescContent 里每张 <img> 都带
  size="790x1580"，尺寸可直接读；拿不到时退回 URL 里的 -tps-W-H 后缀。
"""
from __future__ import annotations

import re
from urllib.parse import unquote

_CROP = re.compile(r"~crop,(\d+),(\d+),(\d+),(\d+)~")
_OID = re.compile(r"/(O1CN\w+?)(?:~|_|\?|$)")
_SIZE = re.compile(r"(\.(?:jpg|jpeg|png|webp|gif))_[^/]*$")


def _oid(url: str) -> str:
    m = _OID.search(url)
    if m:
        return m.group(1)
    return url.split("/")[-1].split("?")[0]


def clean_detail_images(urls: list[str], sizes: list[list[int]] | None = None,
                        exclude_main_square: int = 1000, max_side: int = 4200,
                        min_side: int = 50, min_height: int = 200) -> list[str]:
    """返回去重、按原始纵向顺序排列的原图 URL 列表。

    sizes: 与 urls 等长的 [[w, h], ...]，来自 <img size="WxH">；缺省时按 URL 推断。
    """
    size_map: dict[str, list[int]] = {}
    if sizes and len(sizes) == len(urls):
        size_map = {u: s for u, s in zip(urls, sizes)}

    rows = []
    for idx, u in enumerate(urls):
        y = 0
        m = _CROP.search(u)
        if m:
            y = int(m.group(2))
        base = _CROP.sub("", u)
        base = base.replace("_.webp", "")
        base = _SIZE.sub(r"\1", base)
        base = unquote(base)

        w = h = 0
        if u in size_map:
            sz = size_map[u]
            w, h = (sz[0], sz[1]) if len(sz) >= 2 else (0, 0)

        rows.append({"idx": idx, "url": base, "oid": _oid(base), "y": y,
                     "w": w, "h": h})

    # 1) 同一张图的多段：保留 y 最小的（即原图本身，不带 crop）
    by_oid: dict[str, dict] = {}
    for r in rows:
        cur = by_oid.get(r["oid"])
        if cur is None or r["y"] < cur["y"]:
            by_oid[r["oid"]] = r

    picked = sorted(by_oid.values(), key=lambda r: (r["y"], r["idx"]))

    # 2) 剔主图、超大图、装饰小图
    out = []
    for r in picked:
        w, h = r["w"], r["h"]
        if w and h:
            if w < min_side or h < min_side:
                continue          # 10x10 是透明占位符
            if h < min_height:
                continue          # 750x128 这类是横幅条/分隔图，不是商品详情
            if abs(w - h) <= w * 0.05 and w >= exclude_main_square:
                continue          # 正方形大图 = 主图
            if max(w, h) > max_side:
                continue          # 5000x4943 这类超尺寸通常是主图原始文件
        out.append(r["url"])

    # 3) 去重（剥 crop 后可能撞车）
    seen, uniq = set(), []
    for u in out:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq
