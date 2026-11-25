# 進捗引き継ぎノート — SoiKaxa ソフトウェア v1

> 1 セッション 1 機能 / 着手前にスモークテスト / 終了時はテスト緑 + コミット + 本ファイル更新。
> 機能リストは `passes` 列だけを更新する（行の削除・受け入れ条件の書き換えはしない）。

---

## 1. 現在の状態

| 項目 | 内容 |
| ---- | ---- |
| 最終更新 | 2026-11-25（BT 接続の根本原因、LCD の文字の流れ） |
| ブランチ | `feat/saved-public-playlists`（PR #6 `feat/lcd-marquee-and-setup` の上） |
| 起動コマンド | `SOIKAXA_DATA_DIR=$PWD/.data SOIKAXA_FAKE=1 SOIKAXA_HTTP_PORT=8080 SOIKAXA_HTTP_HOST=127.0.0.1 uv run python -m soikaxa.daemon` |
| スモークテスト | `uv run pytest` → 期待: 337 passed |
| 既知の壊れている箇所 | なし（実機の未確認は下表の R 項目） |

---

## 2. 機能リスト（`passes` のみ更新）

U = 開発 PC で検証済み（pytest + フェイク起動の HTTP スモーク）/ R = 実機での確認が必要

| ID | 機能 | 受け入れ条件 | passes |
| -- | ---- | ------------ | ------ |
| FR-001 | プレイリスト一覧・選択 | オーナー API で一覧を取得し、選ぶと再生が始まる | ✅ U |
| FR-002 | 連続再生・キュー優先 | EOF で次曲、キュー先頭が優先、最終曲後に IDLE | ✅ U |
| FR-003 | resolve 失敗スキップ・フォールバック | 失敗曲をスキップ、連続 3 回でローカル MP3 へ | ✅ U |
| FR-004 | 次曲 1 曲先読み | 先読みは 1 曲、期限切れは再解決 | ✅ U |
| FR-005 | タッチ操作 | 0.3 秒未満無視 / タップ / 1.5 秒 / 10 秒 | ✅ U / ✅ R（タップ・表示切替ボタン。2026-11-23） |
| FR-006 | BT スピーカー | スキャン・接続・記憶・自動再接続のコマンド | ✅ U / ❌ R（音） |
| FR-007 | 端末 AP + ポータル | WIFI QR、接続検知 302、dnsmasq 設定 | ✅ U / ✅ R（iPhone で QR → 画面が自動で開く。2026-11-23） |
| FR-008 | 上流 Wi-Fi 複数登録・AP 同時動作 | NM プロファイル作成・優先度・チャンネル追従 | ✅ U / ❌ R |
| FR-009 | Wi-Fi 登録 3 経路 | 一覧 / 手入力ステルス / 写真 QR、失敗分類 | ✅ U（jsQR 解読確認済み）/ ❌ R（iPhone Live Text） |
| FR-010 | 権限分離 | PIN・セッション・CSRF・ロック・受付停止 | ✅ U |
| FR-011 | ゲスト検索・追加 | 上限 3、次に流す許可、追加者名 | ✅ U |
| FR-012 | LCD 状態別画面 | 480×320、日本語、はみ出し省略 | ✅ U / ❌ R（QR 1m 読取） |
| FR-013 | 回転レコード | 72 フレーム RGB565、停止中は回転しない | ✅ U |
| FR-014 | 自動再生 | 設定 ON + 前回あり → BT 接続後に再生 | ✅ U |
| FR-015 | キャプティブ Wi-Fi 検知 | 「テザリングへ」案内 | ✅ U |
| FR-016 | 認証削除・初期化 | PIN・Wi-Fi・認証・記憶機器を消去 | ✅ U |
| FR-017 | 音源認証登録 | browser.json を 0600 で端末内に保存 | ✅ U（実アカウントは R） |
| FR-018 | 音量・キュー編集 | 0〜100、削除・移動 | ✅ U |
| FR-019 | 更新・ヘルスチェック | timer、停止中表示、今すぐ更新 | ✅ U（ユニット）/ ❌ R |
| FR-020 | ウォッチドッグ | WATCHDOG=1 を周期通知、ロック停滞時は止める | ✅ U |
| NFR | 起動 20 秒 / A2DP 30 分 / ブチ切り 20 回 / 同時 10 人 | 一次 PRD フェーズ 1・5・7 | ❌ R |

