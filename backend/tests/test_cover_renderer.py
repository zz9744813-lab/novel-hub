from pathlib import Path
import struct


def test_render_cover_creates_exact_320x480_png(tmp_path: Path):
    from app.cover_renderer import render_cover

    output = render_cover(tmp_path / "cover.png", title="测试书", genre="玄幻", logline="一个人在雾港寻找真相")
    assert output.exists()
    assert output.suffix == ".png"
    data = output.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (320, 480)
