"""mtop 客户端：签名、令牌刷新、Cookie 管理、失败重试。

核心公式（淘宝 / 天猫 / 1688 通用）::

    sign = md5( token + "&" + t + "&" + appKey + "&" + data )

其中 token 取 Cookie 里 ``_m_h5_tk`` 下划线前的部分，t 为毫秒时间戳，
data 必须与最终 URL 中 ``data=`` 的字符串逐字节一致。
"""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from ..core.config import get_settings
from .ratelimit import CooldownActive, RateLimiter

FAIL_TOKEN_EMPTY = "FAIL_SYS_TOKEN_EMPTY"
FAIL_TOKEN_EXPIRED = "FAIL_SYS_TOKEN_EXOIRED"   # 平台原文拼写错误，照抄
FAIL_TOKEN_EXPIRED2 = "FAIL_SYS_TOKEN_EXPIRED"
FAIL_ILLEGAL_ACCESS = "FAIL_SYS_ILLEGAL_ACCESS"
FAIL_USER_VALIDATE = "FAIL_SYS_USER_VALIDATE"
RISK_CODES = (FAIL_USER_VALIDATE, "RGV587_ERROR", "FAIL_SYS_TRAFFIC_LIMIT")


class MTopError(RuntimeError):
    def __init__(self, code: str, message: str = "", payload: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.payload = payload or {}


class MTopClient:
    """一个实例 = 一个站点 + 一个账号会话，内部复用连接与令牌。"""

    def __init__(
        self,
        site: str = "taobao",
        cookie_file: str | Path | None = None,
        cookies: dict[str, str] | None = None,
        app_key: str | None = None,
        timeout: float | None = None,
    ):
        s = get_settings()
        self.site = site
        self.app_key = app_key or s.get("mtop.app_key", "12574478")
        self.host = s.get(f"mtop.hosts.{site}", "h5api.m.taobao.com")
        self.token_ttl = int(s.get("mtop.token_ttl", 86400))
        timeout = timeout or float(s.get("http.timeout", 15))
        self.ua = s.get("http.user_agent_taobao")
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_at: float = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.ua,
                "Referer": f"https://{'item.taobao.com' if site == 'taobao' else 'detail.1688.com'}/",
                "Accept": "application/json",
            },
        )
        if cookies:
            for k, v in cookies.items():
                self.client.cookies.set(k, v)
        if cookie_file:
            self.load_cookies(cookie_file)
        # 所有出站请求必须过限速器；site 就是平台名
        self.limiter = RateLimiter(site)

    # ------------------------------------------------------------ cookies
    def load_cookies(self, path: str | Path) -> None:
        """支持两种格式：``k=v; k=v`` 单行，或 Netscape cookies.txt。"""
        p = Path(path)
        if not p.exists():
            return
        text = p.read_text(encoding="utf-8", errors="ignore")
        pairs: dict[str, str] = {}
        if "\t" in text:  # netscape
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    pairs[parts[5]] = parts[6]
        else:
            for chunk in text.replace("\n", ";").split(";"):
                if "=" in chunk:
                    k, v = chunk.split("=", 1)
                    pairs[k.strip()] = v.strip()
        for k, v in pairs.items():
            self.client.cookies.set(k, v, domain=self.host)

    def cookie(self, name: str) -> str | None:
        return self.client.cookies.get(name)

    # ------------------------------------------------------------ token
    def token(self) -> str:
        with self._lock:
            if self._token and time.time() - self._token_at < self.token_ttl:
                return self._token
            tk = self.cookie("_m_h5_tk") or ""
            self._token = tk.split("_")[0] if tk else ""
            self._token_at = time.time()
            return self._token

    def refresh_token(self) -> str:
        """空跑一次请求，让服务端 Set-Cookie 下发新的 _m_h5_tk。"""
        with self._lock:
            self._token = ""
            self._token_at = 0.0
        self._raw_call("mtop.common.getTimestamp", "1.0", {}, allow_fail=True)
        return self.token()

    # ------------------------------------------------------------ sign
    def sign(self, t: str, data: str) -> str:
        raw = f"{self.token()}&{t}&{self.app_key}&{data}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ call
    def call(self, api: str, version: str = "1.0", data: dict | None = None,
             retries: int = 2) -> dict[str, Any]:
        data = data or {}
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return self._raw_call(api, version, data)
            except MTopError as e:
                last = e
                if e.code in (FAIL_TOKEN_EMPTY, FAIL_TOKEN_EXPIRED, FAIL_TOKEN_EXPIRED2):
                    self.refresh_token()
                    continue
                if e.code in RISK_CODES:
                    raise
                if attempt >= retries:
                    raise
                time.sleep(1.5 * (attempt + 1) + random.random())
            except httpx.HTTPError as e:
                last = e
                if attempt >= retries:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _raw_call(self, api: str, version: str, data: dict,
                  allow_fail: bool = False) -> dict[str, Any]:
        # 限速：冷却期内会直接抛 CooldownActive，不会偷偷发出去
        self.limiter.acquire()

        t = str(int(time.time() * 1000))
        data_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        params = {
            "jsv": "2.6.1",
            "appKey": self.app_key,
            "t": t,
            "sign": self.sign(t, data_str),
            "api": api,
            "v": version,
            "type": "json",
            "dataType": "json",
            "data": data_str,
        }
        url = f"https://{self.host}/h5/{api}/{version}/?{urlencode(params, quote_via=quote)}"
        resp = self.client.get(url)
        resp.raise_for_status()
        body = resp.text

        # 先看正文里有没有风控信号，命中就熔断（写进持久化冷却，跨进程生效）
        risk = self.limiter.note_body(body)
        if risk:
            raise MTopError(risk, f"命中风控信号，{self.site} 已进入冷却期", {"body": body[:500]})

        try:
            payload = resp.json()
        except ValueError:
            raise MTopError("NON_JSON", body[:200], {"text": body[:2000]})

        ret = payload.get("ret") or []
        code = str(ret[0]) if ret else ""
        ok = code.startswith("SUCCESS")
        if not ok and not allow_fail:
            short = code.split("::")[0]
            # 平台错误码也过一遍熔断规则
            if short in ("FAIL_SYS_USER_VALIDATE", "RGV587_ERROR", "FAIL_SYS_SESSION_EXPIRED"):
                self.limiter.on_error(short)
            raise MTopError(short, code, payload)
        return payload

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MTopClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
