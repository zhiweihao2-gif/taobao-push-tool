"""用本机 codex 的 image_gen.imagegen 重做商品主图。

思路：保留商品本体（外形/配色/键位/文字），背景、灯光、台面、投影全部由 AI 重新生成。
与 `remake_images.py`（抠图 + 合成）的区别：那条是「原图像素 + 新背景」，这条是「AI 重画整张图」，
pHash 与感知特征都与原图拉开得远得多。

前提：
    codex.exe 可执行（默认 C:\\Users\\<user>\\AppData\\Local\\OpenAI\\Codex\\bin\\<hash>\\codex.exe）
    codex 已登录，且 image_gen.imagegen 工具可用

用法：
    python scripts\\codex_remake.py --item 745703904965
    python scripts\\codex_remake.py --item 745703904965 --preset stone --size 1440
    python scripts\\codex_remake.py --item 745703904965 --dry-run     # 只打印 prompt
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from src.core.storage import load_item
from src.media.restyle import phash, hamming

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "restyled"
CODEX_GEN = Path.home() / ".codex" / "generated_images"

# 统一背景（用户选定：全店一套风格）
PRESETS = {
    "stone": (
        "浅灰色影棚风。商品放在一个哑光深灰色立方体石台上（石台表面有细微天然纹理），"
        "顶部偏左的柔和主光源，右侧有极淡的补光，商品下方有自然柔和的落地投影，"
        "四周背景是干净的浅灰到白的渐变，画面边缘有轻微暗角。"
    ),
    "white": (
        "纯白电商主图风。商品置于纯白无缝背景前，底部有极淡的柔和投影（几乎看不见），"
        "均匀散射光，无任何道具、台面、装饰。"
    ),
    "studio": (
        "浅灰专业影棚。商品悬置于浅灰渐变背景前，顶部大面积柔光箱打光，"
        "正面有细微反光，底部有柔和的接触阴影，画面干净克制。"
    ),
}


def find_codex() -> str | None:
    """定位 codex.exe。"""
    env = os.environ.get("CODEX_EXE")
    if env and Path(env).is_file():
        return env
    pats = [
        str(Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "*" / "codex.exe"),
        str(Path.home() / ".local" / "bin" / "codex"),
        "/usr/local/bin/codex",
    ]
    for p in pats:
        hits = sorted(glob.glob(p))
        if hits:
            return hits[-1]
    return shutil.which("codex")


def build_prompt(src: Path, preset: str, out_path: Path | None = None) -> str:
    bg = PRESETS[preset]
    lines = [
        "用 image_gen.imagegen 工具，以我附加的这张商品照片作为参考，重新生成一张商品主图。",
        "",
        "【硬性要求】",
        "1. 商品本体必须与原图一致：外形轮廓、整体配色、结构布局、上面的文字与图案都不能改动。",
        "2. 只重新生成「商品以外的所有内容」：背景、灯光、台面、投影、氛围。",
        "3. 不要添加任何文字、logo、水印、价签、装饰物、人物。",
        "",
        "【新场景】",
        bg,
        "",
        "【构图】",
        "商品在画面中占约 55% 面积，视觉重心略微偏下，四周留白充足。",
        "",
        "参考图：%s" % src,
    ]
    if out_path:
        lines += ["", "生成结果保存到：%s" % out_path]
    lines += ["", "只做这一件事。完成后用一行报告最终文件路径和尺寸。"]
    return "\n".join(lines)


def latest_generated(after: float) -> Path | None:
    """找 codex 刚生成的图片（~/.codex/generated_images 下最新的）。"""
    if not CODEX_GEN.is_dir():
        return None
    cands = []
    for p in CODEX_GEN.rglob("*.png"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m >= after - 2:
            cands.append((m, p))
    if not cands:
        return None
    return max(cands)[1]


def run_one(codex: str, src: Path, preset: str, dst: Path, timeout: int = 300) -> dict:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()

    prompt = build_prompt(src, preset, dst)
    t0 = time.time()
    try:
        r = subprocess.run(
            [codex, "exec", "--skip-git-repo-check", "-i", str(src), "-"],
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "src": src.name, "why": "codex 超时 %ds" % timeout}
    dt = time.time() - t0

    if not dst.is_file():
        cand = latest_generated(t0)
        if cand:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cand, dst)

    if not dst.is_file():
        tail = (r.stderr or r.stdout or "")[-300:]
        return {"ok": False, "src": src.name, "why": "没拿到图片（%.0fs）%s" % (dt, tail.replace("\n", " ")[:160])}

    try:
        im = Image.open(dst)
        size = "%dx%d" % im.size
        d = hamming(phash(Image.open(src).convert("RGB")), phash(im.convert("RGB")))
    except Exception as e:
        return {"ok": False, "src": src.name, "why": "打开失败 %s" % str(e)[:60]}

    return {
        "ok": True,
        "src": src.name,
        "dst": str(dst.relative_to(ROOT)),
        "size": size,
        "phash_dist": d,
        "enough": d >= 12,
        "secs": round(dt, 1),
        "kb": os.path.getsize(dst) // 1024,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", required=True)
    ap.add_argument("--preset", default="stone", choices=sorted(PRESETS))
    ap.add_argument("--timeout", type=int, default=300, help="单张超时秒数")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    codex = find_codex()
    if not codex:
        print("[XX] 找不到 codex.exe，设置环境变量 CODEX_EXE 指向它")
        return 1
    print("codex: %s" % codex)

    it = load_item(a.item)
    if not it:
        print("[XX] 找不到商品 %s" % a.item)
        return 1

    srcs = []
    for m in (it.main_images or []):
        lp = m.get("local_path") if isinstance(m, dict) else getattr(m, "local_path", None)
        if lp and os.path.isfile(lp):
            srcs.append(Path(lp))
    if a.limit:
        srcs = srcs[: a.limit]
    if not srcs:
        print("[XX] 没有本地主图（先跑 MediaPipeline 下载）")
        return 1

    out_dir = OUT / a.item / ("codex_" + a.preset)
    print("主图 %d 张，背景 %s，输出 %s" % (len(srcs), a.preset, out_dir))
    print("")

    if a.dry_run:
        print(build_prompt(srcs[0], a.preset, out_dir / "example.png"))
        return 0

    report = []
    for i, src in enumerate(srcs, 1):
        dst = out_dir / (src.stem + ".png")
        print("[%d/%d] %s ..." % (i, len(srcs), src.name), end="", flush=True)
        r = run_one(codex, src, a.preset, dst, a.timeout)
        report.append(r)
        if r["ok"]:
            print(" OK  %s  dist=%d  %ss  %dKB" % (r["size"], r["phash_dist"], r["secs"], r["kb"]))
        else:
            print(" [XX] %s" % r.get("why"))

    rep = out_dir / "report.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    with open(rep, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    ok = sum(1 for r in report if r.get("ok"))
    print("")
    print("=" * 60)
    print("成功 %d/%d，输出 %s" % (ok, len(report), out_dir))
    print("报告 %s" % rep)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
