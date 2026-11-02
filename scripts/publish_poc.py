"""淘宝发布 PoC —— 通过 CDP 挂接你自己开着的 Chrome，在卖家中心填表。

前置条件（只需做一次）：

  1. 完全关闭 Chrome（任务管理器里确认没有 chrome.exe 残留）
  2. 用带调试端口的方式启动：
       "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\chrome-debug"
  3. 在弹出的 Chrome 里登录卖家中心：https://myseller.taobao.com/home.htm
  4. 保持这个 Chrome 开着，回来跑本脚本

用法::

    # 只检查 Chrome 和登录态
    python scripts/publish_poc.py check

    # 探测发布页结构（第一次必跑，用来确定 selector）
    python scripts/publish_poc.py probe --cid 50023905

    # 按已有商品数据填表（到截图为止，不点提交）
    python scripts/publish_poc.py fill --item 1073025553052 --cid 50023905

本脚本**永远不会**替你点「提交」。填完截图，由你人工确认。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.browser_pub import (  # noqa: E402
    CDP_URL_DEFAULT,
    BrowserSession,
    CDPError,
    build_probe_report,
    check_chrome_alive,
    dump_form_fields,
    dump_page_summary,
    group_fields,
    human_pause,
    human_type,
    list_pages,
    publish_url_for,
    set_value_native,
    write_report,
)

OUT_DIR = ROOT / "data" / "publish_poc"


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def _hr(title: str = "") -> None:
    print("\n" + "=" * 68)
    if title:
        print(title)
        print("=" * 68)


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [!!]   {msg}")


def _info(msg: str) -> None:
    print(f"  [..]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [XX]   {msg}")


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #


def cmd_check(args: argparse.Namespace) -> int:
    _hr("1. 检查 Chrome 调试端口")
    ok, msg = check_chrome_alive(args.cdp)
    if not ok:
        _fail(msg)
        print()
        print("请按下面步骤启动 Chrome（先完全退出所有 Chrome 窗口）：")
        print()
        print('  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
              '--remote-debugging-port=9222 --user-data-dir="C:\\chrome-debug"')
        print()
        print("然后用这个 Chrome 登录 https://myseller.taobao.com/home.htm")
        return 2
    _ok(msg)

    _hr("2. 当前标签页")
    pages = list_pages(args.cdp)
    if not pages:
        _warn("没有打开的标签页")
    for i, p in enumerate(pages, 1):
        print(f"  {i}. {(p.get('title') or '?')[:40]:42} {(p.get('url') or '')[:70]}")

    _hr("3. 卖家中心登录态")
    try:
        with BrowserSession(cdp_url=args.cdp) as sess:
            state = sess.login_state_detail()
    except CDPError as exc:
        _fail(str(exc))
        return 2

    print(f"  当前页: {state['url'][:80]}")
    print(f"  标题  : {state['title']}")
    if state["logged_in"]:
        _ok("卖家中心已登录")
        return 0
    _fail(f"未登录（命中登录页特征：{state['login_hint'] or '无'}）")
    print()
    print("请在这个 Chrome 里打开 https://myseller.taobao.com/home.htm 扫码登录，然后重跑。")
    return 3


def cmd_probe(args: argparse.Namespace) -> int:
    _hr("探测发布页结构")
    try:
        with BrowserSession(cdp_url=args.cdp) as sess:
            report = build_probe_report(sess, cid=args.cid)
    except CDPError as exc:
        _fail(str(exc))
        return 2

    if not report.get("ok"):
        _fail(report.get("hint", "探测失败"))
        write_report(report, OUT_DIR / "probe_report.json")
        return 3

    _hr("步骤记录")
    for s in report.get("steps", []):
        step = s.get("step")
        if step == "seller_home":
            print(f"  {step:16} logged_in={s.get('logged_in')}  {s.get('url','')[:60]}")
        else:
            print(f"  {step:16} {s.get('url','')[:70]}")
            if s.get("errors"):
                for e in s["errors"][:5]:
                    _warn(f"页面提示: {e}")
            if s.get("iframes"):
                for f in s["iframes"][:3]:
                    _info(f"iframe: {f[:70]}")

    fields = report.get("fields") or []
    grouped = group_fields(fields)
    _hr(f"可填字段（共 {len(fields)} 个，可见 {sum(len(v) for k,v in grouped.items() if k!='error')} 个）")
    for name in ("title", "price", "stock", "outer_id", "brand", "model", "unknown", "error"):
        items = grouped.get(name) or []
        if not items:
            continue
        print(f"\n  -- {name} ({len(items)}) --")
        for f in items[:12]:
            if f.get("kind") == "error":
                _fail(f.get("error", ""))
                continue
            sel = f"#{f['id']}" if f.get("id") else (f"[name={f['name']}]" if f.get("name") else "?")
            print(f"    {f['tag']:12} {f.get('type',''):10} {sel[:38]:40} "
                  f"label={f.get('label','')[:22]:24} ph={f.get('placeholder','')[:18]}")

    if report.get("screenshot"):
        _hr("截图")
        print(f"  {report['screenshot']}")

    path = write_report(report, OUT_DIR / f"probe_{args.cid or 'bare'}.json")
    _hr("报告已保存")
    print(f"  {path}")
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    """按商品数据填表。填完截图，不提交。"""
    from src.core.storage import load_item  # 延迟导入，避免无关依赖

    _hr("加载商品数据")
    item = load_item(args.item)
    if item is None:
        _fail(f"本地没有 {args.item} 的数据 —— 先跑 2-采集一个商品.bat")
        return 4
    print(f"  标题: {item.title[:50]}")
    print(f"  主图: {len(item.main_images)} 张  详情图: {len(item.detail_images)} 张")
    print(f"  SKU : {len(item.skus)} 条")
    if item.skus:
        prices = [s.price for s in item.skus if s.price]
        if prices:
            print(f"  价格: {min(prices)} ~ {max(prices)}")

    cid = args.cid
    if not cid:
        _fail("必须指定 --cid（目标类目 ID）")
        return 4

    _hr(f"打开发布页 cid={cid}")
    try:
        with BrowserSession(cdp_url=args.cdp) as sess:
            if not sess.is_logged_in():
                _fail("卖家中心未登录 —— 先在这个 Chrome 里扫码登录")
                return 3

            url = publish_url_for(cid)
            _info(url)
            sess.goto(url, settle_ms=3500)
            summary = dump_page_summary(sess.page())
            print(f"  落地: {summary.get('url','')[:80]}")
            print(f"  标题: {summary.get('title','')}")

            if summary.get("errors"):
                for e in summary["errors"][:6]:
                    _warn(e)

            fields = dump_form_fields(sess.page())
            grouped = group_fields(fields)
            _hr("字段识别结果")
            for name in ("title", "price", "stock", "brand", "model"):
                n = len(grouped.get(name) or [])
                print(f"  {name:10} {n} 个")

            # 先只截图，填表逻辑等 probe 结果确认 selector 后再开
            shot = sess.screenshot(OUT_DIR / f"fill_{args.item}_{cid}.png")
            _hr("截图")
            print(f"  {shot}")

            path = write_report(
                {"item": args.item, "cid": cid, "summary": summary, "fields": fields},
                OUT_DIR / f"fill_{args.item}_{cid}.json",
            )
            _info(f"字段 dump: {path}")

            if not args.dry_fill:
                _hr("下一步")
                print("  本版本只做「打开页面 + 截图 + dump 字段」，")
                print("  确认 selector 后才会启用自动填表。")
                print("  先看截图和 dump 文件，把关键字段的 selector 告诉我。")
    except CDPError as exc:
        _fail(str(exc))
        return 2

    return 0


def cmd_pages(args: argparse.Namespace) -> int:
    pages = list_pages(args.cdp)
    if not pages:
        _fail(f"{args.cdp} 上没有标签页（Chrome 没开？）")
        return 2
    for i, p in enumerate(pages, 1):
        print(f"{i}. {(p.get('title') or '?')[:44]:46} {p.get('url','')[:80]}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="淘宝发布 PoC（CDP 挂接已有 Chrome，不自动提交）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--cdp", default=CDP_URL_DEFAULT, help=f"CDP 地址，默认 {CDP_URL_DEFAULT}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="检查 Chrome 端口和登录态").set_defaults(func=cmd_check)
    sub.add_parser("pages", help="列出标签页").set_defaults(func=cmd_pages)

    p_probe = sub.add_parser("probe", help="探测发布页结构")
    p_probe.add_argument("--cid", help="目标类目 ID（不带则只看落地页）")
    p_probe.set_defaults(func=cmd_probe)

    p_fill = sub.add_parser("fill", help="打开发布页并按商品数据填表（不提交）")
    p_fill.add_argument("--item", required=True, help="本地已采集的商品 ID")
    p_fill.add_argument("--cid", required=True, help="目标类目 ID")
    p_fill.add_argument("--dry-fill", action="store_true", help="只打开+截图，不尝试填表")
    p_fill.set_defaults(func=cmd_fill)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
