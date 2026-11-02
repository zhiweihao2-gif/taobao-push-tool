"""素材管道：并发下载 → 尺寸/比例规整 → EXIF 清理 → 水印裁切 → 落盘 + manifest + 统计。

设计要点
- **Referer 按域名精确映射**：阿里 / 京东 CDN 会校验来源页，按最长域名后缀匹配，
  ``cbu01.alicdn.com``（1688）优先于 ``alicdn.com``（淘宝）
- **单图重试**：最多 3 次尝试（1 次原始 + 2 次重试），指数退避 0.5s / 1.5s；
  只对「可能瞬时恢复」的失败重试（网络异常 / 超时 / 5xx / 408 / 425 / 429 / 空响应体），
  4xx、HTML 错误页属于确定性失败，直接判定失败并记录
- **失败隔离**：单张图失败只写 manifest 的 ``error`` 字段，不影响其他图；
  即使全部失败，``run()`` 仍返回 item
- **文件名**用源文件 sha256 前 16 位，天然去重
- **尺寸策略**（读 ``media.*`` 配置）
  * 主图 / SKU 图：先居中裁成 1:1（淘宝、京东主图强制正方），
    短边 < ``min_main_size`` 时用 Lanczos 放大到 ``target_main_size``
  * 详情图：宽度 > ``detail_max_width`` 时等比缩小（详情图不需要原图分辨率，省空间）
  * 详情图可按 ``crop_watermark_bottom`` / ``crop_watermark_right`` 去水印
- **格式归一**：webp / gif / png / 带透明通道一律转 RGB 存 JPEG（``quality=92, optimize=True``），
  EXIF（含 GPS）全部丢弃——原始 ``info`` 不复制、保存时不写 exif 段
- **产物**：``manifest.json`` + ``item.extra["media_stats"]`` + ``item.extra["media_manifest"]``
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx
from PIL import Image as PILImage
from PIL import ImageOps

from ..core.config import get_settings
from ..core.storage import safe_name
from ..core.models import Image, Item, Video

# Pillow 9.1 起 LANCZOS 迁到 Image.Resampling，这里兼容两代写法
_LANCZOS = getattr(PILImage, "Resampling", PILImage).LANCZOS

DEFAULT_REFERER = "https://www.taobao.com/"

# 域名 → Referer。匹配时取「最长后缀优先」，因此 cbu01.alicdn.com 必须排在 alicdn.com 之前。
REFERERS: dict[str, str] = {
    "cbu01.alicdn.com": "https://detail.1688.com/",   # 1688 商品图
    "cloud.video.taobao.com": "https://item.taobao.com/",  # 淘宝主图视频
    "alicdn.com": "https://item.taobao.com/",         # 淘宝 / 天猫商品图
    "360buyimg.com": "https://item.jd.com/",          # 京东商品图
    "jd.com": "https://item.jd.com/",
}
_REFERER_ORDER: tuple[str, ...] = (
    "cbu01.alicdn.com", "cloud.video.taobao.com",
    "alicdn.com", "360buyimg.com", "jd.com",
)

#: 第 2 / 3 次尝试前的等待秒数（指数退避）
RETRY_BACKOFF: tuple[float, ...] = (0.5, 1.5)

#: 值得重试的 HTTP 状态码（其余 4xx 视为确定性失败）
RETRYABLE_STATUS: frozenset[int] = frozenset({408, 425, 429})

#: 主图 / SKU 图按 1:1 方图规整
_SQUARE_ROLES: frozenset[str] = frozenset({"main", "sku"})

#: 存盘统一用的编码参数
JPEG_QUALITY = 92

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class MediaDownloadError(RuntimeError):
    """单张图下载 / 处理失败。

    携带 ``url`` / ``role`` / ``attempts``，便于上层按失败原因分类统计，
    不需要解析异常字符串。
    """

    def __init__(self, message: str, *, url: str = "", role: str = "", attempts: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.url = url
        self.role = role
        self.attempts = attempts

    def __str__(self) -> str:  # pragma: no cover - 便于日志排查
        return f"[{self.role or '-'}] {self.message} ({self.url})"


class _Retryable(Exception):
    """内部标记：本次失败可能瞬时恢复，值得退避重试。"""


def referer_for(url: str, default: str = DEFAULT_REFERER) -> str:
    """按 URL 主机名映射 Referer，命中不到返回 ``default``（默认淘宝首页）。

    >>> referer_for("https://cbu01.alicdn.com/img/x.jpg")
    'https://detail.1688.com/'
    >>> referer_for("https://img14.360buyimg.com/n0/jfs/t1/a.jpg")
    'https://item.jd.com/'
    >>> referer_for("https://img.alicdn.com/imgextra/i1/a.jpg")
    'https://item.taobao.com/'
    >>> referer_for("https://cdn.example.com/a.jpg")
    'https://www.taobao.com/'
    """
    host = (urlparse(url).hostname or "").lower()
    for domain in _REFERER_ORDER:
        if host == domain or host.endswith("." + domain):
            return REFERERS[domain]
    return default


class MediaPipeline:
    """单商品素材管道。

    用法::

        item = MediaPipeline(item).run(with_detail=True)      # 批量
        image = MediaPipeline(item).download_one(url, "main")  # 单张（发布前补图）
    """

    #: 单张图最多尝试次数 = 1 次原始 + len(RETRY_BACKOFF) 次重试
    MAX_ATTEMPTS = len(RETRY_BACKOFF) + 1

    def __init__(self, item: Item):
        s = get_settings()
        self.item = item
        self.cfg = s
        self.min_size = int(s.get("media.min_main_size", 800))
        self.target_size = int(s.get("media.target_main_size", 800))
        self.detail_max_width = int(s.get("media.detail_max_width", 750))
        self.strip_exif = bool(s.get("media.strip_exif", True))
        # 说明：落盘始终不写 EXIF/GPS（平台会二次压缩，元数据只有风险没有收益），
        # strip_exif 保留用于兼容既有配置，不提供"保留 EXIF"的模式。
        self.quality = int(s.get("media.jpeg_quality", JPEG_QUALITY))
        self.crop_bottom = float(s.get("media.crop_watermark_bottom", 0.0) or 0.0)
        self.crop_right = float(s.get("media.crop_watermark_right", 0.0) or 0.0)
        self.max_workers = max(1, int(s.get("media.max_workers", 8) or 8))
        self.timeout = float(s.get("http.timeout", 20) or 20)
        self.user_agent = str(s.get("http.user_agent_pc") or DEFAULT_UA)
        self.default_referer = DEFAULT_REFERER

        self.dir: Path = s.media_dir / item.item_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path: Path = self.dir / "manifest.json"

        self.manifest: list[dict[str, Any]] = []
        self.stats: dict[str, int] = {"total": 0, "ok": 0, "failed": 0, "bytes": 0, "resized": 0}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ public
    def run(self, with_detail: bool = True, with_video: bool = True) -> Item:
        """下载并处理该商品的全部素材，始终返回 item（失败只体现在 manifest/stats）。"""
        targets: list[Image] = list(self.item.main_images)
        if with_detail:
            targets += list(self.item.detail_images)

        if targets:
            with httpx.Client(timeout=self.timeout, follow_redirects=True,
                              headers=self._base_headers()) as client:
                with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    futures = [pool.submit(self._one, client, img) for img in targets]
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception as e:  # _one 已兜底，这里只防御未知异常
                            self._append({"error": f"unexpected: {type(e).__name__}: {e}"})

        # 视频单独处理：文件大、不能转码、超时要长
        if with_video and self.item.videos:
            for v in self.item.videos:
                try:
                    self._download_video(v)
                except Exception as e:
                    self._append({"role": "video", "url": v.url,
                                  "error": f"{type(e).__name__}: {e}"})
                    self._bump("failed")

        self.item.media_dir = str(self.dir)
        self.item.extra["media_stats"] = dict(self.stats)
        self.item.extra["media_manifest"] = str(self.manifest_path)
        self._write_manifest()
        return self.item

    # ------------------------------------------------------------ 视频
    VIDEO_CHUNK = 512 * 1024        # 每片 512KB
    VIDEO_RETRIES = 3

    def _download_video(self, video: Video) -> None:
        """下载视频，原样保存（不转码），回填 local_path / size / sha256。

        **必须用 Range 分片**：实测淘宝视频 CDN（cloud.video.taobao.com）对
        大文件的流式下载会中途掐断（2.4MB 的文件只给 116KB 就
        ``peer closed connection``），但支持 206 Partial Content。
        分片后 2.4MB 只需 2.3 秒，流式则 150 秒都下不完。
        """
        ref = referer_for(video.url, self.default_referer)
        path = self.dir / f"video_{video.video_id or 'main'}.mp4"
        headers = {**self._base_headers(), "Referer": ref}

        with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0),
                          follow_redirects=True, headers=headers) as client:
            total = self._probe_size(client, video.url)
            if not total:
                raise MediaDownloadError("拿不到视频大小，无法分片下载",
                                         url=video.url, role="video")

            digest = hashlib.sha256()
            written = 0
            with path.open("wb") as f:
                offset = 0
                while offset < total:
                    end = min(offset + self.VIDEO_CHUNK - 1, total - 1)
                    part = self._fetch_range(client, video.url, offset, end)
                    if not part:
                        raise MediaDownloadError(
                            f"分片 {offset}-{end} 拿不到数据", url=video.url,
                            role="video")
                    f.write(part)
                    digest.update(part)
                    written += len(part)
                    offset += len(part)

        if written != total:
            raise MediaDownloadError(
                f"视频大小不符：期望 {total}，实际 {written}",
                url=video.url, role="video")

        video.local_path = str(path)
        video.size_bytes = written
        video.sha256 = digest.hexdigest()
        self._bump("ok")
        self._append({
            "role": "video", "url": video.url, "referer": ref,
            "local": str(path), "bytes": written, "sha256": video.sha256,
            "video_id": video.video_id, "dimension": video.dimension,
            "chunked": True, "content_type": "video/mp4",
        })

    def _probe_size(self, client: httpx.Client, url: str) -> int | None:
        """用第一片 Range 探测总大小（CDN 不返回 content-length 时也能拿到）。"""
        try:
            r = client.get(url, headers={"Range": "bytes=0-0"})
            cr = r.headers.get("content-range", "")   # bytes 0-0/2494780
            if "/" in cr:
                tail = cr.rsplit("/", 1)[-1].strip()
                if tail.isdigit():
                    return int(tail)
            cl = r.headers.get("content-length")
            if cl and cl.isdigit():
                return int(cl)
        except Exception:
            pass
        return None

    def _fetch_range(self, client: httpx.Client, url: str,
                     start: int, end: int) -> bytes:
        for attempt in range(self.VIDEO_RETRIES):
            try:
                r = client.get(url, headers={"Range": f"bytes={start}-{end}"})
                if r.status_code in (200, 206) and r.content:
                    return r.content
            except Exception:
                pass
            time.sleep(0.5 * (attempt + 1))
        return b""

    def download_one(self, url: str, role: str = "main",
                     referer: str | None = None) -> Image:
        """下载 + 处理 + 落盘单张图，返回回填了本地路径/尺寸/hash 的 :class:`Image`。

        失败时抛 :class:`MediaDownloadError`（manifest 里同时留下 ``error`` 记录），
        供发布前补图等外部调用方按需 catch。
        """
        ref = referer or referer_for(url, self.default_referer)
        with httpx.Client(timeout=self.timeout, follow_redirects=True,
                          headers=self._base_headers()) as client:
            _, image = self._execute(client, url, role, ref)
        return image

    # ------------------------------------------------------------ worker
    def _one(self, client: httpx.Client, img: Image) -> None:
        """线程池任务：处理单个 Image 条目，失败不影响其他条目。"""
        try:
            ref = referer_for(img.url, self.default_referer)
            _, done = self._execute(client, img.url, img.role, ref)
        except MediaDownloadError:
            return  # 已写入 manifest.error + stats
        img.local_path = done.local_path
        img.width = done.width
        img.height = done.height
        img.sha256 = done.sha256

    def _execute(self, client: httpx.Client, url: str, role: str,
                 referer: str) -> tuple[dict[str, Any], Image]:
        """下载 + 处理 + 记录统计；成功返回 (manifest 记录, Image)，失败记 error 后抛出。"""
        self._bump("total")
        try:
            record, image = self._download_and_process(client, url, role, referer)
        except MediaDownloadError as e:
            self._append({"role": role, "url": url, "referer": referer,
                          "error": e.message, "attempts": e.attempts})
            self._bump("failed")
            raise
        except Exception as e:  # 未预期的异常也收敛成单图失败
            msg = f"{type(e).__name__}: {e}"
            self._append({"role": role, "url": url, "referer": referer,
                          "error": msg, "attempts": 1})
            self._bump("failed")
            raise MediaDownloadError(msg, url=url, role=role) from e

        self._append(record)
        self._bump("ok")
        self._bump("bytes", int(record.get("bytes") or 0))
        if record.get("resized"):
            self._bump("resized")
        return record, image

    def _download_and_process(self, client: httpx.Client, url: str, role: str,
                              referer: str) -> tuple[dict[str, Any], Image]:
        raw, content_type, attempts = self._fetch(client, url, role, referer)
        digest = hashlib.sha256(raw).hexdigest()

        src, src_format = self._open_image(raw, url, role)
        src_size = src.size
        out, resized = self._apply_policy(src, role)

        path = self.dir / f"{role}_{digest[:16]}.jpg"
        nbytes = self._write_jpeg(out, path)

        image = Image(url=url, role=role, local_path=str(path), sha256=digest,
                      width=out.width, height=out.height)
        record = {
            "role": role,
            "url": url,
            "referer": referer,
            "local": str(path),
            "width": out.width,
            "height": out.height,
            "src_width": src_size[0],
            "src_height": src_size[1],
            "src_format": src_format,
            "content_type": content_type,
            "bytes": nbytes,
            "sha256": digest,
            "resized": resized,
            "attempts": attempts,
        }
        return record, image

    def _fetch(self, client: httpx.Client, url: str, role: str,
               referer: str) -> tuple[bytes, str, int]:
        """带 Referer 下载，返回 (原始字节, content-type, 实际尝试次数)。"""
        last: MediaDownloadError | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                resp = client.get(url, headers={"Referer": referer})
                status = resp.status_code
                if status >= 400:
                    msg = f"HTTP {status}"
                    if status in RETRYABLE_STATUS or status >= 500:
                        raise _Retryable(msg)
                    raise MediaDownloadError(msg, url=url, role=role, attempts=attempt)

                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype.startswith("text/"):
                    # CDN 用 HTML 错误页顶替图片：判失败，绝不落盘成 .jpg
                    raise MediaDownloadError(
                        f"响应是文本而非图片（content-type={ctype}），疑似 HTML 错误页",
                        url=url, role=role, attempts=attempt)
                body = resp.content or b""
                if not body:
                    raise _Retryable("空响应体")
                return body, ctype, attempt
            except _Retryable as e:
                last = MediaDownloadError(str(e), url=url, role=role, attempts=attempt)
            except httpx.TimeoutException as e:
                last = MediaDownloadError(f"超时: {type(e).__name__}", url=url, role=role,
                                          attempts=attempt)
            except httpx.HTTPError as e:
                last = MediaDownloadError(f"{type(e).__name__}: {e}", url=url, role=role,
                                          attempts=attempt)

            if attempt <= len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt - 1])

        raise last or MediaDownloadError("下载失败", url=url, role=role,
                                         attempts=self.MAX_ATTEMPTS)

    # ------------------------------------------------------------ 图像处理
    def _open_image(self, raw: bytes, url: str, role: str) -> tuple[PILImage.Image, str | None]:
        """解码字节流，返回 (图像, 原始格式)；Exif 方向先摆正，后续统一转 RGB。"""
        try:
            im = PILImage.open(io.BytesIO(raw))
            im.load()
        except Exception as e:
            raise MediaDownloadError(f"图片解码失败: {type(e).__name__}: {e}",
                                     url=url, role=role) from e
        src_format = im.format
        try:
            im = ImageOps.exif_transpose(im) or im
        except Exception:
            pass  # 方向信息异常时不影响主流程
        return im, src_format

    def _apply_policy(self, im: PILImage.Image, role: str) -> tuple[PILImage.Image, bool]:
        """按角色做裁切 / 缩放，返回 (处理后图像, 是否改变了尺寸)。"""
        changed = False
        w, h = im.size

        # 1) 去水印：只对详情图，按比例裁掉底部 / 右侧
        if role == "detail" and (0 < self.crop_bottom < 1 or 0 < self.crop_right < 1):
            nw = w if self.crop_right <= 0 else max(1, int(round(w * (1 - self.crop_right))))
            nh = h if self.crop_bottom <= 0 else max(1, int(round(h * (1 - self.crop_bottom))))
            if (nw, nh) != (w, h):
                im = im.crop((0, 0, nw, nh))
                changed = True

        # 2) 统一转 RGB（带 alpha 的贴合白底，避免转 JPEG 变黑）
        im = self._to_rgb(im)

        # 3) 主图 / SKU 图：居中裁正方 → 不足则放大（淘宝、京东主图强制 1:1）
        if role in _SQUARE_ROLES:
            w, h = im.size
            if w != h:
                side = min(w, h)
                left = (w - side) // 2
                top = (h - side) // 2
                im = im.crop((left, top, left + side, top + side))
                changed = True
            if min(im.size) < self.min_size:
                im = im.resize((self.target_size, self.target_size), _LANCZOS)
                changed = True

        # 4) 详情图：超宽等比缩小
        elif role == "detail" and self.detail_max_width > 0 and im.width > self.detail_max_width:
            nh = max(1, int(round(im.height * self.detail_max_width / im.width)))
            im = im.resize((self.detail_max_width, nh), _LANCZOS)
            changed = True

        return im, changed

    @staticmethod
    def _to_rgb(im: PILImage.Image) -> PILImage.Image:
        """任意模式（P / RGBA / LA / CMYK / GIF）转 RGB，透明区贴白底。"""
        if im.mode == "RGB":
            return im
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = PILImage.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            return bg
        return im.convert("RGB")

    def _write_jpeg(self, im: PILImage.Image, path: Path) -> int:
        """落盘为 JPEG：quality=92 + optimize，且完全不携带 EXIF（含 GPS）。"""
        clean = im if im.mode == "RGB" else self._to_rgb(im)
        for key in ("exif", "icc_profile", "xmp", "XML:com.adobe.xmp"):
            clean.info.pop(key, None)
        buf = io.BytesIO()
        # 不传 exif= 参数，Pillow 就不会写入任何元数据段
        clean.save(buf, format="JPEG", quality=self.quality, optimize=True)
        data = buf.getvalue()
        path.write_bytes(data)
        return len(data)

    # ------------------------------------------------------------ 记录
    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }

    def _append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.manifest.append(record)

    def _bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + n

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def download_all(items: Iterable[Item], with_detail: bool = True) -> list[Item]:
    """批量跑素材管道，逐商品串行（商品内部并发）。"""
    return [MediaPipeline(it).run(with_detail=with_detail) for it in items]
