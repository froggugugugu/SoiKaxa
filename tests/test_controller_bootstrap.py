from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image
from soikaxa import bootstrap
from soikaxa.adapters.base import Resolved, Track
from soikaxa.config import SettingsStore, Speaker
from soikaxa.daemon import PlayerService, TrackView
from soikaxa.fakes import FakeTransport
from soikaxa.lcd import screens
from soikaxa.lcd.controller import (
    AUTO_DISC_SEC,
    AUTO_LIST_SEC,
    CAL_DONE_SEC,
    CAL_TIMEOUT_SEC,
    LIST_ADDED_SEC,
    MSG_NO_SPEAKER,
    CoverLoader,
    LcdController,
)
from soikaxa.lcd.fb import Framebuffer
from soikaxa.player import MpvPlayer
from soikaxa.runner import CommandResult
from soikaxa.state import Event
from soikaxa.touch import DEFAULT_CAL, Gesture, TouchCal


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


def test_notice_by_state(lcd):
    c, svc, *_ = lcd
    svc.dispatch(Event.NET_UP)
    assert c.view()[1].notice == MSG_NO_SPEAKER  # ONLINE: スピーカー未接続の案内
    svc.dispatch(Event.BT_CONNECTING)
    assert c.view()[1].notice == MSG_NO_SPEAKER
    svc.net_notice = "上流の案内"
    assert c.view()[1].notice == "上流の案内"  # 既存の通知を上書きしない
    svc.net_notice = ""
    svc.dispatch(Event.BT_CONNECTED)
    assert c.view()[1].notice == ""  # IDLE: 案内なし


def test_setup_code_is_shown_only_while_pin_unset(lcd):
    c, svc, fb, *_ = lcd
    code = ["493817"]
    c.setup_code = lambda: code[0]
    svc.dispatch(Event.NET_UP)
    svc.dispatch(Event.BT_CONNECTED)
    assert c.view()[1].notice == "設定コード 493817"
    with_code = c.compose()[1].tobytes()
    svc.dispatch(Event.FAIL)
    err_with = c.compose()[1].tobytes()
    code[0] = None  # PIN 登録後
    assert c.compose()[1].tobytes() != err_with
    svc.dispatch(Event.RECOVER)
    svc.dispatch(Event.BT_CONNECTED)
    assert c.view()[1].notice == "" and c.compose()[1].tobytes() != with_code


def test_now_screen_while_playing_regardless_of_state(lcd):
    """上流なし（AP_ONLY）のローカル再生やエラー中でも、曲が流れていれば曲名とレコードを出す。"""
    c, svc, fb, jobs, *_ = lcd
    svc.dispatch(Event.NET_DOWN)
    assert c.view()[0] == "setup"
    svc.select_playlist("p")
    assert svc.state.value == "AP_ONLY" and c.view()[0] == "now" and c.view()[1].title == "A"
    svc.dispatch(Event.FAIL)
    svc.net_notice = "この Wi-Fi は同意が必要。テザリングへ"
    name, view = c.view()
    assert name == "now" and view.notice.startswith("この Wi-Fi")
    c.tick()
    jobs.pop()()
    c.tick()
    before = fb.path.read_bytes()
    c.tick()
    c.tick()
    assert fb.path.read_bytes() != before  # 状態が PLAYING でなくてもレコードが回る


def test_full_redraw_keeps_record_angle(lcd):
    """進捗更新の全面描画でレコードが 0° に戻らない（毎秒のカクつき防止）。"""
    c, svc, fb, jobs, *_ = lcd
    svc.dispatch(Event.NET_UP)
    svc.select_playlist("p")
    c.tick()
    jobs.pop()()
    c.tick()
    for _ in range(10):
        c.tick()  # 回転を進める

    def region():
        data = fb.path.read_bytes()
        x, y = 20, 60
        return b"".join(data[((y + i) * 480 + x) * 2 :][:400] for i in range(200))

    spinning = region()
    assert spinning != c._frames.frames[0]
    c.invalidate()
    c.tick()  # 全面描画
    assert region() == spinning


