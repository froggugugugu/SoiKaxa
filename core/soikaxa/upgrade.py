"""ソフトウェア更新（GitHub Releases）。

確認はデーモンが行い、適用は root の oneshot ユニット（scripts/upgrade.sh）に任せる。
適用の進み具合は upgrade.sh が root 専用の <status_dir>/upgrade.json に書く。デーモンが書けるのは
<data_dir>/upgrade-request.json（依頼済みの印）だけで、画面には新しい方を出す。root が書く場所を
デーモンから書けなくするのは、シンボリックリンクを仕込んで root に別のファイルを書かせないため。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

DEFAULT_REPO = "froggugugugu/SoiKaxa"
STATUS_FILE = "upgrade.json"
REQUEST_FILE = "upgrade-request.json"
DEFAULT_STATUS_DIR = Path("/var/lib/soikaxa-upgrade")
# 「依頼済み・実行中」をこれより古ければ止まったものとみなす（upgrade.sh の上限と同じ 30 分）
STALE_SEC = 30 * 60
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")  # upgrade.sh と同じく 3 桁だけ
_ACTIVE = ("requested", "running")
MAX_NOTES = 2000


class UpgradeError(Exception):
    pass


def parse_version(text: str) -> tuple[int, int, int] | None:
    """'v0.2.0' / '0.2.0' → (0, 2, 0)。形式が違えば None。"""
    m = _TAG.match(text if text.startswith("v") else "v" + text)
    if not m:
        return None
    return (int(m[1]), int(m[2]), int(m[3]))


def installed_version() -> str:
    try:
        return metadata.version("soikaxa")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def github_latest(repo: str, timeout: float = 10.0) -> dict[str, Any] | None:
    """最新リリースの JSON。リリースが 1 つも無ければ None。"""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "soikaxa-upgrader"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  固定の https URL
            data: dict[str, Any] = json.loads(r.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise UpgradeError(f"GitHub が応答しません（{e.code}）") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise UpgradeError("インターネットに接続できません") from e


@dataclass
class Upgrader:
    data_dir: Path
    start_unit: Callable[[], bool]
    fetch_latest: Callable[[], dict[str, Any] | None]
    current: str = ""
    status_dir: Path = DEFAULT_STATUS_DIR
    clock: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if not self.current:
            self.current = installed_version()

    @property
    def request_path(self) -> Path:
        return self.data_dir / REQUEST_FILE

    @staticmethod
    def _load(path: Path) -> dict[str, Any] | None:
        try:
            st = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return st if isinstance(st, dict) else None

    def status(self) -> dict[str, Any]:
        result = self._load(self.status_dir / STATUS_FILE)
        req = self._load(self.request_path)
        candidates = [x for x in (result, req) if x]
        if not candidates:
            return {"state": "idle"}
        # 同じ秒なら root 側（結果）を採る
        st = max(candidates, key=lambda x: (float(x.get("at") or 0), x is result))
        if st.get("state") in _ACTIVE and self.clock() - float(st.get("at") or 0) > STALE_SEC:
            st = {**st, "state": "failed", "message": "更新が途中で止まりました"}
        return st

    def busy(self) -> bool:
        return self.status().get("state") in _ACTIVE

    def check(self) -> dict[str, Any]:
        rel = self.fetch_latest()
        cur = parse_version(self.current) or (0, 0, 0)
        out: dict[str, Any] = {"current": self.current, "latest": None, "available": False, "notes": ""}
        if not rel:
            return out
        tag = str(rel.get("tag_name") or "")
        ver = parse_version(tag)
        if ver is None:
            return out
        out.update(
            latest=tag.removeprefix("v"),
            available=ver > cur,
            notes=str(rel.get("body") or "")[:MAX_NOTES],
        )
        return out

    def start(self) -> bool:
        """新しい版があれば更新ユニットを起動する。実行中・最新・起動失敗なら False。"""
        if self.busy() or not self.check()["available"]:
            return False
        self._write({"state": "requested", "from": self.current, "message": "更新を準備しています"})
        if self.start_unit():
            return True
        self._write({"state": "failed", "from": self.current, "message": "更新を開始できません"})
        return False

    def _write(self, st: dict[str, Any]) -> None:
        tmp = self.request_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**st, "at": int(self.clock())}, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.request_path)
