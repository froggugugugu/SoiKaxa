"""Bluetooth（BlueZ / bluetoothctl 非対話モード）。A2DP の音声経路は PipeWire が担う。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .runner import CommandRunner

MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
_DEVICE_LINE = re.compile(r"^Device ([0-9A-Fa-f:]{17}) ?(.*)$")


class InvalidMac(ValueError):
    pass


def validate_mac(mac: str) -> str:
    m = (mac or "").strip().upper()
    if not MAC_RE.fullmatch(m):
        raise InvalidMac("MAC は XX:XX:XX:XX:XX:XX")
    return m


@dataclass(frozen=True)
class BtDevice:
    mac: str
    name: str
    paired: bool = False
    connected: bool = False


def _parse_devices(stdout: str) -> list[tuple[str, str]]:
    out = []
    for line in stdout.splitlines():
        m = _DEVICE_LINE.match(line.strip())
        if m:
            out.append((m.group(1).upper(), m.group(2).strip()))
    return out


def _parse_info(stdout: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for line in stdout.splitlines():
        if ":" in line:
            k, v = line.strip().split(":", 1)
            info.setdefault(k.strip(), v.strip())
    return info


class BluetoothManager:
    def __init__(self, runner: CommandRunner):
        self.r = runner

    def _ctl(self, *args: str, timeout: float = 15.0) -> str:
        return self.r.run(["bluetoothctl", *args], timeout=timeout).stdout

    def scan(self, seconds: int = 8) -> list[BtDevice]:
        self._ctl("--timeout", str(int(seconds)), "scan", "on", timeout=seconds + 5)
        paired = {mac for mac, _ in _parse_devices(self._ctl("devices", "Paired"))}
        devices = []
        for mac, name in _parse_devices(self._ctl("devices")):
            if not name or name.replace("-", ":").upper() == mac:
                continue  # 名前の無い機器（MAC がそのまま名前）は選べないので出さない
            devices.append(BtDevice(mac, name, paired=mac in paired))
        return devices

    def paired(self) -> list[BtDevice]:
        return [
            BtDevice(mac, name, paired=True, connected=self.is_connected(mac))
            for mac, name in _parse_devices(self._ctl("devices", "Paired"))
        ]

    def info(self, mac: str) -> dict[str, str]:
        return _parse_info(self._ctl("info", validate_mac(mac)))

    def is_connected(self, mac: str) -> bool:
        return self.info(mac).get("Connected") == "yes"

    def pair_and_connect(self, mac: str) -> bool:
        mac = validate_mac(mac)
        if self.info(mac).get("Paired") != "yes":
            out = self.r.run(["bluetoothctl", "--timeout", "20", "pair", mac], timeout=25)
            if not out.ok and "AlreadyExists" not in out.stdout + out.stderr:
                return False
        self._ctl("trust", mac)
        return self.connect(mac)

    def connect(self, mac: str) -> bool:
        mac = validate_mac(mac)
        res = self.r.run(["bluetoothctl", "--timeout", "15", "connect", mac], timeout=20)
        return res.ok and ("Connection successful" in res.stdout or self.is_connected(mac))

    def disconnect(self, mac: str) -> bool:
        return self.r.run(["bluetoothctl", "disconnect", validate_mac(mac)]).ok

    def remove(self, mac: str) -> bool:
        """ペアリング情報ごと削除する（初期化用）。"""
        return self.r.run(["bluetoothctl", "remove", validate_mac(mac)]).ok

    def name_of(self, mac: str) -> str:
        return self.info(mac).get("Name", "")
