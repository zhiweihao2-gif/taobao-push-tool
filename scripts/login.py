"""扫码登录引导：开一个真浏览器，你自己登，脚本只负责把 cookie 收下来。

用法::

    python scripts/login.py --platform taobao --shop default
    python scripts/login.py --platform alibaba
    python scripts/login.py --platform jd

流程：打开登录页 → 你在窗口里扫码 / 输密码 → 回到终端按回车 →
导出 ``config/cookies/<platform>.storage.json``（Playwright 完整环境）、
``config/cookies/<platform>.txt``（``k=v; k=v`` 单行）以及
``config/cookies/<platform>.json``（SessionStore 格式，带 saved_at）。

⚠️ 这一步是人工交互的：脚本不会代填账号密码、不碰验证码、不上传任何数据，
跑脚本的人自己登录自己的账号，账号风险自负。

playwright 没装时会打印安装命令并以退出码 2 结束，不抛栈。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import (  # noqa: E402
    SessionHealth,
    SessionStore,
    dump_cookie_header,
)

LOGIN_URLS: dict[str, str] = {
    "taobao": "https://login.taobao.com/",
    "alibaba": "https://login.1688.com/",
    "jd": "https://passport.jd.com/new/login.aspx",
}

INSTALL_HINT = (
    "[!] 没装 playwright，装一下再来：\n"
    "    & $py -m pip install playwright\n"
    "    & $py -m playwright install chromium\n"
    "    （$py = 本项目用的 python 解释器）"
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="打开登录页，人工登录后导出 cookie 到 config/cookies/")
    p.add_argument("--platform", required=True, choices=sorted(LOGIN_URLS),
                   help="taobao | alibaba | jd")
    p.add_argument("--shop", default="default",
                   help="店铺标识，默认 default；多店铺时区分文件")
    p.add_argument("--url", default=None, help="覆盖默认登录页 URL（可选）")
    return p


def _export(store: SessionStore, platform: str, shop: str,
            cookies: list[dict]) -> tuple[Path, Path, Path]:
    """把 Playwright 的 cookies 列表落盘成三份文件。"""
    pairs = {c["name"]: c["value"] for c in cookies if c.get("name")}
    domains = {c["name"]: c.get("domain", "") for c in cookies if c.get("name")}
    header = dump_cookie_header(pairs)

    txt_path = store.txt_path(platform, shop)
    txt_path.write_text(header, encoding="utf-8")
    json_path = store.save_cookies(platform, pairs, shop=shop,
                                   domains={k: v for k, v in domains.items() if v})
    storage_path = store.path(platform, shop, ".storage.json")
    return json_path, txt_path, storage_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    platform, shop = args.platform, args.shop

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(INSTALL_HINT)
        return 2

    store = SessionStore()
    url = args.url or LOGIN_URLS[platform]
    storage_path = store.path(platform, shop, ".storage.json")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=False)
        except Exception as exc:                      # 浏览器二进制缺失
            print(f"[!] Chromium 起不来：{exc}\n    先跑：& $py -m playwright install chromium")
            return 2
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            print(f"[!] 打开 {url} 失败：{exc}\n    检查网络或换 --url 试试")
            context.close()
            browser.close()
            return 1

        print(f"[*] 已打开 {url}")
        print("[*] 请在浏览器窗口里扫码 / 登录（脚本不代填账号密码）。")
        print(f"[*] 登录成功后 {platform} 首页能正常打开，再回到这里按回车导出 cookie ...")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("\n[!] 没确认，放弃导出。")
            context.close()
            browser.close()
            return 1

        context.storage_state(path=str(storage_path))
        json_path, txt_path, _ = _export(store, platform, shop, context.cookies())
        context.close()
        browser.close()

    health = SessionHealth(store).check(platform, shop)
    print(f"[+] 存储态：{storage_path}")
    print(f"[+] 纯文本：{txt_path}")
    print(f"[+] 结构化：{json_path}")
    if health.ok:
        print(f"[+] 体检通过（cookie 年龄 {health.age_hours}h）")
    else:
        print(f"[!] 体检未过：{health.reason}，缺 {health.missing or '-'}")
        print("    可能登录没完成，或该平台这次没下发全必需 cookie；重跑一次本脚本即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
