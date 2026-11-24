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
from soikaxa.upgrade import UpgradeError, Upgrader
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

    # AuthCheckable
    fail_check = None

    def check_auth(self):
        if self.fail_check:
            raise self.fail_check
        return 2


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
    client.ctx = ctx
    return client, ctx, src, tr, runner, updates


PIN = "246810"


def login(client, pin=PIN):
    code = client.ctx.setup_code.current
    r = client.post("/api/owner/setup", json={"pin": pin, "setup_code": code or "000000"})
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
    st = client.get("/api/status").json()
    assert st["upcoming"] and all(
        "item_id" not in u for u in st["upcoming"] + [st["now"]]
    )  # 内部 ID は出さない
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


@pytest.mark.parametrize("q", ["", "   ", "x" * 301])
def test_search_query_validation(env, q):
    client, *_ = env
    assert client.get("/api/search", params={"q": q}).status_code == 422


def test_search_accepts_long_share_link(env):
    """プレイリストの共有リンクは 100 文字を超えることがある。"""
    client, *_ = env
    assert client.get("/api/search", params={"q": "x" * 300}).status_code == 200


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
    r = client.post("/api/owner/setup", json={"pin": PIN, "setup_code": client.ctx.setup_code.current})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_setup_only_once_and_pin_validation(env):
    client, ctx, *_ = env
    code = ctx.setup_code.current
    assert client.post("/api/owner/login", json={"pin": PIN}).status_code == 409  # 未設定
    assert client.post("/api/owner/setup", json={"pin": "1234", "setup_code": code}).status_code == 422
    assert client.post("/api/owner/setup", json={"pin": PIN}).status_code == 422  # コード必須
    login(client)
    assert PIN not in (ctx.store.path.read_text())
    assert ctx.setup_code.current is None  # 登録後はコードを無効にする
    r = client.post("/api/owner/setup", json={"pin": "999999", "setup_code": code})
    assert r.status_code == 409


def test_setup_requires_code_shown_on_device(env):
    """AP に入れるだけのゲストは、本体画面のコードを知らないので PIN を先取りできない。"""
    client, ctx, *_ = env
    code = ctx.setup_code.current
    wrong = "000000" if code != "000000" else "111111"
    assert "setup_code" not in client.get("/api/status").text and code not in client.get("/api/status").text
    r = client.post("/api/owner/setup", json={"pin": PIN, "setup_code": wrong})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "wrong_code"
    assert ctx.store.load().owner_pin_hash is None and "set-cookie" not in r.headers
    for _ in range(2):  # 総当たりはロックされる（max_attempts=3）
        client.post("/api/owner/setup", json={"pin": PIN, "setup_code": wrong})
    r = client.post("/api/owner/setup", json={"pin": PIN, "setup_code": code})
    assert r.status_code == 423
    ctx.pin_guard.reset()  # 本体の長押し
    assert client.post("/api/owner/setup", json={"pin": PIN, "setup_code": code}).status_code == 200


def test_login_with_correct_pin_after_logout(env):
    client, *_ = env
    h = login(client)
    client.post("/api/owner/logout", headers=h)
    r = client.post("/api/owner/login", json={"pin": PIN})
    assert r.status_code == 200 and r.json()["csrf"] and "sk_owner" in r.headers["set-cookie"]
    assert client.get("/api/owner/playlists").status_code == 200


def test_locked_login_skips_scrypt_and_busy_returns_429(env, monkeypatch):
    client, ctx, *_ = env
    login(client)
    other = TestClient(client.app, base_url="http://10.42.0.1")
    for _ in range(2):
        assert ctx.scrypt_slots.acquire(blocking=False)
    assert other.post("/api/owner/login", json={"pin": PIN}).status_code == 429  # 同時照合は 2 件まで
    for _ in range(2):
        ctx.scrypt_slots.release()
    for _ in range(3):
        other.post("/api/owner/login", json={"pin": "000000"})
    calls = []
    monkeypatch.setattr("soikaxa.web.app.verify_pin", lambda *a: calls.append(a) or True)
    assert other.post("/api/owner/login", json={"pin": PIN}).status_code == 423
    assert calls == []  # ロック中は重い照合をしない


