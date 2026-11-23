"""再生キュー: プレイリスト（順番）+ ゲストキュー（優先）。I/O を持たない。"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .adapters.base import Track


class QueueLimitExceeded(Exception):
    """1 人あたりのキュー上限を超えた。"""


class ItemNotFound(KeyError):
    pass


@dataclass(frozen=True)
class QueueItem:
    item_id: str
    track: Track
    added_by: str = ""
    guest_id: str | None = None
    added_at: float = 0.0


@dataclass(frozen=True)
class NowPlaying:
    track: Track
    origin: Literal["queue", "playlist"]
    item: QueueItem | None = None

    @property
    def added_by(self) -> str:
        return self.item.added_by if self.item else ""


@dataclass
class PlayQueue:
    clock: Callable[[], float] = time.time
    repeat: Callable[[], bool] = field(default=lambda: False)  # True ならプレイリストの最後の次は先頭
    playlist_id: str | None = None
    _tracks: list[Track] = field(default_factory=list)
    _cursor: int = 0
    _items: list[QueueItem] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ---- プレイリスト ----
    def set_playlist(self, playlist_id: str, tracks: list[Track], start: int = 0) -> None:
        with self._lock:
            self.playlist_id = playlist_id
            self._tracks = list(tracks)
            self._cursor = max(0, min(start, len(self._tracks)))

    @property
    def playlist_remaining(self) -> int:
        return len(self._tracks) - self._cursor

    # ---- ゲストキュー ----
    def add(
        self,
        track: Track,
        *,
        added_by: str = "",
        guest_id: str | None = None,
        play_next: bool = False,
        limit: int | None = None,
    ) -> QueueItem:
        with self._lock:
            if guest_id is not None and limit is not None and self.count_by_guest(guest_id) >= limit:
                raise QueueLimitExceeded(f"limit {limit}")
            item = QueueItem(f"q{next(self._ids)}", track, added_by.strip(), guest_id, self.clock())
            if play_next:
                self._items.insert(0, item)
            else:
                self._items.append(item)
            return item

    def count_by_guest(self, guest_id: str) -> int:
        with self._lock:
            return sum(1 for i in self._items if i.guest_id == guest_id)

    def remove(self, item_id: str, guest_id: str | None = None) -> QueueItem:
        """guest_id を渡すと、そのゲストが追加した曲だけを消す（他人の曲は ItemNotFound）。"""
        with self._lock:
            for i, it in enumerate(self._items):
                if it.item_id == item_id and (guest_id is None or it.guest_id == guest_id):
                    return self._items.pop(i)
        raise ItemNotFound(item_id)

    def move(self, item_id: str, index: int) -> None:
        with self._lock:
            item = self.remove(item_id)
            index = max(0, min(index, len(self._items)))
            self._items.insert(index, item)

    def items(self) -> list[QueueItem]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    # ---- 再生順 ----
    def _wrap(self) -> None:
        """リピート中にプレイリストを使い切ったら先頭に戻す（ロック内で呼ぶ）。"""
        if self._tracks and self._cursor >= len(self._tracks) and self.repeat():
            self._cursor = 0

    def peek_next(self) -> NowPlaying | None:
        with self._lock:
            if self._items:
                return NowPlaying(self._items[0].track, "queue", self._items[0])
            self._wrap()
            if self._cursor < len(self._tracks):
                return NowPlaying(self._tracks[self._cursor], "playlist")
            return None

    def pop_next(self) -> NowPlaying | None:
        with self._lock:
            if self._items:
                it = self._items.pop(0)
                return NowPlaying(it.track, "queue", it)
            self._wrap()
            if self._cursor < len(self._tracks):
                t = self._tracks[self._cursor]
                self._cursor += 1
                return NowPlaying(t, "playlist")
            return None

    def upcoming(self, n: int = 5) -> list[NowPlaying]:
        with self._lock:
            out = [NowPlaying(i.track, "queue", i) for i in self._items[:n]]
            rest = n - len(out)
            if rest > 0:
                ahead = self._tracks[self._cursor :]
                if self.repeat():
                    ahead += self._tracks[: self._cursor]  # 先頭に戻って 1 周分まで
                out += [NowPlaying(t, "playlist") for t in ahead[:rest]]
            return out
