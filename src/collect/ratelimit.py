"""全局请求限速 + 熔断冷却（跨进程持久化）。

设计原则：**限速不靠调用方自觉，所有出站请求必须经过这里。**

三层保护：
1. 最小间隔     —— 两次请求之间强制 sleep
2. 每小时配额   —— 超过就阻塞到最早那条记录过期
3. 熔断冷却     —— 一旦平台返回风控/滑块，写入持久化冷却期，**所有进程**都停下

冷却状态存在 SQLite（`data/app.db`）里，不是内存，所以：
- 我这个 agent 调试时触发滑块 → 你之后跑脚本也会被拦住
- 反过来你触发 → 我这边也会停

用法::

    from src.collect.ratelimit import guard
    with guard("taobao") as g:
        resp = client.get(url)
        g.note_body(resp.text)      # 自动识别风控并熔断

或者手动::

    limiter = RateLimiter("taobao")
    limiter.acquire()               # 阻塞直到允许
    ...
    limiter.on_error("SLIDER_REQUIRED")
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..core.config import get_settings

# 每平台限额：min_interval 秒/次，per_hour 次/小时
LIMITS: dict[str, dict[str, float]] = {
    "taobao": {"min_interval": 3.0, "per_hour": 100},
    "tmall": {"min_interval": 3.0, "per_hour": 100},
    "alibaba": {"min_interval": 4.0, "per_hour": 80},
    "jd": {"min_interval": 1.5, "per_hour": 400},
}
DEFAULT_LIMIT = {"min_interval": 3.0, "per_hour": 100}

# 熔断时长（秒）：命中这些错误后，该平台整体停摆
COOLDOWN_SECONDS: dict[str, int] = {
    "SLIDER_REQUIRED": 30 * 60,     # 滑块：30 分钟
    "RISK_CONTROL": 15 * 60,        # 风控：15 分钟
    "ANTI_BOT": 20 * 60,            # 1688 反爬：20 分钟
    "SESSION_EXPIRED": 0,           # 登录过期不需要冷却，重新登录即可
    "TOKEN_EMPTY": 0,               # 刷 token 即可，不冷却
}

# 正文里出现这些就视为熔断信号
RISK_MARKERS: dict[str, str] = {
    "FAIL_SYS_USER_VALIDATE": "SLIDER_REQUIRED",
    "RGV587_ERROR": "RISK_CONTROL",
    "_____tmd_____": "ANTI_BOT",
    "x5secdata": "SLIDER_REQUIRED",
    "punish": "ANTI_BOT",
    "FAIL_SYS_SESSION_EXPIRED": "SESSION_EXPIRED",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ratelimit_state (
  platform TEXT PRIMARY KEY,
  last_request REAL NOT NULL DEFAULT 0,
  cooldown_until REAL NOT NULL DEFAULT 0,
  cooldown_reason TEXT NOT NULL DEFAULT '',
  total_requests INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ratelimit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rl_platform_ts ON ratelimit_log(platform, ts);
"""

_thread_lock = threading.Lock()


class CooldownActive(RuntimeError):
    """平台处于熔断冷却期，拒绝发请求。"""

    def __init__(self, platform: str, remaining: float, reason: str):
        self.platform = platform
        self.remaining = remaining
        self.reason = reason
        super().__init__(
            f"{platform} 处于冷却期，还需等待 {remaining / 60:.1f} 分钟"
            f"（原因：{reason or '风控'}）。\n"
            f"  不要重试，等冷却结束，或重新登录刷新登录态。"
        )


