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
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)

Point = tuple[int, int]


class Gesture(str, Enum):
    TAP = "TAP"
    TOGGLE = "TOGGLE"
    NEXT = "NEXT"
    SETUP_QR = "SETUP_QR"


TouchResult = tuple[Gesture, Point | None, Point | None]  # (ジェスチャ, 画面の位置, 生の座標)


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


@dataclass(frozen=True)
class TouchCal:
    """タッチパネルの生の座標 → 画面の座標の対応（位置合わせの結果）。

    swap: 生の X/Y が画面の縦横と入れ替わっている。
    x / y: 画面の左端・右端（上端・下端）に当たる生の値。逆向きなら大小が逆になる。
    """

    swap: bool
    x: tuple[int, int]
    y: tuple[int, int]

    def map(self, raw_x: int, raw_y: int, size: tuple[int, int]) -> Point:
        a, b = (raw_y, raw_x) if self.swap else (raw_x, raw_y)
        fx = (a - self.x[0]) / (self.x[1] - self.x[0])
        fy = (b - self.y[0]) / (self.y[1] - self.y[0])
        w, h = size
        return (round(min(1.0, max(0.0, fx)) * (w - 1)), round(min(1.0, max(0.0, fy)) * (h - 1)))

    def to_dict(self) -> dict[str, Any]:
        return {"swap": self.swap, "x": list(self.x), "y": list(self.y)}

    @classmethod
    def from_dict(cls, d: Any) -> TouchCal | None:
        """設定ファイルの値から作る。壊れていれば None（既定値を使う）。"""
        try:
            cal = cls(bool(d["swap"]), (int(d["x"][0]), int(d["x"][1])), (int(d["y"][0]), int(d["y"][1])))
        except (TypeError, KeyError, IndexError, ValueError):
            return None
        if not isinstance(d["swap"], bool) or cal.x[0] == cal.x[1] or cal.y[0] == cal.y[1]:
            return None
        return cal


# OSOYOO 3.5" B（install.sh の dtoverlay=osoyoo35b）の実測から推定。左右が逆で、0〜4095 の一部しか使わない。
# 位置合わせ（オーナー画面 → 設定）をすると、その結果に置き換わる
DEFAULT_CAL = TouchCal(swap=False, x=(3570, 250), y=(200, 3960))

# 位置合わせで順にタップしてもらう画面上の点（左上 → 右上 → 左下）
CAL_TARGETS: tuple[Point, ...] = ((40, 40), (440, 40), (40, 280))
MIN_RAW_SPAN = 200  # 2 点の生の値がこれより近ければ、同じ所を押したとみなしてやり直す


def solve_calibration(raws: list[Point], size: tuple[int, int] = (480, 320)) -> TouchCal:
    """CAL_TARGETS を順に押したときの生の座標 3 つから TouchCal を求める。押し方がおかしければ ValueError。"""
    if len(raws) != len(CAL_TARGETS):
        raise ValueError("3 点が必要です")
    (sx0, sy0), (sx1, _), (_, sy2) = CAL_TARGETS
    r0, r1, r2 = raws
    # 左上 → 右上 は画面の横移動。生の値で大きく動いた軸が画面の X
    swap = abs(r1[1] - r0[1]) > abs(r1[0] - r0[0])
    ax = 1 if swap else 0
    ay = 1 - ax
    dx, dy = r1[ax] - r0[ax], r2[ay] - r0[ay]
    if abs(dx) < MIN_RAW_SPAN or abs(dy) < MIN_RAW_SPAN:
        raise ValueError("タップの位置が近すぎます")
    w, h = size
    kx, ky = dx / (sx1 - sx0), dy / (sy2 - sy0)  # 画面 1px あたりの生の値
    x = (round(r0[ax] - sx0 * kx), round(r0[ax] + (w - 1 - sx0) * kx))
    y = (round(r0[ay] - sy0 * ky), round(r0[ay] + (h - 1 - sy0) * ky))
    return TouchCal(swap, x, y)


class TouchTracker:
    """タッチパネルの生のイベント（座標・押下・解放）から、ジェスチャ・画面の位置・生の座標を作る。

    Linux の入力層は前回と同じ（または誤差内の）座標を送らないので、押下ごとに座標を消さず、
    最後に受け取った座標を使う。同じボタンを続けて押したときに位置が分からなくなるのを防ぐ。
    """

    def __init__(self, size: tuple[int, int], cal: TouchCal, classifier: TouchClassifier | None = None):
        self.size, self.cal = size, cal
        self.c = classifier or TouchClassifier()
        self.raw = [0, 0]
        self._has_pos = False

    def abs_x(self, v: int) -> None:
        self.raw[0], self._has_pos = v, True

    def abs_y(self, v: int) -> None:
        self.raw[1], self._has_pos = v, True

    def _result(self, g: Gesture | None) -> TouchResult | None:
        if g is None:
            return None
        if not self._has_pos:
            return (g, None, None)
        raw = (self.raw[0], self.raw[1])
        return (g, self.cal.map(*raw, size=self.size), raw)

    def down(self, t: float) -> None:
        self.c.press(t)

    def up(self, t: float) -> TouchResult | None:
        return self._result(self.c.release(t))

    def tick(self, t: float) -> TouchResult | None:
        return self._result(self.c.tick(t))


class EvdevTouchSource:  # pragma: no cover - 実機（evdev）でのみ動く
    """evdev のイベントを TouchTracker に流し、(ジェスチャ, 画面の位置, 生の座標) をコールバックする。"""

    def __init__(
        self,
        device_path: str,
        on_touch: Callable[[Gesture, Point | None, Point | None], None],
        cal: TouchCal = DEFAULT_CAL,
        tick_sec: float = 0.05,
        size: tuple[int, int] = (480, 320),
    ):
        self.device_path = device_path
        self.on_touch = on_touch
        self.tick_sec = tick_sec
        self.t = TouchTracker(size, cal)
        self._lock = threading.Lock()  # イベント受信とタイマーの 2 スレッドから触る

    def set_calibration(self, cal: TouchCal) -> None:
        with self._lock:
            self.t.cal = cal

    def start(self) -> None:
        threading.Thread(target=self._events, name="touch", daemon=True).start()
        threading.Thread(target=self._ticker, name="touch-tick", daemon=True).start()

    def _emit(self, r: TouchResult | None) -> None:
        if r:
            log.info("touch %s raw=%s screen=%s", r[0].value, r[2], r[1])  # 位置合わせの確認用
            self.on_touch(*r)

    def _events(self) -> None:
        import evdev

        dev = evdev.InputDevice(self.device_path)
        log.info("touch device %s cal=%s", self.device_path, self.t.cal)
        for ev in dev.read_loop():
            r = None
            with self._lock:
                if ev.type == evdev.ecodes.EV_ABS and ev.code == evdev.ecodes.ABS_X:
                    self.t.abs_x(ev.value)
                elif ev.type == evdev.ecodes.EV_ABS and ev.code == evdev.ecodes.ABS_Y:
                    self.t.abs_y(ev.value)
                elif ev.type == evdev.ecodes.EV_KEY and ev.code == evdev.ecodes.BTN_TOUCH:
                    if ev.value == 1:
                        self.t.down(time.monotonic())
                    elif ev.value == 0:
                        r = self.t.up(time.monotonic())
            self._emit(r)

    def _ticker(self) -> None:
        while True:
            time.sleep(self.tick_sec)
            with self._lock:
                r = self.t.tick(time.monotonic()) if self.t.c.pressed else None
            self._emit(r)


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
