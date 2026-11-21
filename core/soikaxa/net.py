"""Wi-Fi 制御（NetworkManager / nmcli）。

- 上流（STA）の登録・接続・削除・スキャン
- 端末 AP（`soikaxa-ap` 接続プロファイル、ipv4.method=shared）の起動とチャンネル追従
- Wi-Fi QR 文字列、AP 資格情報の生成

nmcli は常に `-t`（terse）で呼び、引数配列で渡す（シェルを経由しない）。
"""

from __future__ import annotations

import re
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .runner import CommandRunner

AP_CON_NAME = "soikaxa-ap"
STA_PREFIX = "sta-"
AP_IP = "10.42.0.1"
CONNECTIVITY_URL = "http://connectivitycheck.gstatic.com/generate_204"

ConnectReason = Literal["ok", "wrong_password", "not_found", "captive_portal", "error"]
Connectivity = Literal["online", "captive", "offline"]
HttpGet = Callable[[str], tuple[int, str]]


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    channel: int | None
    in_use: bool


@dataclass(frozen=True)
class SavedNetwork:
    ssid: str
    priority: int


@dataclass(frozen=True)
class ConnectResult:
    ok: bool
    reason: ConnectReason
    message: str = ""


class InvalidWifiInput(ValueError):
    pass


def split_terse(line: str) -> list[str]:
    """nmcli -t の 1 行を分割する（`\\:` と `\\\\` のエスケープを解く）。"""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def validate_ssid(ssid: str) -> str:
    if not isinstance(ssid, str) or not (1 <= len(ssid.encode()) <= 32):
        raise InvalidWifiInput("SSID は 1〜32 バイト")
    if any(ord(c) < 0x20 for c in ssid):
        raise InvalidWifiInput("SSID に制御文字は使えません")
    return ssid


def validate_password(password: str) -> str:
    if password == "":
        return password  # オープンネットワーク
    if not (8 <= len(password) <= 63) or any(ord(c) < 0x20 or ord(c) > 0x7E for c in password):
        raise InvalidWifiInput("パスワードは 8〜63 文字の英数記号")
    return password


def wifi_qr_payload(ssid: str, password: str) -> str:
    """`WIFI:T:WPA;S:<ssid>;P:<pass>;;`。特殊文字 `\\ ; , : "` をエスケープする。"""

    def esc(s: str) -> str:
        return re.sub(r'([\\;,:"])', r"\\\1", s)

    if not password:
        return f"WIFI:T:nopass;S:{esc(ssid)};;"
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


_PW_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 紛らわしい文字（l, 1, o, 0, i）を除く


def generate_ap_credentials(mac: str | None) -> tuple[str, str]:
    hexes = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    suffix = hexes[-4:].upper() if len(hexes) >= 4 else secrets.token_hex(2).upper()
    password = "".join(secrets.choice(_PW_ALPHABET) for _ in range(10))
    return f"soikaxa-{suffix}", password


def default_http_get(url: str) -> tuple[int, str]:  # pragma: no cover - ネットワーク依存
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(url, timeout=5) as r:  # noqa: S310 - 固定 URL
            return r.status, r.read(512).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError):
        return 0, ""


def classify_connectivity(status: int, body: str) -> Connectivity:
    if status == 204:
        return "online"
    if status == 0:
        return "offline"
    return "captive"  # 200 + HTML やリダイレクトは同意画面


def _classify_failure(stderr: str) -> ConnectReason:
    s = stderr.lower()
    if "secrets were required" in s or "psk" in s or "802-1x" in s or "authentication" in s:
        return "wrong_password"
    if "no network with ssid" in s or "not found" in s:
        return "not_found"
    return "error"


