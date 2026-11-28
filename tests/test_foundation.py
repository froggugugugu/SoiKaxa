import json
import os
import socket
import stat

import pytest
from soikaxa import auth, watchdog
from soikaxa.config import LastPlaylist, Settings, SettingsStore, Speaker, data_dir
from soikaxa.fakes import FakeRunner
from soikaxa.runner import CommandResult, SubprocessRunner

# ---------- runner ----------


def test_subprocess_runner_ok_and_missing():
    r = SubprocessRunner()
    assert r.run(["echo", "hi"]).stdout.strip() == "hi"
    missing = r.run(["soikaxa-no-such-command"])
    assert missing.returncode == 127 and not missing.ok


def test_subprocess_runner_timeout():
    assert SubprocessRunner().run(["sleep", "2"], timeout=0.1).returncode == 124


def test_fake_runner_prefix_and_priority():
    f = FakeRunner().on(["nmcli"], CommandResult(0, "a")).on(["nmcli", "dev"], CommandResult(0, "b"))
    assert f.run(["nmcli", "dev", "x"]).stdout == "b"
    assert f.run(["nmcli", "con"]).stdout == "a"
    assert f.run(["other"]).stdout == ""
    assert f.called("nmcli") == [["nmcli", "dev", "x"], ["nmcli", "con"]]
    f.on(["dyn"], lambda argv: CommandResult(0, argv[-1]))
    assert f.run(["dyn", "z"]).stdout == "z"


# ---------- config ----------


def test_data_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SOIKAXA_DATA_DIR", str(tmp_path))
    assert data_dir() == tmp_path
    monkeypatch.delenv("SOIKAXA_DATA_DIR")
    assert str(data_dir()) == "/var/lib/soikaxa"


def test_store_roundtrip_and_permissions(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.load() == Settings()
    s = store.update(
        volume=80, last_playlist=LastPlaylist("local", "pl"), known_speakers=[Speaker("AA", "x")]
    )
    assert s.volume == 80 and s.last_playlist == LastPlaylist("local", "pl")
    mode = stat.S_IMODE(os.stat(tmp_path / "settings.json").st_mode)
    assert mode == 0o600
    again = SettingsStore(tmp_path / "settings.json").load()
    assert again == s


def test_store_saved_playlists_roundtrip_and_skips_broken(tmp_path):
    from soikaxa.config import MAX_SAVED_PLAYLISTS, SavedPlaylist

    p = tmp_path / "settings.json"
    store = SettingsStore(p)
    s = store.update(saved_playlists=[SavedPlaylist("ytmusic", "PL1", "Jazz")])
    assert (
        SettingsStore(p).load().saved_playlists
        == s.saved_playlists
        == (SavedPlaylist("ytmusic", "PL1", "Jazz"),)
    )
    many = [{"adapter": "a", "id": str(i)} for i in range(MAX_SAVED_PLAYLISTS + 5)]
    p.write_text(json.dumps({"saved_playlists": [{"id": "no-adapter"}, "x", *many]}))
    got = SettingsStore(p).load().saved_playlists
    assert len(got) == MAX_SAVED_PLAYLISTS and got[0] == SavedPlaylist("a", "0", "")


def test_store_backward_compat_unknown_missing_and_clamp(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"volume": 999, "unknown_key": 1, "guest_queue_limit": 0}))
    s = SettingsStore(p).load()
    assert s.volume == 100 and s.guest_queue_limit == 1 and s.autoplay is True


@pytest.mark.parametrize("content", ["{broken", "[1,2]", json.dumps({"volume": "x"})])
def test_store_corrupt_file_falls_back_to_defaults(tmp_path, content):
    p = tmp_path / "settings.json"
    p.write_text(content)
    assert SettingsStore(p).load() == Settings()


