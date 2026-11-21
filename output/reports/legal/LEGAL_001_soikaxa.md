# 法務チェック — SoiKaxa v1（2026-11-21）

総合: **CRITICAL 0**。要対応 1 件（ライセンス未決）。jsQR の表記は README に追記済み

## プロジェクトのライセンス

- 未決（PRD Q-1）。推奨は MIT。`LICENSE` が無い間は第三者が利用できない（公開前チェックの未完項目）
- アダプタ README に免責（個人利用・自己責任・規約抵触の可能性）を記載済み

## ランタイム依存（`pip-licenses`）

| ライセンス | パッケージ | 判定 |
| --- | --- | --- |
| MIT / MIT-CMU | fastapi, pydantic, anyio, h11, urllib3, ytmusicapi, pillow ほか | 問題なし |
| BSD-3-Clause | starlette, uvicorn, click, idna | 問題なし |
| BSD（分類子に "Other/Proprietary" 併記） | qrcode 8.2 | 実体は BSD（LICENSE ファイル）。分類子の誤記で問題なし |
| Apache-2.0 | requests, opentelemetry-api | 問題なし（NOTICE 保持） |
| MPL-2.0 | certifi | 無改変利用のため問題なし |
| PSF-2.0 | typing_extensions | 問題なし |
| Unlicense | yt-dlp | 問題なし（アダプタ側のみ） |

GPL 系の Python 依存はなし。mpv（GPL/LGPL）・BlueZ（GPL）・NetworkManager（GPL）は別プロセスとして呼び出すため、コアのライセンスに伝播しない（PRD 8.1 の判断どおり）。

## 同梱物

| ファイル | ライセンス | 対応 |
| --- | --- | --- |
| `core/soikaxa/web/static/jsQR.js`（jsQR 1.4.0） | Apache-2.0 | `jsQR.LICENSE` を同梱し、README に表記済み |

## 知的財産・規約

| 項目 | 状態 |
| --- | --- |
| 名称・SSID・README に「YouTube」「yt」なし（D-10） | `check_deps.py` で README を検査済み、SSID は `soikaxa-XXXX` |
| DRM 回避 | 実装なし |
| YouTube 利用規約 | yt-dlp 経由の取得は抵触の可能性。個人利用に限定し、アダプタを別パッケージに分離（コアはアダプタなしでローカル MP3 が鳴る＝テストで確認） |
| フォント | LCD はシステムの Noto Sans CJK（OFL）を参照するのみで同梱しない。ケースのロゴ用 Orbitron（OFL）は CAD 側で人間が扱う |
| プライバシー | 個人データはオーナー端末内のみ（ゲスト名は任意・メモリのみ・再起動で消える）。外部送信なし |