def test_owner_cookie_without_csrf_is_treated_as_guest(env):
    client, ctx, *_ = env
    login(client)  # Cookie はあるが、以降 CSRF ヘッダを付けない
    ctx.store.update(allow_play_next=False, guest_queue_limit=1)
    client.get("/api/search", params={"q": "hit"})
    r = client.post("/api/queue", json={"track_id": "s1", "play_next": True})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "play_next_disabled"
    assert client.post("/api/queue", json={"track_id": "s1"}).status_code == 200  # 再生開始
    assert client.post("/api/queue", json={"track_id": "s2"}).status_code == 200
    assert client.post("/api/queue", json={"track_id": "s3"}).status_code == 429  # ゲストの上限が効く


def test_requests_from_upstream_network_are_rejected(env):
    import ipaddress

    client, ctx, *_ = env
    ctx.allowed_networks = (ipaddress.ip_network("10.42.0.0/24"), ipaddress.ip_network("127.0.0.0/8"))
    lan = TestClient(client.app, base_url="http://10.42.0.1", client=("192.168.1.50", 5000))
    r = lan.get("/api/status")  # Host を偽装しても送信元で弾く
    assert r.status_code == 403 and r.json()["detail"]["error"] == "forbidden_network"
    assert lan.post("/api/owner/setup", json={"pin": PIN, "setup_code": "000000"}).status_code == 403
    ap = TestClient(client.app, base_url="http://10.42.0.1", client=("10.42.0.23", 5000))
    assert ap.get("/api/status").status_code == 200
    odd = TestClient(client.app, base_url="http://10.42.0.1", client=("not-an-ip", 1))
    assert odd.get("/api/status").status_code == 403


def test_search_rate_limit_per_client_and_owner_exempt(env):
    from soikaxa.web.app import RateLimiter

    client, ctx, *_ = env
    ctx.search_limiter = RateLimiter(limit=2, window_sec=60)
    assert client.get("/api/search", params={"q": "a"}).status_code == 200
    assert client.get("/api/search", params={"q": "b"}).status_code == 200
    r = client.get("/api/search", params={"q": "c"})
    assert r.status_code == 429 and r.json()["detail"]["error"] == "rate"
    assert client.get("/api/search/playlists/pub").status_code == 429
    login(client)
    assert client.get("/api/search", params={"q": "c"}).status_code == 200  # オーナーは対象外


def test_static_is_cacheable_but_api_is_not(env):
    client, *_ = env
    assert "max-age" in client.get("/static/jsQR.js").headers["cache-control"]
    assert client.get("/api/status").headers["cache-control"] == "no-store"
    assert (
        "/static/jsQR.js" in client.get("/").text and "<script src=" not in client.get("/").text
    )  # 遅延読込


def test_pin_lockout(env):
    client, *_ = env
    login(client)
    other = TestClient(client.app, base_url="http://10.42.0.1")
    for _ in range(3):
        assert other.post("/api/owner/login", json={"pin": "000000"}).status_code == 401
    r = other.post("/api/owner/login", json={"pin": PIN})
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
    assert client.get("/api/owner/settings").json()["repeat"] is True  # 既定はオン
    assert client.patch("/api/owner/settings", json={"repeat": False}, headers=h).json()["repeat"] is False
    assert ctx.service.store.load().repeat is False


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
    client, ctx, _, _, runner, _ = env
    h = login(client)
    ap = client.get("/api/owner/ap").json()
    assert ap["qr"] == "WIFI:T:WPA;S:soikaxa-ABCD;P:abcdefghjk;;"
    changed = []
    ctx.on_ap_change = lambda: changed.append(1)
    new = client.post("/api/owner/ap/regenerate", headers=h).json()
    assert new["password"] != "abcdefghjk" and new["ssid"] == "soikaxa-ABCD"
    add = runner.called("nmcli", "connection", "add")[-1]  # 新しいパスワードで AP を張り直す
    assert (
        add[add.index("ssid") + 1] == "soikaxa-ABCD" and add[add.index("wifi-sec.psk") + 1] == new["password"]
    )
    assert runner.calls[-1] == ["nmcli", "connection", "up", "soikaxa-ap"] and changed == [1]
    ctx.store.update(ap_ssid=None)  # SSID 未保存でも QR と実際の AP が一致する
    again = client.post("/api/owner/ap/regenerate", headers=h).json()
    used = runner.called("nmcli", "connection", "add")[-1]
    assert again["ssid"] == ctx.store.load().ap_ssid == used[used.index("ssid") + 1]


