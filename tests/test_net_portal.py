import pytest
from soikaxa import net, portal
from soikaxa.fakes import FakeRunner
from soikaxa.runner import CommandResult

SCAN = "\n".join(
    [
        "*:Home\\:5G:80:WPA2:36",
        " :Cafe:60:WPA2:6",
        " :Cafe:70:WPA2:11",
        " ::40:WPA2:1",
        " :Open:30::1",
        "garbage",
    ]
)
SCAN_PREFIX = ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN"]


def mgr(runner, **kw):
    kw.setdefault("http_get", lambda url: (204, ""))
    return net.WifiManager(runner, **kw)


def test_split_terse_escapes():
    assert net.split_terse(r"a\:b:c\\d:") == ["a:b", "c\\d", ""]


def test_scan_parses_dedups_sorts_and_hides_stealth():
    r = FakeRunner().on(SCAN_PREFIX, CommandResult(0, SCAN))
    nets = mgr(r).scan()
    assert [n.ssid for n in nets] == ["Home:5G", "Cafe", "Open"]
    assert nets[0].in_use and nets[0].channel == 36
    assert nets[1].signal == 70 and nets[1].channel == 11
    assert r.calls[0][-2:] == ["--rescan", "yes"]


def test_saved_filters_sta_profiles():
    out = "\n".join(
        [
            "sta-Home:802-11-wireless:20",
            "soikaxa-ap:802-11-wireless:0",
            "Wired:802-3-ethernet:0",
            "sta-Cafe:802-11-wireless:x",
        ]
    )
    r = FakeRunner().on(["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY"], CommandResult(0, out))
    assert [(s.ssid, s.priority) for s in mgr(r).saved()] == [("Home", 20), ("Cafe", 0)]


def test_add_and_connect_success_hidden_and_channel_sync():
    r = FakeRunner().on(SCAN_PREFIX, CommandResult(0, SCAN))
    res = mgr(r).add_and_connect("Venue", "password123", hidden=True, priority=5)
    assert res.ok and res.reason == "ok"
    add = r.called("nmcli", "connection", "add")[0]
    assert add[add.index("ssid") + 1] == "Venue"
    assert add[add.index("802-11-wireless.hidden") + 1] == "yes"
    assert add[add.index("wifi-sec.psk") + 1] == "password123"
    assert add[add.index("connection.autoconnect-priority") + 1] == "5"
    assert add[add.index("con-name") + 1] == "sknew-Venue"  # 仮の名前で作る
    rename, modify = r.called("nmcli", "connection", "modify")
    assert rename == ["nmcli", "connection", "modify", "sknew-Venue", "connection.id", "sta-Venue"]
    assert modify[-2:] == ["802-11-wireless.channel", "36"] and "a" in modify
    order = [c[2:4] for c in r.calls if c[:2] == ["nmcli", "connection"]]
    # 接続できてから旧設定を消す
    assert order.index(["up", "sknew-Venue"]) < order.index(["delete", "sta-Venue"])


def test_open_network_has_no_security_args():
    r = FakeRunner()
    mgr(r).add_and_connect("Free", "")
    assert "wifi-sec.psk" not in r.called("nmcli", "connection", "add")[0]


@pytest.mark.parametrize(
    ("stderr", "hidden", "reason"),
    [
        (
            "Error: Connection activation failed: Secrets were required, but not provided",
            False,
            "wrong_password",
        ),
        ("Error: No network with SSID 'X' found.", False, "not_found"),
        ("Error: No network with SSID 'X' found.", True, "not_found"),
        ("Error: something else", False, "error"),
    ],
)
def test_connect_failure_classification_and_cleanup(stderr, hidden, reason):
    r = FakeRunner().on(["nmcli", "connection", "up"], CommandResult(4, "", stderr))
    res = mgr(r).add_and_connect("X", "password123", hidden=hidden)
    assert not res.ok and res.reason == reason
    deleted = [c[3] for c in r.called("nmcli", "connection", "delete")]
    assert deleted == ["sknew-X", "sknew-X"]  # 仮プロファイルだけ消す。保存済みの sta-X は残す
    assert not [c for c in r.called("nmcli", "connection", "modify") if "connection.id" in c]
    if reason == "not_found" and not hidden:
        assert "ステルス" in res.message


def test_add_failure_returns_error():
    r = FakeRunner().on(["nmcli", "connection", "add"], CommandResult(2, "", "bad"))
    assert mgr(r).add_and_connect("X", "").reason == "error"


def test_captive_portal_detected_after_connect():
    res = mgr(FakeRunner(), http_get=lambda url: (302, "")).add_and_connect("Hotel", "")
    assert res.reason == "captive_portal" and "テザリング" in res.message


@pytest.mark.parametrize(
    ("status", "expected"), [(204, "online"), (200, "captive"), (302, "captive"), (0, "offline")]
)
def test_connectivity(status, expected):
    assert net.classify_connectivity(status, "") == expected
    assert mgr(FakeRunner(), http_get=lambda url: (status, "")).connectivity() == expected


@pytest.mark.parametrize("ssid", ["", "x" * 33, "a\nb"])
def test_invalid_ssid(ssid):
    with pytest.raises(net.InvalidWifiInput):
        mgr(FakeRunner()).add_and_connect(ssid, "")


