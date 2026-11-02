"""一键启动：检查登录 → 需要就扫码 → 起服务 → 自动开浏览器。

把原来「1-登录淘宝.bat」和「6-打开工作台.bat」两步合成一步，
由 ``0-开始.bat`` 调用。

行为：
- 登录态有效   → 直接起服务 + 开浏览器
- 登录态无效   → 先弹浏览器扫码，成功后自动继续
- 服务已占用   → 直接打开浏览器，不重复起
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.ratelimit import RateLimiter  # noqa: E402
from src.collect.session import SessionHealth, SessionStore  # noqa: E402

HOST = "127.0.0.1"
PORT = 8848
URL = f"http://{HOST}:{PORT}/"
PLATFORM = "taobao"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def port_open(host: str = HOST, port: int = PORT, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def wait_port(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open():
            return True
        time.sleep(0.4)
    return False


def check_login() -> tuple[bool, str]:
    store = SessionStore()
    st = SessionHealth(store).check(PLATFORM)
    return st.ok, st.reason


def do_login() -> bool:
    """调 login_auto.py 走扫码流程（前台，用户看得见）。"""
    say()
    say("=" * 56)
    say("  需要登录淘宝：浏览器会弹出，用手机淘宝扫码")
    say("=" * 56)
    say()
    script = ROOT / "scripts" / "login_auto.py"
    code = subprocess.call([sys.executable, str(script), "--platform", PLATFORM])
    if code != 0:
        return False
    ok, reason = check_login()
    return ok


def main() -> int:
    say()
    say("=" * 56)
    say("  一键铺货 · 启动中")
    say("=" * 56)

    # 1. 服务已经在跑？直接开浏览器
    if port_open():
        say()
        say(f"  服务已在运行（端口 {PORT}），直接打开浏览器")
        webbrowser.open(URL)
        say()
        say(f"  → {URL}")
        say("  这个窗口可以关了。")
        return 0

    # 2. 登录态
    ok, reason = check_login()
    say()
    if ok:
        say(f"  ✓ 登录态正常（{reason}）")
    else:
        say(f"  ! 登录态 {reason}，需要先登录")
        if not do_login():
            say()
            say("  × 登录没成功。再跑一次这个启动器即可。")
            return 1
        say("  ✓ 登录成功")
        # 登录后解除因过期导致的冷却
        try:
            lim = RateLimiter(PLATFORM)
            if lim.cooldown_remaining() > 0:
                lim.reset_cooldown()
                say("  ✓ 已解除冷却")
        except Exception:
            pass

    # 3. 起服务
    say()
    say(f"  启动服务 {URL} …")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.main:app",
         "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT),
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )

    if not wait_port():
        say("  × 服务没能启动，看上面的报错")
        try:
            proc.terminate()
        except Exception:
            pass
        return 1

    say(f"  ✓ 服务已就绪")
    webbrowser.open(URL)

    say()
    say("=" * 56)
    say(f"  工作台已打开：{URL}")
    say()
    say("  这个窗口别关 —— 关了服务就停。")
    say("  按 Ctrl+C 停止。")
    say("=" * 56)
    say()

    # 4. 保持前台
    try:
        proc.wait()
    except KeyboardInterrupt:
        say()
        say("  正在停止服务…")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        say("  已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
