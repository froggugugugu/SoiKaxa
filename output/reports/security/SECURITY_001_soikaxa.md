# セキュリティスキャン — SoiKaxa v1（2026-11-21）

総合: **CRITICAL 0 / HIGH 0 / MEDIUM 1 / LOW 2**

> 追記（2026-11-21）: review-sweep が HIGH 1 件（PIN 未設定時のオーナー先取り）と MEDIUM 5 件を検出。HIGH と MEDIUM 4 件は修正済み、平文 HTTP は受容のまま。詳細は `output/reports/review/REVIEW_002_sweep-response.md`。本書の初版（セルフレビュー）はこれらを見落としていた

## 依存 CVE

`uvx pip-audit -r <uv export --no-dev>` → **No known vulnerabilities found**（ランタイム依存 22 パッケージ）

## シークレット混入

| 確認 | 結果 |
| --- | --- |
| `.gitignore` | `browser.json` / `oauth.json` / `*.pin` / `config/secrets*` / `.data/` / `.venv/` を除外 |
| リポジトリ内の認証情報 | なし（テストの PIN・パスワードはダミー値） |
| 保存時の権限 | settings / browser.json は 0600、データ・認証ディレクトリは 0700（テストで検証） |
| ログ | PIN・パスワード・認証ヘッダを出力しない（例外は型名のみログ） |

## OWASP Top 10 観点

| 項目 | 対策 | 検証 |
| --- | --- | --- |
| A01 アクセス制御 | `/api/owner/*` は `require_owner`（セッション + 状態変更は CSRF ヘッダ）。ゲストは検索キャッシュの ID のみ追加・展開可（オーナーの非公開プレイリストに到達不可） | `test_web.py` の権限・CSRF・展開テスト |
| A02 暗号 | PIN は scrypt（n=2^14, r=8, p=1, salt 16B）、`hmac.compare_digest` | `test_foundation.py` |
| A03 インジェクション | 外部コマンドは引数配列（shell 不使用）、MAC・SSID・パスワードは検証後のみ渡す。DOM は `textContent` のみ | `test_net_portal.py` / `test_bt_health_touch.py` |
| A04 設計 | PIN 6〜8 桁、5 回失敗でロック（300 秒から 2 倍ずつ）、PIN 初回登録は本体表示の設定コード必須、ゲスト上限、受付停止 | `test_pin_lockout` |
| A05 設定 | CSP `default-src 'self'`、`X-Content-Type-Options`、`Referrer-Policy`、`Cache-Control: no-store`、OpenAPI/ドキュメント無効 | `test_index_and_static_and_security_headers` |
| A07 認証 | セッション Cookie `HttpOnly; SameSite=Strict`、4 時間で失効、ログアウト・初期化で破棄 | `test_session_cookie_flags` |
| A08 完全性 | jsQR は npm の公式 tarball（sha256 `b5299b37…6602`）を同梱。外部 CDN 不使用 | `test_index...`（https:// を含まない） |
| A10 SSRF | ジャケット取得は http(s) のみ・3 秒・2MB 上限、`file://` 等は拒否 | `test_cover_loader_cache_and_rejects` |

## 指摘

| 重大度 | 内容 | 対応 |
| --- | --- | --- |
| MEDIUM | 端末 AP 上の通信は HTTP（TLS なし）。同じ AP の参加者は PIN・Cookie を盗聴しうる | AP は WPA2（パスワードは QR で配布）。自己署名 TLS はポータル簡易ブラウザで警告が出て使えないため受容。Web は端末 AP 側からだけ受け付ける。PIN を他サービスと使い回さない旨を設定画面に表示 |
| LOW | CSP に `'unsafe-inline'`（単一 HTML のインライン script/style） | XSS 経路は `textContent` 限定で塞いでいる。将来 JS を外部ファイル化して外す |
| LOW | psk が nmcli 引数で一瞬プロセス一覧に出る | REVIEW S-3 と同じ。単一ユーザー端末のため受容 |

## 権限設計（実機）

- デーモンは非 root（`User=%i`）、ポート 80 は `CAP_NET_BIND_SERVICE` のみ付与
- polkit ルールで NetworkManager 操作と `soikaxa-update@*.service` の start だけを `soikaxa` グループに許可
