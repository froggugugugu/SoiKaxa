"""Web API のリクエストモデル（入力検証）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..auth import PIN_PATTERN


class PinIn(BaseModel):
    pin: str = Field(pattern=PIN_PATTERN)


class SetupIn(BaseModel):
    pin: str = Field(pattern=PIN_PATTERN)
    setup_code: str = Field(pattern=r"^[0-9]{6}$")


class EnqueueIn(BaseModel):
    track_id: str = Field(min_length=1, max_length=512)
    name: str = Field(default="", max_length=20)
    play_next: bool = False


class PlayIn(BaseModel):
    playlist_id: str = Field(min_length=1, max_length=512)
    adapter: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")  # 省略時は今の音源


class SavedPlaylistIn(BaseModel):
    playlist_id: str = Field(min_length=1, max_length=512)
    adapter: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")  # 削除で使う（音源と ID の組）


class ControlIn(BaseModel):
    action: Literal["toggle", "pause", "resume", "next"]


class VolumeIn(BaseModel):
    volume: int = Field(ge=0, le=100)


class MoveIn(BaseModel):
    index: int = Field(ge=0, le=1000)


class SettingsPatch(BaseModel):
    guest_enabled: bool | None = None
    allow_play_next: bool | None = None
    guest_queue_limit: int | None = Field(default=None, ge=1, le=20)
    autoplay: bool | None = None
    repeat: bool | None = None
    active_adapter: str | None = Field(default=None, max_length=64)
    probe_track_id: str | None = Field(default=None, max_length=512)


class WifiIn(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)
    hidden: bool = False
    label: str = Field(default="", max_length=40)


class BtConnectIn(BaseModel):
    mac: str = Field(pattern=r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


class AuthIn(BaseModel):
    adapter: str = Field(min_length=1, max_length=64)
    raw: str = Field(min_length=1, max_length=64_000)


class AuthTestIn(BaseModel):
    adapter: str = Field(min_length=1, max_length=64)


class AuthDeleteIn(BaseModel):
    adapter: str = Field(min_length=1, max_length=64)


class ResetIn(BaseModel):
    confirm: Literal["RESET"]
