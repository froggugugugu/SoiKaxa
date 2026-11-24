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
MAX_PLAYLIST_TRACKS = 500
SHARE_HOSTS = frozenset({"music.youtube.com", "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
URL_RE = re.compile(r"https?://\S+")
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


def parse_share_link(text: str) -> tuple[str, str] | None:
    """共有リンクから ("song", videoId) か ("playlist", playlistId) を取り出す。対象外なら None。

    アプリの「リンクをコピー」で得られる music.youtube.com / youtube.com / youtu.be の URL を受け付ける。
    前後に文章が付いていてもよい。曲とプレイリストの両方を含むときは曲を優先する。
    """
    m = URL_RE.search(text or "")
    if not m:
        return None
    u = urlparse(m.group(0))
    host = (u.hostname or "").lower()
    if host not in SHARE_HOSTS:
        return None
    qs = parse_qs(u.query)
    vid = u.path.strip("/") if host == "youtu.be" else (qs.get("v") or [""])[0]
    if VIDEO_ID_RE.fullmatch(vid):
        return ("song", vid)
    pid = (qs.get("list") or [""])[0]
    if u.path.rstrip("/") == "/playlist" and PLAYLIST_ID_RE.fullmatch(pid):
        return ("playlist", pid)
    return None


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
        self._clients: dict[bool, Any] = {}
        self._owner_playlists: set[str] = set()
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
            self._clients.clear()
            self._owner_playlists = set()

    def clear_auth(self) -> None:
        self.auth_path.unlink(missing_ok=True)
        with self._lock:
            self._clients.clear()
            self._owner_playlists = set()

    # ---- AuthCheckable ----
    def check_auth(self) -> int:
        """認証付きでライブラリのプレイリスト一覧を 1 回だけ取得する（再生 URL の解決はしない）。"""
        return len(self.list_playlists())

    # ---- 内部 ----
    def _yt(self, auth: bool) -> Any:
        """auth=True はオーナーの認証付き、False は未認証のクライアント。

        ゲストの検索・公開プレイリスト展開は未認証で行い、オーナーのアカウントを使わない。
        """
        with self._lock:
            if auth not in self._clients:
                self._clients[auth] = self._ytmusic_factory(str(self.auth_path) if auth else None)
            return self._clients[auth]

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
        items = self._call(lambda: self._yt(True).get_library_playlists(limit=100)) or []
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
        self._owner_playlists = {p.id for p in out}
        return out

    def list_tracks(self, playlist_id: str) -> list[Track]:
        if not PLAYLIST_ID_RE.fullmatch(playlist_id or ""):
            raise AdapterError("invalid playlist id")

        def fetch(auth: bool) -> dict[str, Any]:
            yt = self._yt(auth)
            return self._call(lambda: yt.get_playlist(playlist_id, limit=MAX_PLAYLIST_TRACKS)) or {}

        if playlist_id in self._owner_playlists and self.has_auth():
            data = fetch(True)
        else:
            try:
                data = fetch(False)  # 公開プレイリストは未認証で取る
            except AdapterError:
                if not self.has_auth():
                    raise
                data = fetch(True)  # 再起動直後の自動再生など、オーナーの非公開プレイリスト
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
        yt = self._yt(False)
        shared = parse_share_link(q)
        if shared:
            return self._from_share_link(yt, *shared)
        songs = self._call(lambda: yt.search(q, filter="songs", limit=limit)) or []
        tracks: list[Track | Playlist] = [t for t in (self._track(s) for s in songs) if t]
        found = self._call(lambda: yt.search(q, filter="community_playlists", limit=5)) or []
        pls: list[Track | Playlist] = []
        for p in found[:5]:
            pid = p.get("playlistId") or (p.get("browseId") or "").removeprefix("VL")
            if pid:
                pls.append(Playlist(id=pid, title=p.get("title", pid), source=NAME, thumbnail=_thumb(p)))
        pls = pls[:limit]
        # プレイリストの枠を先に確保する（曲だけで上限を使い切らない）
        return tracks[: limit - len(pls)] + pls

    def _from_share_link(self, yt: Any, kind: str, ident: str) -> list[Track | Playlist]:
        """共有リンクの曲（またはプレイリスト）1 件。再生できない・見つからないときは空。"""
        if kind == "playlist":
            try:
                data = self._call(lambda: yt.get_playlist(ident, limit=1)) or {}
            except AdapterError:
                return []
            title = data.get("title") or "共有されたプレイリスト"
            return [Playlist(id=ident, title=title, source=NAME, thumbnail=_thumb(data))]
        song = self._call(lambda: yt.get_song(ident)) or {}
        if (song.get("playabilityStatus") or {}).get("status") != "OK":
            return []
        d = song.get("videoDetails") or {}
        thumb = _thumb(d.get("thumbnail") or {})
        try:
            duration: int | None = int(d.get("lengthSeconds") or "")
        except (TypeError, ValueError):
            duration = None
        return [
            Track(
                id=ident,
                title=d.get("title") or ident,
                artist=d.get("author") or "",
                duration_sec=duration,
                source=NAME,
                extra={"thumbnail": thumb} if thumb else {},
            )
        ]

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
