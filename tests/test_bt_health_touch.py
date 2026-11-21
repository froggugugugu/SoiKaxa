import pytest
from soikaxa import bt, health
from soikaxa.adapters.base import AdapterError, Resolved
from soikaxa.fakes import FakeRunner
from soikaxa.runner import CommandResult
from soikaxa.touch import Gesture, TouchClassifier

MAC = "AA:BB:CC:DD:EE:FF"
DEVICES = (
    f"Device {MAC} JBL Flip\nDevice 11:22:33:44:55:66 11-22-33-44-55-66\nnoise\nDevice 77:88:99:AA:BB:CC Sony"
)
CONNECT = ["bluetoothctl", "--timeout", "15", "connect"]
PAIR = ["bluetoothctl", "--timeout", "20", "pair"]


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
    devs = bt.BluetoothManager(r).scan(seconds=3)
    assert [(d.mac, d.name, d.paired) for d in devs] == [
        (MAC, "JBL Flip", True),
        ("77:88:99:AA:BB:CC", "Sony", False),
    ]
    assert r.calls[0] == ["bluetoothctl", "--timeout", "3", "scan", "on"]


def test_paired_and_info():
    m = bt.BluetoothManager(bt_runner("Name: JBL Flip\nPaired: yes\nConnected: yes"))
    p = m.paired()
    assert p[0].connected and p[0].paired
    assert m.name_of(MAC) == "JBL Flip"


def test_pair_and_connect_flow():
    r = bt_runner().on(CONNECT, CommandResult(0, "Connection successful"))
    assert bt.BluetoothManager(r).pair_and_connect(MAC.lower())
    cmds = [c[1:] for c in r.calls]
    assert ["--timeout", "20", "pair", MAC] in cmds and ["trust", MAC] in cmds


def test_already_paired_skips_pair():
    r = bt_runner("Paired: yes\nConnected: yes").on(CONNECT, CommandResult(0))
    assert bt.BluetoothManager(r).pair_and_connect(MAC)
    assert not r.called(*PAIR)


def test_pair_failure_and_already_exists():
    r = bt_runner().on(PAIR, CommandResult(1, "Failed to pair"))
    assert not bt.BluetoothManager(r).pair_and_connect(MAC)
    r2 = bt_runner().on(PAIR, CommandResult(1, "org.bluez.Error.AlreadyExists"))
    r2.on(CONNECT, CommandResult(0, "Connection successful"))
    assert bt.BluetoothManager(r2).pair_and_connect(MAC)


def test_connect_failure_and_disconnect():
    m = bt.BluetoothManager(bt_runner().on(CONNECT, CommandResult(1, "Failed")))
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


def test_short_touch_ignored():
    c = TouchClassifier()
    c.press(0)
    assert c.release(0.29) is None


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
    assert bt.BluetoothManager(r).remove(MAC.lower())
    assert r.calls[-1] == ["bluetoothctl", "remove", MAC]
