"""验证 CategoryApi：搜索 / 子级 / 反查。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.publish.category_api import CategoryApi, CategoryApiError  # noqa: E402


def main():
    with CategoryApi() as api:
        print("=" * 70)
        print("1. 搜索「键帽」")
        print("=" * 70)
        for c in api.search("键帽"):
            print("  id=%s leaf=%s publish=%s" % (c["id"], c["leaf"], c["publish"]))
            print("    %s" % " > ".join(c["path"]))
            print("    idpath=%s" % c["idpath"])

        print()
        print("=" * 70)
        print("2. 搜索「机械键盘」")
        print("=" * 70)
        for c in api.search("机械键盘"):
            print("  id=%s leaf=%s  %s" % (c["id"], c["leaf"], " > ".join(c["path"])))
            print("    idpath=%s" % c["idpath"])

        print()
        print("=" * 70)
        print("3. 一级类目（前 10）")
        print("=" * 70)
        tops = api.children(None)
        print("  共 %d 个" % len(tops))
        for c in tops[:10]:
            print("  id=%-10s leaf=%-5s %s" % (c["id"], c["leaf"], c["name"]))

        print()
        print("=" * 70)
        print("4. 反查 200670001（键帽）")
        print("=" * 70)
        info = api.resolve(200670001)
        print("  %s" % info)

        print()
        print("=" * 70)
        print("5. 子树展开 50008090（3C 数码配件，深度 2，上限 60）")
        print("=" * 70)
        nodes = api.walk(50008090, max_depth=2, limit=60)
        print("  展开 %d 个" % len(nodes))
        for n in nodes[:20]:
            print("  %s%s id=%-12s %s"
                  % ("  " * n.get("depth", 1), "·" if n["leaf"] else "▸",
                     n["id"], n["name"][:30]))


if __name__ == "__main__":
    try:
        main()
    except CategoryApiError as e:
        print("[CategoryApiError] %s" % e.message)
        sys.exit(1)
