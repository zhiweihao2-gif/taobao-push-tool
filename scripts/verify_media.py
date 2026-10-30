"""真实验证 ``MediaPipeline``：真实 CDN 下载 + 尺寸规整 + 失败隔离 + 统计。

跑法::

    & $py scripts/verify_media.py

在线部分
1. 淘宝 CDN（``img.alicdn.com``）：主图 1:1 居中裁切 + 短边不足 800 时 Lanczos 放大
2. 京东 CDN：先 GET ``item.m.jd.com`` 详情页，正则抽 ``jfs/t\\d+/...jpg`` 拼
   ``https://img14.360buyimg.com/n0/<path>`` 再下载
3. 故意给一个 404 URL：验证失败只落 manifest.error，不炸整体、不影响其他图
4. 详情图：宽度 > 750 等比缩小；未超宽的保持原尺寸
5. ``item.extra["media_stats"]`` / ``item.extra["media_manifest"]`` 落值

离线部分（内存桩 client，不联网）
6. webp → jpg、透明 GIF → jpg（白底）
7. 200 + text/html 错误页 → 判定失败，绝不存成 .jpg
8. EXIF（含 GPS）被清干净
9. 404 不重试（attempts==1）、瞬时失败重试后成功（attempts==3）
10. ``media.crop_watermark_right`` / ``crop_watermark_bottom`` 对详情图生效
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from PIL import Image as PILImage  # noqa: E402

from src.core.config import get_settings  # noqa: E402
from src.core.models import Category, Image, Item  # noqa: E402
from src.media import downloader as dl  # noqa: E402
from src.media.downloader import (  # noqa: E402
    MediaDownloadError, MediaPipeline, referer_for,
)

PASS, FAIL = 0, 0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 任务给定的淘宝固定样例 URL（部分环境已下线为 1x1 占位 gif，因此做了回退发现）
TAOBAO_FIXED = ("https://img.alicdn.com/imgextra/i1/O1CN01Z5paLz1O0zuMZqf0W_"
                "!!6000000001644-0-tps-800-800.jpg")
JD_DETAIL_PAGE = "https://item.m.jd.com/product/100008348542.html"
BAD_URL = "https://img.alicdn.com/imgextra/i1/__verify_media_404_not_exists__.jpg"

ALI_IMG_RE = re.compile(r"""(?:https?:)?//img\.alicdn\.com/[^"'\s)<>]+\.(?:jpg|jpeg|png)""")
JFS_PATH_RE = re.compile(r"jfs/t\d+/[\w/]+\.jpg")


def check(name: str, cond: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name}  {detail}")
    return bool(cond)


# ---------------------------------------------------------------- 探测工具
class Probe:
    """一次 CDN 探测结果。"""

    def __init__(self, url: str, status: int, ctype: str, size: tuple[int, int] | None,
                 nbytes: int, why: str = "") -> None:
        self.url, self.status, self.ctype = url, status, ctype
        self.size, self.nbytes, self.why = size, nbytes, why

    @property
    def ok(self) -> bool:
        return self.size is not None


def probe(client: httpx.Client, url: str) -> Probe:
    """GET 一次并解码，返回真实尺寸；不可用则 why 说明原因。"""
    try:
        r = client.get(url, headers={"Referer": referer_for(url)})
    except Exception as e:
        return Probe(url, -1, "", None, 0, f"{type(e).__name__}: {e}")
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
    n = len(r.content)
    if r.status_code != 200:
        return Probe(url, r.status_code, ctype, None, n, f"HTTP {r.status_code}")
    if ctype.startswith("text/"):
        return Probe(url, r.status_code, ctype, None, n, f"content-type={ctype}")
    try:
        im = PILImage.open(io.BytesIO(r.content))
        im.load()
    except Exception as e:
        return Probe(url, r.status_code, ctype, None, n, f"解码失败 {type(e).__name__}")
    if min(im.size) < 100:
        return Probe(url, r.status_code, ctype, None, n, f"占位图 {im.size[0]}x{im.size[1]}")
    return Probe(url, r.status_code, ctype, im.size, n)