def test_long_title_scrolls_by_partial_write(lcd):
    """長い曲名は帯だけを部分書き込みして流す。位置が変わらなければ書かない。曲が変わったら先頭から。"""
    from soikaxa.lcd.screens import MARQUEE_PAUSE_SEC, TITLE_BOX

    c, svc, fb, jobs, clock, tr = lcd
    for e in (Event.NET_UP, Event.BT_CONNECTED):
        svc.dispatch(e)
    c._title = ""
    writes = []
    orig = fb.write_raw
    fb.write_raw = lambda data, xy, size: (writes.append((xy, size)), orig(data, xy, size))[1]
    long = "とても長い曲名のテスト — Extended Version (Live at Somewhere 2026)"
    svc.status = lambda orig=svc.status: replace(orig(), now=TrackView(id="t1", title=long))
    c.tick()  # 全面
    writes.clear()
    c.tick()  # 先頭で停止中: 帯は書かない
    assert not [w for w in writes if w[0] == TITLE_BOX[:2]]
    clock.t += MARQUEE_PAUSE_SEC + 0.5
    c.tick()
    strip = [w for w in writes if w[0] == TITLE_BOX[:2]]
    assert strip == [(TITLE_BOX[:2], (TITLE_BOX[2] - TITLE_BOX[0], TITLE_BOX[3] - TITLE_BOX[1]))]
    n = len(writes)
    c.tick()  # 時間が進まず位置も同じ → 書かない
    assert len(writes) == n
    svc.status = lambda orig=svc.status: replace(orig(), now=TrackView(id="t2", title=long + "2"))
    c.invalidate()
    c.tick()
    assert c._marquee() == 0  # 曲が変わったら先頭から


def test_setup_full_is_drawn_once_not_every_tick(lcd):
    c, svc, fb, jobs, clock, _ = lcd
    svc.dispatch(Event.NET_UP)
    writes = []
    orig = fb.write_raw
    fb.write_raw = lambda *a: (writes.append(1), orig(*a))[1]
    c.tick()
    c.show_setup_qr()
    c.tick()
    n = len(writes)
    for _ in range(20):
        c.invalidate()  # 曲の変化や 1 秒ごとの更新が来ても描き直さない
        c.tick()
    assert len(writes) == n and c._last_screen == "setup_full"
    clock.t += 31
    c.tick()
    assert len(writes) == n + 1 and c._last_screen == "now"


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


def test_connect_speaker_skips_while_owner_is_connecting(rt):
    """オーナーの「接続」中は自動再接続を送らず、状態も動かさない（同時 connect で互いに失敗させない）。"""
    rt.service.dispatch(Event.NET_UP)
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")])
    rt.bt._manual = True
    n = len(rt.runner.calls)
    assert not bootstrap.connect_speaker(rt)
    assert len(rt.runner.calls) == n and rt.service.state.value == "ONLINE"


def test_forgetting_last_speaker_leaves_idle_and_playing(rt):
    """最後の記憶スピーカーを消したら、待機中・再生中のままにせず「スピーカーなし」へ戻す。"""
    _music(rt)
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")])
    rt.service.dispatch(Event.NET_UP)
    assert bootstrap.connect_speaker(rt)
    rt.service.select_playlist("Mix", "local")
    assert rt.service.state.value == "PLAYING"
    rt.store.update(known_speakers=[])  # オーナーが「本体から削除」
    assert not bootstrap.connect_speaker(rt)
    assert rt.service.state.value == "ONLINE" and not rt.speaker_connected


def _music(rt, name="Mix", n=2):
    d = rt.data_dir.parent / "music" / name
    d.mkdir(parents=True)
    for i in range(1, n + 1):
        (d / f"{i}.mp3").write_bytes(b"")


def test_captive_notice_survives_ok_healthcheck_and_clears_when_online(rt):
    """FR-015: 毎朝のヘルスチェックが正常でも、キャプティブの案内を消さない。"""
    from soikaxa.health import HealthResult, save_result

    save_result(HealthResult(True, "OK", 40.0, 1.0), rt.data_dir / "health.json")
    rt.wifi.http_get = lambda u: (200, "<html>")
    for n in range(2, 8):  # 監視を何周しても文言が残る
        bootstrap.monitor_step(rt, n)
        assert rt.service.status().notice == bootstrap.MSG_CAPTIVE
    assert rt.service.state.value == "ERROR"
    rt.wifi.http_get = lambda u: (204, "")
    bootstrap.monitor_step(rt, 2)
    assert rt.service.status().notice == "" and rt.service.state.value in ("ONLINE", "IDLE")
    rt.wifi.http_get = lambda u: (200, "<html>")
    bootstrap.monitor_step(rt, 2)
    rt.wifi.http_get = lambda u: (0, "")
    bootstrap.monitor_step(rt, 2)
    assert rt.service.net_notice == ""  # 圏外になったら同意の案内は出さない


