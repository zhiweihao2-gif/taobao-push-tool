"""关掉所有 publish.htm 标签页，给下一次跑腾出干净环境。

用法：
    python scripts/reset_tabs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.publish.browser_pub import BrowserSession  # noqa: E402


def main() -> int:
    with BrowserSession("http://127.0.0.1:9222", timeout_ms=30000) as s:
        pages = list(s._context.pages)
        print("共 %d 个标签页" % len(pages))
        killed = 0
        for p in pages:
            try:
                u = p.url
            except Exception:
                u = ""
            mark = ""
            if "publish.htm" in u or "upload.taobao.com" in u:
                try:
                    p.close()
                    killed += 1
                    mark = "  [已关闭]"
                except Exception as e:
                    mark = "  [关闭失败 %s]" % type(e).__name__
            print("  %-80s%s" % (u[:80], mark))
        left = len(list(s._context.pages))
        print()
        print("关闭 %d 个，剩余 %d 个" % (killed, left))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