def ali_candidates(client: httpx.Client) -> list[str]:
    """从淘宝首页抓取真实 img.alicdn.com 图片 URL（固定样例失效时的回退来源）。"""
    out: list[str] = []
    seen: set[str] = set()
    for page in ("https://www.taobao.com/", "https://main.m.taobao.com/"):
        try:
            r = client.get(page)
        except Exception as e:
            print(f"    页面不可达 {page}: {type(e).__name__}: {e}")
            continue
        for m in ALI_IMG_RE.findall(r.text):
            u = m if m.startswith("http") else "https:" + m
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        if len(out) >= 10:
            break
    return out


def pick(cands: list[str], pred, used: set[str], tried: list[str],
         note, max_try: int = 14) -> tuple[str, tuple[int, int]] | None:
    """在候选里挑第一张满足尺寸条件的真实可用图。"""
    n = 0
    for u in cands:
        if u in used or n >= max_try:
            continue
        n += 1
        p = probe(PROBE_CLIENT, u)
        if not p.ok:
            tried.append(f"    跳过 {u}\n         → {p.why}")
            continue
        if not pred(*p.size):
            tried.append(f"    跳过 {u}\n         → 尺寸 {p.size[0]}x{p.size[1]} 不满足条件")
            continue
        used.add(u)
        note.append(f"    选中 {u}  ({p.size[0]}x{p.size[1]}, {p.ctype}, {p.nbytes} bytes)")
        return u, p.size
    return None


PROBE_CLIENT: httpx.Client  # 在 main() 里初始化


# ---------------------------------------------------------------- 京东
def jd_urls(client: httpx.Client) -> tuple[list[str], str]:
    """GET 京东详情页 → 正则抽 jfs 路径 → 拼 img14.360buyimg.com/n0/ 下载地址。"""
    try:
        r = client.get(JD_DETAIL_PAGE, headers={"Referer": "https://item.jd.com/"})
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}"
    paths: list[str] = []
    for p in JFS_PATH_RE.findall(r.text):
        if p not in paths:
            paths.append(p)
    if not paths:
        return [], f"页面 {len(r.text)} 字节，未匹配到 jfs 路径"
    return [f"https://img14.360buyimg.com/n0/{p}" for p in paths], ""


def first_working(client: httpx.Client, urls: list[str]) -> tuple[str, tuple[int, int]] | None:
    for u in urls:
        p = probe(client, u)
        if p.ok:
            return u, p.size
    return None


# ---------------------------------------------------------------- 打印
def show(rec: dict) -> None:
    """打印单图明细：URL / 状态 / 原始尺寸 / 处理后尺寸 / 字节 / 路径。"""
    tag = "OK  " if "error" not in rec else "FAIL"
    print(f"  [{tag}] role={rec.get('role', '-')}")
    print(f"         URL      : {rec.get('url')}")
    if "error" in rec:
        print(f"         状态     : 失败 — {rec['error']}  (attempts={rec.get('attempts')})")
        return
    print(f"         Referer  : {rec.get('referer')}")
    print(f"         原始尺寸 : {rec['src_width']}x{rec['src_height']}"
          f"  ({rec.get('src_format')}, {rec.get('content_type')})")
    print(f"         处理后   : {rec['width']}x{rec['height']}"
          f"{'  ← 已裁切/缩放' if rec.get('resized') else '  ← 保持原尺寸'}")
    print(f"         字节     : {rec['bytes']}   本地: {rec['local']}")
    print(f"         sha256   : {rec['sha256'][:16]}…  attempts={rec.get('attempts')}")


def manifest_of(item_id: str) -> list[dict]:
    p = get_settings().media_dir / item_id / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


def jpeg_info(path: str) -> tuple[tuple[int, int], str, int, int]:
    """(尺寸, mode, 字节数, exif 条目数)"""
    b = Path(path).read_bytes()
    im = PILImage.open(io.BytesIO(b))
    im.load()
    return im.size, im.mode, len(b), len(dict(im.getexif()))


