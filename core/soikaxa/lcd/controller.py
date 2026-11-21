"""LcdController: PlayerService の状態から画面を選び、回転レコードだけを部分書き込みする。"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import urllib.request
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from PIL import Image

from ..daemon import PlayerService, TrackView
from ..state import State
from .fb import Framebuffer
from .record import DIAMETER, RecordFrames, record_disc
from .screens import (
    RECORD_XY,
    NowPlayingView,
    render_boot,
    render_error,
    render_now_playing,
    render_setup,
    render_setup_full,
)

log = logging.getLogger(__name__)

SETUP_FULL_SEC = 30.0
FPS = 8
MSG_NO_SPEAKER = "スピーカーが見つかりません"


class CoverLoader:
    """ジャケット画像をダウンロードして端末内にキャッシュする（http(s) のみ、3 秒・2MB 上限）。"""

    def __init__(self, cache_dir: Path, fetch: Callable[[str], bytes] | None = None):
        self.cache_dir = cache_dir
        self._fetch = fetch or self._default_fetch

    @staticmethod
    def _default_fetch(url: str) -> bytes:  # pragma: no cover - ネットワーク依存
        with urllib.request.urlopen(url, timeout=3) as r:  # noqa: S310 - スキームは呼び出し側で検証済み
            return r.read(2_000_000)

    def __call__(self, track: TrackView) -> Image.Image | None:
        url = track.thumbnail
        if not url or not url.startswith(("https://", "http://")):
            return None
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".img")
        try:
            data = path.read_bytes() if path.exists() else self._fetch(url)
            img = Image.open(BytesIO(data))
            img.load()
            if not path.exists():
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return img.convert("RGB")
        except Exception:
            log.info("cover unavailable")
            return None


class LcdController:
    def __init__(
        self,
        service: PlayerService,
        fb: Framebuffer,
        qr_payload: Callable[[], str],
        portal_url: str,
        cover_loader: Callable[[TrackView], Image.Image | None] = lambda t: None,
        clock: Callable[[], float] = time.monotonic,
        run_async: Callable[[Callable[[], None]], None] | None = None,
    ):
        self.svc = service
        self.fb = fb
        self.qr_payload = qr_payload
        self.portal_url = portal_url
        self.cover_loader = cover_loader
        self.clock = clock
        self.run_async = run_async or (lambda f: threading.Thread(target=f, daemon=True).start())
        self.boot_progress = 0.0
        self.error_message = ""
        self._setup_until = 0.0
        self._dirty = threading.Event()
        self._frames: RecordFrames | None = None
        self._frames_for: str | None = None
        self._disc: Image.Image | None = None
        self._last_screen: str = ""
        self._stop = threading.Event()
        service.on_change(self.invalidate)

    def invalidate(self) -> None:
        self._dirty.set()

    def show_setup_qr(self) -> None:
        self._setup_until = self.clock() + SETUP_FULL_SEC
        self.invalidate()

    # ---- 描画 ----
    def compose(self) -> tuple[str, Image.Image]:
        """現在の状態に対応する (画面名, 画像) を返す。"""
        qr = self.qr_payload()
        if self.clock() < self._setup_until:
            return "setup_full", render_setup_full(qr)
        st = self.svc.status()
        if st.state == State.BOOT:
            return "boot", render_boot(self.boot_progress)
        if st.state == State.AP_ONLY:
            return "setup", render_setup(qr, self.portal_url)
        if st.state == State.ERROR:
            return "error", render_error(self.error_message or st.notice or "エラー", qr)
        notice = st.notice
        if st.state in (State.ONLINE, State.BT_CONNECTING) and not notice:
            notice = MSG_NO_SPEAKER
        nxt = st.upcoming[0] if st.upcoming else None
        view = NowPlayingView(
            title=st.now.title if st.now else "待機中",
            artist=st.now.artist if st.now else "",
            position=st.position,
            duration=st.duration,
            next_title=nxt.title if nxt else None,
            next_added_by=nxt.added_by if nxt else "",
            guest_qr=qr,
            paused=st.paused,
            notice=notice,
        )
        self._ensure_frames(st.now)
        return "now", render_now_playing(view, self._disc)

    def _ensure_frames(self, now: TrackView | None) -> None:
        key = now.id if now else None
        if key == self._frames_for:
            return
        self._frames_for = key
        self._frames = None
        seed = now.title if now else "soikaxa"
        self._disc = record_disc(None, seed)  # 生成中は静止画
        if now is None:
            return

        def build() -> None:
            cover = self.cover_loader(now)
            frames = RecordFrames.build(cover, seed)
            if self._frames_for == key:
                self._frames = frames
                self._disc = record_disc(cover, seed)
                self.invalidate()

        self.run_async(build)

    def render_once(self) -> str:
        name, img = self.compose()
        self.fb.write(img)
        self._last_screen = name
        return name

    def tick(self) -> None:
        """1 フレーム分。dirty なら全面、再生中ならレコード領域のみ。"""
        if self._dirty.is_set() or self.clock() < self._setup_until or self._last_screen == "setup_full":
            self._dirty.clear()
            self.render_once()
            return
        st_playing = (
            self._last_screen == "now" and self.svc.state == State.PLAYING and not self.svc.status().paused
        )
        if st_playing and self._frames:
            self.fb.write_raw(self._frames.next(), RECORD_XY, (DIAMETER, DIAMETER))

    def start(self) -> None:  # pragma: no cover - スレッドループ
        def loop() -> None:
            last_full = 0.0
            while not self._stop.is_set():
                try:
                    if self.clock() - last_full > 1.0:  # 進捗表示のため 1 秒ごとに全面更新
                        self.invalidate()
                        last_full = self.clock()
                    self.tick()
                except Exception:
                    log.exception("lcd tick failed")
                self._stop.wait(1 / FPS)

        threading.Thread(target=loop, name="lcd", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
