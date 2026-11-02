"""启动一个带调试端口的独立 Chrome（不影响你日常用的那个）。

为什么要独立 profile：Chrome 只允许「首次用某个 user-data-dir 启动时」打开
远程调试端口。如果你直接给日常 profile 加参数，Chrome 会因为已有实例而忽略它。

用法::

    python scripts/start_chrome_debug.py            # 启动并打开卖家中心
    python scripts/start_chrome_debug.py --port 9222
    python scripts/start_chrome_debug.py --check    # 只检查是否已在跑
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import check_chrome_alive, list_pages  # noqa: E402

DEFAULT_PROFILE = Path(os.environ.get("LOCALAPPDATA", "C:/")) / "dsh-chrome-debug"
SELLER_HOME = "https://myseller.taobao.com/home.htm"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str | None:
    for p in CHROME_CANDIDATES:
        if p and Path(p).exists():
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="启动带调试端口的 Chrome")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE))
    ap.add_argument("--check", action="store_true", help="只检查，不启动")
    ap.add_argument("--no-open", action="store_true", help="启动后不自动打开卖家中心")
    args = ap.parse_args(argv)

    cdp = f"http://127.0.0.1:{args.port}"

    ok, msg = check_chrome_alive(cdp)
    if ok:
        print(f"[OK] 调试端口已在运行：{msg}")
        pages = list_pages(cdp)
        if pages:
            print(f"[..] 当前 {len(pages)} 个标签页：")
            for i, p in enumerate(pages[:8], 1):
                print(f"     {i}. {(p.get('title') or '?')[:40]:42} {(p.get('url') or '')[:60]}")
        if args.check:
            return 0
        print()
        print("如果这个 Chrome 还没登录卖家中心，请在里面打开：")
        print(f"  {SELLER_HOME}")
        return 0

    if args.check:
        print(f"[XX] {msg}")
        return 2

    exe = find_browser()
    if not exe:
        print("[XX] 没找到 Chrome 或 Edge。请手动启动：")
        print(f'  "<Chrome路径>" --remote-debugging-port={args.port} --user-data-dir="{args.profile}"')
        return 2

    profile = Path(args.profile)
    profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe,
        f"--remote-debugging-port={args.port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if not args.no_open:
        cmd.append(SELLER_HOME)

    print(f"[..] 启动 {Path(exe).name}")
    print(f"[..] profile: {profile}")
    print(f"[..] 端口   : {args.port}")
    subprocess.Popen(cmd, close_fds=True)

    # 等端口起来。新 profile 首次启动要建目录、初始化，实测 Edge 约 8~15 秒。
    for _ in range(60):
        time.sleep(0.5)
        ok, msg = check_chrome_alive(cdp, timeout=2.0)
        if ok:
            print(f"[OK] 已就绪：{msg}")
            print()
            print("=" * 64)
            print("接下来：在弹出的浏览器里登录卖家中心")
            print(f"  {SELLER_HOME}")
            print("扫码登录完成后，回来运行：")
            print("  python scripts/publish_poc.py check")
            print("=" * 64)
            return 0

    print("[XX] 启动了但端口没起来（等了 30 秒）")
    print("     可能原因：另一个实例已用同一个 --user-data-dir 在跑")
    print(f"     试着手动启动：\"{exe}\" --remote-debugging-port={args.port} --user-data-dir=\"{profile}\"")
    return 3


if __name__ == "__main__":
    sys.exit(main())
