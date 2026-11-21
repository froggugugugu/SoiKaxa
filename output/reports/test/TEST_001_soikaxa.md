# テスト・パフォーマンスレポート — SoiKaxa v1（2026-11-21）

## ユニット / API テスト

`uv run pytest --cov` → **188 passed / 0 failed**、カバレッジ **99%**（HTML: `testreport/coverage/`）。5 回連続実行で失敗なし。

| ファイル | 件数の主な対象 |
| --- | --- |
| test_foundation | runner, config（後方互換・破損時の既定値・0600）, auth（scrypt・ロック・セッション）, watchdog |
| test_queue_state | キュー優先・上限・並替、状態遷移表 |
| test_player | mpv IPC、旧曲 end-file の無視、event 配送スレッド（デッドロック回帰） |
| test_net_portal | nmcli 生成/パース、失敗分類、キャプティブ判定、Wi-Fi QR エスケープ、ポータル判定 |
| test_bt_health_touch | bluetoothctl、ヘルスチェック、タッチ閾値 |
| test_lcd | RGB565 既知値、fb 部分書き込み、全画面 480×320、回転 72 フレーム |
| test_adapters / test_ytmusic | registry、ローカル MP3、ytmusic（Mock・例外変換・ID 検証・0600） |
| test_daemon | 連続再生、スキップ、フォールバック、先読み・期限切れ、並行 enqueue、resolve 中の非ブロック |
| test_controller_bootstrap | 状態別画面選択、フレーム非同期生成、起動ヘルパ |
| test_web | 権限・CSRF・PIN ロック・上限・ポータル 302・Wi-Fi/BT/認証/初期化 |
| test_deps | 依存方向、base.py 無変更 |

## E2E（フェイク起動での HTTP スモーク）

`SOIKAXA_FAKE=1` でデーモンを実起動し、HTTP 経由で 15 項目を確認 → **15/15 PASS**:
index（外部 CDN なし）、ポータル 302、状態、PIN 設定、プレイリスト一覧、再生開始、ゲスト日本語検索、次に流す、次曲に追加者名、ゲストのオーナー API 拒否、次曲送り、Wi-Fi スキャン、BT 接続、AP QR。

目視確認（Playwright + Chrome、390×844 モバイル）: ゲスト画面・オーナー画面とも横はみ出しなし（`scrollWidth == 390`）。LCD 出力（`fb.raw`）を PNG 化して確認し、ゲスト QR を同梱 jsQR で解読 → `WIFI:T:WPA;S:soikaxa-7964;P:…;;` を取得。

ブラウザ自動 E2E（Playwright テストスイート）は本サイクル未作成（PRD §8 で API テストに代替と決定）。

## パフォーマンス（開発 PC: Apple Silicon。Pi 4B では数倍遅い想定）

| 項目 | 計測値 | 目標・考察 |
| --- | --- | --- |
| 回転レコード 72 フレーム生成 | 119 ms | Pi 4B で 1 秒前後の見込み。生成中は静止画（PRD 9.5） |
| フレームのメモリ | 5.76 MB | PRD 想定 約 5.8 MB と一致 |
| 200×200 領域の fb 書き込み | 0.61 ms | 8 fps（125 ms/フレーム）に対して十分。実機は SPI 転送が律速 |
| 通常画面の描画（PIL） | 3.8 ms | 1 秒ごとの全面更新で問題なし |
| RGB565 変換（480×320） | 0.52 ms | numpy 不要 |
| PIN 照合（scrypt） | 35 ms | 総当たり抑止と応答性のバランス良好 |

実機で計測すべき項目: 起動から再生まで（目標 20 秒）、次曲切替（2 秒）、SPI の実 fps、A2DP 30 分途切れ、同時 10 ゲスト。
