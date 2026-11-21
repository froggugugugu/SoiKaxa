import pytest
from fastapi.testclient import TestClient
from soikaxa.adapters.base import AdapterError, AuthRequired, Playlist, Resolved, Track
from soikaxa.auth import PinGuard
from soikaxa.bt import BluetoothManager
from soikaxa.config import SettingsStore
from soikaxa.daemon import PlayerService
from soikaxa.fakes import FakeRunner, FakeTransport
from soikaxa.net import WifiManager
from soikaxa.player import MpvPlayer
from soikaxa.runner import CommandResult
from soikaxa.web.app import AppContext, SearchCache, create_app

MAC = "AA:BB:CC:DD:EE:FF"


class Src:
    name = "remote"

    def __init__(self):
        self.auth = False
        self.fail_search = None

    def list_playlists(self):
        return [Playlist(id="mine", title="Mine", track_count=2, source="remote")]

    def list_tracks(self, pid):
        if pid not in ("mine", "pub"):
            raise AdapterError("x")
        return [
            Track(id=f"{pid}-1", title="T1", source="remote"),
            Track(id=f"{pid}-2", title="T2", source="remote"),
        ]

    def search(self, q, limit=20):
        if self.fail_search:
            raise self.fail_search
        return [
            Track(id="s1", title="Hit", artist="A", source="remote"),
            Track(id="s2", title="Hit2", source="remote"),
            Track(id="s3", title="Hit3", source="remote"),
            Track(id="s4", title="Hit4", source="remote"),
            Playlist(id="pub", title="Public", source="remote"),
        ]

    def resolve(self, tid):
        return Resolved(uri=f"r://{tid}")

    # AuthCapable
    def has_auth(self):
        return self.auth

    def register_auth(self, raw):
        if raw == "bad":
            raise AdapterError("認証ヘッダを解釈できません")
        self.auth = True

    def clear_auth(self):
        self.auth = False


