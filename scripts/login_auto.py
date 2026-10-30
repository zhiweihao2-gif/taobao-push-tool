"""傻瓜版登录：弹出浏览器 → 你扫码 → 脚本自动发现登录成功 → 保存 cookie → 自动退出。

不需要按回车，不需要懂技术。

用法::

    python scripts/login_auto.py --platform taobao
    python scripts/login_auto.py --platform alibaba
    python scripts/login_auto.py --platform jd

也建议直接双击项目根目录的 `1-登录淘宝.bat`。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import SessionHealth, SessionStore  # noqa: E402

LOGIN_URLS = {
    "taobao": "https://login.taobao.com/",
    "tmall": "https://login.taobao.com/",
    "alibaba": "https://login.1688.com/",
    "jd": "https://passport.jd.com/new/login.aspx",
}

# 判定"已经登录成功"的 cookie 组合（任一组合出现即算成功）
#
# 关键教训：cookie2 / _tb_token_ 在**未登录**的匿名会话里也存在，拿它们当成功标志
# 会导致脚本在你还没扫码完时就以为登录成功、存下一份残缺 cookie。
# 淘宝真正只有登录后才出现的是 _l_g_ 和 tracknick，所以这里只认它们。
SUCCESS_MARKERS = {
    "taobao": [("_l_g_",), ("tracknick",)],
    "tmall": [("_l_g_",), ("tracknick",)],
    "alibaba": [("__cn_logon__",), ("lid",), ("_nk_",)],
    "jd": [("thor",)],
}

PLATFORM_CN = {"taobao": "淘宝", "tmall": "淘宝", "alibaba": "1688", "jd": "京东"}


def say(msg: str = "") -> None:
    print(msg, flush=True)


def wait_login(page, context, platform: str, timeout_sec: int = 300) -> dict[str, str]:
    """轮询 cookie + 页面 URL，直到确认登录成功。返回 cookie dict。

    判定必须同时满足：
      1. 出现登录专属 cookie（淘宝是 _l_g_ / tracknick）
      2. 页面已经离开登录页（URL 里不再有 login / passport）
    只有 1 不满足 2 时，可能是登录跳转还没完成，继续等。
    """
    markers = SUCCESS_MARKERS.get(platform, [("_l_g_",)])
    deadline = time.time() + timeout_sec
    last_tip = 0.0
    while time.time() < deadline:
        try:
            url = page.url or ""
        except Exception:
            url = ""
        on_login_page = any(k in url for k in ("login", "passport", "signin"))

        cookies = {c["name"]: c["value"] for c in context.cookies()}
        has_marker = any(all(n in cookies for n in combo) for combo in markers)

        if has_marker and not on_login_page:
            time.sleep(1.5)                      # 等 cookie 落定
            return {c["name"]: c["value"] for c in context.cookies()}

        now = time.time()
        if now - last_tip > 20:
            left = int(deadline - now)
            if has_marker and on_login_page:
                say(f"    检测到登录 cookie，但页面还在登录页，等待跳转…（剩余 {left} 秒）")
            else:
                say(f"    等待中… 请在浏览器里完成登录（剩余 {left} 秒）")
            last_tip = now
        time.sleep(2)
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="taobao",
                    choices=["taobao", "tmall", "alibaba", "jd"])
    ap.add_argument("--shop", default="default")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    platform = args.platform
    name_cn = PLATFORM_CN.get(platform, platform)

    say("=" * 56)
    say(f"  登录{name_cn} —— 只需要做一件事：扫码")
    say("=" * 56)
    say()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        say("！缺少 playwright，请先执行：")
        say("  python -m pip install playwright")
        say("  python -m playwright install chromium")
        return 2

    store = SessionStore()
    out_dir = ROOT / "config" / "cookies"
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        say("正在打开浏览器…（第一次会慢几秒）")
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True, locale="zh-CN")
        page = context.new_page()
        page.goto(LOGIN_URLS.get(platform, LOGIN_URLS["taobao"]),
                  wait_until="domcontentloaded", timeout=60000)

        say()
        say(f"  → 请在弹出的浏览器里登录你的{name_cn}账号")
        say("  → 扫码或输密码都可以，登录成功后脚本会自动保存并关闭")
        say()

        cookies = wait_login(page, context, platform, args.timeout)

        if not cookies:
            say("× 超时了，没检测到登录成功。")
            say("  可能是没登录完，或者登录页要求滑块。再跑一次这个脚本即可。")
            try:
                browser.close()
            except Exception:
                pass
            return 1

        # 保存三种格式，兼容所有下游代码
        state_path = out_dir / f"{platform}.storage.json"
        try:
            context.storage_state(path=str(state_path))
        except Exception:
            pass

        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        (out_dir / f"{platform}.txt").write_text(header, encoding="utf-8")
        store.save_cookies(platform, cookies, shop=args.shop)

        say()
        status = SessionHealth(store).check(platform, shop=args.shop)
        if status.ok:
            say("✓ 登录成功，cookie 已保存")
            say(f"  文件：{store.path(platform, shop=args.shop)}")
            say(f"  共 {len(cookies)} 个 cookie，体检：OK")
        else:
            say("！cookie 已保存，但看起来登录还没完成")
            say(f"  体检结果：{status.reason}" +
                (f"，缺 {', '.join(status.missing)}" if status.missing else ""))
            say()
            say("  请确认：浏览器里是不是真的进到了淘宝首页（右上角显示你的用户名）？")
            say("  如果没进去，再双击一次 1-登录淘宝.bat 重来。")

        say()
        say("  窗口 3 秒后自动关闭，可以直接去采集了。")
        time.sleep(3)
        try:
            browser.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
