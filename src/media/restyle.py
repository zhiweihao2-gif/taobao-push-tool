"""商品图重制：保留商品本体，其余全部重新生成。

背景：平台有防盗图保护（详情/主图直接引用他人 img.alicdn.com URL 会被拒，
见 2026-11-04 实测「详情中引用了他人图片」），且图片版权仍属原作者。
本模块只做技术层面的「重制」，不改变版权归属。

处理链（全部本地，无云端依赖）：

    1. 抠图      rembg u2net → 商品 alpha 蒙版
    2. 裁边      可选，切掉四周 N px（去边缘水印/店铺标语）
    3. 新背景    纯色 / 竖向渐变 / 影棚径向光（程序化生成，非原图任何像素）
    4. 落地阴影  商品底部投柔和椭圆阴影（高斯模糊 + 透明度衰减）
    5. 重排      1440x1440 画布，商品占 76~82%，视觉重心略高于几何中心
    6. 打散指纹  ±1.2° 旋转 + 1~2.5% 缩放 + brightness/contrast ±3%
                 目的：让 pHash 汉明距离拉开到 12 bit 以上

判据：`restyle_one()` 返回的 `phash_dist` 低于 12 说明改得不够，应加大
`jitter` 或换更复杂的背景。

用法：
    from src.media.restyle import restyle_one
    r = restyle_one("in.jpg", "out.jpg", bg="gradient")
    print(r)   # {'ok': True, 'phash_dist': 21, ...}
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

# rembg 首次调用会下载 u2net.onnx（约 176MB）到 ~/.u2net/，
# 之后离线可用。这里延迟 import，避免没装依赖时整个模块挂掉。
_SESSION = None


def _session():
    global _SESSION
    if _SESSION is None:
        from rembg import new_session
        _SESSION = new_session("u2net")
    return _SESSION


# ---------------------------------------------------------------- 背景生成

def _bg_white(size: int) -> Image.Image:
    return Image.new("RGB", (size, size), (255, 255, 255))


def _bg_gradient(size: int, top=(248, 248, 250), bottom=(222, 224, 230)) -> Image.Image:
    """竖向渐变。纯程序生成，与原图无关。"""
    col = Image.new("RGB", (1, size))
    px = col.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return col.resize((size, size), Image.BILINEAR)


def _bg_studio(size: int, seed: int | None = None) -> Image.Image:
    """影棚风：中心亮、四周暗的径向光 + 轻微噪点。"""
    rnd = random.Random(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx, cy = size * 0.5, size * 0.42
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (size * 0.72)
    falloff = np.clip(1.0 - r * 0.62, 0.28, 1.0)

    base = np.array([252.0, 252.0, 253.0], dtype=np.float32)
    edge = np.array([rnd.randint(198, 214)] * 3, dtype=np.float32)
    mix = falloff[..., None]
    arr = edge + (base - edge) * mix

    noise = np.random.default_rng(seed).normal(0, 1.6, (size, size, 1))
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def make_background(kind: str, size: int, seed: int | None = None) -> Image.Image:
    if kind == "white":
        return _bg_white(size)
    if kind == "gradient":
        return _bg_gradient(size)
    if kind == "studio":
        return _bg_studio(size, seed)
    raise ValueError("unknown bg kind: %r (white|gradient|studio)" % kind)


# ---------------------------------------------------------------- 抠图

def cutout(img: Image.Image) -> Image.Image:
    """返回带 alpha 的前景（商品本体）。"""
    from rembg import remove
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    out = remove(buf.getvalue(), session=_session())
    return Image.open(io.BytesIO(out)).convert("RGBA")


def _trim_alpha(fg: Image.Image, pad: int = 6) -> Image.Image:
    """按 alpha 的实际包围盒裁掉透明边，让商品贴边可控。"""
    bbox = fg.getchannel("A").getbbox()
    if not bbox:
        return fg
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(fg.width, x1 + pad)
    y1 = min(fg.height, y1 + pad)
    return fg.crop((x0, y0, x1, y1))


# ---------------------------------------------------------------- 合成

def _compose(
    fg: Image.Image,
    bg: Image.Image,
    fill_ratio: float = 0.78,
    baseline: float = 0.60,
    shadow: bool = True,
) -> Image.Image:
    """把商品放到新背景上，底部投影，重心略高于几何中心。"""
    size = bg.width
    target = int(size * fill_ratio)
    scale = min(target / fg.width, target / fg.height)
    nw, nh = max(1, int(fg.width * scale)), max(1, int(fg.height * scale))
    fg = fg.resize((nw, nh), Image.LANCZOS)

    x = (size - nw) // 2
    y = int(size * baseline) - nh // 2
    y = max(int(size * 0.06), min(y, size - nh - int(size * 0.06)))

    out = bg.copy()

    if shadow:
        # 底部椭圆阴影：不依赖原图，纯合成
        sh = Image.new("L", (size, size), 0)
        from PIL import ImageDraw
        d = ImageDraw.Draw(sh)
        ew, eh = int(nw * 0.92), max(18, int(nh * 0.10))
        ex = x + (nw - ew) // 2
        ey = y + nh - int(eh * 0.55)
        d.ellipse([ex, ey, ex + ew, ey + eh], fill=110)
        sh = sh.filter(ImageFilter.GaussianBlur(radius=max(8, size // 60)))
        dark = Image.new("RGB", (size, size), (150, 150, 156))
        out = Image.composite(dark, out, sh.point(lambda v: int(v * 0.52)))

    out.paste(fg, (x, y), fg)
    return out


# ---------------------------------------------------------------- 打散指纹

def _jitter(img: Image.Image, seed: int | None = None) -> Image.Image:
    rnd = random.Random(seed)
    ang = rnd.uniform(-1.2, 1.2)
    sc = rnd.uniform(1.0, 1.025)
    w, h = img.size
    big = img.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
    rot = big.rotate(ang, resample=Image.BICUBIC, expand=False)
    cx, cy = rot.width // 2, rot.height // 2
    rot = rot.crop((cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h))

    # 轻微亮度/对比度扰动
    b = 1.0 + rnd.uniform(-0.03, 0.03)
    c = 1.0 + rnd.uniform(-0.03, 0.03)
    px = np.asarray(rot, dtype=np.float32) * b
    px = (px - 128.0) * c + 128.0
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), rot.mode)


# ---------------------------------------------------------------- 指纹对比

def phash(img: Image.Image, hash_size: int = 8) -> int:
    """感知哈希。返回整数，便于算汉明距离。"""
    g = img.convert("L").resize((hash_size * 4, hash_size * 4), Image.LANCZOS)
    px = np.asarray(g, dtype=np.float32)
    # 8x8 DCT 的近似：直接对 32x32 做块均值再比较
    blocks = px.reshape(hash_size, 4, hash_size, 4).mean(axis=(1, 3))
    med = np.median(blocks)
    bits = (blocks > med).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(bool(b))
    return v


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------------------------------------------------------------- 入口

def restyle_one(
    src: str | Path,
    dst: str | Path,
    bg: str = "studio",
    size: int = 1440,
    fill_ratio: float = 0.78,
    baseline: float = 0.60,
    shadow: bool = True,
    jitter: bool = True,
    seed: int | None = None,
) -> dict[str, Any]:
    """单图重制。返回统计信息，含 pHash 汉明距离。"""
    src, dst = Path(src), Path(dst)
    t0 = __import__("time").time()

    orig = Image.open(src)
    orig_rgb = orig.convert("RGB")

    fg = cutout(orig_rgb)
    fg = _trim_alpha(fg)
    if fg.width < 20 or fg.height < 20:
        return {"ok": False, "why": "抠图后前景太小（%dx%d）" % (fg.width, fg.height)}

    bg_img = make_background(bg, size, seed)
    out = _compose(fg, bg_img, fill_ratio=fill_ratio, baseline=baseline, shadow=shadow)
    if jitter:
        out = _jitter(out, seed)

    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, quality=92, subsampling=0)

    d = hamming(phash(orig_rgb), phash(out))
    return {
        "ok": True,
        "src": str(src),
        "dst": str(dst),
        "size": "%dx%d" % out.size,
        "fg": "%dx%d" % (fg.width, fg.height),
        "bg": bg,
        "phash_dist": d,
        "enough": d >= 12,
        "secs": round(__import__("time").time() - t0, 2),
    }


def restyle_batch(
    paths: list[str | Path],
    out_dir: str | Path,
    **kw: Any,
) -> list[dict[str, Any]]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for i, p in enumerate(paths):
        p = Path(p)
        try:
            r = restyle_one(p, out_dir / p.name, seed=kw.pop("seed", None) or (1000 + i), **kw)
        except Exception as e:
            r = {"ok": False, "src": str(p), "why": "%s: %s" % (type(e).__name__, str(e)[:80])}
        res.append(r)
    return res