def test_health_failure_notice_is_separate_from_network(rt):
    from soikaxa.health import MSG_SOURCE_DOWN, HealthResult, save_result

    bootstrap.apply_health(rt)  # ファイルなし
    assert rt.service.health_notice == ""
    save_result(HealthResult(False, MSG_SOURCE_DOWN, None, 1.0), rt.data_dir / "health.json")
    bootstrap.monitor_step(rt, 2)
    assert rt.service.status().notice == MSG_SOURCE_DOWN
    bootstrap.monitor_step(rt, 3)  # 奇数周はネットワーク確認をしない
    assert rt.service.status().notice == MSG_SOURCE_DOWN


def test_speaker_loss_is_detected_while_playing_and_reconnected(rt):
    """FR-006: 再生中・待機中でも切断に気づいて再接続する。"""
    mac = "AA:BB:CC:DD:EE:FF"
    _music(rt)
    rt.store.update(known_speakers=[Speaker(mac, "Fake")])
    rt.service.dispatch(Event.NET_UP)
    assert bootstrap.connect_speaker(rt)
    rt.service.select_playlist("Mix", "local")
    assert rt.service.state.value == "PLAYING"
    rt.runner.on(["bluetoothctl", "info"], CommandResult(0, "Connected: no"))
    rt.runner.on(["bluetoothctl", "connect"], CommandResult(1, "Failed"))
    assert not bootstrap.connect_speaker(rt)
    assert rt.service.state.value == "ONLINE" and not rt.speaker_connected
    rt.runner.on(["bluetoothctl", "connect"], CommandResult(0, "Connection successful"))
    assert bootstrap.connect_speaker(rt)
    assert rt.service.state.value == "IDLE" and rt.speaker_connected


def test_autoplay_only_when_speaker_becomes_connected(rt):
    _music(rt, n=1)
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")], repeat=False)
    rt.service.dispatch(Event.NET_UP)
    rt.service.select_playlist("Mix", "local")
    rt.service.next()  # 1 曲だけ・リピートなしなので再生終了
    assert rt.service.status().now is None
    assert bootstrap.connect_speaker(rt) and rt.service.status().now is not None  # 接続時に自動再生
    rt.service.next()
    for _ in range(3):  # 接続したままの定期確認では、終わった再生を始め直さない
        assert bootstrap.connect_speaker(rt)
    assert rt.service.status().now is None


def test_speaker_connects_and_autoplays_without_upstream(rt):
    """上流 Wi-Fi が無くても（AP_ONLY）、スピーカーにつないでローカル音源を流す。"""
    _music(rt)
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")])
    rt.service.select_playlist("Mix", "local")
    rt.service.next()
    rt.service.next()
    rt.service.dispatch(Event.NET_DOWN)
    assert bootstrap.connect_speaker(rt)
    assert rt.service.state.value == "AP_ONLY" and rt.service.is_playing


def test_long_press_unlocks_pin_guard(rt):
    done = []
    bootstrap.on_gesture(rt, Gesture.SETUP_QR, lambda: done.append("show"), lambda: done.append("unlock"))
    assert done == ["unlock", "show"]
    bootstrap.on_gesture(rt, Gesture.SETUP_QR, lambda: done.append("show"))  # unlock 省略可


def test_connect_speaker_failure(rt):
    rt.service.dispatch(Event.NET_UP)
    rt.runner.on(["bluetoothctl", "info"], CommandResult(0, "Connected: no"))
    rt.runner.on(["bluetoothctl", "connect"], CommandResult(1, "Failed"))
    rt.store.update(known_speakers=[Speaker("AA:BB:CC:DD:EE:FF", "Fake")])
    assert not bootstrap.connect_speaker(rt) and rt.service.state.value == "ONLINE"


