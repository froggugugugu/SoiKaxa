import pytest
from soikaxa.adapters.base import Track
from soikaxa.queue import ItemNotFound, PlayQueue, QueueLimitExceeded
from soikaxa.state import TRANSITIONS, Event, State, StateMachine


def t(i: str) -> Track:
    return Track(id=i, title=i.upper())


# ---------- queue ----------


def test_playlist_order_and_exhaustion():
    q = PlayQueue()
    q.set_playlist("p", [t("a"), t("b")])
    assert q.pop_next().track.id == "a"
    assert q.pop_next().track.id == "b"
    assert q.pop_next() is None and q.peek_next() is None


def test_queue_has_priority_and_play_next_goes_first():
    q = PlayQueue(clock=lambda: 5.0)
    q.set_playlist("p", [t("a"), t("b")])
    q.add(t("x"), added_by=" Aさん ")
    n = q.add(t("y"), play_next=True)
    assert q.peek_next().track.id == "y"
    first = q.pop_next()
    assert first.origin == "queue" and first.item == n and first.added_by == ""
    second = q.pop_next()
    assert second.track.id == "x" and second.added_by == "Aさん" and second.item.added_at == 5.0
    assert q.pop_next().origin == "playlist"


def test_guest_limit():
    q = PlayQueue()
    for _ in range(3):
        q.add(t("a"), guest_id="g1", limit=3)
    with pytest.raises(QueueLimitExceeded):
        q.add(t("a"), guest_id="g1", limit=3)
    q.add(t("a"), guest_id="g2", limit=3)  # 別ゲストは可
    q.add(t("a"), guest_id=None, limit=3)  # オーナーは無制限
    assert q.count_by_guest("g1") == 3
    q.pop_next()  # 再生されたら枠が空く
    q.add(t("a"), guest_id="g1", limit=3)


def test_remove_move_items_clear_upcoming():
    q = PlayQueue()
    q.set_playlist("p", [t("p1"), t("p2"), t("p3")], start=1)
    a = q.add(t("a"))
    b = q.add(t("b"))
    c = q.add(t("c"))
    q.move(c.item_id, 0)
    assert [i.item_id for i in q.items()] == [c.item_id, a.item_id, b.item_id]
    q.move(a.item_id, 99)
    assert q.items()[-1] == a
    assert q.remove(b.item_id) == b
    with pytest.raises(ItemNotFound):
        q.remove("nope")
    up = q.upcoming(4)
    assert [u.track.id for u in up] == ["c", "a", "p2", "p3"]
    assert q.playlist_remaining == 2
    q.clear()
    assert q.items() == []


def test_set_playlist_clamps_start():
    q = PlayQueue()
    q.set_playlist("p", [t("a")], start=10)
    assert q.pop_next() is None


# ---------- state ----------


def test_happy_path_and_callback():
    seen = []
    sm = StateMachine(on_change=lambda a, b, e: seen.append((a, b, e)))
    assert sm.dispatch(Event.NET_DOWN) == State.AP_ONLY
    assert sm.dispatch(Event.NET_UP) == State.ONLINE
    assert sm.dispatch(Event.BT_CONNECTING) == State.BT_CONNECTING
    assert sm.dispatch(Event.BT_CONNECTED) == State.IDLE
    assert sm.dispatch(Event.PLAY) == State.PLAYING
    assert sm.dispatch(Event.STOP) == State.IDLE
    assert len(seen) == 6 and seen[0] == (State.BOOT, State.AP_ONLY, Event.NET_DOWN)


def test_ignored_events_do_not_change_state_or_notify():
    seen = []
    sm = StateMachine(on_change=lambda *a: seen.append(a))
    assert sm.dispatch(Event.PLAY) == State.BOOT
    assert not sm.can(Event.PLAY)
    assert seen == []


def test_playing_survives_net_down_but_not_bt_lost():
    sm = StateMachine()
    for e in (Event.NET_UP, Event.BT_CONNECTED, Event.PLAY):
        sm.dispatch(e)
    assert sm.dispatch(Event.NET_DOWN) == State.PLAYING
    assert sm.dispatch(Event.BT_LOST) == State.BT_CONNECTING


def test_error_and_recover():
    sm = StateMachine()
    sm.dispatch(Event.FAIL)
    assert sm.state == State.ERROR
    assert sm.dispatch(Event.RECOVER) == State.ONLINE


def test_every_state_has_entry_and_fail_or_recovery():
    assert set(TRANSITIONS) == set(State)
    for st, table in TRANSITIONS.items():
        assert st == State.ERROR or Event.FAIL in table
