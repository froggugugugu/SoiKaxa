"""ソースアダプタの読み込み。

- `local`（ローカル MP3）は常に含む（オフラインフォールバック）
- 追加アダプタは entry point グループ `soikaxa.adapters` で登録された
  `factory(data_dir: Path) -> SourceAdapter` を呼んで生成する。コアは個別アダプタを import しない
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from .base import SourceAdapter
from .local_mp3 import LocalAdapter

log = logging.getLogger(__name__)
GROUP = "soikaxa.adapters"


def music_dir(data_dir: Path) -> Path:
    return Path(os.environ.get("SOIKAXA_MUSIC_DIR", str(data_dir / "music")))


def load_adapters(
    data_dir: Path, eps: Callable[[], Iterable[EntryPoint]] = lambda: entry_points(group=GROUP)
) -> dict[str, SourceAdapter]:
    adapters: dict[str, SourceAdapter] = {"local": LocalAdapter(music_dir(data_dir))}
    for ep in eps():
        if ep.name in adapters:
            log.warning("adapter %s: duplicate name ignored", ep.name)
            continue
        try:
            adapter = ep.load()(data_dir)
        except Exception:  # アダプタの不具合でデーモンを起動不能にしない
            log.exception("adapter %s: failed to load", ep.name)
            continue
        if not isinstance(adapter, SourceAdapter):
            log.warning("adapter %s: does not implement SourceAdapter", ep.name)
            continue
        adapters[ep.name] = adapter
    return adapters
