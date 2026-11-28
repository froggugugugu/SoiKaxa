import pytest
from soikaxa.adapters.base import AdapterError, Playlist, Resolved, Track
from soikaxa.config import LastPlaylist, SettingsStore
from soikaxa.daemon import MSG_FALLBACK, PlayerService
from soikaxa.fakes import FakeTransport
from soikaxa.player import MpvPlayer
from soikaxa.queue import QueueLimitExceeded
from soikaxa.state import Event, State


class FakeAdapter:
    def __init__(self, name, playlists, fail=(), expires=None):
        self.name = name
        self._pls = playlists  # {pid: [track ids]}
        self.fail = set(fail)
        self.expires = expires
        self.resolved: list[str] = []

    def list_playlists(self):
        return [Playlist(id=p, title=p, source=self.name) for p in self._pls]

    def list_tracks(self, pid):
        if pid not in self._pls:
            raise AdapterError("nope")
        return [Track(id=t, title=t.upper(), source=self.name) for t in self._pls[pid]]

    def search(self, q, limit=20):
        return [Track(id=f"s-{q}", title=q, source=self.name)][:limit]

    def resolve(self, tid):
        self.resolved.append(tid)
        if tid in self.fail or "*" in self.fail:
            raise AdapterError(tid)
        return Resolved(uri=f"{self.name}://{tid}", expires_at=self.expires)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, remote=None, local=None, active="remote", **kw):
    tr = FakeTransport()
    store = SettingsStore(tmp_path / "s.json")
    store.update(active_adapter=active)
    adapters = {"local": local or FakeAdapter("local", {"lp": ["l1", "l2"]})}
    if remote is not None:
        adapters["remote"] = remote
    jobs = []
    kw.setdefault("run_async", jobs.append)
    svc = PlayerService(adapters=adapters, player=MpvPlayer(tr), store=store, **kw)
    svc.jobs = jobs
    return svc, tr


def loaded(tr):
    return [c[1] for c in tr.sent if c[0] == "loadfile"]


def run_jobs(svc):
    while svc.jobs:
        svc.jobs.pop(0)()


def test_requires_fallback(tmp_path):
    with pytest.raises(ValueError):
        PlayerService(adapters={}, player=MpvPlayer(FakeTransport()), store=SettingsStore(tmp_path / "s"))