class WifiManager:
    def __init__(
        self,
        runner: CommandRunner,
        sta_if: str = "wlan0",
        ap_if: str = "wlan0",
        http_get: HttpGet = default_http_get,
    ):
        self.r = runner
        self.sta_if = sta_if
        self.ap_if = ap_if
        self.http_get = http_get

    # ---- スキャン ----
    def scan(self, rescan: bool = True) -> list[WifiNetwork]:
        res = self.r.run(
            [
                "nmcli",
                "-t",
                "-f",
                "IN-USE,SSID,SIGNAL,SECURITY,CHAN",
                "dev",
                "wifi",
                "list",
                "ifname",
                self.sta_if,
                "--rescan",
                "yes" if rescan else "no",
            ],
            timeout=20,
        )
        best: dict[str, WifiNetwork] = {}
        for line in res.stdout.splitlines():
            parts = split_terse(line)
            if len(parts) < 5 or not parts[1]:
                continue  # ステルス（SSID 空）は一覧に出さない
            in_use, ssid, signal, security, chan = parts[:5]
            n = WifiNetwork(
                ssid=ssid,
                signal=int(signal) if signal.isdigit() else 0,
                security=security,
                channel=int(chan) if chan.isdigit() else None,
                in_use=in_use.strip() == "*",
            )
            if ssid not in best or n.signal > best[ssid].signal or n.in_use:
                best[ssid] = n
        return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal))

    def saved(self) -> list[SavedNetwork]:
        res = self.r.run(["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY", "connection", "show"])
        out = []
        for line in res.stdout.splitlines():
            parts = split_terse(line)
            if len(parts) >= 3 and parts[0].startswith(STA_PREFIX) and "wireless" in parts[1]:
                prio = int(parts[2]) if parts[2].lstrip("-").isdigit() else 0
                out.append(SavedNetwork(parts[0][len(STA_PREFIX) :], prio))
        return sorted(out, key=lambda s: -s.priority)

    # ---- 登録・接続 ----
    def add_and_connect(
        self, ssid: str, password: str, *, hidden: bool = False, priority: int = 10
    ) -> ConnectResult:
        validate_ssid(ssid)
        validate_password(password)
        name = STA_PREFIX + ssid
        self.r.run(["nmcli", "connection", "delete", name])  # 既存なら置き換え（無ければ失敗して良い）
        args = [
            "nmcli",
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            self.sta_if,
            "con-name",
            name,
            "ssid",
            ssid,
            "connection.autoconnect",
            "yes",
            "connection.autoconnect-priority",
            str(int(priority)),
            "802-11-wireless.hidden",
            "yes" if hidden else "no",
        ]
        if password:
            args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
        added = self.r.run(args)
        if not added.ok:
            return ConnectResult(False, "error", added.stderr.strip()[:200])
        up = self.r.run(["nmcli", "connection", "up", name], timeout=45)
        if not up.ok:
            self.r.run(["nmcli", "connection", "delete", name])
            reason = _classify_failure(up.stderr)
            if reason == "not_found" and not hidden:
                return ConnectResult(False, "not_found", "見つかりません。ステルスなら「ステルス」をオンに")
            msg = {"wrong_password": "パスワードが違います", "not_found": "見つかりません"}.get(
                reason, "接続失敗"
            )
            return ConnectResult(False, reason, msg)
        self.sync_ap_channel()
        if self.connectivity() == "captive":
            return ConnectResult(
                False, "captive_portal", "この Wi-Fi は同意が必要です。テザリングに切り替えてください"
            )
        return ConnectResult(True, "ok", "接続しました")

    def delete(self, ssid: str) -> bool:
        validate_ssid(ssid)
        return self.r.run(["nmcli", "connection", "delete", STA_PREFIX + ssid]).ok

    def connectivity(self) -> Connectivity:
        status, body = self.http_get(CONNECTIVITY_URL)
        return classify_connectivity(status, body)

    # ---- AP ----
    def start_ap(self, ssid: str, password: str, channel: int | None = None) -> bool:
        validate_ssid(ssid)
        validate_password(password)
        self.r.run(["nmcli", "connection", "delete", AP_CON_NAME])
        args = [
            "nmcli",
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            self.ap_if,
            "con-name",
            AP_CON_NAME,
            "ssid",
            ssid,
            "802-11-wireless.mode",
            "ap",
            "connection.autoconnect",
            "yes",
            "ipv4.method",
            "shared",
            "ipv4.addresses",
            f"{AP_IP}/24",
            "ipv6.method",
            "disabled",
        ]
        args += self._band_args(channel)
        if password:
            args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
        if not self.r.run(args).ok:
            return False
        return self.r.run(["nmcli", "connection", "up", AP_CON_NAME], timeout=30).ok

    @staticmethod
    def _band_args(channel: int | None) -> list[str]:
        if channel is None:
            return ["802-11-wireless.band", "bg"]
        band = "bg" if channel <= 14 else "a"
        return ["802-11-wireless.band", band, "802-11-wireless.channel", str(channel)]

    def sta_channel(self) -> int | None:
        for n in self.scan(rescan=False):
            if n.in_use:
                return n.channel
        return None

    def sync_ap_channel(self) -> int | None:
        """STA と AP が同一 IF の場合、AP を STA と同じチャンネルへ寄せる（同時動作の前提）。"""
        if self.sta_if != self.ap_if:
            return None
        ch = self.sta_channel()
        if ch is None:
            return None
        self.r.run(["nmcli", "connection", "modify", AP_CON_NAME, *self._band_args(ch)])
        self.r.run(["nmcli", "connection", "up", AP_CON_NAME], timeout=30)
        return ch

    def mac_address(self) -> str | None:
        res = self.r.run(["nmcli", "-t", "-f", "GENERAL.HWADDR", "device", "show", self.ap_if])
        for line in res.stdout.splitlines():
            parts = split_terse(line)
            if len(parts) >= 2 and parts[0] == "GENERAL.HWADDR":
                return ":".join(parts[1:])
        return None