def test_store_reset_keeps_ap_credentials(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.update(ap_ssid="soikaxa-1234", ap_password="pw12345678", owner_pin_hash="h", volume=10)
    s = store.reset()
    assert s.ap_ssid == "soikaxa-1234" and s.ap_password == "pw12345678"
    assert s.owner_pin_hash is None and s.volume == 60


# ---------- auth ----------


def test_pin_hash_and_verify():
    h = auth.hash_pin("123456")
    assert "123456" not in h
    assert auth.verify_pin("123456", h)
    assert not auth.verify_pin("123457", h)
    assert not auth.verify_pin("123456", None)
    assert not auth.verify_pin("123456", "garbage")
    assert not auth.verify_pin("123456", "md5$1$1$1$AA==$AA==")
    assert auth.hash_pin("123456") != h  # ソルト


@pytest.mark.parametrize("bad", ["1234", "12345", "123456789", "12a456", "", " 123456"])
def test_pin_validation(bad):
    with pytest.raises(auth.InvalidPin):
        auth.hash_pin(bad)


def test_pin_guard_lockout_and_recovery():
    now = [1000.0]
    g = auth.PinGuard(max_attempts=3, lockout_sec=60, clock=lambda: now[0])
    assert g.attempt(False) is False
    assert g.attempt(True) is True  # 成功でリセット
    for _ in range(3):
        g.attempt(False)
    with pytest.raises(auth.PinLocked) as e:
        g.check()
    assert e.value.retry_after == pytest.approx(60)
    with pytest.raises(auth.PinLocked):
        g.attempt(True)  # ロック中は正しい PIN でも拒否
    now[0] += 61
    assert g.attempt(True) is True


def test_pin_guard_lockout_doubles_and_reset_unlocks():
    now = [0.0]
    g = auth.PinGuard(max_attempts=1, lockout_sec=10, clock=lambda: now[0], max_lockout_sec=25)
    expected = [10, 20, 25, 25]  # 2 倍ずつ、上限あり
    for want in expected:
        g.attempt(False)
        with pytest.raises(auth.PinLocked) as e:
            g.check()
        assert e.value.retry_after == pytest.approx(want)
        now[0] += want + 1
    g.attempt(False)
    g.reset()  # 本体の長押し
    assert g.attempt(True) is True
    g.attempt(False)
    with pytest.raises(auth.PinLocked) as e:
        g.check()
    assert e.value.retry_after == pytest.approx(10)  # 成功でロック時間も元に戻る


def test_setup_code():
    c = auth.SetupCode()
    assert c.current is None and not c.verify("000000")
    code = c.ensure()
    assert len(code) == 6 and code.isdigit() and c.ensure() == code
    assert c.verify(code) and not c.verify("x" + code[1:]) and not c.verify(None)
    assert c.regenerate() == c.current
    c.clear()
    assert c.current is None and not c.verify(code)


def test_session_store_csrf_and_expiry():
    now = [0.0]
    s = auth.SessionStore(ttl_sec=10, clock=lambda: now[0])
    sid, csrf = s.create()
    assert s.check(sid) and s.check_csrf(sid, csrf) and s.csrf_for(sid) == csrf
    assert not s.check_csrf(sid, "wrong") and not s.check_csrf(sid, None)
    assert not s.check(None) and not s.check("nope")
    now[0] = 11
    assert not s.check(sid)
    sid2, _ = s.create()
    s.revoke(sid2)
    assert not s.check(sid2)
    sid3, _ = s.create()
    s.revoke_all()
    assert not s.check(sid3)


# ---------- watchdog ----------


def test_notify_noop_without_socket():
    assert watchdog.notify("READY=1", env={}) is False


def test_notify_sends_datagram():
    import tempfile

    d = tempfile.mkdtemp(prefix="sk", dir="/tmp")  # AF_UNIX のパス長制限を避ける
    path = os.path.join(d, "n.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    try:
        assert watchdog.notify("WATCHDOG=1", env={"NOTIFY_SOCKET": path})
        assert srv.recv(64) == b"WATCHDOG=1"
    finally:
        srv.close()
        os.unlink(path)
        os.rmdir(d)


def test_watchdog_interval():
    assert watchdog.watchdog_interval({"WATCHDOG_USEC": "30000000"}) == 15
    assert watchdog.watchdog_interval({}) == 10
    assert watchdog.watchdog_interval({"WATCHDOG_USEC": "x"}) == 10


def test_watchdog_beat_respects_health():
    sent: list[str] = []
    ok = [True]
    w = watchdog.Watchdog(interval=0.01, healthy=lambda: ok[0], send=lambda m: sent.append(m) or True)
    assert w.beat() and sent == ["WATCHDOG=1"]
    ok[0] = False
    assert not w.beat() and len(sent) == 1


def test_watchdog_thread_runs():
    import threading

    hit = threading.Event()
    w = watchdog.Watchdog(interval=0.01, send=lambda m: hit.set() or True)
    w.start()
    assert hit.wait(1)
    w.stop()
