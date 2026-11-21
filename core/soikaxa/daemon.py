"""制御デーモン: PlayerService（アプリケーションサービス）と main()。

Web・LCD・タッチはこのクラスの公開メソッドだけを呼ぶ（ARCH §5.2）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .adapters.base import AdapterError, Playlist, Resolved, SourceAdapter, Track
from .config import LastPlaylist, SettingsStore
from .player import EndReason, MpvError
from .queue import NowPlaying, PlayQueue, QueueItem
from .state import Event, State, StateMachine

log = logging.getLogger(__name__)

MSG_FALLBACK = "音源に接続できないためローカル音源で再生中"
EXPIRY_MARGIN_SEC = 30.0


class PlayerLike(Protocol):  # MpvPlayer / テスト用フェイク
    def play(self, resolved: Resolved) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def position(self) -> float | None: ...
    def duration(self) -> float | None: ...
    def on_end(self, cb: Callable[[EndReason], None]) -> None: ...


@dataclass(frozen=True)
class TrackView:
    id: str
    title: str
    artist: str = ""
    duration_sec: int | None = None
    added_by: str = ""
    origin: str = "playlist"
    thumbnail: str | None = None

    @classmethod
    def of(cls, np: NowPlaying) -> TrackView:
        t = np.track
        thumb = t.extra.get("thumbnail") if isinstance(t.extra, dict) else None
        return cls(t.id, t.title, t.artist, t.duration_sec, np.added_by, np.origin, thumb)


@dataclass(frozen=True)
class StatusView:
    state: State
    now: TrackView | None
    paused: bool
    position: float | None
    duration: float | None
    upcoming: list[TrackView] = field(default_factory=list)
    volume: int = 60
    adapter: str = "local"
    playlist_id: str | None = None
    notice: str = ""


def _start_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="prefetch", daemon=True).start()


class PlayerService:
    def __init__(
        self,
        *,
        adapters: dict[str, SourceAdapter],
        player: PlayerLike,
        store: SettingsStore,
        fallback: str = "local",
        max_consecutive_failures: int = 3,
        clock: Callable[[], float] = time.time,
        run_async: Callable[[Callable[[], None]], None] = _start_thread,
    ):
        if fallback not in adapters:
            raise ValueError("fallback adapter missing")
        self.adapters = adapters
        self.player = player
        self.store = store
        self.fallback = fallback
        self.max_failures = max_consecutive_failures
        self.clock = clock
        self.run_async = run_async
        self.queue = PlayQueue(clock=clock)
        self.sm = StateMachine(on_change=lambda *_: self._notify())
        self._lock = threading.RLock()
        self._listeners: list[Callable[[], None]] = []
        self._current: NowPlaying | None = None
        self._paused = False
        self._failures = 0
        self._fallback_active = False
        self._prefetched: tuple[str, Resolved] | None = None
        self._notice = ""
        self.health_notice = ""
        player.on_end(lambda reason: self._advance())

    # ---------- 購読 ----------
    def on_change(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                log.exception("listener failed")

    # ---------- 読取 ----------
    @property
    def active_name(self) -> str:
        if self._fallback_active:
            return self.fallback
        name = self.store.load().active_adapter
        return name if name in self.adapters else self.fallback

    @property
    def active(self) -> SourceAdapter:
        return self.adapters[self.active_name]

    @property
    def state(self) -> State:
        return self.sm.state

    def healthy(self, timeout: float = 5.0) -> bool:
        """ウォッチドッグ用。再生ループのロックが一定時間内に取れること。"""
        if not self._lock.acquire(timeout=timeout):
            return False
        self._lock.release()
        return True

    def status(self) -> StatusView:
        # ロックを取らない（resolve 中でも Web・LCD を止めない）。参照の読み出しのみで一貫性は十分
        current = self._current
        playing = current is not None
        return StatusView(
            state=self.sm.state,
            now=TrackView.of(current) if current else None,
            paused=self._paused,
            position=self.player.position() if playing else None,
            duration=self.player.duration() if playing else None,
            upcoming=[TrackView.of(n) for n in self.queue.upcoming(5)],
            volume=self.store.load().volume,
            adapter=self.active_name,
            playlist_id=self.queue.playlist_id,
            notice=self._notice or self.health_notice,
        )

    def playlists(self) -> list[Playlist]:
        return self.active.list_playlists()

    def search(self, query: str, limit: int = 20) -> list[Track | Playlist]:
        return self.active.search(query, limit)

    def playlist_tracks(self, playlist_id: str) -> list[Track]:
        return self.active.list_tracks(playlist_id)

    # ---------- 操作 ----------
    def select_playlist(self, playlist_id: str, adapter: str | None = None) -> None:
        name = adapter or self.active_name
        if name not in self.adapters:
            raise AdapterError(f"unknown adapter: {name}")
        tracks = self.adapters[name].list_tracks(playlist_id)
        with self._lock:
            if name != self.fallback:
                self._fallback_active = False
                self._notice = ""
            self._failures = 0
            self.queue.set_playlist(playlist_id, tracks)
            self.store.update(last_playlist=LastPlaylist(name, playlist_id))
            self._advance()

    def set_active_adapter(self, name: str) -> None:
        if name not in self.adapters:
            raise AdapterError(f"unknown adapter: {name}")
        with self._lock:
            self._fallback_active = False
            self._notice = ""
            self.store.update(active_adapter=name)
        self._notify()

    def toggle(self) -> None:
        with self._lock:
            if self._current is None:
                if self.queue.peek_next():
                    self._advance()
                return
            if self._paused:
                self.resume()
            else:
                self.pause()

    def pause(self) -> None:
        with self._lock:
            if self._current is None:
                return
            self.player.pause()
            self._paused = True
        self._notify()

    def resume(self) -> None:
        with self._lock:
            if self._current is None:
                return
            self.player.resume()
            self._paused = False
        self._notify()

    def next(self) -> None:
        self._advance()

    def enqueue(
        self,
        track: Track,
        *,
        added_by: str = "",
        guest_id: str | None = None,
        play_next: bool = False,
        limit: int | None = None,
    ) -> QueueItem:
        # キュー操作は PlayQueue 自身のロックで足りる。再生中の resolve を待たせない
        item = self.queue.add(track, added_by=added_by, guest_id=guest_id, play_next=play_next, limit=limit)
        if self._current is None:
            with self._lock:
                if self._current is None:  # 同時追加で 2 回 advance しない
                    self._advance()
        else:
            self._prefetch_next()
        self._notify()
        return item

    def remove(self, item_id: str) -> None:
        self.queue.remove(item_id)
        self._notify()

    def move(self, item_id: str, index: int) -> None:
        self.queue.move(item_id, index)
        self._notify()

    def set_volume(self, volume: int) -> None:
        v = max(0, min(100, int(volume)))
        self.player.set_volume(v)
        self.store.update(volume=v)
        self._notify()

    def dispatch(self, event: Event) -> State:
        return self.sm.dispatch(event)

    def autoplay(self) -> bool:
        """設定 ON かつ前回プレイリストがあれば再生を始める（FR-014）。"""
        s = self.store.load()
        if not s.autoplay or not s.last_playlist or self._current is not None:
            return False
        try:
            self.select_playlist(s.last_playlist.id, s.last_playlist.adapter)
        except AdapterError as e:
            log.warning("autoplay failed: %s", type(e).__name__)
            return False
        return True

    # ---------- 再生ループ ----------
    def _adapter_for(self, track: Track) -> SourceAdapter:
        return self.adapters.get(track.source) or self.active

    def _take_prefetched(self, track_id: str) -> Resolved | None:
        p, self._prefetched = self._prefetched, None
        if not p or p[0] != track_id:
            return None
        exp = p[1].expires_at
        if exp is not None and exp - EXPIRY_MARGIN_SEC <= self.clock():
            return None  # 期限切れ → 再解決
        return p[1]

    def _advance(self) -> None:
        with self._lock:
            while True:
                np = self.queue.pop_next()
                if np is None:
                    self._finish()
                    return
                try:
                    resolved = self._take_prefetched(np.track.id) or self._adapter_for(np.track).resolve(
                        np.track.id
                    )
                    self.player.play(resolved)
                except (AdapterError, MpvError) as e:
                    log.warning("skip %s: %s", np.track.id, type(e).__name__)
                    self._failures += 1
                    if self._failures >= self.max_failures and not self._fallback_active:
                        self._switch_to_fallback()
                    continue
                self._failures = 0
                self._current = np
                self._paused = False
                self.sm.dispatch(Event.PLAY)
                self._prefetch_next()
                break
        self._notify()

    def _finish(self) -> None:
        was_playing = self._current is not None
        self._current = None
        self._paused = False
        self._prefetched = None
        if was_playing:
            try:
                self.player.stop()
            except MpvError:
                log.warning("stop failed")
        self.sm.dispatch(Event.STOP)
        self._notify()

    def _switch_to_fallback(self) -> None:
        if self.active_name == self.fallback:
            return
        self._fallback_active = True
        self._notice = MSG_FALLBACK
        fb = self.adapters[self.fallback]
        try:
            pls = fb.list_playlists()
            if pls:
                self.queue.set_playlist(pls[0].id, fb.list_tracks(pls[0].id))
        except AdapterError:
            log.warning("fallback adapter has no playlist")
        log.warning("switched to fallback adapter")

    def _prefetch_next(self) -> None:
        nxt = self.queue.peek_next()
        if nxt is None or (self._prefetched and self._prefetched[0] == nxt.track.id):
            return
        track = nxt.track
        adapter = self._adapter_for(track)

        def job() -> None:
            try:
                r = adapter.resolve(track.id)
            except AdapterError:
                return  # 再生時にもう一度試す
            with self._lock:
                self._prefetched = (track.id, r)

        self.run_async(job)


if __name__ == "__main__":  # pragma: no cover - systemd から `python -m soikaxa.daemon`
    from .bootstrap import main

    main()