---

## 3. 次にやること（優先順）

0. 実機の手順書は `docs/setup-raspberry-pi.md`。実機のユーザー名は Imager で決めたもの（例の `soi` ではない場合がある）。結果は `scripts/04_report.sh` の出力で共有
1. 実機で未確認: スピーカーの組み直し（リセット後の再ペアリング）と音出し、共有リンクでのゲスト追加、ゲストの取り消し、リピート、LCD のリスト / 自動切替、待機画面の URL
2. 実アカウントで ytmusic アダプタ（認証登録 → プレイリスト → 再生）。認証ヘッダはプライベートウィンドウで取り、ログアウトせず閉じる
3. 【人間】一次 PRD フェーズ 0〜1: `scripts/01_setup.sh` → `scripts/02_verify.sh` → `docs/phase1-results.md` 記入
4. フェーズ 5: overlayfs、Deno 導入、電源ブチ切り 20 回
5. FR-002 の受け入れ条件「最終曲後に IDLE」は、リピート（既定オン）をオフにしたときの動作。条件文の見直しは人間判断

---

## 4. セッションログ（新しいものを上に）

### 2026-11-25 — 公開プレイリストの保存・再生

- **やったこと**: オーナー画面「再生」に「保存した公開プレイリスト」。キーワード・共有リンクで探して保存（最大 20、検索結果に出たものだけ）、再生（保存時の音源で）、削除（2 回押し）。設定 `saved_playlists`、初期化で消える。API: `GET /api/owner/public-playlists`、`POST /api/owner/saved-playlists(/remove)`、`/api/owner/play` に `adapter`
- **検証**: `uv run pytest` → 337 passed / ruff 0 / mypy 0 / check_deps 0 / フェイク起動 + Chromium（390px）で 探す → 保存 → 再生 → 削除 を操作して確認。fresh context のレビュー指摘 3 件（検索時の音源を保持、（音源, ID）の組で判定、保存・削除をロックで直列化）に対応
- **未完了 / 申し送り**: 実アカウントの音源で、公開プレイリストの検索と再生（認証なしでも曲一覧が取れるか）は R。ブランチ `feat/saved-public-playlists`（PR #6 の上）

### 2026-11-25 — LCD の文字の流れと表示の不具合

- **やったこと**: 再生中の曲名・アーティスト名がはみ出すとき横に流す（4 秒停止 → 20px/秒）。右列の書き出しをリストの見出しにそろえ、QR 横の案内を QR の上辺にそろえる。部分書き込みで上下に割れる問題を、画面の写し + 行範囲の一括書き込みで修正。コンソールのカーソルが LCD で点滅する問題を `vt.global_cursor_default=0` で修正（`01_setup.sh`）。PR #5 作成（PR #4 の上に積む）
- **検証**: `uv run pytest` → 333 passed / ruff 0 / mypy 0 / check_deps 0 / 流れる途中のフレームを PNG で目視
- **手順・スクリプトの見直し**: `deploy.sh --update`（OS 更新なしでコードと設定を反映、起動オプション変更時だけ再起動）を追加。`--code-only` は設定が古い Pi に `--update` を促す。`01_setup.sh` は WirePlumber の設定が変わったときだけ WirePlumber を再起動（アプリ内アップグレードでも効く）。`04_report.sh` に WirePlumber・ログイン状態・カーソル設定・BT の失敗ログ。手順書に更新方法の表、確認項目 4b / 9b、症状 2 件、ユニット名の注意。ブランチ `feat/lcd-marquee-and-setup`（PR #5 の上に積む）
- **未完了 / 申し送り**: 実機で未確認（割れ・速さ・カーソル・`--update` の動作）。今の実機は `scripts/deploy.sh soikaxa@soikaxa.local --update` で全部入る

### 2026-11-25 — BT 接続の衝突対策と QR の待ち時間案内

