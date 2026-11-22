"""起動配線（ARCH §11）。`python -m soikaxa.daemon` から呼ばれる。

`SOIKAXA_FAKE=1` で mpv / nmcli / bluetoothctl をフェイクに差し替え、開発 PC で Web を触れる。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adapters.registry import load_adapters
from .auth import PinGuard, SetupCode
from .bt import BluetoothManager
from .config import Settings, SettingsStore, data_dir, ensure_private_dir
from .daemon import PlayerService
from .fakes import FakeRunner, FakeTransport
from .health import load_result
from .net import AP_IP, WifiManager, generate_ap_credentials, wifi_qr_payload
from .player import MpvPlayer, MpvTransport
from .portal import PORTAL_URL
from .runner import CommandResult, CommandRunner, SubprocessRunner
from .state import Event, State
from .touch import Gesture
from .watchdog import Watchdog, notify

log = logging.getLogger("soikaxa")

POLL_SEC = 15.0  # スピーカーの確認間隔
NET_EVERY = 2  # 上流とヘルスチェックの確認は POLL_SEC × NET_EVERY ごと
MSG_CAPTIVE = "この Wi-Fi は同意が必要。テザリングへ"


@dataclass
class Runtime:
    service: PlayerService
    store: SettingsStore
    wifi: WifiManager
    bt: BluetoothManager
    runner: CommandRunner
    data_dir: Path
    fake: bool
    speaker_connected: bool = False


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
    # AP を wlan0 上の仮想 IF（uap0）にした場合は SOIKAXA_AP_SAME_RADIO=1。USB ドングルなら未設定
    same_radio = True if os.environ.get("SOIKAXA_AP_SAME_RADIO") == "1" else None
    wifi = WifiManager(runner, sta_if=sta_if, ap_if=ap_if, http_get=http_get, same_radio=same_radio)
    bt = BluetoothManager(runner)
    player = MpvPlayer(transport)
    svc = PlayerService(adapters=load_adapters(d), player=player, store=store)
    player.set_volume(store.load().volume)
    return Runtime(svc, store, wifi, bt, runner, d, fake)


def connect_network(rt: Runtime) -> None:
    status = rt.wifi.connectivity()
    if status == "online":
        rt.service.net_notice = ""
        rt.service.dispatch(Event.NET_UP)
    elif status == "captive":
        rt.service.net_notice = MSG_CAPTIVE
        rt.service.dispatch(Event.FAIL)
    else:
        rt.service.net_notice = ""
        rt.service.dispatch(Event.NET_DOWN)


def apply_health(rt: Runtime) -> None:
    """毎朝のヘルスチェック結果を通知に反映する（上流ネットワーク由来の通知とは別枠）。"""
    res = load_result(rt.data_dir / "health.json")
    if res is not None:
        rt.service.health_notice = "" if res.ok else res.message


def connect_speaker(rt: Runtime) -> bool:
    """記憶スピーカーの接続を確かめ、切れていれば再接続する。全状態で定期的に呼ぶ。

    自動再生は「未接続 → 接続」に変わったときだけ（再生が終わるたびに始め直さない）。
    """
    known = rt.store.load().known_speakers
    if not known:
        rt.speaker_connected = False
        return False
    svc = rt.service
    connected = any(rt.bt.is_connected(sp.mac) for sp in known)
    if not connected:
        if svc.state in (State.IDLE, State.PLAYING):
            svc.dispatch(Event.BT_LOST)  # → BT_CONNECTING
        svc.dispatch(Event.BT_CONNECTING)
        connected = any(rt.bt.connect(sp.mac) for sp in known)
    if not connected:
        if svc.state == State.BT_CONNECTING:
            svc.dispatch(Event.BT_LOST)  # → ONLINE（次の周回でまた試す）
        rt.speaker_connected = False
        return False
    svc.dispatch(Event.BT_CONNECTED)
    if not rt.speaker_connected:
        rt.speaker_connected = True
        svc.autoplay()
    return True


def on_gesture(
    rt: Runtime, gesture: Gesture, show_setup: Callable[[], None], unlock: Callable[[], None] = lambda: None
) -> None:
    if gesture == Gesture.TOGGLE:
        rt.service.toggle()
    elif gesture == Gesture.NEXT:
        rt.service.next()
    elif gesture == Gesture.SETUP_QR:
        unlock()  # 本体の長押しは「その場にいる」証明。PIN のロックを解く
        show_setup()


def monitor_step(rt: Runtime, n: int) -> None:
    """監視 1 周分。n は周回番号。"""
    if n % NET_EVERY == 0:
        connect_network(rt)
        apply_health(rt)
    connect_speaker(rt)


def _monitor(rt: Runtime) -> None:  # pragma: no cover - 常駐ループ
    n = 0
    while True:
        time.sleep(POLL_SEC)
        n += 1
        try:
            monitor_step(rt, n)
        except Exception:
            log.exception("monitor failed")


def main() -> None:  # pragma: no cover - 実行時配線（各部品は個別にテスト済み）
    import uvicorn

    from .lcd.controller import CoverLoader, LcdController
    from .lcd.fb import Framebuffer, find_lcd_fb
    from .touch import EvdevTouchSource, find_touch_device
    from .web.app import AppContext, create_app

    logging.basicConfig(
        level=os.environ.get("SOIKAXA_LOG", "INFO"), format="%(asctime)s %(name)s %(message)s"
    )
    rt = build_runtime()
    settings = ensure_ap_credentials(rt.store, rt.wifi)
    setup_code = SetupCode()
    pin_guard = PinGuard()

    def qr() -> str:
        s = rt.store.load()
        return wifi_qr_payload(s.ap_ssid or "", s.ap_password or "")

    def code() -> str | None:
        return setup_code.current if rt.store.load().owner_pin_hash is None else None

    lcd = None
    fb = Framebuffer(os.environ.get("SOIKAXA_FB") or find_lcd_fb())
    if fb.available or rt.fake:
        if rt.fake and not fb.available:
            fb = Framebuffer(rt.data_dir / "fb.raw")
        lcd = LcdController(
            rt.service, fb, qr, PORTAL_URL, CoverLoader(rt.data_dir / "covers"), setup_code=code
        )
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
        show = lcd.show_setup_qr if lcd else (lambda: None)
        EvdevTouchSource(touch_dev, lambda g: on_gesture(rt, g, show, pin_guard.reset)).start()

    def run_update() -> bool:
        unit = os.environ.get("SOIKAXA_UPDATE_UNIT")  # soikaxa@.service が設定する
        if not unit:
            return False
        return rt.runner.run(["systemctl", "start", "--no-block", unit]).ok

    # 端末 AP の参加者と端末自身からだけ受け付ける（上流の会場 Wi-Fi 側からは 403）
    nets = os.environ.get("SOIKAXA_ALLOWED_NETS", f"{AP_IP}/24,127.0.0.0/8,::1/128")
    ctx = AppContext(
        service=rt.service,
        store=rt.store,
        wifi=rt.wifi,
        bt=rt.bt,
        pin_guard=pin_guard,
        setup_code=setup_code,
        allowed_networks=tuple(
            ipaddress.ip_network(n.strip(), strict=False) for n in nets.split(",") if n.strip()
        ),
        run_update=run_update,
        on_ap_change=(lcd.invalidate if lcd else lambda: None),
        extra_hosts=frozenset({os.uname().nodename.lower(), f"{os.uname().nodename.lower()}.local"}),
    )
    app = create_app(ctx)
    if rt.fake and (c := code()):
        log.info("setup code (fake mode only): %s", c)
    Watchdog(healthy=rt.service.healthy).start()
    notify("READY=1")
    port = int(os.environ.get("SOIKAXA_HTTP_PORT", "80"))
    uvicorn.run(app, host=os.environ.get("SOIKAXA_HTTP_HOST", "0.0.0.0"), port=port, log_level="warning")
