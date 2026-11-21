# コードレビュー — SoiKaxa v1（2026-11-21）

- 対象: `core/soikaxa/`、`adapters/soikaxa-adapter-ytmusic/`、`scripts/`、`systemd/`
- 方法: PJM（実装者）によるセルフレビュー + 機械チェック。【制約】fresh context のレビュー agent は起動できなかった（Agent 呼び出しがプラグインのフックで書き換えられ auto mode の審査を通らない）。実装者の視点に偏りがありうる
- 総合判定: **条件付き承認**（MUST 0 件。下記の SHOULD は実機フェーズで確認）

## 機械チェック

| チェック | 結果 |
| --- | --- |
| `uv run pytest` | 188 passed / 0 failed（5 回連続で安定） |
| カバレッジ | 99%（2265 行中 22 行未カバー。`UnixSocketTransport`・`EvdevTouchSource`・`bootstrap.main` 等の実機専用部分は `pragma: no cover` で除外） |
| `ruff check` / `ruff format --check` | 0 件 |
| `mypy` | 0 件（32 ファイル） |
| `scripts/check_deps.py` | 0 件（依存方向・音源固有名の混入・README 命名） |
| `base.py` 無変更 | `tests/test_deps.py` で input 原本とバイト一致 |

## レビュー中に修正した指摘（MUST → 修正済み）

| # | 指摘 | 影響 | 修正 |
| --- | --- | --- | --- |
| M-1 | mpv の event を IPC 受信スレッド上でコールバックしていた。曲終了 → `play()` が応答待ちになり、応答を読む受信スレッド自身が止まる | 実機で 1 曲目の終了時にデーモンが停止（Mock では再現しない） | `_Dispatcher` に event 配送専用スレッドを追加。回帰テスト `test_event_listener_may_send_commands_without_deadlock` |
| M-2 | `status()` / `enqueue()` が再生ループのロックを取り、resolve（数秒）の間 Web・LCD が止まる | ゲスト操作 30 秒の NFR を損なう | `status()` をロックなしの読み出しに、`enqueue()` をキューのロック＋ダブルチェックに変更。テスト 2 件追加 |
| M-3 | ウォッチドッグが常に健全を返していた | デッドロックしても systemd が再起動しない | `PlayerService.healthy()`（ロック取得 5 秒）を通知条件に |
| M-4 | `loadfile replace` 時の旧曲 `end-file` で次曲が二重に進む | 曲飛ばし | `playlist_entry_id` で現在曲だけを扱う |

## SHOULD（未対応・申し送り）

| # | 指摘 | 理由 / 方針 |
| --- | --- | --- |
| S-1 | 監視ループが 30 秒ごとに `NET_UP`/`FAIL` を送るため、再生中にキャプティブ判定が出ると状態が ERROR になり LCD がエラー画面になる（音は継続） | 実機で頻度を見て判断。案: PLAYING 中は FAIL を notice のみにする |
| S-2 | nmcli / bluetoothctl の出力形式は開発 PC では実物で確認していない | 実機フェーズ（PROGRESS 次の一手 3） |
| S-3 | `wifi.add_and_connect` は psk を nmcli の引数で渡す（同一端末のローカルユーザーから `ps` で一瞬見える） | 単一ユーザー端末のため許容。気になれば `passwd-file` 方式へ |
| S-4 | Cookie なしのゲストは共通 ID `anonymous` で上限を共有する | `/api/status` で必ず Cookie を発行するため通常は発生しない |

## CONSIDER

- `index.html` が 1 ファイル約 400 行。機能追加時は JS を `static/app.js` に分ける（同一オリジンなので制約内）
- `bootstrap.main()` はユニットテスト対象外。実機で `journalctl` を確認する
