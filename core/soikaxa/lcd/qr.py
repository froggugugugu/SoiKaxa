"""QR 画像（白地に黒、読み取り性優先。ECC M、クワイエットゾーン 2 モジュール）。"""

from __future__ import annotations

from functools import lru_cache

import qrcode
from PIL import Image
from qrcode.constants import ERROR_CORRECT_M


@lru_cache(maxsize=8)
def make_qr(payload: str, size: int) -> Image.Image:
    """戻り値はキャッシュを共有する。呼び出し側で書き換えない（paste の元にするだけ）。"""
    q = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2, box_size=1)
    q.add_data(payload)
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
    # 整数倍で拡大してモジュール境界をにじませない
    scale = max(1, size // img.width)
    img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (size, size), "white")
    off = (size - img.width) // 2
    canvas.paste(img, (off, off))
    return canvas