# ---------------------------------------------------------------- 在线验证
def online() -> str:
    print("\n[1] 采集真实 CDN URL")
    global PROBE_CLIENT
    notes: list[str] = []
    tried: list[str] = []
    used: set[str] = set()

    main_small = None
    detail_wide = None
    detail_narrow = None

    p = probe(PROBE_CLIENT, TAOBAO_FIXED)
    if p.ok:
        used.add(TAOBAO_FIXED)
        notes.append(f"    固定样例可用 {TAOBAO_FIXED}  ({p.size[0]}x{p.size[1]})")
        main_small = (TAOBAO_FIXED, p.size) if min(p.size) < 800 else None
        if p.size[0] >= 900:
            detail_wide = (TAOBAO_FIXED, p.size)
        elif 150 <= p.size[0] <= 750:
            detail_narrow = (TAOBAO_FIXED, p.size)
    else:
        notes.append(f"    固定样例不可用：{p.why}（{p.status}, {p.ctype}, {p.nbytes} bytes）→ 回退首页发现")

    cands = ali_candidates(PROBE_CLIENT)
    notes.append(f"    淘宝首页发现 img.alicdn.com 候选 {len(cands)} 个")
    print("\n".join(notes))

    if main_small is None:
        # 优先挑「非正方且尺寸适中」的图：同时验证居中裁正方 + 短边不足放大
        main_small = (pick(cands, lambda w, h: 300 <= min(w, h) < 800 and max(w, h) <= 900,
                           used, tried, notes)
                      or pick(cands, lambda w, h: min(w, h) < 800, used, tried, notes))
    if detail_wide is None:
        detail_wide = pick(cands, lambda w, h: w >= 900, used, tried, notes)
    if detail_narrow is None:
        detail_narrow = pick(cands, lambda w, h: 150 <= w <= 750 and min(w, h) >= 150,
                            used, tried, notes)

    if tried:
        print("    已排除的候选：")
        print("\n".join(tried[:6]))
    if notes:
        print("\n".join(n for n in notes if n.startswith("    选中")))

    # 京东
    jd_list, jd_err = jd_urls(PROBE_CLIENT)
    jd_hit = first_working(PROBE_CLIENT, jd_list)
    if jd_hit:
        print(f"    京东详情页抽取 jfs 路径 {len(jd_list)} 条，命中 {jd_hit[0]}")
        print(f"         → 实际尺寸 {jd_hit[1][0]}x{jd_hit[1][1]}")
    else:
        print(f"    × 京东图片不可用：{jd_err or '所有 jfs 路径均下载失败'}")

    # ------------------------------------------------------------ 建 item
    main_urls: list[tuple[str, str]] = []      # (url, 说明)
    if main_small:
        main_urls.append((main_small[0], f"淘宝 {main_small[1][0]}x{main_small[1][1]}"))
    if jd_hit:
        main_urls.append((jd_hit[0], f"京东 {jd_hit[1][0]}x{jd_hit[1][1]}"))
    main_urls.append((BAD_URL, "故意 404"))

    detail_urls: list[tuple[str, str]] = []
    if detail_wide:
        detail_urls.append((detail_wide[0], f"超宽 {detail_wide[1][0]}x{detail_wide[1][1]}"))
    if detail_narrow:
        detail_urls.append((detail_narrow[0], f"窄图 {detail_narrow[1][0]}x{detail_narrow[1][1]}"))

    check("拿到可用的淘宝 CDN 主图", main_small is not None, "固定样例与首页发现都不可用")
    check("拿到可用的京东 CDN 主图", jd_hit is not None, jd_err)
    check("拿到详情图样例（超宽 + 窄图）", detail_wide is not None and detail_narrow is not None,
          f"wide={bool(detail_wide)} narrow={bool(detail_narrow)}")

    item_id = "verify_media_" + time.strftime("%Y%m%d_%H%M%S")
    item = Item(
        item_id=item_id, platform="taobao",
        title="素材管道真实验证", category=Category(cid="50010850"),
        main_images=[Image(url=u, role="main") for u, _ in main_urls],
        detail_images=[Image(url=u, role="detail") for u, _ in detail_urls],
    )
    print(f"\n[2] 跑 MediaPipeline（item_id={item_id}，"
          f"主图 {len(main_urls)} 张 / 详情图 {len(detail_urls)} 张）")
    t0 = time.time()
    pipe = MediaPipeline(item)
    out = pipe.run(with_detail=True)
    cost = time.time() - t0

    print("\n[3] 单图明细")
    for rec in pipe.manifest:
        show(rec)

    stats = out.extra.get("media_stats", {})
    manifest_path = out.extra.get("media_manifest")
    print(f"\n    统计: {stats}")
    print(f"    manifest: {manifest_path}")
    print(f"    总耗时: {cost:.1f}s")

    # ------------------------------------------------------------ 断言
    print("\n[4] 在线断言")
    ok_recs = [r for r in pipe.manifest if "error" not in r]
    bad_recs = [r for r in pipe.manifest if "error" in r]

    check("manifest 文件已落盘且可读回", bool(manifest_path) and manifest_of(item_id) == pipe.manifest,
          str(manifest_path))
    check("stats.total = 主图+详情图数量",
          stats.get("total") == len(main_urls) + len(detail_urls), str(stats))
    check("stats.ok + stats.failed = stats.total",
          stats.get("ok", 0) + stats.get("failed", 0) == stats.get("total"), str(stats))
    check("stats.failed == 1（只有那个 404）", stats.get("failed") == 1, str(stats))
    check("stats.bytes > 0", stats.get("bytes", 0) > 0, str(stats.get("bytes")))
    check("stats.resized > 0（确实发生了裁切/缩放）", stats.get("resized", 0) > 0, str(stats))

    # 主图 1:1 + 短边 >= 800
    mains = [r for r in ok_recs if r["role"] == "main"]
    for r in mains:
        check(f"主图 1:1 且短边>=800  {r['width']}x{r['height']}",
              r["width"] == r["height"] and min(r["width"], r["height"]) >= 800,
              f"{r['width']}x{r['height']}  src={r['src_width']}x{r['src_height']}")

    # 详情图宽度上限
    for r in [r for r in ok_recs if r["role"] == "detail"]:
        cap = int(get_settings().get("media.detail_max_width", 750))
        check(f"详情图宽度 <= {cap}  ({r['width']}x{r['height']})",
              r["width"] <= cap, f"{r['width']}x{r['height']}")
        if r["src_width"] <= cap:
            check(f"未超宽的详情图不动  {r['width']}x{r['height']}",
                  (r["width"], r["height"]) == (r["src_width"], r["src_height"])
                  and not r["resized"], str(r))

    # 失败路径
    check("404 URL 被记进 manifest.error", any(r.get("url") == BAD_URL for r in bad_recs),
          str([r.get("url") for r in bad_recs]))
    check("失败条目带 error 文案", all(r.get("error") for r in bad_recs), str(bad_recs))
    check("失败不影响其他图（ok>=3）", stats.get("ok", 0) >= 3, str(stats))
    check("run() 仍返回 item（全流程未抛异常）", isinstance(out, Item))

    # 文件实体 + EXIF
    for r in ok_recs:
        size, mode, nbytes, exif_n = jpeg_info(r["local"])
        check(f"磁盘文件存在且为 JPEG/RGB {Path(r['local']).name}",
              size == (r["width"], r["height"]) and mode == "RGB" and nbytes == r["bytes"]
              and Path(r["local"]).suffix == ".jpg", f"{size} {mode} {nbytes}")
        check(f"EXIF 已清空 {Path(r['local']).name}", exif_n == 0, f"exif={exif_n}")

    # 全部失败时 run() 仍返回 item
    print("\n[4b] 全部失败的兜底")
    allbad_id = item_id + "_allbad"
    allbad = Item(
        item_id=allbad_id, platform="taobao", title="全失败",
        category=Category(cid="1"),
        main_images=[Image(url=BAD_URL, role="main"),
                     Image(url=BAD_URL + "?v=2", role="main")],
    )
    t0 = time.time()
    got = MediaPipeline(allbad).run(with_detail=False)
    cost = time.time() - t0
    st = got.extra.get("media_stats", {})
    print(f"    stats={st}  耗时={cost:.1f}s  返回类型={type(got).__name__}")
    check("全部失败时 run() 仍返回 item", isinstance(got, Item) and got is allbad)
    check("全部失败 stats: ok=0 failed=2",
          st.get("ok") == 0 and st.get("failed") == 2 and st.get("total") == 2, str(st))
    check("全部失败 manifest 两条 error",
          len([r for r in manifest_of(allbad_id) if "error" in r]) == 2,
          str(manifest_of(allbad_id)))
    return item_id