def test_continuous_playback_and_end_goes_idle(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a", "b"]}))
    svc.store.update(repeat=False)
    for e in (Event.NET_UP, Event.BT_CONNECTED):
        svc.dispatch(e)
    svc.select_playlist("p")
    assert loaded(tr) == ["remote://a"] and svc.state == State.PLAYING
    tr.finish()
    assert loaded(tr)[-1] == "remote://b"
    tr.finish()
    assert svc.status().now is None and svc.state == State.IDLE
    assert ["stop"] in tr.sent
    assert svc.store.load().last_playlist == LastPlaylist("remote", "p")


def test_repeat_is_on_by_default_and_loops_playlist(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a", "b"]}))
    assert svc.store.load().repeat
    svc.select_playlist("p")
    tr.finish()
    assert [u.id for u in svc.status().upcoming] == ["a", "b"]  # 最後の曲の次は先頭（1 周分）
    tr.finish()
    assert loaded(tr)[-1] == "remote://a" and svc.status().now.id == "a"
    svc.store.update(repeat=False)  # 再生中に切り替えても次の判定から効く
    tr.finish()
    tr.finish()
    assert svc.status().now is None


def test_queue_has_priority_over_playlist(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a", "b"]}))
    svc.select_playlist("p")
    svc.enqueue(Track(id="g", title="G", source="remote"), added_by="Aさん", guest_id="x")
    st = svc.status()
    assert [u.id for u in st.upcoming] == ["g", "b", "a"] and st.upcoming[0].added_by == "Aさん"
    svc.next()
    assert loaded(tr)[-1] == "remote://g"
    assert svc.status().now.added_by == "Aさん" and svc.status().now.origin == "queue"


def test_enqueue_when_idle_starts_playback_and_limit(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {}))
    svc.enqueue(Track(id="g", title="G", source="remote"), guest_id="x", limit=1)
    assert loaded(tr) == ["remote://g"]
    svc.enqueue(Track(id="h", title="H", source="remote"), guest_id="x", limit=1)
    with pytest.raises(QueueLimitExceeded):
        svc.enqueue(Track(id="i", title="I", source="remote"), guest_id="x", limit=1)


def test_resolve_failure_skips(tmp_path):
    remote = FakeAdapter("remote", {"p": ["a", "b", "c"]}, fail={"a"})
    svc, tr = make(tmp_path, remote)
    svc.select_playlist("p")
    assert loaded(tr) == ["remote://b"]
    assert svc.status().notice == ""


def test_consecutive_failures_switch_to_local_fallback(tmp_path):
    remote = FakeAdapter("remote", {"p": ["a", "b", "c", "d"]}, fail={"*"})
    svc, tr = make(tmp_path, remote)
    svc.select_playlist("p")
    assert loaded(tr) == ["local://l1"]
    st = svc.status()
    assert st.adapter == "local" and st.notice == MSG_FALLBACK
    assert remote.resolved == ["a", "b", "c"]  # 3 回で切替
    # オーナーが音源を選び直すとフォールバックは解除される
    remote.fail.clear()
    svc.select_playlist("p", "remote")
    assert svc.status().adapter == "remote" and svc.status().notice == ""


def test_fallback_without_playlists_goes_idle(tmp_path):
    remote = FakeAdapter("remote", {"p": ["a", "b", "c"]}, fail={"*"})
    svc, tr = make(tmp_path, remote, local=FakeAdapter("local", {}))
    svc.select_playlist("p")
    assert svc.status().now is None and loaded(tr) == []


def test_mpv_error_is_skipped(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    tr.fail.add("loadfile")
    svc.select_playlist("p")
    assert svc.status().now is None


def test_prefetch_one_and_reuse(tmp_path):
    remote = FakeAdapter("remote", {"p": ["a", "b", "c"]})
    svc, tr = make(tmp_path, remote)
    svc.select_playlist("p")
    assert len(svc.jobs) == 1
    run_jobs(svc)
    assert remote.resolved == ["a", "b"]  # 次曲 1 曲だけ
    tr.finish()
    assert remote.resolved == ["a", "b"]  # 先読み結果を再利用
    assert loaded(tr)[-1] == "remote://b"


def test_prefetch_expired_is_resolved_again(tmp_path):
    clock = Clock()
    remote = FakeAdapter("remote", {"p": ["a", "b"]}, expires=clock.t + 100)
    svc, tr = make(tmp_path, remote, clock=clock)
    svc.select_playlist("p")
    run_jobs(svc)
    clock.t += 80  # 期限 30 秒前を過ぎた
    tr.finish()
    assert remote.resolved == ["a", "b", "b"]


def test_prefetch_failure_is_ignored_and_mismatch_discarded(tmp_path):
    remote = FakeAdapter("remote", {"p": ["a", "b", "c"]})
    svc, tr = make(tmp_path, remote)
    svc.select_playlist("p")
    remote.fail.add("b")
    run_jobs(svc)
    remote.fail.clear()
    svc.enqueue(Track(id="g", title="G", source="remote"), play_next=True)
    run_jobs(svc)
    svc.next()
    assert loaded(tr)[-1] == "remote://g"


def test_toggle_pause_resume_and_idle_toggle(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    svc.toggle()  # 何もない
    assert loaded(tr) == []
    svc.queue.set_playlist("p", [Track(id="a", title="A", source="remote")])
    svc.toggle()  # キューがあれば再生開始
    assert loaded(tr) == ["remote://a"]
    svc.toggle()
    assert svc.status().paused and tr.props["pause"] is True
    svc.toggle()
    assert not svc.status().paused
    svc._current = None
    svc.pause()
    svc.resume()


def test_restart_seeks_to_start_of_current_track(tmp_path):
    """スピーカーの「前の曲」ボタン: 今の曲を頭から（本体に前の曲の機能はない）。

    何も流れていなければ何もしない。
    """
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    svc.restart()
    assert not [c for c in tr.sent if c[0] == "seek"]
    svc.queue.set_playlist("p", [Track(id="a", title="A", source="remote")])
    svc.toggle()
    svc.restart()
    assert ["seek", 0, "absolute"] in tr.sent
    assert loaded(tr) == ["remote://a"]  # 曲は変えない


def test_volume_remove_move_listeners_and_search(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    calls = []
    svc.on_change(lambda: calls.append(1))
    svc.on_change(lambda: 1 / 0)  # 失敗する購読者があっても止まらない
    svc.set_volume(130)
    assert tr.props["volume"] == 100 and svc.store.load().volume == 100
    svc.select_playlist("p")
    a = svc.enqueue(Track(id="x", title="X", source="remote"))
    b = svc.enqueue(Track(id="y", title="Y", source="remote"))
    svc.move(b.item_id, 0)
    svc.remove(a.item_id)
    assert [i.item_id for i in svc.queue.items()] == [b.item_id]
    assert calls
    assert svc.search("q")[0].id == "s-q"
    assert [p.id for p in svc.playlists()] == ["p"]
    assert svc.playlist_tracks("p")[0].id == "a"


def test_unknown_active_adapter_falls_back_to_local(tmp_path):
    svc, _ = make(tmp_path, None, active="gone")
    assert svc.active_name == "local"
    with pytest.raises(AdapterError):
        svc.set_active_adapter("gone")
    with pytest.raises(AdapterError):
        svc.select_playlist("x", "gone")


def test_set_active_adapter(tmp_path):
    svc, _ = make(tmp_path, FakeAdapter("remote", {}), active="local")
    svc.set_active_adapter("remote")
    assert svc.active_name == "remote" and svc.store.load().active_adapter == "remote"


def test_autoplay(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    assert not svc.autoplay()  # 前回なし
    svc.store.update(last_playlist=LastPlaylist("remote", "p"))
    assert svc.autoplay() and loaded(tr) == ["remote://a"]
    assert not svc.autoplay()  # 再生中は何もしない
    svc2, _ = make(tmp_path / "2", FakeAdapter("remote", {}))
    svc2.store.update(last_playlist=LastPlaylist("remote", "missing"))
    assert not svc2.autoplay()
    svc2.store.update(autoplay=False)
    assert not svc2.autoplay()


def test_status_position_and_health_notice(tmp_path):
    svc, tr = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    svc.health_notice = "音源側の変更で停止中。更新待ち"
    assert svc.status().position is None and svc.status().notice.startswith("音源側")
    svc.select_playlist("p")
    tr.props.update({"time-pos": 3.0, "duration": 10.0})
    st = svc.status()
    assert (st.position, st.duration, st.playlist_id) == (3.0, 10.0, "p")


def test_default_async_runner_runs_thread(tmp_path):
    import time

    remote = FakeAdapter("remote", {"p": ["a", "b"]})
    svc, _ = make(tmp_path, remote, run_async=__import__("soikaxa.daemon", fromlist=["x"])._start_thread)
    svc.select_playlist("p")
    for _ in range(100):
        if svc._prefetched:
            break
        time.sleep(0.01)
    assert svc._prefetched[0] == "b"


def test_status_and_enqueue_do_not_block_while_resolving(tmp_path):
    import threading

    gate = threading.Event()
    entered = threading.Event()

    class Slow(FakeAdapter):
        def resolve(self, tid):
            if tid == "slow":
                entered.set()
                gate.wait(2)
            return super().resolve(tid)

    svc, tr = make(tmp_path, Slow("remote", {"p": ["a", "slow"]}))
    svc.select_playlist("p")
    t = threading.Thread(target=svc.next)
    t.start()
    assert entered.wait(1)  # 再生ループが resolve 中（ロック保持）
    assert svc.healthy()  # 数秒の resolve では不健全にしない
    assert not svc.healthy(max_advance_sec=0)  # 終わらない曲送りは不健全
    assert svc.status().now.id == "a"  # 待たされない
    svc.enqueue(Track(id="g", title="G", source="remote"))  # 再生中なので待たされない
    gate.set()
    t.join(2)
    assert svc.healthy() and svc.status().now.id == "slow"


def test_concurrent_enqueue_when_idle_plays_both(tmp_path):
    import threading

    svc, tr = make(tmp_path, FakeAdapter("remote", {}))
    barrier = threading.Barrier(2)

    def add(tid):
        barrier.wait()
        svc.enqueue(Track(id=tid, title=tid, source="remote"))

    ts = [threading.Thread(target=add, args=(x,)) for x in ("x", "y")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(2)
    now = svc.status().now.id
    rest = [u.id for u in svc.status().upcoming]
    assert sorted([now, *rest]) == ["x", "y"] and len(loaded(tr)) == 1


def test_failure_count_resets_on_success(tmp_path):
    """「連続」失敗だけを数える。成功を挟めばフォールバックしない。"""
    remote = FakeAdapter(
        "remote", {"p": ["x1", "x2", "ok", "x3", "x4", "ok2"]}, fail={"x1", "x2", "x3", "x4"}
    )
    svc, tr = make(tmp_path, remote)
    svc.select_playlist("p")
    assert loaded(tr) == ["remote://ok"]
    tr.finish()
    assert loaded(tr)[-1] == "remote://ok2" and svc.status().adapter == "remote"


def test_gives_up_when_fallback_also_fails(tmp_path):
    remote = FakeAdapter("remote", {"p": [f"r{i}" for i in range(10)]}, fail={"*"})
    local = FakeAdapter("local", {"lp": [f"l{i}" for i in range(50)]}, fail={"*"})
    svc, tr = make(tmp_path, remote, local=local)
    svc.select_playlist("p")
    assert svc.status().now is None and loaded(tr) == []
    assert len(remote.resolved) + len(local.resolved) == 6  # 3 回でフォールバック、さらに 3 回で打ち切り


def test_all_local_failures_stop_without_looping_whole_queue(tmp_path):
    local = FakeAdapter("local", {"lp": [f"l{i}" for i in range(50)]}, fail={"*"})
    svc, _ = make(tmp_path, None, local=local, active="local")
    svc.select_playlist("lp")
    assert len(local.resolved) == 6 and svc.status().now is None


def test_prefetch_runs_one_at_a_time(tmp_path):
    remote = FakeAdapter("remote", {"p": ["a", "b"]})
    svc, _ = make(tmp_path, remote)
    svc.select_playlist("p")
    for i in range(5):  # ゲストが続けて追加しても resolve スレッドを増やさない
        svc.enqueue(Track(id=f"g{i}", title="G", source="remote"), play_next=True)
    assert len(svc.jobs) == 1
    run_jobs(svc)
    svc.enqueue(Track(id="h", title="H", source="remote"), play_next=True)
    assert len(svc.jobs) == 1  # 終われば次を先読みできる


def test_is_playing_and_notice_priority(tmp_path):
    svc, _ = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    assert not svc.is_playing
    svc.select_playlist("p")
    assert svc.is_playing
    svc.pause()
    assert not svc.is_playing
    svc.health_notice, svc.net_notice = "health", "net"
    assert svc.status().notice == "net"  # ネットワーク由来がヘルスチェックより優先
    svc.net_notice = ""
    assert svc.status().notice == "health"


def test_playlist_tracks_uses_given_adapter(tmp_path):
    svc, _ = make(tmp_path, FakeAdapter("remote", {"p": ["a"]}))
    assert svc.playlist_tracks("lp", "local")[0].id == "l1"
    assert svc.playlist_tracks("p", "unknown")[0].id == "a"  # 不明ならアクティブな音源
