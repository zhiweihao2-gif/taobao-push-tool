"""探查 mtop 真实调用：令牌下发、签名校验、pcdetail 返回结构。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collect.mtop import MTopClient, MTopError  # noqa: E402

UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def dump_cookies(client: MTopClient, label: str) -> None:
    names = [c.name for c in client.client.cookies.jar]
    print(f"  [{label}] cookies: {names}")


def main() -> int:
    item_id = sys.argv[1] if len(sys.argv) > 1 else "600000000000"

    print("== 1. 裸请求 getTimestamp（不带 sign 校验）==")
    c = MTopClient(site="taobao")
    try:
        r = c._raw_call("mtop.common.getTimestamp", "1.0", {}, allow_fail=True)
        print("  ret:", r.get("ret"))
    except Exception as e:
        print("  ERR:", e)
    dump_cookies(c, "after getTimestamp")

    print("\n== 2. 带 sign 调 pcdetail（token 为空）==")
    try:
        r = c._raw_call("mtop.taobao.pcdetail.data.get", "1.0",
                        {"itemId": item_id, "detail_v": "3.3.2", "exParams": "{}"},
                        allow_fail=True)
        print("  ret:", r.get("ret"))
    except Exception as e:
        print("  ERR:", e)
    dump_cookies(c, "after pcdetail")

    print("\n== 3. 走 call()（自动刷 token 重试）==")
    try:
        r = c.call("mtop.taobao.pcdetail.data.get", "1.0",
                   {"itemId": item_id, "detail_v": "3.3.2", "exParams": "{}"})
        print("  ret:", r.get("ret"))
        data = r.get("data") or {}
        print("  data keys:", list(data.keys())[:15])
    except MTopError as e:
        print(f"  MTopError {e.code}: {str(e)[:200]}")
    except Exception as e:
        print(f"  {type(e).__name__}: {str(e)[:200]}")
    dump_cookies(c, "final")

    print("\n== 4. 直接 HTTP 观察首次响应的 Set-Cookie ==")
    with httpx.Client(timeout=15, follow_redirects=True,
                      headers={"User-Agent": UA_M,
                               "Referer": "https://item.taobao.com/"}) as hc:
        url = ("https://h5api.m.taobao.com/h5/mtop.taobao.pcdetail.data.get/1.0/"
               "?jsv=2.6.1&appKey=12574478&t=1&sign=0&api=mtop.taobao.pcdetail.data.get"
               "&v=1.0&type=json&dataType=json&data=%7B%22itemId%22%3A%22" + item_id + "%22%7D")
        resp = hc.get(url)
        print("  status:", resp.status_code, "body:", resp.text[:200])
        for h in resp.headers.get_list("set-cookie"):
            print("  set-cookie:", h[:160])
    return 0


if __name__ == "__main__":
    sys.exit(main())
