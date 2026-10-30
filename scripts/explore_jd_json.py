"""从京东移动端页面提取内嵌 JSON 变量并打印结构（括号计数法截取）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

FILE = Path("data/raw/jd/100008348542_M.html")
if len(sys.argv) > 1:
    FILE = Path(sys.argv[1])


def extract_js_object(text: str, var: str) -> dict | None:
    """截取 `var = ({...})` 或 `var = {...}`，用括号配平，跳过字符串内的括号。"""
    m = re.search(rf"(?:window\.)?{re.escape(var)}\s*=\s*\(?\s*\{{", text)
    if not m:
        return None
    start = text.index("{", m.start())
    depth, i, in_str, quote, esc = 0, start, False, "", False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        else:
            if ch in "\"'":
                in_str, quote = True, ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw = text[start:i + 1]
                    try:
                        return json.loads(raw)
                    except ValueError as e:
                        print(f"  ! {var} json 解析失败: {e}")
                        return {"_raw_head": raw[:300]}
        i += 1
    return None


def walk(node, prefix: str = "", depth: int = 0, max_depth: int = 2) -> None:
    if depth > max_depth:
        return
    if isinstance(node, dict):
        for k, v in list(node.items())[:40]:
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                n = len(v)
                print(f"  {path}  <{type(v).__name__} len={n}>")
                if isinstance(v, dict) and depth < max_depth:
                    walk(v, path, depth + 1, max_depth)
                elif isinstance(v, list) and v and isinstance(v[0], dict) and depth < max_depth:
                    print(f"      [0] keys: {list(v[0].keys())[:14]}")
                    walk(v[0], path + "[0]", depth + 1, max_depth)
            else:
                s = str(v).replace("\n", " ")
                print(f"  {path} = {s[:80]}")


def main() -> int:
    text = FILE.read_text(encoding="utf-8", errors="ignore")
    out_dir = Path("data/raw/jd")
    for var in ("_itemOnly", "_itemGlobal", "_itemInfo", "skuPro", "propMap"):
        print(f"\n===== {var} =====")
        obj = extract_js_object(text, var)
        if obj is None:
            print("  (未找到)")
            continue
        (out_dir / f"{FILE.stem}_{var.strip('_')}.json").write_text(
            json.dumps(obj, ensure_ascii=False, indent=1)[:400000], encoding="utf-8")
        walk(obj, max_depth=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
