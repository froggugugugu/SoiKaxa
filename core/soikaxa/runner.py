"""外部コマンド実行の抽象。テストでは FakeRunner を注入する。

シェルは経由しない（引数は常に配列で渡す）。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    def run(
        self, args: Sequence[str], *, timeout: float = 10.0, input: str | None = None
    ) -> CommandResult: ...


class SubprocessRunner:
    """実機用。タイムアウト・未インストールは returncode で表す（例外にしない）。"""

    def run(self, args: Sequence[str], *, timeout: float = 10.0, input: str | None = None) -> CommandResult:
        try:
            p = subprocess.run(  # noqa: S603 - 引数配列、shell=False
                list(args), input=input, capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError:
            return CommandResult(127, "", f"command not found: {args[0]}")
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"timeout: {args[0]}")
        return CommandResult(p.returncode, p.stdout, p.stderr)
