# データモデル

## スキーマ配置

- 永続設定: `core/soikaxa/config.py`（`Settings` dataclass → `<data>/settings.json`）
- アダプタ型: `core/soikaxa/adapters/base.py`（`Playlist` / `Track` / `Resolved`、変更禁止）
- API 入力: `core/soikaxa/web/schemas.py`（pydantic）

## スキーマ詳細

### Settings（`settings.json`、0600、`schema_version=1`）

| フィールド | 型 | 備考 |
| ---------- | -- | ---- |
| `schema_version` | int | 読込時に 1 へ正規化 |
| `device_model` | str | `pi4b`（スリム版用の識別） |
| `owner_pin_hash` | str \| null | `scrypt$N$r$p$salt$dk`。平文は保存しない |
| `ap_ssid` / `ap_password` | str \| null | 初回起動で生成。初期化でも維持 |
| `active_adapter` | str | 存在しなければ `local` として扱う |
| `last_playlist` | {adapter, id} \| null | 自動再生用 |
| `autoplay` | bool | 既定 true |
| `volume` | int | 0〜100 に丸める |
| `guest_enabled` / `allow_play_next` | bool | 既定 true |
| `guest_queue_limit` | int | 1〜20 に丸める。既定 3 |
| `known_speakers` | [{mac, name}] | 先頭が最新、最大 5 |
| `probe_track_id` | str \| null | ヘルスチェック用の曲 ID |
| `wifi_labels` | {ssid: label} | 会場名。Wi-Fi パスワードは NetworkManager のみに保存 |

後方互換: 未知キーは無視、欠損キーは既定値、壊れた JSON は既定値で起動（起動不能にしない）。

### その他の永続データ

| データ | 場所 |
| ------ | ---- |
| 音源認証（ytmusic） | `<data>/adapters/ytmusic/browser.json`（0600、ディレクトリ 0700） |
| ヘルスチェック結果 | `<data>/health.json` |
| ジャケットキャッシュ | `<data>/covers/` |

## フォームバリデーション

| スキーマ | 対象 | 主なルール |
| -------- | ---- | ---------- |
| `PinIn` | PIN 設定・ログイン | 4〜8 桁の数字 |
| `EnqueueIn` | キュー追加 | `name` ≤ 20 文字、`track_id` は検索キャッシュに存在すること |
| `WifiIn` | Wi-Fi 登録 | SSID 1〜32 バイト・制御文字不可、パスワード空 or 8〜63 の印字可能 ASCII |
| `BtConnectIn` | スピーカー接続 | `XX:XX:XX:XX:XX:XX` |
| `VolumeIn` / `MoveIn` | 音量・並替 | 0〜100 / 0〜1000 |
| `SettingsPatch` | 設定 | 上限 1〜20 |
| `AuthIn` | 音源認証 | 1〜64,000 文字 |
| `ResetIn` | 初期化 | `confirm == "RESET"` |

## 重要なバリデーションルール

- ゲストの追加・プレイリスト展開は `SearchCache` にある ID のみ（クライアント送信のメタデータを信用しない）
- ゲストのキュー上限はゲスト ID（`sk_gid` Cookie）単位。オーナー（CSRF 付き）は上限・受付停止・次に流す禁止の対象外
- PIN は 5 回失敗で 300 秒ロック（端末全体）
- ytmusic の曲 ID は `^[A-Za-z0-9_-]{11}$`、プレイリスト ID は `^[A-Za-z0-9_-]{2,64}$`

## ユーティリティ関数

| 関数 | 用途 |
| ---- | ---- |
| `net.wifi_qr_payload` | `WIFI:` QR 文字列（`\ ; , : "` をエスケープ） |
| `net.generate_ap_credentials` | `soikaxa-XXXX` と紛らわしい文字を除いた 10 文字パスワード |
| `lcd.fb.to_rgb565` | RGB → RGB565 LE（numpy 非依存） |
| `config.write_private_file` | 0600 の原子的書き込み |
