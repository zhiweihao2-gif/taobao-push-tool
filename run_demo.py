"""命令行：一个链接 → 一份完整商品包。

用法::

    python run_demo.py --platform taobao --id 6xxxxxxxxx
    python run_demo.py --platform jd --id 100012043978 --no-media
    python run_demo.py --platform taobao --id 6xxxx --publish --mode dry_run
    python run_demo.py --batch links.txt --platform taobao
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback
from urllib.parse import urlparse

import httpx

from src.collect.alibaba import Alibaba1688Collector
from src.collect.jd import JDCollector
from src.collect.resolve import ResolveError, resolve_item_id
from src.collect.ratelimit import CooldownActive, RateLimiter
from src.collect.taobao import TaobaoCollector
from src.core.config import get_settings
from src.core.storage import safe_name, save_item, save_raw
from src.media.downloader import MediaPipeline
from src.publish.taobao_pub import TaobaoPublisher

COLLECTORS = {
    "taobao": TaobaoCollector,
    "tmall": TaobaoCollector,
    "alibaba": Alibaba1688Collector,
    "jd": JDCollector,
}

# 短链 / 推广链 / 跳转链，需要先跟一次 HTTP 才能拿到真实商品 ID
REDIRECT_HOSTS = (
    "tb.cn", "m.tb.cn", "click.simba.taobao.com", "s.click.taobao.com",
    "uland.taobao.com", "ai.taobao.com", "s.taobao.com", "m.tmall.com",
    "q.1688.com", "m.1688.com", "u.jd.com", "3.cn", "item.m.jd.com",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

ID_PATTERNS = (
    r"[?&](?:id|itemId|item_id|offerId|sku)=(\d{6,})",
    r"/(?:item|offer|product|detail)/(\d{6,})",
    r"/(\d{8,})(?:\.html)?",
)


def run_one(platform: str, item_id: str, media: bool, detail: bool,
            publish: bool, mode: str, target_cid: str | None,
            render_desc: bool = False) -> bool:
    cls = COLLECTORS[platform]
    print(f"[1/4] 采集 {platform}/{item_id} ...")
    collector = cls()

    status = getattr(collector, "status", None)
    if status is not None and not status.ok:
        missing = f" 缺失: {status.missing}" if status.missing else ""
        print(f"      ! 登录态 {status.reason}{missing}")
        # 登录态明确不可用时直接拒绝，不发请求。
        # 否则会白挨一次风控（实测：过期 cookie 请求 → RGV587 → 熔断 15 分钟）。
        if status.reason in ("NO_COOKIE_FILE", "STALE", "INCOMPLETE"):
            print(f"      → 已跳过，未发起任何请求（避免白挨风控）")
            print(f"      → 双击 1-登录{platform}相关启动器 重新扫码，或执行：")
            print(f"        python scripts/login.py --platform {platform}")
            return False

    raw = collector.fetch(item_id)
    save_raw(raw)
    if raw.error:
        print(f"      ! 采集异常: {raw.error}")

    print("[2/4] 归一化 ...")
    item = collector.to_item(raw)

    caps = (item.extra or {}).get("capabilities") or {}
    if not item.title:
        print(f"      × 未取到商品标题，采集失败。endpoint={raw.endpoint}")
        err = raw.error or ""
        if "SLIDER_REQUIRED" in err:
            print("      → 淘宝要求过滑块验证：说明请求太频繁了。")
            print("        等 10~30 分钟再试；或者重新双击 1-登录淘宝.bat 换一份登录态。")
            print("        批量采集时把间隔调大：--delay 3")
        elif "RISK_CONTROL" in err:
            print("      → 被风控拦截：先双击 3-检查登录状态.bat 确认登录态有效。")
        elif "SESSION_EXPIRED" in err:
            print("      → 登录态过期了：重新双击 1-登录淘宝.bat。")
        elif "ANTI_BOT" in err:
            print("      → 1688 反爬拦截：需要先登录（双击 1-登录淘宝.bat 里选 1688）。")
        return False

    # 详情图：淘宝静态接口已下线，只能开浏览器渲染
    if render_desc and hasattr(collector, "attach_rendered_desc"):
        print("[2.5/4] 渲染详情页抓详情图（10~20 秒）...")
        collector.attach_rendered_desc(item)
        n = len(item.detail_images)
        if n:
            print(f"      拿到详情图 {n} 张")
        else:
            err = (item.extra or {}).get("render_error")
            print(f"      没拿到详情图{('：' + err) if err else ''}")

    if media:
        print(f"[3/4] 下载素材 → {get_settings().media_dir / item_id} ...")
        MediaPipeline(item).run(with_detail=detail)
    else:
        print("[3/4] 跳过素材下载")

    path = save_item(item)
    print(f"[4/4] 已保存 {path}\n")
    print(item.summary())
    if caps:
        got = {k: v for k, v in caps.items() if v not in (False, 0, "missing", "need_login")}
        need = [k for k, v in caps.items() if v in (False, 0, "missing", "need_login")]
        print(f"  能力: 已获取 {list(got)}")
        if need:
            print(f"        需登录/不可得 {need}")

    if publish:
        print(f"\n[+] 发布（{mode}）...")
        pub = TaobaoPublisher(mode=mode)
        payload = pub.build_payload(item, target_cid)
        result = pub.publish(item, target_cid)
        print(f"    ok={result.ok} code={result.error_code} msg={result.error_msg}")
        if mode == "dry_run":
            print("    载荷预览：")
            for k, v in payload.items():
                print(f"      {k}: {str(v)[:160]}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="一键铺货 · 采集/预览/发布")
    ap.add_argument("--platform", choices=list(COLLECTORS), default="taobao")
    ap.add_argument("--id", dest="item_id", help="商品 ID")
    ap.add_argument("--batch", help="批量文件，每行一个商品 ID 或完整链接")
    ap.add_argument("--no-media", action="store_true", help="不下载图片")
    ap.add_argument("--with-detail", action="store_true", help="同时下载详情图")
    ap.add_argument("--render-desc", action="store_true",
                    help="用浏览器渲染详情页抓详情图（慢，10-20 秒/条）")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--mode", default="dry_run", choices=["draft", "direct", "dry_run"])
    ap.add_argument("--target-cid", default=None)
    ap.add_argument("--skip-existing", action="store_true",
                    help="已采集过的商品跳过（断点续采）")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="批量时每条之间的间隔秒数（默认 1.0，建议 ≥ 1 防风控）")
    args = ap.parse_args()

    # 启动前先看熔断状态：冷却期内直接拒绝，不浪费一次请求
    limiter = RateLimiter(args.platform)
    st = limiter.status()
    if st["cooldown_remaining"] > 0:
        print(f"\n× {args.platform} 正在冷却中，还需等待 "
              f"{st['cooldown_remaining'] / 60:.0f} 分钟")
        print(f"  原因：{st['cooldown_reason'] or '风控'}")
        print(f"  本小时已用 {st['used_last_hour']}/{int(st['per_hour'])} 次")
        print("  现在重试只会让冷却变长。")
        print("  查看状态：双击 5-查看请求配额.bat")
        return 1

    raw_inputs: list[str] = []
    if args.batch:
        # utf-8-sig：Windows 记事本/bat 生成的 txt 常带 BOM，不去掉会让第一行 ID 带隐形字符
        with open(args.batch, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                raw_inputs.append(line)
    elif args.item_id:
        raw_inputs.append(args.item_id)
    else:
        ap.error("需要 --id 或 --batch")

    print(f"解析 {len(raw_inputs)} 个输入…")
    ids: list[str] = []
    for raw in raw_inputs:
        try:
            resolved = resolve_item_id(raw, args.platform)
            print(f"  {raw[:60]:<62} -> {resolved}")
            ids.append(resolved)
        except ValueError as e:
            print(f"  × {raw[:60]}")
            for line in str(e).splitlines():
                print(f"    {line}")
    if not ids:
        print("\n没有可采集的商品 ID，退出。")
        return 1

    items_dir = get_settings().items_dir
    ok, skipped = 0, 0
    for i, iid in enumerate(ids, 1):
        print(f"\n===== [{i}/{len(ids)}] {iid} =====")
        if args.skip_existing and (items_dir / f"{safe_name(iid)}.json").exists():
            print("  - 已采集，跳过")
            skipped += 1
            continue
        try:
            run_one(args.platform, iid, not args.no_media, args.with_detail,
                    args.publish, args.mode, args.target_cid,
                    render_desc=args.render_desc)
            ok += 1
        except CooldownActive as e:
            print(f"  × 已停止：{e}")
            print("  → 批量采集已中止，避免继续加重风控。")
            print("  → 查看状态：双击 5-查看请求配额.bat")
            break
        except Exception as e:
            print(f"  × 失败: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)
        if i < len(ids) and args.delay > 0:
            time.sleep(args.delay)
    print(f"\n完成 {ok}/{len(ids)}" + (f"（跳过 {skipped}）" if skipped else ""))
    return 0 if ok + skipped == len(ids) else 1


def _extract_id(text: str, platform: str) -> str:
    """兼容旧调用：等价于 resolve_item_id。"""
    return resolve_item_id(text, platform)


if __name__ == "__main__":
    sys.exit(main())
