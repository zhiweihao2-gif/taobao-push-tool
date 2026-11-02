"""browser_pub 的离线测试。

不联网、不开浏览器。只测纯函数和可 mock 的部分。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish import browser_pub as bp  # noqa: E402


class TestCheckChromeAlive(unittest.TestCase):
    """CDP 端口探测：要认得 Chrome / Edge / 无头，也要能区分别的东西。"""

    def _mock_response(self, browser: str, status: int = 200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = {
            "Browser": browser,
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc",
        }
        return resp

    def test_chrome_accepted(self):
        with patch.object(bp.httpx, "get", return_value=self._mock_response("Chrome/156.0.0.0")):
            ok, msg = bp.check_chrome_alive()
        self.assertTrue(ok)
        self.assertIn("Chrome", msg)

    def test_edge_accepted(self):
        """本机只有 Edge 也要能用 —— 同一套 CDP 协议。"""
        with patch.object(bp.httpx, "get", return_value=self._mock_response("Edg/156.0.4314.58")):
            ok, msg = bp.check_chrome_alive()
        self.assertTrue(ok)
        self.assertIn("Edg", msg)

    def test_headless_accepted(self):
        with patch.object(bp.httpx, "get", return_value=self._mock_response("HeadlessChrome/120.0")):
            ok, _ = bp.check_chrome_alive()
        self.assertTrue(ok)

    def test_non_browser_rejected(self):
        """端口被别的程序占了要明确说出来，不能误判成浏览器。"""
        with patch.object(bp.httpx, "get", return_value=self._mock_response("node.js/22.0")):
            ok, msg = bp.check_chrome_alive()
        self.assertFalse(ok)
        self.assertIn("不是 Chromium", msg)

    def test_connect_error_message(self):
        import httpx

        with patch.object(bp.httpx, "get", side_effect=httpx.ConnectError("refused")):
            ok, msg = bp.check_chrome_alive()
        self.assertFalse(ok)
        self.assertIn("连不上", msg)

    def test_timeout_message_hints_startup(self):
        import httpx

        with patch.object(bp.httpx, "get", side_effect=httpx.TimeoutException("slow")):
            ok, msg = bp.check_chrome_alive()
        self.assertFalse(ok)
        self.assertIn("还在启动", msg)

    def test_non_200(self):
        with patch.object(bp.httpx, "get", return_value=self._mock_response("Chrome", status=404)):
            ok, msg = bp.check_chrome_alive()
        self.assertFalse(ok)
        self.assertIn("404", msg)

    def test_non_json_body(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        with patch.object(bp.httpx, "get", return_value=resp):
            ok, msg = bp.check_chrome_alive()
        self.assertFalse(ok)
        self.assertIn("不是 JSON", msg)


class TestPublishUrl(unittest.TestCase):
    def test_with_cid(self):
        url = bp.publish_url_for("50023905")
        self.assertIn("catId=50023905", url)
        self.assertIn("fromAICategory=true", url)
        self.assertTrue(url.startswith(bp.PUBLISH_URL))

    def test_without_ai_flag(self):
        url = bp.publish_url_for(50023905, from_ai=False)
        self.assertIn("catId=50023905", url)
        self.assertNotIn("fromAICategory", url)

    def test_int_cid(self):
        self.assertIn("catId=123", bp.publish_url_for(123))


class TestPageClassification(unittest.TestCase):
    def test_is_publish_page(self):
        self.assertTrue(bp.is_publish_page("https://item.upload.taobao.com/sell/v2/publish.htm?catId=1"))
        self.assertFalse(bp.is_publish_page("https://item.upload.taobao.com/sell/ai/category.htm"))

    def test_is_success_page(self):
        self.assertTrue(bp.is_success_page("https://item.upload.taobao.com/sell/v2/success.htm"))
        self.assertFalse(bp.is_success_page(bp.PUBLISH_URL))


class TestFieldClassification(unittest.TestCase):
    def test_title(self):
        self.assertEqual(bp.classify_field({"label": "商品标题", "placeholder": ""}), "title")
        self.assertEqual(bp.classify_field({"label": "", "placeholder": "请输入宝贝标题"}), "title")

    def test_price(self):
        self.assertEqual(bp.classify_field({"label": "一口价", "placeholder": ""}), "price")
        self.assertEqual(bp.classify_field({"label": "价格", "id": "price-input"}), "price")

    def test_stock(self):
        self.assertEqual(bp.classify_field({"label": "库存数量", "placeholder": ""}), "stock")
        self.assertEqual(bp.classify_field({"label": "", "id": "quantity"}), "stock")

    def test_outer_id(self):
        self.assertEqual(bp.classify_field({"label": "商家编码", "placeholder": ""}), "outer_id")
        self.assertEqual(bp.classify_field({"label": "货号"}), "outer_id")

    def test_brand(self):
        self.assertEqual(bp.classify_field({"label": "品牌", "placeholder": ""}), "brand")

    def test_model(self):
        self.assertEqual(bp.classify_field({"label": "型号", "placeholder": ""}), "model")

    def test_unknown(self):
        self.assertEqual(bp.classify_field({"label": "随便什么", "id": "xyz"}), "unknown")

    def test_order_matters_title_before_stock(self):
        """同时含「标题」和「数量」时，标题优先（淘宝的标题框 placeholder 常带数量提示）。"""
        f = {"label": "商品标题（最多60字）", "placeholder": "请输入数量"}
        self.assertEqual(bp.classify_field(f), "title")


class TestGroupFields(unittest.TestCase):
    def test_filters_invisible(self):
        fields = [
            {"kind": "input", "label": "商品标题", "visible": True},
            {"kind": "input", "label": "价格", "visible": False},
        ]
        g = bp.group_fields(fields)
        self.assertEqual(len(g.get("title", [])), 1)
        self.assertNotIn("price", g)

    def test_error_passthrough(self):
        g = bp.group_fields([{"kind": "error", "error": "boom"}])
        self.assertEqual(len(g["error"]), 1)

    def test_empty(self):
        self.assertEqual(bp.group_fields([]), {})


class TestHumanPause(unittest.TestCase):
    def test_within_range(self):
        """停顿必须落在给定区间内 —— 固定间隔比慢更容易被识别。"""
        for _ in range(20):
            t0 = __import__("time").time()
            bp.human_pause(0.01, 0.03)
            dt = __import__("time").time() - t0
            self.assertGreaterEqual(dt, 0.008)
            self.assertLess(dt, 0.2)


class TestSafeTitle(unittest.TestCase):
    def test_ok(self):
        page = MagicMock()
        page.title.return_value = "发布宝贝"
        self.assertEqual(bp._safe_title(page), "发布宝贝")

    def test_exception_returns_empty(self):
        page = MagicMock()
        page.title.side_effect = RuntimeError("detached")
        self.assertEqual(bp._safe_title(page), "")

    def test_truncated(self):
        page = MagicMock()
        page.title.return_value = "x" * 200
        self.assertEqual(len(bp._safe_title(page)), 80)


class TestDumpHelpers(unittest.TestCase):
    def test_dump_form_fields_error_captured(self):
        page = MagicMock()
        page.evaluate.side_effect = RuntimeError("no page")
        out = bp.dump_form_fields(page)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "error")
        self.assertIn("no page", out[0]["error"])

    def test_dump_page_summary_error_captured(self):
        page = MagicMock()
        page.url = "https://x"
        page.evaluate.side_effect = RuntimeError("gone")
        out = bp.dump_page_summary(page)
        self.assertEqual(out["url"], "https://x")
        self.assertIn("error", out)


class TestBrowserSession(unittest.TestCase):
    def test_raises_when_chrome_down(self):
        with patch.object(bp, "check_chrome_alive", return_value=(False, "连不上")):
            with self.assertRaises(bp.CDPError) as ctx:
                with bp.BrowserSession():
                    pass
        self.assertIn("连不上", str(ctx.exception))

    def test_exit_does_not_close_browser(self):
        """退出时必须只断开 Playwright，绝不能关掉用户的浏览器。"""
        sess = bp.BrowserSession()
        fake_browser = MagicMock()
        fake_pw = MagicMock()
        sess._browser = fake_browser
        sess._pw = fake_pw
        sess.__exit__(None, None, None)
        fake_pw.stop.assert_called_once()
        fake_browser.close.assert_not_called()


class TestConstants(unittest.TestCase):
    def test_success_path(self):
        self.assertEqual(bp.SUCCESS_PATH, "/sell/v2/success.htm")

    def test_login_hints_cover_seller_login(self):
        hints = " ".join(bp._LOGIN_HINTS)
        self.assertIn("loginmyseller", hints)
        self.assertIn("login.taobao.com", hints)


if __name__ == "__main__":
    unittest.main(verbosity=2)
