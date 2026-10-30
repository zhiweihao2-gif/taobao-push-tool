"""登录态管理：cookie 落盘、有效性体检、风控错误识别。

三件事，各管一段：

* ``SessionStore`` —— 把浏览器里导出的 cookie 收下来（``k=v; k=v`` 单行或
  Netscape ``cookies.txt``），统一落成 ``config/cookies/<platform>.json``，
  读的时候无脑给 ``httpx`` 用。老的纯文本 ``<platform>.txt`` 继续能读。
* ``SessionHealth`` —— 不联网的体检：cookie 名齐不齐、文件放了多久。
  ``_m_h5_tk`` 这类令牌 24 小时就换，所以淘宝 / 1688 的过期阈值单独压到 24h。
* ``RiskDetector`` —— 平台返回体里那些看不懂的错误串，映射成稳定 code，
  采集器按 code 决定「刷 token 重试」还是「喊人重新登录」。

落盘结构::

    {
      "platform": "taobao",
      "shop": "default",
      "saved_at": "2026-10-30T14:40:00",
      "cookies": {"cookie2": "...", "_tb_token_": "..."},
      "domains": {"cookie2": ".taobao.com"}      # 仅 Netscape 来源才有
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.config import get_settings

# 各平台必需 cookie；内层 tuple 表示「命中任一即算有」
#
# 注意：``_m_h5_tk`` **不在**必需项里。它是 mtop 的运行时令牌，由服务端在第一次
# h5api 请求时通过 Set-Cookie 下发，采集器（MTopClient.refresh_token）会自动获取，
# 用户导出 cookie 时本来就不该有它。把它当必需项会误判成「登录不完整」。
REQUIRED_COOKIES: dict[str, tuple[tuple[str, ...], ...]] = {
    # _l_g_ 只有真正登录后才有；cookie2 / _tb_token_ 匿名会话也存在
    "taobao": (("cookie2",), ("_tb_token_",), ("_l_g_", "tracknick")),
    "tmall": (("cookie2",), ("_tb_token_",), ("_l_g_", "tracknick")),
    "alibaba": (("cookie2",), ("__cn_logon__", "lid")),
    "1688": (("cookie2",), ("__cn_logon__", "lid")),
    # 京东没有 token 签名，thor 是登录票据，pin 有则更好但可以缺
    "jd": (("thor",),),
}

# 超过多少小时算「不新鲜」：淘宝系令牌约 24h，其它按 7 天
STALE_HOURS: dict[str, int] = {
    "taobao": 24,
    "tmall": 24,
    "alibaba": 24,
    "1688": 24,
}
DEFAULT_STALE_HOURS = 7 * 24

JD_EMPTY_TITLE = "京东(JD.COM)-正品低价"
JD_DATA_MARKERS = ('"skuId"', '"price"', "商品编号", "item.jd.com/", "京东价")


# ---------------------------------------------------------------- cookie 解析
def parse_cookie_text(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """解析 cookie 文本，返回 ``(cookies, domains)``。

    认两种格式：``k=v; k=v`` 单行头，以及 Netscape ``cookies.txt``
    （TAB 分隔 7 列，第 6 列名、第 7 列值，``#HttpOnly_`` 前缀照收）。
    """
    text = text or ""
    cookies: dict[str, str] = {}
    domains: dict[str, str] = {}
    if not text.strip():
        return cookies, domains

    lines = text.splitlines()
    netscape = any(len(ln.split("\t")) >= 7 for ln in lines)
    if netscape:
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            elif line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            name, value = parts[5].strip(), parts[6].strip()
            if name:
                cookies[name] = value
                domains[name] = parts[0].strip()
        return cookies, domains

    body = text.replace("Cookie:", "").replace("\r", ";").replace("\n", ";")
    for chunk in body.split(";"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies, domains


def dump_cookie_header(cookies: dict[str, str]) -> str:
    """把 dict 拼回 ``k=v; k=v`` 单行，直接塞 ``Cookie:`` 头。"""
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k)


# ---------------------------------------------------------------- 存储
class SessionStore:
    """cookie 落盘 / 读取；默认目录取 ``get_settings().cookie_dir``。"""

    def __init__(self, cookie_dir: str | Path | None = None) -> None:
        self.cookie_dir = Path(cookie_dir) if cookie_dir else get_settings().cookie_dir
        self.cookie_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 路径
    def path(self, platform: str, shop: str = "default", suffix: str = ".json") -> Path:
        """``<platform><suffix>``；非默认店铺加后缀 ``<platform>.<shop>``。"""
        stem = platform if shop in ("", "default") else f"{platform}.{shop}"
        return self.cookie_dir / f"{stem}{suffix}"

    def txt_path(self, platform: str, shop: str = "default") -> Path:
        return self.path(platform, shop, ".txt")

    # ------------------------------------------------------------ 写
    def save_cookie_header(self, platform: str, text: str, shop: str = "default") -> Path:
        """解析 ``k=v; k=v`` 或 Netscape cookies.txt，落盘成 JSON，返回路径。"""
        cookies, domains = parse_cookie_text(text)
        if not cookies:
            raise ValueError(f"{platform}: cookie 文本里没解析出任何 k=v")
        payload: dict[str, Any] = {
            "platform": platform,
            "shop": shop,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "cookies": cookies,
        }
        if domains:
            payload["domains"] = domains
        target = self.path(platform, shop)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def save_cookies(self, platform: str, cookies: dict[str, str],
                     shop: str = "default",
                     domains: dict[str, str] | None = None) -> Path:
        """已解析好的 dict 直接落盘（Playwright 导出走这条更省事）。"""
        payload: dict[str, Any] = {
            "platform": platform,
            "shop": shop,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "cookies": {str(k): str(v) for k, v in cookies.items()},
        }
        if domains:
            payload["domains"] = {str(k): str(v) for k, v in domains.items()}
        target = self.path(platform, shop)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    # ------------------------------------------------------------ 读
    def load(self, platform: str, shop: str = "default") -> dict[str, str]:
        """返回 ``{name: value}``；文件不存在返回空 dict（不抛异常）。"""
        target = self.path(platform, shop)
        if target.exists():
            data = self._read_json(target)
            cookies = data.get("cookies")
            if isinstance(cookies, dict) and cookies:
                return {str(k): str(v) for k, v in cookies.items()}
        for fallback in (self.txt_path(platform, shop),
                         self.cookie_dir / f"{platform}.txt"):
            if fallback.exists():
                parsed, _ = parse_cookie_text(
                    fallback.read_text(encoding="utf-8", errors="ignore"))
                if parsed:
                    return parsed
        return {}

    def exists(self, platform: str, shop: str = "default") -> bool:
        return (self.path(platform, shop).exists()
                or self.txt_path(platform, shop).exists()
                or (self.cookie_dir / f"{platform}.txt").exists())

    def saved_at(self, platform: str, shop: str = "default") -> datetime | None:
        """JSON 里的 saved_at 优先，没有就看文件 mtime。"""
        target = self.path(platform, shop)
        if target.exists():
            ts = self._read_json(target).get("saved_at")
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts)
                except ValueError:
                    pass
            return datetime.fromtimestamp(target.stat().st_mtime)
        for fallback in (self.txt_path(platform, shop),
                         self.cookie_dir / f"{platform}.txt"):
            if fallback.exists():
                return datetime.fromtimestamp(fallback.stat().st_mtime)
        return None

    def cookie_header(self, platform: str, shop: str = "default") -> str:
        """拼成可以直接放进请求头的 ``k=v; k=v``。"""
        return dump_cookie_header(self.load(platform, shop))

    def to_cookiejar_dict(self, platform: str, shop: str = "default",
                          domain: str | None = None) -> dict[str, str]:
        """给 ``httpx.Client(cookies=...)`` 的 ``{name: value}``。

        传 ``domain`` 时：有域名信息（Netscape 来源）就按域名过滤，
        纯文本来源不知道域名，全量返回。
        """
        cookies = self.load(platform, shop)
        if not domain or not cookies:
            return cookies
        domains = self.domains(platform, shop)
        if not domains:
            return cookies
        want = domain.lower().lstrip(".")
        out: dict[str, str] = {}
        for name, value in cookies.items():
            host = str(domains.get(name, "")).lower().lstrip(".")
            if not host or host == want or want.endswith(host) or host.endswith(want):
                out[name] = value
        return out

    def domains(self, platform: str, shop: str = "default") -> dict[str, str]:
        target = self.path(platform, shop)
        if not target.exists():
            return {}
        raw = self._read_json(target).get("domains")
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- 体检
@dataclass
class SessionStatus:
    """体检结果。``ok=False`` 时看 ``reason``；``INCOMPLETE`` 时看 ``missing``。"""

    ok: bool = False
    reason: str = "NO_COOKIE_FILE"
    missing: list[str] = field(default_factory=list)
    age_hours: float = 0.0

    def __bool__(self) -> bool:
        return self.ok

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason,
                "missing": list(self.missing), "age_hours": self.age_hours}


class SessionHealth:
    """离线体检：不联网，只看 cookie 名是否齐 + 文件有多旧。"""

    REQUIRED = REQUIRED_COOKIES
    STALE = STALE_HOURS

    def __init__(self, store: SessionStore | None = None) -> None:
        self.store = store or SessionStore()

    def check(self, platform: str, shop: str = "default") -> SessionStatus:
        platform = (platform or "").lower()
        cookies = self.store.load(platform, shop)
        if not cookies:
            return SessionStatus(False, "NO_COOKIE_FILE", [], 0.0)
        age = self.age_hours(platform, shop)
        missing = self.missing(platform, cookies)
        if missing:
            return SessionStatus(False, "INCOMPLETE", missing, age)
        limit = STALE_HOURS.get(platform, DEFAULT_STALE_HOURS)
        if age > limit:
            return SessionStatus(False, "STALE", [], age)
        return SessionStatus(True, "OK", [], age)

    def missing(self, platform: str, cookies: dict[str, str]) -> list[str]:
        groups = REQUIRED_COOKIES.get((platform or "").lower(), ())
        return [group[0] for group in groups
                if not any(cookies.get(name) for name in group)]

    def age_hours(self, platform: str, shop: str = "default") -> float:
        saved = self.store.saved_at(platform, shop)
        if saved is None:
            return 0.0
        delta = (datetime.now() - saved).total_seconds()
        return round(max(delta, 0.0) / 3600.0, 2)


# ---------------------------------------------------------------- 风控
class RiskDetector:
    """把平台返回体里的错误串翻译成稳定 code + 一句人话提示。"""

    def classify(self, platform: str, status_code: int,
                 body_text: str) -> tuple[str, str]:
        """返回 ``(code, hint)``，按优先级从具体到宽泛。"""
        text = body_text or ""
        platform = (platform or "").lower()
        lowered = text.lower()

        if "FAIL_SYS_TOKEN_EMPTY" in text:
            return "TOKEN_EMPTY", "令牌为空，需先刷 token 重试"
        if "RGV587_ERROR" in text:
            return "RISK_CONTROL", "淘宝风控拦截，需要登录态（cookie2/_tb_token_）或过滑块"
        if "FAIL_SYS_USER_VALIDATE" in text or "x5sec" in lowered:
            return "SLIDER_REQUIRED", "需要过滑块验证"
        if "FAIL_SYS_SESSION_EXPIRED" in text:
            return "SESSION_EXPIRED", "登录态过期，重新导出 cookie"
        if "_____tmd_____" in text or (platform in ("alibaba", "1688") and "punish" in lowered):
            return "ANTI_BOT", "1688 反爬拦截，需要登录+滑块"
        if status_code == 404:
            return "NOT_FOUND", "商品不存在或已下架"
        if platform == "jd" and JD_EMPTY_TITLE in text and not any(
                marker in text for marker in JD_DATA_MARKERS):
            return "NOT_FOUND", "商品不存在或已下架"
        return "OK", ""
