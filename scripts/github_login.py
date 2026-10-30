"""GitHub 登录助手 v2：持久化浏览器 profile + cookie 检测 + 自动建 token。

v1 的缺陷：靠反复 `page.goto()` 判断登录状态，会打断登录流程，导致永远检测不到。
v2 改为：
- 用 `launch_persistent_context` 持久化浏览器 profile，**登录一次以后都不用再登**
- 只读 cookie（`user_session` / `logged_in`）判断状态，不导航、不干扰页面
- 登录成功后自动建 token，抓取并保存

用法::

    python scripts/github_login.py            # 登录 + 建 token
    python scripts/github_login.py --check    # 只看状态
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN_FILE = ROOT / "config" / "github_token.txt"
STATE_FILE = ROOT / "config" / "github.storage.json"
PROFILE_DIR = ROOT / "config" / "browser_profile"

LOGIN_URL = "https://github.com/login"
NEW_TOKEN_URL = "https://github.com/settings/tokens/new"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def logged_in(ctx) -> bool:
    """只读 cookie，不导航。GitHub 登录后一定有 user_session。"""
    try:
        names = {c["name"] for c in ctx.cookies()}
    except Exception:
        return False
    return "user_session" in names


def get_username(ctx, page) -> str | None:
    """从已登录的上下文里取用户名（优先 API，其次页面）。"""
    try:
        r = ctx.request.get("https://api.github.com/user", timeout=15000)
        if r.ok:
            data = r.json()
            if data.get("login"):
                return data["login"]
    except Exception:
        pass
    try:
        page.goto("https://github.com/settings/profile",
                  wait_until="domcontentloaded", timeout=30000)
        time.sleep(1.5)
        m = re.search(r'"currentUser"\s*:\s*"([^"]+)"', page.content())
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def wait_login(ctx, timeout: int = 600) -> bool:
    """轮询 cookie，不碰页面。"""
    deadline = time.time() + timeout
    last_tip = 0.0
    while time.time() < deadline:
        if logged_in(ctx):
            return True
        now = time.time()
        if now - last_tip > 20:
            say(f"    等待登录… 剩余 {int(deadline - now)} 秒")
            last_tip = now
        time.sleep(2)
    return False


def create_token(page, note: str = "taobao-tool-push") -> str | None:
    say("\n  正在创建 token…")
    page.goto(NEW_TOKEN_URL, wait_until="domcontentloaded", timeout=45000)
    time.sleep(2.5)

    # sudo 模式（部分账号会要求重新输密码）
    if "sudo" in page.url:
        say("  ! GitHub 要求重新验证身份，请在浏览器里完成（等 120 秒）")
        for _ in range(40):
            if "/settings/tokens/new" in page.url:
                break
            time.sleep(3)
        time.sleep(1)

    filled = False
    for sel in ('#oauth_access_description',
                'input[name="oauth_access[description]"]',
                'input[name="description"]'):
        try:
            el = page.locator(sel).first
            if el.count():
                el.fill(note)
                filled = True
                break
        except Exception:
            continue
    say(f"    填写备注: {'✓' if filled else '×'}")

    checked = False
    for sel in ('#scopes_repo', 'input[value="repo"]',
                'input[name="oauth_access[scopes][]"][value="repo"]'):
        try:
            el = page.locator(sel).first
            if el.count():
                el.check(force=True)
                checked = True
                break
        except Exception:
            continue
    say(f"    勾选 repo: {'✓' if checked else '×'}")

    submitted = False
    for sel in ('button:has-text("Generate token")', 'button[type="submit"]',
                'input[type="submit"]'):
        try:
            el = page.locator(sel).last
            if el.count():
                el.click(timeout=10000)
                submitted = True
                break
        except Exception:
            continue
    say(f"    提交: {'✓' if submitted else '×'}")
    if not submitted:
        return None

    time.sleep(4)
    pat = re.compile(r"(ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")
    for _ in range(12):
        m = pat.search(page.content())
        if m:
            return m.group(1)
        try:
            m = pat.search(page.inner_text("body"))
            if m:
                return m.group(1)
        except Exception:
            pass
        time.sleep(1.5)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    headless = bool(args.check)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if args.check:
            ok = logged_in(ctx)
            user = get_username(ctx, page) if ok else None
            say(f"登录状态: {'✓ 已登录 ' + (user or '') if ok else '× 未登录'}")
            ctx.close()
            return 0 if ok else 1

        say("检查 GitHub 登录状态…")
        if logged_in(ctx):
            say("  ✓ 已经登录过（持久化 profile 记住了）")
        else:
            say()
            say("=" * 56)
            say("  请在打开的浏览器里登录 GitHub")
            say("=" * 56)
            say("  → 输入账号密码，有 2FA 就输验证码")
            say("  → 登录成功后脚本自动继续")
            say("  → 这次登录会被记住，以后不用再登")
            say()
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            if not wait_login(ctx, args.timeout):
                say("\n× 超时，没检测到登录成功。再跑一次即可（会复用已登录状态）。")
                ctx.close()
                return 1

        user = get_username(ctx, page)
        say(f"\n✓ 已登录：{user or '(取不到用户名)'}")

        try:
            ctx.storage_state(path=str(STATE_FILE))
        except Exception:
            pass

        token = create_token(page)
        ctx.close()

    if not token:
        say("\n× 自动创建 token 失败。")
        say("  手动方式：打开 https://github.com/settings/tokens/new")
        say("  勾选 repo → Generate token → 把 ghp_ 开头那串发我")
        return 1

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    say("\n✓ token 已生成并保存")
    say(f"  用户: {user}")
    say(f"  文件: {TOKEN_FILE}（在 .gitignore 里，不会被提交）")
    say(f"  前缀: {token[:7]}…{token[-4:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
