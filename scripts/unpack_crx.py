"""解析 Chrome CRX3 扩展包，提取内部 ZIP 并解压。

CRX3 结构：
    "Cr24" | version=3 (u32 LE) | header_len (u32 LE) | header (protobuf) | ZIP
"""
from __future__ import annotations

import struct
import sys
import zipfile
from pathlib import Path

CRX = Path(r"C:\Users\hao\Downloads\店透视-Chrome_5.0.6.crx")
OUT_DIR = Path(__file__).resolve().parents[1] / "analysis" / "dianshiyan"


def parse_crx(data: bytes) -> tuple[int, int, bytes]:
    if data[:4] != b"Cr24":
        raise ValueError("不是 CRX 文件")
    version = struct.unpack("<I", data[4:8])[0]
    if version == 3:
        header_len = struct.unpack("<I", data[8:12])[0]
        header = data[12:12 + header_len]
        zip_start = 12 + header_len
    elif version == 2:
        pk_len = struct.unpack("<I", data[8:12])[0]
        sig_len = struct.unpack("<I", data[12:16])[0]
        header = data[16:16 + pk_len]
        zip_start = 16 + pk_len + sig_len
    else:
        raise ValueError(f"未知 CRX 版本 {version}")
    return version, zip_start, header


def main() -> int:
    data = CRX.read_bytes()
    version, zip_start, header = parse_crx(data)
    print(f"文件: {CRX.name}  {len(data):,} 字节")
    print(f"CRX 版本: {version}")
    print(f"ZIP 起始偏移: {zip_start}")
    print(f"头部长度: {len(header)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUT_DIR.parent / "extension.zip"
    zip_path.write_bytes(data[zip_start:])
    print(f"已提取 ZIP → {zip_path}")

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        print(f"\n包内文件数: {len(names)}")
        total = sum(i.file_size for i in z.infolist())
        print(f"解压后总大小: {total:,} 字节")

        print("\n=== 按大小排前 30 ===")
        for info in sorted(z.infolist(), key=lambda i: -i.file_size)[:30]:
            print(f"  {info.file_size:>9,}  {info.filename}")

        z.extractall(OUT_DIR)
        print(f"\n已解压到 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
