"""回転レコード（PRD 9.5）: 直径 200px、5° 刻み 72 フレームを曲切替時に事前生成する。

ジャケットを円のほぼ全面に出し、細い黒縁にマゼンタの弧を置いて回転を見せる。
"""

from __future__ import annotations

import hashlib

from PIL import Image, ImageDraw

from .fb import to_rgb565
from .theme import BG, CYAN, MAGENTA, MUTED

DIAMETER = 200
RIM = 10  # 黒縁の幅（片側）
COVER_DIAMETER = DIAMETER - 2 * RIM
STEP_DEG = 5
FRAMES = 360 // STEP_DEG


def pattern_cover(seed: str, size: int = COVER_DIAMETER) -> Image.Image:
    """ジャケットが無い曲用。シードから決定論的に生成する幾何模様。"""
    h = hashlib.sha256(seed.encode()).digest()
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    palette = [CYAN, MAGENTA, MUTED]
    cells = 4
    cell = size // cells
    for i in range(cells * cells):
        b = h[i]
        x, y = (i % cells) * cell, (i // cells) * cell
        color = palette[b % 3]
        kind = (b >> 2) % 3
        if kind == 0:
            d.rectangle([x + 3, y + 3, x + cell - 4, y + cell - 4], outline=color, width=2)
        elif kind == 1:
            d.ellipse([x + 4, y + 4, x + cell - 5, y + cell - 5], outline=color, width=2)
        else:
            d.line([x + 3, y + cell - 4, x + cell - 4, y + 3], fill=color, width=2)
    return img


def _circle_mask(size: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size - 1, size - 1], fill=255)
    return m


def record_disc(cover: Image.Image | None, seed: str) -> Image.Image:
    """回転 0° の円盤（背景色で塗った正方形）。"""
    img = Image.new("RGB", (DIAMETER, DIAMETER), BG)
    d = ImageDraw.Draw(img)
    c = DIAMETER // 2
    d.ellipse([0, 0, DIAMETER - 1, DIAMETER - 1], fill=(0x10, 0x15, 0x1B), outline=CYAN, width=2)
    art = (cover or pattern_cover(seed)).convert("RGB").resize((COVER_DIAMETER, COVER_DIAMETER))
    img.paste(art, (RIM, RIM), _circle_mask(COVER_DIAMETER))
    r = c - RIM // 2  # 黒縁の中央
    d.arc([c - r, c - r, c + r, c + r], start=-60, end=-15, fill=MAGENTA, width=3)  # 回転が見える弧
    d.ellipse([c - 5, c - 5, c + 5, c + 5], fill=BG, outline=(0x10, 0x15, 0x1B), width=2)  # センターホール
    return img


class RecordFrames:
    def __init__(self, frames: list[bytes]):
        self.frames = frames
        self.index = 0

    @classmethod
    def build(cls, cover: Image.Image | None, seed: str) -> RecordFrames:
        disc = record_disc(cover, seed)
        mask = _circle_mask(DIAMETER)
        bg = Image.new("RGB", (DIAMETER, DIAMETER), BG)
        frames = []
        for i in range(FRAMES):
            rot = disc.rotate(-i * STEP_DEG, resample=Image.Resampling.BICUBIC, fillcolor=BG)
            frames.append(to_rgb565(Image.composite(rot, bg, mask)))
        return cls(frames)

    def next(self) -> bytes:
        f = self.frames[self.index]
        self.index = (self.index + 1) % len(self.frames)
        return f

    @property
    def current(self) -> bytes:
        return self.frames[self.index]

    @property
    def shown(self) -> bytes:
        """直前に next() で返したフレーム（いま画面に出ている回転角）。"""
        return self.frames[(self.index - 1) % len(self.frames)]
