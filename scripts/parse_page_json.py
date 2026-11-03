"""从已保存的商品页 HTML 里抠出内嵌的 JSON 商品数据。

天猫/淘宝 PC 详情页虽然调 mtop 接口，但接口返回的是容器配置，
真正的商品数据是**服务端直出**写进 HTML 的一个大 JSON 里的，
里面同时有 `basicParamList`（参数表）和 `skuCore.sku2info`（SKU 价格/库存）。

这个脚本不联网，只解析本地 HTML。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_script_blocks(html: str) -> list[str]:
    """粗略切出所有 <script>...</script> 的内容。"""
    out = []
    i = 0
    while True:
        a = html.find("<script", i)
        if a < 0:
            break
        gt = html.find(">", a)
        if gt < 0:
            break
        b = html.find("</script>", gt)
        if b < 0:
            break
        out.append(html[gt + 1:b])
        i = b + 9
    return out


def balanced_json(text: str, start: int) -> str | None:
    """从 text[start] 这个 '{' 或 '[' 开始，配平括号取出完整 JSON 文本。"""
    if start >= len(text) or text[start] not in "{[":
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_obj(html: str, marker: str) -> dict | None:
    """找到含 marker 的最大那个可解析 JSON 对象。"""
    best = None
    for blk in find_script_blocks(html):
        if marker not in blk:
            continue
        # 从 marker 往前找一个 '{'，逐步左移尝试
        idx = blk.find(marker)
        probe = idx
        tries = 0
        while probe > 0 and tries < 4000:
            probe = blk.rfind("{", 0, probe)
            if probe < 0:
                break
            tries += 1
            raw = balanced_json(blk, probe)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if isinstance(obj, dict) and marker in raw:
                if best is None or len(raw) > len(best[0]):
                    best = (raw, obj)
                break
    return best[1] if best else None


def dig(obj, *path, default=None):
    cur = obj
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and -len(cur) <= k < len(cur):
            cur = cur[k]
        else:
            return default
        if cur is None:
            return default
    return cur


def summarize(obj: dict) -> None:
    item = obj.get("item") or {}
    print("标题:", str(item.get("title"))[:70])
    print("图片:", len(item.get("images") or []), "张")
    # 类目：淘宝商品页直出的字段名是 leafCategory / rootCategory（不是 categoryId！）
    leaf = item.get("leafCategory") or obj.get("leafCategory")
    root = item.get("rootCategory") or obj.get("rootCategory")
    print("类目 leafCategory:", leaf, " rootCategory:", root)
    if not leaf:
        print("  [!] 没有 leafCategory —— 天猫页或老结构，需另找")

    # 参数：componentsVO.extensionInfoVO.infos[].items[] 或 basicParamList
    params: list[tuple[str, str]] = []
    ext = dig(obj, "componentsVO", "extensionInfoVO", default={}) or {}
    for grp in (ext.get("infos") or []):
        for it in (grp.get("items") or []):
            t, v = it.get("title"), it.get("text")
            if t and v:
                params.append((str(t), str(v) if not isinstance(v, list) else ",".join(map(str, v))))

    # 直出的 basicParamList / paramList
    for key in ("basicParamList", "paramList", "props"):
        lst = obj.get(key)
        if isinstance(lst, list):
            for it in lst:
                if isinstance(it, dict):
                    n = it.get("propertyName") or it.get("name")
                    v = it.get("valueName") or it.get("value")
                    if n and v:
                        params.append((str(n), str(v)))

    # 全局搜索（basicParamList 可能嵌在别的层）
    def walk(o, depth=0):
        if depth > 8:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("basicParamList", "paramList") and isinstance(v, list):
                    for it in v:
                        if isinstance(it, dict):
                            n = it.get("propertyName") or it.get("name")
                            val = it.get("valueName") or it.get("value")
                            if n and val:
                                params.append((str(n), str(val)))
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for it in o[:50]:
                walk(it, depth + 1)

    walk(obj)
    seen, uniq = set(), []
    for n, v in params:
        key = (n, v)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((n, v))
    print("\n参数 (%d 项):" % len(uniq))
    for n, v in uniq[:40]:
        print("   %-18s %s" % (n, v[:60]))

    # SKU
    sku2 = dig(obj, "skuCore", "sku2info", default={}) or {}
    sku_base = obj.get("skuBase") or (dig(obj, "skuBase", default={}) or {})
    props = sku_base.get("props") or []
    skus = sku_base.get("skus") or []
    print("\nSKU 维度 (%d):" % len(props))
    for p in props:
        vals = [v.get("name") for v in (p.get("values") or [])]
        print("   pid=%-10s %-12s %s" % (p.get("pid"), p.get("name"), vals[:8]))
    print("\nSKU 明细 (%d):" % len(skus))
    for s in skus[:12]:
        sid = str(s.get("skuId"))
        info = sku2.get(sid) or {}
        price = dig(info, "price", "priceText", default="?")
        qty = info.get("quantity", "?")
        print("   skuId=%-18s propPath=%-14s 价格=%-8s 库存=%s"
              % (sid, s.get("propPath"), price, qty))
    if not skus and sku2:
        d = sku2.get("0") or {}
        print("   [单规格] 价格=%s 库存=%s"
              % (dig(d, "price", "priceText", default="?"), d.get("quantity", "?")))


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/parse_page_json.py <html文件>")
        return 2
    p = Path(sys.argv[1])
    if not p.exists():
        print("[!] 文件不存在: %s" % p)
        return 1
    html = p.read_text(encoding="utf-8", errors="ignore")
    print("HTML %d 字节" % len(html))

    obj = extract_obj(html, "skuCore")
    if not obj:
        print("[!] 没找到含 skuCore 的 JSON")
        return 1
    print("抠到 JSON %d 字节" % len(json.dumps(obj, ensure_ascii=False)))
    summarize(obj)

    out = p.with_suffix(".extracted.json")
    out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n-> %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
