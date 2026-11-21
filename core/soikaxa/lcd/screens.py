"""状態別の LCD 画面（480×320、PRD 9.4）。すべて純粋関数で PIL Image を返す。"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from .qr import make_qr
from .record import DIAMETER, record_disc
from .theme import BG, CYAN, FG, HEIGHT, MAGENTA, MUTED, WIDTH, Font, font

RECORD_XY = (20, (HEIGHT - DIAMETER) // 2)


@dataclass(frozen=True)
class NowPlayingView:
    title: str
    artist: str = ""
    position: float | None = None
    duration: float | None = None
    next_title: str | None = None
    next_added_by: str = ""
    guest_qr: str = ""
    paused: bool = False
    notice: str = ""  # 例: 「音源側の変更で停止中」


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    return img, ImageDraw.Draw(img)


def fit_text(text: str, f: Font, max_width: int) -> str:
    """幅に収まらなければ末尾を「…」で切る。"""
    if f.getlength(text) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.getlength(text[:mid] + ell) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def _fmt(sec: float | None) -> str:
    if sec is None:
        return "--:--"
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


def render_boot(progress: float) -> Image.Image:
    img, d = _canvas()
    d.text((WIDTH // 2, 130), "起動中…", font=font(32), fill=FG, anchor="mm")
    p = max(0.0, min(1.0, progress))
    x0, x1, y = 90, WIDTH - 90, 190
    d.rectangle([x0, y, x1, y + 8], outline=MUTED)
    d.rectangle([x0, y, x0 + int((x1 - x0) * p), y + 8], fill=CYAN)
    d.text((WIDTH // 2, 290), "SOIKAXA", font=font(16), fill=MUTED, anchor="mm")
    return img


def _draw_code(d: ImageDraw.ImageDraw, xy: tuple[int, int], code: str | None) -> None:
    """PIN 未設定のときだけ出す設定コード（本体の前にいる人だけが読める）。"""
    if not code:
        return
    x, y = xy
    d.text((x, y), "設定コード", font=font(14), fill=MUTED)
    d.text((x, y + 18), code, font=font(30), fill=MAGENTA)


def render_setup(qr_payload: str, url: str, code: str | None = None) -> Image.Image:
    """上流未接続: 左半分に大きく設定用 QR。"""
    img, d = _canvas()
    img.paste(make_qr(qr_payload, 220), (10, 50))
    x, w = 250, WIDTH - 260
    d.text((x, 60), "スマホでこの", font=font(24), fill=FG)
    d.text((x, 92), "QR を読んで", font=font(24), fill=FG)
    d.text((x, 124), "ください", font=font(24), fill=FG)
    d.text((x, 190), "開かないときは", font=font(16), fill=MUTED)
    d.text((x, 214), fit_text(url, font(18), w), font=font(18), fill=CYAN)
    _draw_code(d, (x, 248), code)
    return img


def render_now_playing(view: NowPlayingView, disc: Image.Image | None = None) -> Image.Image:
    img, d = _canvas()
    img.paste(disc or record_disc(None, view.title), RECORD_XY)
    x, w = 240, WIDTH - 250
    d.text((x, 20), fit_text(view.title or "—", font(24), w), font=font(24), fill=FG)
    d.text((x, 54), fit_text(view.artist, font(18), w), font=font(18), fill=MUTED)
    # 進捗
    y = 88
    d.rectangle([x, y, x + w, y + 4], fill=(0x1C, 0x24, 0x2C))
    if view.position is not None and view.duration:
        ratio = max(0.0, min(1.0, view.position / view.duration))
        d.rectangle([x, y, x + int(w * ratio), y + 4], fill=CYAN)
    status = "II" if view.paused else ""
    d.text((x, 98), f"{_fmt(view.position)} / {_fmt(view.duration)} {status}", font=font(14), fill=MUTED)
    # 次の曲
    d.text((x, 124), "次:", font=font(16), fill=MAGENTA)
    nxt = view.next_title or "—"
    if view.next_added_by:
        nxt = f"{nxt}（{view.next_added_by}）"
    d.text((x + 30, 124), fit_text(nxt, font(16), w - 30), font=font(16), fill=FG)
    if view.notice:
        d.text((x, 150), fit_text(view.notice, font(14), w), font=font(14), fill=MAGENTA)
    # ゲスト QR（静止）
    if view.guest_qr:
        img.paste(make_qr(view.guest_qr, 140), (x, 172))
        d.text((x + 150, 230), "曲を追加", font=font(16), fill=CYAN)
        d.text((x + 150, 254), "QR を読む", font=font(14), fill=MUTED)
    return img


def render_error(message: str, qr_payload: str, code: str | None = None) -> Image.Image:
    img, d = _canvas()
    d.text((20, 24), "!", font=font(40), fill=MAGENTA)
    d.text((60, 30), fit_text(message, font(22), WIDTH - 80), font=font(22), fill=FG)
    img.paste(make_qr(qr_payload, 200), (20, 100))
    d.text((240, 160), "設定はこの QR から", font=font(18), fill=MUTED)
    _draw_code(d, (240, 210), code)
    return img


def render_setup_full(qr_payload: str, caption: str = "設定用 QR", code: str | None = None) -> Image.Image:
    img, d = _canvas()
    img.paste(make_qr(qr_payload, 280), ((WIDTH - 280) // 2 - 60, 20))
    d.text((WIDTH - 110, HEIGHT // 2), caption, font=font(16), fill=FG, anchor="mm")
    _draw_code(d, (WIDTH - 150, HEIGHT // 2 + 30), code)
    return img
