"""采集器基类 + 通用工具。"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.models import Item, RawItem


# ---------------------------------------------------------------- 取值工具

def dig(obj: Any, *paths: str, default: Any = None) -> Any:
    """按多个候选路径取值，返回第一个非 None。

    dig(data, "skuCore.sku2info.123.price.priceText",
             "skuCore.sku2info.123.price", default="0")
    """
    for path in paths:
        node = obj
        ok = True
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                ok = False
                break
        if ok and node is not None:
            return node
    return default


def maybe_json(value: Any) -> Any:
    """apiStack 的 value 常是字符串化 JSON，递归解一层。"""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "{[":
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def api_stack_map(api_stack: Any) -> dict[str, Any]:
    """把 apiStack 统一成 {name: 已解析对象}，兼容 list / dict / 字符串三种形态。"""
    out: dict[str, Any] = {}
    if isinstance(api_stack, str):
        api_stack = maybe_json(api_stack)
    if isinstance(api_stack, list):
        for entry in api_stack:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("api") or ""
            out[name] = maybe_json(entry.get("value"))
    elif isinstance(api_stack, dict):
        for k, v in api_stack.items():
            out[k] = maybe_json(v)
    return out


# ---------------------------------------------------------------- 图片 URL

_SIZE_SUFFIX = re.compile(r"(\.(?:jpg|jpeg|png|webp|gif))_[^/]*$", re.I)


def clean_image_url(url: str, host_prefix: str = "https:") -> str:
    """补全协议 + 去掉 CDN 尺寸后缀，拿到原图地址。"""
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = host_prefix + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    url = _SIZE_SUFFIX.sub(r"\1", url)
    # 淘宝常见 _!!0-item_pic.jpg 形式，保留；仅清理查询串里的尺寸参数
    url = re.sub(r"\?.*$", "", url) if "alicdn.com" in url else url
    return url


_IMG_SRC = re.compile(
    r"""<img[^>]+?(?:src|data-src|data-ks-lazyload|data-lazyload)\s*=\s*["']([^"']+)["']""",
    re.I,
)


def extract_img_urls(html: str) -> list[str]:
    """从详情富文本里按出现顺序抽图片，去重。"""
    if not html:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _IMG_SRC.finditer(html):
        u = clean_image_url(m.group(1))
        if not u or u.startswith("data:"):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def strip_tags(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- 基类

class BaseCollector:
    platform: str = ""

    def fetch(self, item_id: str) -> RawItem:
        raise NotImplementedError

    def to_item(self, raw: RawItem) -> Item:
        raise NotImplementedError

    def collect(self, item_id: str) -> Item:
        raw = self.fetch(item_id)
        return self.to_item(raw)

    def source_url(self, item_id: str) -> str:
        raise NotImplementedError
