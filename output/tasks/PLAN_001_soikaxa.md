# PLAN_001 — SoiKaxa ソフトウェア v1 実装計画

入力: `output/prd/PRD_001_soikaxa.md`、`output/design/ARCH_001_soikaxa.md`

## 要件サマリー

- 選択プレイリストを連続再生し、キューを優先すること（FR-001, FR-002）
- resolve 失敗をスキップし、連続 3 回でローカル MP3 へ切り替えること（FR-003）
- 次曲を 1 曲だけ先読みし、期限切れは再解決すること（FR-004）
- タッチ 0.3 秒未満無視・タップでトグル・1.5 秒で次曲・10 秒で設定 QR（FR-005）
- BT スキャン・接続・記憶・自動再接続のコマンドを発行すること（FR-006）
- Wi-Fi QR 文字列・接続検知応答・dnsmasq 設定を生成すること（FR-007）
- 上流 Wi-Fi の登録・優先度・AP チャンネル追従（FR-008）、3 経路登録と失敗分類（FR-009）
- オーナー PIN・セッション・CSRF・ロック、ゲスト受付停止（FR-010）
- ゲスト検索・キュー追加・上限・次に流す許可（FR-011）
- LCD 状態別画面・回転レコード 72 フレーム・RGB565（FR-012, FR-013）
- 自動再生、キャプティブポータル検知、初期化、認証登録、音量・キュー編集（FR-014〜FR-018）
- 更新/ヘルスチェック timer、ウォッチドッグ（FR-019, FR-020）

## 影響調査

| カテゴリ | 対象 | 変更内容 |
| --- | --- | --- |
| 設定 | `pyproject.toml`, `core/pyproject.toml`, `adapters/soikaxa-adapter-ytmusic/pyproject.toml`, `.gitignore` | 追加 |
| ユーティリティ | `core/soikaxa/runner.py`, `config.py`, `auth.py`, `watchdog.py` | 追加 |
| ストア | `core/soikaxa/queue.py`, `state.py` | 追加 |
| ユーティリティ | `core/soikaxa/player.py`, `net.py`, `bt.py`, `portal.py`, `touch.py`, `health.py` | 追加 |
| スキーマ | `core/soikaxa/adapters/base.py`, `local_mp3.py` | 移動（input から取込、無変更） |
| スキーマ | `core/soikaxa/adapters/capabilities.py`, `registry.py` | 追加 |
| コンポーネント | `core/soikaxa/lcd/*` | 追加 |
| ページ | `core/soikaxa/web/app.py`, `schemas.py`, `static/index.html`, `static/jsQR.js` | 追加 |
| ユーティリティ | `core/soikaxa/daemon.py` | 追加 |
| 設定 | `adapters/soikaxa-adapter-ytmusic/soikaxa_adapter_ytmusic/__init__.py` | 追加 |
| 設定 | `systemd/*.service`, `systemd/*.timer`, `scripts/*` | 移動 + 追加 |
| テスト | `tests/*`, `adapters/soikaxa-adapter-ytmusic/tests/*` | 追加 |
| ドキュメント | `docs/project.md`, `architecture.md`, `data-model.md`, `development-patterns.md`, `docs/PRD.md`, `docs/DECISIONS.md`, `README.md` | 変更 / 移動 |

## タスク分解

### Phase 1（並行不可・基盤）

- [ ] T1 — リポジトリ整備: input/soikaxa の資産取込、uv workspace、ruff/mypy/pytest 設定（変更ファイル: pyproject.toml, core/pyproject.toml, .gitignore, core/soikaxa/adapters/base.py, local_mp3.py, scripts/01_setup.sh, scripts/02_verify.sh, systemd/soikaxa.service, case/README.md, docs/PRD.md, docs/DECISIONS.md | 依存: なし）
- [ ] T2 — 共有基盤 runner / config / auth / watchdog（変更ファイル: core/soikaxa/runner.py, config.py, auth.py, watchdog.py, tests/test_config.py, tests/test_auth.py, tests/test_watchdog.py | 依存: T1）

### Phase 2（Phase 1 完了後・並行可能）

