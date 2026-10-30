"""检查三个平台的登录态是否可用（不联网，只看本地 cookie 文件）。

用法::

    python scripts/check_login.py

也建议直接双击 `3-检查登录状态.bat`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import SessionHealth, SessionStore  # noqa: E402

NAMES = {"taobao": "淘宝", "tmall": "天猫", "alibaba": "1688", "jd": "京东"}

REASON_CN = {
    "OK": "✓ 正常，可以采集",
    "NO_COOKIE_FILE": "× 还没登录过",
    "INCOMPLETE": "× 登录不完整（需要重新登录）",
    "STALE": "× 登录过期了（需要重新登录）",
}

MISSING_CN = {
    "_l_g_": "淘宝登录凭证",
    "tracknick": "淘宝用户名",
    "cookie2": "会话标识",
    "_tb_token_": "页面令牌",
    "__cn_logon__": "1688 登录凭证",
    "lid": "1688 登录标识",
    "thor": "京东登录票据",
}


def main() -> int:
    store = SessionStore()
    health = SessionHealth(store)

    print()
    for platform in ("taobao", "alibaba", "jd"):
        name = NAMES.get(platform, platform)
        status = health.check(platform)
        cookies = store.load(platform)
        print(f"  {name:<6} {REASON_CN.get(status.reason, status.reason)}")
        print(f"         本地 cookie {len(cookies)} 个", end="")
        if cookies:
            age = status.age_hours
            print(f"，保存于 {age:.1f} 小时前")
        else:
            print()
        if status.missing:
            readable = [MISSING_CN.get(m, m) for m in status.missing]
            print(f"         缺少：{'、'.join(readable)}")
            print(f"         解决：双击 1-登录{'淘宝' if platform == 'taobao' else ('1688' if platform == 'alibaba' else '京东')}.bat")
        print()

    print("  说明：京东不登录也能采集部分数据；淘宝/1688 必须登录。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
