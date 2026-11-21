from io import BytesIO

import pytest
from PIL import Image
from soikaxa import bootstrap
from soikaxa.adapters.base import Resolved, Track
from soikaxa.config import SettingsStore, Speaker
from soikaxa.daemon import PlayerService, TrackView
from soikaxa.fakes import FakeTransport
from soikaxa.lcd.controller import MSG_NO_SPEAKER, CoverLoader, LcdController
from soikaxa.lcd.fb import Framebuffer
from soikaxa.player import MpvPlayer
from soikaxa.runner import CommandResult
from soikaxa.state import Event
from soikaxa.touch import Gesture


class Src:
    name = "local"

    def list_playlists(self):
        return []

    def list_tracks(self, pid):
        return [
            Track(id="a", title="A", source="local", extra={"thumbnail": "https://img/a.jpg"}),
            Track(id="b", title="B", source="local"),
        ]

    def search(self, q, limit=20):
        return []

    def resolve(self, tid):
        return Resolved(uri=tid)


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def lcd(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    tr = FakeTransport()
    svc = PlayerService(
        adapters={"local": Src()}, player=MpvPlayer(tr), store=store, run_async=lambda f: None
    )
    jobs = []
    clock = Clock()
    fb = Framebuffer(tmp_path / "fb")
    c = LcdController(
        svc,
        fb,
        lambda: "WIFI:T:WPA;S:soikaxa-ABCD;P:abcdefghjk;;",
        "http://10.42.0.1/",
        cover_loader=lambda t: Image.new("RGB", (50, 50), (255, 0, 0)),
        clock=clock,
        run_async=jobs.append,
    )
    return c, svc, fb, jobs, clock, tr


def test_screen_selection_by_state(lcd):
    c, svc, fb, jobs, clock, _ = lcd
    assert c.render_once() == "boot"
    assert fb.path.stat().st_size == 480 * 320 * 2
    svc.dispatch(Event.NET_DOWN)
    assert c.compose()[0] == "setup"
    svc.dispatch(Event.FAIL)
    c.error_message = "Wi-Fi に繋がりません"
    assert c.compose()[0] == "error"
    svc.dispatch(Event.RECOVER)
    assert c.compose()[0] == "now"
    c.show_setup_qr()
    assert c.compose()[0] == "setup_full"
    clock.t += 31
    assert c.compose()[0] == "now"


def test_record_frames_built_async_and_partial_write(lcd):
    c, svc, fb, jobs, clock, tr = lcd
    for e in (Event.NET_UP, Event.BT_CONNECTED):
        svc.dispatch(e)
    svc.select_playlist("p")
    c.tick()  # dirty → 全面描画、フレーム生成ジョブ登録
    assert c._last_screen == "now" and len(jobs) == 1
    jobs.pop()()
    assert c._frames is not None and len(c._frames.frames) == 72
    c.tick()  # invalidate 済み → 全面
    before = fb.path.read_bytes()
    c.tick()  # レコード領域のみ（0° は全面描画と同じ）
    c.tick()  # 5°
    after = fb.path.read_bytes()
    assert before != after and len(after) == len(before)
    svc.pause()
    c.tick()
    paused = fb.path.read_bytes()
    c.tick()
    assert fb.path.read_bytes() == paused  # 一時停止中は回転しない


def test_stale_frames_discarded_on_track_change(lcd):
    c, svc, fb, jobs, *_ = lcd
    svc.dispatch(Event.NET_UP)
    svc.select_playlist("p")
    c.compose()
    svc.next()
    c.compose()
    jobs.pop(0)()  # 前の曲のジョブが後から終わっても採用しない
    assert c._frames is None
    jobs.pop(0)()
    assert c._frames is not None


def test_no_speaker_notice(lcd):
    c, svc, *_ = lcd
    svc.dispatch(Event.NET_UP)
    c.compose()
    assert MSG_NO_SPEAKER  # ONLINE ではスピーカー案内を出す（描画が例外なく完了すること）


def test_cover_loader_cache_and_rejects(tmp_path):
    buf = BytesIO()
    Image.new("RGB", (10, 10), (0, 255, 0)).save(buf, "PNG")
    calls = []
    loader = CoverLoader(tmp_path / "covers", fetch=lambda url: calls.append(url) or buf.getvalue())
    t = TrackView(id="a", title="A", thumbnail="https://img/a.png")
    assert loader(t).size == (10, 10)
    assert loader(t).size == (10, 10) and len(calls) == 1  # 2 回目はキャッシュ
    assert loader(TrackView(id="b", title="B", thumbnail="file:///etc/passwd")) is None
    assert loader(TrackView(id="c", title="C")) is None
    bad = CoverLoader(tmp_path / "c2", fetch=lambda url: b"not an image")
    assert bad(TrackView(id="d", title="D", thumbnail="https://x/d")) is None


# ---------- bootstrap ----------


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("SOIKAXA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOIKAXA_MUSIC_DIR", str(tmp_path / "music"))
    return bootstrap.build_runtime(fake=True)


def test_build_runtime_fake_and_ap_credentials(rt):
    assert rt.fake and "local" in rt.service.adapters
    s = bootstrap.ensure_ap_credentials(rt.store, rt.wifi)
    assert s.ap_ssid.startswith("soikaxa-") and len(s.ap_password) == 10
    assert bootstrap.ensure_ap_credentials(rt.store, rt.wifi) == s  # 2 回目は維持


def test_connect_network_states(rt):
    rt.wifi.http_get = lambda u: (0, "")
    bootstrap.connect_network(rt)
    assert rt.service.state.value == "AP_ONLY"
    rt.wifi.http_get = lambda u: (204, "")
    bootstrap.connect_network(rt)
    assert rt.service.state.value == "ONLINE"
    rt.wifi.http_get = lambda u: (200, "<html>")
    bootstrap.connect_network(rt)
    assert rt.service.state.value == "ERROR" and "テザリング" in rt.service.status().notice


def test_connect_speaker(rt):
    rt.service.dispatch(Event.NET_UP)
    assert not bootstrap.connect_speaker(rt)  # 記憶機器なし
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")])
    assert bootstrap.connect_speaker(rt) and rt.service.state.value == "IDLE"


def test_connect_speaker_failure(rt):
    rt.service.dispatch(Event.NET_UP)
    rt.runner.on(["bluetoothctl", "info"], CommandResult(0, "Connected: no"))
    rt.runner.on(["bluetoothctl", "--timeout", "15", "connect"], CommandResult(1, "Failed"))
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")])
    assert not bootstrap.connect_speaker(rt) and rt.service.state.value == "ONLINE"


def test_on_gesture_routes(rt):
    shown = []
    music = rt.data_dir.parent / "music" / "Mix"
    music.mkdir(parents=True)
    for n in ("1.mp3", "2.mp3"):
        (music / n).write_bytes(b"")
    rt.service.select_playlist("Mix", "local")
    bootstrap.on_gesture(rt, Gesture.TOGGLE, lambda: shown.append(1))
    assert rt.service.status().paused
    bootstrap.on_gesture(rt, Gesture.NEXT, lambda: shown.append(1))
    assert rt.service.status().now.title == "2"
    bootstrap.on_gesture(rt, Gesture.SETUP_QR, lambda: shown.append(1))
    assert shown == [1]
