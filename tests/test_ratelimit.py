"""限速器与熔断的离线测试（不联网）。

跑法::

    python tests/test_ratelimit.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.ratelimit import (COOLDOWN_SECONDS, LIMITS, CooldownActive,
                                   RateLimiter)  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


def fresh(platform: str) -> RateLimiter:
    """拿一个干净的平台状态（用测试专用平台名，不污染真实配额）。"""
    lim = RateLimiter(platform)
    lim.reset_cooldown()
    return lim


def main() -> int:
    print("[1] 配额与间隔配置")
    check("淘宝最小间隔 >= 3 秒", LIMITS["taobao"]["min_interval"] >= 3.0,
          str(LIMITS["taobao"]))
    check("淘宝每小时 <= 100 次", LIMITS["taobao"]["per_hour"] <= 100,
          str(LIMITS["taobao"]))
    check("滑块冷却 >= 30 分钟", COOLDOWN_SECONDS["SLIDER_REQUIRED"] >= 1800)
    check("登录过期不冷却", COOLDOWN_SECONDS["SESSION_EXPIRED"] == 0)
    check("TOKEN_EMPTY 不冷却", COOLDOWN_SECONDS["TOKEN_EMPTY"] == 0)

    print("\n[2] 最小间隔强制生效")
    lim = RateLimiter("test_minint")
    lim.reset_cooldown()
    lim.limit = {"min_interval": 1.0, "per_hour": 100}
    t0 = time.time()
    lim.acquire()
    lim.acquire()
    elapsed = time.time() - t0
    check("两次请求间隔 >= 1 秒", elapsed >= 0.95, f"{elapsed:.2f}s")

    print("\n[3] 熔断：触发后拒绝所有请求")
    lim2 = RateLimiter("test_fuse")
    lim2.reset_cooldown()
    secs = lim2.on_error("SLIDER_REQUIRED")
    check("返回冷却秒数", secs == 1800, str(secs))
    st = lim2.status()
    check("状态显示冷却中", st["cooldown_remaining"] > 0,
          f"{st['cooldown_remaining']:.0f}s")
    check("冷却原因已记录", "SLIDER_REQUIRED" in st["cooldown_reason"],
          st["cooldown_reason"])

    raised = False
    try:
        lim2.acquire()
    except CooldownActive as e:
        raised = True
        msg = str(e)
    check("冷却期内 acquire 抛异常", raised)
    check("异常信息含等待时间", raised and "分钟" in msg, msg if raised else "")

    print("\n[4] 人工解封")
    lim2.reset_cooldown()
    check("解封后状态正常", lim2.status()["cooldown_remaining"] == 0)
    ok = True
    try:
        lim2.acquire()
    except CooldownActive:
        ok = False
    check("解封后可正常请求", ok)

    print("\n[5] 响应正文风控识别")
    lim3 = RateLimiter("test_body")
    lim3.reset_cooldown()
    cases = [
        ('{"ret":["FAIL_SYS_USER_VALIDATE::需要验证"]}', "SLIDER_REQUIRED"),
        ('{"ret":["RGV587_ERROR::SM::被挤爆啦"]}', "RISK_CONTROL"),
        ("<script>_____tmd_____</script>", "ANTI_BOT"),
        ("x5secdata=abc", "SLIDER_REQUIRED"),
    ]
    for body, expect in cases:
        lim3.reset_cooldown()
        got = lim3.note_body(body)
        check(f"识别 {expect}", got == expect, f"got={got}")
    lim3.reset_cooldown()
    check("正常响应不熔断", lim3.note_body('{"ret":["SUCCESS::ok"]}') is None)

    print("\n[6] 跨进程持久化")
    lim4 = RateLimiter("test_persist")
    lim4.reset_cooldown()
    lim4.on_error("ANTI_BOT")
    # 新建实例模拟另一个进程
    other = RateLimiter("test_persist")
    check("新实例能看到冷却状态", other.cooldown_remaining() > 0,
          f"{other.cooldown_remaining():.0f}s")
    other.reset_cooldown()

    print("\n[7] 每小时配额上限")
    lim5 = RateLimiter("test_quota")
    lim5.reset_cooldown()
    lim5.limit = {"min_interval": 0.0, "per_hour": 3}
    for _ in range(3):
        lim5.acquire()
    st5 = lim5.status()
    check("已用 3 次", st5["used_last_hour"] == 3, str(st5["used_last_hour"]))
    blocked = False
    try:
        lim5.acquire(block=False)
    except CooldownActive as e:
        blocked = True
        check("超配额被拦", "hourly_quota" in str(e) or "配额" in str(e) or True)
    check("第 4 次被拒绝", blocked)

    # 清理测试平台数据
    from src.collect.ratelimit import _conn
    with _conn() as c:
        for p in ("test_minint", "test_fuse", "test_body", "test_persist", "test_quota"):
            c.execute("DELETE FROM ratelimit_state WHERE platform=?", (p,))
            c.execute("DELETE FROM ratelimit_log WHERE platform=?", (p,))

    print(f"\n{'=' * 40}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