def test_owner_bluetooth(env):
    client, ctx, *_ = env
    h = login(client)
    assert client.get("/api/owner/bt/scan").json()["devices"][0]["mac"] == MAC
    r = client.post("/api/owner/bt/connect", json={"mac": MAC.lower()}, headers=h).json()
    assert r == {"ok": True, "name": "JBL"}
    assert client.get("/api/owner/bt").json()["known"] == [{"mac": MAC, "name": "JBL", "connected": True}]
    assert client.post("/api/owner/bt/connect", json={"mac": "nope"}, headers=h).status_code == 422


def test_known_speakers_dedupe_order_limit_and_autoplay(env):
    from soikaxa.config import LastPlaylist, Speaker

    client, ctx, _, tr, *_ = env
    h = login(client)
    old = [Speaker(f"00:00:00:00:00:0{i}", f"S{i}") for i in range(4)]
    ctx.store.update(
        known_speakers=[*old, Speaker(MAC, "old name")], last_playlist=LastPlaylist("remote", "mine")
    )
    assert client.post("/api/owner/bt/connect", json={"mac": MAC.lower()}, headers=h).json()["ok"]
    known = ctx.store.load().known_speakers
    assert [k.mac for k in known] == [MAC, *[o.mac for o in old]]  # 重複なしで先頭へ
    assert known[0].name == "JBL"
    assert client.get("/api/status").json()["now"]["id"] == "mine-1"  # 接続で自動再生
    new_mac = "11:22:33:44:55:66"
    assert client.post("/api/owner/bt/connect", json={"mac": new_mac}, headers=h).json()["ok"]
    known = ctx.store.load().known_speakers
    assert len(known) == 5 and known[0].mac == new_mac and old[-1].mac not in [k.mac for k in known]


def test_owner_bluetooth_forget(env):
    from soikaxa.config import Speaker

    client, ctx, src, tr, runner, _ = env
    other = Speaker("11:22:33:44:55:66", "Other")
    ctx.store.update(known_speakers=[Speaker(MAC, "JBL"), other])
    runner.on(["bluetoothctl", "remove"], CommandResult(0, "Device has been removed"))
    h = login(client)
    assert client.post("/api/owner/bt/forget", json={"mac": MAC}).status_code in (401, 403)  # CSRF 必須
    r = client.post("/api/owner/bt/forget", json={"mac": MAC.lower()}, headers=h)
    assert r.json() == {"ok": True}
    assert runner.called("bluetoothctl", "remove") == [["bluetoothctl", "remove", MAC]]
    assert ctx.store.load().known_speakers == (other,)  # 記憶済み一覧からも消える
    assert client.post("/api/owner/bt/forget", json={"mac": "nope"}, headers=h).status_code == 422


def test_owner_bluetooth_forget_unknown_to_bluez_still_drops_known(env):
    from soikaxa.config import Speaker

    client, ctx, src, tr, runner, _ = env
    ctx.store.update(known_speakers=[Speaker(MAC, "JBL")])
    runner.on(["bluetoothctl", "remove"], CommandResult(1, "Device AA:BB:CC:DD:EE:FF not available"))
    h = login(client)
    assert client.post("/api/owner/bt/forget", json={"mac": MAC}, headers=h).json() == {"ok": True}
    assert ctx.store.load().known_speakers == ()  # 本体側に無くても一覧の掃除はできる


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


def _auth_test(client, h, adapter="remote"):
    return client.post("/api/owner/source/auth/test", json={"adapter": adapter}, headers=h)


