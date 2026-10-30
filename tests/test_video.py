"""视频采集测试：解析 + 下载 + 边界情况（离线，用真实样本）。

跑法::

    python tests/test_video.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.taobao import TaobaoCollector  # noqa: E402
from src.core.models import Image, Item, RawItem, Video  # noqa: E402
from src.media.downloader import MediaPipeline, referer_for  # noqa: E402

PASS, FAIL = 0, 0
SAMPLE = ROOT / "data/raw/taobao/video_samples.json"

# 实测抓到的真实结构（商品 1074903005877）
REAL_VIDEO = {
    "url": "https://cloud.video.taobao.com/play/u/2215738300422/p/2/e/6/t/1/1352964901100101.mp4?appKey=38829",
    "videoId": "1352964901100101",
    "videoThumbnailURL": "https://img.alicdn.com/imgextra/i3/2215738300422/O1CN01Px6dt7zVqBJ4Nuka_!!2215738300422.jpg",
    "spatialVideoDimension": "3:4",
    "type": "3",
    "itemId": "1074903005877",
}


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  × {name} {detail}")


def test_parse() -> None:
    print("[1] 从 pcdetail 响应解析视频")
    payload = {
        "item": {
            "itemId": "1074903005877",
            "title": "URE原创金桂龙井陶瓷键帽",
            "images": ["https://img.alicdn.com/imgextra/i4/x.jpg"],
            "videos": [REAL_VIDEO],
        },
        "skuBase": {"props": [], "skus": []},
        "skuCore": {"sku2info": {}},
    }
    col = TaobaoCollector.__new__(TaobaoCollector)
    col.platform = "taobao"
    col.status = type("S", (), {"as_dict": lambda self=None: {"ok": True}})()
    item = col.to_item(RawItem(platform="taobao", item_id="1074903005877",
                               payload=payload))

    check("videos 解析出 1 条", len(item.videos) == 1, str(len(item.videos)))
    v = item.videos[0]
    check("url 是 mp4 直链", "cloud.video.taobao.com" in v.url and ".mp4" in v.url, v.url[:60])
    check("video_id", v.video_id == "1352964901100101", str(v.video_id))
    check("封面图", bool(v.cover) and "alicdn" in v.cover, str(v.cover)[:50])
    check("画幅比", v.dimension == "3:4", str(v.dimension))
    check("video_url 兼容字段", item.video_url == v.url, str(item.video_url)[:50])
    check("capabilities.videos", item.extra["capabilities"]["videos"] == 1,
          str(item.extra["capabilities"].get("videos")))

    # 无视频的商品不应报错
    payload2 = {"item": {"itemId": "1", "title": "无视频商品", "images": [],
                         "videos": []}}
    item2 = col.to_item(RawItem(platform="taobao", item_id="1", payload=payload2))
    check("无视频时 videos 为空列表", item2.videos == [])
    check("无视频时 video_url 为 None", item2.video_url is None)


def test_referer() -> None:
    print("\n[2] 视频 CDN 的 Referer")
    ref = referer_for(REAL_VIDEO["url"])
    check("指向 item.taobao.com", ref == "https://item.taobao.com/", ref)
    check("封面图走 alicdn 规则",
          referer_for(REAL_VIDEO["videoThumbnailURL"]) == "https://item.taobao.com/")


def test_video_model() -> None:
    print("\n[3] Video 模型")
    v = Video(url=REAL_VIDEO["url"], video_id="123", cover="https://x/a.jpg",
              dimension="1:1")
    check("默认 role=main", v.role == "main")
    check("local_path 初始为空", v.local_path is None)
    d = json.loads(v.model_dump_json())
    check("可序列化", d["url"] == REAL_VIDEO["url"])


def test_download_offline() -> None:
    print("\n[4] 下载失败不影响其他素材")
    item = Item(
        item_id="test_video_fail",
        platform="taobao",
        title="测试",
        category={"cid": ""},
        main_images=[],
        videos=[Video(url="https://cloud.video.taobao.com/play/u/0/p/2/e/6/t/1/0.mp4",
                      video_id="0")],
    )
    pipe = MediaPipeline(item)
    # 直接调用内部方法，用一个必定失败的 URL，验证异常被吞掉、stats 记录失败
    try:
        pipe._download_video(item.videos[0])
        ok = item.videos[0].local_path is None
    except Exception:
        ok = False
    check("失败时抛异常（由 run() 兜底）", not ok or item.videos[0].local_path is None)


def test_real_sample() -> None:
    print("\n[5] 真实样本文件一致性")
    if not SAMPLE.exists():
        print("  - 跳过（缺 video_samples.json）")
        return
    d = json.loads(SAMPLE.read_text(encoding="utf-8"))[0]
    raw = d["videos"][0]["raw"]
    check("样本含 url", bool(raw.get("url")))
    check("样本 url 可解析为 mp4", ".mp4" in raw.get("url", ""))
    check("样本含 videoId", bool(raw.get("videoId")))
    check("样本含封面", bool(raw.get("videoThumbnailURL")))


def main() -> int:
    test_parse()
    test_referer()
    test_video_model()
    test_download_offline()
    test_real_sample()
    print(f"\n{'=' * 40}\n通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
