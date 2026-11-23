# 実機セットアップ手順（Raspberry Pi 4B）

SoiKaxa を Raspberry Pi 4B に入れる手順。**Imager で書き込む → Mac で 1 コマンド → スマホで初回設定**の 3 段階。
使い方（ゲスト・オーナーの操作）は `docs/guide.html` を見る。

- 作業 PC: macOS（コマンドは Mac のターミナル前提）
- 所要時間: 40 分前後（うち 20 分ほどはインストールの待ち時間）
- フェーズ 1 の技術検証（A2DP・AP と上流の同時動作）は 2026-11-22 に合格済み（`docs/phase1-results.md`）。この手順はその結果を前提にしている

## 0. 用意するもの

| もの | 条件 |
| --- | --- |
| Raspberry Pi 4B | メモリ容量は問わない |
| microSD | 16GB 以上（32GB A1 推奨） |
| 電源 | USB-C 5V/3A（15W 以上） |
| OSOYOO 3.5 インチ SPI LCD（型番 2022013800） | 最初から付けてよい（ドライバが入るまで画面は白い） |
| Bluetooth スピーカー | A2DP 対応 |
| スマホ | 初回設定と操作 |
| 自宅 Wi-Fi の SSID とパスワード | **2.4GHz で見えるもの**（理由は下） |
| LAN ケーブル | 任意。あると Wi-Fi で困ったときに SSH で入り直せる |

2.4GHz が必要な理由: 端末 AP（`soikaxa-XXXX`）は上流 Wi-Fi と同じ無線チップ・同じチャンネルで動く。5GHz の DFS チャンネル（52〜144）では AP を開始できないため、上流は 2.4GHz に固定する（インストールで自動設定）。2.4GHz と 5GHz で同じ SSID を出しているルーターならそのままでよい。

## 1. microSD に OS を書き込む

Raspberry Pi Imager（2.0.12 以降）を起動し、次のとおり選んで書き込む。

| 画面 | 選ぶ・入力する内容 |
| --- | --- |
| デバイス | Raspberry Pi 4 |
| OS | **Raspberry Pi OS (other)** → **Raspberry Pi OS (Legacy, 64-bit) Lite**（説明に "Debian Bookworm" とあるもの） |
| ストレージ | 挿した microSD |
| ホスト名 | `soikaxa` |
| ロケール | タイムゾーン `Asia/Tokyo`、キーボード `jp`（US 配列なら `us`） |
| ユーザー | 好きなユーザー名（例 `soi`）とパスワード |
| Wi-Fi | 自宅 Wi-Fi の SSID・パスワード、国 `JP` |
| リモートアクセス（SSH） | 有効。公開鍵認証を選び、`cat ~/.ssh/id_ed25519.pub` の 1 行を貼る |
| Raspberry Pi Connect | 使わない |

OS は最新の Trixie ではなく **Legacy の Bookworm** を選ぶ（AP と上流の同時動作は Bookworm で確認済み）。

## 2. 起動して、Mac から 1 コマンドで入れる

1. microSD と LCD を Pi に付けて電源を入れる。初回は設定の反映で 1〜2 分かかる
2. Mac でつながることを確かめる（`soi` は自分のユーザー名に置き換える）:

```bash
ssh soi@soikaxa.local 'grep VERSION_CODENAME /etc/os-release; id -u'
# → VERSION_CODENAME=bookworm と 1000
```

前回の Pi に接続したことがあると `REMOTE HOST IDENTIFICATION HAS CHANGED!` で止まる。書き込み直すと鍵が変わるためで、`ssh-keygen -R soikaxa.local` で古い記録を消してからやり直す。

3. インストールする。手元の MP3 も一緒に送ると、インターネットや音源の認証なしで音出しを確かめられる（フォルダ名がプレイリスト名になる）:

```bash
cd ~/Development/SoiKaxa
scripts/deploy.sh soi@soikaxa.local --music ~/Development/mp3/Test   # --music は省略可
```

`deploy.sh` がやること: コード送信 → Pi で `scripts/install.sh`（OS 更新・mpv/BlueZ/PipeWire・LCD ドライバ・本体・権限・AP 用 `uap0`・上流の 2.4GHz 固定）→ 曲の送信 → 再起動。何度実行してもよい。

