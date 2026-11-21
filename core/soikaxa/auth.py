"""オーナー PIN とセッション。

- PIN は scrypt でハッシュ化して保存（平文を残さない）
- PinGuard: 連続失敗でロック（総当たり対策）
- SessionStore: サーバー側セッション + CSRF トークン（メモリのみ）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

PIN_PATTERN = r"^[0-9]{6,8}$"
_PIN_RE = re.compile(PIN_PATTERN)
_N, _R, _P = 2**14, 8, 1


class InvalidPin(ValueError):
    pass


def validate_pin(pin: str) -> str:
    if not isinstance(pin, str) or not _PIN_RE.fullmatch(pin):
        raise InvalidPin("PIN は 6〜8 桁の数字")
    return pin


def hash_pin(pin: str) -> str:
    validate_pin(pin)
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(dk).decode()}"


def verify_pin(pin: str, stored: str | None) -> bool:
    if not stored or not isinstance(pin, str):
        return False
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(pin.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


class PinLocked(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"locked for {retry_after:.0f}s")
        self.retry_after = retry_after


class PinGuard:
    """端末全体で失敗回数を数える（端末 AP 内の少人数前提）。

    ロックは 2 倍ずつ延びる（上限 max_lockout_sec）。成功か、本体操作による reset() で元に戻る。
    """

    def __init__(
        self,
        max_attempts: int = 5,
        lockout_sec: float = 300.0,
        clock: Callable[[], float] = time.time,
        max_lockout_sec: float = 3600.0,
    ):
        self.max_attempts = max_attempts
        self.lockout_sec = lockout_sec
        self.max_lockout_sec = max_lockout_sec
        self._clock = clock
        self._failures = 0
        self._lockouts = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def reset(self) -> None:
        """ロックを解除する。本体の長押し（その場にいる人だけができる操作）から呼ぶ。"""
        with self._lock:
            self._failures = 0
            self._lockouts = 0
            self._locked_until = 0.0

    def check(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._locked_until:
                raise PinLocked(self._locked_until - now)

    def attempt(self, ok: bool) -> bool:
        """結果を記録する。ロック中は PinLocked。"""
        self.check()
        with self._lock:
            if ok:
                self._failures = 0
                self._lockouts = 0
                return True
            self._failures += 1
            if self._failures >= self.max_attempts:
                self._failures = 0
                duration = min(self.lockout_sec * (2**self._lockouts), self.max_lockout_sec)
                self._lockouts += 1
                self._locked_until = self._clock() + duration
            return False


class SetupCode:
    """オーナー PIN 未設定の間だけ有効な 6 桁コード。本体の LCD にだけ表示する。

    AP に入れるだけのゲストが先に PIN を登録してオーナーを名乗ることを防ぐ。
    """

    def __init__(self) -> None:
        self._code: str | None = None
        self._lock = threading.Lock()

    def ensure(self) -> str:
        with self._lock:
            if self._code is None:
                self._code = f"{secrets.randbelow(1_000_000):06d}"
            return self._code

    def regenerate(self) -> str:
        with self._lock:
            self._code = None
        return self.ensure()

    def clear(self) -> None:
        with self._lock:
            self._code = None

    @property
    def current(self) -> str | None:
        return self._code

    def verify(self, code: str) -> bool:
        cur = self._code
        return bool(cur and isinstance(code, str) and hmac.compare_digest(cur, code))


@dataclass
class _Session:
    csrf: str
    expires_at: float


class SessionStore:
    def __init__(self, ttl_sec: float = 4 * 3600, clock: Callable[[], float] = time.time):
        self.ttl_sec = ttl_sec
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, str]:
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self._lock:
            self._sessions[sid] = _Session(csrf, self._clock() + self.ttl_sec)
        return sid, csrf

    def _get(self, sid: str | None) -> _Session | None:
        if not sid:
            return None
        with self._lock:
            s = self._sessions.get(sid)
            if s and s.expires_at < self._clock():
                del self._sessions[sid]
                return None
            return s

    def check(self, sid: str | None) -> bool:
        return self._get(sid) is not None

    def check_csrf(self, sid: str | None, token: str | None) -> bool:
        s = self._get(sid)
        return bool(s and token and hmac.compare_digest(s.csrf, token))

    def csrf_for(self, sid: str | None) -> str | None:
        s = self._get(sid)
        return s.csrf if s else None

    def revoke(self, sid: str | None) -> None:
        with self._lock:
            self._sessions.pop(sid or "", None)

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()