@pytest.fixture
def env(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.update(active_adapter="remote", ap_ssid="soikaxa-ABCD", ap_password="abcdefghjk")
    tr = FakeTransport()
    src = Src()
    svc = PlayerService(
        adapters={"local": Src(), "remote": src}, player=MpvPlayer(tr), store=store, run_async=lambda f: None
    )
    runner = (
        FakeRunner()
        .on(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN"], CommandResult(0, "*:Home:80:WPA2:6"))
        .on(
            ["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY"],
            CommandResult(0, "sta-Home:802-11-wireless:10"),
        )
        .on(["bluetoothctl", "devices"], CommandResult(0, f"Device {MAC} JBL"))
        .on(["bluetoothctl", "info"], CommandResult(0, "Name: JBL\nPaired: yes\nConnected: yes"))
        .on(["bluetoothctl", "--timeout", "15", "connect"], CommandResult(0, "Connection successful"))
    )
    updates = []
    ctx = AppContext(
        service=svc,
        store=store,
        wifi=WifiManager(runner, http_get=lambda u: (204, "")),
        bt=BluetoothManager(runner),
        pin_guard=PinGuard(max_attempts=3, lockout_sec=60),
        run_update=lambda: updates.append(1) or True,
    )
    client = TestClient(create_app(ctx), base_url="http://10.42.0.1")
    return client, ctx, src, tr, runner, updates


def login(client, pin="1234"):
    r = client.post("/api/owner/setup", json={"pin": pin})
    if r.status_code == 409:
        r = client.post("/api/owner/login", json={"pin": pin})
    assert r.status_code == 200, r.text
    return {"X-SoiKaxa-CSRF": r.json()["csrf"]}


# ---------- 公開・ポータル ----------


def test_index_and_static_and_security_headers(env):
    client, *_ = env
    r = client.get("/")
    assert r.status_code == 200 and "SOI" in r.text and "text/html" in r.headers["content-type"]
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/static/jsQR.js").status_code == 200
    # 外部 CDN を参照しない
    assert "http://" not in r.text.replace("http://10.42.0.1", "") and "https://" not in r.text


@pytest.mark.parametrize(
    ("host", "path"),
    [
        ("captive.apple.com", "/hotspot-detect.html"),
        ("connectivitycheck.gstatic.com", "/generate_204"),
        ("example.com", "/anything"),
        ("10.42.0.1", "/ncsi.txt"),
    ],
)
def test_captive_portal_redirects(env, host, path):
    client, *_ = env
    r = client.get(path, headers={"host": host}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "http://10.42.0.1/"


def test_status_issues_guest_cookie_and_hides_secrets(env):
    client, *_ = env
    r = client.get("/api/status")
    body = r.json()
    assert "sk_gid" in r.cookies
    assert body["state"] == "BOOT" and body["has_pin"] is False and body["is_owner"] is False
    assert "abcdefghjk" not in r.text and "csrf" not in body
    assert "owner_pin_hash" not in r.text


# ---------- ゲスト ----------


def test_guest_search_enqueue_and_limits(env):
    client, ctx, src, tr, *_ = env
    client.get("/api/status")
    res = client.get("/api/search", params={"q": "hit"}).json()["results"]
    assert res[0] == {"kind": "track", "id": "s1", "title": "Hit", "artist": "A", "duration_sec": None}
    assert res[-1]["kind"] == "playlist"
    r = client.post("/api/queue", json={"track_id": "s1", "name": "Aさん"})
    assert r.status_code == 200
    assert client.get("/api/status").json()["now"]["added_by"] == "Aさん"
    assert client.post("/api/queue", json={"track_id": "s2"}).status_code == 200
    assert client.post("/api/queue", json={"track_id": "s3"}).status_code == 200
    assert client.post("/api/queue", json={"track_id": "s4"}).status_code == 200
    # 再生中の 1 曲は消費済みなので上限 3 = キューに 3 件
    r = client.post("/api/queue", json={"track_id": "s1"})
    assert r.status_code == 429 and r.json()["detail"]["error"] == "limit"


def test_guest_cannot_add_unknown_or_forged_track(env):
    client, *_ = env
    assert client.post("/api/queue", json={"track_id": "never-searched"}).status_code == 404
    assert client.post("/api/queue", json={"track_id": "x", "name": "n" * 21}).status_code == 422


def test_guest_playlist_expansion_only_for_searched(env):
    client, *_ = env
    assert client.get("/api/search/playlists/mine").status_code == 404  # オーナーの非公開に到達不可
    client.get("/api/search", params={"q": "hit"})
    r = client.get("/api/search/playlists/pub")
    assert r.status_code == 200 and r.json()["results"][0]["id"] == "pub-1"
    assert client.post("/api/queue", json={"track_id": "pub-2"}).status_code == 200


@pytest.mark.parametrize("q", ["", "   ", "x" * 101])
def test_search_query_validation(env, q):
    client, *_ = env
    assert client.get("/api/search", params={"q": q}).status_code == 422


def test_search_errors_mapped(env):
    client, _, src, *_ = env
    src.fail_search = AuthRequired("expired")
    assert client.get("/api/search", params={"q": "x"}).json()["detail"]["error"] == "auth_required"
    src.fail_search = AdapterError("down")
    r = client.get("/api/search", params={"q": "x"})
    assert r.status_code == 502 and r.json()["detail"]["error"] == "source_error"


def test_play_next_permission_and_guest_disabled(env):
    client, ctx, *_ = env
    client.get("/api/search", params={"q": "hit"})
    ctx.store.update(allow_play_next=False)
    r = client.post("/api/queue", json={"track_id": "s1", "play_next": True})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "play_next_disabled"
    ctx.store.update(guest_enabled=False)
    assert client.get("/api/search", params={"q": "hit"}).status_code == 403
    assert client.post("/api/queue", json={"track_id": "s1"}).status_code == 403


def test_owner_bypasses_guest_limits(env):
    client, ctx, *_ = env
    h = login(client)
    ctx.store.update(guest_enabled=False, allow_play_next=False, guest_queue_limit=1)
    client.get("/api/search", params={"q": "hit"})
    for tid in ("s1", "s2", "s3"):
        assert (
            client.post("/api/queue", json={"track_id": tid, "play_next": True}, headers=h).status_code == 200
        )
    assert client.get("/api/owner/queue").json()["items"][0]["guest_id"] is None


# ---------- オーナー認証 ----------


def test_owner_api_requires_session_and_csrf(env):
    client, *_ = env
    assert client.get("/api/owner/playlists").status_code == 401
    h = login(client)
    assert client.get("/api/owner/playlists").status_code == 200  # GET は CSRF 不要
    assert client.post("/api/owner/control", json={"action": "next"}).status_code == 403
    assert (
        client.post(
            "/api/owner/control", json={"action": "next"}, headers={"X-SoiKaxa-CSRF": "x"}
        ).status_code
        == 403
    )
    assert client.post("/api/owner/control", json={"action": "next"}, headers=h).status_code == 200
    st = client.get("/api/status").json()
    assert st["is_owner"] and st["csrf"] == h["X-SoiKaxa-CSRF"]


def test_session_cookie_flags(env):
    client, *_ = env
    r = client.post("/api/owner/setup", json={"pin": "1234"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_setup_only_once_and_pin_validation(env):
    client, ctx, *_ = env
    assert client.post("/api/owner/login", json={"pin": "1234"}).status_code == 409  # 未設定
    assert client.post("/api/owner/setup", json={"pin": "12"}).status_code == 422
    login(client)
    assert "1234" not in (ctx.store.path.read_text())
    assert client.post("/api/owner/setup", json={"pin": "9999"}).status_code == 409


def test_pin_lockout(env):
    client, *_ = env
    login(client)
    other = TestClient(client.app, base_url="http://10.42.0.1")
    for _ in range(3):
        assert other.post("/api/owner/login", json={"pin": "0000"}).status_code == 401
    r = other.post("/api/owner/login", json={"pin": "1234"})
    assert r.status_code == 423 and r.json()["detail"]["error"] == "locked"


def test_logout(env):
    client, *_ = env
    h = login(client)
    assert client.post("/api/owner/logout", headers=h).status_code == 200
    assert client.get("/api/owner/playlists").status_code == 401


# ---------- オーナー機能 ----------


def test_owner_playback_volume_queue(env):
    client, ctx, src, tr, *_ = env
    h = login(client)
    pls = client.get("/api/owner/playlists").json()
    assert pls == {"adapter": "remote", "playlists": [{"id": "mine", "title": "Mine", "track_count": 2}]}
    assert client.post("/api/owner/play", json={"playlist_id": "mine"}, headers=h).status_code == 200
    assert client.get("/api/status").json()["now"]["id"] == "mine-1"
    for act in ("pause", "resume", "toggle", "next"):
        assert client.post("/api/owner/control", json={"action": act}, headers=h).status_code == 200
    assert client.post("/api/owner/control", json={"action": "reboot"}, headers=h).status_code == 422
    assert client.post("/api/owner/volume", json={"volume": 30}, headers=h).json() == {"volume": 30}
    assert client.post("/api/owner/volume", json={"volume": 101}, headers=h).status_code == 422
    client.get("/api/search", params={"q": "x"})
    ids = [client.post("/api/queue", json={"track_id": t}, headers=h).json()["item_id"] for t in ("s1", "s2")]
    assert client.post(f"/api/owner/queue/{ids[1]}/move", json={"index": 0}, headers=h).status_code == 200
    assert [i["item_id"] for i in client.get("/api/owner/queue").json()["items"]] == [ids[1], ids[0]]
    assert client.delete(f"/api/owner/queue/{ids[0]}", headers=h).status_code == 200
    assert client.delete("/api/owner/queue/zzz", headers=h).status_code == 404
    assert client.post("/api/owner/queue/zzz/move", json={"index": 0}, headers=h).status_code == 404
    assert client.post("/api/owner/play", json={"playlist_id": "nope"}, headers=h).status_code == 502


def test_owner_settings(env):
    client, ctx, *_ = env
    h = login(client)
    cfg = client.patch(
        "/api/owner/settings",
        json={"guest_enabled": False, "guest_queue_limit": 5, "active_adapter": "local"},
        headers=h,
    ).json()
    assert (
        cfg["guest_enabled"] is False and cfg["guest_queue_limit"] == 5 and cfg["active_adapter"] == "local"
    )
    assert cfg["adapters"] == ["local", "remote"]
    assert client.patch("/api/owner/settings", json={"guest_queue_limit": 0}, headers=h).status_code == 422
    assert client.patch("/api/owner/settings", json={"active_adapter": "gone"}, headers=h).status_code == 502
    assert client.get("/api/owner/settings").json()["guest_queue_limit"] == 5


def test_owner_wifi_flow(env):
    client, ctx, src, tr, runner, _ = env
    h = login(client)
    assert client.get("/api/owner/wifi/scan").json()["networks"][0]["ssid"] == "Home"
    r = client.post(
        "/api/owner/wifi",
        json={"ssid": "Venue", "password": "password123", "hidden": True, "label": "会場"},
        headers=h,
    ).json()
    assert r["ok"] and r["reason"] == "ok"
    assert ctx.store.load().wifi_labels == {"Venue": "会場"}
    assert ctx.service.state.value == "ONLINE"
    assert client.get("/api/owner/wifi").json()["saved"] == [
        {"ssid": "Home", "priority": 10, "label": "Home"}
    ]
    assert (
        client.post("/api/owner/wifi", json={"ssid": "V", "password": "short"}, headers=h).status_code == 422
    )
    assert client.post("/api/owner/wifi", json={"ssid": "", "password": ""}, headers=h).status_code == 422
    assert client.delete("/api/owner/wifi/Venue", headers=h).json() == {"ok": True}
    assert ctx.store.load().wifi_labels == {}
    assert "password123" not in ctx.store.path.read_text()


def test_owner_wifi_captive(env):
    client, ctx, *_ = env
    ctx.wifi.http_get = lambda u: (200, "<html>")
    h = login(client)
    r = client.post("/api/owner/wifi", json={"ssid": "Hotel"}, headers=h).json()
    assert r["reason"] == "captive_portal" and "テザリング" in client.get("/api/status").json()["notice"]


def test_owner_ap_info_and_regenerate(env):
    client, ctx, *_ = env
    h = login(client)
    ap = client.get("/api/owner/ap").json()
    assert ap["qr"] == "WIFI:T:WPA;S:soikaxa-ABCD;P:abcdefghjk;;"
    new = client.post("/api/owner/ap/regenerate", headers=h).json()
    assert new["password"] != "abcdefghjk" and new["ssid"] == "soikaxa-ABCD"


def test_owner_bluetooth(env):
    client, ctx, *_ = env
    h = login(client)
    assert client.get("/api/owner/bt/scan").json()["devices"][0]["mac"] == MAC
    r = client.post("/api/owner/bt/connect", json={"mac": MAC.lower()}, headers=h).json()
    assert r == {"ok": True, "name": "JBL"}
    assert client.get("/api/owner/bt").json()["known"] == [{"mac": MAC, "name": "JBL", "connected": True}]
    assert client.post("/api/owner/bt/connect", json={"mac": "nope"}, headers=h).status_code == 422


def test_owner_bluetooth_failure(env):
    client, ctx, src, tr, runner, _ = env
    runner.on(["bluetoothctl", "--timeout", "15", "connect"], CommandResult(1, "Failed"))
    runner.on(["bluetoothctl", "info"], CommandResult(0, "Paired: yes\nConnected: no"))
    h = login(client)
    assert client.post("/api/owner/bt/connect", json={"mac": MAC}, headers=h).json()["ok"] is False


def test_owner_source_auth(env):
    client, ctx, src, *_ = env
    h = login(client)
    listed = client.get("/api/owner/source/auth").json()["adapters"]
    assert {"adapter": "remote", "registered": False} in listed
    assert client.post(
        "/api/owner/source/auth", json={"adapter": "remote", "raw": "cookie: x"}, headers=h
    ).json()["ok"]
    assert src.auth
    assert (
        client.post("/api/owner/source/auth", json={"adapter": "remote", "raw": "bad"}, headers=h).status_code
        == 422
    )
    assert (
        client.post("/api/owner/source/auth", json={"adapter": "nope", "raw": "x"}, headers=h).status_code
        == 404
    )
    assert client.request("DELETE", "/api/owner/source/auth", json={"adapter": "remote"}, headers=h).json()[
        "ok"
    ]
    assert not src.auth


def test_owner_update_and_reset(env):
    client, ctx, src, tr, runner, updates = env
    h = login(client)
    src.auth = True
    assert client.post("/api/owner/update", headers=h).json() == {"ok": True} and updates == [1]
    assert client.post("/api/owner/reset", json={"confirm": "no"}, headers=h).status_code == 422
    assert client.post("/api/owner/reset", json={"confirm": "RESET"}, headers=h).json() == {"ok": True}
    s = ctx.store.load()
    assert s.owner_pin_hash is None and s.ap_ssid == "soikaxa-ABCD" and not src.auth
    assert runner.called("nmcli", "connection", "delete")
    assert client.get("/api/owner/playlists").status_code == 401


def test_search_cache_lru():
    c = SearchCache(capacity=2)
    for i in range(3):
        c.put(Track(id=str(i), title=""))
    assert c.track("0") is None and c.track("2") is not None
    c.put(Playlist(id="p", title=""))
    assert c.has_playlist("p") and c.track("p") is None
