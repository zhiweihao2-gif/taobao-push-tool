# -*- coding: utf-8 -*-
"""在浏览器里同源 fetch 搜商品 + 采集 + 入库。

为什么需要它：mtop 直连（MTopClient）连续请求十几次就会被自家限速器熔断
（SLIDER_REQUIRED，冷却 30 分钟），而浏览器发出的请求带完整指纹，
风控看到的是正常用户，也不经过我们的 RateLimiter。

流程：
    1. 预热一个淘宝商品页，拿到 _m_h5_tk
    2. 同源 fetch mtop.taobao.wsearch.appsearch 搜关键词，拿候选 ID
    3. 逐个同源 fetch mtop.taobao.pcdetail.data.get 验证（区分「不存在」和「风控」）
    4. 对第一个有效的，fetch mtop.taobao.detail.getdesc v6.0 拿详情图文
    5. 组装成 Item 写进 data/items/

用法：
    python scripts/browser_collect.py --q 机械键盘
    python scripts/browser_collect.py --id 902412139914
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.publish.browser_pub import BrowserSession  # noqa: E402

APP_KEY = "12574478"
WARMUP = "https://item.taobao.com/item.htm?id=902412139914"


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class MtopInBrowser:
    """在页面里用 fetch 调 mtop，自动管 token 和签名。"""

    def __init__(self, page):
        self.page = page
        self.token = ""
        self.refresh_token()

    def refresh_token(self):
        tok = self.page.evaluate("""() => {
            const m = document.cookie.match(/_m_h5_tk=([^;]+)/);
            return m ? m[1] : '';
        }""") or ""
        if not tok:
            # 空跑一次让它下发
            u = ("https://h5api.m.taobao.com/h5/mtop.common.getTimestamp/1.0/"
                 "?jsv=2.7.0&appKey=%s&t=%d&sign=&data={}" % (APP_KEY, int(time.time() * 1000)))
            self.page.evaluate(
                "(u) => fetch(u, {credentials:'include'}).then(r=>r.text()).catch(e=>'')", u)
            self.page.wait_for_timeout(1500)
            tok = self.page.evaluate("""() => {
                const m = document.cookie.match(/_m_h5_tk=([^;]+)/);
                return m ? m[1] : '';
            }""") or ""
        self.token = tok.split("_")[0] if tok else ""

    def call(self, api: str, version: str, data: dict, host: str = "h5api.m.taobao.com",
             retry_on_expired: bool = True) -> dict:
        ds = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        t = str(int(time.time() * 1000))
        sign = md5("%s&%s&%s&%s" % (self.token, t, APP_KEY, ds))
        qs = urllib.parse.urlencode({
            "jsv": "2.7.0", "appKey": APP_KEY, "t": t, "sign": sign,
            "api": api, "v": version, "type": "originaljson", "dataType": "json",
            "H5Request": "true", "AntiFlood": "true", "AntiCreep": "true",
        })
        url = "https://%s/h5/%s/%s/?%s&data=%s" % (
            host, api, version, qs, urllib.parse.quote(ds))
        body = self.page.evaluate(
            "(u) => fetch(u, {credentials:'include', headers:{'Accept':'application/json'}})"
            ".then(r=>r.text()).catch(e=>'FETCH_ERR '+e)", url) or ""

        if body.startswith("FETCH_ERR"):
            raise RuntimeError("fetch 失败: %s" % body[:160])
        try:
            obj = json.loads(body)
        except Exception:
            raise RuntimeError("响应不是 JSON: %s" % body[:200])

        ret = obj.get("ret") or []
        rets = " ".join(ret) if isinstance(ret, list) else str(ret)
        if retry_on_expired and ("TOKEN_EXOIRED" in rets or "TOKEN_EMPTY" in rets
                                 or "TOKEN_EXPIRED" in rets):
            self.refresh_token()
            return self.call(api, version, data, host, retry_on_expired=False)
        if "SUCCESS" not in rets:
            raise RuntimeError("mtop 返回 %s" % rets[:200])
        return obj


def search_ids(mb: MtopInBrowser, q: str, n: str = "20") -> list[str]:
    r = mb.call("mtop.taobao.wsearch.appsearch", "1.0",
                {"q": q, "page": "1", "n": n, "sort": "_sale"})
    s = json.dumps(r, ensure_ascii=False)
    ids, seen = [], set()
    for m in re.finditer(r'"(?:itemId|item_id|nid)"\s*:\s*"?(\d{10,16})"?', s):
        i = m.group(1)
        if i not in seen:
            seen.add(i)
            ids.append(i)
    return ids


def try_detail(mb: MtopInBrowser, item_id: str):
    """返回 (kind, payload)。kind: ok / gone / risk"""
    try:
        d = mb.call("mtop.taobao.pcdetail.data.get", "1.0",
                    {"id": item_id, "detail_v": "3.3.2", "exParams": "{}"})
    except RuntimeError as e:
        msg = str(e)
        if any(k in msg for k in ("USER_VALIDATE", "RGV587", "TRAFFIC_LIMIT",
                                  "被挤爆", "punish", "_____tmd_____")):
            return "risk", msg
        return "gone", msg

    body = d.get("data") if isinstance(d.get("data"), dict) else d
    pc = (body or {}).get("pcTrade") or {}
    # pcTrade.redirectReason 有几种，只有 noitem 才是真的不存在。
    # 实测 745703904965 返回 {"redirectReason":"cSeller",
    #   "redirectUrl":"...&ft=t"} —— 那是「跳 C 店版页面」，商品完全有效。
    if pc.get("redirectReason"):
        ru = str(pc.get("redirectUrl") or pc.get("redirectReason") or "")
        if "error.item.taobao.com" in ru or "type=noitem" in ru:
            return "gone", ru
    if not ((body or {}).get("item") or {}).get("title"):
        return "gone", "无 title"
    return "ok", d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="", help="关键词搜索")
    ap.add_argument("--id", default="", help="直接指定商品 ID")
    ap.add_argument("--n", default="20")
    ap.add_argument("--with-media", action="store_true")
    a = ap.parse_args()

    if not a.q and not a.id:
        print("[XX] 需要 --q 或 --id")
        return 2

    with BrowserSession() as s:
        p = s.page()
        p.set_viewport_size({"width": 1440, "height": 1000})
        print("[..] 预热 %s" % WARMUP)
        try:
            p.goto(WARMUP, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("[!] goto:", str(e).split("\n")[0][:100])
        p.wait_for_timeout(2500)

        mb = MtopInBrowser(p)
        print("[..] _m_h5_tk = %s" % (mb.token[:24] or "(空)"))

        # 1) 定候选 ID
        if a.id:
            cands = [a.id]
        else:
            cands = search_ids(mb, a.q, a.n)
            print("[OK] 搜索「%s」-> %d 个候选" % (a.q, len(cands)))
            if not cands:
                return 1

        # 2) 逐个验证，拿第一个有效
        picked, payload = None, None
        for k, iid in enumerate(cands[:12], 1):
            kind, extra = try_detail(mb, iid)
            if kind == "risk":
                print("  %2d. %-16s [风控] %s" % (k, iid, str(extra)[:70]))
                print("[XX] 触发风控，停止（不要重试）")
                return 3
            if kind == "gone":
                print("  %2d. %-16s [不存在]" % (k, iid))
                continue
            title = (extra.get("data", extra).get("item") or {}).get("title", "")
            print("  %2d. %-16s [OK] %s" % (k, iid, str(title)[:46]))
            picked, payload = iid, extra
            break

        if not picked:
            print("[XX] 没有可用商品")
            return 1

        # 3) 详情图文
        desc = {}
        try:
            d = mb.call("mtop.taobao.detail.getdesc", "6.0", {"id": picked})
            desc = d.get("data") or {}
            print("[OK] getdesc v6.0 -> %d 字符 HTML，参数 %d 条"
                  % (len(str(desc.get("pcDescContent") or "")),
                     len(desc.get("itemProperties") or [])))
        except RuntimeError as e:
            print("[!] getdesc 失败: %s" % str(e)[:160])

    # 4) 组装 Item（复用 collector 的解析逻辑）
    from src.collect.taobao import TaobaoCollector
    from src.core.models import RawItem
    from src.core.storage import save_item
    from src.collect.base import dig

    # 注意：TaobaoCollector.fetch() 里 payload 取的是 resp["data"]，
    # to_item() 里 `d = raw.payload` 直接按 data 层用。所以这里必须剥掉外层。
    inner = payload.get("data") if isinstance(payload, dict) else None
    raw = RawItem(
        platform="taobao", item_id=picked,
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        payload=inner if isinstance(inner, dict) else payload,
        endpoint="mtop.taobao.pcdetail.data.get (browser)",
    )
    col = TaobaoCollector()
    item = col.to_item(raw)

    html = str(desc.get("pcDescContent") or "")
    if html:
        from src.collect.base import extract_img_with_size
        from src.collect.detail_img import clean_detail_images
        from src.core.models import Image as Img

        imgs = extract_img_with_size(html)
        urls = [x["url"] for x in imgs]
        sizes = [(x["w"], x["h"]) for x in imgs]
        cleaned = clean_detail_images(urls, sizes)
        item.detail_images = [Img(url=u) for u in cleaned]
        print("[OK] 详情图 %d -> 清洗后 %d 张" % (len(urls), len(cleaned)))

    save_item(item)
    print("\n" + "=" * 60)
    print("商品 %s" % picked)
    print("  %s" % item.title[:70])
    print("  价格 %.2f~%.2f  主图 %d  SKU %d  参数 %d"
          % (item.price.min or 0, item.price.max or 0,
             len(item.main_images), len(item.skus), len(item.params)))
    print("  类目 %s" % item.category.cid)
    print("  已写入 data/items/%s.json" % picked)
    print("=" * 60)
    print("\n参数:")
    for pv in item.params:
        print("   %-16s %s" % (pv.name, str(pv.value)[:50]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
