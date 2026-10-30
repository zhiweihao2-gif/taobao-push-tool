"""检查商品是否有主图视频，并抓出视频 URL。

从 data/raw/taobao/video_candidates.json 里取商品 ID，用 pcdetail 逐个查 videos 字段。
限速器会自动控制间隔（淘宝 3 秒/次）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.collect.mtop import MTopClient, MTopError  # noqa: E402
from src.collect.ratelimit import CooldownActive  # noqa: E402
from src.collect.session import SessionStore  # noqa: E402

CAND = ROOT / "data/raw/taobao/video_candidates.json"
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def dig_videos(data: dict) -> list[dict]:
    """从 pcdetail 返回里挖所有可能是视频的结构。"""
    hits: list[dict] = []
    item = data.get("item") or {}
    for key in ("videos", "video", "videoList", "mediaList"):
        v = item.get(key)
        if isinstance(v, list) and v:
            hits += [{"from": f"item.{key}", "raw": x} for x in v]
        elif isinstance(v, dict) and v:
            hits.append({"from": f"item.{key}", "raw": v})

    comp = data.get("componentsVO") or {}
    for name, node in comp.items():
        s = json.dumps(node, ensure_ascii=False)
        if '"video' in s or "mp4" in s or "Video" in s:
            hits.append({"from": f"componentsVO.{name}", "raw": node})
    return hits


def main() -> int:
    if not CAND.exists():
        print("先跑 scripts/find_video_item2.py")
        return 1
    items = json.loads(CAND.read_text(encoding="utf-8"))
    ids = [i["id"] for i in items][:LIMIT]
    print(f"检查 {len(ids)} 个商品（限速 3 秒/次）\n")

    cookies = SessionStore().load("taobao")
    client = MTopClient(site="taobao", cookies=cookies)
    found: list[dict] = []

    for idx, item_id in enumerate(ids, 1):
        try:
            resp = client.call("mtop.taobao.pcdetail.data.get", "1.0",
                               {"id": item_id, "detail_v": "3.3.2", "exParams": "{}"})
            data = resp.get("data") or {}
            title = (data.get("item") or {}).get("title", "")[:40]
            videos = dig_videos(data)
            mark = "★有视频" if videos else "  "
            print(f"[{idx}/{len(ids)}] {item_id}  {mark}  {title}")
            if videos:
                found.append({"item_id": item_id, "title": title, "videos": videos})
                for v in videos:
                    print(f"      {v['from']}: {json.dumps(v['raw'], ensure_ascii=False)[:200]}")
        except CooldownActive as e:
            print(f"\n× 触发限速，停止：{e}")
            break
        except MTopError as e:
            print(f"[{idx}/{len(ids)}] {item_id}  ERR {e.code}")
        except Exception as e:
            print(f"[{idx}/{len(ids)}] {item_id}  ERR {type(e).__name__}: {str(e)[:60]}")

    if found:
        out = ROOT / "data/raw/taobao/video_samples.json"
        out.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n找到 {len(found)} 个带视频的商品 → {out}")
    else:
        print("\n这批商品都没有主图视频")
    return 0


if __name__ == "__main__":
    sys.exit(main())
