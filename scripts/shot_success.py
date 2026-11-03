"""截成功页 + dump 商品信息。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import BrowserSession  # noqa: E402


def main() -> int:
    with BrowserSession() as s:
        page = s.page()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.wait_for_timeout(2500)
        print("URL: " + page.url)

        try:
            body = page.evaluate("() => (document.body && document.body.innerText) || ''")
            print("\n页面文本:\n%s" % body[:1200])
        except Exception as exc:
            print("文本读取失败: %s" % str(exc).split("\n")[0][:80])

        s.screenshot(str(ROOT / "data" / "publish_poc" / "success.png"), timeout_ms=30000, full_page=True)
        print("\n截图: data/publish_poc/success.png")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
