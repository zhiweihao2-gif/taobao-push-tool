"""在浏览器里同源 fetch mtop 接口，绕过我们自己的限速器。

用途：验证店透视反混淆包里翻出来的接口版本号（getdesc 是 v7.0 不是 v1.0）。

用法:
    python scripts/mtop_browser_call.py --api mtop.taobao.detail.getdesc --v 7.0 --data '{"id":"902412139914"}'
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.browser_pub import BrowserSession  # noqa: E402

APP_KEY = "12574478"
WARMUP = "https://item.taobao.com/item.htm?id=902412139914"


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--v", default="1.0")
    ap.add_argument("--data", default="{}")
    ap.add_argument("--host", default="h5api.m.taobao.com")
    ap.add_argument("--warmup", default=WARMUP)
    ap.add_argument("--save", default="")
    a = ap.parse_args()

    with BrowserSession() as s:
        p = s.page()
        print("[..] 预热 %s" % a.warmup)
        try:
            p.goto(a.warmup, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("[!] goto:", str(e).split("\n")[0][:80])
        p.wait_for_timeout(2500)

        # 拿 _m_h5_tk（页面自己请求过就有了；没有就空跑一次让它下发）
        tok = p.evaluate("""() => {
            const m = document.cookie.match(/_m_h5_tk=([^;]+)/);
            return m ? m[1] : '';
        }""")
        print("[..] _m_h5_tk = %s" % (tok[:24] or "(空)"))

        if not tok:
            # 空跑一次触发 Set-Cookie
            warm_url = ("https://%s/h5/%s/%s/?jsv=2.7.0&appKey=%s&t=%d&sign=&data=%s"
                        % (a.host, a.api, a.v, APP_KEY, int(time.time() * 1000),
                           urllib.parse.quote(a.data)))
            p.evaluate("(u) => fetch(u, {credentials:'include'}).then(r=>r.text()).catch(e=>'ERR '+e)",
                       warm_url)
            p.wait_for_timeout(2000)
            tok = p.evaluate("""() => {
                const m = document.cookie.match(/_m_h5_tk=([^;]+)/);
                return m ? m[1] : '';
            }""")
            print("[..] 重取 _m_h5_tk = %s" % (tok[:24] or "(仍空)"))

        token = tok.split("_")[0] if tok else ""
        t = str(int(time.time() * 1000))
        data = a.data
        sign = md5("%s&%s&%s&%s" % (token, t, APP_KEY, data))
        print("[..] sign = %s (t=%s)" % (sign, t))

        qs = urllib.parse.urlencode({
            "jsv": "2.7.0", "appKey": APP_KEY, "t": t, "sign": sign,
            "api": a.api, "v": a.v, "type": "originaljson", "dataType": "json",
            "H5Request": "true", "AntiFlood": "true", "AntiCreep": "true",
        })
        url = "https://%s/h5/%s/%s/?%s&data=%s" % (
            a.host, a.api, a.v, qs, urllib.parse.quote(data))
        print("[..] %s" % url[:180])

        body = p.evaluate(
            "(u) => fetch(u, {credentials:'include', headers:{'Accept':'application/json'}})"
            ".then(r=>r.text()).catch(e=>'FETCH_ERR '+e)", url)
        print()
        print("=" * 78)
        print("响应 %d 字符" % len(body or ""))
        print("=" * 78)
        print((body or "")[:1500])

        if a.save and body:
            os.makedirs(os.path.dirname(a.save) or ".", exist_ok=True)
            with open(a.save, "w", encoding="utf-8") as f:
                f.write(body)
            print()
            print("已写入 %s" % a.save)
            try:
                d = json.loads(body)
                print("ret:", d.get("ret"))
                if d.get("data"):
                    print("data keys:", list(d["data"].keys())[:15])
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
