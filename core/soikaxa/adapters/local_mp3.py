"""ローカル MP3 アダプタ (コア同梱・オフラインフォールバック)。

<root>/<プレイリスト名>/*.mp3  をプレイリストとして扱う。
参考実装: アダプタはこの程度の分量で書けることを示す。
"""
from __future__ import annotations

from pathlib import Path

from .base import AdapterError, Playlist, Resolved, SourceAdapter, Track

AUDIO_EXT = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}


class LocalAdapter(SourceAdapter):
    name = "local"

    def __init__(self, root: str | Path = "/srv/soikaxa/music"):
        self.root = Path(root)

    def list_playlists(self) -> list[Playlist]:
        if not self.root.is_dir():
            return []
        return [
            Playlist(id=d.name, title=d.name, source=self.name,
                     track_count=sum(1 for f in d.iterdir() if f.suffix.lower() in AUDIO_EXT))
            for d in sorted(self.root.iterdir()) if d.is_dir()
        ]

    def list_tracks(self, playlist_id: str) -> list[Track]:
        d = self.root / playlist_id
        if not d.is_dir():
            raise AdapterError(f"playlist not found: {playlist_id}")
        return [
            Track(id=f"{playlist_id}/{f.name}", title=f.stem, source=self.name)
            for f in sorted(d.iterdir()) if f.suffix.lower() in AUDIO_EXT
        ]

    def search(self, query: str, limit: int = 20) -> list[Track | Playlist]:
        q = query.lower()
        out: list[Track | Playlist] = []
        for pl in self.list_playlists():
            if q in pl.title.lower():
                out.append(pl)
            out.extend(t for t in self.list_tracks(pl.id) if q in t.title.lower())
            if len(out) >= limit:
                break
        return out[:limit]

    def resolve(self, track_id: str) -> Resolved:
        p = (self.root / track_id).resolve()
        if not p.is_file() or self.root.resolve() not in p.parents:
            raise AdapterError(f"track not found: {track_id}")
        return Resolved(uri=str(p))
