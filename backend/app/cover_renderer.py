"""Dependency-free deterministic 320x480 PNG book-cover renderer."""
from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _rgb(seed: bytes, offset: int) -> tuple[int, int, int]:
    return tuple(seed[(offset + i) % len(seed)] for i in range(3))  # type: ignore[return-value]


def render_cover(path: str | Path, *, title: str = "", genre: str = "", logline: str = "", width: int = 320, height: int = 480) -> Path:
    """Render a real PNG, not a path placeholder. Output dimensions are exact."""
    if (width, height) not in {(320, 480), (160, 240)}:
        raise ValueError("cover dimensions must be 320x480 or 160x240")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = hashlib.sha256(f"{title}|{genre}|{logline}".encode("utf-8")).digest()
    c1, c2, c3 = _rgb(seed, 0), _rgb(seed, 7), _rgb(seed, 14)
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray([0])
        t = y / max(1, height - 1)
        for x in range(width):
            u = x / max(1, width - 1)
            # Three-colour diagonal gradient with a subtle vignette.
            if t + u < 0.9:
                a, b, q = c1, c2, (t + u) / 0.9
            else:
                a, b, q = c2, c3, min(1.0, (t + u - 0.9) / 1.1)
            vignette = max(0.72, 1.0 - 0.28 * (((u - .5) ** 2 + (t - .5) ** 2) ** .5) * 2)
            row.extend(
                int(max(0, min(255, (a[i] * (1 - q) + b[i] * q) * vignette)))
                for i in range(3)
            )
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _chunk(b"IDAT", zlib.compress(raw, 9))
    png += _chunk(b"IEND", b"")
    path.write_bytes(png)
    return path
