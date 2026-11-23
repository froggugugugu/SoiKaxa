"""設定の永続化。JSON 1 ファイル、原子的書き換え、0600。

後方互換: 未知キーは無視、欠損キーは既定値で補完する。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_DATA_DIR = "/var/lib/soikaxa"


def data_dir() -> Path:
    """データディレクトリ。`SOIKAXA_DATA_DIR` で切替（開発 PC 用）。"""
    return Path(os.environ.get("SOIKAXA_DATA_DIR", DEFAULT_DATA_DIR))


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_private_file(path: Path, text: str) -> None:
    """0600 で原子的に書き込む。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


@dataclass(frozen=True)
class Speaker:
    mac: str
    name: str = ""


@dataclass(frozen=True)
class LastPlaylist:
    adapter: str
    id: str


@dataclass(frozen=True)
class Settings:
    schema_version: int = SCHEMA_VERSION
    device_model: str = "pi4b"
    owner_pin_hash: str | None = None
    ap_ssid: str | None = None
    ap_password: str | None = None
    active_adapter: str = "local"
    last_playlist: LastPlaylist | None = None
    autoplay: bool = True
    repeat: bool = True  # プレイリストの最後の曲のあと先頭に戻る
    touch_cal: dict[str, Any] | None = None  # LCD のタッチの位置合わせ結果（touch.TouchCal.to_dict）
    volume: int = 60
    guest_enabled: bool = True
    allow_play_next: bool = True
    guest_queue_limit: int = 3
    known_speakers: tuple[Speaker, ...] = field(default_factory=tuple)
    probe_track_id: str | None = None
    wifi_labels: dict[str, str] = field(default_factory=dict)  # SSID → 会場名

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["known_speakers"] = [asdict(s) for s in self.known_speakers]
        return d

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Settings:
        names = {f.name for f in fields(cls)}
        kw: dict[str, Any] = {k: v for k, v in raw.items() if k in names}
        lp = kw.get("last_playlist")
        kw["last_playlist"] = LastPlaylist(**lp) if isinstance(lp, dict) else None
        sp = kw.get("known_speakers") or []
        kw["known_speakers"] = tuple(
            Speaker(mac=s["mac"], name=s.get("name", "")) for s in sp if isinstance(s, dict) and "mac" in s
        )
        labels = kw.get("wifi_labels")
        kw["wifi_labels"] = {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
        kw["schema_version"] = SCHEMA_VERSION
        s = cls(**kw)
        return replace(
            s,
            volume=max(0, min(100, int(s.volume))),
            guest_queue_limit=max(1, min(20, int(s.guest_queue_limit))),
        )


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._cache: Settings | None = None

    def load(self) -> Settings:
        with self._lock:
            if self._cache is None:
                self._cache = self._read()
            return self._cache

    def _read(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Settings()
        except (json.JSONDecodeError, OSError):
            # 壊れた設定で起動不能にしない（電源ブチ切り対策）
            return Settings()
        if not isinstance(raw, dict):
            return Settings()
        try:
            return Settings.from_json(raw)
        except (TypeError, ValueError):
            return Settings()

    def save(self, settings: Settings) -> Settings:
        with self._lock:
            write_private_file(self.path, json.dumps(settings.to_json(), ensure_ascii=False, indent=2))
            self._cache = settings
            return settings

    def update(self, **changes: Any) -> Settings:
        current = self.load()
        merged = Settings.from_json({**current.to_json(), **_jsonable(changes)})
        return self.save(merged)

    def reset(self) -> Settings:
        """初期化（PIN・認証・記憶機器を消す）。AP 資格情報は端末固有なので維持する。"""
        cur = self.load()
        return self.save(
            Settings(ap_ssid=cur.ap_ssid, ap_password=cur.ap_password, device_model=cur.device_model)
        )


def _jsonable(changes: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in changes.items():
        if isinstance(v, LastPlaylist):
            out[k] = asdict(v)
        elif k == "known_speakers":
            out[k] = [asdict(s) if isinstance(s, Speaker) else s for s in v]
        else:
            out[k] = v
    return out