def test_on_gesture_list_button(rt):
    """LCD のリストボタンは軽いタップでも反応し、再生/停止にはしない。ほかの場所の軽いタップは無視。"""
    _music(rt, n=2)
    rt.service.select_playlist("Mix", "local")
    toggled = []

    class Lcd:
        def hits_mode_button(self, pos):
            return pos == (430, 270)

        def cycle_mode(self):
            toggled.append(1)

    lcd = Lcd()
    bootstrap.on_gesture(rt, Gesture.TAP, lambda: None, pos=(430, 270), lcd=lcd)
    bootstrap.on_gesture(rt, Gesture.TOGGLE, lambda: None, pos=(430, 270), lcd=lcd)
    assert toggled == [1, 1] and not rt.service.status().paused
    bootstrap.on_gesture(rt, Gesture.TAP, lambda: None, pos=(20, 20), lcd=lcd)
    assert toggled == [1, 1] and not rt.service.status().paused
    bootstrap.on_gesture(rt, Gesture.TOGGLE, lambda: None, pos=(20, 20), lcd=lcd)
    assert rt.service.status().paused


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


def test_idle_screen_shows_access_url(lcd):
    """待機中（曲なし）は、PC などから開けるようにオーナー画面の URL（AP の IP）を出す。"""
    c, svc, *_ = lcd
    for e in (Event.NET_DOWN, Event.FAIL, Event.RECOVER):
        svc.dispatch(e)
    name, view = c.view()
    assert name == "now" and view.title == "待機中"
    assert view.hint == "http://10.42.0.1/#owner"
    without = screens.render_now_playing(replace(view, hint="")).tobytes()
    assert c.compose()[1].tobytes() != without
    svc.select_playlist("p")
    assert c.view()[1].hint == ""  # 再生中はアーティスト名を出す


def _playing(lcd):
    c, svc, fb, jobs, clock, tr = lcd
    svc.dispatch(Event.NET_UP)
    svc.select_playlist("p")
    return c, svc, fb, jobs, clock


def test_mode_button_cycles_record_list_auto(lcd):
    """LCD のボタンを押すたびに レコード → リスト → 自動 → レコード。時間では勝手に変わらない。"""
    c, svc, fb, jobs, clock = _playing(lcd)
    v = c.view()[1]
    assert v.queue_rows is None and v.mode == "disc"
    clock.t += 120
    assert c.view()[1].queue_rows is None
    c.cycle_mode()
    v = c.view()[1]
    assert v.mode == "list" and v.queue_rows and v.queue_rows[0].title == "B"
    clock.t += 120
    assert c.view()[1].queue_rows is not None  # リストも勝手には戻らない
    c.cycle_mode()
    v = c.view()[1]
    assert v.mode == "auto" and v.queue_rows is None  # 自動はレコードから始める
    clock.t += AUTO_DISC_SEC + 1
    assert c.view()[1].queue_rows is not None
    clock.t += AUTO_LIST_SEC
    assert c.view()[1].queue_rows is None
    c.cycle_mode()
    assert c.view()[1].mode == "disc"