# ---------------------------------------------------------------- 离线验证
class _FakeResp:
    def __init__(self, status: int, body: bytes, ctype: str) -> None:
        self.status_code, self.content = status, body
        self.headers = {"content-type": ctype}


class _FakeClient:
    """内存桩 client：按调用次数依次返回预设响应。"""

    def __init__(self, *resps: _FakeResp) -> None:
        self.resps = list(resps)
        self.calls = 0

    def get(self, url: str, headers: dict | None = None) -> _FakeResp:
        self.calls += 1
        return self.resps[min(self.calls - 1, len(self.resps) - 1)]


def _encode(im: PILImage.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def offline() -> None:
    print("\n[5] 离线验证（内存桩 client，不联网）")
    s = get_settings()
    media_cfg = s.raw.setdefault("media", {})
    saved = {k: media_cfg.get(k) for k in ("crop_watermark_bottom", "crop_watermark_right")}
    media_cfg["crop_watermark_bottom"] = 0.0
    media_cfg["crop_watermark_right"] = 0.0
    tag = "verify_media_offline_" + time.strftime("%H%M%S")

    try:
        # 6. webp 1200x900 → 主图 900x900
        webp = _encode(PILImage.new("RGB", (1200, 900), (200, 120, 60)), "WEBP", quality=90)
        it = Item(item_id=tag + "_webp", platform="taobao", title="webp", category=Category(cid="1"))
        p = MediaPipeline(it)
        rec, img = p._download_and_process(  # noqa: SLF001 - 验证内部处理链路
            _FakeClient(_FakeResp(200, webp, "image/webp")),
            "https://img.alicdn.com/x/a.webp", "main", referer_for("https://img.alicdn.com/x/a.webp"))
        print(f"    webp 1200x900 → {rec['width']}x{rec['height']}  "
              f"{rec['bytes']} bytes  {rec['local']}")
        check("webp 转 jpg 且居中裁成 1:1", rec["src_format"] == "WEBP"
              and rec["width"] == rec["height"] == 900 and Path(rec["local"]).suffix == ".jpg",
              str(rec))

        # 7. 透明 GIF → jpg（白底）
        gif = _encode(PILImage.new("P", (300, 200), 0), "GIF", transparency=0)
        it2 = Item(item_id=tag + "_gif", platform="taobao", title="gif", category=Category(cid="1"))
        rec2, _ = MediaPipeline(it2)._download_and_process(  # noqa: SLF001
            _FakeClient(_FakeResp(200, gif, "image/gif")),
            "https://img.alicdn.com/x/a.gif", "detail", referer_for("https://img.alicdn.com/x/a.gif"))
        size2, mode2, _, exif2 = jpeg_info(rec2["local"])
        print(f"    gif 300x200 → {size2[0]}x{size2[1]} mode={mode2}  {rec2['local']}")
        check("透明 GIF 转 RGB JPEG", mode2 == "RGB" and size2 == (300, 200) and exif2 == 0,
              f"{size2} {mode2}")

        # 8. HTML 错误页（200 + text/html）→ 判失败、不落盘、不重试
        html = "<html><body>404 not found</body></html>".encode()
        it3 = Item(item_id=tag + "_html", platform="taobao", title="html", category=Category(cid="1"))
        p3 = MediaPipeline(it3)
        stub_html = _FakeClient(_FakeResp(200, html, "text/html; charset=utf-8"))
        try:
            p3._execute(stub_html, "https://img.alicdn.com/x/dead.jpg", "main",  # noqa: SLF001
                        referer_for("https://img.alicdn.com/x/dead.jpg"))
            check("text/html 被判定为失败", False, "竟然当成图片处理了")
        except MediaDownloadError as e:
            print(f"    200 text/html → 失败: {e.message}  attempts={e.attempts}")
            check("text/html 被判定为失败", "text/html" in e.message, e.message)
            check("text/html 确定性失败不重试", e.attempts == 1 and stub_html.calls == 1,
                  f"attempts={e.attempts} calls={stub_html.calls}")
            check("text/html 不落盘成 .jpg", list(p3.dir.glob("*.jpg")) == [],
                  str([x.name for x in p3.dir.iterdir()]))
            check("text/html 失败写入 manifest.error 且计入 failed",
                  p3.stats["failed"] == 1 and any("error" in r for r in p3.manifest),
                  str(p3.stats))

        # 9. 4xx 不重试（真实网络，URL 已确认返回 404）
        it4 = Item(item_id=tag + "_404", platform="taobao", title="404", category=Category(cid="1"))
        p4 = MediaPipeline(it4)
        t = time.time()
        try:
            p4.download_one(BAD_URL, "main")
            check("404 抛 MediaDownloadError", False)
        except MediaDownloadError as e:
            cost = time.time() - t
            print(f"    404 → 失败: {e.message}  attempts={e.attempts}  {cost:.2f}s")
            check("404 抛 MediaDownloadError 且不重试(attempts=1)",
                  e.attempts == 1, f"attempts={e.attempts} cost={cost:.2f}s")
            check("404 失败条目写入 manifest", any(r.get("error") for r in p4.manifest),
                  str(p4.manifest))
            check("404 error 记录含状态码", any("404" in str(r.get("error", "")) for r in p4.manifest),
                  str(p4.manifest))
            check("404 不产生本地文件", not any(p4.dir.glob("*.jpg")), str(list(p4.dir.glob("*"))))

        # 10. 瞬时失败重试后成功（把退避压成 0，验证 3 次尝试）
        old_backoff = dl.RETRY_BACKOFF
        dl.RETRY_BACKOFF = (0.0, 0.0)
        try:
            png = _encode(PILImage.new("RGB", (600, 600), (10, 200, 30)), "PNG")
            it5 = Item(item_id=tag + "_retry", platform="taobao", title="retry",
                       category=Category(cid="1"))
            p5 = MediaPipeline(it5)
            stub_fail = _FakeClient(_FakeResp(503, b"", "text/html"),
                                    _FakeResp(200, png, "image/png"))
            rec5, _ = p5._download_and_process(stub_fail,  # noqa: SLF001
                                               "https://img.alicdn.com/x/r.png", "main",
                                               referer_for("https://img.alicdn.com/x/r.png"))
            print(f"    503→200 重试成功 attempts={rec5['attempts']} calls={stub_fail.calls}")
            check("5xx 重试后成功（3 次尝试内）",
                  rec5["attempts"] == 2 and stub_fail.calls == 2 and rec5["width"] == 800,
                  str(rec5))
        finally:
            dl.RETRY_BACKOFF = old_backoff

        # 11. EXIF + GPS 清除
        exif = PILImage.Exif()
        exif[0x010F] = "VerifyCam"           # Make
        exif[0x0110] = "VerifyModel-X"       # Model
        exif[0x8825] = {1: "N", 2: (39.0, 54.0, 0.0)}   # GPS IFD
        exif_bytes = exif.tobytes()
        noisy = _encode(PILImage.new("RGB", (1000, 1000), (90, 90, 200)), "JPEG",
                        quality=95, exif=exif_bytes)
        check("构造样本确实带 EXIF", len(dict(PILImage.open(io.BytesIO(noisy)).getexif())) > 0)
        it6 = Item(item_id=tag + "_exif", platform="taobao", title="exif",
                   category=Category(cid="1"))
        rec6, _ = MediaPipeline(it6)._download_and_process(  # noqa: SLF001
            _FakeClient(_FakeResp(200, noisy, "image/jpeg")),
            "https://img.alicdn.com/x/e.jpg", "main", referer_for("https://img.alicdn.com/x/e.jpg"))
        blob = Path(rec6["local"]).read_bytes()
        size6, mode6, _, exif6 = jpeg_info(rec6["local"])
        print(f"    EXIF 样本 1000x1000 → {size6} mode={mode6} exif条目={exif6} "
              f"{rec6['bytes']} bytes")
        check("处理后 EXIF 条目为 0", exif6 == 0, f"exif={exif6}")
        check("GPS / 厂商字符串不出现在文件里",
              b"VerifyCam" not in blob and b"VerifyModel" not in blob)
        check("1:1 且短边>=800 的图保持原尺寸", size6 == (1000, 1000), str(size6))

        # 12. 水印裁切（右 + 底）
        media_cfg["crop_watermark_right"] = 0.10
        media_cfg["crop_watermark_bottom"] = 0.20
        it7 = Item(item_id=tag + "_crop", platform="taobao", title="crop",
                   category=Category(cid="1"))
        p7 = MediaPipeline(it7)
        check("crop 配置已生效（读 settings.media.*）",
              p7.crop_right == 0.10 and p7.crop_bottom == 0.20,
              f"right={p7.crop_right} bottom={p7.crop_bottom}")
        src7 = _encode(PILImage.new("RGB", (480, 400), (255, 0, 0)), "JPEG", quality=95)
        rec7, _ = p7._download_and_process(  # noqa: SLF001
            _FakeClient(_FakeResp(200, src7, "image/jpeg")),
            "https://img.alicdn.com/x/c.jpg", "detail", referer_for("https://img.alicdn.com/x/c.jpg"))
        print(f"    详情图 480x400 → {rec7['width']}x{rec7['height']}（右侧裁 10% + 底部裁 20%）")
        check("详情图右侧裁切生效", rec7["width"] == 432, f"w={rec7['width']}")
        check("详情图底部裁切生效", rec7["height"] == 320, f"h={rec7['height']}")

        # 13. Referer 映射
        check("cbu01.alicdn.com → 1688",
              referer_for("https://cbu01.alicdn.com/img/x.jpg") == "https://detail.1688.com/")
        check("alicdn.com → 淘宝 item",
              referer_for("https://img.alicdn.com/a.jpg") == "https://item.taobao.com/")
        check("360buyimg.com → 京东 item",
              referer_for("https://img14.360buyimg.com/n0/a.jpg") == "https://item.jd.com/")
        check("jd.com → 京东 item",
              referer_for("https://img.jd.com/a.jpg") == "https://item.jd.com/")
        check("未知域名 → 淘宝首页",
              referer_for("https://cdn.example.com/a.jpg") == "https://www.taobao.com/")
    finally:
        for k, v in saved.items():
            if v is None:
                media_cfg.pop(k, None)
            else:
                media_cfg[k] = v


# ---------------------------------------------------------------- main
def main() -> int:
    global PROBE_CLIENT
    print("=" * 78)
    print("MediaPipeline 真实验证")
    print(f"root      : {ROOT}")
    print(f"media_dir : {get_settings().media_dir}")
    print("=" * 78)
    with httpx.Client(timeout=25, follow_redirects=True,
                      headers={"User-Agent": UA,
                               "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}) as pc:
        PROBE_CLIENT = pc
        item_id = online()

    offline()

    print("\n" + "=" * 78)
    print(f"通过 {PASS} / 失败 {FAIL}")
    print(f"在线产物目录: {get_settings().media_dir / item_id}")
    print("=" * 78)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