def _conn() -> sqlite3.Connection:
    s = get_settings()
    c = sqlite3.connect(s.db_path, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


class RateLimiter:
    def __init__(self, platform: str):
        self.platform = (platform or "").lower()
        self.limit = LIMITS.get(self.platform, DEFAULT_LIMIT)

    # ------------------------------------------------------------ 状态
    def status(self) -> dict:
        now = time.time()
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM ratelimit_state WHERE platform=?", (self.platform,)
            ).fetchone()
            hour_ago = now - 3600
            used = c.execute(
                "SELECT COUNT(*) FROM ratelimit_log WHERE platform=? AND ts>?",
                (self.platform, hour_ago),
            ).fetchone()[0]
        if not row:
            return {"platform": self.platform, "cooldown_remaining": 0,
                    "cooldown_reason": "", "used_last_hour": used,
                    "per_hour": self.limit["per_hour"],
                    "min_interval": self.limit["min_interval"],
                    "last_request_ago": None, "total": 0}
        return {
            "platform": self.platform,
            "cooldown_remaining": max(0.0, row["cooldown_until"] - now),
            "cooldown_reason": row["cooldown_reason"],
            "used_last_hour": used,
            "per_hour": self.limit["per_hour"],
            "min_interval": self.limit["min_interval"],
            "last_request_ago": (now - row["last_request"]) if row["last_request"] else None,
            "total": row["total_requests"],
        }

    def cooldown_remaining(self) -> float:
        return self.status()["cooldown_remaining"]

    # ------------------------------------------------------------ 取票
    def acquire(self, block: bool = True, max_wait: float = 120.0) -> float:
        """申请一次请求许可。返回实际等待秒数。

        block=False 时，需要等待就抛 CooldownActive。
        """
        waited = 0.0
        while True:
            with _thread_lock, _conn() as c:
                now = time.time()
                row = c.execute(
                    "SELECT * FROM ratelimit_state WHERE platform=?",
                    (self.platform,),
                ).fetchone()

                # 1. 熔断冷却
                if row and row["cooldown_until"] > now:
                    remain = row["cooldown_until"] - now
                    if not block:
                        raise CooldownActive(self.platform, remain,
                                             row["cooldown_reason"])
                    # 冷却期内绝不自作主张等待，直接报错更安全
                    raise CooldownActive(self.platform, remain,
                                         row["cooldown_reason"])

                # 2. 最小间隔
                last = row["last_request"] if row else 0.0
                gap = self.limit["min_interval"] - (now - last)
                if gap > 0:
                    if not block or waited + gap > max_wait:
                        raise CooldownActive(self.platform, gap, "min_interval")
                    time.sleep(gap)
                    waited += gap
                    continue

                # 3. 每小时配额
                hour_ago = now - 3600
                used = c.execute(
                    "SELECT COUNT(*) FROM ratelimit_log WHERE platform=? AND ts>?",
                    (self.platform, hour_ago),
                ).fetchone()[0]
                if used >= self.limit["per_hour"]:
                    oldest = c.execute(
                        "SELECT ts FROM ratelimit_log WHERE platform=? AND ts>? "
                        "ORDER BY ts LIMIT 1", (self.platform, hour_ago),
                    ).fetchone()
                    wait = (oldest["ts"] + 3600 - now) if oldest else 60.0
                    if not block or waited + wait > max_wait:
                        raise CooldownActive(self.platform, wait, "hourly_quota")
                    time.sleep(max(1.0, wait))
                    waited += wait
                    continue

                # 4. 记账
                c.execute(
                    """INSERT INTO ratelimit_state(platform, last_request, total_requests)
                       VALUES(?,?,1)
                       ON CONFLICT(platform) DO UPDATE SET
                         last_request=excluded.last_request,
                         total_requests=total_requests+1""",
                    (self.platform, now),
                )
                c.execute("INSERT INTO ratelimit_log(platform, ts) VALUES(?,?)",
                          (self.platform, now))
                # 清掉一小时前的旧记录，避免表无限增长
                c.execute("DELETE FROM ratelimit_log WHERE ts < ?", (now - 7200,))
                return waited

    # ------------------------------------------------------------ 熔断
    def on_error(self, code: str, detail: str = "") -> int:
        """记录一次风控事件，返回冷却秒数（0 表示不需要冷却）。"""
        seconds = COOLDOWN_SECONDS.get(code, 0)
        if seconds <= 0:
            return 0
        until = time.time() + seconds
        with _thread_lock, _conn() as c:
            c.execute(
                """INSERT INTO ratelimit_state(platform, cooldown_until, cooldown_reason)
                   VALUES(?,?,?)
                   ON CONFLICT(platform) DO UPDATE SET
                     cooldown_until=excluded.cooldown_until,
                     cooldown_reason=excluded.cooldown_reason""",
                (self.platform, until, f"{code} {detail}".strip()),
            )
        return seconds

    def note_body(self, text: str) -> str | None:
        """从响应正文里识别风控信号，命中就熔断并返回错误码。"""
        if not text:
            return None
        for marker, code in RISK_MARKERS.items():
            if marker in text:
                self.on_error(code)
                return code
        return None

    def reset_cooldown(self) -> None:
        """人工解除冷却（比如已经重新登录、确认风控解除）。"""
        with _thread_lock, _conn() as c:
            c.execute(
                "UPDATE ratelimit_state SET cooldown_until=0, cooldown_reason='' "
                "WHERE platform=?", (self.platform,),
            )


@contextmanager
def guard(platform: str) -> Iterator[RateLimiter]:
    """with guard("taobao") as g: ...  自动 acquire。"""
    limiter = RateLimiter(platform)
    limiter.acquire()
    yield limiter


def all_status() -> list[dict]:
    return [RateLimiter(p).status() for p in ("taobao", "alibaba", "jd")]