def test_list_button_hit_area(lcd):
    c, svc, fb, jobs, clock = _playing(lcd)
    x0, y0, x1, y1 = screens.MODE_BUTTON
    c.render_once()
    assert c.hits_mode_button(((x0 + x1) // 2, (y0 + y1) // 2))
    assert not c.hits_mode_button((20, 20)) and not c.hits_mode_button(None)
    c.show_setup_qr()
    c.render_once()
    assert not c.hits_mode_button(((x0 + x1) // 2, (y0 + y1) // 2))  # ボタンが出ていない画面では押せない


def test_added_list_returns_to_chosen_mode(lcd):
    c, svc, fb, jobs, clock = _playing(lcd)
    c.cycle_mode()
    c.view()
    svc.enqueue(Track(id="g", title="ゲストの曲", source="local"), added_by="Aさん")
    assert c.view()[1].queue_rows[0].highlight
    clock.t += LIST_ADDED_SEC + 1
    rows = c.view()[1].queue_rows
    assert rows is not None and not rows[0].highlight  # リストを選んでいたらリストのまま


def test_guest_add_shows_list_with_highlight_then_returns(lcd):
    c, svc, fb, jobs, clock = _playing(lcd)
    c.view()
    svc.enqueue(Track(id="g", title="ゲストの曲", source="local"), added_by="Aさん")
    rows = c.view()[1].queue_rows
    assert rows[0].title == "ゲストの曲" and rows[0].highlight and "Aさん" in rows[0].sub
    clock.t += LIST_ADDED_SEC - 1
    assert c.view()[1].queue_rows is not None
    clock.t += 2
    assert c.view()[1].queue_rows is None  # 追加表示のあとはレコードから数え直す


def test_record_is_not_drawn_over_list(lcd):
    c, svc, fb, jobs, clock = _playing(lcd)
    c.tick()
    jobs.pop()()
    c.tick()
    svc.enqueue(Track(id="g", title="ゲストの曲", source="local"), added_by="Aさん")
    c.tick()  # 全面描画（リスト）
    before = fb.path.read_bytes()
    for _ in range(5):
        c.tick()
    assert fb.path.read_bytes() == before


def test_list_without_upcoming_is_empty(lcd):
    c, svc, fb, jobs, clock, _ = lcd
    svc.dispatch(Event.NET_UP)
    assert c.view()[1].queue_rows is None
    c.cycle_mode()
    assert c.view()[1].queue_rows == ()  # 「次の曲はありません」を出す


def test_touch_cal_default_and_saved(rt):
    assert bootstrap.load_touch_cal(rt.store) == DEFAULT_CAL
    cal = TouchCal(True, (100, 3900), (3800, 200))
    rt.store.update(touch_cal=cal.to_dict())
    assert bootstrap.load_touch_cal(rt.store) == cal
    rt.store.update(touch_cal={"broken": 1})
    assert bootstrap.load_touch_cal(rt.store) == DEFAULT_CAL  # 壊れていたら既定値


def test_on_touch_routes_to_calibration_and_saves(rt):
    _music(rt, n=2)
    rt.service.select_playlist("Mix", "local")
    applied, cal = [], TouchCal(False, (3500, 300), (250, 3900))

    class Lcd:
        calibrating = True
        taps = []

        def calibration_tap(self, raw):
            self.taps.append(raw)
            return cal if len(self.taps) == 3 else None

        def hits_mode_button(self, pos):
            return False

        def cycle_mode(self):
            pass

    lcd = Lcd()
    for raw in [(1, 1), (2, 2)]:
        bootstrap.on_touch(rt, Gesture.TOGGLE, (5, 5), raw, lambda: None, lcd=lcd, apply_cal=applied.append)
    bootstrap.on_touch(rt, Gesture.NEXT, (5, 5), (9, 9), lambda: None, lcd=lcd, apply_cal=applied.append)
    assert lcd.taps == [(1, 1), (2, 2)] and not rt.service.status().paused  # 位置合わせ中は再生操作しない
    bootstrap.on_touch(rt, Gesture.TAP, (5, 5), (3, 3), lambda: None, lcd=lcd, apply_cal=applied.append)
    assert applied == [cal] and bootstrap.load_touch_cal(rt.store) == cal
    lcd.calibrating = False
    bootstrap.on_touch(rt, Gesture.TOGGLE, (5, 5), (3, 3), lambda: None, lcd=lcd, apply_cal=applied.append)
    assert rt.service.status().paused  # 位置合わせ後はふつうのタップ


CAL_RAWS = [(3280, 670), (520, 670), (3280, 3490)]  # 左上 → 右上 → 左下（左右が逆のパネル）


def test_touch_calibration_flow(lcd):
    c, svc, fb, jobs, clock = _playing(lcd)
    c.start_calibration()
    assert c.calibrating and c.compose()[0] == "calibrate"
    assert c.calibration_tap(CAL_RAWS[0]) is None and c.calibration_step == 1
    assert c.calibration_tap(CAL_RAWS[1]) is None and c.calibration_step == 2
    before = c.compose()[1].tobytes()
    cal = c.calibration_tap(CAL_RAWS[2])
    assert isinstance(cal, TouchCal) and not cal.swap and cal.x[0] > cal.x[1]  # 左右が逆と分かる
    assert not c.calibrating and c.compose()[0] == "calibrate"  # 「保存しました」を少し出す
    assert c.compose()[1].tobytes() != before
    clock.t += CAL_DONE_SEC + 1
    assert c.compose()[0] == "now"


def test_touch_calibration_retry_and_timeout(lcd):
    c, svc, fb, jobs, clock = _playing(lcd)
    c.start_calibration()
    for _ in range(3):
        assert c.calibration_tap((1000, 1000)) is None  # 同じ所を 3 回 → やり直し
    assert c.calibrating and c.calibration_step == 0 and "もう一度" in c.calibration_message
    clock.t += CAL_TIMEOUT_SEC + 1
    assert not c.calibrating and c.compose()[0] == "now"  # 放置したら元の画面に戻る
    assert c.calibration_tap((1, 1)) is None