- [ ] T3 — queue + state（変更ファイル: core/soikaxa/queue.py, state.py, tests/test_queue.py, tests/test_state.py | 依存: T1）
- [ ] T4 — player（変更ファイル: core/soikaxa/player.py, tests/test_player.py | 依存: T1）
- [ ] T5 — net + portal（変更ファイル: core/soikaxa/net.py, portal.py, tests/test_net.py, tests/test_portal.py | 依存: T2）
- [ ] T6 — bt + health（変更ファイル: core/soikaxa/bt.py, health.py, tests/test_bt.py, tests/test_health.py | 依存: T2）
- [ ] T7 — touch（変更ファイル: core/soikaxa/touch.py, tests/test_touch.py | 依存: T1）
- [ ] T8 — lcd（変更ファイル: core/soikaxa/lcd/*, tests/test_lcd.py | 依存: T1）
- [ ] T9 — adapters registry/capabilities + ytmusic アダプタ（変更ファイル: core/soikaxa/adapters/capabilities.py, registry.py, adapters/soikaxa-adapter-ytmusic/*, tests/test_adapters.py | 依存: T1）

### Phase 3（Phase 2 完了後）

- [ ] T10 — daemon（PlayerService・先読み・フォールバック・main・フェイクモード）（変更ファイル: core/soikaxa/daemon.py, core/soikaxa/fakes.py, tests/test_daemon.py | 依存: T3, T4, T9）
- [ ] T11 — LcdController（変更ファイル: core/soikaxa/lcd/controller.py | 依存: T8, T10）

### Phase 4（Phase 3 完了後）

- [ ] T12 — web API + 単一 HTML + jsQR 同梱（変更ファイル: core/soikaxa/web/*, tests/test_web.py | 依存: T2, T5, T6, T10）

### Phase 5（Phase 4 完了後）

- [ ] T13 — systemd unit/timer、check_deps、ドキュメント同期、README（変更ファイル: systemd/*, scripts/check_deps.py, tests/test_deps.py, docs/*, README.md | 依存: T10, T12）

## 依存関係グラフ

```text
T1 ─┬─> T2 ─┬─> T5 ──────────────┐
    │       └─> T6 ──────────────┤
    ├─> T3 ─┐                    │
    ├─> T4 ─┼─> T10 ─┬─> T11     ├─> T12 ─> T13
    ├─> T9 ─┘        └───────────┘
    ├─> T7 ─────────────> (T10 main で結線)
    └─> T8 ─────────────> T11
```

## テスト戦略

- ユニット: 各モジュールに `tests/test_<module>.py`。外部プロセスは `FakeRunner`（引数列を記録し、登録済み応答を返す）、mpv は `FakeTransport`
- サービス: `FakePlayer`（`finish()` で EOF を発火）と `FakeAdapter`（失敗する ID を指定可能）で再生ループを決定論的に検証
- API: `TestClient` で権限・CSRF・PIN ロック・上限・ポータル 302
- E2E（ブラウザ）: 本サイクルは API テストで代替（PRD §8）
- ゲート: `pytest` 全パス、カバレッジ 80% 以上、ruff 0 件、mypy 0 件、`check_deps.py` 0 件

## ドキュメント更新計画

| 対象 | 内容 |
| --- | --- |
| `project-config.md` §2 / §3 / §11 | 技術スタック・コマンド・落とし穴を追記（AI 更新可セクションのみ） |
| `docs/project.md` | 概要・コマンド・技術スタック・ルーティング |
| `docs/architecture.md` | ディレクトリ・レイヤー・テスト配置 |
| `docs/data-model.md` | Settings スキーマ |
| `docs/development-patterns.md` | Mock 規約・落とし穴 |
| `output/tasks/PROGRESS.md` | 進捗 |

## リスク・懸念事項

| リスク | 対策 |
| --- | --- |
| nmcli / bluetoothctl の出力形式が実機版と異なる | terse 形式に限定し、パーサは未知行を無視。実機での確認項目を PROGRESS に残す |
| jsQR の取得に外部ネットワークが要る | 取得できなければ手入力フォールバックのみ実装し、同梱を人間タスクに回す |
| 開発 PC に Noto CJK フォントがない | フォント探索 + 既定フォントにフォールバック。テストは画像サイズ・非空のみ検証 |
