"""アダプタの任意能力（4 関数 I/F の外側）。

`SourceAdapter` は増やさない（D-09）。認証登録のようなアダプタ固有の操作は、
実装しているアダプタだけがこの Protocol を満たす。コアは isinstance で判定する。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthCapable(Protocol):
    def has_auth(self) -> bool: ...

    def register_auth(self, raw: str) -> None:
        """オーナーが貼り付けた認証情報を端末内に保存する。不正なら AdapterError。"""
        ...

    def clear_auth(self) -> None: ...


@runtime_checkable
class AuthCheckable(Protocol):
    def check_auth(self) -> int:
        """登録済みの認証で音源に 1 回だけアクセスし、オーナーのプレイリスト数を返す（疎通確認）。

        未登録・期限切れは AuthRequired、それ以外の失敗は AdapterError。
        """
        ...
