"""スピーカーのボタン（再生/停止・曲送り）を本体の操作にする。

スピーカーのボタンは Bluetooth の AVRCP で届き、BlueZ が「<スピーカー名> (AVRCP)」という
入力デバイスのキー入力にする（2026-11-27 実機で確認: FUNLOGY Portable Mini2）。
入力デバイスはスピーカーがつながるたびに作り直されるので、定期的に探し直す。
音量ボタンは多くのスピーカーで本体側が処理し、ここには届かない。
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# linux/input-event-codes.h（evdev を import しなくても判定・テストできるよう数値で持つ）
KEY_PAUSE = 119
KEY_NEXTSONG = 163
KEY_PLAYPAUSE = 164
KEY_PREVIOUSSONG = 165
KEY_STOPCD = 166
KEY_PLAYCD = 200
KEY_PAUSECD = 201
KEY_PLAY = 207

ACTIONS = {
    KEY_PLAYPAUSE: "toggle",
    KEY_PLAYCD: "resume",
    KEY_PLAY: "resume",
    KEY_PAUSECD: "pause",
    KEY_PAUSE: "pause",
    KEY_STOPCD: "pause",  # 止めても曲は進めない（再生/停止と同じ扱い）
    KEY_NEXTSONG: "next",
    KEY_PREVIOUSSONG: "restart",  # 本体に「前の曲」は無いので、今の曲を頭から
}
PRESS = 1  # 0 = 離した、2 = 押しっぱなしのくり返し


def action_for(code: int, value: int) -> str | None:
    """キー入力 → 本体の操作名（PlayerService のメソッド名）。押した瞬間だけ反応する。"""
    return ACTIONS.get(code) if value == PRESS else None


def is_speaker_buttons(device_name: str) -> bool:
    return device_name.endswith("(AVRCP)")


def dispatch(service: Any, code: int, value: int) -> None:
    action = action_for(code, value)
    if action is None:
        return
    log.info("speaker button %s", action)
    try:
        getattr(service, action)()
    except Exception:
        log.exception("speaker button %s failed", action)  # 読み取りスレッドは止めない


class SpeakerButtons:  # pragma: no cover - 実機（evdev）でのみ動く
    """「(AVRCP)」の入力デバイスを探して読み、ボタンを service に渡す。"""

    def __init__(self, service: Any, rescan_sec: float = 3.0):
        self.service = service
        self.rescan_sec = rescan_sec
        self._open: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._scan_loop, name="speaker-buttons", daemon=True).start()

    def _scan_loop(self) -> None:
        import evdev

        while True:
            try:
                for path in evdev.list_devices():
                    with self._lock:
                        if path in self._open:
                            continue
                    try:
                        dev = evdev.InputDevice(path)
                    except OSError:
                        continue
                    if not is_speaker_buttons(dev.name):
                        dev.close()
                        continue
                    with self._lock:
                        self._open.add(path)
                    threading.Thread(
                        target=self._read, args=(dev,), name="speaker-buttons-read", daemon=True
                    ).start()
            except Exception:
                log.exception("speaker button scan failed")
            time.sleep(self.rescan_sec)

    def _read(self, dev: Any) -> None:
        import evdev

        log.info("speaker buttons: %s (%s)", dev.name, dev.path)
        try:
            for ev in dev.read_loop():
                if ev.type == evdev.ecodes.EV_KEY:
                    dispatch(self.service, ev.code, ev.value)
        except OSError:
            log.info("speaker buttons gone: %s", dev.path)  # スピーカーが切れた。つながり直せば探し直す
        finally:
            with self._lock:
                self._open.discard(dev.path)
            with contextlib.suppress(OSError):
                dev.close()