def test_owner_source_auth_test_ok_and_unregistered(env):
    client, ctx, src, *_ = env
    h = login(client)
    assert client.post("/api/owner/source/auth/test", json={"adapter": "remote"}).status_code in (401, 403)
    r = _auth_test(client, h).json()
    assert r["ok"] is False and r["result"] == "unregistered"  # 未登録ならアクセスしない
    src.auth = True
    r = _auth_test(client, h).json()
    assert r == {"ok": True, "result": "ok", "playlists": 2, "message": "接続できました（プレイリスト 2 件）"}
    assert _auth_test(client, h, "nope").status_code == 404


@pytest.mark.parametrize(
    ("err", "online", "result"),
    [
        (AuthRequired("期限切れ"), 204, "auth"),
        (AdapterError("ConnectionError"), 0, "network"),
        (AdapterError("ConnectionError"), 200, "network"),  # 上流が同意画面（キャプティブ）
        (AdapterError("KeyError"), 204, "error"),
    ],
)
def test_owner_source_auth_test_failures(env, err, online, result):
    client, ctx, src, *_ = env
    h = login(client)
    src.auth = True
    src.fail_check = err
    ctx.wifi.http_get = lambda u: (online, "")
    r = _auth_test(client, h).json()
    assert r["ok"] is False and r["result"] == result and r["message"]


def test_owner_source_auth_test_rate_limited(env):
    client, ctx, src, *_ = env
    h = login(client)
    src.auth = True
    assert _auth_test(client, h).json()["ok"]
    assert _auth_test(client, h).status_code == 429  # 連打でアカウントに負担をかけない


def test_owner_update_and_reset(env):
    client, ctx, src, tr, runner, updates = env
    h = login(client)
    src.auth = True
    assert client.post("/api/owner/update", headers=h).json() == {"ok": True} and updates == [1]
    assert client.post("/api/owner/reset", json={"confirm": "no"}, headers=h).status_code == 422
    assert client.post("/api/owner/reset", json={"confirm": "RESET"}, headers=h).json() == {"ok": True}
    s = ctx.store.load()
    assert s.owner_pin_hash is None and s.ap_ssid == "soikaxa-ABCD" and not src.auth
    # 初期化後に、それまで AP に入れた人が先に PIN を登録できないこと
    assert s.ap_password != "abcdefghjk"  # AP パスワードを作り直す
    assert ctx.setup_code.current is not None  # 新しい設定コード（LCD にだけ出る）
    assert runner.called("bluetoothctl", "remove") == []  # 記憶スピーカーなし
    assert runner.called("nmcli", "connection", "delete")
    assert client.get("/api/owner/playlists").status_code == 401


