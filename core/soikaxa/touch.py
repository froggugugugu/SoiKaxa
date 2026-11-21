"""タッチ判定（PRD 9.3）。

- 接触 0.3 秒未満: 無視（誤タッチ防止）
- 0.3〜1.5 秒未満で離す: TOGGLE（再生/停止）
- 押したまま 1.5 秒: NEXT（押下中に 1 回だけ発火）
- 押したまま 10 秒: SETUP_QR（設定用 QR 全面表示）
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from enum import Enum


class Gesture(str, Enum):
    TOGGLE = "TOGGLE"
    NEXT = "NEXT"
    SETUP_QR = "SETUP_QR"


class TouchClassifier:
    def __init__(self, min_sec: float = 0.3, long_sec: float = 1.5, setup_sec: float = 10.0):
        self.min_sec, self.long_sec, self.setup_sec = min_sec, long_sec, setup_sec
        self._down_at: float | None = None
        self._fired: set[Gesture] = set()

    @property
    def pressed(self) -> bool:
        return self._down_at is not None

    def press(self, t: float) -> None:
        if self._down_at is None:
            self._down_at = t
            self._fired = set()

    def tick(self, t: float) -> Gesture | None:
        """押下中に定期的に呼ぶ。長押しの閾値到達で 1 回だけ返す。"""
        if self._down_at is None:
            return None
        held = t - self._down_at
        if held >= self.setup_sec and Gesture.SETUP_QR not in self._fired:
            self._fired.add(Gesture.SETUP_QR)
            return Gesture.SETUP_QR
        if held >= self.long_sec and Gesture.NEXT not in self._fired:
            self._fired.add(Gesture.NEXT)
            return Gesture.NEXT
        return None

    def release(self, t: float) -> Gesture | None:
        if self._down_at is None:
            return None
        pending = self.tick(t)  # tick を取りこぼしても閾値到達分は返す
        held = t - self._down_at
        fired = self._fired
        self._down_at = None
        if pending:
            return pending
        if fired or held < self.min_sec:
            return None
        return Gesture.TOGGLE


class EvdevTouchSource:  # pragma: no cover - 実機（evdev）でのみ動く
    """BTN_TOUCH の押下/解放を TouchClassifier に流し、ジェスチャをコールバックする。"""

    def __init__(
        self,
        device_path: str,
        on_gesture: Callable[[Gesture], None],
        classifier: TouchClassifier | None = None,
        tick_sec: float = 0.05,
    ):
        self.device_path = device_path
        self.on_gesture = on_gesture
        self.c = classifier or TouchClassifier()
        self.tick_sec = tick_sec

    def start(self) -> None:
        threading.Thread(target=self._events, name="touch", daemon=True).start()
        threading.Thread(target=self._ticker, name="touch-tick", daemon=True).start()

    def _emit(self, g: Gesture | None) -> None:
        if g:
            self.on_gesture(g)

    def _events(self) -> None:
        import evdev

        dev = evdev.InputDevice(self.device_path)
        for ev in dev.read_loop():
            if ev.type == evdev.ecodes.EV_KEY and ev.code == evdev.ecodes.BTN_TOUCH:
                now = time.monotonic()
                if ev.value == 1:
                    self.c.press(now)
                elif ev.value == 0:
                    self._emit(self.c.release(now))

    def _ticker(self) -> None:
        while True:
            time.sleep(self.tick_sec)
            if self.c.pressed:
                self._emit(self.c.tick(time.monotonic()))


def find_touch_device() -> str | None:  # pragma: no cover - 実機のみ
    try:
        import evdev
    except ImportError:
        return None
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if evdev.ecodes.BTN_TOUCH in d.capabilities().get(evdev.ecodes.EV_KEY, []):
            return path
    return None
