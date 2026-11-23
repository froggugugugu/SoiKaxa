"""SoiKaxa ソースアダプタ インターフェース。

コアは YouTube を知らない。すべての音源はこの 4 関数で抽象化する。
アダプタは別パッケージ (soikaxa-adapter-*) として実装し、
entry point `soikaxa.adapters` で登録する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Playlist:
    id: str
    title: str
    track_count: int | None = None
    source: str = ""          # アダプタ名 (例: "ytmusic", "local", "radio")
    thumbnail: str | None = None


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str = ""
    duration_sec: int | None = None
    source: str = ""
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Resolved:
    """mpv に渡す再生対象。URL でもローカルパスでもよい。"""
    uri: str
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: float | None = None   # epoch 秒。期限付き URL の再解決判断に使う


@runtime_checkable
class SourceAdapter(Protocol):
    """4 関数だけ。これ以上増やさない。"""

    name: str

    def list_playlists(self) -> list[Playlist]:
        """オーナーが選べるプレイリスト一覧。"""
        ...

    def list_tracks(self, playlist_id: str) -> list[Track]:
        """プレイリスト内の曲を再生順で。"""
        ...

    def search(self, query: str, limit: int = 20) -> list[Track | Playlist]:
        """ゲスト向け検索。公開コンテンツのみ返す。"""
        ...

    def resolve(self, track_id: str) -> Resolved:
        """再生直前に呼ぶ。失敗は例外 (コアがスキップしてログする)。"""
        ...


class AdapterError(Exception):
    """resolve/search 失敗。コアは次の曲へスキップする。"""


class AuthRequired(AdapterError):
    """認証未登録または期限切れ。LCD と Web に案内を出す。"""
