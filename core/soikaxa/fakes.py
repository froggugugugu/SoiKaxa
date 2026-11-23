"""開発 PC 用のフェイク（テストと `SOIKAXA_FAKE=1` 起動で使う）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .runner import CommandResult

Responder = CommandResult | Callable[[list[str]], CommandResult]


class FakeRunner:
    """引数列を記録し、先頭一致で登録済み応答を返す。未登録は returncode 0・空出力。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._rules: list[tuple[tuple[str, ...], Responder]] = []

    def on(self, prefix: Sequence[str], result: Responder) -> FakeRunner:
        # 後から登録したものを優先する
        self._rules.insert(0, (tuple(prefix), result))
        return self

    def run(self, args: Sequence[str], *, timeout: float = 10.0, input: str | None = None) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)
        for prefix, res in self._rules:
            if tuple(argv[: len(prefix)]) == prefix:
                return res(argv) if callable(res) else res
        return CommandResult(0, "", "")

    def called(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


class FakeTransport:
    """mpv IPC のフェイク。loadfile ごとに playlist_entry_id を払い出す。"""

    def __init__(self) -> None:
        self.sent: list[list[object]] = []
        self.props: dict[str, object] = {"pause": False, "volume": 100}
        self.fail: set[str] = set()
        self._listeners: list[Callable[[dict[str, object]], None]] = []
        self._entry = 0
        self.current_entry: int | None = None

    def send(self, command: list[object]) -> dict[str, object]:
        self.sent.append(command)
        name = str(command[0])
        if name in self.fail:
            return {"error": "error running command"}
        if name == "loadfile":
            old = self.current_entry
            self._entry += 1
            self.current_entry = self._entry
            if old is not None:
                self.emit({"event": "end-file", "reason": "stop", "playlist_entry_id": old})
            return {"error": "success", "data": {"playlist_entry_id": self._entry}}
        if name == "set_property":
            self.props[str(command[1])] = command[2]
        elif name == "cycle":
            self.props[str(command[1])] = not self.props.get(str(command[1]))
        elif name == "get_property":
            key = str(command[1])
            if key not in self.props:
                return {"error": "property unavailable"}
            return {"error": "success", "data": self.props[key]}
        elif name == "stop":
            self.current_entry = None
        return {"error": "success", "data": None}

    def on_event(self, cb: Callable[[dict[str, object]], None]) -> None:
        self._listeners.append(cb)

    def emit(self, msg: dict[str, object]) -> None:
        for cb in list(self._listeners):
            cb(msg)

    def finish(self, reason: str = "eof") -> None:
        """現在曲を終了させる。"""
        entry, self.current_entry = self.current_entry, None
        self.emit({"event": "end-file", "reason": reason, "playlist_entry_id": entry})
