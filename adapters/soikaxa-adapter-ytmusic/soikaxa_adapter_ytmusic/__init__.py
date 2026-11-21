"""SoiKaxa 用 YouTube Music ソースアダプタ（個人利用・自己責任）。

- ytmusicapi: プレイリスト一覧・曲一覧・公開コンテンツ検索
- yt-dlp: resolve(track_id) → 期限付きの音声ストリーム URL
- 認証: ytmusicapi の browser 方式。`<data>/adapters/ytmusic/browser.json`（0600）に保存。コミットしない
- DRM 回避は行わない
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from soikaxa.adapters.base import AdapterError, AuthRequired, Playlist, Resolved, SourceAdapter, Track
from soikaxa.config import write_private_file

log = logging.getLogger(__name__)

NAME = "ytmusic"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
YTDL_OPTS = {
    "format": "bestaudio[ext=webm]/bestaudio",
    "quiet": True,
    "noplaylist": True,
    "no_warnings": True,
}


def _default_ytmusic(auth: str | None) -> Any:
    from ytmusicapi import YTMusic

    return YTMusic(auth) if auth else YTMusic()


def _default_ytdl(opts: dict[str, Any]) -> Any:
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


def _default_setup(raw: str) -> str:
    import ytmusicapi

    return ytmusicapi.setup(filepath=None, headers_raw=raw)


def _artists(item: dict[str, Any]) -> str:
    return ", ".join(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))


def _thumb(item: dict[str, Any]) -> str | None:
    thumbs = item.get("thumbnails") or []
    return thumbs[-1].get("url") if thumbs else None


def _expires(url: str) -> float | None:
    try:
        v = parse_qs(urlparse(url).query).get("expire")
        return float(v[0]) if v else None
    except (ValueError, IndexError):
        return None


class YTMusicAdapter:
    name = NAME

    def __init__(
        self,
        auth_dir: Path,
        ytmusic_factory: Callable[[str | None], Any] = _default_ytmusic,
        ytdl_factory: Callable[[dict[str, Any]], Any] = _default_ytdl,
        setup: Callable[[str], str] = _default_setup,
    ):
        self.auth_dir = auth_dir
        self._ytmusic_factory = ytmusic_factory
        self._ytdl_factory = ytdl_factory
        self._setup = setup
        self._client: Any = None
        self._lock = threading.Lock()

    @property
    def auth_path(self) -> Path:
        return self.auth_dir / "browser.json"

    # ---- AuthCapable ----
    def has_auth(self) -> bool:
        return self.auth_path.is_file()

    def register_auth(self, raw: str) -> None:
        if not raw or len(raw) > 64_000:
            raise AdapterError("認証ヘッダが空か長すぎます")
        try:
            text = self._setup(raw)
        except Exception as e:
            raise AdapterError(
                "認証ヘッダを解釈できません（ログイン済みのリクエストヘッダを貼り付け）"
            ) from e
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.auth_dir, 0o700)
        write_private_file(self.auth_path, text)
        with self._lock:
            self._client = None

    def clear_auth(self) -> None:
        self.auth_path.unlink(missing_ok=True)
        with self._lock:
            self._client = None

    # ---- 内部 ----
    def _yt(self) -> Any:
        with self._lock:
            if self._client is None:
                self._client = self._ytmusic_factory(str(self.auth_path) if self.has_auth() else None)
            return self._client

    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except AdapterError:
            raise
        except Exception as e:
            msg = str(e).lower()
            if "401" in msg or "403" in msg or "sign in" in msg or "login" in msg:
                raise AuthRequired("認証の期限切れ。オーナー画面から再登録してください") from e
            raise AdapterError(f"{type(e).__name__}") from e

    # ---- SourceAdapter 4 関数 ----
    def list_playlists(self) -> list[Playlist]:
        if not self.has_auth():
            raise AuthRequired("認証が未登録です")
        items = self._call(lambda: self._yt().get_library_playlists(limit=100)) or []
        out = []
        for it in items:
            pid = it.get("playlistId")
            if not pid:
                continue
            count = it.get("count")
            try:
                n = int(str(count).replace(",", "").split()[0]) if count is not None else None
            except ValueError:
                n = None
            out.append(
                Playlist(id=pid, title=it.get("title", pid), track_count=n, source=NAME, thumbnail=_thumb(it))
            )
        return out

    def list_tracks(self, playlist_id: str) -> list[Track]:
        if not PLAYLIST_ID_RE.fullmatch(playlist_id or ""):
            raise AdapterError("invalid playlist id")
        data = self._call(lambda: self._yt().get_playlist(playlist_id, limit=None)) or {}
        return [t for t in (self._track(it) for it in data.get("tracks") or []) if t]

    def _track(self, it: dict[str, Any]) -> Track | None:
        vid = it.get("videoId")
        if not vid or it.get("isAvailable") is False:
            return None
        extra = {"thumbnail": _thumb(it)} if _thumb(it) else {}
        return Track(
            id=vid,
            title=it.get("title", vid),
            artist=_artists(it),
            duration_sec=it.get("duration_seconds"),
            source=NAME,
            extra=extra,
        )

    def search(self, query: str, limit: int = 20) -> list[Track | Playlist]:
        q = (query or "").strip()
        if not q:
            return []
        songs = self._call(lambda: self._yt().search(q, filter="songs", limit=limit)) or []
        out: list[Track | Playlist] = [t for t in (self._track(s) for s in songs) if t]
        pls = self._call(lambda: self._yt().search(q, filter="community_playlists", limit=5)) or []
        for p in pls:
            pid = p.get("playlistId") or (p.get("browseId") or "").removeprefix("VL")
            if pid:
                out.append(Playlist(id=pid, title=p.get("title", pid), source=NAME, thumbnail=_thumb(p)))
        return out[:limit]

    def resolve(self, track_id: str) -> Resolved:
        if not VIDEO_ID_RE.fullmatch(track_id or ""):
            raise AdapterError("invalid track id")
        url = f"https://music.youtube.com/watch?v={track_id}"

        def extract() -> Any:
            with self._ytdl_factory(dict(YTDL_OPTS)) as ydl:
                return ydl.extract_info(url, download=False)

        info = self._call(extract) or {}
        stream = info.get("url")
        if not stream:
            raise AdapterError("no audio stream")
        headers = {str(k): str(v) for k, v in (info.get("http_headers") or {}).items()}
        return Resolved(uri=stream, headers=headers, expires_at=_expires(stream))


def create(data_dir: Path) -> SourceAdapter:
    """entry point `soikaxa.adapters: ytmusic`。"""
    return YTMusicAdapter(Path(data_dir) / "adapters" / NAME)
