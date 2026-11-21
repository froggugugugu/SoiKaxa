"""ヘルスチェック: 音源の resolve 可否と SoC 温度。

`python -m soikaxa.health` は systemd timer（毎朝）から呼ばれ、結果を `<data>/health.json` に残す。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .adapters.base import AdapterError, SourceAdapter
from .config import write_private_file
from .runner import CommandRunner

THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
MSG_SOURCE_DOWN = "音源側の変更で停止中。更新待ち"


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    message: str
    temperature_c: float | None = None
    checked_at: float = 0.0


def read_temperature(runner: CommandRunner, thermal_path: Path = THERMAL_PATH) -> float | None:
    out = runner.run(["vcgencmd", "measure_temp"])
    m = re.search(r"temp=([0-9.]+)", out.stdout) if out.ok else None
    if m:
        return float(m.group(1))
    try:
        return int(thermal_path.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None


def check_source(adapter: SourceAdapter, probe_track_id: str | None) -> tuple[bool, str]:
    if not probe_track_id:
        return True, "確認用の曲が未設定"
    try:
        adapter.resolve(probe_track_id)
    except AdapterError as e:
        return False, f"{MSG_SOURCE_DOWN}（{type(e).__name__}）"
    return True, "OK"


def run_healthcheck(
    adapter: SourceAdapter, probe_track_id: str | None, runner: CommandRunner, clock=time.time
) -> HealthResult:
    ok, msg = check_source(adapter, probe_track_id)
    return HealthResult(ok, msg, read_temperature(runner), clock())


def save_result(result: HealthResult, path: Path) -> None:
    write_private_file(path, json.dumps(asdict(result), ensure_ascii=False))


def load_result(path: Path) -> HealthResult | None:
    try:
        return HealthResult(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def main() -> int:  # pragma: no cover - systemd timer から実行
    from .adapters.registry import load_adapters
    from .config import SettingsStore, data_dir
    from .runner import SubprocessRunner

    d = data_dir()
    settings = SettingsStore(d / "settings.json").load()
    adapters = load_adapters(d)
    adapter = adapters.get(settings.active_adapter) or adapters["local"]
    result = run_healthcheck(adapter, settings.probe_track_id, SubprocessRunner())
    save_result(result, d / "health.json")
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
