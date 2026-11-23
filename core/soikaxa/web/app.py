"""Web アプリ（FastAPI + 単一 HTML）。ARCH §8 のルーティング・§12 のセキュリティ方針に従う。

- ゲスト API: 検索結果キャッシュにある ID だけを追加・展開できる（クライアントのメタデータを信用しない）
- オーナー API: PIN → サーバー側セッション（HttpOnly Cookie）+ CSRF ヘッダ
- 接続検知 URL・外部ホスト宛ては端末のポータルへ 302
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..adapters.base import AdapterError, AuthRequired, Playlist, Track
from ..adapters.capabilities import AuthCapable
from ..auth import PinGuard, PinLocked, SessionStore, SetupCode, hash_pin, verify_pin
from ..bt import BluetoothManager, InvalidMac
from ..config import SettingsStore, Speaker
from ..daemon import PlayerService, StatusView, TrackView
from ..net import InvalidWifiInput, WifiManager, generate_ap_credentials, wifi_qr_payload
from ..portal import PORTAL_URL, is_detection_request
from ..queue import ItemNotFound, QueueLimitExceeded
from ..state import Event
from ..upgrade import UpgradeError, Upgrader, installed_version
from . import schemas as s

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
OWNER_COOKIE = "sk_owner"
GUEST_COOKIE = "sk_gid"
CSRF_HEADER = "x-soikaxa-csrf"
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


class SearchCache:
    """ゲストに見せた検索結果（LRU）。追加・展開はここにある ID のみ許す。"""

    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        self._items: OrderedDict[tuple[str, str], Track | Playlist] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, item: Track | Playlist) -> None:
        key = ("track" if isinstance(item, Track) else "playlist", item.id)
        with self._lock:
            self._items[key] = item
            self._items.move_to_end(key)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)

    def track(self, track_id: str) -> Track | None:
        with self._lock:
            v = self._items.get(("track", track_id))
            return v if isinstance(v, Track) else None

    def playlist(self, playlist_id: str) -> Playlist | None:
        with self._lock:
            v = self._items.get(("playlist", playlist_id))
            return v if isinstance(v, Playlist) else None


class RateLimiter:
    """キーごとの回数制限（直近 window 秒で limit 回まで）。"""

    def __init__(
        self, limit: int = 30, window_sec: float = 60.0, clock: Callable[[], float] = time.monotonic
    ):
        self.limit, self.window_sec, self._clock = limit, window_sec, clock
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            if len(self._hits) > 1000:  # 際限なく溜めない
                self._hits.clear()
            hits = [t for t in self._hits.get(key, []) if now - t < self.window_sec]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


Network = ipaddress.IPv4Network | ipaddress.IPv6Network
MAX_GUEST_PLAYLIST_TRACKS = 200
MAX_SEARCH_CHARS = 300  # 曲名のほか、音源アプリの共有リンク（プレイリストは 100 文字を超える）も受ける


@dataclass
class AppContext:
    service: PlayerService
    store: SettingsStore
    wifi: WifiManager
    bt: BluetoothManager
    sessions: SessionStore = field(default_factory=SessionStore)
    pin_guard: PinGuard = field(default_factory=PinGuard)
    cache: SearchCache = field(default_factory=SearchCache)
    run_update: Callable[[], bool] = lambda: False
    upgrader: Upgrader | None = None  # None = ソフトウェア更新に未対応（開発 PC など）
    on_ap_change: Callable[[], None] = lambda: None
    extra_hosts: frozenset[str] = frozenset()
    secure_cookies: bool = False  # 端末 AP は http のみ
    setup_code: SetupCode = field(default_factory=SetupCode)
    search_limiter: RateLimiter = field(default_factory=RateLimiter)
    # 空なら制限なし（テスト用）。実機では端末 AP のサブネットとループバックだけを許可する
    allowed_networks: tuple[Network, ...] = ()
    scrypt_slots: threading.BoundedSemaphore = field(default_factory=lambda: threading.BoundedSemaphore(2))
    setup_lock: threading.Lock = field(default_factory=threading.Lock)


# ---------- 変換 ----------


def _track_json(t: TrackView | None) -> dict[str, Any] | None:
    if t is None:
        return None
    out = asdict(t)
    out.pop("item_id", None)  # LCD 用の内部 ID。キュー操作の ID はオーナー API だけで返す
    return out


def _status_json(st: StatusView) -> dict[str, Any]:
    return {
        "state": st.state.value,
        "now": _track_json(st.now),
        "paused": st.paused,
        "position": st.position,
        "duration": st.duration,
        "upcoming": [_track_json(u) for u in st.upcoming],
        "volume": st.volume,
        "notice": st.notice,
    }


def _item_json(x: Track | Playlist) -> dict[str, Any]:
    if isinstance(x, Track):
        return {
            "kind": "track",
            "id": x.id,
            "title": x.title,
            "artist": x.artist,
            "duration_sec": x.duration_sec,
        }
    return {"kind": "playlist", "id": x.id, "title": x.title, "track_count": x.track_count}


def _err(status: int, code: str, message: str = "") -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def create_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="SoiKaxa", docs_url=None, redoc_url=None, openapi_url=None)
    svc, store = ctx.service, ctx.store
    if store.load().owner_pin_hash is None:
        ctx.setup_code.ensure()

    def client_allowed(request: Request) -> bool:
        if not ctx.allowed_networks:
            return True
        try:
            addr = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError:
            return False
        return any(addr in n for n in ctx.allowed_networks)

    @app.middleware("http")
    async def portal_and_headers(request: Request, call_next):
        # 上流（会場 Wi-Fi）側からは受け付けない。Host ヘッダは偽装できるので送信元アドレスで判定する
        if not client_allowed(request):
            return JSONResponse({"detail": {"error": "forbidden_network"}}, status_code=403)
        if is_detection_request(request.headers.get("host"), request.url.path, ctx.extra_hosts):
            return RedirectResponse(PORTAL_URL, status_code=302)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        static = request.url.path.startswith("/static/")
        resp.headers["Cache-Control"] = "public, max-age=86400" if static else "no-store"
        return resp

    @app.exception_handler(AuthRequired)
    async def _auth_required(request: Request, exc: AuthRequired) -> JSONResponse:
        return JSONResponse({"detail": {"error": "auth_required", "message": str(exc)}}, status_code=409)

    @app.exception_handler(AdapterError)
    async def _adapter_error(request: Request, exc: AdapterError) -> JSONResponse:
        return JSONResponse(
            {"detail": {"error": "source_error", "message": "音源に接続できません"}}, status_code=502
        )

    @app.exception_handler(InvalidWifiInput)
    async def _wifi_error(request: Request, exc: InvalidWifiInput) -> JSONResponse:
        return JSONResponse({"detail": {"error": "invalid", "message": str(exc)}}, status_code=422)

    # ---------- 依存 ----------
    def is_owner(request: Request) -> bool:
        return ctx.sessions.check(request.cookies.get(OWNER_COOKIE))

    def require_owner(request: Request) -> str:
        sid = request.cookies.get(OWNER_COOKIE)
        if not ctx.sessions.check(sid):
            raise _err(401, "owner_required")
        if request.method not in ("GET", "HEAD") and not ctx.sessions.check_csrf(
            sid, request.headers.get(CSRF_HEADER)
        ):
            raise _err(403, "csrf")
        return sid or ""

    def require_guest_access(request: Request) -> None:
        if not store.load().guest_enabled and not is_owner(request):
            raise _err(403, "guest_disabled", "ゲスト受付は停止中です")

    def guest_id(request: Request) -> str | None:
        gid = request.cookies.get(GUEST_COOKIE)
        return gid if gid and len(gid) <= 64 else None

    def login_response(resp: Response) -> dict[str, str]:
        sid, csrf = ctx.sessions.create()
        resp.set_cookie(
            OWNER_COOKIE, sid, httponly=True, samesite="strict", secure=ctx.secure_cookies, path="/"
        )
        return {"csrf": csrf}

    # ---------- 公開 ----------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/status")
    def status(request: Request, response: Response) -> dict[str, Any]:
        if not guest_id(request):
            response.set_cookie(
                GUEST_COOKIE,
                secrets.token_urlsafe(16),
                httponly=True,
                samesite="lax",
                secure=ctx.secure_cookies,
                max_age=365 * 86400,
                path="/",
            )
        st = store.load()
        owner = is_owner(request)
        out = _status_json(svc.status())
        out.update(
            guest_enabled=st.guest_enabled,
            allow_play_next=st.allow_play_next,
            guest_queue_limit=st.guest_queue_limit,
            is_owner=owner,
            has_pin=st.owner_pin_hash is not None,
        )
        if owner:
            out["csrf"] = ctx.sessions.csrf_for(request.cookies.get(OWNER_COOKIE))
        return out

    # ---------- ゲスト ----------
    def limit_search(request: Request) -> None:
        if is_owner(request):
            return
        key = request.client.host if request.client else "unknown"
        if not ctx.search_limiter.allow(key):
            raise _err(429, "rate", "検索が多すぎます。少し待ってください")

    guest_search = [Depends(require_guest_access), Depends(limit_search)]

    @app.get("/api/search", dependencies=guest_search)
    def search(q: str) -> dict[str, Any]:
        q = q.strip()
        if not 1 <= len(q) <= MAX_SEARCH_CHARS:
            raise _err(422, "invalid", f"1〜{MAX_SEARCH_CHARS} 文字で検索してください")
        results = svc.search(q, 20)
        for r in results:
            ctx.cache.put(r)
        return {"results": [_item_json(r) for r in results]}

    @app.get("/api/search/playlists/{playlist_id}", dependencies=guest_search)
    def search_playlist(playlist_id: str) -> dict[str, Any]:
        pl = ctx.cache.playlist(playlist_id)
        if pl is None:
            raise _err(404, "not_found")
        # 検索した時点の音源で展開する（途中で音源を切り替えても壊れない）
        tracks = svc.playlist_tracks(playlist_id, pl.source or None)[:MAX_GUEST_PLAYLIST_TRACKS]
        for t in tracks:
            ctx.cache.put(t)
        return {"results": [_item_json(t) for t in tracks]}

    @app.post("/api/queue", dependencies=[Depends(require_guest_access)])
    def enqueue(body: s.EnqueueIn, request: Request) -> dict[str, Any]:
        owner = is_owner(request)
        if owner and not ctx.sessions.check_csrf(
            request.cookies.get(OWNER_COOKIE), request.headers.get(CSRF_HEADER)
        ):
            owner = False  # CSRF 無しのオーナー Cookie はゲスト扱い
        track = ctx.cache.track(body.track_id)
        if track is None:
            raise _err(404, "not_found", "検索結果から選んでください")
        st = store.load()
        if body.play_next and not st.allow_play_next and not owner:
            raise _err(403, "play_next_disabled", "「この次に流す」は停止中です")
        gid = None if owner else (guest_id(request) or "anonymous")
        try:
            item = svc.enqueue(
                track,
                added_by=body.name.strip(),
                guest_id=gid,
                play_next=body.play_next,
                limit=None if owner else st.guest_queue_limit,
            )
        except QueueLimitExceeded as e:
            raise _err(429, "limit", f"1 人 {st.guest_queue_limit} 曲までです") from e
        return {"item_id": item.item_id}

    # ---------- オーナー: 認証 ----------
    def locked(e: PinLocked) -> HTTPException:
        return _err(423, "locked", f"{int(e.retry_after)} 秒後に再試行（本体を 10 秒長押しで解除）")

    @app.post("/api/owner/setup")
    def owner_setup(body: s.SetupIn, response: Response) -> dict[str, str]:
        """PIN の初回登録。本体 LCD に出ているセットアップコードを知っている人だけができる。"""
        with ctx.setup_lock:  # 確認と保存を 1 つの操作にする（同時登録の後勝ちを防ぐ）
            if store.load().owner_pin_hash is not None:
                raise _err(409, "already_set")
            try:
                ok = ctx.pin_guard.attempt(ctx.setup_code.verify(body.setup_code))
            except PinLocked as e:
                raise locked(e) from e
            if not ok:
                raise _err(
                    403, "wrong_code", "実機の画面に表示されている 6 桁の数字（設定コード）を入れてください"
                )
            store.update(owner_pin_hash=hash_pin(body.pin))
            ctx.setup_code.clear()
        ctx.on_ap_change()  # LCD から設定コードを消す
        return login_response(response)

    @app.post("/api/owner/login")
    def owner_login(body: s.PinIn, response: Response) -> dict[str, str]:
        stored = store.load().owner_pin_hash
        if stored is None:
            raise _err(409, "no_pin")
        try:
            ctx.pin_guard.check()  # ロック中は重い照合（scrypt）をしない
        except PinLocked as e:
            raise locked(e) from e
        if not ctx.scrypt_slots.acquire(blocking=False):
            raise _err(429, "busy", "混み合っています。もう一度試してください")
        try:
            matched = verify_pin(body.pin, stored)
        finally:
            ctx.scrypt_slots.release()
        try:
            ok = ctx.pin_guard.attempt(matched)
        except PinLocked as e:
            raise locked(e) from e
        if not ok:
            raise _err(401, "wrong_pin", "PIN が違います")
        return login_response(response)

    owner = [Depends(require_owner)]

    @app.post("/api/owner/logout", dependencies=owner)
    def owner_logout(request: Request, response: Response) -> dict[str, bool]:
        ctx.sessions.revoke(request.cookies.get(OWNER_COOKIE))
        response.delete_cookie(OWNER_COOKIE, path="/")
        return {"ok": True}

    # ---------- オーナー: 再生 ----------
    @app.get("/api/owner/playlists", dependencies=owner)
    def owner_playlists() -> dict[str, Any]:
        return {
            "adapter": svc.active_name,
            "playlists": [
                {"id": p.id, "title": p.title, "track_count": p.track_count} for p in svc.playlists()
            ],
        }

    @app.post("/api/owner/play", dependencies=owner)
    def owner_play(body: s.PlayIn) -> dict[str, bool]:
        svc.select_playlist(body.playlist_id)
        return {"ok": True}

    @app.post("/api/owner/control", dependencies=owner)
    def owner_control(body: s.ControlIn) -> dict[str, bool]:
        getattr(svc, body.action)()
        return {"ok": True}

    @app.post("/api/owner/volume", dependencies=owner)
    def owner_volume(body: s.VolumeIn) -> dict[str, int]:
        svc.set_volume(body.volume)
        return {"volume": body.volume}

    @app.get("/api/owner/queue", dependencies=owner)
    def owner_queue() -> dict[str, Any]:
        return {
            "items": [
                {
                    "item_id": i.item_id,
                    "title": i.track.title,
                    "artist": i.track.artist,
                    "added_by": i.added_by,
                    "guest_id": i.guest_id,
                }
                for i in svc.queue.items()
            ]
        }

    @app.delete("/api/owner/queue/{item_id}", dependencies=owner)
    def owner_queue_delete(item_id: str) -> dict[str, bool]:
        try:
            svc.remove(item_id)
        except ItemNotFound as e:
            raise _err(404, "not_found") from e
        return {"ok": True}

    @app.post("/api/owner/queue/{item_id}/move", dependencies=owner)
    def owner_queue_move(item_id: str, body: s.MoveIn) -> dict[str, bool]:
        try:
            svc.move(item_id, body.index)
        except ItemNotFound as e:
            raise _err(404, "not_found") from e
        return {"ok": True}

    # ---------- オーナー: 設定 ----------
    def settings_json() -> dict[str, Any]:
        st = store.load()
        return {
            "guest_enabled": st.guest_enabled,
            "allow_play_next": st.allow_play_next,
            "guest_queue_limit": st.guest_queue_limit,
            "autoplay": st.autoplay,
            "active_adapter": svc.active_name,
            "adapters": sorted(svc.adapters),
            "probe_track_id": st.probe_track_id,
        }

    @app.get("/api/owner/settings", dependencies=owner)
    def owner_settings() -> dict[str, Any]:
        return settings_json()

    @app.patch("/api/owner/settings", dependencies=owner)
    def owner_settings_patch(body: s.SettingsPatch) -> dict[str, Any]:
        changes = body.model_dump(exclude_none=True)
        adapter = changes.pop("active_adapter", None)
        if adapter is not None:
            svc.set_active_adapter(adapter)
        if changes:
            store.update(**changes)
        return settings_json()

    # ---------- オーナー: Wi-Fi ----------
    @app.get("/api/owner/wifi/scan", dependencies=owner)
    def wifi_scan() -> dict[str, Any]:
        return {"networks": [asdict(n) for n in ctx.wifi.scan()]}

    @app.get("/api/owner/wifi", dependencies=owner)
    def wifi_saved() -> dict[str, Any]:
        labels = store.load().wifi_labels
        return {
            "saved": [
                {"ssid": n.ssid, "priority": n.priority, "label": labels.get(n.ssid, n.ssid)}
                for n in ctx.wifi.saved()
            ]
        }

    @app.post("/api/owner/wifi", dependencies=owner)
    def wifi_add(body: s.WifiIn) -> dict[str, Any]:
        res = ctx.wifi.add_and_connect(body.ssid, body.password, hidden=body.hidden)
        if res.ok or res.reason == "captive_portal":
            labels = dict(store.load().wifi_labels)
            labels[body.ssid] = body.label.strip() or body.ssid
            store.update(wifi_labels=labels)
        if res.ok:
            svc.net_notice = ""
            svc.dispatch(Event.NET_UP)
        elif res.reason == "captive_portal":
            svc.net_notice = res.message
        return asdict(res)

    @app.delete("/api/owner/wifi/{ssid}", dependencies=owner)
    def wifi_delete(ssid: str) -> dict[str, bool]:
        ok = ctx.wifi.delete(ssid)
        labels = dict(store.load().wifi_labels)
        if labels.pop(ssid, None) is not None:
            store.update(wifi_labels=labels)
        return {"ok": ok}

    @app.get("/api/owner/ap", dependencies=owner)
    def ap_info() -> dict[str, Any]:
        st = store.load()
        return {
            "ssid": st.ap_ssid,
            "password": st.ap_password,
            "qr": wifi_qr_payload(st.ap_ssid or "", st.ap_password or ""),
        }

    def new_ap() -> None:
        """AP パスワードを作り直して張り直す。SSID が未保存なら一緒に保存する。"""
        ssid, password = generate_ap_credentials(ctx.wifi.mac_address())
        ssid = store.load().ap_ssid or ssid
        store.update(ap_ssid=ssid, ap_password=password)
        ctx.wifi.start_ap(ssid, password)
        ctx.on_ap_change()

    @app.post("/api/owner/ap/regenerate", dependencies=owner)
    def ap_regenerate() -> dict[str, Any]:
        new_ap()
        return ap_info()

    # ---------- オーナー: Bluetooth ----------
    @app.get("/api/owner/bt", dependencies=owner)
    def bt_state() -> dict[str, Any]:
        known = store.load().known_speakers
        return {
            "known": [{"mac": k.mac, "name": k.name, "connected": ctx.bt.is_connected(k.mac)} for k in known]
        }

    @app.get("/api/owner/bt/scan", dependencies=owner)
    def bt_scan() -> dict[str, Any]:
        return {"devices": [asdict(d) for d in ctx.bt.scan()]}

    @app.post("/api/owner/bt/connect", dependencies=owner)
    def bt_connect(body: s.BtConnectIn) -> dict[str, Any]:
        try:
            ok = ctx.bt.pair_and_connect(body.mac)
        except InvalidMac as e:
            raise _err(422, "invalid", str(e)) from e
        if not ok:
            return {"ok": False, "message": "スピーカーに接続できません。ペアリングモードを確認してください"}
        mac = body.mac.upper()
        name = ctx.bt.name_of(mac)
        speakers = [Speaker(mac, name)] + [k for k in store.load().known_speakers if k.mac != mac]
        store.update(known_speakers=speakers[:5])
        svc.dispatch(Event.BT_CONNECTED)
        svc.autoplay()
        return {"ok": True, "name": name}

    # ---------- オーナー: 音源認証 ----------
    def auth_adapter(name: str) -> AuthCapable:
        a = svc.adapters.get(name)
        if not isinstance(a, AuthCapable):
            raise _err(404, "not_found")
        return a

    @app.get("/api/owner/source/auth", dependencies=owner)
    def auth_list() -> dict[str, Any]:
        return {
            "adapters": [
                {"adapter": n, "registered": a.has_auth()}
                for n, a in sorted(svc.adapters.items())
                if isinstance(a, AuthCapable)
            ]
        }

    @app.post("/api/owner/source/auth", dependencies=owner)
    def auth_register(body: s.AuthIn) -> dict[str, bool]:
        try:
            auth_adapter(body.adapter).register_auth(body.raw)
        except AdapterError as e:
            raise _err(422, "invalid", str(e)) from e
        return {"ok": True}

    @app.delete("/api/owner/source/auth", dependencies=owner)
    def auth_delete(body: s.AuthDeleteIn) -> dict[str, bool]:
        auth_adapter(body.adapter).clear_auth()
        return {"ok": True}

    # ---------- オーナー: 保守 ----------
    @app.post("/api/owner/update", dependencies=owner)
    def update_now() -> dict[str, bool]:
        return {"ok": ctx.run_update()}

    @app.get("/api/owner/upgrade", dependencies=owner)
    def upgrade_status() -> dict[str, Any]:
        up = ctx.upgrader
        if up is None:
            return {"supported": False, "current": installed_version(), "status": {"state": "idle"}}
        return {"supported": True, "current": up.current, "status": up.status()}

    def require_upgrader() -> Upgrader:
        if ctx.upgrader is None:
            raise _err(404, "not_supported", "この端末ではソフトウェア更新を使えません")
        return ctx.upgrader

    @app.post("/api/owner/upgrade/check", dependencies=owner)
    def upgrade_check() -> dict[str, Any]:
        try:
            return require_upgrader().check()
        except UpgradeError as e:
            raise _err(502, "network", str(e)) from e

    @app.post("/api/owner/upgrade", dependencies=owner)
    def upgrade_start() -> dict[str, Any]:
        up = require_upgrader()
        if up.busy():
            raise _err(409, "busy", "更新を実行中です")
        try:
            if not up.check()["available"]:
                raise _err(409, "up_to_date", "すでに最新です")
            ok = up.start()
        except UpgradeError as e:
            raise _err(502, "network", str(e)) from e
        return {"ok": ok, "status": up.status()}

    @app.post("/api/owner/reset", dependencies=owner)
    def reset(body: s.ResetIn, response: Response) -> dict[str, bool]:
        for a in svc.adapters.values():
            if isinstance(a, AuthCapable):
                a.clear_auth()
        for n in ctx.wifi.saved():
            ctx.wifi.delete(n.ssid)
        for sp in store.load().known_speakers:
            ctx.bt.remove(sp.mac)  # ペアリングも消す（初期化後に勝手に再接続させない）
        svc.queue.clear()
        store.reset()
        ctx.sessions.revoke_all()
        response.delete_cookie(OWNER_COOKIE, path="/")
        # PIN が無い状態に戻る。それまで AP に入れた人が先に PIN を登録できないよう、
        # 設定コードと AP パスワードを作り直す（どちらも本体の LCD にだけ出る）
        ctx.setup_code.regenerate()
        new_ap()
        return {"ok": True}

    return app
