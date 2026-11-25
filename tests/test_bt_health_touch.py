import pytest
from soikaxa import bt, health
from soikaxa.adapters.base import AdapterError, Resolved
from soikaxa.fakes import FakeRunner
from soikaxa.runner import CommandResult
from soikaxa.touch import (
    CAL_TARGETS,
    DEFAULT_CAL,
    Gesture,
    TouchCal,
    TouchClassifier,
    TouchTracker,
    solve_calibration,
)

MAC = "AA:BB:CC:DD:EE:FF"
DEVICES = (
    f"Device {MAC} JBL Flip\nDevice 11:22:33:44:55:66 11-22-33-44-55-66\nnoise\nDevice 77:88:99:AA:BB:CC Sony"
)
CONNECT = ["bluetoothctl", "--timeout", "15", "connect"]
PAIR = ["bluetoothctl", "--timeout", "20", "pair"]


def mgr(r, sleeps=None):
    """待ち時間（ペアリング直後・再試行の間）を実際には眠らず記録する。"""
    return bt.BluetoothManager(r, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def bt_runner(info="Paired: no\nConnected: no\nName: JBL Flip"):
    return (
        FakeRunner()
        .on(["bluetoothctl", "devices"], CommandResult(0, DEVICES))
        .on(["bluetoothctl", "devices", "Paired"], CommandResult(0, f"Device {MAC} JBL Flip"))
        .on(["bluetoothctl", "info"], CommandResult(0, info))
    )


def test_validate_mac():
    assert bt.validate_mac(" aa:bb:cc:dd:ee:ff ") == MAC
    for bad in ["", "AA:BB", "AA:BB:CC:DD:EE:FG", "AA:BB:CC:DD:EE:FF; reboot"]:
        with pytest.raises(bt.InvalidMac):
            bt.validate_mac(bad)


def test_scan_lists_named_devices_with_paired_flag():
    r = bt_runner()
    devs = mgr(r).scan(seconds=3)
    assert [(d.mac, d.name, d.paired) for d in devs] == [
        (MAC, "JBL Flip", True),
        ("77:88:99:AA:BB:CC", "Sony", False),
    ]
    assert r.calls[0] == ["bluetoothctl", "--timeout", "3", "scan", "on"]


def test_paired_and_info():
    m = mgr(bt_runner("Name: JBL Flip\nPaired: yes\nConnected: yes"))
    p = m.paired()
    assert p[0].connected and p[0].paired
    assert m.name_of(MAC) == "JBL Flip"


PAIR_OK = CommandResult(0, "Attempting to pair with AA:BB:CC:DD:EE:FF\nPairing successful")


def test_pair_and_connect_flow():
    r = bt_runner().on(CONNECT, CommandResult(0, "Connection successful")).on(PAIR, PAIR_OK)
    assert mgr(r).pair_and_connect(MAC.lower()) == "ok"
    cmds = [c[1:] for c in r.calls]
    assert ["--timeout", "20", "pair", MAC] in cmds and ["trust", MAC] in cmds


def test_already_paired_skips_pair():
    r = bt_runner("Paired: yes\nConnected: yes").on(CONNECT, CommandResult(0))
    assert mgr(r).pair_and_connect(MAC) == "ok"
    assert not r.called(*PAIR)


def _stateful_runner(pair_result):
    """remove / pair で `info` の Paired が変わる BlueZ を模す。"""
    state = {"paired": True}

    def on_info(argv):
        return CommandResult(0, f"Name: JBL\nPaired: {'yes' if state['paired'] else 'no'}\nConnected: no")

    def on_remove(argv):
        state["paired"] = False
        return CommandResult(0)

    def on_pair(argv):
        if "Pairing successful" in pair_result.stdout:
            state["paired"] = state["repaired"] = True
        return pair_result

    def on_connect(argv):  # 古い鍵ではつながらず、組み直した後はつながる
        ok = state.get("repaired")
        return CommandResult(0, "Connection successful" if ok else "Failed to connect")

    r = bt_runner().on(["bluetoothctl", "info"], on_info).on(["bluetoothctl", "remove"], on_remove)
    r.on(PAIR, on_pair)
    r.on(CONNECT, on_connect)
    return r


def test_stale_pairing_is_removed_and_repaired():
    """スピーカー側がペアリングを忘れた（リセット・別機器とペアリング）ときは、本体側も消して組み直す。"""
    r = _stateful_runner(PAIR_OK)
    assert mgr(r).pair_and_connect(MAC) == "ok"
    cmds = [c[1:] for c in r.calls]
    order = [cmds.index(x) for x in (["remove", MAC], ["--timeout", "20", "pair", MAC], ["trust", MAC])]
    assert order == sorted(order)


def test_stale_pairing_repair_gives_up_when_pair_fails():
    r = _stateful_runner(CommandResult(0, "Failed to pair: org.bluez.Error.AuthenticationFailed"))
    assert mgr(r).pair_and_connect(MAC) == "pair_failed"
    assert r.called("bluetoothctl", "remove") and not r.called("bluetoothctl", "trust")


@pytest.mark.parametrize("code", [0, 1])
def test_pair_failure_is_detected_from_output_not_exit_code(code):
    """`bluetoothctl --timeout` は失敗しても終了コード 0 を返すことがある。失敗なら trust しない。"""
    r = bt_runner().on(PAIR, CommandResult(code, "Failed to pair: org.bluez.Error.AuthenticationCanceled"))
    assert mgr(r).pair_and_connect(MAC) == "pair_failed"
    assert not r.called("bluetoothctl", "trust") and not r.called(*CONNECT)


def test_pair_without_message_trusts_bluez_state():
    r = bt_runner().on(PAIR, CommandResult(0, ""))  # 出力が取れなくても Paired: yes なら成功とみなす
    r.on(["bluetoothctl", "info"], CommandResult(0, "Paired: yes\nConnected: no"))
    r.on(CONNECT, CommandResult(0, "Connection successful"))
    assert mgr(r).pair_and_connect(MAC) == "ok"


def test_pair_target_gone_is_not_found():
    """スキャンから時間がたち BlueZ が一覧から消した機器。"""
    r = bt_runner("Device AA:BB:CC:DD:EE:FF not available")
    r.on(PAIR, CommandResult(0, "Device AA:BB:CC:DD:EE:FF not available"))
    assert mgr(r).pair_and_connect(MAC) == "not_found"
    assert not r.called("bluetoothctl", "trust")


def test_connect_after_pair_waits_and_retries():
    """ペアリング直後の connect はスピーカー側の接続とぶつかりやすい。待ってから、失敗しても数回試す。"""
    tries = []

    def on_connect(argv):
        tries.append(argv)
        busy = "Failed to connect: org.bluez.Error.InProgress br-connection-busy"
        return CommandResult(0, busy if len(tries) < 3 else "Connection successful")

    r = bt_runner().on(PAIR, PAIR_OK).on(CONNECT, on_connect)
    sleeps = []
    assert mgr(r, sleeps).pair_and_connect(MAC) == "ok"
    assert len(tries) == 3
    cmds = [c[1:] for c in r.calls]
    assert cmds.index(["trust", MAC]) < cmds.index(["--timeout", "15", "connect", MAC])
    assert sleeps[0] == bt.BluetoothManager.PAIR_SETTLE_SEC and len(sleeps) == 3


def test_retry_succeeds_when_speaker_connected_by_itself():
    """待つ間にスピーカー側からつながったら、もう connect を送らずに成功とする。"""
    state = {"n": 0}

    def on_info(argv):
        return CommandResult(0, f"Paired: yes\nConnected: {'yes' if state['n'] else 'no'}")

    def on_connect(argv):
        state["n"] += 1
        return CommandResult(0, "Failed to connect: org.bluez.Error.InProgress")

    r = bt_runner().on(["bluetoothctl", "info"], on_info).on(CONNECT, on_connect)
    assert mgr(r).pair_and_connect(MAC) == "ok"
    assert state["n"] == 1 and not r.called("bluetoothctl", "remove")


def test_known_speaker_is_not_removed_after_one_failure():
    """記憶済みの鍵は 1 回の失敗では消さない（電源投入直後の一時的な失敗のことがある）。"""
    n = []

    def on_connect(argv):
        n.append(1)
        return CommandResult(0, "Connection successful" if len(n) == 2 else "Failed to connect")

    r = bt_runner("Paired: yes\nConnected: no").on(CONNECT, on_connect)
    assert mgr(r).pair_and_connect(MAC) == "ok"
    assert not r.called("bluetoothctl", "remove") and not r.called(*PAIR)


def test_connect_uses_output_not_exit_code():
    r = bt_runner().on(CONNECT, CommandResult(1, "Connection successful"))
    assert mgr(r).connect(MAC)


def test_failure_output_is_logged(caplog):
    failed = CommandResult(0, "Failed to connect: org.bluez.Error.Failed")
    r = bt_runner().on(PAIR, PAIR_OK).on(CONNECT, failed)
    with caplog.at_level("WARNING", logger="soikaxa.bt"):
        assert mgr(r).pair_and_connect(MAC) == "connect_failed"
    assert "org.bluez.Error.Failed" in caplog.text


def test_manual_connect_blocks_auto_connect():
    """オーナーの接続中は busy になり、監視ループの connect はロックで後回しになる。"""
    import threading

    started, release = threading.Event(), threading.Event()
    seen = {}

    def on_pair(argv):
        started.set()
        release.wait(5)
        return PAIR_OK

    r = bt_runner().on(PAIR, on_pair).on(CONNECT, CommandResult(0, "Connection successful"))
    m = mgr(r)
    t = threading.Thread(target=lambda: seen.setdefault("manual", m.pair_and_connect(MAC)))
    t.start()
    assert started.wait(5) and m.busy
    auto = threading.Thread(target=lambda: seen.setdefault("auto", m.connect(MAC)))
    auto.start()
    auto.join(0.2)
    assert auto.is_alive()  # 手動の操作が終わるまで connect を送らない
    assert not r.called(*CONNECT)
    release.set()
    t.join(5)
    auto.join(5)
    assert seen == {"manual": "ok", "auto": True} and not m.busy


def test_paired_but_connect_fails():
    r = bt_runner().on(PAIR, PAIR_OK).on(CONNECT, CommandResult(0, "Failed to connect: org.bluez.Error"))
    assert mgr(r).pair_and_connect(MAC) == "connect_failed"
    assert r.called("bluetoothctl", "trust")


def test_already_exists_counts_as_paired():
    r = bt_runner().on(PAIR, CommandResult(1, "Failed to pair: org.bluez.Error.AlreadyExists"))
    r.on(CONNECT, CommandResult(0, "Connection successful"))
    assert mgr(r).pair_and_connect(MAC) == "ok"


def test_connect_failure_and_disconnect():
    m = mgr(bt_runner().on(CONNECT, CommandResult(1, "Failed")))
    assert not m.connect(MAC)
    assert m.disconnect(MAC)


# ---------- health ----------


class Probe:
    name = "probe"

    def __init__(self, fail=False):
        self.fail = fail

    def resolve(self, track_id):
        if self.fail:
            raise AdapterError("x")
        return Resolved(uri="u")


def test_temperature_sources(tmp_path):
    assert health.read_temperature(FakeRunner().on(["vcgencmd"], CommandResult(0, "temp=48.3'C"))) == 48.3
    f = tmp_path / "t"
    f.write_text("51234\n")
    missing = FakeRunner().on(["vcgencmd"], CommandResult(127))
    assert health.read_temperature(missing, f) == pytest.approx(51.234)
    assert health.read_temperature(missing, tmp_path / "none") is None


def test_check_source_and_roundtrip(tmp_path):
    assert health.check_source(Probe(), None)[0] is True
    assert health.check_source(Probe(), "t") == (True, "OK")
    ok, msg = health.check_source(Probe(fail=True), "t")
    assert not ok and health.MSG_SOURCE_DOWN in msg
    res = health.run_healthcheck(Probe(fail=True), "t", FakeRunner(), clock=lambda: 9.0)
    assert res.checked_at == 9.0 and not res.ok
    p = tmp_path / "health.json"
    health.save_result(res, p)
    assert health.load_result(p) == res
    assert health.load_result(tmp_path / "missing.json") is None


# ---------- touch ----------


def test_short_touch_is_tap_not_toggle():
    """0.3 秒未満は再生/停止にしない（誤タッチ防止）。ボタン用の軽いタップとしてだけ返す。"""
    c = TouchClassifier()
    c.press(0)
    assert c.release(0.29) == Gesture.TAP
    c.press(1)
    assert c.release(1.03) is None  # 0.05 秒未満はノイズ


FULL = TouchCal(swap=False, x=(0, 4095), y=(0, 4095))


@pytest.mark.parametrize(
    ("cal", "raw", "want"),
    [
        (FULL, (0, 0), (0, 0)),
        (FULL, (4095, 4095), (479, 319)),
        (FULL, (2048, 1024), (240, 80)),
        (TouchCal(False, (4095, 0), (0, 4095)), (4095, 0), (0, 0)),  # 左右が逆
        (TouchCal(True, (0, 4095), (0, 4095)), (4095, 0), (0, 319)),  # 生の X が画面の縦
        (FULL, (-50, 5000), (0, 319)),  # 範囲外は端に丸める
    ],
)
def test_touch_cal_map(cal, raw, want):
    assert cal.map(*raw, size=(480, 320)) == want


def _raws_for(cal_like):
    """画面の座標 → 生の座標（テスト用に校正の逆算をする）。"""
    swap, (x0, x1), (y0, y1) = cal_like
    out = []
    for sx, sy in CAL_TARGETS:
        rx = round(x0 + (x1 - x0) * sx / 479)
        ry = round(y0 + (y1 - y0) * sy / 319)
        out.append((ry, rx) if swap else (rx, ry))
    return out


@pytest.mark.parametrize("truth", [(False, (3570, 250), (200, 3960)), (True, (300, 3800), (3900, 150))])
def test_solve_calibration_recovers_orientation_and_range(truth):
    cal = solve_calibration(_raws_for(truth))
    assert cal.swap == truth[0]
    for got, want in ((cal.x, truth[1]), (cal.y, truth[2])):
        assert all(abs(g - w) <= 2 for g, w in zip(got, want, strict=True))
    for (sx, sy), raw in zip(CAL_TARGETS, _raws_for(truth), strict=True):
        px, py = cal.map(*raw, size=(480, 320))
        assert abs(px - sx) <= 1 and abs(py - sy) <= 1


def test_solve_calibration_rejects_bad_taps():
    with pytest.raises(ValueError):
        solve_calibration([(1000, 1000), (1010, 1005), (1000, 1010)])  # 同じ所を 3 回
    with pytest.raises(ValueError):
        solve_calibration([(1000, 1000)])


def test_touch_cal_dict_roundtrip_and_validation():
    assert TouchCal.from_dict(DEFAULT_CAL.to_dict()) == DEFAULT_CAL
    for bad in (None, {}, {"swap": False, "x": [1, 1], "y": [0, 10]}, {"swap": "x", "x": "ab", "y": [0, 1]}):
        assert TouchCal.from_dict(bad) is None
    assert DEFAULT_CAL.map(800, 3500, size=(480, 320))[0] > 388  # 右下のボタンに当たる（ログの実測値）


def test_tap_toggles():
    c = TouchClassifier()
    c.press(0)
    assert c.tick(1.0) is None
    assert c.release(1.0) == Gesture.TOGGLE


def test_long_press_next_fires_once_while_held():
    c = TouchClassifier()
    c.press(0)
    assert c.tick(1.5) == Gesture.NEXT
    assert c.tick(2.0) is None
    assert c.release(3.0) is None  # 離しても TOGGLE にならない


def test_setup_qr_after_10s():
    c = TouchClassifier()
    c.press(0)
    assert c.tick(1.6) == Gesture.NEXT
    assert c.tick(10.0) == Gesture.SETUP_QR
    assert c.tick(11) is None
    assert c.release(12) is None


def test_release_without_tick_still_reports_threshold():
    c = TouchClassifier()
    c.press(0)
    assert c.release(2.0) == Gesture.NEXT
    assert c.release(3.0) is None
    assert c.tick(3.0) is None


def test_press_while_pressed_keeps_first_timestamp():
    c = TouchClassifier()
    c.press(0)
    c.press(1.4)
    assert c.pressed
    assert c.tick(1.5) == Gesture.NEXT


def test_bt_remove():
    r = bt_runner()
    assert mgr(r).remove(MAC.lower())
    assert r.calls[-1] == ["bluetoothctl", "remove", MAC]


def test_tracker_reuses_last_position_when_panel_sends_no_new_coordinates():
    """同じ所を続けて押すと、Linux の入力層は前回と同じ座標を送らない。前回の座標を使う（ボタンの連打）。"""
    t = TouchTracker(size=(480, 320), cal=FULL)
    t.down(0.0)
    assert t.up(0.1) == (Gesture.TAP, None, None)  # まだ一度も座標が来ていない
    t.down(1.0)
    t.abs_x(3800)
    t.abs_y(3500)
    assert t.up(1.1) == (Gesture.TAP, (444, 273), (3800, 3500))
    t.down(2.0)  # 座標イベントなし（同じ位置）
    assert t.up(2.4) == (Gesture.TOGGLE, (444, 273), (3800, 3500))
    t.cal = TouchCal(False, (4095, 0), (0, 4095))  # 位置合わせの結果は次のタッチから効く
    t.down(3.0)
    assert t.up(3.1)[1] == (35, 273)
