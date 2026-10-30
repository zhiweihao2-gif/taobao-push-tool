"""文案清洗：极限词、品牌词、联系方式、长度控制。

源商品文案直接搬运是最常见的违规来源，发布前必须过这一层。
"""
from __future__ import annotations

import re

from ..core.config import get_settings

DEFAULT_PATTERNS = [
    r"正品", r"官方", r"旗舰", r"专卖", r"最低价", r"最便宜", r"第一", r"顶级",
    r"国家级", r"史上最低", r"包邮", r"秒杀", r"清仓", r"[0-9]{11}",
    r"(微信|VX|vx|QQ)[:：]?\s*\S+",
]


class TextSanitizer:
    def __init__(self, extra_patterns: list[str] | None = None,
                 brand_blacklist: list[str] | None = None):
        s = get_settings()
        patterns = s.get("sanitize.remove_patterns", DEFAULT_PATTERNS) or DEFAULT_PATTERNS
        patterns = list(patterns) + list(extra_patterns or [])
        self.patterns = [re.compile(p, re.I) for p in patterns]
        self.brand_blacklist = [b for b in (brand_blacklist or []) if b]
        self.max_len = int(s.get("sanitize.max_title_length", 60))
        self.fill_keywords = s.get("sanitize.fill_keywords", []) or []

    def clean_title(self, title: str) -> str:
        out = title or ""
        for p in self.patterns:
            out = p.sub("", out)
        for b in self.brand_blacklist:
            out = re.sub(re.escape(b), "", out, flags=re.I)
        out = re.sub(r"[【】\[\]（）()<>《》]", " ", out)
        out = re.sub(r"\s+", " ", out).strip(" -_|,，。")

        if len(out) > self.max_len:
            out = out[: self.max_len]
        # 长度不足时补通用词，避免标题过短被判低质
        i = 0
        while len(out) < 12 and i < len(self.fill_keywords):
            out = f"{out} {self.fill_keywords[i]}".strip()
            i += 1
        return out

    def clean_html(self, html: str) -> str:
        """去掉详情里的联系方式与站外链接，保留图片与基本排版。"""
        out = html or ""
        for p in self.patterns:
            out = p.sub("", out)
        out = re.sub(r'<a\b[^>]*>(.*?)</a>', r"\1", out, flags=re.S | re.I)
        out = re.sub(r"https?://(?!img\.alicdn|img\.taobao|gw\.alicdn)[^\s\"'<>]+", "", out)
        return out

    def check(self, title: str) -> list[str]:
        """返回标题里命中的违规片段，供 Validator 做发布前拦截。"""
        hits: list[str] = []
        for p in self.patterns:
            hits += p.findall(title or "")
        hits += [b for b in self.brand_blacklist if b.lower() in (title or "").lower()]
        return hits
