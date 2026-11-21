# プロジェクト情報

## 概要

SoiKaxa（ソイカシャ）— Raspberry Pi 4B 単体で動く Bluetooth 音楽プレーヤー。端末 AP + キャプティブポータルでスマホから操作し、3.5 インチ SPI LCD に曲名とゲスト用 QR を表示する。仕様の正は `docs/PRD.md`（一次）と `output/prd/PRD_001_soikaxa.md`（受け入れ基準）。ケースは `input/SOIKAXA.md` v0.6。

## コマンド

```bash
uv sync --all-packages                                   # 依存インストール（Python 3.11）
uv run pytest                                            # テスト
uv run pytest --cov --cov-report=html:testreport/coverage  # カバレッジ
uv run ruff check . && uv run ruff format --check .      # lint / format
uv run mypy core/soikaxa adapters/soikaxa-adapter-ytmusic/soikaxa_adapter_ytmusic  # 型
uv run python scripts/check_deps.py                      # 依存方向・命名チェック
# 開発 PC でフェイク起動（mpv/nmcli/bluetoothctl なし）。http://127.0.0.1:8080/
# PIN 初回登録に要る設定コードは、フェイク起動時だけログに出る（実機は LCD のみ）
SOIKAXA_DATA_DIR=$PWD/.data SOIKAXA_MUSIC_DIR=$PWD/.data/music SOIKAXA_FAKE=1 \
  SOIKAXA_HTTP_PORT=8080 SOIKAXA_HTTP_HOST=127.0.0.1 uv run python -m soikaxa.daemon
# 実機（Pi）: 01_setup.sh → 02_verify.sh（フェーズ 1）→ 03_install.sh（root で実行）
```

## 技術スタック

| カテゴリ | 技術 |
| -------- | ---- |
| 言語 | Python 3.11（Bookworm 標準） |
| パッケージ | uv workspace（`core` = soikaxa、`adapters/soikaxa-adapter-ytmusic`） |
| Web | FastAPI + uvicorn、単一 HTML（外部 CDN なし）、jsQR 1.4.0 同梱 |
| 画像 | Pillow、qrcode |
| 再生 | mpv（JSON IPC、外部プロセス） |
| 音源 | ytmusicapi + yt-dlp（アダプタのみ） |
| 端末 | NetworkManager（nmcli）、BlueZ（bluetoothctl）、PipeWire、evdev（任意） |
| 品質 | pytest + pytest-cov、ruff、mypy |

## 環境変数

| 変数 | 既定 | 用途 |
| ---- | ---- | ---- |
| `SOIKAXA_DATA_DIR` | `/var/lib/soikaxa` | 設定・認証・キャッシュ |
| `SOIKAXA_MUSIC_DIR` | `<data>/music` | ローカル MP3 のルート |
| `SOIKAXA_FAKE` | — | `1` でフェイク実行 |
| `SOIKAXA_HTTP_HOST` / `SOIKAXA_HTTP_PORT` | `0.0.0.0` / `80` | Web 待ち受け |
| `SOIKAXA_STA_IF` / `SOIKAXA_AP_IF` | `wlan0` / STA と同じ | AP を USB ドングルに分ける場合に指定 |
| `SOIKAXA_FB` | `/dev/fb1` | LCD フレームバッファ |
| `SOIKAXA_FONT` | 自動探索 | LCD フォント |
| `SOIKAXA_UPDATE_UNIT` | なし（`soikaxa@.service` が設定） | 「今すぐ更新」で起動するユニット。未設定なら更新は開始しない |
| `SOIKAXA_ALLOWED_NETS` | `10.42.0.1/24,127.0.0.0/8,::1/128` | Web を受け付ける送信元。上流側から操作したい場合だけ追加する |

## ルーティング

| パス | ページ | 機能 |
| ---- | ------ | ---- |
| `/` | ゲストビュー | 今の曲・次の曲、検索、キュー追加 / 次に流す |
| `/#owner` | オーナービュー | PIN、再生・音量・キュー、Wi-Fi（一覧/手入力/写真）、スピーカー、音源認証、設定、初期化 |
| `/api/*` | API | 一覧は `output/design/ARCH_001_soikaxa.md` §8 |

## ストア一覧

| ストア | 責務 |
| ------ | ---- |
| `SettingsStore`（`config.py`） | 設定 JSON の永続化（0600、原子的書き換え） |
| `PlayQueue`（`queue.py`） | プレイリスト + ゲストキュー（メモリ） |
| `SessionStore` / `PinGuard`（`auth.py`） | オーナーセッション・PIN ロック（メモリ） |
| `SearchCache`（`web/app.py`） | ゲストに見せた検索結果（追加可能 ID の許可リスト） |

## 制約事項

- パスエイリアス: なし（パッケージ `soikaxa` の相対 import）
- E2E テスト: HTTP スモーク（フェイク起動）と API テストで代替。ブラウザ E2E は任意
- Git Hooks: なし（`project-config.md` §9 は人間決定）
- `core/soikaxa/adapters/base.py` と `local_mp3.py` は input からの無変更コピー（D-09）。ruff の対象外
