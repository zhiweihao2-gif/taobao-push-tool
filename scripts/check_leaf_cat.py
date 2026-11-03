"""检查采集数据里有没有类目 ID（leafCategory / rootCategory）。

用法:
    python scripts/check_leaf_cat.py
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAT_LEAF = re.compile(r'"leafCategory\\*":\\*"(\d+)\\*"')
PAT_ROOT = re.compile(r'"rootCategory\\*":\\*"(\d+)\\*"')


def scan_text(s):
    return set(PAT_LEAF.findall(s)), set(PAT_ROOT.findall(s))


def main():
    print("=" * 70)
    print("扫描 data/raw/taobao/*.json")
    print("=" * 70)
    any_hit = False
    for f in sorted(glob.glob(r"data/raw/taobao/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print("  [跳过] %s: %s" % (os.path.basename(f), str(e)[:40]))
            continue
        s = json.dumps(d, ensure_ascii=False)
        leaf, root = scan_text(s)
        tag = ""
        if leaf or root:
            any_hit = True
            tag = "  <== 命中"
        print("%-42s leaf=%-22s root=%s%s"
              % (os.path.basename(f)[:42], sorted(leaf) or "-", sorted(root) or "-", tag))

    print()
    print("=" * 70)
    print("扫描 data/items/*.json 里的 category")
    print("=" * 70)
    for f in sorted(glob.glob(r"data/items/*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        cat = d.get("category") or {}
        if not isinstance(cat, dict):
            cat = {}
        raw = d.get("raw") or {}
        leaf = ""
        if isinstance(raw, dict):
            payload = raw.get("payload") or {}
            if isinstance(payload, dict):
                leaf = str(payload.get("item", {}).get("leafCategory") or "")
        print("%-30s cid=%-16s path=%-20s rawLeaf=%s"
              % (os.path.basename(f)[:30], cat.get("cid"), cat.get("path"), leaf or "-"))

    print()
    print("=" * 70)
    print("扫描 data/publish_poc/*.html 里的 leafCategory")
    print("=" * 70)
    for f in sorted(glob.glob(r"data/publish_poc/dom_*.html")):
        try:
            html = open(f, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        leaf, root = scan_text(html)
        print("%-46s leaf=%-22s root=%s"
              % (os.path.basename(f)[:46], sorted(leaf) or "-", sorted(root) or "-"))

    if not any_hit:
        print()
        print("[!] raw JSON 里没找到 leafCategory —— 说明 mtop 接口不返回，只有 HTML 里有")


if __name__ == "__main__":
    main()
