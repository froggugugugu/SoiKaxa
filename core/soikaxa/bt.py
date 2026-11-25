"""Bluetooth（BlueZ / bluetoothctl 非対話モード）。A2DP の音声経路は PipeWire が担う。"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .runner import CommandResult, CommandRunner

log = logging.getLogger(__name__)

MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
_DEVICE_LINE = re.compile(r"^Device ([0-9A-Fa-f:]{17}) ?(.*)$")


# not_found = BlueZ の一覧から消えた（スキャンから時間がたった）
# connect_failed = ペアリングはできたが接続に失敗
PairOutcome = Literal["ok", "not_found", "pair_failed", "connect_failed"]


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


def _log_failure(op: str, mac: str, res: CommandResult) -> None:
    """失敗の理由（`org.bluez.Error.*`）を journal に残す。画面の案内だけでは原因を追えないため。"""
    out = " | ".join(s.strip() for s in (res.stdout, res.stderr) if s.strip())
    log.warning("bt %s %s 失敗 (rc=%d): %s", op, mac, res.returncode, out[-500:] or "出力なし")


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
    """接続系の操作（connect / pair / remove）は 1 本のロックで直列にする。

    監視ループの自動再接続とオーナーの「接続」が同時に `connect` を送ると、BlueZ は片方を
    `InProgress` で失敗させる。手動側はその失敗を古い鍵と見なして組み直しに進み、
    つながりかけた接続まで壊していた。
    """

    # ペアリング直後はスピーカーが自分から接続しに来ることがあり、すぐの connect がぶつかる
    PAIR_SETTLE_SEC = 2.0
    RETRY_WAIT_SEC = 2.0

    def __init__(self, runner: CommandRunner, sleep: Callable[[float], None] = time.sleep):
        self.r = runner
        self._sleep = sleep
        self._lock = threading.RLock()
        self._manual = False

    @property
    def busy(self) -> bool:
        """オーナーの接続操作中。監視ループはこの間、自動再接続を休む。"""
        return self._manual

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

    def pair_and_connect(self, mac: str) -> PairOutcome:
        """オーナーが選んだ機器に接続する。ペアリング済みでもつながらなければ組み直す。

        スピーカー側がペアリングを忘れる（リセット・別機器とのペアリング）と、本体側の古い鍵では
        接続できない。ペアリングモードの機器を前にした明示操作なので、ここでだけ組み直す
        （自動再接続の `connect` では行わない。電源が切れているだけの場合があるため）。
        画面で原因を案内できるよう、どの段階で失敗したかを返す。
        """
        mac = validate_mac(mac)
        self._manual = True  # ロック待ちの間も、監視ループに次の自動再接続を始めさせない
        try:
            with self._lock:
                return self._pair_and_connect(mac)
        finally:
            self._manual = False

    def _pair_and_connect(self, mac: str) -> PairOutcome:
        if self.info(mac).get("Paired") == "yes":
            # 一度の失敗で消さない（電源を入れた直後・スピーカー側からの接続と重なっただけのことがある）
            if self._connect_retry(mac, attempts=2):
                return "ok"
            log.warning("bt: %s は記憶済みの鍵でつながらないため、ペアリングし直します", mac)
            self.remove(mac)
        paired = self._pair(mac)
        if paired != "ok":
            return paired  # 失敗したまま trust しない（Paired: no なのに Trusted: yes が残る）
        self._ctl("trust", mac)
        self._sleep(self.PAIR_SETTLE_SEC)
        return "ok" if self._connect_retry(mac, attempts=3) else "connect_failed"

    def _pair(self, mac: str) -> PairOutcome:
        # `--timeout` 付きの bluetoothctl は失敗しても終了コード 0 で終わることがある。出力と状態で判断する
        res = self.r.run(["bluetoothctl", "--timeout", "20", "pair", mac], timeout=25)
        out = res.stdout + res.stderr
        if "Pairing successful" in out or "AlreadyExists" in out:
            return "ok"
        if "not available" in out:
            _log_failure("pair", mac, res)
            return "not_found"
        if self.info(mac).get("Paired") == "yes":
            return "ok"
        _log_failure("pair", mac, res)
        return "pair_failed"

    def _connect_retry(self, mac: str, attempts: int) -> bool:
        for i in range(attempts):
            if i:
                self._sleep(self.RETRY_WAIT_SEC)
                if self.is_connected(mac):  # 待つ間にスピーカー側からつながった
                    return True
            if self._connect_once(mac):
                return True
        return False

    def connect(self, mac: str) -> bool:
        """1 回だけ接続を試す（監視ループの自動再接続用）。手動の操作中はそちらが終わるまで待つ。"""
        mac = validate_mac(mac)
        with self._lock:
            return self._connect_once(mac)

    def _connect_once(self, mac: str) -> bool:
        res = self.r.run(["bluetoothctl", "--timeout", "15", "connect", mac], timeout=20)
        # pair と同じく終了コードは当てにしない。出力か BlueZ の状態で判断する
        if "Connection successful" in res.stdout or self.is_connected(mac):
            return True
        _log_failure("connect", mac, res)
        return False

    def disconnect(self, mac: str) -> bool:
        return self.r.run(["bluetoothctl", "disconnect", validate_mac(mac)]).ok

    def remove(self, mac: str) -> bool:
        """ペアリング情報ごと削除する（初期化用）。"""
        mac = validate_mac(mac)
        with self._lock:
            return self.r.run(["bluetoothctl", "remove", mac]).ok

    def name_of(self, mac: str) -> str:
        return self.info(mac).get("Name", "")
