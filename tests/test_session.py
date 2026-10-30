"""登录态模块离线单测：不联网、不碰真 cookie，全在临时目录里造数据。

跑法::

    & $py tests/test_session.py

覆盖：
1. cookie 解析（``k=v; k=v`` 单行 + Netscape cookies.txt）
2. SessionStore 保存 / 加载往返、域名过滤、cookie_header
3. SessionHealth 四种状态：NO_COOKIE_FILE / INCOMPLETE / STALE / ok
4. RiskDetector 七条规则各一条真实错误串
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.session import (  # noqa: E402
    RiskDetector,
    SessionHealth,
    SessionStore,
    dump_cookie_header,
    parse_cookie_text,
)

PASS, FAIL = 0, 0
TMP = Path(tempfile.mkdtemp(prefix="cookies_test_"))

FULL_TAOBAO = ("_m_h5_tk=abc123_1699999999999; cookie2=xyz789; "
               "_tb_token_=e5f6a7b8c9d; _l_g_=Ug%3D%3D")
FULL_ALIBABA = ("_m_h5_tk=tk_1688_1700000000000; cookie2=c2for1688; lid=user123; "
                "__cn_logon__=true")
FULL_JD = "thor=jd-token-abc; pin=testuser; __jdu=1234567890"

NETSCAPE = """# Netscape HTTP Cookie File
# https://curl.haxx.se/rfc/cookie_spec.html
.taobao.com\tTRUE\t/\tFALSE\t1999999999\t_m_h5_tk\tabc123_1699999999999
.taobao.com\tTRUE\t/\tFALSE\t1999999999\tcookie2\txyz789
#HttpOnly_.taobao.com\tTRUE\t/\tFALSE\t1999999999\t_tb_token_\te5f6a7b8c9d
.1688.com\tTRUE\t/\tFALSE\t1999999999\tlid\tuser123
"""


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


# ---------------------------------------------------------------- 1. 解析
def test_parse() -> None:
    print("\n[1] cookie 解析")
    cookies, domains = parse_cookie_text(FULL_TAOBAO)
    check("单行格式 4 条", len(cookies) == 4, str(cookies))
    check("单行 _m_h5_tk 值", cookies.get("_m_h5_tk") == "abc123_1699999999999")
    check("单行 _l_g_ 保留 %3D%3D 编码", cookies.get("_l_g_") == "Ug%3D%3D")
    check("单行无域名信息", domains == {})

    ns, ns_domains = parse_cookie_text(NETSCAPE)
    check("Netscape 4 条", len(ns) == 4, str(ns))
    check("Netscape 取第 6/7 列", ns.get("cookie2") == "xyz789")
    check("带 #HttpOnly_ 前缀也认", ns.get("_tb_token_") == "e5f6a7b8c9d")
    check("Netscape 记下域名", ns_domains.get("cookie2") == ".taobao.com")

    header = dump_cookie_header({"a": "1", "b": "2"})
    check("拼回单行", header == "a=1; b=2", header)
    check("空文本不炸", parse_cookie_text("") == ({}, {}))
    check("Cookie: 前缀可容忍",
          parse_cookie_text("Cookie: k=v")[0].get("k") == "v")


# ---------------------------------------------------------------- 2. 存储
def test_store() -> None:
    print("\n[2] SessionStore 往返")
    store = SessionStore(TMP)
    p = store.save_cookie_header("taobao", FULL_TAOBAO)
    check("落盘文件名", p.name == "taobao.json", p.name)
    raw = json.loads(p.read_text(encoding="utf-8"))
    check("结构含 platform/saved_at/cookies",
          set(("platform", "saved_at", "cookies")) <= set(raw), str(raw.keys()))
    check("platform 字段", raw["platform"] == "taobao")
    check("saved_at 可解析", bool(datetime.fromisoformat(raw["saved_at"])))

    back = store.load("taobao")
    check("加载往返一致", back == parse_cookie_text(FULL_TAOBAO)[0], str(back))
    check("cookie_header 可拼头", "cookie2=xyz789" in store.cookie_header("taobao"))
    check("to_cookiejar_dict 全是 str/str",
          all(isinstance(k, str) and isinstance(v, str) for k, v in
              store.to_cookiejar_dict("taobao", "default", "h5api.m.taobao.com").items()))
    check("没有域名信息时不过滤",
          len(store.to_cookiejar_dict("taobao", "default", "h5api.m.taobao.com")) == 4)

    # 多店铺互不覆盖
    store.save_cookie_header("taobao", "cookie2=shopB; _m_h5_tk=t2; _tb_token_=b; _l_g_=c",
                             shop="shopB")
    check("多店铺文件名", store.path("taobao", "shopB").name == "taobao.shopB.json")
    check("多店铺独立", store.load("taobao", "shopB").get("cookie2") == "shopB")
    check("default 未被污染", store.load("taobao").get("cookie2") == "xyz789")

    # 兼容纯文本
    (TMP / "jd.txt").write_text(FULL_JD, encoding="utf-8")
    check("兼容 txt 格式", store.load("jd").get("thor") == "jd-token-abc")
    check("txt 也算存在", store.exists("jd"))

    # Netscape 落盘后按域名过滤
    store.save_cookie_header("alibaba", NETSCAPE)
    check("Netscape 来源记域名", store.domains("alibaba").get("cookie2") == ".taobao.com")
    picked = store.to_cookiejar_dict("alibaba", "default", "detail.1688.com")
    check("域名过滤只留 1688 的", picked == {"lid": "user123"}, str(picked))

    empty = SessionStore(TMP / "sub" / "nothing")
    check("空目录返回空 dict", empty.load("taobao") == {})
    check("空目录 saved_at 为 None", empty.saved_at("taobao") is None)


# ---------------------------------------------------------------- 3. 体检
def _age(store: SessionStore, platform: str, hours: float,
         shop: str = "default") -> None:
    """把刚存的文件时间改旧，模拟过期。"""
    p = store.path(platform, shop)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["saved_at"] = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_health() -> None:
    print("\n[3] SessionHealth 四种状态")
    store = SessionStore(TMP / "health")
    h = SessionHealth(store)

    st = h.check("tmall")
    check("NO_COOKIE_FILE", st.ok is False and st.reason == "NO_COOKIE_FILE", st.as_dict())

    store.save_cookie_header("taobao", "cookie2=only; _m_h5_tk=tk_1")
    st = h.check("taobao")
    check("INCOMPLETE", st.ok is False and st.reason == "INCOMPLETE", st.as_dict())
    check("missing 列出缺项", set(st.missing) == {"_tb_token_", "_l_g_"}, str(st.missing))

    store.save_cookie_header("taobao", FULL_TAOBAO)
    st = h.check("taobao")
    check("ok", st.ok is True and st.reason == "OK", st.as_dict())
    check("ok 时 missing 为空", st.missing == [])
    check("age_hours 是数字", isinstance(st.age_hours, float) and st.age_hours < 1)

    _age(store, "taobao", 30)
    st = h.check("taobao")
    check("STALE（淘宝 24h 阈值）", st.ok is False and st.reason == "STALE", st.as_dict())
    check("STALE 的 age_hours", st.age_hours >= 29, str(st.age_hours))

    # 1688 同 24h；京东按 7 天
    store.save_cookie_header("alibaba", FULL_ALIBABA)
    _age(store, "alibaba", 25)
    check("1688 25h 算过期", h.check("alibaba").reason == "STALE")

    store.save_cookie_header("jd", FULL_JD)
    _age(store, "jd", 25)
    check("京东 25h 仍有效", h.check("jd").ok is True, h.check("jd").as_dict())
    _age(store, "jd", 8 * 24)
    check("京东 8 天过期", h.check("jd").reason == "STALE")

    # 1688 允许 __cn_logon__ 或 lid 二选一
    store.save_cookie_header("alibaba", "_m_h5_tk=a; cookie2=b; lid=ok")
    check("1688 有 lid 即可", h.check("alibaba").ok is True, h.check("alibaba").as_dict())
    store.save_cookie_header("alibaba", "_m_h5_tk=a; cookie2=b")
    check("1688 两样都缺则 INCOMPLETE",
          h.check("alibaba").missing == ["__cn_logon__"], str(h.check("alibaba").missing))

    # 可选 pin 缺失不影响京东
    store.save_cookie_header("jd", "thor=only-token")
    check("京东缺 pin 仍 ok", h.check("jd").ok is True)


# ---------------------------------------------------------------- 4. 风控
def test_risk() -> None:
    print("\n[4] RiskDetector 七条规则")
    d = RiskDetector()

    cases = [
        ("TOKEN_EMPTY", "taobao", 200,
         '{"ret":["FAIL_SYS_TOKEN_EMPTY::令牌为空"],"data":{}}',
         "令牌为空"),
        ("RISK_CONTROL", "taobao", 200,
         '{"ret":["RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试!"]}',
         "风控拦截"),
        ("SLIDER_REQUIRED", "taobao", 200,
         '<html><script>window.x5sec={}</script>FAIL_SYS_USER_VALIDATE</html>',
         "滑块"),
        ("SESSION_EXPIRED", "alibaba", 200,
         '{"ret":["FAIL_SYS_SESSION_EXPIRED::Session过期"]}',
         "重新导出"),
        ("ANTI_BOT", "alibaba", 200,
         '<html>_____tmd_____ punish?x=1</html>',
         "反爬"),
        ("NOT_FOUND", "jd", 404,
         '<html><title>京东(JD.COM)-正品低价</title></html>',
         "商品不存在"),
        ("OK", "jd", 200,
         '<html>{"skuId":"100","price":"99.00"} 商品编号 100</html>',
         ""),
    ]
    for expect_code, platform, status, body, hint_kw in cases:
        code, hint = d.classify(platform, status, body)
        check(f"{expect_code} ← {body.strip()[:28]!r}",
              code == expect_code and hint_kw in hint, f"got={code} hint={hint}")

    # 规则优先级：同时出现 token 空和风控串，取更靠前的
    code, _ = d.classify("taobao", 200, "RGV587_ERROR FAIL_SYS_TOKEN_EMPTY")
    check("优先级：TOKEN_EMPTY 先于 RISK_CONTROL", code == "TOKEN_EMPTY", code)
    code, _ = d.classify("taobao", 200, "FAIL_SYS_USER_VALIDATE RGV587_ERROR")
    check("优先级：RISK_CONTROL 先于 SLIDER", code == "RISK_CONTROL", code)


def main() -> int:
    print(f"临时目录：{TMP}")
    test_parse()
    test_store()
    test_health()
    test_risk()
    print(f"\n{'=' * 40}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
