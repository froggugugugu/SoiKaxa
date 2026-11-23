"""タッチ判定（PRD 9.3）。

- 接触 0.05 秒未満: 無視（ノイズ）
- 0.05〜0.3 秒未満で離す: TAP（LCD のボタンだけが反応する軽いタップ。ほかの場所では無視）
- 0.3〜1.5 秒未満で離す: TOGGLE（再生/停止。ボタンの上ならボタン）
- 押したまま 1.5 秒: NEXT（押下中に 1 回だけ発火）
- 押したまま 10 秒: SETUP_QR（設定用 QR 全面表示）
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum

log = logging.getLogger(__name__)

Point = tuple[int, int]


class Gesture(str, Enum):
    TAP = "TAP"
    TOGGLE = "TOGGLE"
    NEXT = "NEXT"
    SETUP_QR = "SETUP_QR"


class TouchClassifier:
    def __init__(
        self, min_sec: float = 0.3, long_sec: float = 1.5, setup_sec: float = 10.0, tap_sec: float = 0.05
    ):
        self.min_sec, self.long_sec, self.setup_sec, self.tap_sec = min_sec, long_sec, setup_sec, tap_sec
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
        if fired or held < self.tap_sec:
            return None
        return Gesture.TOGGLE if held >= self.min_sec else Gesture.TAP


def map_touch(
    x: int,
    y: int,
    *,
    x_range: tuple[int, int],
    y_range: tuple[int, int],
    size: tuple[int, int],
    swap_xy: bool = False,
    invert_x: bool = False,
    invert_y: bool = False,
) -> Point:
    """タッチパネルの生の座標を画面の座標（0..幅-1, 0..高さ-1）に変換する。

    パネルの向きは LCD の回転設定とずれることがあるので、入れ替え・反転を指定できる。
    """
    fx = (x - x_range[0]) / max(1, x_range[1] - x_range[0])
    fy = (y - y_range[0]) / max(1, y_range[1] - y_range[0])
    if swap_xy:
        fx, fy = fy, fx
    if invert_x:
        fx = 1 - fx
    if invert_y:
        fy = 1 - fy
    w, h = size
    return (round(min(1.0, max(0.0, fx)) * (w - 1)), round(min(1.0, max(0.0, fy)) * (h - 1)))


class EvdevTouchSource:  # pragma: no cover - 実機（evdev）でのみ動く
    """BTN_TOUCH の押下/解放を TouchClassifier に流し、ジェスチャと触れた位置をコールバックする。

    orient は map_touch の向き補正（swap_xy / invert_x / invert_y）。
    """

    def __init__(
        self,
        device_path: str,
        on_gesture: Callable[[Gesture, Point | None], None],
        classifier: TouchClassifier | None = None,
        tick_sec: float = 0.05,
        size: tuple[int, int] = (480, 320),
        orient: dict[str, bool] | None = None,
    ):
        self.device_path = device_path
        self.on_gesture = on_gesture
        self.c = classifier or TouchClassifier()
        self.tick_sec = tick_sec
        self.size = size
        self.orient = orient or {}
        self._raw: list[int] = [0, 0]
        self._ranges: tuple[tuple[int, int], tuple[int, int]] = ((0, 4095), (0, 4095))
        self._touched = False  # 押下中に座標を受け取ったか

    def start(self) -> None:
        threading.Thread(target=self._events, name="touch", daemon=True).start()
        threading.Thread(target=self._ticker, name="touch-tick", daemon=True).start()

    def _pos(self) -> Point | None:
        if not self._touched:
            return None
        return map_touch(
            *self._raw,
            x_range=self._ranges[0],
            y_range=self._ranges[1],
            size=self.size,
            **self.orient,
        )

    def _emit(self, g: Gesture | None) -> None:
        if g:
            pos = self._pos()
            log.info("touch %s raw=%s screen=%s", g.value, tuple(self._raw), pos)  # 向きの確認用
            self.on_gesture(g, pos)

    def _events(self) -> None:
        import evdev

        dev = evdev.InputDevice(self.device_path)
        absinfo = dict(dev.capabilities().get(evdev.ecodes.EV_ABS, []))
        ax, ay = absinfo.get(evdev.ecodes.ABS_X), absinfo.get(evdev.ecodes.ABS_Y)
        if ax and ay:
            self._ranges = ((ax.min, ax.max), (ay.min, ay.max))
        for ev in dev.read_loop():
            if ev.type == evdev.ecodes.EV_ABS and ev.code in (evdev.ecodes.ABS_X, evdev.ecodes.ABS_Y):
                self._raw[0 if ev.code == evdev.ecodes.ABS_X else 1] = ev.value
                self._touched = True
            elif ev.type == evdev.ecodes.EV_KEY and ev.code == evdev.ecodes.BTN_TOUCH:
                now = time.monotonic()
                if ev.value == 1:
                    self._touched = False
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