- 最後に `注意: ... は 2.4GHz で見つかりません` と出たら、その Wi-Fi は 5GHz 専用。再起動後に端末 AP が立たないことがある（「うまくいかないとき」の表）
- `~/Music` やデスクトップの MP3 は macOS のプライバシー保護で `Operation not permitted` になることがある。`~/Development` などに一度コピーしてから送る

4. 再起動して 1 分ほど待つと、LCD に「起動中…」→ **Wi-Fi の QR と 6 桁の設定コード**が出る。スマホの Wi-Fi 一覧に `soikaxa-XXXX` が見えれば成功

## 3. スマホで初回設定

1. スマホのカメラで LCD の QR を読み、`soikaxa-XXXX` に接続する。画面が自動で開く（開かなければブラウザで `http://10.42.0.1/`）
2. 「オーナーはこちら」→ ① LCD に表示されている **6 桁の数字（設定コード）** と、② 新しい **PIN（6〜8 桁。自分で決める）** を入れて「設定」
3. 「スピーカー」: スピーカーをペアリングモードにして「スキャン」→「接続」（初回は 10〜20 秒ほど。ボタンに経過秒数が出る）
4. 「再生」: 「マイプレイリスト」から選んで「再生」
5. YouTube Music を使う場合: 「音源」に認証ヘッダを貼って「登録」（取り方は `docs/guide.html` の「音源を登録する」）
6. 「設定」で「電源 ON で前回のプレイリストを自動再生」をオンにする

Web 画面は端末 AP（`soikaxa-XXXX`）に参加したスマホからだけ開ける。自宅 Wi-Fi 側の PC から開くと 403 になる（仕様）。

## 4. 新しいバージョンに更新する

スマホだけでできる。本体がインターネット（上流 Wi-Fi）につながっていること。

1. オーナー画面 →「設定」→「ソフトウェア更新」の **新しいバージョンを確認**
2. 「v0.2.0 があります」と変更点が出たら **アップグレード**
3. 数分待つ（音楽はいったん止まる）。終わると PIN の入れ直しを求められるので入れ直し、「設定」で「完了: v0.2.0 に更新しました」を確かめる

新しい版が起動しなかったときは、自動で元の版に戻して「元に戻しました: …」と出る。更新前のコードは Pi の `~/soikaxa.prev` に残る。
記録は Pi の `/var/lib/soikaxa-upgrade/upgrade.json`、詳しいログは `journalctl -u soikaxa-upgrade@$USER -n 80 --no-pager`。

### 新しいバージョンを配る（開発する人向け）

GitHub の Releases に出したものだけが配られる（ブランチの途中のコードは届かない）。

```bash
# 1) core/pyproject.toml の version を上げてコミットし、main にマージする（タグと同じ番号にする）
uv lock && git commit -am "chore: v0.2.0 をリリース"
# 2) main でタグを打ってリリースを作る（push は確認してから）
git switch main && git pull
git tag v0.2.0 && git push origin v0.2.0
gh release create v0.2.0 --generate-notes --title v0.2.0
```

リリースの説明文（`--generate-notes` の内容）がオーナー画面の「変更点」に出る。タグと `core/pyproject.toml` の version が違うと、本体は更新を中止する。

## 5. 開発中のコードを直接送る

```bash
scripts/deploy.sh soi@soikaxa.local --code-only   # 送信 → サービス再起動（数秒）
```

依存パッケージやスクリプト・systemd の定義を変えたときは `--code-only` を付けずに実行する。リリース前の確認用で、普段の更新は 4 の画面から行う。

## 6. 動作確認チェックリスト

| # | 確認 | 期待 |
| --- | --- | --- |
| 1 | QR を読むと画面が自動で開く（iPhone / Android） | 開く |
| 2 | 設定コードなし、または違うコードでは PIN を設定できない | 「実機の画面に表示されている 6 桁の数字（設定コード）を…」と出る |
| 3 | スピーカーから音が出る | 出る |
| 4 | LCD に曲名・次の曲・QR が出て、レコードが回る | 回転がカクつかない |
| 5 | 画面タップ（0.3 秒以上）で一時停止/再開、1.5 秒長押しで次の曲 | 動く |
| 6 | 10 秒長押しで設定用 QR が全面に出る | 出る（30 秒で戻る） |
| 7 | 別のスマホ（ゲスト）で QR → 検索 →「追加」 | LCD の「次:」に曲名と名前が出る |
| 8 | 曲が終わると次の曲に進む | 2 曲以上続けて進む |
| 9 | 再生中にスピーカーの電源を切って入れ直す | 30 秒程度で再接続する |
| 10 | 電源を抜いて挿し直す | 自動で起動し、前回のプレイリストが流れる（音が出るまでの秒数を控える） |
| 11 | `vcgencmd measure_temp` | 再生 10 分後に 70℃ 未満 |

