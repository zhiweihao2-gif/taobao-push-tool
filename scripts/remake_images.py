"""商品图重制：保留商品本体，背景全部用 AI 重新生成。

技术链路：
    原图 → rembg 抠图 → 裁透明边 → 按面积缩放 → 贴到 AI 背景
         → 底部投柔和阴影 → 打散指纹（微旋转/缩放/亮度）→ 输出 1440x1440

判据：pHash 汉明距离 ≥ 12（低于说明改得不够，L2 感知哈希仍会命中原图）。

用法：
    python scripts/remake_images.py --item 745703904965
    python scripts/remake_images.py --item 745703904965 --bgs bg_white,bg_stone
    python scripts/remake_images.py --item 745703904965 --main-only
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFilter

from src.core.storage import load_item, save_item
from src.media.restyle import cutout, _trim_alpha, phash, hamming

ROOT = Path(__file__).resolve().parents[1]
BGPACK = ROOT / "data" / "publish_poc" / "bgpack"
OUT = ROOT / "data" / "restyled"

CANVAS = 1440
# 商品底边落在画布的这个比例高度上（0.72 = 下方 28% 留给背景台面/留白）
BASELINE = 0.72
# 商品占画布的面积比例（按面积算，不按长边，避免宽图撑爆）
AREA_RATIO = 0.42


def log(m=""):
    print(m, flush=True)


def _fit_by_area(fg: Image.Image, canvas: int, area_ratio: float) -> Image.Image:
    """按面积缩放：保证 fg 的像素面积占画布 area_ratio，同时不超出 0.88 边长。"""
    target_area = canvas * canvas * area_ratio
    cur_area = fg.width * fg.height
    scale = (target_area / max(1, cur_area)) ** 0.5
    nw, nh = int(fg.width * scale), int(fg.height * scale)
    # 硬约束：任一边不超过画布 0.88
    cap = int(canvas * 0.88)
    if nw > cap or nh > cap:
        s2 = min(cap / nw, cap / nh)
        nw, nh = int(nw * s2), int(nh * s2)
    return fg.resize((max(1, nw), max(1, nh)), Image.LANCZOS)


def _jitter(img: Image.Image, seed: int) -> Image.Image:
    """微旋转 + 微缩放 + 亮度/对比度扰动，用来打散感知哈希。"""
    import numpy as np
    rnd = random.Random(seed)
    ang = rnd.uniform(-1.4, 1.4)
    sc = rnd.uniform(1.006, 1.03)
    w, h = img.size
    big = img.resize((int(w * sc), int(h * sc)), Image.LANCZOS)
    rot = big.rotate(ang, resample=Image.BICUBIC, expand=False)
    cx, cy = rot.width // 2, rot.height // 2
    rot = rot.crop((cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h))
    b = 1.0 + rnd.uniform(-0.03, 0.03)
    c = 1.0 + rnd.uniform(-0.03, 0.03)
    px = np.asarray(rot, dtype=np.float32) * b
    px = (px - 128.0) * c + 128.0
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), rot.mode)


def compose(fg: Image.Image, bg_path: Path, seed: int) -> tuple[Image.Image, dict]:
    bg = Image.open(bg_path).convert("RGB").resize((CANVAS, CANVAS), Image.LANCZOS)
    fg2 = _fit_by_area(fg, CANVAS, AREA_RATIO)
    nw, nh = fg2.size

    x = (CANVAS - nw) // 2
    y = int(CANVAS * BASELINE) - nh

    out = bg.copy()

    # 底部投影（椭圆 + 高斯模糊，比纯 paste 有落地感）
    sh = Image.new("L", (CANVAS, CANVAS), 0)
    d = ImageDraw.Draw(sh)
    ew, eh = int(nw * 0.90), max(14, int(nh * 0.055))
    ex = x + (nw - ew) // 2
    ey = y + nh - eh // 2
    d.ellipse([ex, ey, ex + ew, ey + eh], fill=125)
    sh = sh.filter(ImageFilter.GaussianBlur(radius=max(12, CANVAS // 90)))
    # 背景越暗，阴影越浅
    avg = sum(bg.resize((8, 8)).convert("L").getdata()) / 64.0
    strength = 0.42 if avg > 128 else 0.28
    dark = Image.new("RGB", (CANVAS, CANVAS), (120, 120, 124) if avg > 128 else (20, 20, 24))
    out = Image.composite(dark, out, sh.point(lambda v: int(v * strength)))

    out.paste(fg2, (x, y), fg2)
    out = _jitter(out, seed)
    return out, {"fg": "%dx%d" % (nw, nh), "x": x, "y": y}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--bgs", default="bg_white,bg_stone,bg_wood,bg_dark",
                    help="逗号分隔，来自 data/publish_poc/bgpack/<name>.png")
    ap.add_argument("--main-only", action="store_true", help="只处理主图（详情图是带文字的长条，不适合重制）")
    ap.add_argument("--limit", type=int, default=0, help="每个背景最多处理几张主图")
    a = ap.parse_args()

    it = load_item(a.item)
    if not it:
        log("[XX] 找不到商品 %s" % a.item)
        return 1

    mains = []
    for m in (it.main_images or []):
        lp = m.get("local_path") if isinstance(m, dict) else getattr(m, "local_path", None)
        if lp and os.path.isfile(lp):
            mains.append(Path(lp))
    if a.limit:
        mains = mains[: a.limit]
    log("主图 %d 张，背景 %s" % (len(mains), a.bgs))
    if not mains:
        log("[XX] 没有本地主图")
        return 1

    bgs = [x.strip() for x in a.bgs.split(",") if x.strip()]
    missing = [b for b in bgs if not (BGPACK / (b + ".png")).is_file()]
    if missing:
        log("[XX] 缺背景: %s（先跑 codex 生成，或检查 data/publish_poc/bgpack/）" % missing)
        return 1

    out_dir = OUT / a.item
    out_dir.mkdir(parents=True, exist_ok=True)

    # 抠图只做一次，各背景复用（抠图是最慢的一步）
    log("")
    log("抠图中...")
    t0 = time.time()
    cut = {}
    for p in mains:
        try:
            fg = _trim_alpha(cutout(Image.open(p).convert("RGB")))
            if fg.width < 20 or fg.height < 20:
                log("  %s  前景太小，跳过" % p.name)
                continue
            cut[p] = fg
            log("  %-34s %dx%d" % (p.name, fg.width, fg.height))
        except Exception as e:
            log("  %s  [XX] %s" % (p.name, str(e).split("\n")[0][:60]))
    log("抠图完成 %d/%d，耗时 %.1fs" % (len(cut), len(mains), time.time() - t0))
    if not cut:
        return 1

    log("")
    log("合成中...")
    report = []
    for bi, b in enumerate(bgs):
        bp = BGPACK / (b + ".png")
        for mi, (src, fg) in enumerate(cut.items()):
            dst = out_dir / ("%s__%s.jpg" % (src.stem, b))
            try:
                img, meta = compose(fg, bp, seed=1000 + bi * 97 + mi)
                img.save(dst, quality=93, subsampling=0)
                d = hamming(phash(Image.open(src).convert("RGB")), phash(img))
                report.append({
                    "src": src.name, "bg": b, "dst": str(dst.relative_to(ROOT)),
                    "fg": meta["fg"], "phash_dist": d, "enough": d >= 12,
                    "kb": os.path.getsize(dst) // 1024,
                })
                log("  %-30s + %-12s dist=%-3d %s" % (src.name[:30], b, d, "OK" if d >= 12 else "!!"))
            except Exception as e:
                log("  %s + %s  [XX] %s" % (src.name, b, str(e).split("\n")[0][:60]))

    rep_path = out_dir / "report.json"
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    ok = sum(1 for r in report if r["enough"])
    log("")
    log("=" * 62)
    log("生成 %d 张，pHash 达标 %d 张" % (len(report), ok))
    log("输出目录: %s" % out_dir)
    log("报告: %s" % rep_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
