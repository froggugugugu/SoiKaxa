# 開発パターン集

プロジェクト固有のコード規約・落とし穴。構成は `docs/architecture.md`、スキーマは `docs/data-model.md`。

---

## 1. 外部プロセスの扱い

- nmcli / bluetoothctl / systemctl / vcgencmd は必ず `CommandRunner` 経由で、**引数配列**で渡す（シェルを使わない）
- テストは `soikaxa.fakes.FakeRunner().on([...prefix], CommandResult(...))` で応答を登録する。後から登録したルールが優先
- nmcli は `-t`（terse）出力のみパースする。`\:` エスケープは `net.split_terse` で解く
- bluetoothctl に渡す値は `bt.validate_mac` を通した MAC のみ
- `bluetoothctl --timeout N <cmd>` の成否は終了コードで判断しない（失敗しても 0 で終わることがある）。出力（`Pairing successful` / `Connection successful`）か `info` の状態で判断する
- BT の接続系操作（connect / pair / remove）は `BluetoothManager` のロックで直列にする。オーナーの「接続」中（`bt.busy`）は監視ループの自動再接続を休む。ペアリング直後は 2 秒待ってから connect し、失敗しても数回試す（スピーカー側からの接続とぶつかるため）。失敗時は bluetoothctl の出力を journal に残す
- mpv は `MpvTransport` を注入する。テストは `FakeTransport`（`finish()` で現在曲の EOF を発火）

## 2. 再生ループ

- 現在曲の終了判定は mpv の `playlist_entry_id` で行う。`loadfile replace` で差し替えた旧曲の `end-file(stop)` を次曲送りと誤認しない
- `PlayerService._advance()` は RLock 内でループし、resolve/play の失敗はスキップ。連続 `max_consecutive_failures` 回でフォールバック（`local`）へ切替
- 先読みは次曲 1 曲だけ（アカウントのボット判定リスクを下げる）。期限 30 秒前を過ぎた先読み結果は捨てて再解決
- 非同期処理は `run_async` を注入してテストでは同期実行する
- 通知は由来ごとに別フィールド（`_notice` フォールバック > `net_notice` 上流 > `health_notice` ヘルスチェック）。片方の更新で他方を消さない
- LCD は「曲が流れているか」（`status().now` / `is_playing`）で通常画面と回転を決める。状態機械の `PLAYING` には依存しない（上流なしのローカル再生などで `PLAYING` にならないため）
- mpv の event は専用スレッドで配送する。受信スレッド上で購読者を呼ぶと、購読者が送ったコマンドの応答を読めずデッドロックする

## 3. コア / アダプタ境界

- `adapters/base.py` は変更しない（D-09）。アダプタ固有操作は `adapters/capabilities.AuthCapable` のような任意 Protocol で表し、`isinstance` で判定する
- コアに `youtube` / `ytmusic` の文字列を書かない（`scripts/check_deps.py` が検出）。README・SSID にも入れない
- アダプタは entry point `soikaxa.adapters` の `factory(data_dir)` で生成する

## 4. Web

- DOM 生成は `textContent` / `createElement` のみ。`innerHTML` 禁止（CSP は `script-src 'self' 'unsafe-inline'`）
- 外部リソースは同一オリジンのみ（jsQR は `web/static/` に同梱）。getUserMedia は使わない（ポータル簡易ブラウザで不可）
- 入力欄は `autocorrect=off autocapitalize=off spellcheck=false`
- `confirm()` / `prompt()` / `alert()` は使わない。キャプティブの専用ブラウザでは出ずに即キャンセル扱いになる。確認は `armed(button, 文言, 注意)` の 2 回押し、文字入力の確認は画面上の入力欄で行う（`tests/test_web.py` が検査）
- オーナーの状態変更 API は `X-SoiKaxa-CSRF` 必須。CSRF なしのオーナー Cookie はゲスト扱い
- ゲストが追加できるのは `SearchCache` にある ID だけ
- PIN の初回登録は `SetupCode`（LCD にだけ表示）で本体の前にいることを確かめる。コードを API 応答やログ（実機）に出さない
- アクセス制御は送信元アドレスで行う（`allowed_networks`）。Host ヘッダは偽装できるので使わない

## 5. UI 規約

