"""素材管道离线回归：不联网，用内存桩 client 驱动完整处理链路。

跑法::

    python tests/test_media.py

覆盖：
1. Referer 按域名精确映射（淘宝 / 1688 / 京东 / 兜底）
2. 主图：非正方居中裁切 + 短边不足 Lanczos 放大到 800x800
3. 主图：已满足 1:1 且短边 >= 800 时不动
4. 详情图：超宽等比缩到 750；未超宽不动
5. webp / 透明 GIF / RGBA PNG → RGB JPEG（白底）
6. 200 + text/html 错误页：判失败、不落盘、不重试
7. 4xx 不重试，5xx 重试 2 次后成功；全失败时 attempts == 3 且只记 error
8. EXIF（含 GPS）清空
9. 去水印：底部 / 右侧按比例裁切（详情图）
10. stats / manifest / run() 空素材的统计结构
11. sha256 去重：同内容同名文件
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image as PILImage  # noqa: E402

from src.core.config import get_settings  # noqa: E402
from src.core.models import Category, Image, Item  # noqa: E402
from src.media import downloader as dl  # noqa: E402
from src.media.downloader import (  # noqa: E402
    MediaDownloadError, MediaPipeline, referer_for,
)

PASS, FAIL = 0, 0
TAG = "test_media_regress"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name}  {detail}")


# ---------------------------------------------------------------- 桩
class Resp:
    def __init__(self, status: int, body: bytes = b"", ctype: str = "image/jpeg") -> None:
        self.status_code, self.content = status, body
        self.headers = {"content-type": ctype}


class Stub:
    """按调用次序返回预设响应，并记录调用次数 / 最后一次的 Referer。"""

    def __init__(self, *resps: Resp) -> None:
        self.resps = list(resps)
        self.calls = 0
        self.referers: list[str | None] = []

    def get(self, url: str, headers: dict | None = None) -> Resp:
        self.calls += 1
        self.referers.append((headers or {}).get("Referer"))
        return self.resps[min(self.calls - 1, len(self.resps) - 1)]


def blob(im: PILImage.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def solid(w: int, h: int, color=(120, 60, 200)) -> bytes:
    return blob(PILImage.new("RGB", (w, h), color), "JPEG", quality=95)


def pipe(name: str) -> MediaPipeline:
    item = Item(item_id=f"{TAG}_{name}", platform="taobao", title=name,
                category=Category(cid="1"))
    return MediaPipeline(item)


def proc(p: MediaPipeline, body: bytes, role: str, ctype: str = "image/jpeg",
         url: str = "https://img.alicdn.com/x/a.jpg", status: int = 200):
    """用桩 client 走一次 _download_and_process。"""
    return p._download_and_process(  # noqa: SLF001 - 故意验证内部链路
        Stub(Resp(status, body, ctype)), url, role, referer_for(url))


def jpeg_of(path: str) -> PILImage.Image:
    im = PILImage.open(path)
    im.load()
    return im


# ---------------------------------------------------------------- 1. Referer
def test_referer() -> None:
    print("\n[1] Referer 映射")
    cases = {
        "https://img.alicdn.com/imgextra/i1/a.jpg": "https://item.taobao.com/",
        "https://img.alicdn.com/a.jpg?x=1": "https://item.taobao.com/",
        "https://cbu01.alicdn.com/img/x.jpg": "https://detail.1688.com/",
        "https://gd2.alicdn.com/cbu01.alicdn.com/x.jpg": "https://item.taobao.com/",
        "https://img14.360buyimg.com/n0/jfs/t1/a.jpg": "https://item.jd.com/",
        "https://img.jd.com/a.jpg": "https://item.jd.com/",
        "https://cdn.example.com/a.jpg": "https://www.taobao.com/",
        "not-a-url": "https://www.taobao.com/",
    }
    for url, want in cases.items():
        check(f"referer {url[:42]:<42} → {want}", referer_for(url) == want, referer_for(url))


# ---------------------------------------------------------------- 2/3. 主图
def test_main() -> None:
    print("\n[2] 主图 1:1 与放大")
    p = pipe("main")

    rec, img = proc(p, solid(480, 320), "main")
    check("480x320 → 800x800", (rec["width"], rec["height"]) == (800, 800), str(rec))
    check("resized 标记", rec["resized"] is True)
    check("Image 回填尺寸", (img.width, img.height) == (800, 800), str(img))
    check("源尺寸留档", (rec["src_width"], rec["src_height"]) == (480, 320))
    check("落盘 .jpg 且为 RGB",
          Path(rec["local"]).suffix == ".jpg" and jpeg_of(rec["local"]).mode == "RGB")

    rec2, _ = proc(p, solid(1200, 1000), "main")
    check("1200x1000 → 居中裁 1000x1000（不缩小）",
          (rec2["width"], rec2["height"]) == (1000, 1000), str(rec2))

    rec3, _ = proc(p, solid(1000, 1000), "main")
    check("1000x1000 不动", (rec3["width"], rec3["height"]) == (1000, 1000)
          and rec3["resized"] is False, str(rec3))

    rec4, _ = proc(p, solid(300, 900), "main")
    check("300x900 → 裁 300x300 再放大 800x800",
          (rec4["width"], rec4["height"]) == (800, 800)
          and (rec4["src_width"], rec4["src_height"]) == (300, 900), str(rec4))


# ---------------------------------------------------------------- 4. 详情图
def test_detail() -> None:
    print("\n[3] 详情图宽度上限")
    p = pipe("detail")
    rec, _ = proc(p, solid(1040, 384), "detail")
    check("1040x384 → 750x277", (rec["width"], rec["height"]) == (750, 277), str(rec))
    rec2, _ = proc(p, solid(480, 672), "detail")
    check("480x672 不动", (rec2["width"], rec2["height"]) == (480, 672)
          and rec2["resized"] is False, str(rec2))


# ---------------------------------------------------------------- 5. 格式
def test_format() -> None:
    print("\n[4] 格式归一")
    p = pipe("format")
    webp = blob(PILImage.new("RGB", (1200, 900), (5, 5, 5)), "WEBP", quality=88)
    rec, _ = proc(p, webp, "main", "image/webp", "https://img.alicdn.com/x/a.webp")
    check("webp → jpg 900x900", rec["src_format"] == "WEBP"
          and (rec["width"], rec["height"]) == (900, 900)
          and Path(rec["local"]).suffix == ".jpg", str(rec))

    gif = blob(PILImage.new("P", (300, 200), 0), "GIF", transparency=0)
    rec2, _ = proc(p, gif, "detail", "image/gif", "https://img.alicdn.com/x/b.gif")
    check("透明 GIF → RGB jpg", jpeg_of(rec2["local"]).mode == "RGB"
          and (rec2["width"], rec2["height"]) == (300, 200), str(rec2))

    rgba = PILImage.new("RGBA", (600, 600), (255, 0, 0, 0))   # 全透明
    rec3, _ = proc(p, blob(rgba, "PNG"), "main", "image/png",
                   "https://img.alicdn.com/x/c.png")
    px = jpeg_of(rec3["local"]).convert("RGB").getpixel((5, 5))
    check("透明 PNG 贴白底（角落为白）", all(v > 245 for v in px), str(px))


# ---------------------------------------------------------------- 6/7. 失败
def test_failure() -> None:
    print("\n[5] 失败路径与重试")
    old = dl.RETRY_BACKOFF
    dl.RETRY_BACKOFF = (0.0, 0.0)
    try:
        p = pipe("html")
        stub = Stub(Resp(200, b"<html>oops</html>", "text/html; charset=utf-8"))
        try:
            p._execute(stub, "https://img.alicdn.com/x/dead.jpg", "main",  # noqa: SLF001
                       referer_for("https://img.alicdn.com/x/dead.jpg"))
            check("text/html 判失败", False, "被当成图片了")
        except MediaDownloadError as e:
            check("text/html 判失败并写明原因", "text/html" in e.message, e.message)
            check("text/html 不重试", e.attempts == 1 and stub.calls == 1,
                  f"attempts={e.attempts} calls={stub.calls}")
            check("text/html 不落盘", list(p.dir.glob("*.jpg")) == [])
        check("失败计入 stats.failed 并写 manifest.error",
              p.stats["failed"] == 1 and any("error" in r for r in p.manifest), str(p.stats))

        p404 = pipe("404")
        stub404 = Stub(Resp(404, b"", "text/html"))
        try:
            p404._execute(stub404, "https://img14.360buyimg.com/n0/jfs/t1/x/none.jpg",  # noqa: SLF001
                          "main", "https://item.jd.com/")
            check("404 判失败", False)
        except MediaDownloadError as e:
            check("404 不重试（4xx 确定性失败）", e.attempts == 1 and stub404.calls == 1,
                  f"attempts={e.attempts} calls={stub404.calls}")

        prec = pipe("retryok")
        good = solid(600, 600)
        stub_ok = Stub(Resp(503, b"", "text/html"), Resp(200, good, "image/jpeg"))
        rec, _ = prec._download_and_process(  # noqa: SLF001
            stub_ok, "https://img.alicdn.com/x/a.jpg", "main",
            referer_for("https://img.alicdn.com/x/a.jpg"))
        check("503 → 200 重试后成功", rec["attempts"] == 2 and stub_ok.calls == 2
              and (rec["width"], rec["height"]) == (800, 800), str(rec))

        pdead = pipe("retrydead")
        stub_dead = Stub(Resp(503, b"", "text/html"))
        try:
            pdead._execute(stub_dead, "https://img.alicdn.com/x/a.jpg", "main",  # noqa: SLF001
                           referer_for("https://img.alicdn.com/x/a.jpg"))
            check("持续 5xx 最终失败", False)
        except MediaDownloadError as e:
            check("持续 5xx：共 3 次尝试后失败", e.attempts == 3 and stub_dead.calls == 3,
                  f"attempts={e.attempts} calls={stub_dead.calls}")
            check("持续 5xx 只写一条 error 记录",
                  len([r for r in pdead.manifest if "error" in r]) == 1, str(pdead.manifest))

        ptimeout = pipe("timeout")
        try:
            ptimeout._download_and_process(  # noqa: SLF001
                Stub(Resp(500, b"", ""), Resp(200, good, "image/jpeg")),
                "https://img.alicdn.com/x/a.jpg", "main",
                referer_for("https://img.alicdn.com/x/a.jpg"))
            check("5xx 重试链正常", True)
        except MediaDownloadError as e:
            check("5xx 重试链正常", False, e.message)
    finally:
        dl.RETRY_BACKOFF = old


# ---------------------------------------------------------------- 8. EXIF
def test_exif() -> None:
    print("\n[6] EXIF / GPS 清除")
    exif = PILImage.Exif()
    exif[0x010F] = "RegressCam"
    exif[0x0110] = "RegressModel"
    exif[0x8825] = {1: "N", 2: (31.0, 14.0, 0.0)}
    noisy = blob(PILImage.new("RGB", (1000, 1000), (30, 30, 30)), "JPEG",
                 quality=95, exif=exif.tobytes())
    check("样本带 EXIF", len(dict(PILImage.open(io.BytesIO(noisy)).getexif())) >= 3)
    rec, _ = proc(pipe("exif"), noisy, "main")
    out = Path(rec["local"]).read_bytes()
    check("输出 EXIF 为空", len(dict(jpeg_of(rec["local"]).getexif())) == 0)
    check("GPS / 厂商串不残留",
          b"RegressCam" not in out and b"RegressModel" not in out)
    check("strip_exif 配置存在", "strip_exif" in dir(pipe("exif")))


# ---------------------------------------------------------------- 9. 水印
def test_watermark() -> None:
    print("\n[7] 去水印裁切")
    cfg = get_settings().raw.setdefault("media", {})
    saved = {k: cfg.get(k) for k in ("crop_watermark_bottom", "crop_watermark_right")}
    try:
        cfg["crop_watermark_right"] = 0.10
        cfg["crop_watermark_bottom"] = 0.20
        p = pipe("crop")
        check("配置读入", (p.crop_right, p.crop_bottom) == (0.10, 0.20),
              f"{p.crop_right}/{p.crop_bottom}")
        rec, _ = proc(p, solid(480, 400), "detail")
        check("详情图 480x400 → 432x320", (rec["width"], rec["height"]) == (432, 320), str(rec))
        rec2, _ = proc(p, solid(480, 400), "main")
        check("主图不做水印裁切（仍 800x800）",
              (rec2["width"], rec2["height"]) == (800, 800), str(rec2))
    finally:
        for k, v in saved.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v


# ---------------------------------------------------------------- 10/11. 统计
def test_stats() -> None:
    print("\n[8] 统计 / manifest / 去重")
    item = Item(item_id=f"{TAG}_stats", platform="taobao", title="stats",
                category=Category(cid="1"), main_images=[], detail_images=[])
    out = MediaPipeline(item).run(with_detail=True)
    check("空素材 run() 返回 item", out is item)
    check("空素材 stats 全 0",
          out.extra.get("media_stats") == {"total": 0, "ok": 0, "failed": 0,
                                           "bytes": 0, "resized": 0},
          str(out.extra.get("media_stats")))
    check("empty manifest 已落盘",
          Path(out.extra.get("media_manifest", "")).exists(), str(out.extra))
    check("media_dir 回填", out.media_dir == str(get_settings().media_dir / item.item_id))

    p = pipe("stats2")
    body = solid(500, 500)
    url = "https://img.alicdn.com/x/a.jpg"
    rec_a, _ = p._execute(Stub(Resp(200, body, "image/jpeg")), url, "main",  # noqa: SLF001
                          referer_for(url))
    rec_b, _ = p._execute(Stub(Resp(200, body, "image/jpeg")), url, "main",  # noqa: SLF001
                          referer_for(url))
    check("同内容 sha256 去重（同名文件）", rec_a["local"] == rec_b["local"], str(rec_a["local"]))
    check("stats 键齐备", set(p.stats) == {"total", "ok", "failed", "bytes", "resized"},
          str(p.stats))
    check("stats.bytes 累加", p.stats["bytes"] == 2 * rec_a["bytes"], str(p.stats))
    check("stats.total 累加", p.stats["total"] == 2, str(p.stats))
    check("manifest 记录字段完整",
          {"role", "url", "referer", "local", "width", "height", "src_width",
           "src_height", "bytes", "sha256", "resized", "attempts"} <= set(rec_a),
          str(sorted(rec_a)))


def main() -> int:
    test_referer()
    test_main()
    test_detail()
    test_format()
    test_failure()
    test_exif()
    test_watermark()
    test_stats()
    print(f"\n{'=' * 40}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
