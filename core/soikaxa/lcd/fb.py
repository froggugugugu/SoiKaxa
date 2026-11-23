"""fbtft（/dev/fb1, RGB565 リトルエンディアン）への書き込み。"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageChops

from .theme import HEIGHT, WIDTH


def to_rgb565(img: Image.Image) -> bytes:
    """RGB → RGB565 LE。numpy 非依存（Pillow のチャンネル演算のみ）。"""
    r, g, b = img.convert("RGB").split()
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: (v & 0x1C) << 3), b.point(lambda v: v >> 3))
    return Image.merge("LA", (lo, hi)).tobytes()


def find_lcd_fb(sys_root: str | Path = "/sys/class/graphics") -> str:
    """480×320 のフレームバッファを探す。HDMI の有無で fb0/fb1 が入れ替わるため。無ければ /dev/fb1。"""
    root = Path(sys_root)
    for d in sorted(root.glob("fb[0-9]*")) if root.is_dir() else []:
        try:
            size = (d / "virtual_size").read_text().strip()
        except OSError:
            continue
        if size == f"{WIDTH},{HEIGHT}":
            return f"/dev/{d.name}"
    return "/dev/fb1"


class Framebuffer:
    def __init__(self, path: str | Path = "/dev/fb1", width: int = WIDTH, height: int = HEIGHT):
        self.path = Path(path)
        self.width, self.height = width, height

    @property
    def available(self) -> bool:
        return self.path.exists()

    def write(self, img: Image.Image, xy: tuple[int, int] = (0, 0)) -> None:
        self.write_raw(to_rgb565(img), xy, img.size)

    def write_raw(self, data: bytes, xy: tuple[int, int], size: tuple[int, int]) -> None:
        x, y = xy
        w, h = size
        if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
            raise ValueError("region out of framebuffer")
        stride, row = self.width * 2, w * 2
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o644)
        try:
            if x == 0 and w == self.width:
                os.pwrite(fd, data, y * stride)
                return
            view = memoryview(data)
            for i in range(h):
                os.pwrite(fd, view[i * row : (i + 1) * row], (y + i) * stride + x * 2)
        finally:
            os.close(fd)
