"""视频采集验证：解析 + 真实下载。

用已抓到的真实视频样本（商品 1074903005877）验证整条链路，
不消耗 mtop 配额（视频走 CDN，不经过 mtop 限速）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.models import Image, Item, Video  # noqa: E402
from src.media.downloader import MediaPipeline, referer_for  # noqa: E402

SAMPLE = ROOT / "data/raw/taobao/video_samples.json"


def test_parse() -> None:
    print("[1] 从真实响应解析视频")
    if not SAMPLE.exists():
        print("  - 跳过（缺 video_samples.json）")
        return
    d = json.loads(SAMPLE.read_text(encoding="utf-8"))[0]
    raw = d["videos"][0]["raw"]
    v = Video(
        url=raw["url"],
        video_id=raw.get("videoId"),
        cover=raw.get("videoThumbnailURL"),
        dimension=raw.get("spatialVideoDimension"),
    )
    print(f"  ✓ url      = {v.url[:80]}")
    print(f"  ✓ video_id = {v.video_id}")
    print(f"  ✓ cover    = {(v.cover or '')[:70]}")
    print(f"  ✓ 画幅     = {v.dimension}")
    assert "cloud.video.taobao.com" in v.url
    assert v.video_id
    print()


def test_referer() -> None:
    print("[2] Referer 映射")
    cases = [
        ("https://cloud.video.taobao.com/play/u/1/p/2/e/6/t/1/123.mp4", "taobao"),
        ("https://img.alicdn.com/a.jpg", "taobao"),
        ("https://img14.360buyimg.com/n0/a.jpg", "jd"),
    ]
    for url, expect in cases:
        got = referer_for(url)
        ok = ("taobao" in got) if expect == "taobao" else ("jd" in got)
        print(f"  {'✓' if ok else '×'} {url[:55]:<57} -> {got}")
    print()


def test_download() -> None:
    print("[3] 真实下载视频")
    if not SAMPLE.exists():
        print("  - 跳过")
        return
    d = json.loads(SAMPLE.read_text(encoding="utf-8"))[0]
    raw = d["videos"][0]["raw"]
    video = Video(url=raw["url"], video_id=raw.get("videoId"),
                  cover=raw.get("videoThumbnailURL"),
                  dimension=raw.get("spatialVideoDimension"))

    item = Item(
        item_id=f"{d['item_id']}_video_test",
        platform="taobao",
        title=d["title"],
        category={"cid": ""},
        main_images=[Image(url=raw.get("videoThumbnailURL") or "")],
        videos=[video],
    )
    pipeline = MediaPipeline(item)
    print(f"  下载中… {video.url[:70]}")
    pipeline.run(with_detail=False, with_video=True)

    print(f"  local_path = {video.local_path}")
    print(f"  size       = {video.size_bytes:,} 字节" if video.size_bytes else "  size = ?")
    print(f"  sha256     = {(video.sha256 or '')[:32]}…")
    print(f"  stats      = {item.extra.get('media_stats')}")

    if video.local_path and Path(video.local_path).exists():
        size = Path(video.local_path).stat().st_size
        head = Path(video.local_path).read_bytes()[:12]
        is_mp4 = b"ftyp" in head
        print(f"  ✓ 文件存在 {size:,} 字节")
        print(f"  {'✓' if is_mp4 else '×'} MP4 文件头: {head!r}")
    else:
        print("  × 文件未生成，看 manifest 里的 error")
        m = ROOT / "data/media" / item.item_id / "manifest.json"
        if m.exists():
            for e in json.loads(m.read_text(encoding="utf-8")):
                if e.get("role") == "video":
                    print("    ", e.get("error"))
    print()


def main() -> int:
    test_parse()
    test_referer()
    test_download()
    return 0


if __name__ == "__main__":
    sys.exit(main())