- **やったこと**: アプリの「接続」だけが connect_failed になり、SSH の手打ち（pair → trust → connect）ではつながる件。原因候補 2 つに対処: ① 監視ループの自動再接続とオーナーの接続が同時に connect を送る → `BluetoothManager` の接続系をロックで直列化し、手動中（`busy`）は `connect_speaker` が休む ② pair → trust → connect を間を置かず送る → ペアリング後 2 秒待ち、connect は再試行（記憶済みは 2 回、ペアリング後は 3 回）、待つ間に自分からつながったら成功。記憶済みの鍵を 1 回の失敗で消さない。connect の判定も終了コードではなく出力と `info`。失敗時は bluetoothctl の出力を journal（`soikaxa.bt`）に記録。接続待ちの最大時間の文言を更新。QR を読んでから専用ブラウザが開くまで 5〜6 秒かかる案内を LCD（再生中のゲスト QR・設定用 QR）と guide.html に追加
- **検証**: `uv run pytest` → 323 passed / ruff 0 / format 0 / mypy 0 / check_deps 0 / LCD 2 画面を PNG に描画し、はみ出し・ボタンとの重なりなしを目視
- **実機ログ（22:13）で判明**: connect は 15 秒たっても `Attempting to connect` のまま無応答、pair は 20 秒時点でまだ UUID を受信中に打ち切られていた（`--timeout` で bluetoothctl が終わるとペアリングは取り消される）→ pair / connect から `--timeout` を外し、上限は runner で pair 60 秒 / connect 30 秒。無応答で打ち切った connect は再試行しない。ログは結果の行だけを拾う（UUID の行で埋もれていた）。326 passed
- **実機ログ（22:23）で根本原因が判明**: `br-connection-profile-unavailable`。WirePlumber が logind の active ユーザーにしか A2DP を登録せず、SSH ログイン中だけつながっていた → `01_setup.sh` で logind 連携を切る設定を置く。既存の実機には手動で同じ設定を入れる
- **未完了 / 申し送り**: 実機で再確認が必要（R）。次に失敗したら `journalctl -u soikaxa@<user> | grep "bt "` で `org.bluez.Error.*` を確認できる
- **判断・却下した案**: 失敗理由ごとに分岐する再試行は採らず、回数固定の再試行に（実機の正確なエラー文字列が未確認のため）

### 2026-11-25 — 実機の BT 組み直しの不具合対応（PR #4 / #5）

- **やったこと**: 専用ブラウザで confirm()/prompt() が出ず削除などが無反応 → 2 回押し `armed()` に統一（PR #4）。FUNLOGY Portable Mini2 がアプリから再接続できず手動 bluetoothctl ではつながった件 → ペアリング成否を出力と `info` で判定、失敗段階（not_found / pair_failed / connect_failed）ごとに案内、スピーカー画面に「つながらないとき」の手順（PR #5）
- **検証**: `uv run pytest` → 316 passed / ruff 0 / mypy 0 / Chromium（playwright-core）で 2 回押しと失敗案内の表示を確認
- **実機で分かったこと**: スピーカーは電源 ON だけでは前の機器へつなぎに行く（`Connected: yes → no` を繰り返す）。iPhone の登録解除 + ペアリングモードで `pair` が成功。PipeWire は正常
- **未完了 / 申し送り**: `bluetoothctl --timeout` が失敗時も終了コード 0 を返すかは実機で未確認（判定は出力ベースにしたので影響なし）。PR #5 を実機で確認（削除 → ペアリングモード → スキャン → 接続）

### 2026-11-24 — オーナー画面: BT の本体から削除 / 音源認証の接続テスト

- **やったこと**: `POST /api/owner/bt/forget`（bluetoothctl remove + 記憶済み一覧から削除）と記憶済み行の「本体から削除」ボタン。`AuthCheckable.check_auth()`（認証付きでプレイリスト一覧を 1 回取得）と `POST /api/owner/source/auth/test`、「接続テスト」ボタン。ARCH_001 §8 と guide.html を更新
- **検証**: `uv run pytest` → 303 passed / ruff 0 / mypy 0 / check_deps 0 / フェイク起動で forget・auth/test を curl 確認
- **レビュー対応（PR #3）**: 最後の記憶スピーカーを消すと IDLE/PLAYING のまま残る不具合を修正（`connect_speaker` が ONLINE へ戻す）。0 件時は認証切れの可能性を添える。接続テストの結果は再読込で消す。接続中の判定は表示クラスではなく `connected` で行う
- **未完了 / 申し送り**: 実アカウントでの接続テスト（期限切れ時に空一覧が返るか）と実機 BT の削除→再ペアリングは R。**接続中のスピーカーを削除したとき本当に音が止まるか**（PipeWire が別出力に切り替わるなら確認ダイアログの文言を直す）も R。画面の見た目は未確認（Playwright 未使用）
- **判断・却下した案**: 削除はボタン単独（ワンタップ自動つなぎ直しは不採用）/ 接続テストは認証確認のみ（再生 URL 解決までは行わない）。音源ごとに 10 秒 1 回