- 色トークン（LCD `lcd/theme.py` と Web の CSS 変数で同じ値）: 背景 `#0B0F14`、シアン `#5DCAA5`、マゼンタ `#ED93B1`、文字 `#C9D1D9`、補助 `#7D8590`
- Web の入力欄の枠は `--field`（`#5F6B78`。パネル・背景と 3:1 以上）。区切り線の `--line` を枠に使うと入力欄が見えなくなる
- ダークテーマ固定（端末用途）。QR は白地に黒・静止
- LCD のテキストは `screens.fit_text` で幅に収め、はみ出しは「…」
- タップ領域は 44px 以上

## 6. テストパターン

- 設定ファイルは `tmp_path` の `SettingsStore` を使う
- Web は `TestClient(create_app(ctx), base_url="http://10.42.0.1")`（Host がポータル判定に使われるため）
- AF_UNIX ソケットは macOS でパス長制限（104 バイト）がある。`tmp_path` ではなく `/tmp` 直下に作る

---

## 7. デザインシステム

### 7.1 参照するデザインシステム

| リソース | URL | 用途 |
| -------- | --- | ---- |
| PRD 9.5 | `docs/PRD.md` | 配色・フォント・回転レコード |

---

## 8. 過去の問題事例

| 問題 | 原因 | 対策 |
| ---- | ---- | ---- |
| ruff format が `base.py` を整形し D-09（無変更）に反する | 一括整形 | `pyproject.toml` の `extend-exclude` に追加、`tests/test_deps.py` で原本一致を検査 |
| Pillow 13 に RGB565 のパッカーがない | `tobytes("raw","BGR;16")` 非対応 | `ImageChops` によるチャンネル演算で変換（`lcd/fb.py`） |
| 保存済み Wi-Fi の登録し直しに失敗すると元の設定まで消えた | 接続確認の前に既存プロファイルを削除していた | 仮の名前（`sknew-`）で作成 → 接続成功後に旧設定を消して改名 |
| 実機で AP（`soikaxa-ap`）が作られず、ログにも何も出ない | polkit ルールが `50-` だったため、`49-polkit-pkla-compat.rules`（OS 付属の NetworkManager.pkla）に先に `settings.modify.system=no` を返された | ルールを `10-soikaxa.rules` にする。確認は `sudo systemd-run --uid=$USER --pipe --wait nmcli general permissions` |
| オーナー画面の削除・再生成などが無反応（一瞬ちらつくだけ） | 専用ブラウザで `confirm()` が表示されず false を返していた | 2 回押しの `armed()` に統一し、ネイティブダイアログの使用をテストで禁止 |
| スピーカーを削除して組み直しても「接続できません」としか出ず、手動の bluetoothctl ならつながった | スピーカーが電源 ON だけで前の機器（スマホ・古い鍵の本体）につなぎに行き、ペアリングが成立していなかった。アプリは `pair` の終了コードを信じて先へ進み（`Paired: no` なのに `Trusted: yes`）、失敗の段階を伝えられなかった | 成否を出力と `info` で判定し、`not_found` / `pair_failed` / `connect_failed` ごとに次の一手を画面に出す。スピーカー画面に「つながらないとき」の手順 |
| アプリの「接続」だけが「音の接続に失敗」になり、SSH で pair → trust → connect を手で打つとつながった | 監視ループの自動再接続とオーナーの接続が同時に `connect` を送り、BlueZ が片方を失敗させていた。さらに pair → trust → connect を間を置かず送るため、ペアリング直後にスピーカー側から来る接続とぶつかった。1 回の失敗で記憶済みの鍵まで消していた | 接続系をロックで直列化し、手動中は自動再接続を休む。ペアリング後に待ってから再試行つきで connect。失敗理由を journal に記録 |
| 上流が 5GHz の DFS チャンネル（例 124）だと AP が立たない | 同じ無線チップの `uap0` は上流と同じチャンネル必須で、brcmfmac は DFS チャンネルで AP を開始できない（`AP-DISABLED`）。失敗時に上流も一度切れる | `same_radio` のとき `add_and_connect` が上流を `802-11-wireless.band bg` に固定（屋外利用も考慮）。SoiKaxa 以外が作った接続（Imager の `preconfigured`）は手順書 8.2 で手動固定 |
| フック（PreToolUse）が `sudo` を含むコマンド文字列をブロック | ハーネスの安全ルール | `sudo` を含むスクリプトは Write ツールで作成 |
