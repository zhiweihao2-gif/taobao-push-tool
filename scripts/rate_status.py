"""查看/管理各平台的请求限速与熔断状态。

用法::

    python scripts/rate_status.py
    python scripts/rate_status.py --reset taobao     # 确认风控已解除时手动解封
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.ratelimit import RateLimiter, all_status  # noqa: E402

NAMES = {"taobao": "淘宝", "alibaba": "1688", "jd": "京东"}


def fmt_sec(s: float) -> str:
    if s <= 0:
        return "-"
    if s < 60:
        return f"{s:.0f} 秒"
    if s < 3600:
        return f"{s / 60:.0f} 分钟"
    return f"{s / 3600:.1f} 小时"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", metavar="PLATFORM",
                    help="解除某平台的冷却（taobao / alibaba / jd）")
    args = ap.parse_args()

    if args.reset:
        limiter = RateLimiter(args.reset)
        limiter.reset_cooldown()
        print(f"已解除 {NAMES.get(args.reset, args.reset)} 的冷却。")
        print("确认平台风控真的解除了再用，否则会立刻再次熔断。")
        return 0

    print()
    print(f"{'平台':<8}{'状态':<16}{'本小时已用':<14}{'最小间隔':<12}{'累计请求'}")
    print("-" * 70)
    for st in all_status():
        name = NAMES.get(st["platform"], st["platform"])
        if st["cooldown_remaining"] > 0:
            state = f"熔断 {fmt_sec(st['cooldown_remaining'])}"
        else:
            state = "正常"
        used = f"{st['used_last_hour']}/{int(st['per_hour'])}"
        print(f"{name:<8}{state:<16}{used:<14}{st['min_interval']:<12.1f}{st['total']}")

    print()
    for st in all_status():
        if st["cooldown_remaining"] > 0:
            name = NAMES.get(st["platform"], st["platform"])
            print(f"  {name} 冷却原因：{st['cooldown_reason'] or '风控'}")
            print(f"  还需等待：{fmt_sec(st['cooldown_remaining'])}")
            print(f"  解除方式：python scripts/rate_status.py --reset {st['platform']}")
            print()
    print("  说明：冷却状态存在 data/app.db，跨进程生效。")
    print("        一旦触发滑块/风控，所有脚本都会被拦住，不会继续加重。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