## 7. 結果を共有する

```bash
ssh soi@soikaxa.local 'bash ~/soikaxa/scripts/04_report.sh' > ~/Desktop/soikaxa-report.txt
```

パスワード類は含まれない。チェックリストの ○ × と、気になった挙動（画面の写真があると早い）を添える。

## うまくいかないとき

Pi に入って調べるときは `ssh soi@soikaxa.local`。ログは `journalctl -u soikaxa@$USER -n 60 --no-pager`。

| 症状 | 見るところ・直し方 |
| --- | --- |
| `soikaxa.local` に SSH できない | ルーターの管理画面で Pi の IP を調べ、`soi@192.168.x.x` で接続（`deploy.sh` にも IP を渡せる）。Imager の Wi-Fi 設定が入っていなければ LAN ケーブルでつなぐ |
| `install.sh` が途中で止まった | 表示されたエラーを記録して、もう一度 `deploy.sh` を実行する（途中からやり直せる） |
| LCD が白いまま | 電源を抜き差しする。直らなければ `ls /dev/fb*` と `dmesg \| grep -i -E "spi\|ili\|fb"` を記録する |
| LCD の向きが縦・上下逆 | `/boot/firmware/config.txt` の `dtoverlay=osoyoo35b:speed=20000000` に `,rotate=90`（または `270`）を足して再起動（未検証） |
| LCD に文字化け（□） | `fc-list \| grep -i noto \| head -3`（`fonts-noto-cjk` が入っているか） |
| AP（`soikaxa-XXXX`）が見えない | ① `nmcli -t -f ACTIVE,SSID,CHAN dev wifi list ifname wlan0 --rescan no \| grep ^yes` のチャンネルが 15 以上 → 上流が 5GHz。2.4GHz で見える Wi-Fi に替えるか、ルーターの 5GHz を 36〜48 にする。② `sudo systemd-run --uid=$USER --pipe --wait nmcli general permissions \| grep settings.modify.system` が `no` → `/etc/polkit-1/rules.d/10-soikaxa.rules` があるか確認。③ `iw dev` に `uap0` が無い → `systemctl status soikaxa-uap0` |
| AP に入れるが画面が開かない | ブラウザで `http://10.42.0.1/` を直接開く。開けば自動表示だけの問題（機種と OS を記録） |
| サービスが再起動を繰り返す | 上のログを確認。`Permission denied` / `Read-only file system` ならサンドボックス設定（`systemd/soikaxa@.service` の後半）が原因の可能性。ログを共有する |
| スピーカーの `connect` がすぐ切れる（`br-connection-unknown`） | LE 側でペアリングされた。`bluetoothctl remove <MAC>` → `sudo systemctl restart bluetooth` → Web から接続し直す（Pi は Classic 専用設定済み） |
| 音が出ない | `wpctl status` の Sinks にスピーカーがあるか。`bluetoothctl info <MAC>` が `Connected: yes` か |
| 曲が 1 曲目で止まる | `journalctl -u soikaxa@$USER -f` を流しながら再現し、ログを共有 |
| PIN を忘れた・ロックされた | ロックは本体を 10 秒長押しで解除。PIN を忘れたら `sudo systemctl stop soikaxa@$USER && rm /var/lib/soikaxa/settings.json && sudo systemctl start soikaxa@$USER`（設定が初期化され、新しい設定コードが LCD に出る） |

やり直すとき: アプリの設定だけなら Web の「設定」→「初期化」。全部なら手順 1 の書き込みからやり直す（40 分程度）。

## 参考

- フェーズ 1 の個別検証（A2DP・AP 同時動作の手動確認）は `scripts/02_verify.sh` と、この文書の git 履歴（2026-11-22 版）にある
- Raspberry Pi OS: <https://www.raspberrypi.com/software/operating-systems/>
- OSOYOO 3.5 インチ SPI LCD（Bookworm/Trixie、2025-10-01 以降）: <https://osoyoo.com/?p=61051>
- 仮想 AP インターフェースと Bookworm / Trixie の報告: <https://github.com/raspberrypi/linux/issues/7092>
