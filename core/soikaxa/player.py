"""mpv JSON IPC ラッパ。

mpv は `--idle --no-video --input-ipc-server=<sock>` で常駐させ、Unix ソケット越しに操作する。
テストでは FakeTransport を注入する。
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol

from .adapters.base import Resolved

log = logging.getLogger(__name__)

EndReason = Literal["eof", "error"]


class MpvError(Exception):
    pass


class MpvTransport(Protocol):
    def send(self, command: list[Any]) -> dict[str, Any]:
        """コマンドを送り応答（{"error": "success", "data": ...}）を返す。"""
        ...

    def on_event(self, cb: Callable[[dict[str, Any]], None]) -> None: ...


class UnixSocketTransport:  # pragma: no cover - 実機のみ。プロトコル処理は _Dispatcher でテスト
    def __init__(self, path: str, timeout: float = 5.0, connect_wait: float = 10.0):
        self.path = path
        self.timeout = timeout
        self._sock = self._connect(connect_wait)
        self._dispatcher = _Dispatcher()
        self._wlock = threading.Lock()
        threading.Thread(target=self._read_loop, name="mpv-ipc", daemon=True).start()

    def _connect(self, wait: float) -> socket.socket:
        deadline = time.monotonic() + wait
        while True:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(self.path)
                return s
            except OSError:
                s.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

    def _read_loop(self) -> None:
        buf = b""
        while True:
            chunk = self._sock.recv(65536)
            if not chunk:
                self._dispatcher.close()
                return
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                if line.strip():
                    self._dispatcher.feed(line)

    def send(self, command: list[Any]) -> dict[str, Any]:
        rid, waiter = self._dispatcher.register()
        payload = json.dumps({"command": command, "request_id": rid}).encode() + b"\n"
        with self._wlock:
            self._sock.sendall(payload)
        return self._dispatcher.wait(rid, waiter, self.timeout)

    def on_event(self, cb: Callable[[dict[str, Any]], None]) -> None:
        self._dispatcher.listeners.append(cb)


class _Dispatcher:
    """request_id で応答を待ち合わせ、event は購読者へ流す（ソケット非依存でテスト可能）。

    event の購読者は専用スレッドで呼ぶ。受信スレッド上で呼ぶと、購読者が mpv へ
    コマンドを送って応答を待ったときに受信スレッド自身が止まりデッドロックする。
    """

    def __init__(self) -> None:
        self._next = 1
        self._waiting: dict[int, tuple[threading.Event, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self._events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        threading.Thread(target=self._deliver, name="mpv-events", daemon=True).start()

    def _deliver(self) -> None:
        while (msg := self._events.get()) is not None:
            for cb in list(self.listeners):
                try:
                    cb(msg)
                except Exception:  # 購読者の失敗で IPC を止めない
                    log.exception("mpv event listener failed")
            self._events.task_done()
        self._events.task_done()

    def drain(self) -> None:
        """配送待ちの event をすべて配り終えるまで待つ（テスト用）。"""
        self._events.join()

    def register(self) -> tuple[int, tuple[threading.Event, list[dict[str, Any]]]]:
        with self._lock:
            rid = self._next
            self._next += 1
            waiter: tuple[threading.Event, list[dict[str, Any]]] = (threading.Event(), [])
            self._waiting[rid] = waiter
            return rid, waiter

    def wait(
        self, rid: int, waiter: tuple[threading.Event, list[dict[str, Any]]], timeout: float
    ) -> dict[str, Any]:
        ev, box = waiter
        if not ev.wait(timeout):
            with self._lock:
                self._waiting.pop(rid, None)
            raise MpvError("mpv IPC timeout")
        if not box:
            raise MpvError("mpv IPC closed")
        return box[0]

    def feed(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            log.warning("mpv: invalid json")
            return
        if "event" in msg:
            self._events.put(msg)
            return
        rid = msg.get("request_id")
        with self._lock:
            waiter = self._waiting.pop(rid, None) if isinstance(rid, int) else None
        if waiter:
            waiter[1].append(msg)
            waiter[0].set()

    def close(self) -> None:
        with self._lock:
            waiting, self._waiting = self._waiting, {}
        for ev, _ in waiting.values():
            ev.set()
        self._events.put(None)


def spawn_mpv(socket_path: str) -> subprocess.Popen[bytes]:  # pragma: no cover - 実機のみ
    return subprocess.Popen(
        [
            "mpv",
            "--idle=yes",
            "--no-video",
            "--no-terminal",
            "--audio-display=no",
            "--cache=yes",
            "--ytdl=no",
            f"--input-ipc-server={socket_path}",
        ]
    )


class MpvPlayer:
    """現在曲（mpv の playlist_entry_id）の自然終了・エラーだけを on_end に通知する。

    loadfile replace で差し替えた旧曲の end-file(stop) は entry id が異なるので無視される。
    """

    def __init__(self, transport: MpvTransport):
        self._t = transport
        self._end_cbs: list[Callable[[EndReason], None]] = []
        self._current_entry: int | None = None
        self._lock = threading.RLock()
        transport.on_event(self._on_event)

    # ---- コマンド ----
    def _cmd(self, *args: Any) -> Any:
        res = self._t.send(list(args))
        if res.get("error") != "success":
            raise MpvError(f"{args[0]}: {res.get('error')}")
        return res.get("data")

    def play(self, resolved: Resolved) -> None:
        headers = [f"{k}: {v}" for k, v in resolved.headers.items()]
        self._cmd("set_property", "http-header-fields", headers)
        # loadfile の応答から entry id を記録し終えるまで end-file の処理を待たせる。
        # 待たせないと、新しい曲の即時エラーを「古い曲の終了」と誤認して取りこぼす
        with self._lock:
            data = self._cmd("loadfile", resolved.uri, "replace")
            entry = data.get("playlist_entry_id") if isinstance(data, dict) else None
            self._current_entry = entry if isinstance(entry, int) else None
        self._cmd("set_property", "pause", False)

    def pause(self) -> None:
        self._cmd("set_property", "pause", True)

    def resume(self) -> None:
        self._cmd("set_property", "pause", False)

    def toggle(self) -> None:
        self._cmd("cycle", "pause")

    def stop(self) -> None:
        with self._lock:
            self._current_entry = None
        self._cmd("stop")

    def set_volume(self, volume: int) -> None:
        self._cmd("set_property", "volume", max(0, min(100, int(volume))))

    def _get(self, name: str) -> Any:
        try:
            return self._cmd("get_property", name)
        except MpvError:
            return None  # 再生前は property unavailable

    def position(self) -> float | None:
        v = self._get("time-pos")
        return float(v) if isinstance(v, int | float) else None

    def duration(self) -> float | None:
        v = self._get("duration")
        return float(v) if isinstance(v, int | float) else None

    def is_paused(self) -> bool:
        return bool(self._get("pause"))

    # ---- イベント ----
    def on_end(self, cb: Callable[[EndReason], None]) -> None:
        self._end_cbs.append(cb)

    def _on_event(self, msg: dict[str, Any]) -> None:
        if msg.get("event") != "end-file":
            return
        reason = msg.get("reason")
        if reason not in ("eof", "error"):
            return
        entry = msg.get("playlist_entry_id")
        with self._lock:
            # entry id を返さない古い mpv では current が None。その場合は全 eof/error を通す
            if self._current_entry is not None and entry != self._current_entry:
                return
            self._current_entry = None
        for cb in list(self._end_cbs):
            cb(reason)
