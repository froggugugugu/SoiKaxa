"""LCD のデザイントークン（PRD 9.5）とフォント解決。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

WIDTH, HEIGHT = 480, 320

BG = (0x0B, 0x0F, 0x14)
CYAN = (0x5D, 0xCA, 0xA5)
MAGENTA = (0xED, 0x93, 0xB1)
FG = (0xC9, 0xD1, 0xD9)
MUTED = (0x7D, 0x85, 0x90)
GROOVE = (0x1C, 0x24, 0x2C)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# 和文は等幅 CJK を優先。無ければ欧文等幅 → 既定フォント
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansMonoCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def find_font_path() -> str | None:
    env = os.environ.get("SOIKAXA_FONT")
    if env and Path(env).is_file():
        return env
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            return p
    return None


@lru_cache(maxsize=16)
def font(size: int) -> Font:
    path = find_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)
