from soikaxa import mediakeys
from soikaxa.mediakeys import (
    KEY_NEXTSONG,
    KEY_PAUSECD,
    KEY_PLAYCD,
    KEY_PLAYPAUSE,
    KEY_PREVIOUSSONG,
    action_for,
    is_speaker_buttons,
)


def test_action_for_maps_speaker_buttons_on_press_only():
    assert action_for(KEY_PLAYPAUSE, 1) == "toggle"
    assert action_for(KEY_PLAYCD, 1) == "resume"
    assert action_for(KEY_PAUSECD, 1) == "pause"
    assert action_for(KEY_NEXTSONG, 1) == "next"
    assert action_for(KEY_PREVIOUSSONG, 1) == "restart"  # 本体に前の曲は無いので頭から
    assert action_for(KEY_PLAYPAUSE, 0) is None  # 離した
    assert action_for(KEY_PLAYPAUSE, 2) is None  # 押しっぱなしのくり返し
    assert action_for(30, 1) is None  # 関係ないキー


def test_is_speaker_buttons_by_device_name():
    assert is_speaker_buttons("FUNLOGY Portable Mini2 (AVRCP)")
    assert not is_speaker_buttons("ADS7846 Touchscreen")


class Svc:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda: self.calls.append(name)


def test_dispatch_calls_service_and_survives_errors():
    svc = Svc()
    mediakeys.dispatch(svc, KEY_NEXTSONG, 1)
    mediakeys.dispatch(svc, KEY_PLAYPAUSE, 0)
    assert svc.calls == ["next"]

    class Broken:
        def next(self):
            raise RuntimeError("x")

    mediakeys.dispatch(Broken(), KEY_NEXTSONG, 1)  # 例外で読み取りスレッドを止めない
