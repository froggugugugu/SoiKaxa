import json
import threading

import pytest
from soikaxa.adapters.base import Resolved
from soikaxa.fakes import FakeTransport
from soikaxa.player import MpvError, MpvPlayer, _Dispatcher


def test_play_sets_headers_loads_and_unpauses():
    tr = FakeTransport()
    p = MpvPlayer(tr)
    p.play(Resolved(uri="https://x/a", headers={"User-Agent": "UA"}))
    assert tr.sent[0] == ["set_property", "http-header-fields", ["User-Agent: UA"]]
    assert tr.sent[1] == ["loadfile", "https://x/a", "replace"]
    assert tr.props["pause"] is False


def test_controls_and_properties():
    tr = FakeTransport()
    p = MpvPlayer(tr)
    p.pause()
    assert p.is_paused()
    p.resume()
    assert not p.is_paused()
    p.toggle()
    assert p.is_paused()
    p.set_volume(150)
    assert tr.props["volume"] == 100
    assert p.position() is None  # 未再生
    tr.props["time-pos"] = 12.5
    tr.props["duration"] = 200
    assert p.position() == 12.5 and p.duration() == 200.0
    p.stop()
    assert tr.sent[-1] == ["stop"]


def test_command_error_raises():
    tr = FakeTransport()
    tr.fail.add("loadfile")
    with pytest.raises(MpvError):
        MpvPlayer(tr).play(Resolved(uri="x"))


def test_end_fires_only_for_current_entry_eof_or_error():
    tr = FakeTransport()
    p = MpvPlayer(tr)
    ends = []
    p.on_end(ends.append)
    p.play(Resolved(uri="a"))
    p.play(Resolved(uri="b"))  # a の end-file(stop) は無視
    assert ends == []
    tr.emit({"event": "end-file", "reason": "eof", "playlist_entry_id": 1})  # 古い曲
    assert ends == []
    tr.emit({"event": "property-change", "name": "x"})
    tr.finish("eof")
    assert ends == ["eof"]
    p.play(Resolved(uri="c"))
    tr.finish("error")
    assert ends == ["eof", "error"]


def test_stop_reason_never_fires():
    tr = FakeTransport()
    p = MpvPlayer(tr)
    ends = []
    p.on_end(ends.append)
    p.play(Resolved(uri="a"))
    tr.finish("stop")
    assert ends == []


def test_dispatcher_matches_request_and_events():
    d = _Dispatcher()
    events = []
    d.listeners.append(events.append)
    d.listeners.append(lambda m: 1 / 0)  # 失敗する購読者があっても止まらない
    rid, w = d.register()
    threading.Timer(
        0.01, lambda: d.feed(json.dumps({"request_id": rid, "error": "success"}).encode())
    ).start()
    assert d.wait(rid, w, 1)["error"] == "success"
    d.feed(b'{"event": "idle"}')
    d.feed(b"not json")
    d.feed(b'{"request_id": 999}')
    d.drain()
    assert events == [{"event": "idle"}]


def test_event_listener_may_send_commands_without_deadlock():
    """購読者が受信スレッドを待たせずに次のコマンドを送れること（実機のデッドロック回帰）。"""
    d = _Dispatcher()
    reader_free = threading.Event()

    def listener(msg):
        rid, w = d.register()
        # 受信側（このテストのメインスレッド）が応答を流せることを確認
        reader_free.set()
        assert d.wait(rid, w, 2)["error"] == "success"

    d.listeners.append(listener)
    d.feed(b'{"event": "end-file", "reason": "eof"}')  # feed は即座に戻る
    assert reader_free.wait(2)
    d.feed(json.dumps({"request_id": 1, "error": "success"}).encode())
    d.drain()


def test_dispatcher_timeout_and_close():
    d = _Dispatcher()
    rid, w = d.register()
    with pytest.raises(MpvError, match="timeout"):
        d.wait(rid, w, 0.01)
    rid2, w2 = d.register()
    d.close()
    with pytest.raises(MpvError, match="closed"):
        d.wait(rid2, w2, 1)
