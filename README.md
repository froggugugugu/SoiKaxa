# SoiKaxa

Bluetooth スピーカーに音楽を流す、Raspberry Pi ベースの単体完結プレーヤー。
操作はスマホ（端末の Wi-Fi に QR で参加）、本体は 3.5 インチ LCD で表示。

- 仕様: [docs/PRD.md](docs/PRD.md) / 受け入れ基準: [output/prd/PRD_001_soikaxa.md](output/prd/PRD_001_soikaxa.md)
- 判断の記録: [docs/DECISIONS.md](docs/DECISIONS.md)
- コマンド・構成: [docs/project.md](docs/project.md)

音源はソースアダプタで差し替え可能（ローカル MP3 同梱、その他は `adapters/` の別パッケージ）。

ライセンス: 未決（MIT を推奨案として検討中）

同梱物: jsQR 1.4.0（Apache-2.0、`core/soikaxa/web/static/jsQR.LICENSE`）
