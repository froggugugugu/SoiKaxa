"""起動配線（ARCH §11）。`python -m soikaxa.daemon` から呼ばれる。

`SOIKAXA_FAKE=1` で mpv / nmcli / bluetoothctl をフェイクに差し替え、開発 PC で Web を触れる。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adapters.registry import load_adapters
from .bt import BluetoothManager
from .config import Settings, SettingsStore, data_dir, ensure_private_dir
from .daemon import PlayerService
from .fakes import FakeRunner, FakeTransport
from .health import load_result
from .net import WifiManager, generate_ap_credentials, wifi_qr_payload
from .player import MpvPlayer, MpvTransport
from .portal import PORTAL_URL
from .runner import CommandResult, CommandRunner, SubprocessRunner
from .state import Event
from .touch import Gesture
from .watchdog import Watchdog, notify

log = logging.getLogger("soikaxa")

NET_POLL_SEC = 30.0
BT_POLL_SEC = 15.0


@dataclass
class Runtime:
    service: PlayerService
    store: SettingsStore
    wifi: WifiManager
    bt: BluetoothManager
    runner: CommandRunner
    data_dir: Path
    fake: bool


def fake_runner() -> FakeRunner:
    r = FakeRunner()
    r.on(
        ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN"],
        CommandResult(0, "*:Home:82:WPA2:6\n :Cafe-Free:60::11\n :Venue 5G:55:WPA2:36"),
    )
    r.on(
        ["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY"],
        CommandResult(0, "sta-Home:802-11-wireless:10"),
    )
    r.on(["bluetoothctl", "devices"], CommandResult(0, "Device AA:BB:CC:DD:EE:FF Fake Speaker"))
    r.on(["bluetoothctl", "info"], CommandResult(0, "Name: Fake Speaker\nPaired: yes\nConnected: yes"))
    r.on(["bluetoothctl", "--timeout", "15", "connect"], CommandResult(0, "Connection successful"))
    return r


def ensure_ap_credentials(store: SettingsStore, wifi: WifiManager) -> Settings:
    s = store.load()
    if s.ap_ssid and s.ap_password:
        return s
    ssid, password = generate_ap_credentials(wifi.mac_address())
    return store.update(ap_ssid=s.ap_ssid or ssid, ap_password=s.ap_password or password)


def build_runtime(fake: bool | None = None) -> Runtime:
    fake = os.environ.get("SOIKAXA_FAKE") == "1" if fake is None else fake
    d = ensure_private_dir(data_dir())
    store = SettingsStore(d / "settings.json")
    runner: CommandRunner = fake_runner() if fake else SubprocessRunner()
    transport: MpvTransport
    if fake:
        transport = FakeTransport()
        http_get = lambda url: (204, "")  # noqa: E731
    else:  # pragma: no cover - 実機
        from .net import default_http_get
        from .player import UnixSocketTransport, spawn_mpv

        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()), "soikaxa-mpv.sock")
        spawn_mpv(sock)
        transport = UnixSocketTransport(sock)
        http_get = default_http_get
    sta_if = os.environ.get("SOIKAXA_STA_IF", "wlan0")
    ap_if = os.environ.get("SOIKAXA_AP_IF", sta_if)
    wifi = WifiManager(runner, sta_if=sta_if, ap_if=ap_if, http_get=http_get)
    bt = BluetoothManager(runner)
    player = MpvPlayer(transport)
    svc = PlayerService(adapters=load_adapters(d), player=player, store=store)
    player.set_volume(store.load().volume)
    return Runtime(svc, store, wifi, bt, runner, d, fake)


def connect_network(rt: Runtime) -> None:
    status = rt.wifi.connectivity()
    if status == "online":
        rt.service.dispatch(Event.NET_UP)
    elif status == "captive":
        rt.service.health_notice = "この Wi-Fi は同意が必要。テザリングへ"
        rt.service.dispatch(Event.FAIL)
    else:
        rt.service.dispatch(Event.NET_DOWN)


def connect_speaker(rt: Runtime) -> bool:
    known = rt.store.load().known_speakers
    if not known:
        return False
    rt.service.dispatch(Event.BT_CONNECTING)
    for sp in known:
        if rt.bt.is_connected(sp.mac) or rt.bt.connect(sp.mac):
            rt.service.dispatch(Event.BT_CONNECTED)
            rt.service.autoplay()
            return True
    rt.service.dispatch(Event.BT_LOST)
    return False


def on_gesture(rt: Runtime, gesture: Gesture, show_setup: Callable[[], None]) -> None:
    if gesture == Gesture.TOGGLE:
        rt.service.toggle()
    elif gesture == Gesture.NEXT:
        rt.service.next()
    elif gesture == Gesture.SETUP_QR:
        show_setup()


def _monitor(rt: Runtime) -> None:  # pragma: no cover - 常駐ループ
    last_bt = 0.0
    while True:
        time.sleep(NET_POLL_SEC)
        try:
            connect_network(rt)
            res = load_result(rt.data_dir / "health.json")
            if res is not None and not res.ok:
                rt.service.health_notice = res.message
            elif res is not None:
                rt.service.health_notice = ""
            if time.monotonic() - last_bt > BT_POLL_SEC and rt.service.state.value in (
                "ONLINE",
                "BT_CONNECTING",
            ):
                last_bt = time.monotonic()
                connect_speaker(rt)
        except Exception:
            log.exception("monitor failed")


def main() -> None:  # pragma: no cover - 実行時配線（各部品は個別にテスト済み）
    import uvicorn

    from .lcd.controller import CoverLoader, LcdController
    from .lcd.fb import Framebuffer
    from .touch import EvdevTouchSource, find_touch_device
    from .web.app import AppContext, create_app

    logging.basicConfig(
        level=os.environ.get("SOIKAXA_LOG", "INFO"), format="%(asctime)s %(name)s %(message)s"
    )
    rt = build_runtime()
    settings = ensure_ap_credentials(rt.store, rt.wifi)

    def qr() -> str:
        s = rt.store.load()
        return wifi_qr_payload(s.ap_ssid or "", s.ap_password or "")

    lcd = None
    fb = Framebuffer(os.environ.get("SOIKAXA_FB", "/dev/fb1"))
    if fb.available or rt.fake:
        if rt.fake and not fb.available:
            fb = Framebuffer(rt.data_dir / "fb.raw")
        lcd = LcdController(rt.service, fb, qr, PORTAL_URL, CoverLoader(rt.data_dir / "covers"))
        lcd.boot_progress = 0.3
        lcd.render_once()
        lcd.start()

    rt.wifi.start_ap(settings.ap_ssid or "", settings.ap_password or "")
    if lcd:
        lcd.boot_progress = 0.6
    connect_network(rt)
    threading.Thread(target=connect_speaker, args=(rt,), daemon=True).start()
    threading.Thread(target=_monitor, args=(rt,), name="monitor", daemon=True).start()

    touch_dev = None if rt.fake else find_touch_device()
    if touch_dev:
        EvdevTouchSource(
            touch_dev, lambda g: on_gesture(rt, g, lcd.show_setup_qr if lcd else lambda: None)
        ).start()

    def run_update() -> bool:
        unit = os.environ.get("SOIKAXA_UPDATE_UNIT", "soikaxa-update.service")
        return rt.runner.run(["systemctl", "start", "--no-block", unit]).ok

    ctx = AppContext(
        service=rt.service,
        store=rt.store,
        wifi=rt.wifi,
        bt=rt.bt,
        run_update=run_update,
        on_ap_change=(lcd.invalidate if lcd else lambda: None),
        extra_hosts=frozenset({os.uname().nodename.lower(), f"{os.uname().nodename.lower()}.local"}),
    )
    app = create_app(ctx)
    Watchdog(healthy=rt.service.healthy).start()
    notify("READY=1")
    port = int(os.environ.get("SOIKAXA_HTTP_PORT", "80"))
    uvicorn.run(app, host=os.environ.get("SOIKAXA_HTTP_HOST", "0.0.0.0"), port=port, log_level="warning")
