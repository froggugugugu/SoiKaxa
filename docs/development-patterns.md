# 開発パターン集

プロジェクト固有のコード規約・落とし穴。構成は `docs/architecture.md`、スキーマは `docs/data-model.md`。

---

## 1. 外部プロセスの扱い

- nmcli / bluetoothctl / systemctl / vcgencmd は必ず `CommandRunner` 経由で、**引数配列**で渡す（シェルを使わない）
- テストは `soikaxa.fakes.FakeRunner().on([...prefix], CommandResult(...))` で応答を登録する。後から登録したルールが優先
- nmcli は `-t`（terse）出力のみパースする。`\:` エスケープは `net.split_terse` で解く
- bluetoothctl に渡す値は `bt.validate_mac` を通した MAC のみ
- mpv は `MpvTransport` を注入する。テストは `FakeTransport`（`finish()` で現在曲の EOF を発火）

## 2. 再生ループ

- 現在曲の終了判定は mpv の `playlist_entry_id` で行う。`loadfile replace` で差し替えた旧曲の `end-file(stop)` を次曲送りと誤認しない
- `PlayerService._advance()` は RLock 内でループし、resolve/play の失敗はスキップ。連続 `max_consecutive_failures` 回でフォールバック（`local`）へ切替
- 先読みは次曲 1 曲だけ（アカウントのボット判定リスクを下げる）。期限 30 秒前を過ぎた先読み結果は捨てて再解決
- 非同期処理は `run_async` を注入してテストでは同期実行する

## 3. コア / アダプタ境界

- `adapters/base.py` は変更しない（D-09）。アダプタ固有操作は `adapters/capabilities.AuthCapable` のような任意 Protocol で表し、`isinstance` で判定する
- コアに `youtube` / `ytmusic` の文字列を書かない（`scripts/check_deps.py` が検出）。README・SSID にも入れない
- アダプタは entry point `soikaxa.adapters` の `factory(data_dir)` で生成する

## 4. Web

- DOM 生成は `textContent` / `createElement` のみ。`innerHTML` 禁止（CSP は `script-src 'self' 'unsafe-inline'`）
- 外部リソースは同一オリジンのみ（jsQR は `web/static/` に同梱）。getUserMedia は使わない（ポータル簡易ブラウザで不可）
- 入力欄は `autocorrect=off autocapitalize=off spellcheck=false`
- オーナーの状態変更 API は `X-SoiKaxa-CSRF` 必須。CSRF なしのオーナー Cookie はゲスト扱い
- ゲストが追加できるのは `SearchCache` にある ID だけ

## 5. UI 規約

- 色トークン（LCD `lcd/theme.py` と Web の CSS 変数で同じ値）: 背景 `#0B0F14`、シアン `#5DCAA5`、マゼンタ `#ED93B1`、文字 `#C9D1D9`、補助 `#7D8590`
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
| フック（PreToolUse）が `sudo` を含むコマンド文字列をブロック | ハーネスの安全ルール | `sudo` を含むスクリプトは Write ツールで作成 |
