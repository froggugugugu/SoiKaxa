"""デーモンの状態機械（ARCH §6.1）。I/O を持たない。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import Enum


class State(str, Enum):
    BOOT = "BOOT"
    AP_ONLY = "AP_ONLY"
    ONLINE = "ONLINE"
    BT_CONNECTING = "BT_CONNECTING"
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    ERROR = "ERROR"


class Event(str, Enum):
    NET_UP = "NET_UP"
    NET_DOWN = "NET_DOWN"
    BT_CONNECTING = "BT_CONNECTING"
    BT_CONNECTED = "BT_CONNECTED"
    BT_LOST = "BT_LOST"
    PLAY = "PLAY"
    STOP = "STOP"
    FAIL = "FAIL"
    RECOVER = "RECOVER"


S, E = State, Event

TRANSITIONS: dict[State, dict[Event, State]] = {
    S.BOOT: {E.NET_UP: S.ONLINE, E.NET_DOWN: S.AP_ONLY, E.FAIL: S.ERROR},
    S.AP_ONLY: {E.NET_UP: S.ONLINE, E.FAIL: S.ERROR},
    S.ONLINE: {
        E.NET_DOWN: S.AP_ONLY,
        E.BT_CONNECTING: S.BT_CONNECTING,
        E.BT_CONNECTED: S.IDLE,
        E.FAIL: S.ERROR,
    },
    S.BT_CONNECTING: {E.NET_DOWN: S.AP_ONLY, E.BT_CONNECTED: S.IDLE, E.BT_LOST: S.ONLINE, E.FAIL: S.ERROR},
    S.IDLE: {E.NET_DOWN: S.AP_ONLY, E.BT_LOST: S.BT_CONNECTING, E.PLAY: S.PLAYING, E.FAIL: S.ERROR},
    # 再生中の上流断は PLAYING に留める（ローカル再生継続。エラー文言のみ表示）
    S.PLAYING: {E.BT_LOST: S.BT_CONNECTING, E.STOP: S.IDLE, E.FAIL: S.ERROR},
    S.ERROR: {E.NET_UP: S.ONLINE, E.NET_DOWN: S.AP_ONLY, E.RECOVER: S.ONLINE},
}


class StateMachine:
    def __init__(self, on_change: Callable[[State, State, Event], None] | None = None):
        self._state = State.BOOT
        self._on_change = on_change
        self._lock = threading.Lock()

    @property
    def state(self) -> State:
        return self._state

    def can(self, event: Event) -> bool:
        return event in TRANSITIONS[self._state]

    def dispatch(self, event: Event) -> State:
        with self._lock:
            prev = self._state
            nxt = TRANSITIONS[prev].get(event)
            if nxt is None or nxt == prev:
                return prev
            self._state = nxt
        if self._on_change:
            self._on_change(prev, nxt, event)
        return nxt