### 2026-11-23 — 実機での手直し（ゲスト体験・LCD・タッチ）

- **やったこと**: 音源の認証ヘッダの取り方を詳しく（PC 必須・プライベートウィンドウ）/ 待機中の LCD にオーナー画面の URL / スピーカーがペアリングを忘れたら組み直して接続、一覧に MAC / LCD 左側をボタンで レコード → リスト → 自動 と切替（追加時は 10 秒リストで強調）、レコードはジャケットを全面に / ゲストが共有リンクを貼って追加、自分の追加曲を取り消し / プレイリストのリピート（既定オン）/ オーナー画面にゲストへ戻るボタン、キューを上に / タッチ座標の読み取りと位置合わせ（既定値は実測から推定）
- **検証**: `uv run pytest` → 292 passed / ruff 0 / mypy 0 / check_deps 0 / フェイク起動の HTTP スモーク（ゲスト画面、共有リンク検索、取り消し、位置合わせ API）/ 実機: 表示切替ボタンが位置合わせなしで当たる、iPhone で QR → 画面が自動で開く
- **未完了 / 申し送り**: 上の「次にやること」1。SSH 鍵は未登録で、deploy はターミナル.app から（`!` 実行はパスワード入力不可）
- **判断・却下した案**: ゲストは専用ブラウザで一時的につないで追加する方式のまま（つなぎっぱなし・Safari 誘導は不要）/ 取り消しの確認は confirm() ではなく 2 回押し（専用ブラウザで出ない可能性）/ タッチの向き補正は環境変数をやめて本体での位置合わせに

### 2026-11-21 — review-sweep と指摘対応

- **やったこと**: MIT ライセンス追加。`/review-sweep`（12 体）で MUST 2 / SHOULD 26 / CONSIDER 24。MUST 2 件と SHOULD の大半を修正（一覧は `output/reports/review/REVIEW_002_sweep-response.md`）
- **検証**: `uv run pytest` → 227 passed（3 回連続）/ カバレッジ 99% / ruff 0 / mypy 0 / check_deps 0 / フェイク起動 HTTP スモーク 15/15 / LCD に設定コードが出ることを画像で確認
- **未完了 / 申し送り**: 修正後の再レビューは未実施。systemd サンドボックスと AP/STA 同時動作は実機で確認
- **判断**: PIN は 6〜8 桁に変更。PIN 初回登録は本体 LCD の設定コード必須。Web は端末 AP 側からのみ受け付け

### 2026-11-21 — TEAM_PJM 自律モード

- **やったこと**: PRD_001 / ARCH_001 / PLAN_001 作成、T1〜T13 実装（コア 21 モジュール + ytmusic アダプタ + Web + LCD + systemd）、Phase 5 レポート
- **検証**: `uv run pytest` → 188 passed（5 回連続）/ カバレッジ 99%（実機専用コードは `pragma: no cover`）/ ruff 0 / mypy 0 / `check_deps.py` 0 / pip-audit 0 件 / フェイク起動 HTTP スモーク 15/15 / LCD 出力の QR を jsQR で解読成功
- **未完了 / 申し送り**: 実機（R 項目）、ライセンス、fresh context の第三者レビュー（Agent 起動がフック干渉で不可だったため未実施）
- **判断・却下した案**: Web とデーモンは同一プロセス（AQ-1）/ 認証登録は `AuthCapable` 任意 Protocol で 4 関数 I/F を守る / ゲストは検索キャッシュの ID のみ追加可 / mpv の event は専用スレッドで配送（受信スレッドで配るとデッドロック）
