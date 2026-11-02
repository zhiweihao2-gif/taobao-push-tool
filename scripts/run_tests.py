"""统一测试运行器。

项目里有两类测试：

1. **独立 runner**（多数）—— 文件里有 `PASS, FAIL = 0, 0` 和 `check()` 函数，
   直接 `python tests/xxx.py` 跑，末尾打印「通过 N / 失败 M」。
2. **pytest 风格** —— 用 `class TestXxx(unittest.TestCase)` 或 `def test_xxx()`，
   需要 pytest 收集。

本脚本自动识别并分别跑，最后汇总。

用法::

    python scripts/run_tests.py              # 全部
    python scripts/run_tests.py -k browser   # 只跑文件名含 browser 的
    python scripts/run_tests.py --stop       # 第一个失败就停
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

_STANDALONE_MARK = re.compile(r"^PASS,\s*FAIL\s*=\s*0", re.M)
_TALLY = re.compile(r"通过\s+(\d+)\s*/\s*失败\s+(\d+)")


def is_standalone(path: Path) -> bool:
    try:
        return bool(_STANDALONE_MARK.search(path.read_text(encoding="utf-8", errors="replace")))
    except Exception:  # noqa: BLE001
        return False


def run_standalone(py: str, path: Path) -> tuple[int, int, str]:
    """返回 (通过, 失败, 摘要)。"""
    try:
        proc = subprocess.run(
            [py, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return 0, 1, "超时(300s)"

    out = (proc.stdout or "") + (proc.stderr or "")
    m = None
    for m in _TALLY.finditer(out):
        pass
    if m:
        p, f = int(m.group(1)), int(m.group(2))
        return p, f, f"通过 {p} / 失败 {f}"
    if proc.returncode != 0:
        return 0, 1, f"异常退出 exit={proc.returncode}"
    return 0, 0, "无计数（可能没有断言）"


def run_pytest(py: str, paths: list[Path]) -> tuple[int, int, str]:
    """用 pytest 跑一批文件，解析 "N passed" / "M failed"。"""
    if not paths:
        return 0, 0, ""
    cmd = [py, "-m", "pytest", *[str(p) for p in paths], "-q", "--tb=short"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return 0, 1, "超时(600s)"

    out = (proc.stdout or "") + (proc.stderr or "")
    passed = sum(int(x) for x in re.findall(r"(\d+) passed", out))
    failed = sum(int(x) for x in re.findall(r"(\d+) (?:failed|error)", out))
    detail = " ".join(
        line.strip() for line in out.splitlines() if re.search(r"\d+ (passed|failed|error)", line)
    )[-160:]
    return passed, failed, detail or f"exit={proc.returncode}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跑全部测试")
    ap.add_argument("-k", default="", help="只跑文件名包含该子串的")
    ap.add_argument("--stop", action="store_true", help="第一个失败就停")
    ap.add_argument("--py", default=sys.executable, help="Python 解释器")
    args = ap.parse_args(argv)

    files = sorted(TESTS.glob("test_*.py"))
    if args.k:
        files = [f for f in files if args.k in f.name]
    if not files:
        print(f"[XX] {TESTS} 下没有匹配的测试文件")
        return 2

    standalone = [f for f in files if is_standalone(f)]
    pytest_style = [f for f in files if not is_standalone(f)]

    print("=" * 62)
    print(f"独立 runner : {len(standalone)} 个")
    print(f"pytest 风格 : {len(pytest_style)} 个")
    print("=" * 62)

    total_pass = total_fail = 0
    bad: list[str] = []

    for f in standalone:
        p, fl, summary = run_standalone(args.py, f)
        total_pass += p
        total_fail += fl
        mark = "OK " if fl == 0 else "FAIL"
        print(f"  [{mark}] {f.name:28} {summary}")
        if fl:
            bad.append(f.name)
            if args.stop:
                break

    if pytest_style and not (args.stop and bad):
        p, fl, summary = run_pytest(args.py, pytest_style)
        total_pass += p
        total_fail += fl
        mark = "OK " if fl == 0 else "FAIL"
        names = ", ".join(f.name for f in pytest_style)
        print(f"  [{mark}] pytest ({names})")
        print(f"         {summary}")
        if fl:
            bad.extend(f.name for f in pytest_style)

    print("-" * 62)
    print(f"合计：通过 {total_pass}  失败 {total_fail}")
    if bad:
        print(f"问题：{', '.join(bad)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