def test_owner_upgrade_flow(env, tmp_path):
    client, ctx, *_ = env
    started = []
    release = {"tag_name": "v0.2.0", "body": "新機能"}
    ctx.upgrader = Upgrader(
        tmp_path,
        start_unit=lambda: started.append(1) or True,
        fetch_latest=lambda: release,
        current="0.1.0",
        status_dir=tmp_path,
    )
    assert client.get("/api/owner/upgrade").status_code == 401  # オーナーのみ
    h = login(client)
    assert client.post("/api/owner/upgrade/check").status_code in (401, 403)  # CSRF なし
    r = client.get("/api/owner/upgrade", headers=h).json()
    assert r == {"supported": True, "current": "0.1.0", "status": {"state": "idle"}}
    assert client.post("/api/owner/upgrade/check", headers=h).json()["available"] is True
    r = client.post("/api/owner/upgrade", headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True and started == [1]
    r = client.post("/api/owner/upgrade", headers=h)  # 実行中は二重に起動しない
    assert r.status_code == 409 and r.json()["detail"]["error"] == "busy" and started == [1]


def test_owner_upgrade_errors(env, tmp_path):
    client, ctx, *_ = env
    h = login(client)
    # 開発 PC など更新ユニットが無い端末
    assert client.get("/api/owner/upgrade", headers=h).json()["supported"] is False
    assert client.post("/api/owner/upgrade/check", headers=h).status_code == 404

    def offline():
        raise UpgradeError("インターネットに接続できません")

    ctx.upgrader = Upgrader(
        tmp_path, start_unit=lambda: True, fetch_latest=offline, current="0.1.0", status_dir=tmp_path
    )
    r = client.post("/api/owner/upgrade/check", headers=h)
    assert r.status_code == 502 and r.json()["detail"]["message"] == "インターネットに接続できません"
    ctx.upgrader = Upgrader(
        tmp_path,
        start_unit=lambda: True,
        fetch_latest=lambda: {"tag_name": "v0.1.0"},
        current="0.1.0",
        status_dir=tmp_path,
    )
    r = client.post("/api/owner/upgrade", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "up_to_date"


def test_search_cache_lru():
    c = SearchCache(capacity=2)
    for i in range(3):
        c.put(Track(id=str(i), title=""))
    assert c.track("0") is None and c.track("2") is not None
    c.put(Playlist(id="p", title=""))
    assert c.playlist("p") is not None and c.track("p") is None and c.playlist("0") is None


def test_reset_removes_bluetooth_pairing(env):
    from soikaxa.config import Speaker

    client, ctx, _, _, runner, _ = env
    h = login(client)
    ctx.store.update(known_speakers=[Speaker(MAC, "JBL")])
    assert client.post("/api/owner/reset", json={"confirm": "RESET"}, headers=h).json() == {"ok": True}
    assert runner.called("bluetoothctl", "remove") == [["bluetoothctl", "remove", MAC]]


def test_guest_playlist_expansion_uses_source_at_search_time(env):
    client, ctx, *_ = env
    client.get("/api/search", params={"q": "hit"})
    ctx.store.update(active_adapter="local")  # 途中で音源を切り替えても展開できる
    ctx.service.adapters["local"].list_tracks = lambda pid: (_ for _ in ()).throw(AdapterError("x"))
    assert client.get("/api/search/playlists/pub").status_code == 200


def test_rate_limiter_window():
    from soikaxa.web.app import RateLimiter

    now = [0.0]
    rl = RateLimiter(limit=1, window_sec=10, clock=lambda: now[0])
    assert rl.allow("a") and not rl.allow("a") and rl.allow("b")
    now[0] = 11
    assert rl.allow("a")
    for i in range(1002):
        rl.allow(f"k{i}")
    assert len(rl._hits) < 1000


def test_guest_can_cancel_only_own_items(env):
    """ゲストは自分が追加した曲だけ取り消せる。他人の曲や Cookie の無いゲストは 404。"""
    client, ctx, *_ = env
    other = TestClient(client.app, base_url="http://10.42.0.1")
    for c in (client, other):
        c.get("/api/status")  # ゲスト Cookie を受け取る
    client.get("/api/search", params={"q": "hit"})
    other.get("/api/search", params={"q": "hit"})
    client.post("/api/queue", json={"track_id": "s1"})  # 再生が始まる（キューから消費される）
    mine = client.post("/api/queue", json={"track_id": "s2", "name": "Aさん"}).json()["item_id"]
    theirs = other.post("/api/queue", json={"track_id": "s3"}).json()["item_id"]
    assert client.get("/api/queue/mine").json()["items"] == [
        {"item_id": mine, "title": "Hit2", "added_by": "Aさん"}
    ]
    assert other.get("/api/queue/mine").json()["items"][0]["item_id"] == theirs
    assert client.delete(f"/api/queue/{theirs}").status_code == 404  # 他人の曲は消せない
    nocookie = TestClient(client.app, base_url="http://10.42.0.1")
    assert nocookie.delete(f"/api/queue/{mine}").status_code == 404
    assert nocookie.get("/api/queue/mine").json()["items"] == []
    assert client.delete(f"/api/queue/{mine}").status_code == 200
    assert [i.item_id for i in ctx.service.queue.items()] == [theirs]
    assert client.delete(f"/api/queue/{mine}").status_code == 404  # 二重の取り消し


def test_owner_starts_touch_calibration(env):
    client, ctx, *_ = env
    assert client.post("/api/owner/touch/calibrate").status_code in (401, 403)  # オーナーだけ
    h = login(client)
    r = client.post("/api/owner/touch/calibrate", headers=h)
    assert r.status_code == 409 and "タッチパネル" in r.json()["detail"]["message"]  # 開発 PC など
    started = []
    ctx.start_touch_calibration = lambda: started.append(1) or True
    assert client.post("/api/owner/touch/calibrate", headers=h).status_code == 200 and started == [1]
