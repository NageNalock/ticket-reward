#!/usr/bin/env python3
"""Remove non-rendering metadata from project PNGs and generated macOS icons."""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_RENDER_CHUNKS = {
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS",
    b"sRGB", b"gAMA", b"cHRM", b"cICP",
}


def strip_png(data: bytes) -> bytes:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Not a PNG image")
    output = bytearray(PNG_SIGNATURE)
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        end = offset + size + 12
        if end > len(data):
            raise ValueError("Truncated PNG chunk")
        kind = data[offset + 4:offset + 8]
        checksum = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(data[offset + 4:end - 4]) != checksum:
            raise ValueError("Invalid PNG chunk checksum")
        if kind in PNG_RENDER_CHUNKS:
            # Preserve the encoded pixels and transparency byte for byte.
            output.extend(data[offset:end])
        elif not kind[0] & 32:
            raise ValueError("Unsupported critical PNG chunk")
        offset = end
        if kind == b"IEND":
            return bytes(output)
    raise ValueError("Missing PNG end marker")


def strip_icns(data: bytes) -> bytes:
    if data[:4] != b"icns" or struct.unpack_from(">I", data, 4)[0] != len(data):
        raise ValueError("Invalid ICNS container")
    output = bytearray()
    offset = 8
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        size = struct.unpack_from(">I", data, offset + 4)[0]
        if size < 8 or offset + size > len(data):
            raise ValueError("Invalid ICNS entry")
        payload = data[offset + 8:offset + size]
        offset += size
        if kind in (b"info", b"TOC "):
            continue
        if payload.startswith(PNG_SIGNATURE):
            payload = strip_png(payload)
        elif kind not in (b"ic04", b"ic05"):
            raise ValueError("Unsupported ICNS image representation")
        output.extend(kind + struct.pack(">I", len(payload) + 8) + payload)
    if offset != len(data):
        raise ValueError("Unexpected trailing ICNS data")
    return b"icns" + struct.pack(">I", len(output) + 8) + output


def main() -> None:
    for name in sys.argv[1:]:
        path = Path(name)
        data = path.read_bytes()
        if path.suffix.lower() == ".png":
            cleaned = strip_png(data)
        elif path.suffix.lower() == ".icns":
            cleaned = strip_icns(data)
        else:
            raise ValueError("Only PNG and ICNS assets are supported")
        if cleaned != data:
            path.write_bytes(cleaned)
        print(f"{path.name}: removed {len(data) - len(cleaned)} bytes of metadata")


if __name__ == "__main__":
    main()
