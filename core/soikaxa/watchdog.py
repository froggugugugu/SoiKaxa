"""systemd sd_notify（依存なし実装）。NOTIFY_SOCKET が無ければ何もしない。"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable, Mapping


def notify(message: str, env: Mapping[str, str] | None = None) -> bool:
    e: Mapping[str, str] = os.environ if env is None else env
    addr = e.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr)
        s.sendall(message.encode())
    return True


def watchdog_interval(env: Mapping[str, str] | None = None, default: float = 10.0) -> float:
    """WATCHDOG_USEC の半分（秒）。未設定なら default。"""
    e: Mapping[str, str] = os.environ if env is None else env
    usec = e.get("WATCHDOG_USEC")
    if not usec:
        return default
    try:
        return max(1.0, int(usec) / 1_000_000 / 2)
    except ValueError:
        return default


class Watchdog:
    def __init__(
        self,
        interval: float | None = None,
        healthy: Callable[[], bool] = lambda: True,
        send: Callable[[str], bool] = notify,
    ):
        self.interval = interval if interval is not None else watchdog_interval()
        self._healthy = healthy
        self._send = send
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> bool:
        if self._healthy():
            return self._send("WATCHDOG=1")
        return False

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.interval):
                self.beat()

        self._thread = threading.Thread(target=loop, name="watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