@pytest.mark.parametrize("pw", ["short", "x" * 64, "ありがとうございます"])
def test_invalid_password(pw):
    with pytest.raises(net.InvalidWifiInput):
        net.validate_password(pw)


def test_delete():
    r = FakeRunner()
    assert mgr(r).delete("Home")
    assert r.calls[-1] == ["nmcli", "connection", "delete", "sta-Home"]


def test_start_ap_args():
    r = FakeRunner()
    assert mgr(r).start_ap("soikaxa-ABCD", "abcdefghjk", channel=6)
    add = r.called("nmcli", "connection", "add")[0]
    assert add[add.index("802-11-wireless.mode") + 1] == "ap"
    assert add[add.index("ipv4.method") + 1] == "shared"
    assert add[add.index("802-11-wireless.channel") + 1] == "6"
    assert r.calls[-1] == ["nmcli", "connection", "up", "soikaxa-ap"]
    r2 = FakeRunner().on(["nmcli", "connection", "add"], CommandResult(1))
    assert not mgr(r2).start_ap("soikaxa-ABCD", "abcdefghjk")


def test_virtual_ap_interface_on_same_radio_follows_sta_channel():
    """uap0（wlan0 上の仮想 IF）は別名でも同じ無線チップ。チャンネルを STA に合わせる。"""
    r = FakeRunner().on(SCAN_PREFIX, CommandResult(0, SCAN))
    m = mgr(r, ap_if="uap0", same_radio=True)
    assert m.start_ap("soikaxa-ABCD", "abcdefghjk")
    add = r.called("nmcli", "connection", "add")[0]
    assert add[add.index("ifname") + 1] == "uap0"
    assert (
        add[add.index("802-11-wireless.channel") + 1] == "36"
        and add[add.index("802-11-wireless.band") + 1] == "a"
    )
    assert m.sync_ap_channel() == 36
    # 上流に未接続ならチャンネル指定なし（2.4GHz）
    add2 = (
        lambda rr: (mgr(rr, ap_if="uap0", same_radio=True).start_ap("soikaxa-ABCD", "abcdefghjk"), rr)[1]
    )(FakeRunner()).called("nmcli", "connection", "add")[0]
    assert "802-11-wireless.channel" not in add2


def test_upstream_locked_to_2ghz_on_same_radio():
    """同じ無線チップでは上流を 2.4GHz に固定する（DFS チャンネルで AP が立たない・屋外利用）。"""
    r = FakeRunner()
    mgr(r, ap_if="uap0", same_radio=True).add_and_connect("Home", "password1")
    add = r.called("nmcli", "connection", "add")[0]
    assert add[add.index("802-11-wireless.band") + 1] == "bg"
    # AP を別チップ（USB ドングル）に分けた場合は帯域を縛らない
    r2 = FakeRunner()
    mgr(r2, ap_if="wlan1").add_and_connect("Home", "password1")
    assert "802-11-wireless.band" not in r2.called("nmcli", "connection", "add")[0]


def test_sync_channel_skipped_for_separate_interfaces_or_no_sta():
    assert mgr(FakeRunner(), ap_if="wlan1").sync_ap_channel() is None
    assert mgr(FakeRunner()).sync_ap_channel() is None


def test_mac_address():
    out = "GENERAL.HWADDR:DC\\:A6\\:32\\:01\\:AB\\:CD"
    r = FakeRunner().on(["nmcli", "-t", "-f", "GENERAL.HWADDR"], CommandResult(0, out))
    assert mgr(r).mac_address() == "DC:A6:32:01:AB:CD"
    assert mgr(FakeRunner()).mac_address() is None


def test_wifi_qr_payload_escaping():
    assert net.wifi_qr_payload("a;b", 'p:w"\\,x1') == 'WIFI:T:WPA;S:a\\;b;P:p\\:w\\"\\\\\\,x1;;'
    assert net.wifi_qr_payload("Free", "") == "WIFI:T:nopass;S:Free;;"


def test_generate_ap_credentials():
    ssid, pw = net.generate_ap_credentials("dc:a6:32:01:ab:cd")
    assert ssid == "soikaxa-ABCD" and len(pw) == 10
    assert not set(pw) & set("l1o0i")
    ssid2, _ = net.generate_ap_credentials(None)
    assert ssid2.startswith("soikaxa-") and len(ssid2) == 12
    for name in (ssid, ssid2):
        assert "yt" not in name.lower() and "youtube" not in name.lower()


# ---------- portal ----------


@pytest.mark.parametrize(
    ("host", "path", "expected"),
    [
        ("captive.apple.com", "/hotspot-detect.html", True),
        ("connectivitycheck.gstatic.com", "/generate_204", True),
        ("www.msftconnecttest.com", "/connecttest.txt", True),
        ("example.com", "/", True),
        ("10.42.0.1", "/", False),
        ("10.42.0.1:80", "/api/status", False),
        ("soikaxa.local", "/", False),
        (None, "/", False),
        ("[::1]", "/", True),
    ],
)
def test_is_detection_request(host, path, expected):
    assert portal.is_detection_request(host, path) is expected


def test_dnsmasq_config_resolves_everything_to_ap():
    assert "address=/#/10.42.0.1" in portal.dnsmasq_config()
    assert portal.PORTAL_URL == "http://10.42.0.1/"
