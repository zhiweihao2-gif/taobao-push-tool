"""把「商品链接 / 短链 / 推广链 / 纯数字」统一解析成商品 ID。

命令行（run_demo.py）和 Web API（/collect）都要用，所以抽成公共模块——
之前只写在 run_demo 里，导致 Web 端拿到完整 URL 当 ID 用，
最后崩在「文件名语法不正确」上。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ID_PATTERNS = (
    r"[?&](?:id|itemId|item_id|offerId|sku)=(\d{6,})",
    r"/(?:item|offer|product|detail)/(\d{6,})",
    r"/(\d{8,})(?:\.html)?",
)

# 已知的推广/短链域名：这些必须跟随跳转才能拿到真实 ID
REDIRECT_HINTS = ("click.simba", "tb.cn", "uland.taobao", "ai.taobao",
                  "s.click.taobao", "u.jd.com", "3.cn")


class ResolveError(ValueError):
    """解析不出商品 ID。"""


def _dig_id(text: str) -> str | None:
    for pat in ID_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _follow(url: str) -> str:
    """跟随跳转，从最终 URL 或页面内容里挖商品 ID。"""
    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        r = c.get(url)
        found = _dig_id(str(r.url))
        if found:
            return found
        found = _dig_id(r.text[:20000])
        if found:
            return found
        for pat in (r'item\.taobao\.com/item\.htm\?id=(\d+)',
                    r'detail\.tmall\.com/item\.htm\?id=(\d+)',
                    r'detail\.1688\.com/offer/(\d+)',
                    r'item\.jd\.com/(\d+)'):
            m = re.search(pat, r.text)
            if m:
                return m.group(1)
    raise ResolveError(
        "这个链接里找不到商品 ID（可能是推广链接、店铺首页或已失效）。\n"
        "  正确格式：https://item.taobao.com/item.htm?id=123456789\n"
        "  或者直接输入数字 ID：123456789")


def resolve_item_id(text: str, platform: str = "taobao") -> str:
    """把各种输入统一成纯数字 ID。解析不出来抛 :class:`ResolveError`。"""
    raw = (text or "").strip().lstrip("\ufeff").strip().strip('"').strip("'").strip()
    if not raw:
        raise ResolveError("没有输入内容")

    # 1. 纯数字
    if raw.isdigit():
        return raw

    is_url = ("://" in raw) or raw.startswith("//") or "." in raw.split("/")[0]

    # 2. 链接里直接带 id=（最常见，不走网络）
    if is_url:
        found = _dig_id(raw)
        if found:
            return found

    # 3. 需要跟随跳转
    if is_url:
        url = raw if "://" in raw else "https://" + raw.lstrip("/")
        host = (urlparse(url).hostname or "").lower()
        try:
            return _follow(url)
        except ResolveError:
            # 推广链接没有 id 时给更具体的提示
            if any(h in host for h in REDIRECT_HINTS) or "ali_refid" in raw:
                raise ResolveError(
                    "这是一个推广/广告链接，里面没有商品 ID。\n"
                    "  请打开商品详情页，复制浏览器地址栏里带 id= 的链接，例如：\n"
                    "  https://item.taobao.com/item.htm?id=123456789\n"
                    "  或者直接输入那串数字。")
            raise
        except Exception as e:
            raise ResolveError(f"访问链接失败（{type(e).__name__}）：{url[:80]}") from e

    raise ResolveError(
        f"看不懂这个输入：{raw[:60]}\n"
        "  请粘贴商品详情页链接（带 id= 的那种），或直接输入数字 ID。")
